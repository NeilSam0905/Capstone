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
- step4 requires cmdstan, which per the repo README is a ~20-30 minute
  one-time native build and is not assumed to be installed.

If either fails, the run is recorded as "skipped" for that step and the
pipeline continues — /api/meta already reports forecast availability as
an honest pending state, so a missing forecast is not a reason to block
step5a/step5, neither of which read Result_Forecast.

Steps run via Popen rather than subprocess.run so stop_pipeline() can hold
a handle to the in-flight child process and terminate it — a plain
subprocess.run() blocks the worker thread until the process exits on its
own, giving a "stop" button nothing to actually stop.
"""
import copy
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# (id, script path relative to repo root, human label, optional)
STEPS = [
    ("create_schema", "scripts/create_schema.py", "Build database schema", False),
    ("populate_dim_date", "scripts/populate_dim_date.py", "Populate calendar dimension", False),
    ("step0", "scripts/step0_convert_sales_with_zeros.py", "Convert raw tally sheets", True),
    ("step1", "scripts/step1_apply_mapping.py", "Apply vocabulary + supplier mapping", False),
    ("allocation", "scripts/proportional_allocation.py", "Allocate price-grouped rows to SKUs", False),
    ("step2", "scripts/step2_load_fact_sales.py", "Load Fact_Sales", False),
    ("step3", "scripts/step3_fsn_classification.py", "Classify Fast / Slow / Non-moving", False),
    ("step4", "scripts/step4_prophet_forecast.py", "Forecast demand (Prophet)", True),
    ("step5a", "scripts/step5a_set_lead_times.py", "Set supplier lead times", False),
    ("step5", "scripts/step5_prescriptive.py", "Compute ROP / EOQ / safety stock", False),
]

_lock = threading.Lock()
_cancel_requested = threading.Event()
_current_proc = None  # Popen handle for whichever step is running right now, if any


def _fresh_steps():
    return [
        {
            "id": sid, "label": label, "optional": optional,
            "status": "pending", "output": "", "error": None, "duration_s": None,
        }
        for sid, _script, label, optional in STEPS
    ]


_state = {"status": "idle", "started_at": None, "finished_at": None, "steps": _fresh_steps()}


def get_status():
    with _lock:
        return copy.deepcopy(_state)


def _run_all():
    global _current_proc
    ok_overall = True
    cancelled = False

    for (sid, script, label, optional), entry in zip(STEPS, _state["steps"]):
        if _cancel_requested.is_set():
            cancelled = True
            break

        with _lock:
            entry["status"] = "running"
        started = time.time()
        try:
            proc = subprocess.Popen(
                [sys.executable, script], cwd=REPO_ROOT,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            with _lock:
                _current_proc = proc
            stdout, stderr = proc.communicate()
            returncode = proc.returncode
        except OSError as exc:
            returncode, stdout, stderr = 1, "", str(exc)
        finally:
            with _lock:
                _current_proc = None

        duration = round(time.time() - started, 1)

        if _cancel_requested.is_set():
            with _lock:
                entry["duration_s"] = duration
                entry["status"] = "cancelled"
            cancelled = True
            break

        failed = returncode != 0
        with _lock:
            entry["duration_s"] = duration
            entry["output"] = (stdout or "")[-4000:]
            if failed:
                entry["error"] = (stderr or "").strip()[-4000:] or f"exited with code {returncode}"
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


def start_pipeline():
    """Returns True if a run was started, False if one is already in progress."""
    with _lock:
        if _state["status"] == "running":
            return False
        _state["status"] = "running"
        _state["started_at"] = datetime.now().isoformat(timespec="seconds")
        _state["finished_at"] = None
        _state["steps"] = _fresh_steps()
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
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except OSError:
            pass
    return True
