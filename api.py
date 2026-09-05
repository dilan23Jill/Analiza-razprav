"""
Debate Analyzer API — FastAPI wrapper around the analysis pipeline.

Features:
  * Async job system (submit -> poll -> results)
  * User authentication (register / login / session tokens)
  * Per-user rate limiting (configurable, default 3 calls/user)
  * SQLite database for users + completed analyses
  * Optional speaker names for better transcript labeling
  * CORS whitelist
  * YouTube URL validation
  * Job cleanup for old in-memory results

Usage:
  pip install fastapi uvicorn python-dotenv
  uvicorn api:app --host 0.0.0.0 --port 8000

Env vars:
  CORS_ORIGINS     comma-separated frontend URLs  (default: localhost:3000,5173)
  RATE_LIMIT_MAX   max analyses per user per 24h   (default: 3, 0=unlimited)
  JOB_TTL          seconds to keep finished jobs   (default: 3600)
"""

import json
import logging
import os
import re
import threading
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
import shutil

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, Response
from pydantic import BaseModel, field_validator

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Naive UTC timestamp. Keeps the exact isoformat shape (no tz offset) of
    the old datetime.utcnow(), so all existing fromisoformat comparisons stay
    valid — this just drops the deprecated utcnow() call."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

# ── CONFIG ────────────────────────────────────────────────────────────────────

_cors_env = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173")
ALLOWED_ORIGINS = [o.strip() for o in _cors_env.split(",") if o.strip()]

RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX", "3"))
JOB_TTL_SECONDS = int(os.getenv("JOB_TTL", "3600"))

# ── DATABASE INIT ─────────────────────────────────────────────────────────────

from database import (
    ensure_admin_user, init_db, save_debate, get_debate, list_debates, count_debates, search_debates,
    create_user, authenticate_user, get_user_by_id,
    create_session, validate_session, delete_session,
    get_credits, set_credits, use_credit, set_admin, list_users,
)

ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")

# ── APP ───────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown hook (replaces the deprecated @app.on_event)."""
    logger.info("Debate Analyzer API starting on port %s", os.getenv("PORT", "8000"))
    logger.info("CORS origins: %s", ALLOWED_ORIGINS)
    logger.info("Static dir exists: %s", (Path(__file__).parent / "static").is_dir())
    init_db()
    # Bootstrap: ensure user_id=1 is an admin with credits. Idempotent — no-op
    # until the first user registers, then promotes them on the next startup.
    ensure_admin_user()
    # Disk hygiene: slim leftover job dirs (audio/scratch out, transcripts kept
    # for reruns) and start the periodic in-memory + on-disk job cleanup.
    # These two were previously defined but never wired — job dirs grew forever.
    _purge_orphan_job_dirs()
    threading.Thread(target=_cleanup_old_jobs, daemon=True).start()
    yield


app = FastAPI(
    title="Debate Analyzer API",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)


# ── API PREFIX MIDDLEWARE (production: frontend sends /api/*, backend expects /*) ──

@app.middleware("http")
async def strip_api_prefix(request: Request, call_next):
    """Strip /api prefix so frontend can call /api/health and backend serves /health."""
    path = request.scope.get("path", "")
    if path.startswith("/api/"):
        request.scope["path"] = path[4:]  # "/api/health" → "/health"
    elif path == "/api":
        request.scope["path"] = "/"
    response = await call_next(request)
    return response


# ── IN-MEMORY STORES ─────────────────────────────────────────────────────────

jobs: Dict[str, Dict[str, Any]] = {}
jobs_lock = threading.Lock()

rate_store: Dict[str, list] = {}
rate_lock = threading.Lock()

login_attempts: Dict[str, list] = {}   # IP -> [timestamp, ...]
login_lock = threading.Lock()
LOGIN_MAX_ATTEMPTS = 10  # per IP per 15 minutes
LOGIN_WINDOW = 900       # 15 minutes


# ── AUTH HELPERS ──────────────────────────────────────────────────────────────

def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _get_token(request: Request) -> Optional[str]:
    """Extract Bearer token from Authorization header."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def _get_current_user(request: Request) -> Optional[Dict]:
    """Get user from token, or None if not logged in."""
    token = _get_token(request)
    if not token:
        return None
    user_id = validate_session(token)
    if not user_id:
        return None
    return get_user_by_id(user_id)


def _require_user(request: Request) -> Dict:
    """Require auth — raises 401 if not logged in."""
    user = _get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Prijava je potrebna")
    return user


# ── RATE LIMITER (now per user_id, fallback to IP) ───────────────────────────

def _rate_key(request: Request, user: Optional[Dict] = None) -> str:
    """Rate limit key: user_id if logged in, else IP."""
    if user:
        return f"user:{user['id']}"
    return f"ip:{_get_client_ip(request)}"


def _check_rate_limit(key: str) -> tuple[bool, int]:
    if RATE_LIMIT_MAX <= 0:
        return True, 999

    with rate_lock:
        cutoff = time.time() - 86400
        timestamps = [ts for ts in rate_store.get(key, []) if ts > cutoff]
        rate_store[key] = timestamps
        remaining = RATE_LIMIT_MAX - len(timestamps)
        return remaining > 0, max(remaining, 0)


def _record_request(key: str) -> None:
    with rate_lock:
        rate_store.setdefault(key, []).append(time.time())


# ── REQUEST / RESPONSE MODELS ────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3 or len(v) > 30:
            raise ValueError("Uporabnisko ime: 3-30 znakov")
        if not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError("Samo crke, stevilke, _ in -")
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("Neveljaven email")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("Geslo: vsaj 6 znakov")
        return v


class LoginRequest(BaseModel):
    login: str      # username or email
    password: str


class AnalyzeRequest(BaseModel):
    youtube_url: str
    mode: str = "solo"
    language: str = "sl"
    speaker_names: Optional[str] = None   # optional: "Speaker1, Speaker2"
    title: Optional[str] = None           # optional debate title
    start_time: Optional[str] = None      # optional: "5:30" or "0:05:30"
    end_time: Optional[str] = None        # optional: "45:00" or "0:45:00"

    @field_validator("youtube_url")
    @classmethod
    def validate_youtube_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            return v   # empty is allowed (file upload uses separate endpoint)
        patterns = [
            r"^https?://(www\.)?youtube\.com/watch\?v=[\w-]{11}",
            r"^https?://youtu\.be/[\w-]{11}",
            r"^https?://(www\.)?youtube\.com/shorts/[\w-]{11}",
        ]
        if not any(re.match(p, v) for p in patterns):
            raise ValueError("Invalid YouTube URL")
        return v

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        # Načina sta solo in debate. Stari vrednosti reaction in debate_1v1
        # se preslikata vanju.
        v = (v or "").strip().lower()
        if v == "reaction":
            return "solo"
        if v == "debate_1v1":
            return "debate"
        if v in ("solo", "debate"):
            return v
        raise ValueError("mode must be 'solo' or 'debate'")

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        if v not in ("sl", "en"):
            raise ValueError("language must be 'sl' or 'en'")
        return v


# Allowed audio/video extensions for upload
ALLOWED_UPLOAD_EXTS = {".mp3", ".mp4", ".m4a", ".wav", ".webm", ".ogg", ".flac", ".aac", ".mkv", ".avi"}


class JobStatus(BaseModel):
    job_id: str
    status: str
    progress: Optional[str] = None
    created_at: str
    result: Optional[Dict] = None
    error: Optional[str] = None


# ── ENDPOINTS: AUTH ──────────────────────────────────────────────────────────

@app.post("/auth/register")
async def register(body: RegisterRequest):
    """Register a new user account."""
    user = create_user(body.username, body.email, body.password)
    if not user:
        raise HTTPException(status_code=409, detail="Uporabnisko ime ali email ze obstaja")

    token = create_session(user["id"])
    return {
        "user": {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "credits": user["credits"],
            "is_admin": user["is_admin"],
        },
        "token": token,
    }


@app.post("/auth/login")
async def login(body: LoginRequest, request: Request):
    """Login with username/email + password."""
    # Rate limit login attempts per IP
    ip = _get_client_ip(request)
    with login_lock:
        cutoff = time.time() - LOGIN_WINDOW
        attempts = [ts for ts in login_attempts.get(ip, []) if ts > cutoff]
        login_attempts[ip] = attempts
        if len(attempts) >= LOGIN_MAX_ATTEMPTS:
            raise HTTPException(status_code=429, detail="Prevec poskusov prijave. Pocakaj 15 minut.")
        login_attempts[ip].append(time.time())

    user = authenticate_user(body.login, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Napacno uporabnisko ime ali geslo")

    token = create_session(user["id"])
    return {
        "user": {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"],
            "credits": user["credits"],
            "is_admin": user["is_admin"],
        },
        "token": token,
    }


@app.post("/auth/logout")
async def logout(request: Request):
    """Logout — invalidate session token."""
    token = _get_token(request)
    if token:
        delete_session(token)
    return {"ok": True}


@app.get("/auth/me")
async def get_me(request: Request):
    """Get current user info including credits (or 401)."""
    user = _require_user(request)
    # Refresh credits from DB
    credits = get_credits(user["id"])
    user["credits"] = credits
    return {"user": user}


# ── ENDPOINTS: ADMIN ─────────────────────────────────────────────────────────

def _require_admin(request: Request) -> None:
    """Verify admin access via ADMIN_SECRET header or admin user flag."""
    # Option 1: ADMIN_SECRET header (for CLI/Postman use)
    secret = request.headers.get("x-admin-secret", "")
    if ADMIN_SECRET and secret == ADMIN_SECRET:
        return
    # Option 2: logged-in admin user
    user = _get_current_user(request)
    if user and user.get("is_admin"):
        return
    raise HTTPException(status_code=403, detail="Admin access required")


@app.get("/admin/users")
async def admin_list_users(request: Request):
    """List all users with credits. Requires admin."""
    _require_admin(request)
    return {"users": list_users()}


@app.post("/admin/credits")
async def admin_set_credits(request: Request):
    """Set credits for a user. Body: {"user_id": int, "credits": int}"""
    _require_admin(request)
    body = await request.json()
    user_id = body.get("user_id")
    credits_val = body.get("credits")
    if user_id is None or credits_val is None:
        raise HTTPException(status_code=422, detail="user_id and credits required")
    try:
        user_id = int(user_id)
        credits_val = int(credits_val)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="user_id and credits must be integers")
    if credits_val < 0:
        raise HTTPException(status_code=422, detail="credits must be non-negative")
    if not set_credits(user_id, credits_val):
        raise HTTPException(status_code=404, detail="User not found")
    logger.info("Admin: set %d credits for user %d", credits_val, user_id)
    return {"ok": True, "user_id": user_id, "credits": credits_val}


@app.post("/admin/set-admin")
async def admin_set_admin(request: Request):
    """Grant/revoke admin. Body: {"user_id": int, "is_admin": bool}"""
    _require_admin(request)
    body = await request.json()
    user_id = body.get("user_id")
    is_admin_flag = body.get("is_admin", True)
    if user_id is None:
        raise HTTPException(status_code=422, detail="user_id required")
    if not set_admin(int(user_id), bool(is_admin_flag)):
        raise HTTPException(status_code=404, detail="User not found")
    logger.info("Admin: set is_admin=%s for user %d", is_admin_flag, user_id)
    return {"ok": True, "user_id": user_id, "is_admin": is_admin_flag}



# ── ENDPOINTS: ANALYSIS ──────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": _utcnow().isoformat()}


@app.get("/rate-limit")
async def check_rate(request: Request):
    user = _get_current_user(request)
    key = _rate_key(request, user)
    _, remaining = _check_rate_limit(key)
    return {"limit": RATE_LIMIT_MAX, "remaining": remaining, "window": "24h"}


def _apply_speaker_names(analysis: Dict, fact_check: Dict, speaker_names: str) -> None:
    """Put the names the user typed onto the speakers, in order, after analysis.

    The pipeline runs entirely on the neutral labels the transcription returns
    (`Speaker 1`, `Speaker 2`), so nothing upstream depends on what the people
    are called. The first name the user typed goes to the first speaker, the
    second to the second, and so on. Renaming reuses the same operation the
    interface offers, so every place that carries a speaker name is updated in
    one way only.

    Two phases with placeholder names, so that swapping two names cannot
    collide with a key that still exists.
    """
    names = [n.strip() for n in (speaker_names or "").split(",") if n.strip()]
    if not names:
        return
    speakers = analysis.get("speakers") or {}
    if not speakers:
        return

    def _order(label: str):
        m = re.match(r"^\s*Speaker\s+(\d+)\s*$", label)
        return (0, int(m.group(1))) if m else (1, label)

    current = sorted(speakers.keys(), key=_order)
    pairs = [(old, new) for old, new in zip(current, names) if old != new]
    if not pairs:
        return

    ops = [EditOp(op="rename_speaker",
                  payload={"from_name": old, "to_name": f"__tmp{i}__"})
           for i, (old, _) in enumerate(pairs)]
    ops += [EditOp(op="rename_speaker",
                   payload={"from_name": f"__tmp{i}__", "to_name": new})
            for i, (_, new) in enumerate(pairs)]
    _apply_edits(analysis, ops)

    # The fact-check result is stored in its own column, so it is renamed here.
    mapping = dict(pairs)
    for f in (fact_check.get("fact_checks") or []) if isinstance(fact_check, dict) else []:
        if f.get("speaker") in mapping:
            f["speaker"] = mapping[f["speaker"]]
    logger.info("Applied speaker names: %s",
                ", ".join(f"{o} -> {n}" for o, n in pairs))


def _distinct_speakers(transcript_path) -> set:
    """Speaker labels the transcript actually distinguishes.

    Read from the transcript rather than from the diarization response: the
    transcript is what every later step sees, so it is the honest place to ask
    how many voices the pipeline ended up with.
    """
    import re as _re
    try:
        text = Path(transcript_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return {m.strip() for m in _re.findall(r"^([^:\n]{1,40}): ", text, _re.M)
            if m.strip()}


def _max_analysable_seconds() -> int:
    """Longest recording the system accepts, in seconds.

    The binding limit is the transcription model, which takes at most 1400
    seconds of audio per call and refuses anything longer outright. The
    transcript budget and the upload size limit both sit far above that at this
    length, so duration is the only constraint that actually binds. We refuse
    rather than truncate, and point the user at the trim slider.
    """
    from config_loader import get as cfg
    return int(float(cfg("pipeline.max_recording_minutes", 45)) * 60)


def _hhmmss_to_seconds(value: str) -> Optional[float]:
    """Parse "5:30" or "1:05:30" into seconds. Returns None if unparsable."""
    raw = (value or "").strip()
    if not raw:
        return None
    parts = raw.split(":")
    # Every part must be a non-empty run of digits: "" and "1::2" are not times.
    if not all(p.strip().isdigit() for p in parts):
        return None
    nums = [int(p) for p in parts]
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 1:
        return float(nums[0])
    return None


def _effective_duration_seconds(url: str, start_time: str, end_time: str) -> float:
    """Length of the material that will actually be analysed.

    When the user picked a range, only that range is downloaded and analysed,
    so a two-hour video trimmed to twenty minutes is perfectly fine. Falls back
    to the video's own length, and to 0 (no objection) when neither is known —
    the post-transcription check still catches those.
    """
    start = _hhmmss_to_seconds(start_time)
    end = _hhmmss_to_seconds(end_time)
    if start is not None and end is not None and end > start:
        return end - start

    try:
        from youtube_downloader import get_youtube_metadata
        full = float(get_youtube_metadata(url, timeout=25).get("duration") or 0)
    except Exception as e:
        logger.info("Duration pre-check skipped (%s) — post-transcription check still applies", e)
        return 0.0

    if start is not None and full > start:
        return full - start
    return full


def _assert_duration_analysable(seconds: float) -> None:
    """Reject an over-long recording BEFORE any credit or API call is spent."""
    limit = _max_analysable_seconds()
    if seconds and seconds > limit:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "Predolg posnetek",
                "duration_sec": int(seconds),
                "max_sec": limit,
                "message": (
                    f"Predolg posnetek: traja {int(seconds // 60)} min, "
                    f"sprejmemo pa največ {limit // 60} min. Z drsnikom izberi "
                    "krajši odsek."
                ),
            },
        )


def _reserve_quota(request: Request, user: Dict) -> Tuple[str, str, int]:
    """Pre-flight: enforce rate limit + atomically reserve 1 credit.

    Call this BEFORE any expensive work (especially file uploads) so we don't
    waste disk / bandwidth on requests that would be rejected anyway. Returns
    (ip, rate_key, remaining). Raises HTTPException 429 / 403 on failure.

    The reserved credit is automatically refunded if the eventual job fails
    (see _run_pipeline error path) or if the caller manually calls
    refund_credit() before _start_job (e.g. when streaming upload aborts).
    """
    ip = _get_client_ip(request)
    key = _rate_key(request, user)

    allowed, remaining = _check_rate_limit(key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "limit": RATE_LIMIT_MAX,
                "remaining": 0,
                "window": "24h",
                "message": f"Najvec {RATE_LIMIT_MAX} analiz na 24 ur.",
            },
        )

    # Atomic credit reservation — concurrent jobs from same user with 1 credit
    # can no longer all pass; only the first use_credit() succeeds.
    if not use_credit(user["id"]):
        raise HTTPException(
            status_code=403,
            detail={"error": "No credits", "credits": 0,
                    "message": "Nimaš kreditov za analizo. Kontaktiraj administratorja."},
        )

    return ip, key, remaining


def _start_job(request: Request, user: Dict, youtube_url: str, mode: str,
               language: str, speaker_names: str, title: str,
               uploaded_file_path: str = "",
               start_time: str = "", end_time: str = "",
               quota: Optional[Tuple[str, str, int]] = None,
               job_id: Optional[str] = None,
               transcript_override: str = "") -> JobStatus:
    """Shared job creation logic for both YouTube URL and file upload.

    `quota`: optional pre-reserved (ip, rate_key, remaining) from _reserve_quota.
    If not provided, this function reserves it itself (URL-only path).
    `job_id`: optional pre-generated id. The upload path streams the file into
    jobs/{job_id}/data BEFORE the job exists, so it passes the same id here to
    avoid a second copy into a freshly-generated dir.
    """
    if quota is None:
        ip, key, remaining = _reserve_quota(request, user)
    else:
        ip, key, remaining = quota

    _record_request(key)

    job_id = job_id or uuid.uuid4().hex[:12]
    now = _utcnow().isoformat()

    with jobs_lock:
        jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "progress": "Waiting to start...",
            "created_at": now,
            "result": None,
            "error": None,
            "config": {
                "youtube_url": youtube_url,
                "mode": mode,
                "language": language,
                "speaker_names": speaker_names,
                "title": title,
                "start_time": start_time,
                "end_time": end_time,
            },
            "user_id": user["id"],
            "ip": ip,
        }

    is_admin = bool(user.get("is_admin"))

    thread = threading.Thread(
        target=_run_pipeline,
        args=(job_id, youtube_url, mode, language,
              ip, user["id"], speaker_names, title, uploaded_file_path,
              start_time, end_time, is_admin, transcript_override),
        daemon=True,
    )
    thread.start()
    logger.info("Job %s submitted by user %s (%d remaining)", job_id, user["username"], remaining - 1)

    return JobStatus(job_id=job_id, status="queued", progress="Waiting to start...", created_at=now)


@app.post("/analyze", response_model=JobStatus)
async def submit_analysis(body: AnalyzeRequest, request: Request):
    """Submit a new analysis job from YouTube URL. Requires login."""
    user = _require_user(request)

    # Check length BEFORE reserving a credit or downloading anything. The user
    # may have picked a time range, in which case only that range is analysed
    # and only its length matters; otherwise the whole video does.
    _assert_duration_analysable(
        _effective_duration_seconds(body.youtube_url,
                                    body.start_time or "", body.end_time or "")
    )

    return _start_job(
        request, user,
        youtube_url=body.youtube_url,
        mode=body.mode,
        language=body.language,
        speaker_names=body.speaker_names or "",
        title=body.title or "",
        start_time=body.start_time or "",
        end_time=body.end_time or "",
    )


# ── METADATA PROBE: lightweight, used by frontend to size the trim slider ──

class ProbeRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("URL is required")
        # Same set of accepted YouTube URL shapes as AnalyzeRequest
        patterns = [
            r"^https?://(www\.)?youtube\.com/watch\?v=[\w-]{11}",
            r"^https?://youtu\.be/[\w-]{11}",
            r"^https?://(www\.)?youtube\.com/shorts/[\w-]{11}",
        ]
        if not any(re.match(p, v) for p in patterns):
            raise ValueError("Invalid YouTube URL")
        return v


@app.post("/probe-youtube")
async def probe_youtube(body: ProbeRequest, request: Request):
    """Return YouTube video metadata (duration, title, etc.) without downloading.

    Used by the frontend to auto-size the trim slider to the video's real
    length so the user doesn't have to guess "10m / 25m / 1h / 2h".
    Requires login (avoids unauthenticated yt-dlp abuse)."""
    _require_user(request)
    from youtube_downloader import get_youtube_metadata
    try:
        meta = get_youtube_metadata(body.url, timeout=25)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if meta.get("is_live"):
        raise HTTPException(status_code=422, detail="Live streams cannot be analyzed")
    _assert_duration_analysable(float(meta.get("duration") or 0))
    return meta


@app.post("/analyze/upload", response_model=JobStatus)
async def submit_upload_analysis(
    request: Request,
    file: UploadFile = File(...),
    mode: str = Form("solo"),
    language: str = Form("sl"),
    speaker_names: str = Form(""),
    title: str = Form(""),
    start_time: str = Form(""),
    end_time: str = Form(""),
):
    """Submit a new analysis job from uploaded audio/video file. Requires login."""
    user = _require_user(request)

    # Validate + normalize mode (legacy values are accepted and remapped):
    #   "reaction"   → "solo"
    #   "debate_1v1" → "debate"
    mode = (mode or "").strip().lower()
    if mode == "reaction":
        mode = "solo"
    elif mode == "debate_1v1":
        mode = "debate"
    if mode not in ("solo", "debate"):
        raise HTTPException(status_code=422, detail="mode must be 'solo' or 'debate'")
    if language not in ("sl", "en"):
        raise HTTPException(status_code=422, detail="language must be 'sl' or 'en'")

    # Validate file extension FIRST (cheapest gate)
    ext = Path(file.filename or "file").suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTS:
        raise HTTPException(
            status_code=422,
            detail=f"Nepodprt format: {ext}. Dovoljeni: {', '.join(sorted(ALLOWED_UPLOAD_EXTS))}",
        )

    # Reserve quota (rate-limit + credit) BEFORE writing any bytes to disk.
    # If we wrote first, a 403/429 here would leave a 500MB file on disk.
    quota = _reserve_quota(request, user)

    # Nalaganje po delih, da velika datoteka ne gre v pomnilnik. Oznaka naloge
    # nastane vnaprej, da datoteka pristane naravnost v njeni mapi.
    max_bytes = 500 * 1024 * 1024
    job_id = uuid.uuid4().hex[:12]
    upload_dir = Path(f"jobs/{job_id}/data")
    upload_dir.mkdir(parents=True, exist_ok=True)
    upload_path = upload_dir / f"uploaded{ext}"

    def _refund_and_cleanup():
        """Roll back the quota reservation + remove the partial job dir."""
        try:
            shutil.rmtree(Path(f"jobs/{job_id}"), ignore_errors=True)
        except OSError:
            pass
        if not user.get("is_admin"):
            try:
                from database import refund_credit
                refund_credit(user["id"])
            except Exception:
                pass

    total = 0
    chunk_size = 1024 * 1024   # 1 MiB
    try:
        with open(upload_path, "wb") as out:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    out.close()
                    _refund_and_cleanup()
                    raise HTTPException(status_code=413, detail="Datoteka je prevelika (max 500MB)")
                out.write(chunk)
    except HTTPException:
        raise
    except Exception:
        _refund_and_cleanup()
        raise
    finally:
        await file.close()

    if total <= 1024:
        _refund_and_cleanup()
        raise HTTPException(status_code=422, detail="Datoteka je prazna ali poškodovana")
    logger.info("Uploaded file saved: %s (%.1f MB)", upload_path, total / 1024 / 1024)

    # Use filename as title if no title given
    if not title:
        title = Path(file.filename or "").stem or ""

    return _start_job(
        request, user,
        youtube_url="",
        mode=mode,
        language=language,
        speaker_names=speaker_names,
        title=title,
        uploaded_file_path=str(upload_path),
        quota=quota,   # already reserved — don't double-charge
        job_id=job_id,  # reuse the dir we just streamed into — no second copy
        start_time=start_time,
        end_time=end_time,
    )


# ── PIPELINE RUNNER ───────────────────────────────────────────────────────────

def _run_pipeline(job_id: str, youtube_url: str, mode: str, language: str,
                  ip: str, user_id: int, speaker_names: str,
                  title: str = "", uploaded_file_path: str = "",
                  start_time: str = "", end_time: str = "",
                  is_admin: bool = False,
                  transcript_override: str = "") -> None:
    """Run the full pipeline in a background thread, then save to DB.

    Concurrency: each job runs inside its own thread-local config override
    block (config_loader.job_overrides). Two parallel jobs see DIFFERENT
    pipeline.mode / data_dir / output_dir — no global mutation, no race.
    """

    pipeline_started_at = time.time()
    trim_start = (start_time or "").strip()
    trim_end = (end_time or "").strip()

    # Job-specific directories (computed before override so they go in the override)
    job_dir = Path(f"jobs/{job_id}")
    data_dir = job_dir / "data"
    output_dir = job_dir / "output"
    data_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Only mode and language are read back through cfg(); every path is passed
    # explicitly to the functions that need it.
    overrides = {
        "pipeline.mode": mode,
        "pipeline.language": language,
    }

    from config_loader import load_config, job_overrides
    # Uvoz mora biti PRED try, sicer je ime v except neznano in napaka zgodaj v
    # cevovodu ostane brez sporočila, naloga pa neoznačena.
    from debate_analyzer import UnsupportedDebateFormatError, RecordingTooLongError

    with job_overrides(**overrides):
      try:
        _update_job(job_id, status="processing", progress="Loading config...")
        load_config()

        # ── Step 0: RERUN path — reuse an existing transcript ─────
        # A rerun re-analyzes with the CURRENT prompts/rules without paying
        # for download + transcription again. Skips straight to step 2b.
        if (transcript_override or "").strip():
            _update_job(job_id, progress="Reusing existing transcript...")
            transcript_path = data_dir / "transcript.txt"
            transcript_path.write_text(transcript_override.strip() + "\n",
                                       encoding="utf-8")
            logger.info("Rerun: reusing transcript (%d chars) — download and "
                        "transcription skipped", len(transcript_override))
        # ── Step 1: Get audio (download or use uploaded file) ─────
        elif uploaded_file_path and Path(uploaded_file_path).exists():
            _update_job(job_id, progress="Using uploaded file...")
            audio_path = Path(uploaded_file_path)
            # Move to job data dir if not already there
            dest = data_dir / audio_path.name
            if audio_path != dest:
                shutil.copy2(str(audio_path), str(dest))
                audio_path = dest
            logger.info("Using uploaded file: %s", audio_path)
        elif youtube_url:
            _update_job(job_id, progress="Downloading audio from YouTube...")
            from youtube_downloader import download_youtube_audio, get_youtube_metadata
            # Auto-fill a missing title from YouTube metadata. The title often
            # encodes the argument structure ("9 razlogov za ...") which the
            # analyzer uses to mirror the announced number of arguments.
            if not (title or "").strip():
                try:
                    title = get_youtube_metadata(youtube_url).get("title", "") or ""
                    if title:
                        logger.info("Title auto-filled from YouTube metadata: %s", title)
                except Exception as e:
                    logger.warning("Could not fetch YouTube title (non-fatal): %s", e)
            audio_path = download_youtube_audio(
                youtube_url, output_path=str(data_dir / "audio.m4a")
            )
        else:
            raise ValueError("Either youtube_url or uploaded file is required")

        # ── Step 1b: Trim audio if start/end time given ──────────
        if (trim_start or trim_end) and not (transcript_override or "").strip():
            _update_job(job_id, progress="Trimming audio to selected range...")
            trimmed_path = data_dir / f"trimmed{audio_path.suffix}"
            ffmpeg_cmd = ["ffmpeg", "-y", "-i", str(audio_path)]
            if trim_start:
                ffmpeg_cmd += ["-ss", trim_start]
            if trim_end:
                ffmpeg_cmd += ["-to", trim_end]
            ffmpeg_cmd += ["-c", "copy", str(trimmed_path)]
            import subprocess
            try:
                result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=120)
            except FileNotFoundError:
                raise ValueError(
                    "Za izrez odseka je potreben ffmpeg, ki ga ni na sistemu. "
                    "Namesti ga ali analiziraj celoten posnetek."
                )
            if result.returncode == 0 and trimmed_path.exists():
                logger.info("Audio trimmed to %s-%s", trim_start or "0", trim_end or "konec")
                audio_path = trimmed_path
            else:
                logger.warning("Trimming failed (rc=%d), using full audio: %s",
                               result.returncode, result.stderr[:300])

        # ── Step 2: Transcription (skipped on rerun) ─────────────
        if not (transcript_override or "").strip():
            _update_job(job_id, progress="Transcribing audio...")
            from transcribe import transcribe_audio
            transcript_path = transcribe_audio(
                str(audio_path), str(data_dir / "transcript.txt"))

        # ── Step 2d: Persist the transcript for future RERUNS ─────
        # jobs/<id> is scratch (audio and outputs get purged); transcripts/
        # is the tiny persistent home that keeps "Ponovna analiza" working
        # across server restarts and disk cleanups.
        try:
            persist_dir = Path("transcripts")
            persist_dir.mkdir(exist_ok=True)
            shutil.copy2(transcript_path, persist_dir / f"{job_id}.txt")
        except OSError as e:
            logger.warning("Could not persist transcript copy (non-fatal): %s", e)

        # ── Step 2e: Did diarization actually separate the speakers? ──
        # Preverba pred izluščanjem, ki je najdražji klic: če je ločevanje
        # govorcev oba glasova zlilo v enega, razprave ni.
        voices = _distinct_speakers(transcript_path)
        logger.info("Transcript: %d distinct speaker label(s)", len(voices))
        if mode == "debate" and len(voices) < 2:
            raise UnsupportedDebateFormatError(
                "diarization_found_one_speaker: the transcript separates only "
                "{} voice ({}). Speaker separation failed on this recording, so "
                "a debate cannot be analysed from it."
                .format(len(voices), ", ".join(sorted(voices)) or "none"),
                sorted(voices),
            )

        # ── Step 3: Analysis, with fact-checking folded into it ───
        # Fact-checking used to run here, before the analysis, on the raw
        # transcript. That spent money on claims the argument extraction then
        # discarded, and left the verdicts sitting beside the arguments rather
        # than attached to them. It now runs INSIDE the analysis, right after
        # the arguments have been extracted and consolidated, so it works on the
        # premises that will actually appear in the report and every verdict
        # carries the arg_id it belongs to.
        #
        # NOTE: an earlier version looked each speaker up on the web (bio, known
        # positions, political leaning) and fed that into the fact-check prompts.
        # It was removed: telling a fact-checker who the speaker is — and how they
        # lean politically — before it judges a claim conflicts with the neutrality
        # requirement, adds an uncontrolled input that varies between runs, and was
        # never visible to the user. Source balancing now keys on the claim's own
        # subject matter instead, which is where it belonged all along.
        _update_job(job_id, progress="Analyzing arguments...")
        from debate_analyzer import DebateAnalyzer, render_text_report
        from fact_checker import FactChecker

        analyzer = DebateAnalyzer()
        transcript_text = transcript_path.read_text(encoding="utf-8").strip()
        fact_checker = FactChecker()
        fact_check_results: Dict = {}

        def _check_argument_premises(speakers: Dict) -> Dict:
            nonlocal fact_check_results
            _update_job(job_id, progress="Fact-checking claims...")
            fact_check_results = fact_checker.fact_check_arguments(
                speakers, transcript=transcript_text)
            return fact_check_results

        analysis = analyzer.analyze_multi_pass(transcript_text, None,
                                               video_title=title or "",
                                               fact_check_fn=_check_argument_premises)

        # ── Speaker names: applied AFTER the analysis, by position ─
        # The whole pipeline runs on the neutral labels the transcription
        # returns, so the argument ids and every cross-reference are built from
        # a label the system controls. Only at the end are the names the user
        # typed put in their place, first name onto the first speaker.
        _apply_speaker_names(analysis, fact_check_results, speaker_names)

        (output_dir / "fact_check.json").write_text(
            json.dumps(fact_check_results, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        report = render_text_report(analysis, fact_check_results)
        (output_dir / "debate_analysis.json").write_text(
            json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (output_dir / "debate_analysis.txt").write_text(report, encoding="utf-8")

        # ── Save to database ──────────────────────────────────────
        duration = time.time() - pipeline_started_at
        created_at = ""
        with jobs_lock:
            created_at = jobs.get(job_id, {}).get("created_at", _utcnow().isoformat())

        save_debate(
            job_id=job_id,
            youtube_url=youtube_url or "",
            mode=mode,
            language=language,
            analysis=analysis,
            fact_check=fact_check_results,
            report_text=report,
            created_at=created_at,
            duration_sec=duration,
            ip_address=ip,
            user_id=user_id,
            speaker_names=speaker_names,
            title=title,
        )

        # ── Update in-memory job ──────────────────────────────────
        # Credit was already reserved atomically in _start_job. Nothing to
        # deduct here. (On failure we refund — see except branch below.)

        _update_job(
            job_id,
            status="completed",
            progress="Done",
            result={
                "analysis": analysis,
                "fact_check_summary": fact_check_results.get("summary", {}),
                "report_text": report,
            },
        )
        logger.info("Job %s completed in %.0fs", job_id, duration)

      except RecordingTooLongError as e:
        # The recording is longer than the analysis can read in one pass. We
        # refuse rather than analyse a silently shortened transcript, and point
        # the user at the trim slider, which is the working way out.
        logger.warning("Job %s stopped — recording too long: %s", job_id, e)
        _update_job(
            job_id, status="failed",
            error="Predolg posnetek. Z drsnikom izberi krajši odsek in poskusi znova.",
        )
        if not is_admin:
            try:
                from database import refund_credit
                refund_credit(user_id)
            except Exception as refund_err:
                logger.error("Refund after too-long recording failed: %s", refund_err)
        return

      except UnsupportedDebateFormatError as e:
        # Not a crash: the recording simply does not fit the supported 1v1
        # format. Give the user an actionable message instead of a stack trace,
        # and refund the credit through the same path as any other failure.
        detected = getattr(e, "detected", []) or []
        if str(e).startswith("diarization_found_one_speaker"):
            msg = ("Ločevanje govorcev na tem posnetku ni uspelo — prepis loči le "
                   "en glas, zato razprave iz njega ni mogoče analizirati. To se "
                   "zgodi pri prekrivajočem govoru ali zelo podobnih glasovih. "
                   "Poskusi z drugim posnetkom, vnesi imena govorcev ročno ali "
                   "zaženi analizo v načinu \u201esolo\u201c.")
        elif str(e).startswith("too_few_debaters"):
            msg = ("V posnetku je zaznan samo en govorec, zato ga ni mogoče analizirati "
                   "kot debato. Zaženi analizo znova v načinu \u201esolo\u201c.")
        else:
            names = ", ".join(detected)
            msg = ("Aplikacija analizira samo debate ena na ena (dva debaterja). "
                   f"V tem posnetku je zaznanih {len(detected)} debaterjev"
                   + (f": {names}. " if names else ". ")
                   + "Analiza je ustavljena, da ne bi primerjala napačnega para. "
                     "Za posnetek z enim govorcem uporabi način \u201esolo\u201c.")
        logger.info("Job %s stopped — unsupported format: %s", job_id, e)
        _update_job(job_id, status="failed", error=msg)
        if not is_admin:
            try:
                from database import refund_credit
                refund_credit(user_id)
            except Exception as refund_err:
                logger.error("Refund after unsupported format failed: %s", refund_err)
        return

      except Exception as e:
        logger.error("Job %s failed: %s\n%s", job_id, e, traceback.format_exc())
        _update_job(job_id, status="failed", error=str(e))

        # Refund the credit reserved in _start_job — user shouldn't be charged
        # for analyses that never completed. Admins are no-op (unlimited).
        if not is_admin:
            try:
                from database import refund_credit
                refund_credit(user_id)
                logger.info("Refunded 1 credit to user %d (job %s failed)", user_id, job_id)
            except Exception as ref_e:
                logger.warning("Credit refund failed for user %d: %s", user_id, ref_e)


def _update_job(job_id: str, **kwargs) -> None:
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(kwargs)


# ── ENDPOINTS: JOBS ──────────────────────────────────────────────────────────

@app.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job(job_id: str, request: Request):
    """Poll a running job's status. Owner-only — completed jobs may include
    the full analysis result, so anonymous lookups would leak user data."""
    user = _require_user(request)
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    # Strict ownership: must own the job (admins bypass for support).
    # Returns 404 (not 403) for non-owners so we don't confirm the ID exists.
    if job.get("user_id") != user["id"] and not user.get("is_admin"):
        raise HTTPException(status_code=404, detail="Job not found")
    return JobStatus(
        job_id=job["job_id"],
        status=job["status"],
        progress=job.get("progress"),
        created_at=job["created_at"],
        result=job.get("result") if job["status"] == "completed" else None,
        error=job.get("error") if job["status"] == "failed" else None,
    )


@app.get("/jobs")
async def list_jobs(request: Request):
    """List current user's in-memory jobs (active + recent)."""
    user = _get_current_user(request)
    with jobs_lock:
        user_jobs = [
            {
                "job_id": j["job_id"],
                "status": j["status"],
                "progress": j.get("progress"),
                "created_at": j["created_at"],
                "config": j.get("config"),
            }
            for j in jobs.values()
            if (user and j.get("user_id") == user["id"]) or
               (not user and j.get("ip") == _get_client_ip(request))
        ]
    return {"jobs": sorted(user_jobs, key=lambda x: x["created_at"], reverse=True)}


# ── ENDPOINTS: DEBATES (persistent DB) ────────────────────────────────────────

@app.get("/debates")
async def list_all_debates(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(None, min_length=2, max_length=100),
    mode: Optional[str] = Query(None, description="Filter by mode: 'solo' or 'debate' (legacy 'debate_1v1', 'reaction' also accepted)"),
):
    """List the CURRENT USER's saved debates. Auth required.

    The previous version allowed anonymous callers (and ?mine=false) to receive
    every debate in the database. Now: login required + always scoped to the
    user's own rows. Admins are no exception — there's a separate admin
    endpoint for cross-user listings if ever needed.
    """
    user = _require_user(request)
    uid = user["id"]

    # Normalize legacy mode filters so a user clicking "Debate" shows BOTH new
    # 'debate' rows AND historical 'debate_1v1' rows in the DB. Same for 'solo'
    # which now also includes legacy 'reaction' rows.
    mode_filter: Optional[List[str]] = None
    if mode:
        m = mode.strip().lower()
        if m in ("debate", "debate_1v1"):
            mode_filter = ["debate", "debate_1v1"]
        elif m in ("solo", "reaction"):
            mode_filter = ["solo", "reaction"]
        else:
            mode_filter = [m]

    if search:
        debates = search_debates(search, limit=limit, user_id=uid, mode=mode_filter)
        return {"debates": debates, "total": len(debates)}

    debates = list_debates(limit=limit, offset=offset, user_id=uid, mode=mode_filter)
    total = count_debates(user_id=uid, mode=mode_filter)
    return {"debates": debates, "total": total}


@app.get("/debates/{debate_id}")
async def get_debate_detail(debate_id: str, request: Request):
    """Get full debate details. Owner-only — anyone else gets 404 (not 403)
    so debate_id existence isn't leaked to other users."""
    user = _require_user(request)
    debate = get_debate(debate_id)
    if not debate:
        raise HTTPException(status_code=404, detail="Debate not found")
    # Strict ownership check. Admins also bypass (for support / moderation).
    if debate.get("user_id") != user["id"] and not user.get("is_admin"):
        raise HTTPException(status_code=404, detail="Debate not found")
    return debate


class RerunRequest(BaseModel):
    """Optional overrides for a rerun — defaults come from the saved debate."""
    mode: Optional[str] = None
    language: Optional[str] = None


def _run_recheck(job_id: str, debate_id: str, language: str,
                 user_id: int, is_admin: bool) -> None:
    """Re-run only the fact-checking over an analysis that is already saved.

    The arguments are not touched. Only the premises are checked again, so the
    sources, the verdicts and the per-source labels are refreshed while the
    argument structure, the fallacies and the rebuttals stay exactly as the
    reader left them.
    """
    try:
        _update_job(job_id, status="processing", progress="Loading saved analysis...")
        debate = get_debate(debate_id)
        if not debate:
            raise ValueError("Debate not found")

        analysis = debate.get("analysis_json") or {}
        if isinstance(analysis, str):
            analysis = json.loads(analysis or "{}")
        speakers = analysis.get("speakers") or {}
        if not speakers:
            raise ValueError("Saved analysis has no arguments to check")

        from config_loader import job_overrides
        with job_overrides(**{"pipeline.language": language}):
            _update_job(job_id, progress="Fact-checking claims...")
            from fact_checker import FactChecker
            from debate_analyzer import render_text_report

            fact_check = FactChecker().fact_check_arguments(speakers)
            report = render_text_report(analysis, fact_check)

        from database import update_debate_fact_check
        update_debate_fact_check(
            debate_id,
            json.dumps(fact_check, ensure_ascii=False),
            report_text=report,
        )

        summary = fact_check.get("summary", {})
        logger.info("Recheck %s done: %d claims, %s", debate_id,
                    fact_check.get("total_claims", 0),
                    summary.get("verdict_breakdown", {}))
        _update_job(job_id, status="completed", progress="Done",
                    result={"debate_id": debate_id,
                            "fact_check_summary": summary})
    except Exception as e:
        logger.error("Recheck %s failed: %s", debate_id, e, exc_info=True)
        _update_job(job_id, status="failed", error=str(e))
        if not is_admin:
            try:
                from database import refund_credit
                refund_credit(user_id)
            except Exception as refund_err:
                logger.error("Refund after failed recheck failed: %s", refund_err)


@app.post("/debates/{debate_id}/recheck", response_model=JobStatus)
async def recheck_debate(debate_id: str, request: Request):
    """Re-run ONLY the fact-checking of a saved debate, in place.

    A full rerun re-does the transcription-free pipeline and creates a new
    entry. This one keeps the arguments, the fallacies and the rebuttals as
    they are and refreshes just the sources and the verdicts, which is what a
    reader wants after a fact-checking fix. Owner-only (404 otherwise)."""
    user = _require_user(request)
    debate = get_debate(debate_id)
    if not debate or (debate.get("user_id") != user["id"] and not user.get("is_admin")):
        raise HTTPException(status_code=404, detail="Debate not found")

    analysis = debate.get("analysis_json") or {}
    if isinstance(analysis, str):
        analysis = json.loads(analysis or "{}")
    if not (analysis.get("speakers") or {}):
        raise HTTPException(
            status_code=409,
            detail={"error": "Nothing to check",
                    "message": "Ta analiza nima argumentov, katerih premise bi lahko preverili."})

    ip, key, remaining = _reserve_quota(request, user)
    _record_request(key)

    job_id = uuid.uuid4().hex[:12]
    now = _utcnow().isoformat()
    with jobs_lock:
        jobs[job_id] = {
            "job_id": job_id, "status": "queued",
            "progress": "Waiting to start...", "created_at": now,
            "result": None, "error": None,
            "config": {"recheck_of": debate_id},
            "user_id": user["id"], "ip": ip,
        }

    threading.Thread(
        target=_run_recheck,
        args=(job_id, debate_id, (debate.get("language") or "sl"),
              user["id"], bool(user.get("is_admin"))),
        daemon=True,
    ).start()
    logger.info("Recheck of %s submitted by %s (%d remaining)",
                debate_id, user["username"], remaining - 1)
    return JobStatus(job_id=job_id, status="queued",
                     progress="Waiting to start...", created_at=now)


@app.post("/debates/{debate_id}/rerun", response_model=JobStatus)
async def rerun_debate(debate_id: str, request: Request,
                       body: Optional[RerunRequest] = None):
    """Re-run the ANALYSIS of a saved debate using its existing transcript —
    no YouTube download, no transcription. Useful after prompt / house-rule
    changes (the analysis cache auto-invalidates on prompt changes, so the
    rerun really uses the current rules). Creates a NEW debate entry so the
    old result stays available for comparison. Owner-only (404 otherwise)."""
    user = _require_user(request)
    debate = get_debate(debate_id)
    if not debate or (debate.get("user_id") != user["id"] and not user.get("is_admin")):
        raise HTTPException(status_code=404, detail="Debate not found")

    # Persistent home first, legacy scratch location second.
    transcript_text = ""
    for tp in (Path(f"transcripts/{debate_id}.txt"),
               Path(f"jobs/{debate_id}/data/transcript.txt")):
        if tp.exists():
            transcript_text = tp.read_text(encoding="utf-8").strip()
            if transcript_text:
                break
    if not transcript_text:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Transcript unavailable",
                "message": "Prepis te analize ni več na disku — poženi polno "
                           "analizo (z avdiom/URL) še enkrat.",
            },
        )

    body = body or RerunRequest()
    mode = (body.mode or debate.get("mode") or "debate").strip().lower()
    if mode not in ("solo", "debate", "reaction", "debate_1v1"):
        raise HTTPException(status_code=400, detail="Invalid mode")
    language = (body.language or debate.get("language") or "sl").strip().lower()

    return _start_job(
        request, user,
        youtube_url=debate.get("youtube_url") or "",
        mode=mode,
        language=language,
        speaker_names=debate.get("speaker_names") or "",
        title=debate.get("title") or "",
        transcript_override=transcript_text,
    )


@app.delete("/debates/{debate_id}")
async def delete_debate_endpoint(debate_id: str, request: Request):
    """Delete a debate by ID. Only the owner can delete."""
    user = _get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    debate = get_debate(debate_id)
    if not debate:
        raise HTTPException(status_code=404, detail="Debate not found")
    if debate.get("user_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to delete this debate")
    from database import delete_debate
    delete_debate(debate_id)
    return {"status": "deleted"}


# ── EDIT ENDPOINT — owner can rename speakers / edit / add / delete arguments
#    + edit summary, position, conclusions etc. directly in the analysis JSON. ──

class EditOp(BaseModel):
    """One edit operation. The frontend sends a list of these in order.

    Supported `op` values:
      • rename_speaker:  {from_name, to_name}
      • edit_speaker_meta: {speaker, fields: {position?, conclusions?}}
      • edit_argument:   {speaker, index, fields: {argument?, premises?, type?, ...}}
      • add_argument:    {speaker, argument: {argument, type, premises, ...}}
      • move_argument:   {from_speaker, index, to_speaker}
      • delete_argument: {speaker, index}
      • add_rebuttal:    {rebuttal: {by, to, target_arg_id, rebuttal_content, ...}}
      • edit_rebuttal:   {index, fields: {...}}
      • delete_rebuttal: {index}
      • add_fallacy:     {fallacy: {speaker, type, evidence, explanation?, ...}}
      • edit_fallacy:    {index, fields: {type?, evidence?, explanation?, ...}}
      • delete_fallacy:  {index}
      • review_fallacy:  {index, verdict: "confirmed"|"dismissed"|null}
      • edit_summary:    {summary}
      • edit_title:      {title}
      • edit_metadata:   {fields: {topic?}}
    """
    op: str
    # Free-form payload — validated per-op in apply logic below
    payload: Dict[str, Any] = {}


class EditRequest(BaseModel):
    operations: List[EditOp]


def _canon_fallacy(raw) -> str:
    """Normalize a hand-typed fallacy name to the closed vocabulary used by the
    model, so a manual entry is counted and displayed like a detected one."""
    from llm_schemas import canonical_fallacy_type
    return canonical_fallacy_type(str(raw or ""))


def _fallacy_category(ftype: str) -> str:
    """Category follows the NAME — the same rule the schema applies to model
    output (see llm_schemas.align_category_with_type). A name outside the closed
    vocabulary gets the neutral default."""
    from llm_schemas import _NAME_TO_CATEGORY
    return _NAME_TO_CATEGORY.get(ftype, "informal")


def _apply_edits(analysis: Dict, operations: List[EditOp]) -> Tuple[Dict, List[str]]:
    """Apply a list of edit ops to the analysis dict. Returns (new_analysis, applied_log).
    Pure function — does not touch DB."""
    speakers = analysis.setdefault("speakers", {})
    applied: List[str] = []

    for op_obj in operations:
        op = op_obj.op
        p = op_obj.payload or {}

        if op == "rename_speaker":
            old, new = (p.get("from_name") or "").strip(), (p.get("to_name") or "").strip()
            if not old or not new or old == new:
                continue
            if old in speakers and new not in speakers:
                speakers[new] = speakers.pop(old)
                applied.append(f"renamed speaker {old!r} → {new!r}")

            # Arguments live INSIDE speakers[name]['arguments'] so they move with the rename.
            # We just need to propagate to every OTHER place the name is referenced.

            # Fallacies / rebuttals / evasions
            for fal in analysis.get("fallacies", []) or []:
                if fal.get("speaker") == old:
                    fal["speaker"] = new
            for reb in analysis.get("rebuttals", []) or []:
                if reb.get("by") == old:  reb["by"] = new
                if reb.get("to") == old:  reb["to"] = new
            for ev in analysis.get("evasions", []) or []:
                if ev.get("evading_speaker") == old:  ev["evading_speaker"] = new

            # ── comparative_evaluation: speaker-keyed dicts ──────────────
            comp = analysis.get("comparative_evaluation", {}) or {}
            per = comp.get("per_speaker")
            if isinstance(per, dict) and old in per:
                per[new] = per.pop(old)
            # Moderator influence: the debater who was pressed harder
            mod_inf = comp.get("moderator_influence")
            if isinstance(mod_inf, dict) and mod_inf.get("pressed_more") == old:
                mod_inf["pressed_more"] = new

            # ── moderator block (name + who was pressed harder) ──────────
            mod = analysis.get("moderator")
            if isinstance(mod, dict):
                if mod.get("name") == old:
                    mod["name"] = new
                if mod.get("pressed_more") == old:
                    mod["pressed_more"] = new

            # ── metadata.participants (role mapping)
            meta = analysis.get("metadata", {}) or {}
            participants = meta.get("participants") or {}
            if isinstance(participants, dict) and old in participants:
                participants[new] = participants.pop(old)

            # ── Fact-checks (when embedded in analysis.fact_check_data)
            fc = analysis.get("fact_check_data") or {}
            for f in (fc.get("fact_checks") or []) if isinstance(fc, dict) else []:
                if f.get("speaker") == old:
                    f["speaker"] = new

        elif op == "edit_speaker_meta":
            sp = (p.get("speaker") or "").strip()
            fields = p.get("fields") or {}
            if sp in speakers and isinstance(fields, dict):
                allowed = {"position", "conclusions"}
                for k, v in fields.items():
                    if k in allowed:
                        speakers[sp][k] = v
                applied.append(f"edited meta of speaker {sp!r}")

        elif op == "edit_argument":
            sp = (p.get("speaker") or "").strip()
            idx = p.get("index")
            fields = p.get("fields") or {}
            if sp in speakers and isinstance(idx, int) and isinstance(fields, dict):
                args = speakers[sp].get("arguments", []) or []
                if 0 <= idx < len(args):
                    allowed = {"argument", "type", "premises"}
                    for k, v in fields.items():
                        if k in allowed:
                            args[idx][k] = v
                    applied.append(f"edited argument #{idx} of {sp!r}")

        elif op == "delete_argument":
            sp = (p.get("speaker") or "").strip()
            idx = p.get("index")
            if sp in speakers and isinstance(idx, int):
                args = speakers[sp].get("arguments", []) or []
                if 0 <= idx < len(args):
                    args.pop(idx)
                    applied.append(f"deleted argument #{idx} of {sp!r}")

        elif op == "review_fallacy":
            # The reader confirms or dismisses a DETECTED fallacy without deleting
            # it. Detection and the verdict on detection stay as two separate
            # records, which is what makes precision computable afterwards:
            # deleting a wrong detection would erase the very thing being counted.
            idx = p.get("index")
            verdict = p.get("verdict")
            fallacies = analysis.get("fallacies", []) or []
            if (isinstance(idx, int) and 0 <= idx < len(fallacies)
                    and verdict in ("confirmed", "dismissed", None)):
                if verdict is None:
                    fallacies[idx].pop("review", None)
                else:
                    fallacies[idx]["review"] = verdict
                applied.append(f"reviewed fallacy #{idx}: {verdict}")

        elif op == "move_argument":
            # Reassign an argument from one speaker to another (within or across sides).
            # Used by the side-grouped editor when user changes the speaker badge.
            sp_from = (p.get("from_speaker") or "").strip()
            sp_to = (p.get("to_speaker") or "").strip()
            idx = p.get("index")
            if (sp_from in speakers and sp_to in speakers
                    and sp_from != sp_to and isinstance(idx, int)):
                src_args = speakers[sp_from].get("arguments", []) or []
                if 0 <= idx < len(src_args):
                    arg = src_args.pop(idx)
                    speakers[sp_to].setdefault("arguments", []).append(arg)
                    applied.append(f"moved argument #{idx} from {sp_from!r} → {sp_to!r}")

        elif op == "add_argument":
            sp = (p.get("speaker") or "").strip()
            arg = p.get("argument") or {}
            if sp in speakers and isinstance(arg, dict) and arg.get("argument"):
                speakers[sp].setdefault("arguments", []).append({
                    "argument": str(arg.get("argument", ""))[:5000],
                    "type": arg.get("type", "factual"),
                    "premises": arg.get("premises", []) if isinstance(arg.get("premises"), list) else [],
                    "user_added": True,   # not auto-assessed
                })
                applied.append(f"added argument to {sp!r}")

        elif op == "add_rebuttal":
            # Add a new rebuttal. Payload: {by, to, target_claim, rebuttal_content,
            # rebuttal_type?, response?}
            rb = p.get("rebuttal") or {}
            content = (rb.get("rebuttal_content") or "").strip()
            by = (rb.get("by") or "").strip()
            if content and by:
                new_rb = {
                    "by": by[:200],
                    "to": (rb.get("to") or "").strip()[:200],
                    "target_claim": (rb.get("target_claim") or "")[:5000],
                    "rebuttal_content": content[:5000],
                    "rebuttal_type": rb.get("rebuttal_type", "direct_contradiction"),
                    "response": (rb.get("response") or "")[:2000],
                    "user_added": True,   # marker so UI can distinguish manual additions
                }
                analysis.setdefault("rebuttals", []).append(new_rb)
                applied.append(f"added rebuttal by {by!r}")

        elif op == "edit_rebuttal":
            idx = p.get("index")
            fields = p.get("fields") or {}
            rebuts = analysis.get("rebuttals", []) or []
            if isinstance(idx, int) and 0 <= idx < len(rebuts) and isinstance(fields, dict):
                allowed = {"by", "to", "target_claim", "rebuttal_content",
                           "rebuttal_type", "response"}
                for k, v in fields.items():
                    if k in allowed:
                        rebuts[idx][k] = v
                applied.append(f"edited rebuttal #{idx}")

        elif op == "delete_rebuttal":
            idx = p.get("index")
            rebuts = analysis.get("rebuttals", []) or []
            if isinstance(idx, int) and 0 <= idx < len(rebuts):
                rebuts.pop(idx)
                applied.append(f"deleted rebuttal #{idx}")

        elif op == "edit_summary":
            new_summary = p.get("summary")
            if isinstance(new_summary, str):
                analysis["summary"] = new_summary[:10000]
                applied.append("edited summary")

        elif op == "edit_title":
            # Title is a top-level DB column (not stored inside analysis_json).
            # Carry the pending value via a special key; the endpoint extracts it.
            new_title = p.get("title")
            if isinstance(new_title, str):
                analysis["_pending_title"] = new_title.strip()[:300]
                applied.append("edited title")

        elif op == "edit_metadata":
            fields = p.get("fields") or {}
            meta = analysis.setdefault("metadata", {})
            for k, v in fields.items():
                if k == "topic" and isinstance(v, str):
                    meta[k] = v[:500]
            applied.append("edited metadata")

        # ── Fallacies: the reader is the final judge ──────────────────────
        # Zaznava zmot se moti v obe smeri, zato mora biti popravljiva v obe.
        # Ročno dodana zmota gre skozi isti slovar kot samodejna.
        elif op == "add_fallacy":
            fal = p.get("fallacy") or {}
            speaker = (fal.get("speaker") or "").strip()
            ftype = _canon_fallacy(fal.get("type"))
            evidence = (fal.get("evidence") or "").strip()
            if speaker and ftype and evidence:
                analysis.setdefault("fallacies", []).append({
                    "speaker": speaker[:200],
                    "type": ftype,
                    "category": _fallacy_category(ftype),
                    "evidence": evidence[:2000],
                    "explanation": (fal.get("explanation") or "")[:2000],
                    "target_arg_id": (fal.get("target_arg_id") or "")[:100],
                    "user_added": True,
                })
                applied.append(f"added fallacy {ftype!r} for {speaker!r}")

        elif op == "edit_fallacy":
            idx = p.get("index")
            fields = p.get("fields") or {}
            fallacies = analysis.get("fallacies", []) or []
            if isinstance(idx, int) and 0 <= idx < len(fallacies) and isinstance(fields, dict):
                fal = fallacies[idx]
                if "type" in fields:
                    ftype = _canon_fallacy(fields["type"])
                    if ftype:
                        fal["type"] = ftype
                        # Category always follows the name, never the other way.
                        fal["category"] = _fallacy_category(ftype)
                for k in ("evidence", "explanation", "target_arg_id", "speaker"):
                    if k in fields and isinstance(fields[k], str):
                        fal[k] = fields[k][:2000]
                applied.append(f"edited fallacy #{idx}")

        elif op == "delete_fallacy":
            idx = p.get("index")
            fallacies = analysis.get("fallacies", []) or []
            if isinstance(idx, int) and 0 <= idx < len(fallacies):
                removed = fallacies.pop(idx)
                applied.append(f"deleted fallacy #{idx} ({removed.get('type', '?')})")

    return analysis, applied


@app.patch("/debates/{debate_id}")
async def edit_debate(debate_id: str, body: EditRequest, request: Request):
    """Apply edit operations to a debate's analysis. Only the owner can edit."""
    user = _require_user(request)
    debate = get_debate(debate_id)
    if not debate:
        raise HTTPException(status_code=404, detail="Debate not found")
    if debate.get("user_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to edit this debate")

    if not body.operations:
        raise HTTPException(status_code=422, detail="No operations provided")
    if len(body.operations) > 100:
        raise HTTPException(status_code=422, detail="Too many operations (max 100 per request)")

    analysis = debate.get("analysis_json")
    if not isinstance(analysis, dict):
        raise HTTPException(status_code=409, detail="Debate analysis not editable (missing or invalid)")

    new_analysis, applied = _apply_edits(analysis, body.operations)

    # Pop the special title key (set by edit_title op) so it doesn't leak into
    # the persisted JSON; pass it as a column update instead.
    pending_title = new_analysis.pop("_pending_title", None)

    # Update derived columns
    new_summary = new_analysis.get("summary") or debate.get("summary") or ""
    new_speakers_csv = ", ".join((new_analysis.get("speakers") or {}).keys())

    from database import update_debate_analysis
    ok = update_debate_analysis(
        debate_id,
        analysis_json=json.dumps(new_analysis, ensure_ascii=False),
        summary=new_summary,
        speakers=new_speakers_csv,
        title=pending_title,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to persist edits")
    logger.info("User %s edited debate %s: %s", user["username"], debate_id, ", ".join(applied) or "no-op")
    return {"status": "ok", "applied": applied, "operations": len(body.operations)}






# ── PDF EXPORT (owner-gated) ──────────────────────────────────────────────────

def _require_owned_debate(debate_id: str, request: Request) -> Dict:
    """Load a debate the caller owns (admins bypass). 404 if missing/!owned —
    same non-leaking behavior as the detail endpoint."""
    user = _require_user(request)
    debate = get_debate(debate_id)
    if not debate:
        raise HTTPException(status_code=404, detail="Debate not found")
    if debate.get("user_id") != user["id"] and not user.get("is_admin"):
        raise HTTPException(status_code=404, detail="Debate not found")
    return debate


@app.get("/debates/{debate_id}/pdf")
async def export_debate_pdf(debate_id: str, request: Request):
    """Render a debate's analysis (verdict, per-speaker arguments, and fact-checked
    claims with their sources) to a downloadable PDF. Owner-gated."""
    debate = _require_owned_debate(debate_id, request)
    try:
        import pdf_export
        data = pdf_export.build_pdf(debate, language=debate.get("language", "sl"))
    except Exception as e:
        logger.error("PDF export failed for %s: %s", debate_id, e)
        raise HTTPException(status_code=500, detail=f"PDF export ni uspel: {e}")
    base = debate.get("title") or debate.get("topic") or "debate"
    safe = re.sub(r"[^\w\- ]+", "", base).strip()[:60] or "debate"
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe}.pdf"'},
    )


# ── BACKGROUND CLEANUP ────────────────────────────────────────────────────────

MAX_JOBS_IN_MEMORY = 200  # Hard cap to prevent OOM


def _purge_job_dir(job_id: str) -> None:
    """Slim the on-disk dir of a finished job: delete the audio and scratch
    output (they leak disk — audio can be 500MB), but KEEP data/transcript.txt.
    The transcript is what powers the rerun feature (re-analysis without paying
    for download + transcription again); it is a few 100KB of text at most."""
    job_dir = Path(f"jobs/{job_id}")
    if not job_dir.exists():
        return
    try:
        keep = job_dir / "data" / "transcript.txt"
        for item in job_dir.iterdir():
            if item.name == "data":
                for f in item.iterdir():
                    if f != keep:
                        (shutil.rmtree(f, ignore_errors=True) if f.is_dir()
                         else f.unlink(missing_ok=True))
            else:
                (shutil.rmtree(item, ignore_errors=True) if item.is_dir()
                 else item.unlink(missing_ok=True))
        if not keep.exists():  # nothing worth keeping → drop the empty shell
            shutil.rmtree(job_dir, ignore_errors=True)
    except OSError as e:
        logger.warning("Failed to purge job dir %s: %s", job_dir, e)


def _purge_orphan_job_dirs() -> None:
    """At startup, slim STALE leftover jobs/<id> dirs from previous runs
    (audio and scratch out, transcripts kept for reruns).

    CRITICAL: only dirs untouched for several hours are swept. Under
    `uvicorn --reload` the dev server restarts whenever the pipeline writes a
    file, which re-runs this sweep — an age guard prevents it from deleting
    the audio of a job that is mid-flight across such a restart."""
    jobs_root = Path("jobs")
    if not jobs_root.is_dir():
        return
    cutoff = time.time() - 6 * 3600
    swept = 0
    for d in jobs_root.iterdir():
        if not d.is_dir():
            continue
        try:
            newest = max((p.stat().st_mtime for p in d.rglob("*")),
                         default=d.stat().st_mtime)
        except OSError:
            continue
        if newest >= cutoff:
            continue  # recently touched — possibly an in-flight job
        _purge_job_dir(d.name)
        swept += 1
    if swept:
        logger.info("Startup cleanup: slimmed %d stale job dir(s) (transcripts kept)", swept)


def _cleanup_old_jobs() -> None:
    while True:
        time.sleep(300)
        cutoff = time.time() - JOB_TTL_SECONDS
        removed: list = []
        with jobs_lock:
            expired = []
            for jid, j in jobs.items():
                if j["status"] not in ("completed", "failed"):
                    continue
                try:
                    ts = datetime.fromisoformat(j["created_at"]).timestamp()
                except (ValueError, KeyError):
                    ts = 0  # Malformed timestamp → mark for cleanup
                if ts < cutoff:
                    expired.append(jid)
            for jid in expired:
                del jobs[jid]
            removed.extend(expired)

            # Hard cap: if still too many jobs, remove oldest completed/failed
            if len(jobs) > MAX_JOBS_IN_MEMORY:
                finished = sorted(
                    [(jid, j) for jid, j in jobs.items() if j["status"] in ("completed", "failed")],
                    key=lambda x: x[1].get("created_at", ""),
                )
                excess = len(jobs) - MAX_JOBS_IN_MEMORY
                for jid, _ in finished[:excess]:
                    del jobs[jid]
                    removed.append(jid)
                if excess > 0:
                    logger.info("Hard-cap cleanup: removed %d oldest jobs", excess)

        # Purge on-disk scratch dirs OUTSIDE the lock (rmtree can be slow).
        for jid in removed:
            _purge_job_dir(jid)

        if expired:
            logger.info("Cleaned up %d expired in-memory jobs", len(expired))


# Startup housekeeping lives in the lifespan handler above, which runs it once
# when the app starts. It used to be duplicated here at import time as well, so
# every run had two cleanup threads sweeping the same directories against each
# other, and the purge ran twice.


# ── ERROR HANDLER ─────────────────────────────────────────────────────────────

@app.exception_handler(422)
async def validation_error_handler(request: Request, exc):
    return JSONResponse(status_code=422, content={"error": "Invalid request", "detail": str(exc)})


# ── STATIC FILES (production: serve built React frontend) ────────────────────

STATIC_DIR = Path(__file__).parent / "static"
_index_html = STATIC_DIR / "index.html"
if STATIC_DIR.is_dir() and _index_html.is_file():
    from fastapi.staticfiles import StaticFiles

    # Serve static assets (JS, CSS, images)
    _assets_dir = STATIC_DIR / "assets"
    if _assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="static-assets")

    # Known API prefixes that should NOT be caught by the SPA fallback
    _API_PREFIXES = (
        "api", "health", "docs", "redoc", "openapi.json",
        "register", "login", "logout", "me", "admin", "auth",
        "analyze", "jobs", "job", "debates", "rate-limit",
    )

    # Catch-all: serve index.html for any non-API route (SPA routing)
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Never intercept API routes
        first_segment = full_path.split("/")[0] if full_path else ""
        if first_segment in _API_PREFIXES:
            raise HTTPException(status_code=404, detail="Not found")
        # If the file exists in static dir, serve it directly
        file_path = STATIC_DIR / full_path
        if full_path and file_path.is_file():
            return FileResponse(str(file_path))
        # Otherwise serve index.html (React Router handles the route)
        return FileResponse(str(_index_html))
    logger.info("SPA static serving enabled from %s", STATIC_DIR)
else:
    logger.warning("No static dir found at %s — SPA serving disabled", STATIC_DIR)


# ── DEV SERVER ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
