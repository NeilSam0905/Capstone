"""
serve.py - run USTore for daily use.

`python app.py` and `npm run dev` are development servers: one process each,
reloaders and debug tooling, not meant for several staff using them all day.
This serves the same Flask app with waitress (a production server that runs
on Windows) and the built dashboard from the same address, and takes a daily
backup of ustore.db while it runs (backup.BackupSchedule).

    cd "UST Prototype Design" && npm install && npm run build   # once, and after any frontend change
    cd backend && pip install -r requirements.txt
    python serve.py                              # http://localhost:8080, this computer only
    python serve.py --host 0.0.0.0               # also other computers on the store's network

Over the network, staff open http://<this computer's address>:8080. The
dashboard and the API share that address, so the login's origin check
(auth.origin_allowed) accepts it with no USTORE_ALLOWED_ORIGINS setting.
Change the default staff password before opening it to the network.

Development is unchanged: `python app.py` plus `npm run dev`.
"""
import argparse
import sys
from pathlib import Path

from flask import abort, send_from_directory

import app as backend
import backup

DIST = Path(__file__).resolve().parent.parent / "UST Prototype Design" / "dist"


def attach_dashboard(flask_app, dist=DIST):
    """Serve the built dashboard: a file when one exists at that path, else
    index.html, so a page reload on any screen still loads the app. Paths
    under /api/ are never answered with the page: an unknown API path is a
    404, not HTML a script would choke on."""

    @flask_app.get("/")
    @flask_app.get("/<path:path>")
    def dashboard(path="index.html"):
        if path.startswith("api/"):
            abort(404)
        if (dist / path).is_file():
            return send_from_directory(dist, path)
        return send_from_directory(dist, "index.html")

    return flask_app


def main():
    ap = argparse.ArgumentParser(description="Serve USTore (API + dashboard) for daily use.")
    ap.add_argument("--host", default="127.0.0.1",
                    help="127.0.0.1 = this computer only (default); 0.0.0.0 = the store's network too")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--no-backup", action="store_true", help="do not take the daily database backup")
    args = ap.parse_args()

    if not (DIST / "index.html").exists():
        sys.exit(f"No built dashboard at {DIST}. Build it first:\n"
                 f'    cd "UST Prototype Design" && npm install && npm run build')

    from waitress import serve                     # imported here so --help works without it
    attach_dashboard(backend.app)
    if not args.no_backup:
        backup.BackupSchedule(log=lambda msg: print(msg, flush=True)).start()
    shown = "localhost" if args.host in ("127.0.0.1", "0.0.0.0") else args.host
    print(f"USTore: http://{shown}:{args.port}  (Ctrl+C to stop)", flush=True)
    serve(backend.app, host=args.host, port=args.port, threads=args.threads)


if __name__ == "__main__":
    main()
