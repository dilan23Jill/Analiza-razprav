"""
SQLite database for persisting debate analyses and user accounts.

Tables:
  users    — registered users (email + hashed password)
  sessions — auth tokens (one per login, 30-day expiry)
  debates  — one row per completed analysis (metadata + full JSON results)

Thread-safe: uses a lock around writes and creates connections per-call.
"""

import hashlib
import json
import logging
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DB_PATH = Path("data/debates.db")
_write_lock = threading.Lock()

SESSION_TTL_DAYS = 30


def _utcnow() -> datetime:
    """Naive UTC timestamp — same isoformat shape as the old datetime.utcnow(),
    so stored timestamps and fromisoformat comparisons remain consistent."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _connect() -> sqlite3.Connection:
    """Create a new connection (safe for multi-threaded use)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ── SCHEMA ───────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create tables (with current full schema) and run migrations on existing DBs.

    Both fresh and pre-existing databases end up in the same final shape.
    """
    with _write_lock:
        conn = _connect()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    username      TEXT NOT NULL UNIQUE,
                    email         TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    credits       INTEGER NOT NULL DEFAULT 1,
                    is_admin      INTEGER NOT NULL DEFAULT 0,
                    created_at    TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    token      TEXT PRIMARY KEY,
                    user_id    INTEGER NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

                CREATE TABLE IF NOT EXISTS debates (
                    id              TEXT PRIMARY KEY,
                    user_id         INTEGER REFERENCES users(id),
                    youtube_url     TEXT NOT NULL DEFAULT '',
                    mode            TEXT NOT NULL DEFAULT 'solo',
                    language        TEXT NOT NULL DEFAULT 'sl',
                    title           TEXT DEFAULT '',
                    topic           TEXT,
                    speakers        TEXT,
                    speaker_names   TEXT,
                    status          TEXT NOT NULL DEFAULT 'completed',

                    analysis_json   TEXT,
                    fact_check_json TEXT,
                    report_text     TEXT,
                    transcript_text TEXT DEFAULT '',

                    summary         TEXT,
                    -- accuracy_rate: opuščen stolpec, ohranjen zaradi starih vrstic
                    accuracy_rate   REAL,

                    created_at      TEXT NOT NULL,
                    duration_sec    REAL,
                    ip_address      TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_debates_created ON debates(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_debates_topic   ON debates(topic);
                CREATE INDEX IF NOT EXISTS idx_debates_user    ON debates(user_id);
            """)
            _run_migrations(conn)
            conn.commit()
        finally:
            conn.close()
    logger.info("Database initialized: %s", DB_PATH)


def _run_migrations(conn) -> None:
    """Bring older databases up to current schema. Idempotent — safe to call always.

    Each ALTER is gated on PRAGMA table_info so it only runs once.
    """
    debate_cols = {row[1] for row in conn.execute("PRAGMA table_info(debates)").fetchall()}
    if "user_id" not in debate_cols:
        conn.execute("ALTER TABLE debates ADD COLUMN user_id INTEGER REFERENCES users(id)")
        logger.info("Migration: added user_id column to debates")
    if "speaker_names" not in debate_cols:
        conn.execute("ALTER TABLE debates ADD COLUMN speaker_names TEXT")
        logger.info("Migration: added speaker_names column to debates")
    if "title" not in debate_cols:
        conn.execute("ALTER TABLE debates ADD COLUMN title TEXT DEFAULT ''")
        logger.info("Migration: added title column to debates")
    if "transcript_text" not in debate_cols:
        conn.execute("ALTER TABLE debates ADD COLUMN transcript_text TEXT DEFAULT ''")
        logger.info("Migration: added transcript_text column to debates")

    # The genre label was a free model guess that nothing depended on and that
    # was shown untranslated; it is gone. Drop it from older databases too.
    if "format" in debate_cols:
        try:
            conn.execute("ALTER TABLE debates DROP COLUMN format")
            logger.info("Migration: dropped format column from debates")
        except Exception as e:
            logger.info("Migration: could not drop format column (%s) — leaving it unused", e)

    # The app no longer declares a winner; drop the leftover column from older
    # databases. DROP COLUMN needs SQLite 3.35+ — on older builds the column is
    # simply left in place, unused and never written to again.
    if "winner" in debate_cols:
        try:
            conn.execute("ALTER TABLE debates DROP COLUMN winner")
            logger.info("Migration: dropped winner column from debates")
        except Exception as e:
            logger.info("Migration: could not drop winner column (%s) — leaving it unused", e)

    # Leftover from an abandoned semantic-search idea: a chunk table with an
    # `embedding` column that nothing ever wrote to and nothing ever read. A
    # table no code touches is the schema equivalent of dead code — anyone
    # inspecting the database has to work out that it means nothing.
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='debate_chunks'"
    ).fetchone():
        try:
            conn.execute("DROP TABLE debate_chunks")
            logger.info("Migration: dropped unused debate_chunks table")
        except Exception as e:
            logger.info("Migration: could not drop debate_chunks (%s) — leaving it", e)

    # Privacy migration: scrub any previously stored transcript payloads.
    purged = conn.execute(
        "UPDATE debates SET transcript_text = '' "
        "WHERE transcript_text IS NOT NULL AND trim(transcript_text) != ''"
    ).rowcount
    if purged:
        logger.info("Migration: purged transcript_text for %d debate(s)", purged)

    user_cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "credits" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN credits INTEGER NOT NULL DEFAULT 1")
        logger.info("Migration: added credits column to users")
    if "is_admin" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
        logger.info("Migration: added is_admin column to users")


def ensure_admin_user() -> None:
    """Ob zagonu aplikacije zagotovi, da ima user_id=1 admin pravice in 100 kreditov.
    (Migracije so v init_db / _run_migrations — TUKAJ NE.)"""
    try:
        with _write_lock:
            conn = _connect()
            try:
                admin_changed = conn.execute(
                    "UPDATE users SET is_admin = 1 WHERE id = 1"
                ).rowcount
                credits_changed = conn.execute(
                    "UPDATE users SET credits = 100 WHERE id = 1"
                ).rowcount
                conn.commit()
                if admin_changed > 0 or credits_changed > 0:
                    logger.info("Initial admin setup: user_id=1 is now ADMIN with 100 credits")
                else:
                    logger.info("user_id=1 does not exist yet (will be set on first registration)")
            finally:
                conn.close()
    except Exception as e:
        logger.error("Failed to run ensure_admin_user: %s", e)


# ── PASSWORD HASHING ─────────────────────────────────────────────────────────

def _hash_password(password: str, salt: str = "") -> str:
    if not salt:
        salt = secrets.token_hex(16)
    h = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}${h}"


def _verify_password(password: str, stored_hash: str) -> bool:
    if "$" not in stored_hash:
        return False
    salt, _ = stored_hash.split("$", 1)
    return _hash_password(password, salt) == stored_hash


# ── USER CRUD ────────────────────────────────────────────────────────────────

def create_user(username: str, email: str, password: str) -> Optional[Dict]:
    """Register a new user. Returns user dict or None if username/email taken."""
    pw_hash = _hash_password(password)
    now = _utcnow().isoformat()

    with _write_lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO users (username, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (username.strip(), email.strip().lower(), pw_hash, now),
            )
            conn.commit()
            user_id = conn.execute(
                "SELECT id FROM users WHERE username = ?", (username.strip(),)
            ).fetchone()["id"]
            return {"id": user_id, "username": username.strip(), "email": email.strip().lower(),
                    "credits": 1, "is_admin": False}
        except sqlite3.IntegrityError:
            return None
        finally:
            conn.close()


def authenticate_user(login: str, password: str) -> Optional[Dict]:
    """Check username-or-email + password. Returns user dict or None."""
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? OR email = ?",
            (login.strip(), login.strip().lower()),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return None
    if not _verify_password(password, row["password_hash"]):
        return None
    return {"id": row["id"], "username": row["username"], "email": row["email"],
            "credits": row["credits"], "is_admin": bool(row["is_admin"])}


# ── CREDITS ──────────────────────────────────────────────────────────────────

def get_credits(user_id: int) -> int:
    conn = _connect()
    try:
        row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    return row["credits"] if row else 0


def set_credits(user_id: int, credits: int) -> bool:
    """Set exact credit amount. Returns True if user exists."""
    with _write_lock:
        conn = _connect()
        try:
            changed = conn.execute(
                "UPDATE users SET credits = ? WHERE id = ?", (max(0, credits), user_id)
            ).rowcount
            conn.commit()
        finally:
            conn.close()
    return changed > 0


def use_credit(user_id: int) -> bool:
    """Atomically deduct 1 credit. Returns True if successful, False if none left.

    Uses a conditional UPDATE — the deduction only happens when credits > 0,
    and `rowcount` tells us whether anything actually changed. Safe under
    concurrent calls thanks to _write_lock + the SQL guard.

    Admins bypass the deduction (effectively unlimited credits) and get True.
    """
    with _write_lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT credits, is_admin FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if not row:
                return False
            if row["is_admin"]:
                return True
            # Conditional decrement — only succeeds if credits > 0
            cur = conn.execute(
                "UPDATE users SET credits = credits - 1 WHERE id = ? AND credits > 0",
                (user_id,),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def refund_credit(user_id: int) -> bool:
    """Return 1 credit to a user. Used when a job fails AFTER its credit was
    reserved — we don't want to charge for analyses that never completed."""
    with _write_lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT is_admin FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if not row:
                return False
            if row["is_admin"]:
                return True   # admin has unlimited; no-op
            conn.execute(
                "UPDATE users SET credits = credits + 1 WHERE id = ?", (user_id,)
            )
            conn.commit()
            return True
        finally:
            conn.close()


def set_admin(user_id: int, is_admin: bool = True) -> bool:
    """Set or remove admin flag. Returns True if user exists."""
    with _write_lock:
        conn = _connect()
        try:
            changed = conn.execute(
                "UPDATE users SET is_admin = ? WHERE id = ?", (1 if is_admin else 0, user_id)
            ).rowcount
            conn.commit()
        finally:
            conn.close()
    return changed > 0


def list_users() -> List[Dict]:
    """List all users with credits info (for admin panel)."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, username, email, credits, is_admin, created_at FROM users ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [{"id": r["id"], "username": r["username"], "email": r["email"],
             "credits": r["credits"], "is_admin": bool(r["is_admin"]),
             "created_at": r["created_at"]} for r in rows]


def get_user_by_id(user_id: int) -> Optional[Dict]:
    conn = _connect()
    try:
        row = conn.execute("SELECT id, username, email, credits, is_admin, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    d = dict(row)
    d["is_admin"] = bool(d.get("is_admin", 0))
    return d


# ── SESSION TOKENS ───────────────────────────────────────────────────────────

def create_session(user_id: int) -> str:
    """Create a new auth token. Returns the token string."""
    token = secrets.token_urlsafe(48)
    now = _utcnow()
    expires = now + timedelta(days=SESSION_TTL_DAYS)

    with _write_lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (token, user_id, now.isoformat(), expires.isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
    return token


def validate_session(token: str) -> Optional[int]:
    """Check token validity. Returns user_id or None."""
    if not token:
        return None
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT user_id, expires_at FROM sessions WHERE token = ?", (token,)
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return None
    if datetime.fromisoformat(row["expires_at"]) < _utcnow():
        delete_session(token)
        return None
    return row["user_id"]


def delete_session(token: str) -> None:
    with _write_lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()
        finally:
            conn.close()


# ── DEBATE CRUD ──────────────────────────────────────────────────────────────

def save_debate(
    job_id: str,
    youtube_url: str,
    mode: str,
    language: str,
    analysis: Dict,
    fact_check: Dict,
    report_text: str,
    created_at: str,
    duration_sec: float = 0.0,
    ip_address: str = "",
    user_id: Optional[int] = None,
    speaker_names: str = "",
    title: str = "",
) -> None:
    """Save a completed analysis to the database."""
    meta = analysis.get("metadata", {})
    topic = meta.get("topic", "")
    speakers = ", ".join(meta.get("participants", {}).keys())

    summary = analysis.get("summary", "")

    with _write_lock:
        conn = _connect()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO debates
                   (id, user_id, youtube_url, mode, language, title, topic, speakers,
                    speaker_names, status,
                    analysis_json, fact_check_json, report_text,
                    summary,
                    created_at, duration_sec, ip_address, transcript_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?,
                           ?, 'completed',
                           ?, ?, ?,
                           ?,
                           ?, ?, ?, ?)""",
                (
                    job_id, user_id, youtube_url, mode, language, title, topic, speakers,
                    speaker_names,
                    json.dumps(analysis, ensure_ascii=False),
                    json.dumps(fact_check, ensure_ascii=False),
                    report_text,
                    summary,
                    created_at, duration_sec, ip_address, "",
                ),
            )
            conn.commit()
            logger.info("Debate saved to DB: %s (%s) user=%s", job_id, title or topic, user_id)
        except Exception as e:
            logger.error("Failed to save debate %s: %s", job_id, e)
        finally:
            conn.close()


def update_debate_fact_check(debate_id: str, fact_check_json: str,
                             report_text: Optional[str] = None) -> bool:
    """Replace only the fact-check of a saved debate.

    The arguments stay as they were, so re-checking the facts does not touch
    them. Used by the re-check endpoint, which re-runs the sources over an
    analysis that is already on disk instead of paying for the whole pipeline
    again.
    """
    with _write_lock:
        conn = _connect()
        try:
            sets = ["fact_check_json = ?"]
            params: List[Any] = [fact_check_json]
            if report_text is not None:
                sets.append("report_text = ?")
                params.append(report_text)
            params.append(debate_id)
            cur = conn.execute(
                f"UPDATE debates SET {', '.join(sets)} WHERE id = ?", params)
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


def get_debate(debate_id: str) -> Optional[Dict]:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM debates WHERE id = ?", (debate_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return _row_to_dict(row, include_full=True)


def update_debate_analysis(debate_id: str, analysis_json: str,
                           summary: Optional[str] = None,
                           speakers: Optional[str] = None,
                           title: Optional[str] = None) -> bool:
    """Replace the analysis_json (and optionally derived fields) for a debate.

    Used by the user-facing edit endpoints — when the user renames a speaker,
    deletes/edits an argument, edits the title, etc. Returns True on success.
    """
    with _write_lock:
        conn = _connect()
        try:
            sets = ["analysis_json = ?"]
            params = [analysis_json]
            if summary is not None:
                sets.append("summary = ?")
                params.append(summary)
            if speakers is not None:
                sets.append("speakers = ?")
                params.append(speakers)
            if title is not None:
                sets.append("title = ?")
                params.append(title)
            params.append(debate_id)
            cur = conn.execute(
                f"UPDATE debates SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            conn.commit()
            return cur.rowcount > 0
        except Exception as e:
            logger.error("Failed to update debate %s: %s", debate_id, e)
            return False
        finally:
            conn.close()


def _mode_clause(mode):
    """Helper: turn `mode` (str or list of str or None) into (sql_clause, params)."""
    if not mode:
        return None, []
    if isinstance(mode, str):
        return "mode = ?", [mode]
    # list / tuple of allowed mode values (e.g. ["debate", "debate_1v1"])
    placeholders = ",".join("?" for _ in mode)
    return f"mode IN ({placeholders})", list(mode)


def list_debates(limit: int = 50, offset: int = 0, user_id: Optional[int] = None, mode=None) -> List[Dict]:
    """List debates. `mode` accepts a str OR a list of strs (for legacy-aware filtering)."""
    conn = _connect()
    cols = """id, user_id, youtube_url, mode, language, title, topic, speakers,
              speaker_names, status, summary, created_at, duration_sec"""
    conditions = []
    params: List = []
    if user_id is not None:
        conditions.append("user_id = ?")
        params.append(user_id)
    mode_sql, mode_params = _mode_clause(mode)
    if mode_sql:
        conditions.append(mode_sql)
        params.extend(mode_params)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    try:
        rows = conn.execute(
            f"SELECT {cols} FROM debates {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r, include_full=False) for r in rows]


def count_debates(user_id: Optional[int] = None, mode=None) -> int:
    conn = _connect()
    conditions = []
    params: List = []
    if user_id is not None:
        conditions.append("user_id = ?")
        params.append(user_id)
    mode_sql, mode_params = _mode_clause(mode)
    if mode_sql:
        conditions.append(mode_sql)
        params.extend(mode_params)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    try:
        count = conn.execute(f"SELECT COUNT(*) FROM debates {where}", params).fetchone()[0]
    finally:
        conn.close()
    return count


def delete_debate(debate_id: str) -> bool:
    """Delete a debate by ID. Returns True if deleted."""
    with _write_lock:
        conn = _connect()
        try:
            cursor = conn.execute("DELETE FROM debates WHERE id = ?", (debate_id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()


def search_debates(query: str, limit: int = 20, user_id: Optional[int] = None, mode=None) -> List[Dict]:
    conn = _connect()
    like = f"%{query}%"
    cols = """id, user_id, youtube_url, mode, language, title, topic, speakers,
              speaker_names, status, summary, created_at, duration_sec"""
    conditions = ["(title LIKE ? OR topic LIKE ? OR summary LIKE ? OR speakers LIKE ?)"]
    params: List = [like, like, like, like]
    if user_id is not None:
        conditions.append("user_id = ?")
        params.append(user_id)
    mode_sql, mode_params = _mode_clause(mode)
    if mode_sql:
        conditions.append(mode_sql)
        params.extend(mode_params)
    where = f"WHERE {' AND '.join(conditions)}"
    try:
        rows = conn.execute(
            f"SELECT {cols} FROM debates {where} ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r, include_full=False) for r in rows]


def _row_to_dict(row: sqlite3.Row, include_full: bool = False) -> Dict:
    d: Dict[str, Any] = dict(row)

    if include_full:
        for key in ("analysis_json", "fact_check_json"):
            raw = d.get(key)
            if raw:
                try:
                    d[key] = json.loads(raw)
                except json.JSONDecodeError:
                    pass
    else:
        d.pop("analysis_json", None)
        d.pop("fact_check_json", None)
        d.pop("report_text", None)

    # Transcript is kept private even in full detail responses.
    d.pop("transcript_text", None)

    return d
