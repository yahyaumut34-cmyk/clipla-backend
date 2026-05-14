"""
Clipla Security Module
- API key authentication (Bearer token)
- Job ownership tracking
- IP anonymization (KVKK/GDPR)
- File validation (magic bytes, size limit, extension whitelist)
"""
import os
import hmac
import hashlib
from typing import Optional

from fastapi import Request, HTTPException, UploadFile

# ── API Key Auth ──────────────────────────────────────────────────────────────

def _get_valid_keys() -> set:
    """All valid API keys (free + pro combined)."""
    raw_free = os.getenv("API_SECRET_KEY", "")
    raw_pro  = os.getenv("PRO_API_KEYS", "")
    keys = set()
    for raw in (raw_free, raw_pro):
        keys.update(k.strip() for k in raw.split(",") if k.strip())
    return keys


def _get_pro_keys() -> set:
    raw = os.getenv("PRO_API_KEYS", "")
    return {k.strip() for k in raw.split(",") if k.strip()}


def _extract_bearer(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def get_caller_key(request: Request) -> str:
    """Return the API key from request state (set by middleware) or header."""
    state_key = getattr(request.state, "api_key", None)
    if state_key:
        return state_key
    return _extract_bearer(request) or "dev"


def is_pro_key(key: str) -> bool:
    return bool(key) and key in _get_pro_keys()


def get_plan_from_request(request: Request) -> str:
    """Return 'pro' or 'free' based on the caller's API key."""
    return "pro" if is_pro_key(get_caller_key(request)) else "free"


def check_auth(request: Request) -> None:
    """
    Enforce API key auth. Called by middleware for all /api/* routes.
    Skipped if API_SECRET_KEY is not configured (dev mode).
    """
    valid_keys = _get_valid_keys()
    if not valid_keys:
        # Dev mode — no key configured, allow all
        request.state.api_key = "dev"
        return

    key = _extract_bearer(request)
    if not key or key not in valid_keys:
        raise HTTPException(
            status_code=401,
            detail="Yetkilendirme gerekli. Authorization: Bearer <api-key>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.api_key = key


# ── Job Ownership ─────────────────────────────────────────────────────────────

# In-memory map: job_id → key_hash[:16]
# NOTE: lost on restart / multi-worker. For persistence use Redis or Supabase.
_JOB_OWNERS: dict = {}


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def claim_job(job_id: str, request: Request) -> None:
    """Associate job_id with the caller's API key (called after upload)."""
    key = get_caller_key(request)
    _JOB_OWNERS[job_id] = _key_hash(key)


def verify_job_owner(job_id: str, request: Request) -> None:
    """
    Raise 403 if the caller doesn't own job_id.
    If no owner is recorded (pre-auth jobs / dev mode), allow.
    """
    owner = _JOB_OWNERS.get(job_id)
    if owner is None:
        return  # Not tracked — backward compat / dev mode
    key = get_caller_key(request)
    if owner != _key_hash(key):
        raise HTTPException(status_code=403, detail="Bu işe erişim izniniz yok.")


# ── IP Anonymization ──────────────────────────────────────────────────────────

_IP_SALT = os.getenv("IP_HASH_SALT", "clipla-ip-salt-2026")


def hash_ip(ip: str) -> str:
    """One-way HMAC-SHA256 hash of IP (KVKK/GDPR compliant)."""
    h = hmac.new(_IP_SALT.encode(), ip.encode(), hashlib.sha256)
    return h.hexdigest()[:16]


# ── File Validation ───────────────────────────────────────────────────────────

_ALLOWED_AUDIO_SUFFIXES = {".webm", ".wav", ".mp3", ".m4a", ".ogg", ".opus", ".aac"}

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "800")) * 1024 * 1024


def is_valid_video_magic(header: bytes) -> bool:
    """
    Check first 12 bytes for known video container signatures.
    Returns False for zero-byte or clearly non-video data.
    """
    if len(header) < 8:
        return False
    # MP4/MOV: bytes 4-7 are 'ftyp', 'moov', 'mdat', 'free', 'skip'
    if header[4:8] in (b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide"):
        return True
    # WebM / MKV
    if header[:4] == b"\x1a\x45\xdf\xa3":
        return True
    # AVI (RIFF container)
    if header[:4] == b"RIFF":
        return True
    # MPEG-TS
    if header[:1] == b"\x47":
        return True
    return False


def sanitize_audio_suffix(filename: Optional[str]) -> str:
    """Return whitelisted audio extension from filename, default .webm."""
    if not filename:
        return ".webm"
    ext = os.path.splitext(filename)[1].lower()
    return ext if ext in _ALLOWED_AUDIO_SUFFIXES else ".webm"


async def read_with_size_limit(file: UploadFile, limit: int = MAX_UPLOAD_BYTES) -> bytes:
    """
    Stream-read upload file with server-side size guard.
    Returns raw bytes. Raises HTTP 413 if limit exceeded.
    """
    data = bytearray()
    chunk_size = 1024 * 1024  # 1 MB chunks
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(
                status_code=413,
                detail=f"Dosya çok büyük. Maksimum {limit // (1024 * 1024)} MB yüklenebilir.",
            )
    return bytes(data)
