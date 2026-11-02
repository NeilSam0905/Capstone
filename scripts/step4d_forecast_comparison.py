"""
step4d - forecast with every other model, for the Demand Forecast page's
comparison.

step4_forecast_model.py and step4c_category_forecast.py make the recommended
models' forecasts, which the whole dashboard reads. This runs the same two
scripts once for each OTHER model the page offers (scripts/forecast_options.py),
with --compare, so their forecasts and accuracy checks land in the comparison
tables and the page's model dropdown can switch between all of them. Nothing
else reads those tables, so no figure outside that page changes.

Categories first (seconds each), then items. Prophet takes about a minute for
categories and about five for items. One model failing does not stop the
others; the step fails at the end, naming them, so the run reports it.

Usage:
    python scripts/step4d_forecast_comparison.py
"""
import os
import sqlite3
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import forecast_options as fo  # noqa: E402

SCRIPTS = {fo.CATEGORY: "scripts/step4c_category_forecast.py",
           fo.ITEM: "scripts/step4_forecast_model.py"}
DB_PATH = os.path.join(ROOT, "ustore.db")


def drop_retired():
    """Comparison rows for a model no longer offered would sit there unread;
    drop them so the tables hold exactly what the page can show."""
    con = sqlite3.connect(DB_PATH)
    try:
        for scope, tables in fo.COMPARE_TABLES.items():
            keep = fo.alternatives(scope)
            for t in tables:
                if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone():
                    con.execute(f"DELETE FROM {t} WHERE model NOT IN ({','.join('?' * len(keep))})", keep)
        con.commit()
    finally:
        con.close()


def main():
    drop_retired()
    failed = []
    for scope in (fo.CATEGORY, fo.ITEM):
        for model in fo.alternatives(scope):
            print(f"--- {scope} forecasts: {model}", flush=True)
            started = time.time()
            r = subprocess.run([sys.executable, "-u", SCRIPTS[scope], "--model", model, "--compare"],
                               cwd=ROOT, capture_output=True, text=True)
            tail = (r.stdout or "").strip().splitlines()[-1:] or [""]
            if r.returncode == 0:
                print(f"    done in {time.time() - started:.0f} s. {tail[0]}", flush=True)
            else:
                err = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or ["no output"]
                print(f"    FAILED: {err[0]}", flush=True)
                failed.append(f"{scope} {model}")
    if failed:
        print(f"Not made for comparison: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("Every model's forecast is ready for comparison on the Demand Forecast page.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
