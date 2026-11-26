"""
set_password.py - change a dashboard account's password (QA-026).

The store starts with two accounts, staff / staff123 and admin / admin123
(auth.DEFAULT_USERS). Change both before the dashboard goes on the store's
network:

    python backend/set_password.py staff
    python backend/set_password.py admin

It asks for the new password twice (nothing is echoed or saved in the shell
history) and stores only its hash in App_User, as the login expects. The
running backend does not need a restart; people already signed in stay signed
in until their session ends. The admin's password is also the one staff type to
confirm an edit (X-Admin-Password), so tell whoever needs it.
"""
import argparse
import getpass
import sqlite3
import sys
from datetime import datetime

from werkzeug.security import generate_password_hash

import auth
import db as dbmod

MIN_LENGTH = 8


def set_password(con, username, password):
    """Store `password` for `username` (case-insensitive). Returns False when
    there is no such account."""
    auth._ensure_users(con)                 # creates the default accounts on an empty table
    row = con.execute("SELECT username FROM App_User WHERE LOWER(username) = ?",
                      (username.strip().lower(),)).fetchone()
    if row is None:
        return False
    con.execute("UPDATE App_User SET password_hash = ? WHERE username = ?",
                (generate_password_hash(password), row[0]))
    con.commit()
    return True


def main():
    ap = argparse.ArgumentParser(description="Change a dashboard account's password.")
    ap.add_argument("username", help="staff or admin (or any account in App_User)")
    args = ap.parse_args()
    if not dbmod.DB_PATH.exists():
        sys.exit(f"{dbmod.DB_PATH} not found.")
    first = getpass.getpass(f"New password for {args.username}: ")
    if len(first) < MIN_LENGTH:
        sys.exit(f"Use at least {MIN_LENGTH} characters. Nothing changed.")
    if first in dict(auth.DEFAULT_USERS).values():
        sys.exit("That is one of the default passwords. Nothing changed.")
    if getpass.getpass("Type it again: ") != first:
        sys.exit("The two did not match. Nothing changed.")
    con = sqlite3.connect(dbmod.DB_PATH, timeout=30)
    try:
        if not set_password(con, args.username, first):
            sys.exit(f"No account called '{args.username}'. Nothing changed.")
    finally:
        con.close()
    print(f"Password changed for {args.username} at {datetime.now():%Y-%m-%d %H:%M}.")


if __name__ == "__main__":
    main()
