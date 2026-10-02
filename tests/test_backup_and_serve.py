"""
tests/test_backup_and_serve.py
------------------------------------------------------------------
Daily use (docs/SYSTEM_GAPS_AND_IMPROVEMENTS.md, 1.3):

  - backend/backup.py copies ustore.db with SQLite's backup API, including
    rows still in the WAL file, keeps only the newest copies, and restores
    one after saving the current database first;
  - the server's schedule backs up at start when the newest copy is a day
    old, and not again until a day has passed;
  - backend/serve.py serves the built dashboard for every non-API path, and
    never answers an /api/ path with the page.
------------------------------------------------------------------
"""
import os
import sqlite3
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

import backup  # noqa: E402  (backend/backup.py)


def make_db(path, rows):
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("CREATE TABLE IF NOT EXISTS Fact_Sales (sale_id INTEGER PRIMARY KEY, quantity_sold INTEGER)")
    con.executemany("INSERT INTO Fact_Sales (quantity_sold) VALUES (?)", [(r,) for r in rows])
    con.commit()
    return con          # left open: the last rows may still be in the -wal file


def total(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT COALESCE(SUM(quantity_sold), 0) FROM Fact_Sales").fetchone()[0]
    finally:
        con.close()


def test_backup_includes_rows_still_in_the_wal(tmp_path):
    db = tmp_path / "ustore.db"
    live = make_db(db, [3, 4, 5])
    copy = backup.take(db, tmp_path / "backups")
    live.close()
    assert copy.exists() and total(copy) == 12
    assert not list((tmp_path / "backups").glob("*.partial"))


def test_only_the_newest_copies_are_kept(tmp_path):
    db = tmp_path / "ustore.db"
    make_db(db, [1]).close()
    out = tmp_path / "backups"
    out.mkdir()
    for i in range(5):
        (out / f"ustore-2026010{i}-000000.db").write_bytes(b"old")
    backup.take(db, out, keep=3)
    kept = [p.name for p in backup.backups(out)]
    assert len(kept) == 3 and kept[0].startswith("ustore-2026") and "20260100" not in " ".join(kept)


def test_restore_saves_the_current_database_first(tmp_path):
    db, out = tmp_path / "ustore.db", tmp_path / "backups"
    make_db(db, [10]).close()
    good = backup.take(db, out)
    make_db(db, [5]).close()                      # a later entry...
    assert total(db) == 15
    safety = backup.restore(good, db, out)        # ...undone by the restore
    assert total(db) == 10
    assert safety is not None and "before-restore" in safety.name and total(safety) == 15


def test_a_damaged_file_is_not_restored(tmp_path):
    db, bad = tmp_path / "ustore.db", tmp_path / "bad.db"
    make_db(db, [7]).close()
    bad.write_bytes(b"not a database at all" * 100)
    with pytest.raises((backup.BackupError, sqlite3.DatabaseError)):
        backup.restore(bad, db, tmp_path / "backups")
    assert total(db) == 7


def test_schedule_backs_up_when_a_day_old_and_not_before(tmp_path):
    db, out = tmp_path / "ustore.db", tmp_path / "backups"
    make_db(db, [1]).close()
    sched = backup.BackupSchedule(interval=3600, log=lambda m: None, db_path=db, backup_dir=out)
    assert sched._due()                           # no backup yet
    first = backup.take(db, out)
    assert not sched._due()                       # just taken
    old = time.time() - 7200
    os.utime(first, (old, old))
    assert sched._due()                           # older than the interval


def test_dashboard_route_never_answers_api_paths(tmp_path):
    from flask import Flask
    import serve                                  # backend/serve.py
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    client = serve.attach_dashboard(Flask(__name__), dist).test_client()
    assert client.get("/").get_data(as_text=True) == "<html>app</html>"
    assert client.get("/assets/app.js").get_data(as_text=True) == "console.log(1)"
    assert client.get("/reorder").get_data(as_text=True) == "<html>app</html>"   # a reload on any screen
    assert client.get("/api/nothing").status_code == 404
