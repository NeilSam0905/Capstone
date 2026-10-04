"""
scripts/vault.py
------------------------------------------------------------------
Keeps the store's data encrypted wherever it leaves this machine.

The pipeline, the backend and every script read and write PLAIN files at the
paths they always have (ustore.db, data/*.csv, ...). Those plain files are now
gitignored and never committed. What git carries instead is vault/: one
AES-256-GCM encrypted copy of each, plus a manifest. Nothing in the pipeline
changes; only what crosses into git does.

  lock     encrypt every protected file that changed since the last sync
  unlock   decrypt every file the vault has that is missing or newer here
  status   show what is in sync, what needs locking / unlocking
  init-key create the key (once, by whoever sets the vault up)

Typical use
-----------
  after a pipeline run from the Tally Interface   nothing - the run re-locks
  after running scripts by hand                   python scripts/vault.py lock
  after a git pull                                python scripts/vault.py unlock
                                                  (the backend also does this on start)
  before committing                               the pre-commit hook refuses a
                                                  commit if the vault is stale or
                                                  a plain data file is staged

The key
-------
USTORE_KEY in backend/.env (gitignored), or the USTORE_KEY environment
variable. 32 random bytes, base64. Share it with teammates privately, never
through git. LOSE IT AND THE VAULT CANNOT BE OPENED - keep a copy somewhere
safe (a password manager).

How "changed" is decided
------------------------
vault/manifest.json holds, per file, a keyed hash (HMAC-SHA256) of its plain
contents - it says whether two copies match without revealing anything
without the key. .vault_state.json (gitignored, per machine) records the hash
at this machine's last lock/unlock. Comparing the three tells a local edit
("lock it") from a teammate's newer vault ("unlock it") from both at once
("conflict" - resolved only with an explicit --prefer-local / --prefer-vault,
never by silently overwriting someone's work).

File format: b"USTV1\\n" + 12-byte nonce + AES-GCM ciphertext and tag, with the
file's repo-relative path as associated data (a file cannot be swapped for
another's ciphertext unnoticed). Encryption and hashing use separate keys
derived from USTORE_KEY with HKDF.

Run:  python scripts/vault.py {status|lock|unlock|init-key} [--prefer-local|--prefer-vault]
------------------------------------------------------------------
"""
import argparse
import base64
import fnmatch
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ROOT = Path(__file__).resolve().parent.parent
VAULT = ROOT / "vault"
MANIFEST = VAULT / "manifest.json"
STATE = ROOT / ".vault_state.json"
ENV_FILE = ROOT / "backend" / ".env"
KEY_VAR = "USTORE_KEY"
MAGIC = b"USTV1\n"

# What is protected: store sales, prices, stock, forecasts and every result
# derived from them. Paths are repo-relative, "/"-separated.
PROTECTED = ["ustore.db", "data/*.csv", "data/*.xlsx", "docs/*.csv", "*.xlsx"]
# Patterns that hold at ANY depth beneath their directory, not just directly
# inside it. Without this, data/pre_contract/*.csv - store-derived results, the
# pre-contract prescriptive preview among them - were outside the guard
# entirely: is_protected() pairs fnmatch with a depth check, and the depth check
# is what makes "*.xlsx" mean the repo root rather than every folder. Two files
# sat there tracked in plain form while the hook reported nothing to refuse.
RECURSIVE = {"data/*.csv", "data/*.xlsx", "docs/*.csv"}
# The 131 MB raw inventory workbook is over GitHub's 100 MB file limit even
# encrypted; it stays local-only, as it already was (.gitignore).
EXCLUDED = ["Copy of USTORE INVENTORY REPORT (1).xlsx"]
DB_FILES = {"ustore.db"}


class VaultError(Exception):
    pass


# ------------------------------------------------------------------ key
def _read_key_text():
    if os.environ.get(KEY_VAR):
        return os.environ[KEY_VAR].strip()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == KEY_VAR:
                return value.strip().strip('"').strip("'")
    return None


def load_keys():
    text = _read_key_text()
    if not text:
        raise VaultError(f"no {KEY_VAR} found in the environment or {ENV_FILE.relative_to(ROOT)} - "
                         f"get the key from whoever set up the vault (or run `init-key` if there is no vault yet)")
    try:
        master = base64.urlsafe_b64decode(text.encode())
    except ValueError as e:
        raise VaultError(f"{KEY_VAR} is not valid base64") from e
    if len(master) != 32:
        raise VaultError(f"{KEY_VAR} must decode to 32 bytes, got {len(master)}")

    def derive(label):
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=label).derive(master)
    return derive(b"ustore-vault encrypt"), derive(b"ustore-vault mac")


def init_key():
    if _read_key_text():
        raise VaultError(f"a {KEY_VAR} already exists - not replacing it (that would lock you out of the vault)")
    if MANIFEST.exists():
        raise VaultError("vault/ already exists, so a key already exists somewhere - get it from its owner")
    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    with ENV_FILE.open("a", encoding="utf-8") as f:
        f.write(f"\n# Encryption key for vault/ (scripts/vault.py). Never commit it; lose it and\n"
                f"# the vault cannot be opened, so keep a copy in a password manager.\n{KEY_VAR}={key}\n")
    print(f"Wrote a new {KEY_VAR} to {ENV_FILE.relative_to(ROOT)}. Back it up somewhere safe now.")


# ------------------------------------------------------------ helpers
def mac_of(mac_key, rel, data):
    return hmac.new(mac_key, rel.encode() + b"\0" + data, hashlib.sha256).hexdigest()


def encrypt(enc_key, rel, data):
    nonce = secrets.token_bytes(12)
    return MAGIC + nonce + AESGCM(enc_key).encrypt(nonce, data, rel.encode())


def decrypt(enc_key, rel, blob):
    if not blob.startswith(MAGIC):
        raise VaultError(f"vault/{rel}.enc is not a vault file")
    n = len(MAGIC)
    try:
        return AESGCM(enc_key).decrypt(blob[n:n + 12], blob[n + 12:], rel.encode())
    except Exception as e:
        raise VaultError(f"vault/{rel}.enc could not be decrypted - wrong key, or the file is damaged") from e


def _write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".vaulttmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save_json(path, obj):
    _write_atomic(path, (json.dumps(obj, indent=1, sort_keys=True) + "\n").encode())


def is_protected(rel):
    rel = rel.replace("\\", "/")
    if rel in EXCLUDED:
        return False
    # fnmatch's "*" spans "/" too, so the depth check is what makes "*.xlsx"
    # mean the repo root rather than every folder. Patterns in RECURSIVE opt out
    # of it: store data is store data however deeply it is filed.
    for p in PROTECTED:
        if fnmatch.fnmatch(rel, p) and (p in RECURSIVE or rel.count("/") == p.count("/")):
            return True
    return False


def local_files():
    """Every plain protected file on disk.

    RECURSIVE patterns are globbed with rglob, because Path.glob's "*" stops at
    a directory boundary where fnmatch's does not. Without that split,
    is_protected() would refuse a file the hook sees while local_files() never
    collected it - so `lock` could not store what `check-commit` would not let
    you commit, and the file could be neither vaulted nor committed.
    """
    out = set()
    for pat in PROTECTED:
        head, _, tail = pat.rpartition("/")
        paths = ((ROOT / head).rglob(tail) if pat in RECURSIVE and head
                 else ROOT.glob(pat))
        for p in paths:
            rel = p.relative_to(ROOT).as_posix()
            if p.is_file() and is_protected(rel) and not p.name.startswith("~$"):
                out.add(rel)
    return out


def _checkpoint(rel):
    """Fold the WAL into ustore.db so its bytes are the whole database. Refuses
    if something is mid-write (the WAL is still not empty afterwards)."""
    path = ROOT / rel
    con = sqlite3.connect(path, timeout=30)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
    wal = path.with_name(path.name + "-wal")
    if wal.exists() and wal.stat().st_size > 0:
        raise VaultError(f"{rel} is being written to right now - stop the pipeline / backend writes and retry")


# ------------------------------------------------------------- status
def status(mac_key):
    """{rel: state}, one of: ok, new, local-changed, vault-newer, missing, conflict."""
    manifest, synced = _load_json(MANIFEST), _load_json(STATE)
    out = {}
    for rel in sorted(local_files() | set(manifest)):
        path = ROOT / rel
        if not path.exists():
            out[rel] = "missing"
            continue
        if rel in DB_FILES:
            wal = path.with_name(path.name + "-wal")
            if wal.exists() and wal.stat().st_size > 0:
                _checkpoint(rel)
        cur = mac_of(mac_key, rel, path.read_bytes())
        man, last = manifest.get(rel), synced.get(rel)
        if man is None:
            out[rel] = "new"
        elif cur == man:
            out[rel] = "ok"
        elif last == cur:
            out[rel] = "vault-newer"
        elif last == man:
            out[rel] = "local-changed"
        else:
            out[rel] = "conflict"
    return out


# ---------------------------------------------------------- lock/unlock
def lock(prefer_local=False, quiet=False):
    enc_key, mac_key = load_keys()
    st = status(mac_key)
    blocked = [r for r, s in st.items() if s == "vault-newer" or (s == "conflict" and not prefer_local)]
    if blocked:
        raise VaultError("the vault has newer copies of: " + ", ".join(blocked) +
                         " - run `unlock` first (or `lock --prefer-local` to overwrite them with yours)")
    manifest, synced = _load_json(MANIFEST), _load_json(STATE)
    done = []
    for rel, s in st.items():
        if s in ("new", "local-changed", "conflict"):
            if rel in DB_FILES:
                _checkpoint(rel)
            data = (ROOT / rel).read_bytes()
            _write_atomic(VAULT / (rel + ".enc"), encrypt(enc_key, rel, data))
            manifest[rel] = synced[rel] = mac_of(mac_key, rel, data)
            done.append(rel)
        elif s == "ok":
            synced[rel] = manifest[rel]
    _save_json(MANIFEST, manifest)
    _save_json(STATE, synced)
    if not quiet:
        print(f"locked {len(done)} changed file(s)" + (": " + ", ".join(done) if done and len(done) <= 10 else ""))
        missing = [r for r, s in st.items() if s == "missing"]
        if missing:
            print(f"note: {len(missing)} vault file(s) have no plain copy here - run `unlock` to restore them")
    return done


def unlock(prefer_vault=False, quiet=False, only_safe=False):
    """only_safe: restore missing / vault-newer files and silently leave anything
    that would overwrite local work (what the backend does on start)."""
    enc_key, mac_key = load_keys()
    st = status(mac_key)
    blocked = [r for r, s in st.items() if s == "local-changed" or (s == "conflict" and not prefer_vault)]
    if blocked and not only_safe:
        raise VaultError("these have local changes not yet locked: " + ", ".join(blocked) +
                         " - run `lock` first (or `unlock --prefer-vault` to discard them)")
    manifest, synced = _load_json(MANIFEST), _load_json(STATE)
    done = []
    for rel, s in st.items():
        take = s in ("missing", "vault-newer") or (s == "conflict" and prefer_vault and not only_safe) \
            or (s == "local-changed" and prefer_vault and not only_safe)
        if take:
            data = decrypt(enc_key, rel, (VAULT / (rel + ".enc")).read_bytes())
            if mac_of(mac_key, rel, data) != manifest[rel]:
                raise VaultError(f"vault/{rel}.enc does not match the manifest - pull again")
            if rel in DB_FILES and (ROOT / rel).exists():
                _checkpoint(rel)
                for side in ("-wal", "-shm"):   # stale sidecars of the old file must not be applied to the new one
                    (ROOT / (rel + side)).unlink(missing_ok=True)
            _write_atomic(ROOT / rel, data)
            synced[rel] = manifest[rel]
            done.append(rel)
        elif s == "ok":
            synced[rel] = manifest[rel]
    _save_json(STATE, synced)
    if not quiet:
        print(f"unlocked {len(done)} file(s)" + (": " + ", ".join(done) if done and len(done) <= 10 else ""))
    return done


def ensure_unlocked():
    """For the backend and pipeline: bring back anything missing or updated in
    the vault, never overwrite local work. Returns a one-line message, or None
    when there is nothing to report. Never raises - a missing key must not stop
    the app from serving the plain files that are already here."""
    if not MANIFEST.exists():
        return None
    try:
        done = unlock(quiet=True, only_safe=True)
        return f"vault: restored {len(done)} file(s)" if done else None
    except (VaultError, sqlite3.Error, OSError) as e:
        return f"vault: could not unlock ({e})"


def check_commit():
    """Pre-commit hook: no plain protected file staged, and the vault current."""
    staged = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"],
                            cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\0")
    plain = [p for p in staged if p and is_protected(p)]
    if plain:
        raise VaultError("these plain data files are staged - unstage them (`git restore --staged <file>`); "
                         "only vault/ may be committed:\n  " + "\n  ".join(plain))
    _, mac_key = load_keys()
    stale = {r: s for r, s in status(mac_key).items() if s in ("new", "local-changed", "conflict")}
    if stale:
        raise VaultError("the vault is behind your data (" + ", ".join(sorted(stale)[:8]) +
                         (" ..." if len(stale) > 8 else "") + ") - run `python scripts/vault.py lock`, "
                         "then `git add vault` and commit again")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    ap.add_argument("command", choices=["status", "lock", "unlock", "init-key", "check-commit"])
    ap.add_argument("--prefer-local", action="store_true", help="lock: overwrite newer vault copies")
    ap.add_argument("--prefer-vault", action="store_true", help="unlock: discard local changes")
    args = ap.parse_args()
    try:
        if args.command == "init-key":
            init_key()
        elif args.command == "lock":
            lock(prefer_local=args.prefer_local)
        elif args.command == "unlock":
            unlock(prefer_vault=args.prefer_vault)
        elif args.command == "check-commit":
            check_commit()
        else:
            _, mac_key = load_keys()
            st = status(mac_key)
            counts = {}
            for rel, s in st.items():
                counts[s] = counts.get(s, 0) + 1
                if s != "ok":
                    print(f"  {s:14s} {rel}")
            print("  " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())))
    except VaultError as e:
        print(f"vault: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
