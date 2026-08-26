"""
pipeline.py — runs the ETL/analytics pipeline (create_schema.py through
step5_prescriptive.py) as a background job so the Tally Interface's
"Run Full Pipeline" button can show live progress instead of blocking the
request for however long the whole run takes.

Each step is invoked the same way the README says to run it by hand:
`python scripts/<name>.py` from the repo root (every script resolves its
CSV/db paths relative to cwd, not to its own file). Steps run with
`sys.executable`, so they use whatever interpreter is running this Flask
process — the same env the rest of the backend already depends on.

step0_convert_sales_with_zeros.py and step4_prophet_forecast.py are marked
optional:

- step0 reads the original TBS tally-sheet workbooks from a local
  rawdata/ folder (real client Excel files — large, sensitive, and
  deliberately not committed to the repo). Its output,
  data/USTore_sales_long_with_zeros.csv, IS committed, and every step
  after step0 reads only from data/*.csv — none of them touch rawdata/
  or openpyxl. So when rawdata/ isn't present, step0 fails and the
  pipeline falls back to that already-committed CSV instead of stopping
  the whole run: step1 onward "just reads the data folder" either way.
- step4 fits Prophet per Fast SKU. It is by far the longest step (tens of
  minutes) and historically needed a separate ~20-30 minute cmdstan build,
  so it is not assumed to be runnable everywhere.

If either fails, the run is recorded as "skipped" for that step and the
pipeline continues — /api/meta already reports forecast availability as
an honest pending state, so a missing forecast is not a reason to block
step5a/step5, neither of which read Result_Forecast.

Three things here exist specifically so a run driven from a browser button
behaves, rather than looking hung:

**Live output.** Children run with `python -u` and their stdout is drained
line by line by a reader thread into a bounded deque, so /api/pipeline/status
carries a live tail of the step that is running right now. Previously this
used `proc.communicate()`, which hands back the entire output only once the
process has already exited — for a step that takes half an hour, that is a
progress bar with nothing behind it.

**Per-step timeouts.** Nothing here is allowed to hang the run forever. A
step that overruns its budget is killed and recorded as timed out (skipped
if optional, failed if not), and the run moves on or stops deliberately
instead of leaving the UI on "Running…" indefinitely.

**Whole-tree kills.** Prophet fits by spawning a compiled prophet_model.bin
per SKU, so the Python child is not the only process to stop. proc.terminate()
alone would kill the interpreter and orphan whatever Stan binary it had
running, so Stop (and the timeout path) kill the process *tree*.

Steps run via Popen rather than subprocess.run so stop_pipeline() can hold
a handle to the in-flight child process and terminate it — a plain
subprocess.run() blocks the worker thread until the process exits on its
own, giving a "stop" button nothing to actually stop.
"""
import copy
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import db as dbmod

REPO_ROOT = Path(__file__).resolve().parent.parent

# How many lines of a step's stdout to keep for the live tail. Bounded because
# step3/step4 print a row per SKU and this whole dict is JSON-serialised on
# every status poll.
TAIL_LINES = 40

# Per-step wall-clock budgets. These are backstops against a hang, not
# performance targets: the generous ones are for steps whose real runtime is
# minutes, and step4's is large because fitting ~50 Prophet models genuinely
# takes tens of minutes on a laptop.
DEFAULT_TIMEOUT_S = 15 * 60
# step4 fits every Fast SKU's production model with mcmc_samples=1000 - full
# NUTS sampling, which is what the manuscript specifies for the uncertainty
# intervals - on top of a MAP fit for validation. Measured on this repo's
# data that is well over an hour for ~50 SKUs, so the budget is hours, not
# minutes. Everything else in the pipeline finishes in seconds to ~2 minutes.
FORECAST_TIMEOUT_S = 4 * 60 * 60

# (id, script relative to repo root, label, optional, timeout seconds, rough runtime)
STEPS = [
    ("create_schema", "scripts/create_schema.py", "Build database schema", False, 120, "seconds"),
    ("populate_dim_date", "scripts/populate_dim_date.py", "Populate calendar dimension", False, 300, "seconds"),
    ("step0", "scripts/step0_convert_sales_with_zeros.py", "Convert raw tally sheets", True, DEFAULT_TIMEOUT_S, "~1 min"),
    ("step1", "scripts/step1_apply_mapping.py", "Apply vocabulary + supplier mapping", False, DEFAULT_TIMEOUT_S, "~10 s"),
    ("allocation", "scripts/proportional_allocation.py", "Allocate price-grouped rows to SKUs", False, DEFAULT_TIMEOUT_S, "~10 s"),
    ("step2", "scripts/step2_load_fact_sales.py", "Load Fact_Sales", False, DEFAULT_TIMEOUT_S, "~30 s"),
    ("step3", "scripts/step3_fsn_classification.py", "Classify Fast / Slow / Non-moving", False, DEFAULT_TIMEOUT_S, "~1 min"),
    ("step4", "scripts/step4_prophet_forecast.py", "Forecast demand (Prophet)", True, FORECAST_TIMEOUT_S, "1-2 hours"),
    ("step5a", "scripts/step5a_set_lead_times.py", "Set supplier lead times", False, DEFAULT_TIMEOUT_S, "~5 s"),
    ("step5", "scripts/step5_prescriptive.py", "Compute ROP / EOQ / safety stock", False, DEFAULT_TIMEOUT_S, "~10 s"),
]

# Steps a caller is allowed to opt out of. Only step4: it is the one step whose
# cost is measured in hours rather than seconds, and the one whose output
# (Result_Forecast) nothing else in the pipeline reads - step5_prescriptive.py
# derives demand from observed history, not from Result_Forecast. Skipping it
# turns "refresh the dashboard after a day of tallying" from a two-hour job
# into a two-minute one, which is the difference between a button people press
# and a button people avoid.
SKIPPABLE = {"step4"}

_lock = threading.Lock()
_cancel_requested = threading.Event()
_current_proc = None  # Popen handle for whichever step is running right now, if any


def _fresh_steps(skip=()):
    return [
        {
            "id": sid, "label": label, "optional": optional, "timeout_s": timeout,
            "estimate": estimate,
            "status": "deselected" if sid in skip else "pending",
            "output": "", "error": None, "error_detail": None, "duration_s": None,
        }
        for sid, _script, label, optional, timeout, estimate in STEPS
    ]


_state = {
    "status": "idle", "started_at": None, "finished_at": None,
    "run_id": None, "steps": _fresh_steps(),
}


def get_status():
    with _lock:
        state = copy.deepcopy(_state)
    # elapsed is derived rather than stored so it stays live between the
    # ~1.2s status polls without a thread ticking it.
    for entry in state["steps"]:
        if entry["status"] == "running" and entry.get("_started_at"):
            entry["duration_s"] = round(time.time() - entry["_started_at"], 1)
        entry.pop("_started_at", None)
    return state


# ------------------------------------------------------- error summarising

# A Python traceback is the wrong thing to put in front of whoever is running
# the store. It is thirty lines of interpreter frames whose only useful content
# is the last one, and the most common failure here - step0 with no rawdata/ -
# is not an error at all, it is the expected state on any machine that does not
# have the client's Excel workbooks. So each failure is reduced to one sentence
# that says what happened and what (if anything) to do; the raw output is kept
# alongside it in `error_detail` for whoever does want the frames.

_EXC_LINE = re.compile(
    r"^(?P<type>[A-Za-z_][\w.]*(?:Error|Exception|Interrupt|Exit))\s*:\s*(?P<msg>.*)$"
)
_MISSING_MODULE = re.compile(r"No module named ['\"]([^'\"]+)['\"]")
_MISSING_FILE = re.compile(r"No such file or directory:\s*['\"]([^'\"]+)['\"]")


def _last_exception(stderr):
    """(type, message) from the last `SomeError: text` line of a traceback, or
    (None, None) if the output isn't one. Reads bottom-up because a traceback
    with chained causes contains several, and the last is the one that
    actually stopped the process."""
    for line in reversed((stderr or "").strip().splitlines()):
        m = _EXC_LINE.match(line.strip())
        if m:
            return m.group("type"), m.group("msg").strip()
    return None, None


def _summarise_error(step_id, script, stderr, returncode):
    """One sentence for the UI. Falls back to the exception line, and then to
    the exit code, so an unrecognised failure still says something specific
    rather than a generic 'it broke'."""
    exc_type, exc_msg = _last_exception(stderr)

    # step0 without the client workbooks: the single most common "failure" in
    # this pipeline and not a problem at all - the run continues off the
    # committed CSV, which is what every later step reads anyway.
    if step_id == "step0" and exc_type == "FileNotFoundError":
        return ("The raw tally-sheet workbooks aren't on this machine "
                "(the rawdata/ folder isn't part of the repo). Skipped - the run "
                "continued from the already-converted sales CSV in data/, which is "
                "what every later step reads. Nothing is missing from the results.")

    if exc_type in ("ModuleNotFoundError", "ImportError"):
        m = _MISSING_MODULE.search(exc_msg or "")
        if m:
            pkg = m.group(1).split(".")[0]
            return (f"The Python package '{pkg}' isn't installed, so this step couldn't run. "
                    f"Install it with: pip install {pkg}")
        return f"A Python package this step needs couldn't be imported: {exc_msg}"

    if exc_type == "FileNotFoundError":
        m = _MISSING_FILE.search(exc_msg or "")
        target = m.group(1) if m else (exc_msg or "a file it needs")
        return f"A file this step needs is missing: {target}"

    if exc_type == "PermissionError":
        return ("A file this step needs is locked by another program. Close it "
                "(Excel holding a CSV open is the usual cause) and run the pipeline again.")

    if exc_type and exc_type.endswith("OperationalError"):
        if "locked" in (exc_msg or "").lower():
            return ("The database was locked by another program while this step wrote to it. "
                    "Close anything else using ustore.db and run the pipeline again.")
        return f"The database rejected something this step did: {exc_msg}"

    if exc_type == "MemoryError":
        return "This step ran out of memory. Close other applications and run the pipeline again."

    if exc_type == "SystemExit":
        # A script calling sys.exit("message") - the message is already written
        # for a person, since these scripts exit that way deliberately.
        return exc_msg or f"The step stopped itself (exit code {returncode})."

    if exc_type:
        return f"{exc_type}: {exc_msg}" if exc_msg else exc_type

    # Not a traceback. Some scripts print their own refusal to stderr and exit
    # non-zero; that text is already meant for a reader, so use its last line.
    tail = [ln.strip() for ln in (stderr or "").strip().splitlines() if ln.strip()]
    if tail:
        return tail[-1]
    return f"The step stopped with exit code {returncode} and printed no error."

# ------------------------------------------------------------ process control

def _kill_tree(proc):
    """Kill `proc` and everything it spawned. On Windows terminate() maps to
    TerminateProcess, which does not touch children — step4 leaves Stan
    binaries running under it, so taskkill /T is what actually stops the step."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
        else:
            proc.terminate()
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass


def _drain(stream, sink, entry, is_stdout):
    """Reader thread: pump one pipe into a bounded deque, refreshing the
    step's visible tail as it goes. One thread per pipe, because reading
    them serially deadlocks as soon as the other pipe's buffer fills."""
    try:
        for line in stream:
            sink.append(line.rstrip("\n"))
            if is_stdout:
                with _lock:
                    entry["output"] = "\n".join(sink)
    except (ValueError, OSError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _run_step(script, entry, timeout_s):
    """Run one step to completion. Returns (returncode, stdout_tail,
    stderr_tail, timed_out); returncode is None if the step was killed."""
    global _current_proc
    out_lines, err_lines = deque(maxlen=TAIL_LINES), deque(maxlen=TAIL_LINES)

    try:
        proc = subprocess.Popen(
            # -u: unbuffered stdout. Without it Python block-buffers when
            # stdout is a pipe, so a 30-minute step emits nothing until it
            # exits and the live tail below stays empty the whole time.
            [sys.executable, "-u", script],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
    except OSError as exc:
        return 1, "", str(exc), False

    with _lock:
        _current_proc = proc

    readers = [
        threading.Thread(target=_drain, args=(proc.stdout, out_lines, entry, True), daemon=True),
        threading.Thread(target=_drain, args=(proc.stderr, err_lines, entry, False), daemon=True),
    ]
    for t in readers:
        t.start()

    timed_out = False
    deadline = time.time() + timeout_s
    try:
        while True:
            try:
                proc.wait(timeout=1)
                break
            except subprocess.TimeoutExpired:
                if _cancel_requested.is_set():
                    _kill_tree(proc)
                    break
                if time.time() > deadline:
                    timed_out = True
                    _kill_tree(proc)
                    break
    finally:
        for t in readers:
            t.join(timeout=5)
        with _lock:
            _current_proc = None

    return proc.returncode, "\n".join(out_lines), "\n".join(err_lines), timed_out


# ------------------------------------------------------------------ the run

def _run_all():
    ok_overall = True
    cancelled = False

    for (sid, script, label, optional, timeout_s, _est), entry in zip(STEPS, _state["steps"]):
        if _cancel_requested.is_set():
            cancelled = True
            break
        if entry["status"] == "deselected":
            continue

        started = time.time()
        with _lock:
            entry["status"] = "running"
            entry["_started_at"] = started

        returncode, stdout, stderr, timed_out = _run_step(script, entry, timeout_s)
        duration = round(time.time() - started, 1)

        if _cancel_requested.is_set():
            with _lock:
                entry["duration_s"] = duration
                entry["_started_at"] = None
                entry["output"] = stdout
                entry["status"] = "cancelled"
            cancelled = True
            break

        failed = timed_out or returncode != 0
        with _lock:
            entry["duration_s"] = duration
            entry["_started_at"] = None
            entry["output"] = stdout
            if failed:
                if timed_out:
                    entry["error"] = (
                        f"This step was still running after {int(timeout_s // 60)} minutes, "
                        f"so it was stopped. Run `python {script}` from the repo root if you "
                        f"want to let it finish."
                    )
                else:
                    entry["error"] = _summarise_error(sid, script, stderr, returncode)
                # The frames are kept, just not shown by default - the frontend
                # puts them behind a "technical details" toggle.
                entry["error_detail"] = (stderr or "").strip()[-4000:] or None
                entry["status"] = "skipped" if optional else "error"
                if not optional:
                    ok_overall = False
            else:
                entry["status"] = "done"

        if failed and not optional:
            break

    with _lock:
        if cancelled:
            _state["status"] = "cancelled"
            for entry in _state["steps"]:
                if entry["status"] == "pending":
                    entry["status"] = "cancelled"
        else:
            _state["status"] = "done" if ok_overall else "error"
        _state["finished_at"] = datetime.now().isoformat(timespec="seconds")
        run_id = _state["run_id"]
        status = _state["status"]
        steps = copy.deepcopy(_state["steps"])

    # step1/step2 dropped and reloaded the tables db.py's indexes sit on, and
    # a database rebuilt from scratch may never have been switched to WAL at
    # all — re-arm both now the writers are gone.
    dbmod.ensure_initialised(force=True)
    _finish_run_record(run_id, status, steps)


def start_pipeline(skip=()):
    """Start a run. `skip` names steps to leave out (only SKIPPABLE ones are
    honoured); they are marked "deselected" rather than pending, so the step
    list still shows them and nothing reads as silently missing.

    Returns True if a run was started, False if one is already in progress."""
    skip = {s for s in skip if s in SKIPPABLE}
    with _lock:
        if _state["status"] == "running":
            return False
        _state["status"] = "running"
        _state["started_at"] = datetime.now().isoformat(timespec="seconds")
        _state["finished_at"] = None
        _state["steps"] = _fresh_steps(skip)
        started_at = _state["started_at"]
    run_id = _open_run_record(started_at)
    with _lock:
        _state["run_id"] = run_id
    _cancel_requested.clear()
    threading.Thread(target=_run_all, daemon=True).start()
    return True


def stop_pipeline():
    """Terminates the step currently running (if any) and halts the rest of
    the run. Returns True if a stop was actually requested (a run was in
    progress), False if there was nothing to stop."""
    with _lock:
        if _state["status"] != "running":
            return False
        proc = _current_proc
    _cancel_requested.set()
    _kill_tree(proc)
    return True


# --------------------------------------------------- run history / staleness

def _run_con():
    """Own connection, not Flask's request-scoped g.con: these writes happen on
    the pipeline worker thread, and SQLite connections are not shareable across
    threads."""
    con = sqlite3.connect(dbmod.DB_PATH, timeout=dbmod.BUSY_TIMEOUT_MS / 1000)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = %d" % dbmod.BUSY_TIMEOUT_MS)
    return con


def _table_exists(con, name):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _max_id(con, table, column):
    if not _table_exists(con, table):
        return 0
    return con.execute("SELECT COALESCE(MAX(%s), 0) FROM %s" % (column, table)).fetchone()[0]


def _open_run_record(started_at):
    """Best-effort. A run must not fail to start because its bookkeeping row
    could not be written — the staleness banner degrades to "never run", the
    pipeline itself is unaffected."""
    try:
        con = _run_con()
        try:
            if not _table_exists(con, "Pipeline_Run"):
                return None
            cur = con.execute(
                "INSERT INTO Pipeline_Run (started_at, status, trigger_source) "
                "VALUES (?, 'running', 'frontend')",
                (started_at,),
            )
            con.commit()
            return cur.lastrowid
        finally:
            con.close()
    except sqlite3.Error:
        return None


def _finish_run_record(run_id, status, steps):
    if run_id is None:
        return
    try:
        con = _run_con()
        try:
            if not _table_exists(con, "Pipeline_Run"):
                return
            con.execute(
                """
                UPDATE Pipeline_Run
                   SET finished_at = ?, status = ?,
                       steps_ok = ?, steps_skipped = ?, steps_failed = ?,
                       max_sale_id = ?, max_event_id = ?, max_closure_id = ?
                 WHERE run_id = ?
                """,
                (
                    datetime.now().isoformat(timespec="seconds"), status,
                    sum(1 for s in steps if s["status"] == "done"),
                    sum(1 for s in steps if s["status"] in ("skipped", "deselected")),
                    sum(1 for s in steps if s["status"] == "error"),
                    _max_id(con, "Fact_Sales", "sale_id"),
                    _max_id(con, "Event_Log", "event_id"),
                    _max_id(con, "Closure_Log", "closure_id"),
                    run_id,
                ),
            )
            con.commit()
        finally:
            con.close()
    except sqlite3.Error:
        pass


def get_staleness(con):
    """Is what the dashboard is showing older than what the store has typed in?

    Tally entries, flagged events and closures land in the database the moment
    they are saved, but fsn_class, Result_Forecast and Result_Prescriptive are
    only recomputed by a pipeline run. This compares the high-water marks
    recorded at the end of the last successful run against the ids in the
    database now: anything higher arrived afterwards and is therefore in no
    analytics currently on screen.

    `stale` is only true when there is something concrete to point at, so the
    banner never nags about nothing."""
    if not _table_exists(con, "Pipeline_Run"):
        return {
            "supported": False, "stale": False, "never_run": True, "running": False,
            "interrupted": None,
            "last_run": None, "pending": {}, "total_pending": 0,
            "reason": "This database predates the Pipeline_Run table — rebuild it with "
                      "scripts/create_schema.py to enable the staleness check.",
        }

    running = con.execute(
        "SELECT COUNT(*) FROM Pipeline_Run WHERE status = 'running'"
    ).fetchone()[0] > 0

    # A run that stopped part-way (Stop pressed, or a step failed) leaves the
    # database half-rebuilt: step3 may have rewritten fsn_class while step5
    # never got to recompute Result_Prescriptive off it. Nothing is pending in
    # the "new data" sense, so the id comparison below would report all-clear
    # on a state that is genuinely inconsistent - hence this separate check on
    # the most recent run of ANY status.
    latest = con.execute(
        "SELECT run_id, status, finished_at FROM Pipeline_Run ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    interrupted = (
        latest["status"] if latest is not None and latest["status"] in ("cancelled", "error") else None
    )

    last = con.execute(
        """
        SELECT run_id, started_at, finished_at, status, steps_skipped,
               max_sale_id, max_event_id, max_closure_id
          FROM Pipeline_Run
         WHERE status = 'done' AND finished_at IS NOT NULL
         ORDER BY run_id DESC LIMIT 1
        """
    ).fetchone()

    if last is None:
        # Nothing to compare against. Only worth flagging if there is manual
        # data sitting in the database that no run has ever consumed.
        pending = {
            "tally_entries": con.execute(
                "SELECT COUNT(*) FROM Fact_Sales WHERE tally_date_flag = 0"
            ).fetchone()[0],
            "events": con.execute("SELECT COUNT(*) FROM Event_Log").fetchone()[0],
            "closures": con.execute("SELECT COUNT(*) FROM Closure_Log").fetchone()[0],
        }
        total = sum(pending.values())
        return {
            "supported": True, "stale": total > 0 or interrupted is not None, "never_run": True,
            "running": running, "interrupted": interrupted, "last_run": None,
            "pending": pending, "total_pending": total,
            "reason": "No completed pipeline run has been recorded for this database.",
        }

    pending = {
        "tally_entries": con.execute(
            "SELECT COUNT(*) FROM Fact_Sales WHERE tally_date_flag = 0 AND sale_id > ?",
            (last["max_sale_id"] or 0,),
        ).fetchone()[0],
        "events": con.execute(
            "SELECT COUNT(*) FROM Event_Log WHERE event_id > ?", (last["max_event_id"] or 0,)
        ).fetchone()[0],
        "closures": con.execute(
            "SELECT COUNT(*) FROM Closure_Log WHERE closure_id > ?", (last["max_closure_id"] or 0,)
        ).fetchone()[0],
    }
    total = sum(pending.values())

    return {
        "supported": True,
        "stale": total > 0 or interrupted is not None,
        "never_run": False,
        "running": running,
        "interrupted": interrupted,
        "last_run": {
            "run_id": last["run_id"],
            "finished_at": last["finished_at"],
            "status": last["status"],
            "steps_skipped": last["steps_skipped"],
        },
        "pending": pending,
        "total_pending": total,
        "reason": None,
    }
