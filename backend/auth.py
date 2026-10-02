"""
auth.py — who is using the API (docs/SYSTEM_GAPS_AND_IMPROVEMENTS.md, 1.2).

Before this, every route was open and `CORS(app)` let any website call them;
only binding to localhost kept that safe. Three things change here:

  - **A login.** Accounts live in App_User (ustore.db), passwords stored as
    werkzeug hashes, never in plain text. The first login attempt against an
    empty table creates the default staff account (DEFAULT_USERS). A signed
    session cookie (HttpOnly, SameSite=Lax) carries the username afterwards.
  - **Origin checks.** CORS is limited to ALLOWED_ORIGINS, and every write
    whose Origin header is not one of them is refused outright. CORS alone
    only stops another site from READING a response - a plain form POST from
    another page on the same machine would still carry the session cookie.
  - **Attribution.** `current_user()` is what app.py writes into
    Fact_Sales.entered_by, Event_Log.created_by, Closure_Log.created_by and
    Inventory_Count.counted_by.

Configuration (environment, all optional):

    USTORE_SECRET_KEY        signs the session cookie. Unset: one is generated
                             once and kept in backend/.secret_key (gitignored),
                             so a restart does not sign everyone out.
    USTORE_ALLOWED_ORIGINS   comma-separated dashboard addresses, for when the
                             frontend is served from anywhere other than the
                             Vite defaults below (e.g. http://192.168.1.20:5173).
"""
import os
import secrets
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import session
from werkzeug.security import check_password_hash, generate_password_hash

import db as dbmod

# The account the store starts with. Created only when App_User is empty, so
# changing or removing it later is not undone by a restart.
DEFAULT_USERS = (("staff", "staff123"),)

SESSION_LIFETIME = timedelta(hours=12)      # one working day, then sign in again

# Vite's dev server (5173) and `vite preview` (4173), by both spellings of the
# local host. The dev server proxies /api, so the browser's Origin is the
# dashboard's address, not the backend's.
DEFAULT_ORIGINS = (
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:4173", "http://127.0.0.1:4173",
)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Login throttling: after MAX_FAILURES wrong passwords from one address, that
# address waits LOCKOUT_SECONDS. In memory - a restart clears it, which is fine
# for slowing down guessing, the only thing it is for.
MAX_FAILURES = 5
LOCKOUT_SECONDS = 60

SECRET_FILE = Path(__file__).resolve().parent / ".secret_key"

# Compared against when the username does not exist, so a wrong username and
# a wrong password take the same time and cannot be told apart.
_DUMMY_HASH = generate_password_hash(secrets.token_hex(16))

_failures = {}                              # remote addr -> (count, first_failure_at)
_failures_lock = threading.Lock()


def allowed_origins():
    extra = os.environ.get("USTORE_ALLOWED_ORIGINS", "")
    return [*DEFAULT_ORIGINS, *(o.strip().rstrip("/") for o in extra.split(",") if o.strip())]


def secret_key():
    """The session-signing key: the environment's, else the one kept on disk,
    else a new one written there. If the file cannot be written the key still
    works for this run; sessions just end at the next restart."""
    env = os.environ.get("USTORE_SECRET_KEY")
    if env:
        return env
    try:
        key = SECRET_FILE.read_text(encoding="utf-8").strip()
        if key:
            return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    try:
        SECRET_FILE.write_text(key, encoding="utf-8")
    except OSError:
        pass
    return key


def configure(app):
    app.config.update(
        SECRET_KEY=secret_key(),
        SESSION_COOKIE_NAME="ustore_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=SESSION_LIFETIME,
    )


def origin_allowed(request):
    """A write is allowed from the dashboard's own address, from the backend's
    own address, or with no Origin at all (not a browser: curl, the tests).
    A browser always sends Origin on a cross-site write, so a missing one is
    not a way around this."""
    origin = request.headers.get("Origin")
    if not origin:
        return True
    origin = origin.rstrip("/")
    return origin in allowed_origins() or origin == request.host_url.rstrip("/")


def current_user():
    return session.get("user")


def _ensure_users(c):
    c.execute(dbmod.APP_USER_DDL)
    if c.execute("SELECT COUNT(*) FROM App_User").fetchone()[0] == 0:
        now = datetime.now().isoformat(timespec="seconds")
        c.executemany(
            "INSERT INTO App_User (username, password_hash, created_at) VALUES (?, ?, ?)",
            [(u, generate_password_hash(p), now) for u, p in DEFAULT_USERS],
        )
        c.commit()


def locked_for(addr):
    """Seconds this address must still wait before another attempt, or 0."""
    with _failures_lock:
        count, since = _failures.get(addr, (0, 0.0))
        if count < MAX_FAILURES:
            return 0
        left = LOCKOUT_SECONDS - (time.monotonic() - since)
        if left <= 0:
            _failures.pop(addr, None)
            return 0
        return int(left) + 1


def _record_failure(addr):
    with _failures_lock:
        count, since = _failures.get(addr, (0, time.monotonic()))
        _failures[addr] = (count + 1, since if count else time.monotonic())


def check_login(c, username, password, addr):
    """The username if the credentials are right, else None. Usernames are
    matched case-insensitively ("Staff" signs in as staff); passwords exactly."""
    _ensure_users(c)
    username = str(username or "").strip().lower()
    row = dbmod.one(c, "SELECT username, password_hash FROM App_User WHERE LOWER(username) = ?",
                    (username,))
    ok = check_password_hash(row["password_hash"] if row else _DUMMY_HASH, str(password or ""))
    if not (row and ok):
        _record_failure(addr)
        return None
    with _failures_lock:
        _failures.pop(addr, None)
    return row["username"]


def sign_in(username):
    session.clear()                         # nothing from before the login carries over
    session.permanent = True
    session["user"] = username


def sign_out():
    session.clear()
