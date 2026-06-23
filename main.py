import time
import os
import re
import uuid
import json
import base64
import shutil
import tempfile
import subprocess
import sys
import logging
import httpx
import anthropic

logger = logging.getLogger(__name__)

# .env dosyasını yükle
from dotenv import load_dotenv
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

_sync_anthropic: Optional[anthropic.Anthropic] = None
_async_anthropic: Optional[anthropic.AsyncAnthropic] = None

def _get_sync_client() -> anthropic.Anthropic:
    global _sync_anthropic
    if _sync_anthropic is None:
        _sync_anthropic = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    return _sync_anthropic

def _get_async_client() -> anthropic.AsyncAnthropic:
    global _async_anthropic
    if _async_anthropic is None:
        _async_anthropic = anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    return _async_anthropic

from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

# ✅ Command router
from clipla_api.command import router as command_router
# ✅ Command function import (process endpoint için)
from clipla_api.command import create_edit_plan, CommandBody
# ✅ Shorts router
from api.shorts import router as shorts_router
# ✅ Subtitles router
from api.subtitles import router as subtitles_router
# ✅ SFX (ses efektleri) router
from api.sfx import router as sfx_router
# ✅ Background music router
from api.music_bg import router as music_router
# ✅ Multi-video merge router
from api.merge import router as merge_router
# ✅ Beat Sync router
from api.beat_sync import router as beat_sync_router
# ✅ Background Removal router
from api.bg_remove import router as bg_remove_router
# ✅ Audio Enhancement router
from api.audio_enhance import router as audio_enhance_router
# ✅ Speed Control router
from api.speed import router as speed_router
# ✅ Color Grading router
from api.color import router as color_router
# ✅ Transform (rotate/crop/reframe) router
from api.transform import router as transform_router
# ✅ Text Overlay router
from api.text_overlay import router as text_router
# ✅ Visual Filters router
from api.filters import router as filters_router
# ✅ Reverse router
from api.reverse import router as reverse_router
# ✅ Local Whisper
from faster_whisper import WhisperModel
# ✅ Supabase
from services.supabase_client import sb
# ✅ Security
from services.security import (
    check_auth, get_plan_from_request,
    claim_job, verify_job_owner,
    hash_ip, read_with_size_limit, is_valid_video_magic,
    sanitize_audio_suffix, MAX_UPLOAD_BYTES,
)
# ✅ Rate limiting
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware


# =========================================================
# Config
# =========================================================
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")

# =========================================================
# App
# =========================================================
app = FastAPI(title="Clipla Backend")
app.include_router(command_router)
app.include_router(shorts_router)
app.include_router(subtitles_router)
app.include_router(sfx_router)
app.include_router(music_router)
app.include_router(merge_router)
app.include_router(beat_sync_router)
app.include_router(bg_remove_router)
app.include_router(audio_enhance_router)
app.include_router(speed_router)
app.include_router(color_router)
app.include_router(transform_router)
app.include_router(text_router)
app.include_router(filters_router)
app.include_router(reverse_router)

# ── Rate limiter ───────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── API Auth middleware ────────────────────────────────────────────────────────
class AuthMiddleware(BaseHTTPMiddleware):
    """Enforce Bearer token auth on all /api/* routes except /health and /api/demo."""
    _OPEN_PATHS = {"/health", "/api/video/health", "/api/demo/verify"}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path not in self._OPEN_PATHS:
            try:
                check_auth(request)
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        return await call_next(request)

_CORS_ORIGINS_RAW = os.getenv("CORS_ORIGINS", "*")
_CORS_ORIGINS = [o.strip() for o in _CORS_ORIGINS_RAW.split(",") if o.strip()]

# Auth middleware önce eklenir (daha iç katmanda çalışır)
app.add_middleware(AuthMiddleware)

# CORS en dışta çalışmalı — en son eklenir
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=False,   # credentials=True + wildcard origin güvenlik açığı
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-User-Plan"],
)


# =========================================================
# Paths
# =========================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STATIC_DIR = os.path.join(BASE_DIR, "static")     # demo: static/index.html
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
TMP_DIR = os.path.join(OUTPUT_DIR, "tmp")

# ✅ Music library (folders: energetic/rhythmic/slow/calm/minimal)
MUSIC_DIR = os.path.join(BASE_DIR, "music")

os.makedirs(STATIC_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(TMP_DIR, exist_ok=True)
os.makedirs(MUSIC_DIR, exist_ok=True)

# static assets (demo vs)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# outputs download
app.mount("/outputs", StaticFiles(directory=OUTPUT_DIR), name="outputs")

# jobs klasörü (shorts, input video vb.)
JOBS_DIR_STATIC = os.path.join(BASE_DIR, "jobs")
os.makedirs(JOBS_DIR_STATIC, exist_ok=True)
app.mount("/jobs", StaticFiles(directory=JOBS_DIR_STATIC), name="jobs")

# ✅ son yüklenen video job_id (RAM)
LAST_JOB_ID: Optional[str] = None
# ✅ aktif video context (job bazlı)
JOB_CONTEXT: dict = {}
# ✅ edit version history (job_id → [{version, command, file, duration, ts}])
EDIT_VERSIONS: Dict[str, list] = {}

# =========================================================
# ✅ Demo Token Auth
# =========================================================
def _get_demo_tokens() -> set[str]:
    """
    CLIPLA_DEMO_TOKENS env:
    clipla-looking-for-angel-investors,clipla-demo,...
    """
    raw = os.getenv("CLIPLA_DEMO_TOKENS", "")
    return {t.strip() for t in raw.split(",") if t.strip()}


def require_demo_token(request: Request) -> str:
    token = (request.query_params.get("token") or "").strip()
    if not token:
        raise HTTPException(status_code=403, detail="Demo erişimi için token gerekli.")
    if token not in _get_demo_tokens():
        raise HTTPException(status_code=403, detail="Geçersiz token.")
    return token


@app.get("/api/demo/verify", include_in_schema=False)
def demo_verify(request: Request):
    require_demo_token(request)
    return {"ok": True}


# =========================================================
# ✅ Demo Analytics (events) + Admin
# =========================================================
ANALYTICS_PATH = os.path.join(OUTPUT_DIR, "_analytics.jsonl")
ADMIN_PASSWORD = os.getenv("CLIPLA_ADMIN_PASSWORD", "clipla2026")


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def log_event(request: Request, event: str, extra: Optional[Dict[str, Any]] = None):
    """
    Events: page_view, upload, process, auto_edit_render, stt_transcribe
    JSONL: her satır 1 event. IP adresi KVKK/GDPR uyumu için hash'lenir.
    """
    try:
        raw_ip = _client_ip(request)
        row = {
            "ts":     datetime.now(timezone.utc).isoformat(),
            "event":  event,
            "ip_h":   hash_ip(raw_ip),   # ham IP saklanmıyor
            "ua":     request.headers.get("user-agent", "")[:200],
        }
        if extra:
            row.update(extra)
        with open(ANALYTICS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def require_admin(request: Request):
    """Admin auth: Authorization: Bearer <CLIPLA_ADMIN_PASSWORD>"""
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    expected = ADMIN_PASSWORD or "clipla2026"
    if not token or token != expected:
        raise HTTPException(
            status_code=403,
            detail="Forbidden",
            headers={"WWW-Authenticate": "Bearer"},
        )


@app.get("/admin", include_in_schema=False)
def admin_dashboard(request: Request):
    require_admin(request)

    total = 0
    unique_ips = set()
    by_event: Dict[str, int] = {}

    if os.path.exists(ANALYTICS_PATH):
        with open(ANALYTICS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    row = json.loads(line)
                    unique_ips.add(row.get("ip", ""))
                    ev = row.get("event", "unknown")
                    by_event[ev] = by_event.get(ev, 0) + 1
                except Exception:
                    continue

    items = "".join(
        [f"<li><b>{k}</b>: {v}</li>" for k, v in sorted(by_event.items(), key=lambda x: -x[1])]
    )

    html = f"""
    <html><head><meta charset="utf-8"><title>Clipla Admin</title></head>
    <body style="font-family:system-ui; padding:20px;">
      <h2>Clipla Demo Analytics</h2>
      <p><b>Total events:</b> {total}</p>
      <p><b>Unique IPs (approx):</b> {len([x for x in unique_ips if x])}</p>
      <h3>Events</h3>
      <ul>{items}</ul>
      <p style="opacity:.7">Not: IP bazlı unique sayım NAT/VPN nedeniyle yaklaşık olabilir.</p>
    </body></html>
    """
    return HTMLResponse(html)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


# =========================================================
# Demo Root "/"  (✅ tokenlı)
# =========================================================
@app.get("/", include_in_schema=False)
def serve_demo(request: Request):
    log_event(request, "page_view")

    index_path = os.path.join(STATIC_DIR, "index.html")
    if not os.path.exists(index_path):
        return JSONResponse(
            status_code=404,
            content={
                "error": "Demo index.html not found",
                "expected_path": index_path,
                "fix": r"Create: backend\static\index.html (real .html, not .txt)",
            },
        )
    return FileResponse(index_path)


# =========================================================
# Models
# =========================================================
class SpeedApplyItem(BaseModel):
    from_: float = Field(..., alias="from")
    to_: float = Field(..., alias="to")
    speed: float

    class Config:
        populate_by_name = True


class PunchApplyItem(BaseModel):
    from_: float = Field(..., alias="from")
    to_: float = Field(..., alias="to")
    zoom: float

    class Config:
        populate_by_name = True


class RenderV2Request(BaseModel):
    speed_apply: List[SpeedApplyItem] = []
    punch_apply: List[PunchApplyItem] = []


class AutoEditRequest(BaseModel):
    command_text: str = ""
    platform: Optional[str] = None
    target_duration_sec: Optional[int] = None
    transition: Optional[str] = None        # fade, fadeblack, fadewhite, slideleft, slideright, wipeleft, wiperight, dissolve, circleopen
    transition_duration: Optional[float] = 0.3


class ProcessRequest(BaseModel):
    command_text: str = ""
    target: Optional[str] = None
    preset: Optional[str] = None
    target_duration_sec: Optional[int] = None


# =========================================================
# Helpers (FFmpeg)
# =========================================================
def _run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _clean_tmp():
    if os.path.exists(TMP_DIR):
        for _ in range(6):
            try:
                shutil.rmtree(TMP_DIR)
                break
            except PermissionError:
                time.sleep(0.25)
            except Exception:
                break
    os.makedirs(TMP_DIR, exist_ok=True)


def get_duration(video_path: str) -> float:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path
    ]
    r = _run(cmd)
    try:
        return float((r.stdout or "").strip())
    except Exception:
        return 0.0
def get_or_create_job_context(job_id: str) -> dict:
    if job_id not in JOB_CONTEXT:
        JOB_CONTEXT[job_id] = {}
    ctx = JOB_CONTEXT[job_id]
    # RAM'de video_path yoksa filesystem'den bul
    if not ctx.get("video_path"):
        # Önce upload dizinini tara
        for d in [UPLOAD_DIR, os.path.join(UPLOAD_DIR, job_id)]:
            if os.path.isdir(d):
                for fname in os.listdir(d):
                    if fname.startswith(job_id) and fname.endswith(('.mp4', '.mov', '.avi', '.webm')):
                        ctx["video_path"] = os.path.join(d, fname)
                        break
            candidate = os.path.join(UPLOAD_DIR, f"{job_id}.mp4")
            if os.path.exists(candidate):
                ctx["video_path"] = candidate
                break
    return ctx


def _save_edit_version(job_id: str, command_text: str, output_file: str, duration: float):
    """Edit tamamlandığında versiyonu RAM + Supabase'e kaydet."""
    versions = EDIT_VERSIONS.setdefault(job_id, [])
    version_num = len(versions) + 1
    versions.append({
        "version":  version_num,
        "command":  command_text,
        "file":     output_file,
        "duration": round(duration, 1),
        "ts":       datetime.now(timezone.utc).isoformat(),
    })
    if len(versions) > 10:
        EDIT_VERSIONS[job_id] = versions[-10:]
    ctx = JOB_CONTEXT.setdefault(job_id, {})
    ctx.setdefault("applied_edits", []).append(command_text)
    if len(ctx["applied_edits"]) > 5:
        ctx["applied_edits"] = ctx["applied_edits"][-5:]
    # Supabase'e de yaz
    import asyncio
    try:
        asyncio.ensure_future(sb.save_edit_version(job_id, version_num, command_text, output_file, round(duration, 1)))
        asyncio.ensure_future(sb.save_last_edited_path(job_id, output_file))
    except Exception:
        pass


def remember_uploaded_video(job_id: str, file_path: str):
    ctx = get_or_create_job_context(job_id)
    ctx["uploaded"] = True
    ctx["video_path"] = file_path
    ctx["duration_sec"] = round(get_duration(file_path), 2)
    # Supabase'e kalıcı olarak da yaz (restart sonrası kaybolmaz)
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(sb.save_job_context(job_id, file_path, ctx))
        else:
            loop.run_until_complete(sb.save_job_context(job_id, file_path, ctx))
    except Exception:
        pass

def detect_silence_segments(video_path: str):
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-i", video_path,
        "-af", "silencedetect=noise=-30dB:d=0.5",
        "-f", "null",
        "-"
    ]
    r = _run(cmd)
    log = r.stderr or ""

    starts = [float(x) for x in re.findall(r"silence_start: ([0-9\\.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9\\.]+)", log)]

    segments = []
    total = 0.0
    for s, e in zip(starts, ends):
        dur = max(0.0, e - s)
        total += dur
        segments.append({"start": round(s, 2), "end": round(e, 2), "dur": round(dur, 2)})

    return segments, total


def build_cuts_from_silence(segments: List[Dict[str, float]]):
    cuts = []
    for seg in segments:
        if seg["dur"] >= 0.7:
            cuts.append({"from": seg["start"], "to": seg["end"], "reason": "silence"})
    return cuts[:300]


def build_keeps_from_cuts(cuts: List[Dict[str, Any]], duration: float):
    if duration <= 0:
        return []
    if not cuts:
        return [{"from": 0.0, "to": round(duration, 2)}]

    cuts_sorted = sorted(cuts, key=lambda x: float(x["from"]))
    keeps = []

    prev_end = 0.0
    for c in cuts_sorted:
        s = float(c["from"])
        e = float(c["to"])
        if s > prev_end:
            keeps.append({"from": round(prev_end, 2), "to": round(s, 2)})
        prev_end = max(prev_end, e)

    if duration > prev_end:
        keeps.append({"from": round(prev_end, 2), "to": round(duration, 2)})

    cleaned = []
    for k in keeps:
        if (k["to"] - k["from"]) >= 0.08:
            cleaned.append(k)
    return cleaned


VALID_TRANSITIONS = {
    'fade', 'fadeblack', 'fadewhite', 'fadegrays',
    'slideleft', 'slideright', 'slideup', 'slidedown',
    'wipeleft', 'wiperight', 'wipeup', 'wipedown',
    'dissolve', 'circleopen', 'circlecrop', 'pixelize',
}

def _get_file_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
        capture_output=True, text=True
    )
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def render_keep_segments(
    video_path: str,
    output_path: str,
    keeps: List[Dict[str, float]],
    transition: Optional[str] = None,
    transition_duration: float = 0.3,
):
    """
    Cut and concatenate keep segments with full re-encode for maximum compatibility.
    Handles videos with no audio, VFR, HEVC, and other edge cases.
    Supports xfade transitions between segments when transition is specified.
    """
    _clean_tmp()

    probe_r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", video_path],
        capture_output=True, text=True
    )
    src_fps = "30"
    src_asr = "44100"
    has_audio = False
    try:
        probe_data = json.loads(probe_r.stdout)
        for st in probe_data.get("streams", []):
            if st.get("codec_type") == "video":
                r = st.get("r_frame_rate", "30/1")
                try:
                    num, den = r.split("/")
                    fps_val = float(num) / float(den)
                    src_fps = str(min(round(fps_val), 60))
                except Exception:
                    src_fps = "30"
            if st.get("codec_type") == "audio":
                has_audio = True
                src_asr = st.get("sample_rate", "44100")
    except Exception:
        pass

    part_files = []
    for i, seg in enumerate(keeps):
        start = float(seg["from"])
        end = float(seg["to"])
        dur = max(0.0, end - start)
        if dur <= 0.08:
            continue

        part_path = os.path.join(TMP_DIR, f"part_{i}.mp4")

        cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-ss", str(start),
            "-i", video_path,
            "-t", str(dur),
            "-vf", f"fps={src_fps},setpts=PTS-STARTPTS",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "17",
            "-pix_fmt", "yuv420p",
            "-avoid_negative_ts", "make_zero",
            "-max_muxing_queue_size", "9999",
        ]
        if has_audio:
            cmd += [
                "-af", f"aresample={src_asr},asetpts=PTS-STARTPTS",
                "-c:a", "aac",
                "-b:a", "192k",
                "-ar", src_asr,
            ]
        else:
            cmd += ["-an"]
        cmd.append(part_path)

        _run(cmd)

        if os.path.exists(part_path) and os.path.getsize(part_path) > 0:
            part_files.append(part_path)

    if not part_files:
        return False, "No segments to render"

    if len(part_files) == 1:
        shutil.copy2(part_files[0], output_path)
        return True, None

    n = len(part_files)
    input_args = []
    for p in part_files:
        input_args += ["-i", p]

    # xfade transitions between segments
    use_transition = transition and transition in VALID_TRANSITIONS and n >= 2
    td = max(0.1, min(float(transition_duration), 1.0))

    if use_transition:
        # Build chained xfade filter_complex
        # Collect segment durations for offset calculation
        seg_durations = [_get_file_duration(p) for p in part_files]

        fc_parts = []
        # Video xfade chain
        cumulative = seg_durations[0]
        prev_v = "[0:v]"
        for i in range(1, n):
            offset = max(0.01, cumulative - td)
            tag = f"[vx{i}]"
            fc_parts.append(f"{prev_v}[{i}:v]xfade=transition={transition}:duration={td}:offset={offset}{tag}")
            prev_v = tag
            cumulative += seg_durations[i] - td
        fc_parts[-1] = fc_parts[-1].rsplit("[", 1)[0] + "[vout]"

        if has_audio:
            # Audio acrossfade chain
            prev_a = "[0:a]"
            for i in range(1, n):
                tag = f"[ax{i}]"
                fc_parts.append(f"{prev_a}[{i}:a]acrossfade=d={td}{tag}")
                prev_a = tag
            fc_parts[-1] = fc_parts[-1].rsplit("[", 1)[0] + "[aout]"
            map_args = ["-map", "[vout]", "-map", "[aout]",
                        "-c:a", "aac", "-b:a", "192k", "-ar", src_asr]
        else:
            map_args = ["-map", "[vout]", "-an"]

        filter_complex = ";".join(fc_parts)
    else:
        if has_audio:
            fc_parts = "".join([f"[{j}:v][{j}:a]" for j in range(n)])
            filter_complex = f"{fc_parts}concat=n={n}:v=1:a=1[vout][aout]"
            map_args = ["-map", "[vout]", "-map", "[aout]",
                        "-c:a", "aac", "-b:a", "192k", "-ar", src_asr]
        else:
            fc_parts = "".join([f"[{j}:v]" for j in range(n)])
            filter_complex = f"{fc_parts}concat=n={n}:v=1:a=0[vout]"
            map_args = ["-map", "[vout]", "-an"]

    cmd_concat = [
        "ffmpeg", "-hide_banner", "-y",
    ] + input_args + [
        "-filter_complex", filter_complex,
    ] + map_args + [
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "17",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-max_muxing_queue_size", "9999",
        output_path
    ]
    r_concat = _run(cmd_concat)

    ok = os.path.exists(output_path) and os.path.getsize(output_path) > 0
    return ok, None if ok else f"Concat failed: {(r_concat.stderr or '')[-300:]}"


def _apply_aspect_ratio(input_path: str, output_path: str, platform: str) -> bool:
    """Orijinal video yönünü KORUR. Boyut çift sayıysa stream copy, değilse minimal re-encode."""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", input_path],
            capture_output=True, text=True
        )
        info = json.loads(probe.stdout)
        w, h = 0, 0
        for s in info.get("streams", []):
            if s.get("codec_type") == "video":
                w = int(s.get("width", 0))
                h = int(s.get("height", 0))
                break
        if not w or not h:
            shutil.copy2(input_path, output_path)
            return True

        if w % 2 == 0 and h % 2 == 0:
            shutil.copy2(input_path, output_path)
            return True

        nw = w if w % 2 == 0 else w - 1
        nh = h if h % 2 == 0 else h - 1
        cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-i", input_path,
            "-vf", f"scale={nw}:{nh}",
            "-c:v", "libx264", "-preset", "slow", "-crf", "17",
            "-pix_fmt", "yuv420p", "-c:a", "copy",
            "-movflags", "+faststart", output_path
        ]
        subprocess.run(cmd, capture_output=True, text=True)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception:
        shutil.copy2(input_path, output_path)
        return True


def _speed_up_video(input_path: str, output_path: str, factor: float = 1.5) -> bool:
    """Videoyu hızlandırır (ses + video birlikte)."""
    try:
        cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-i", input_path,
            "-vf", f"setpts={1/factor:.4f}*PTS",
            "-af", f"atempo={min(factor,2.0):.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", output_path
        ]
        subprocess.run(cmd, capture_output=True, text=True)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception:
        return False


def _normalize_audio(input_path: str, output_path: str) -> bool:
    """Ses seviyesini normalize eder (loudnorm filtresi)."""
    try:
        cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-i", input_path,
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", output_path
        ]
        subprocess.run(cmd, capture_output=True, text=True)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception:
        return False


def _normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _extract_duration_sec(text: str, fallback: int = 60) -> int:
    t = _normalize_text(text)

    m = re.search(r"(\d+)\s*(sn|saniye|sec|seconds|s)\b", t)
    if m:
        return max(5, int(m.group(1)))

    m = re.search(r"(\d+)\s*(dk|dakika|min|minutes)\b", t)
    if m:
        return max(5, int(m.group(1)) * 60)

    return max(5, int(fallback or 60))


def _extract_duration_sec_or_none(text: str) -> Optional[int]:
    """Komutta süre varsa döner, yoksa None — 60 saniye zorlaması yok."""
    t = _normalize_text(text or "")

    m = re.search(r"(\d+)\s*(sn|saniye|sec|seconds|s)\b", t)
    if m:
        return max(5, int(m.group(1)))

    m = re.search(r"(\d+)\s*(dk|dakika|min|minutes)\b", t)
    if m:
        return max(5, int(m.group(1)) * 60)

    return None


def _pick_keeps_v15(
    keeps_list: List[Dict[str, float]],
    duration: float,
    target_seconds: float,
    intro_sec: float = 10.0,
    outro_sec: float = 15.0
):
    if not keeps_list or duration <= 0:
        return keeps_list

    if target_seconds <= (intro_sec + outro_sec):
        intro = {"from": 0.0, "to": round(min(intro_sec, duration), 2)}
        outro_start = max(0.0, duration - outro_sec)
        outro = {"from": round(outro_start, 2), "to": round(duration, 2)}
        return [intro, outro] if outro["from"] > intro["to"] else [intro]

    intro = {"from": 0.0, "to": round(min(intro_sec, duration), 2)}
    outro_start = max(0.0, duration - outro_sec)
    outro = {"from": round(outro_start, 2), "to": round(duration, 2)}

    middle_target = max(0.0, target_seconds - intro_sec - outro_sec)

    middle_keeps = []
    for k in keeps_list:
        s = float(k["from"])
        e = float(k["to"])
        dur = e - s
        if e <= intro["to"] or s >= outro["from"]:
            continue
        if dur >= 6.0:
            middle_keeps.append({"from": s, "to": e})

    middle_keeps = sorted(middle_keeps, key=lambda k: (k["to"] - k["from"]), reverse=True)

    picked_middle = []
    remaining = float(middle_target)

    for k in middle_keeps:
        s, e = k["from"], k["to"]
        dur = e - s
        if remaining <= 0:
            break

        if dur <= remaining:
            picked_middle.append({"from": round(s, 2), "to": round(e, 2)})
            remaining -= dur
        else:
            cut_to = s + remaining
            if (cut_to - s) >= 4.0:
                picked_middle.append({"from": round(s, 2), "to": round(cut_to, 2)})
            remaining = 0
            break

    picked_middle = sorted(picked_middle, key=lambda k: float(k["from"]))

    result = [intro] + picked_middle + [outro]

    cleaned = []
    last_to = -1.0
    for seg in result:
        s = float(seg["from"])
        e = float(seg["to"])
        if e <= s:
            continue
        if s < last_to:
            s = last_to
        if e - s >= 0.08:
            cleaned.append({"from": round(s, 2), "to": round(e, 2)})
            last_to = e

    return cleaned


# =========================================================
# ✅ Music helpers
# =========================================================
def _user_wants_music(command_text: str) -> bool:
    """Komutta müzik isteği var mı? Opt-in — açıkça istenmemişse False."""
    t = (command_text or "").lower()
    music_keywords = [
        "müzik", "muzik", "music", "müzikli", "muzikli",
        "arka plan müzik", "background music", "ses ekle",
        "enerjik", "energetic", "ritmik", "rhythmic",
        "yavaş müzik", "slow music", "calm music", "sakin müzik",
    ]
    return any(k in t for k in music_keywords)


def _guess_music_tag(text: str) -> str:
    """Komuttan müzik ruh halini tahmin et."""
    if not text:
        return "neutral"

    t = text.lower().strip()

    if any(x in t for x in ["enerjik", "hareketli", "hızlı", "upbeat", "dinamik"]):
        return "energetic"

    if any(x in t for x in ["duygusal", "slow", "romantik", "yavaş", "soft", "melankolik"]):
        return "emotional"

    if any(x in t for x in ["dark", "gerilim", "korku", "karanlık", "dramatik", "tense"]):
        return "dark"

    if any(x in t for x in ["komik", "eğlenceli", "fun", "playful", "neşeli"]):
        return "fun"

    return "neutral"


def _pick_music_file(music_dir: str, tag: str) -> str:
    """
    music/<tag>/ içinden ilk mp3'ü seçer.
    """
    folder = os.path.join(music_dir, tag)
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            if name.lower().endswith(".mp3"):
                return os.path.join(folder, name)

    legacy = os.path.join(music_dir, f"{tag}.mp3")
    return legacy


def _mix_music_ffmpeg(video_in: str, music_mp3: str, video_out: str) -> None:
    """
    Voice normalize + background music + ducking.
    """
    if not os.path.exists(music_mp3):
        raise FileNotFoundError(f"Müzik bulunamadı: {music_mp3}")

    af = (
        "[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[a0];"
        "[1:a]volume=0.55[a1];"
        "[a1][a0]sidechaincompress=threshold=0.03:ratio=10:attack=5:release=250[bg];"
        "[a0][bg]amix=inputs=2:duration=first:dropout_transition=2[mix]"
    )

    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-i", video_in,
        "-stream_loop", "-1",
        "-i", music_mp3,
        "-filter_complex", af,
        "-map", "0:v:0",
        "-map", "[mix]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        video_out
    ]
    r = _run(cmd)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "")[-1200:]
        raise RuntimeError(f"ffmpeg mix failed: {tail}")


# =========================================================
# STT Konfigürasyon
#
# NOT: Claude Messages API ses dosyası (audio/wav, mp3 vb.) desteklemiyor.
# Bu nedenle STT için Whisper kullanılır.
# Claude'un rolü: Whisper çıktısını post-process etmek
#   (filler temizleme, noktalama düzeltme, okunabilirlik artırma).
#
# CLAUDE_ENHANCE=true  → Whisper sonrası Claude iyileştirme (varsayılan)
# CLAUDE_ENHANCE=false → Ham Whisper çıktısı
# =========================================================
CLAUDE_ENHANCE_MODEL = os.environ.get("CLAUDE_ENHANCE_MODEL", "claude-haiku-4-5-20251001")
CLAUDE_ENHANCE       = os.environ.get("CLAUDE_ENHANCE", "true").lower() == "true"


def _claude_enhance_transcript(raw_text: str, language: str = "tr") -> Optional[str]:
    """
    Whisper'dan gelen ham transkripti Claude ile iyileştirir:
    - Türkçe filler kelimeleri kaldırır (ıı, şey, yani, hani, ee, mm)
    - Noktalama ekler / düzeltir
    - Tekrar eden kelimeleri temizler
    - Konuşma dilini okunabilir yazıya çevirir

    Başarısız olursa None döner → çağıran ham metni kullanır.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key or not raw_text.strip():
        return None
    try:
        lang_name = "Türkçe" if language.startswith("tr") else "İngilizce"
        prompt = (
            f"Aşağıdaki {lang_name} konuşma transkriptini düzenle:\n\n"
            "Kurallar:\n"
            "- Filler kelimeleri kaldır: ıı, mm, ee, şey, yani, hani, işte, öyle\n"
            "- Tekrar eden kelimeleri tek bırak\n"
            "- Eksik noktalama işaretlerini ekle\n"
            "- Anlamı ve içeriği değiştirme, sadece temizle\n"
            "- Yalnızca düzenlenmiş metni döndür, açıklama ekleme\n\n"
            f"Transkript:\n{raw_text}"
        )

        resp = _get_sync_client().messages.create(
            model=CLAUDE_ENHANCE_MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        enhanced = resp.content[0].text.strip()
        return enhanced if enhanced else None
    except Exception as e:
        logger.warning(f"[claude_enhance] hata: {e}")
        return None


# =========================================================
# Whisper (Local) + cache + AI segments
# =========================================================
WHISPER_MODEL_NAME = os.environ.get("WHISPER_MODEL", "small")
_whisper_model: Optional[WhisperModel] = None


def _get_whisper_model() -> WhisperModel:
    global _whisper_model
    if _whisper_model is None:
        _whisper_model = WhisperModel(
            WHISPER_MODEL_NAME,
            device="cpu",
            compute_type="int8"
        )
    return _whisper_model


def _transcript_cache_path(job_id: str) -> str:
    return os.path.join(OUTPUT_DIR, f"{job_id}_transcript.json")


def _transcribe_video_whisper(video_path: str) -> Dict[str, Any]:
    """Whisper ile video transkript eder. Fallback yolu."""
    model = _get_whisper_model()
    segments, info = model.transcribe(
        video_path,
        vad_filter=True,
        language="tr",
        task="transcribe",
        beam_size=5,
    )
    items = []
    full_text_parts = []
    for s in segments:
        text = (s.text or "").strip()
        if text:
            items.append({
                "start": round(float(s.start), 2),
                "end":   round(float(s.end), 2),
                "text":  text,
            })
            full_text_parts.append(text)
    return {
        "model":    WHISPER_MODEL_NAME,
        "language": getattr(info, "language", None),
        "duration": getattr(info, "duration", None),
        "text":     " ".join(full_text_parts).strip(),
        "segments": items[:500],
    }


def transcribe_whisper_cached(job_id: str, video_path: str) -> Dict[str, Any]:
    """
    Video transkripsiyonu — önbellekli.
    Pipeline: Whisper (STT) → Claude (transcript enhancement, opsiyonel)

    CLAUDE_ENHANCE=true  → Whisper çıktısını Claude ile iyileştir
    CLAUDE_ENHANCE=false → Ham Whisper çıktısı
    """
    cache_path = _transcript_cache_path(job_id)
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # ── 1. Whisper STT ───────────────────────────────────────────
    logger.info(f"[stt] Whisper transkripsiyon başlıyor: {job_id}")
    data = _transcribe_video_whisper(video_path)

    # ── 2. Claude iyileştirme (opsiyonel) ───────────────────────
    if CLAUDE_ENHANCE and data.get("text"):
        lang = data.get("language") or "tr"
        enhanced = _claude_enhance_transcript(data["text"], language=lang)
        if enhanced:
            logger.info(f"[stt] Claude iyileştirme uygulandı: {job_id}")
            data["text_raw"] = data["text"]          # ham Whisper metnini sakla
            data["text"] = enhanced                  # Claude iyileştirmesini kullan
            data["enhanced_by"] = CLAUDE_ENHANCE_MODEL

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return data


def pick_hook_middle_closing(transcript_text: str) -> Dict[str, Any]:
    t = (transcript_text or "").strip()
    if not t:
        return {
            "hook": {"text": "", "reason": "no transcript"},
            "middle": [],
            "closing": {"text": "", "reason": "no transcript"},
        }

    sents = [x.strip() for x in re.split(r"(?<=[\.\!\?])\s+", t) if x.strip()]
    if len(sents) == 1:
        return {
            "hook": {"text": sents[0], "reason": "single sentence"},
            "middle": [],
            "closing": {"text": sents[0], "reason": "single sentence"},
        }

    hook = " ".join(sents[:2]) if len(sents) >= 2 else sents[0]
    closing = sents[-1]

    middle_candidates = sents[2:-1] if len(sents) > 3 else sents[1:-1]
    scored = sorted([(len(x), x) for x in middle_candidates], key=lambda z: z[0], reverse=True)
    picked = [x for _, x in scored[:4]]
    picked_set = set(picked)
    middle = [x for x in middle_candidates if x in picked_set]

    return {
        "hook": {"text": hook, "reason": "first strong sentences"},
        "middle": [{"text": m, "reason": "long/high-signal sentence"} for m in middle],
        "closing": {"text": closing, "reason": "last sentence closing"},
    }


def _preview(text: str, n: int = 320) -> str:
    s = (text or "").strip()
    if len(s) <= n:
        return s
    return s[:n] + "…"


# =========================================================
# ✅ STT (Local) - upload audio/webm -> wav -> faster_whisper
# =========================================================
@app.post("/api/stt/transcribe")
@limiter.limit("30/minute")
async def stt_transcribe(request: Request, file: UploadFile = File(...)):
    log_event(request, "stt_transcribe", {"filename": (file.filename or "")[:80]})

    # Whitelist'ten güvenli uzantı al (path traversal önlemi)
    suffix = sanitize_audio_suffix(file.filename)
    tmp_in = None
    tmp_wav = None
    tmp_wav_boost = None

    def ffprobe_duration(path: str) -> float:
        try:
            rr = _run([
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=nokey=1:noprint_wrappers=1",
                path
            ])
            return float((rr.stdout or "").strip() or "0")
        except Exception:
            return 0.0

    def transcribe_path(wav_path: str) -> tuple[str, str, Optional[str]]:
        """Kısa sesli komutları Whisper ile metne çevirir."""
        model = _get_whisper_model()
        segments, info = model.transcribe(
            wav_path,
            vad_filter=True,
            language="tr",
            task="transcribe",
            beam_size=5
        )
        parts = []
        for s in segments:
            t = (s.text or "").strip()
            if t:
                parts.append(t)
        text = " ".join(parts).strip()
        lang = getattr(info, "language", None)
        detected = (lang or "tr")
        return text, "tr", detected

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
            tmp_in = f.name
            f.write(await file.read())

        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as f2:
            tmp_wav = f2.name

        cmd1 = [
            "ffmpeg", "-hide_banner", "-y",
            "-i", tmp_in,
            "-ac", "1",
            "-ar", "16000",
            "-vn",
            tmp_wav
        ]
        r1 = _run(cmd1)

        if r1.returncode != 0 or (not os.path.exists(tmp_wav)) or os.path.getsize(tmp_wav) == 0:
            raise HTTPException(status_code=500, detail=f"ffmpeg raw wav failed: {(r1.stderr or r1.stdout or '')[-600:]}")

        dur = ffprobe_duration(tmp_wav)
        text, forced_lang, detected_lang = transcribe_path(tmp_wav)

        had_boost = False
        if (not text) and dur >= 0.6:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as f3:
                tmp_wav_boost = f3.name

            cmd2 = [
                "ffmpeg", "-hide_banner", "-y",
                "-i", tmp_wav,
                "-af", "volume=6.0,loudnorm",
                "-ac", "1",
                "-ar", "16000",
                tmp_wav_boost
            ]
            r2 = _run(cmd2)

            if r2.returncode == 0 and os.path.exists(tmp_wav_boost) and os.path.getsize(tmp_wav_boost) > 0:
                had_boost = True
                text2, forced_lang2, detected_lang2 = transcribe_path(tmp_wav_boost)
                if text2:
                    text, forced_lang, detected_lang = text2, forced_lang2, detected_lang2

        return {
            "text": text,
            "language": forced_lang,
            "model": WHISPER_MODEL_NAME,
            "debug": {
                "detected_lang": detected_lang,
                "in_bytes": os.path.getsize(tmp_in) if tmp_in and os.path.exists(tmp_in) else None,
                "wav_bytes": os.path.getsize(tmp_wav) if tmp_wav and os.path.exists(tmp_wav) else None,
                "wav_duration": dur,
                "ffmpeg_rc": r1.returncode,
                "had_boost_retry": had_boost,
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"stt failed: {str(e)}")
    finally:
        for p in [tmp_in, tmp_wav, tmp_wav_boost]:
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


# =========================================================
# ✅ Beta Analytics - User Session & Feedback
# =========================================================
SESSIONS_PATH = os.path.join(OUTPUT_DIR, "_sessions.jsonl")
FEEDBACK_PATH = os.path.join(OUTPUT_DIR, "_feedback.jsonl")


def log_session(user_id: str, event: str, extra: Optional[Dict[str, Any]] = None):
    try:
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "user_id": user_id,
            "event": event,
        }
        if extra:
            row.update(extra)
        with open(SESSIONS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


@app.post("/api/beta/session")
def beta_session(request: Request):
    user_id = str(uuid.uuid4())
    log_session(user_id, "session_start", {
        "ip": _client_ip(request),
        "ua": request.headers.get("user-agent", "")[:200],
    })
    return {"user_id": user_id}


@app.post("/api/beta/feedback")
async def beta_feedback(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}

    user_id = (body.get("user_id") or "")[:64]
    job_id = (body.get("job_id") or "")[:64]
    rating = body.get("rating")
    comment = (body.get("comment") or "")[:500]

    if rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="rating must be 'up' or 'down'")

    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "job_id": job_id,
        "rating": rating,
        "comment": comment,
        "ip": _client_ip(request),
    }
    try:
        with open(FEEDBACK_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass

    return {"ok": True}


@app.get("/api/beta/stats")
def beta_stats(request: Request):
    require_admin(request)

    sessions, uploads, renders = 0, 0, 0
    unique_users = set()
    platforms: Dict[str, int] = {}
    up_votes, down_votes = 0, 0

    if os.path.exists(SESSIONS_PATH):
        with open(SESSIONS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line.strip())
                    if r.get("event") == "session_start":
                        sessions += 1
                        unique_users.add(r.get("user_id", ""))
                except Exception:
                    pass

    if os.path.exists(ANALYTICS_PATH):
        with open(ANALYTICS_PATH, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line.strip())
                    ev = r.get("event", "")
                    if ev == "upload":
                        uploads += 1
                    elif ev == "auto_edit_render":
                        renders += 1
                        p = r.get("platform", "unknown") or "unknown"
                        platforms[p] = platforms.get(p, 0) + 1
                except Exception:
                    pass

    if os.path.exists(FEEDBACK_PATH):
        with open(FEEDBACK_PATH, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line.strip())
                    if r.get("rating") == "up":
                        up_votes += 1
                    elif r.get("rating") == "down":
                        down_votes += 1
                except Exception:
                    pass

    total_feedback = up_votes + down_votes
    satisfaction = round(up_votes / total_feedback * 100) if total_feedback > 0 else 0

    return {
        "sessions": sessions,
        "unique_users": len(unique_users),
        "uploads": uploads,
        "renders": renders,
        "platforms": platforms,
        "feedback": {
            "up": up_votes,
            "down": down_votes,
            "total": total_feedback,
            "satisfaction_pct": satisfaction,
        }
    }


# =========================================================
# Endpoints
# =========================================================
@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


@app.get("/api/video/health")
def video_health_check():
    return {"status": "ok"}


@app.get("/api/video/last-job")
def last_job():
    if not LAST_JOB_ID:
        raise HTTPException(status_code=404, detail="henüz video yüklenmedi")
    return {"job_id": LAST_JOB_ID}

@app.post("/api/video/upload")
@limiter.limit("10/minute")
async def upload_video(request: Request, file: UploadFile = File(...)):
    global LAST_JOB_ID

    # 1. Sunucu tarafı boyut limiti (akışlı okuma — RAM'e tam yüklenmez)
    data = await read_with_size_limit(file)

    # 2. Dosya türü doğrulama (magic bytes)
    if not is_valid_video_magic(data[:12]):
        raise HTTPException(
            status_code=415,
            detail="Geçersiz dosya türü. Lütfen bir video dosyası yükleyin (MP4, MOV, MKV, WebM, AVI).",
        )

    job_id = str(uuid.uuid4())
    log_event(request, "upload", {"job_id": job_id})

    file_path = os.path.join(UPLOAD_DIR, f"{job_id}.mp4")
    with open(file_path, "wb") as f:
        f.write(data)

    # 3. Job sahipliği kaydet (API key → job_id)
    claim_job(job_id, request)

    LAST_JOB_ID = job_id
    remember_uploaded_video(job_id, file_path)

    ctx = JOB_CONTEXT.get(job_id, {})
    duration = ctx.get("duration_sec", 0)
    await sb.create_job(job_id, file.filename or f"{job_id}.mp4", duration)
    await sb.log_event("upload", job_id, {"filename": file.filename})

    return {
        "job_id": job_id,
        "duration_sec": duration,
    }


@app.post("/api/analyze/{job_id}")
@limiter.limit("5/minute")
async def analyze_video(job_id: str, request: Request):
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="AI servisi aktif değil")

    ctx = JOB_CONTEXT.get(job_id, {})
    video_path = ctx.get("video_path")
    if not video_path or not os.path.exists(video_path):
        raise HTTPException(status_code=404, detail="Video bulunamadı")

    duration = ctx.get("duration_sec") or 30.0

    # 3 keyframe çıkar (video'nun %20, %50, %80'inde)
    frames_b64 = []
    for pct in [0.20, 0.50, 0.80]:
        t = max(0.5, float(duration) * pct)
        tmp_fd, frame_path = tempfile.mkstemp(suffix=".jpg")
        os.close(tmp_fd)
        try:
            subprocess.run([
                "ffmpeg", "-hide_banner", "-y",
                "-ss", str(t), "-i", video_path,
                "-frames:v", "1", "-q:v", "4",
                "-vf", "scale=640:-1",
                frame_path,
            ], capture_output=True, timeout=15)
            if os.path.exists(frame_path) and os.path.getsize(frame_path) > 0:
                with open(frame_path, "rb") as f:
                    frames_b64.append(base64.b64encode(f.read()).decode())
        except Exception:
            pass
        finally:
            if os.path.exists(frame_path):
                os.unlink(frame_path)

    # Claude Vision mesajı
    content = []
    for b64 in frames_b64:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
        })

    content.append({
        "type": "text",
        "text": (
            f"Video süresi: {duration:.0f}s. "
            "Görüntüleri analiz et ve SADECE geçerli JSON döndür, başka hiçbir şey yazma:\n"
            '{"scene_type":"komedi|vlog|tutorial|müzik|aksiyon|seyahat|belgesel|spor|diğer",'
            '"mood":"neşeli|dramatik|sakin|enerjik|duygusal|komik|profesyonel",'
            '"has_speech":true,'
            '"summary":"Videonun ne hakkında olduğu (Türkçe, 1 cümle)",'
            '"recommendations":['
            '{"type":"sfx|music|subtitle|effect|filter|enhance|speed|bgremove",'
            '"label":"Kısa açıklama (Türkçe, max 5 kelime)",'
            '"reason":"Neden öneriyorum (Türkçe, 1 cümle)",'
            '"params":{},'
            '"priority":1}'
            ']}\n'
            "Maksimum 4 öneri, öncelik sırasıyla. Sahneye uygun, spesifik öneriler ver."
        ),
    })

    try:
        r = await _get_async_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=900,
            messages=[{"role": "user", "content": content}],
        )
        raw = r.content[0].text.strip()
        # markdown kod bloğunu temizle
        if "```" in raw:
            raw = raw.split("```")[1].lstrip("json").strip().rstrip("`").strip()

        analysis = json.loads(raw)
        JOB_CONTEXT[job_id]["analysis"] = analysis

        return {"job_id": job_id, "frames_analyzed": len(frames_b64), **analysis}

    except Exception as e:
        logger.error(f"[analyze] hata: {e}")
        # Fallback — analiz başarısız olsa da temel öneriler döndür
        fallback = {
            "scene_type": "diğer",
            "mood": "nötr",
            "has_speech": True,
            "summary": "Video yüklendi.",
            "recommendations": [
                {"type": "subtitle", "label": "Otomatik altyazı", "reason": "Altyazı erişilebilirliği artırır.", "params": {}, "priority": 1},
                {"type": "enhance",  "label": "Ses kalitesi",     "reason": "Gürültü azaltma netliği artırır.", "params": {}, "priority": 2},
            ],
        }
        JOB_CONTEXT[job_id]["analysis"] = fallback
        return {"job_id": job_id, "frames_analyzed": len(frames_b64), **fallback}


@app.post("/api/video/process/{job_id}")
def process_video(
    request: Request,
    job_id: str,
    body: ProcessRequest = ProcessRequest(),
    platform: Optional[str] = Query(default=None, description="youtube / shorts / reels")
):
    log_event(request, "process", {"job_id": job_id})

    video_path = os.path.join(UPLOAD_DIR, f"{job_id}.mp4")
    if not os.path.exists(video_path):
        raise HTTPException(status_code=404, detail="video bulunamadı")

    cmd = (body.command_text or "").strip()
    edit_plan = None
    tag_cut_silence = False
    target_sec = None

    if cmd:
        ep = create_edit_plan(CommandBody(
            command_text=cmd,
            target=body.target or "youtube",
            preset=body.preset or "vertical_short",
            target_duration_sec=body.target_duration_sec or 60
        ))
        edit_plan = ep
        notes = ep.notes or []
        tag_cut_silence = any("tag:cut_silence=true" in n for n in notes)
        target_sec = getattr(ep, "target_duration_sec", None)

    duration = get_duration(video_path)
    segments, total_silence = detect_silence_segments(video_path)

    cuts = build_cuts_from_silence(segments) if tag_cut_silence else []

    if cuts:
        keeps = build_keeps_from_cuts(cuts, duration)
    else:
        keeps = [{"from": 0.0, "to": round(duration, 2)}] if duration > 0 else []

    if target_sec and duration > 0 and target_sec < duration:
        keeps = _pick_keeps_v15(
            keeps_list=keeps,
            duration=duration,
            target_seconds=float(target_sec),
            intro_sec=10.0,
            outro_sec=15.0
        )

    if total_silence < 2:
        score = 90
    elif total_silence < 8:
        score = 70
    else:
        score = 50

    tr = transcribe_whisper_cached(job_id, video_path)
    seg = pick_hook_middle_closing(tr.get("text", ""))

    payload = {
        "algorithm_score": score,
        "insights": {
            "message": "analiz üretildi",
            "silence_seconds": round(total_silence, 2),
            "silence_segments_preview": segments[:20],
            "silence_segments_count": len(segments),
            "tip": f"{len(cuts)} sessiz kesim önerisi var." if cuts else "sessiz kesim uygulanmadı (komut tag yok).",
            "job_id": job_id,
            "platform": platform
        },
        "edit_plan": edit_plan.model_dump() if edit_plan else {"version": "edit_plan_v1", "cuts": cuts},
        "command_used": bool(cmd),
        "command_text": cmd,
        "command_tags": {
            "cut_silence": tag_cut_silence,
            "target_duration_sec": target_sec
        },
        "transcript_preview": _preview(tr.get("text", "")),
        "segments": seg,
        "keeps_used": keeps,
        "cuts_used": cuts,
    }

    p = os.path.join(OUTPUT_DIR, f"{job_id}_analysis.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return payload


@app.post("/api/video/render/{job_id}")
def render_v1(job_id: str):
    video_path = os.path.join(UPLOAD_DIR, f"{job_id}.mp4")
    if not os.path.exists(video_path):
        raise HTTPException(status_code=404, detail="video bulunamadı")

    duration = get_duration(video_path)
    if duration <= 0:
        raise HTTPException(status_code=400, detail="video süresi okunamadı")

    segments, _ = detect_silence_segments(video_path)
    cuts = build_cuts_from_silence(segments)
    keeps = build_keeps_from_cuts(cuts, duration)

    out_name = f"{job_id}_cut.mp4"
    output_path = os.path.join(OUTPUT_DIR, out_name)

    ok, err = render_keep_segments(video_path, output_path, keeps)
    if not ok:
        raise HTTPException(status_code=500, detail=f"render failed: {err}")

    return {
        "message": "render ok",
        "job_id": job_id,
        "output_file": out_name,
        "download_url": f"/outputs/{out_name}",
        "cuts_applied": len(cuts),
        "kept_segments": len(keeps)
    }


# =========================================================
# ✅ AUTO-EDIT (MUSIC INCLUDED)
# =========================================================
@app.post("/api/auto-edit/{job_id}")
async def auto_edit_v16(request: Request, job_id: str, body: AutoEditRequest):
    verify_job_owner(job_id, request)
    log_event(request, "auto_edit_render", {"job_id": job_id})

    JOBS_DIR_PATH = os.path.join(BASE_DIR, "jobs")
    video_path = None
    job_dir_path = os.path.join(JOBS_DIR_PATH, job_id)
    if os.path.exists(job_dir_path):
        for fname in os.listdir(job_dir_path):
            if fname.lower().endswith((".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v")):
                video_path = os.path.join(job_dir_path, fname)
                break

    if not video_path or not os.path.exists(video_path):
        old_path = os.path.join(UPLOAD_DIR, f"{job_id}.mp4")
        if os.path.exists(old_path):
            video_path = old_path

    if not video_path or not os.path.exists(video_path):
        raise HTTPException(status_code=404, detail=f"video bulunamadı: {job_id}")

    target_sec = body.target_duration_sec
    claude_result = None

    if body.command_text:
        try:
            from clipla_api.command import _call_claude_api
            duration_probe = get_duration(video_path)
            claude_result = _call_claude_api(body.command_text, duration_probe, None)
        except Exception:
            pass

    if target_sec is None and claude_result and claude_result.get("target_duration_sec"):
        target_sec = int(claude_result["target_duration_sec"])
    if target_sec is None:
        target_sec = _extract_duration_sec_or_none(body.command_text)

    logger.debug(f"[auto_edit] command_text={body.command_text!r} target_sec={target_sec}")

    duration = get_duration(video_path)
    segments, total_silence = detect_silence_segments(video_path)
    cuts = build_cuts_from_silence(segments)
    keeps = build_keeps_from_cuts(cuts, duration)

    if duration > 0 and target_sec and target_sec < duration:
        keeps = _pick_keeps_v15(
            keeps_list=keeps,
            duration=duration,
            target_seconds=float(target_sec),
            intro_sec=10.0,
            outro_sec=15.0
        )

    if total_silence < 2:
        score = 90
    elif total_silence < 8:
        score = 70
    else:
        score = 50

    out_name = f"{job_id}_auto.mp4"
    output_path = os.path.join(OUTPUT_DIR, out_name)

    ok, err = render_keep_segments(
        video_path, output_path, keeps,
        transition=body.transition,
        transition_duration=body.transition_duration or 0.3,
    )
    if not ok:
        raise HTTPException(status_code=500, detail=f"auto-edit render failed: {err}")

    platform = (body.platform or "youtube").lower()
    aspect_out_name = f"{job_id}_auto_aspect.mp4"
    aspect_out_path = os.path.join(OUTPUT_DIR, aspect_out_name)
    aspect_ok = _apply_aspect_ratio(output_path, aspect_out_path, platform)
    if aspect_ok and os.path.exists(aspect_out_path):
        output_path = aspect_out_path
        out_name = aspect_out_name

    cmd_text = _normalize_text(body.command_text or "")
    do_speed_up = False
    do_normalize = False

    if claude_result:
        do_speed_up = bool(claude_result.get("speed_up"))
        do_normalize = bool(claude_result.get("normalize_audio"))
    else:
        do_speed_up = any(k in cmd_text for k in ["hızlandır", "hızlı", "speed up"])
        do_normalize = any(k in cmd_text for k in ["normalize", "ses düzelt", "ses seviye", "audio fix"])

    if do_speed_up:
        spd_name = f"{job_id}_spd.mp4"
        spd_path = os.path.join(OUTPUT_DIR, spd_name)
        if _speed_up_video(output_path, spd_path, 1.5):
            output_path = spd_path
            out_name = spd_name

    if do_normalize:
        norm_name = f"{job_id}_norm.mp4"
        norm_path = os.path.join(OUTPUT_DIR, norm_name)
        if _normalize_audio(output_path, norm_path):
            output_path = norm_path
            out_name = norm_name

    # 2) Music mix — SADECE kullanıcı isterse
    music_tag = None
    music_file = None
    music_out_name = f"{job_id}_auto_music.mp4"
    music_out_path = os.path.join(OUTPUT_DIR, music_out_name)
    music_applied = False
    music_err = None

    if _user_wants_music(body.command_text):
        music_tag = _guess_music_tag(body.command_text)
        music_file = _pick_music_file(MUSIC_DIR, music_tag)
        try:
            _mix_music_ffmpeg(output_path, music_file, music_out_path)
            music_applied = True
        except Exception as e:
            music_err = str(e)

    final_download_url = f"/outputs/{out_name}"
    final_output_file = out_name
    if music_applied:
        final_download_url = f"/outputs/{music_out_name}"
        final_output_file = music_out_name

    tr = transcribe_whisper_cached(job_id, video_path)
    seg = pick_hook_middle_closing(tr.get("text", ""))

    # Persist transcript to Supabase (only when freshly produced)
    if tr.get("text"):
        await sb.save_transcript(
            job_id=job_id,
            model=tr.get("model", WHISPER_MODEL_NAME),
            language=tr.get("language") or "tr",
            full_text=tr["text"],
            segments=tr.get("segments", []),
        )

    output_duration = get_duration(os.path.join(OUTPUT_DIR, final_output_file))
    duration_score = 100
    if output_duration > 0 and duration > 0:
        ratio = output_duration / duration
        if ratio < 0.3:
            duration_score = 60
        elif ratio < 0.6:
            duration_score = 80
        else:
            duration_score = 95
    final_score = int((score + duration_score) / 2)

    result_payload = {
        "message": "auto-edit v1.6 ok",
        "job_id": job_id,
        "algorithm_score": final_score,
        "duration": {
            "input": round(duration, 1),
            "output": round(output_duration, 1),
        },
        "download_url": final_download_url,
        "output_url": f"{PUBLIC_BASE_URL}{final_download_url}",
        "output_file": final_output_file,
        "platform": platform,
        "edit_plan": {
            "cuts_applied": len(cuts),
            "keeps_used": len(keeps),
            "keeps": keeps,
        },
        "scores": {
            "silence_score": score,
            "duration_score": duration_score,
            "info_density": final_score,
            "pacing_score": min(100, final_score + 5),
        },
        "music": {
            "applied": music_applied,
            "tag": music_tag,
            "picked_file": os.path.basename(music_file) if music_file else None,
            "download_url": f"/outputs/{music_out_name}" if music_applied else None,
            "error": music_err,
        },
        "whisper": {"model": tr.get("model"), "language": tr.get("language")},
        "transcript_preview": _preview(tr.get("text", "")),
        "segments": seg,
    }

    await sb.save_edit_results(job_id, None, result_payload)
    await sb.log_event("auto_edit_done", job_id, {
        "platform": platform,
        "score": final_score,
        "output_duration": round(output_duration, 1),
    })

    _save_edit_version(job_id, body.command_text or "", final_output_file, output_duration)

    return result_payload


@app.post("/api/auto-edit")
async def auto_edit_last(request: Request, body: AutoEditRequest):
    # job_id body'de varsa onu kullan (multi-user güvenli)
    job_id = getattr(body, "job_id", None) or LAST_JOB_ID
    if not job_id:
        raise HTTPException(
            status_code=400,
            detail="job_id gerekli. Önce /api/video/upload kullan."
        )
    return await auto_edit_v16(request, job_id, body)
# =========================================================
# 🧠 SIMPLE MEMORY (job_id bazlı)
# =========================================================
CHAT_MEMORY: dict = {}

def get_memory(job_id: str):
    if job_id not in CHAT_MEMORY:
        CHAT_MEMORY[job_id] = []
    return CHAT_MEMORY[job_id]

def add_memory(job_id: str, role: str, content: str):
    mem = get_memory(job_id)
    mem.append({"role": role, "content": content})

    # max 10 mesaj tut
    if len(mem) > 10:
        CHAT_MEMORY[job_id] = mem[-10:]
# =========================================================
# ✅ CHAT ENDPOINT
# =========================================================
class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: List[ChatMessage] = []
    job_id: Optional[str] = None
    language: Optional[str] = "tr-TR"


def _chat_extract_platform(texts: List[str]) -> str:
    """Mesaj geçmişinden platform çıkar."""
    for text in reversed(texts):
        t = text.lower()
        if "tiktok" in t: return "tiktok"
        if "instagram" in t or "reels" in t or "reel" in t: return "instagram"
        if "youtube shorts" in t or "youtube_shorts" in t: return "youtube_shorts"
        if "youtube" in t: return "youtube"
        if "shorts" in t or "kısa" in t: return "youtube_shorts"
    return "youtube_shorts"


def _chat_extract_duration(texts: List[str]) -> Optional[int]:
    """Mesaj geçmişinden hedef süre çıkar (saniye)."""
    for text in reversed(texts):
        t = text.lower()
        m = re.search(r'(\d+)\s*(saniye|sn|secs?|seconds?)', t)
        if m: return int(m.group(1))
        m = re.search(r'(\d+)\s*(dakika|dk|mins?|minutes?)', t)
        if m: return int(m.group(1)) * 60
        if "bir dakika" in t: return 60
        if "iki dakika" in t: return 120
        if "yarım dakika" in t: return 30
    return None


def _chat_build_command(history: List[ChatMessage], last_message: str) -> str:
    """Sohbet geçmişinden edit komutunu birleştir."""
    user_msgs = [m.content for m in history if m.role == "user"]
    user_msgs.append(last_message)
    # Son 5 kullanıcı mesajını birleştir (komut birikimi)
    relevant = [m for m in user_msgs[-5:] if m.strip()]
    return " | ".join(relevant)


@app.post("/api/chat")
@limiter.limit("60/minute")
async def chat(request: Request, body: ChatRequest):
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

    if not ANTHROPIC_API_KEY:
        return {
            "reply": "ANTHROPIC_API_KEY eksik.",
            "ready_to_edit": False,
            "edit_params": None,
        }

    # job_id yoksa son yüklenen videoyu kullan
    job_id = body.job_id or LAST_JOB_ID or "global"

    # geçmiş memory al
    memory = get_memory(job_id)

    # aktif video context al
    job_ctx = JOB_CONTEXT.get(job_id, {})
    uploaded = job_ctx.get("uploaded", False)
    duration_sec = job_ctx.get("duration_sec")
    applied_edits = job_ctx.get("applied_edits", [])

    video_context_text = ""
    if uploaded:
        video_context_text = (
            f"\nAktif video zaten yüklü.\n"
            f"Video süresi: {duration_sec} saniye.\n"
            f"Kullanıcıya tekrar 'videoyu yükle' veya 'video süresi kaç' diye sorma.\n"
            f"Eğer kullanıcı düzenleme istiyorsa, mevcut video üzerinden cevap ver.\n"
        )
    if applied_edits:
        edits_str = " → ".join(applied_edits[-3:])
        video_context_text += f"\nDaha önce uygulanan editler: {edits_str}\n"

    # Dil bazlı yönerge
    lang_hint = "Türkçe cevap ver." if (body.language or "tr").startswith("tr") else "Reply in English."

    system = f"""
Sen Clipla'nın AI video edit asistanısın.

Kurallar:
- {lang_hint}
- Kullanıcıyla doğal konuş
- Kısa ve net cevap ver (max 2-3 cümle)
- Aynı video üzerinde çalıştığını hatırla
- Önceki mesajları dikkate al
- Kullanıcıdan zaten bildiğin bilgileri tekrar sorma
- Video zaten yüklüyse tekrar upload isteme
- Video süresi biliniyorsa tekrar sorma
- Kullanıcı düzenleme istiyorsa doğrudan yardımcı ol
- Kullanıcı edit yapmaya hazırsa yanıtının SONUNA (yeni satırda) tam olarak READY_TO_EDIT yaz
- ASLA emoji kullanma — yanıtlar sesli okunur, emoji sesi bozar
- ASLA markdown kullanma (**, *, #, ---) — düz metin yaz
- Rakam yerine kelime kullan: "yüzde elli" (50% değil), "iki dakika" (2 dk değil)
- Türkçe, İngilizce veya karışık dil komutlarını anlayabilirsin
- Yanıtının EN SONUNA (boş satırdan sonra) 2 kısa öneri ekle: SUGGESTIONS: öneri1 | öneri2

{video_context_text}
"""

    messages = []
    for m in memory:
        messages.append({"role": m["role"], "content": m["content"]})
    for m in body.history[-6:]:  # Frontend geçmişinden son 6 mesaj (memory ile çakışmayı azalt)
        if not any(x["content"] == m.content for x in messages):
            messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": body.message})

    try:
        r = await _get_async_client().messages.create(
            model="claude-haiku-4-5",
            max_tokens=512,
            system=system,
            messages=messages,
        )

        reply_text = r.content[0].text

        ready = "READY_TO_EDIT" in reply_text
        clean_reply = reply_text.replace("READY_TO_EDIT", "").strip()

        # Önerileri ayır
        suggestions: list[str] = []
        if "SUGGESTIONS:" in clean_reply:
            parts = clean_reply.split("SUGGESTIONS:", 1)
            clean_reply = parts[0].strip()
            raw_suggestions = parts[1].strip()
            suggestions = [s.strip() for s in raw_suggestions.split("|") if s.strip()][:3]

        add_memory(job_id, "user", body.message)
        add_memory(job_id, "assistant", clean_reply)
        await sb.log_event("chat", job_id, {"ready": ready})

        edit_params = None
        if ready:
            # Tüm konuşma metninden platform ve süre çıkar
            all_texts = [m.content for m in body.history] + [body.message]
            platform      = _chat_extract_platform(all_texts)
            target_dur    = _chat_extract_duration(all_texts)
            command_text  = _chat_build_command(body.history, body.message)
            edit_params = {
                "command_text":        command_text,
                "platform":            platform,
                "target_duration_sec": target_dur,
                "job_id":              job_id,
            }

        return {
            "reply":          clean_reply,
            "ready_to_edit":  ready,
            "edit_params":    edit_params,
            "suggestions":    suggestions,
        }

    except Exception as e:
        logger.error(f"[chat] endpoint hatası: {e}")
        return {
            "reply": "Şu an yanıt veremiyorum. Lütfen birkaç saniye sonra tekrar deneyin.",
            "ready_to_edit": False,
            "edit_params": None,
        }

# =========================================================
# 💬 STREAMING CHAT ENDPOINT
# =========================================================

@app.post("/api/chat/stream")
@limiter.limit("60/minute")
async def chat_stream(request: Request, body: ChatRequest):
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

    if not ANTHROPIC_API_KEY:
        async def _no_key():
            yield f"data: {json.dumps({'done': True, 'reply': 'ANTHROPIC_API_KEY eksik.', 'ready_to_edit': False, 'edit_params': None})}\n\n"
        return StreamingResponse(_no_key(), media_type="text/event-stream")

    job_id = body.job_id or LAST_JOB_ID or "global"
    memory = get_memory(job_id)
    job_ctx = JOB_CONTEXT.get(job_id, {})
    uploaded = job_ctx.get("uploaded", False)
    duration_sec = job_ctx.get("duration_sec")

    video_context_text = ""
    if uploaded:
        video_context_text = (
            f"\nAktif video zaten yüklü.\n"
            f"Video süresi: {duration_sec} saniye.\n"
            f"Kullanıcıya tekrar 'videoyu yükle' veya 'video süresi kaç' diye sorma.\n"
            f"Eğer kullanıcı düzenleme istiyorsa, mevcut video üzerinden cevap ver.\n"
        )

    lang_hint = "Türkçe cevap ver." if (body.language or "tr").startswith("tr") else "Reply in English."

    system = f"""
Sen Clipla'nın AI video edit asistanısın.

Kurallar:
- {lang_hint}
- Kullanıcıyla doğal konuş
- Kısa ve net cevap ver (max 2-3 cümle)
- Aynı video üzerinde çalıştığını hatırla
- Önceki mesajları dikkate al
- Kullanıcıdan zaten bildiğin bilgileri tekrar sorma
- Video zaten yüklüyse tekrar upload isteme
- Video süresi biliniyorsa tekrar sorma
- Kullanıcı düzenleme istiyorsa doğrudan yardımcı ol
- Kullanıcı edit yapmaya hazırsa yanıtının SONUNA (yeni satırda) tam olarak READY_TO_EDIT yaz
- ASLA emoji kullanma — yanıtlar sesli okunur, emoji sesi bozar
- ASLA markdown kullanma (**, *, #, ---) — düz metin yaz
- Rakam yerine kelime kullan: "yüzde elli" (50% değil), "iki dakika" (2 dk değil)
- Türkçe, İngilizce veya karışık dil komutlarını anlayabilirsin
- Yanıtının EN SONUNA (boş satırdan sonra) 2 kısa öneri ekle: SUGGESTIONS: öneri1 | öneri2

{video_context_text}
"""

    messages = []
    for m in memory:
        messages.append({"role": m["role"], "content": m["content"]})
    for m in body.history[-6:]:
        if not any(x["content"] == m.content for x in messages):
            messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": body.message})

    async def generate():
        full_reply = ""
        try:
            async with _get_async_client().messages.stream(
                model="claude-haiku-4-5",
                max_tokens=512,
                system=system,
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    full_reply += text
                    yield f"data: {json.dumps({'text': text})}\n\n"

            ready = "READY_TO_EDIT" in full_reply
            clean_reply = full_reply.replace("READY_TO_EDIT", "").strip()

            # Önerileri ayır
            suggestions_list: list = []
            if "SUGGESTIONS:" in clean_reply:
                parts = clean_reply.split("SUGGESTIONS:", 1)
                clean_reply = parts[0].strip()
                suggestions_list = [s.strip() for s in parts[1].split("|") if s.strip()][:3]

            add_memory(job_id, "user", body.message)
            add_memory(job_id, "assistant", clean_reply)
            await sb.log_event("chat", job_id, {"ready": ready})

            edit_params = None
            if ready:
                all_texts = [m.content for m in body.history] + [body.message]
                platform     = _chat_extract_platform(all_texts)
                target_dur   = _chat_extract_duration(all_texts)
                command_text = _chat_build_command(body.history, body.message)
                edit_params = {
                    "command_text":        command_text,
                    "platform":            platform,
                    "target_duration_sec": target_dur,
                    "job_id":              job_id,
                }

            yield f"data: {json.dumps({'done': True, 'reply': clean_reply, 'ready_to_edit': ready, 'edit_params': edit_params, 'suggestions': suggestions_list})}\n\n"
        except Exception as e:
            logger.error(f"[chat/stream] hata: {e}")
            yield f"data: {json.dumps({'done': True, 'reply': 'Şu an yanıt veremiyorum. Lütfen tekrar deneyin.', 'ready_to_edit': False, 'edit_params': None})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# =========================================================
# 🎬 EFFECTS ENDPOINT
# =========================================================

# Görsel FFmpeg filtreleri
EFFECT_FILTERS = {
    "impact_shock":    "eq=contrast=1.5:brightness=0.05:saturation=1.8,unsharp=5:5:1.5",
    "comedy_reaction": "eq=saturation=2.0:brightness=0.1,hue=h=20",
    "tension_build":   "eq=contrast=1.3:brightness=-0.05:saturation=0.7,vignette=PI/4",
    "dream_sequence":  "gblur=sigma=3,eq=brightness=0.1:saturation=0.5",
    "epic_reveal":     "eq=contrast=1.4:brightness=0.08:saturation=1.6,unsharp=3:3:1.0",
    "neon_glow":       "hue=h=180,eq=contrast=1.2:brightness=0.15:saturation=2.5",
    "vintage_film":    "colorchannelmixer=0.393:0.769:0.189:0:0.349:0.686:0.168:0:0.272:0.534:0.131,noise=alls=10:allf=t",
    "impact_emphasis": "eq=contrast=1.3:brightness=0.03:saturation=1.5,unsharp=3:3:0.8",
    "epic_moment":     "eq=contrast=1.4:brightness=0.06:saturation=1.7,unsharp=5:5:1.2",
    "fail_tone":       "eq=contrast=0.9:brightness=-0.05:saturation=0.6",
    "crowd_laugh":     "eq=saturation=1.8:brightness=0.08,hue=h=10",
}

# Ses efektleri — FFmpeg lavfi sinyal üreteci ile (harici dosya gerektirmez)
# intensity (0.0-1.0) → volume değerine çevrilir
EFFECT_AUDIO: dict[str, str] = {
    # Şok / darbe: kısa, yüksek frekanslı noise burst
    "impact_shock":    "sine=frequency=60:duration=0.3,volume={vol}",
    # Komedi tepkisi: alçalan frekans sweep (klasik boing hissi)
    "comedy_reaction": "sine=frequency=800:duration=0.4[a];sine=frequency=200:duration=0.4[b];[a][b]amix=inputs=2,volume={vol}",
    # Gerilim: alçak frekanslı drone
    "tension_build":   "sine=frequency=55:duration=2.0,volume={vol}",
    # Epik: yükselen frekans sweep
    "epic_reveal":     "sine=frequency=200:duration=0.8[a];sine=frequency=600:duration=0.8[b];[a][b]amix=inputs=2,volume={vol}",
    "epic_moment":     "sine=frequency=150:duration=1.0[a];sine=frequency=450:duration=1.0[b];[a][b]amix=inputs=2,volume={vol}",
    # Vurgu: kısa darbeli ses
    "impact_emphasis": "sine=frequency=100:duration=0.2,volume={vol}",
    # Başarısızlık: alçalan frekans (sad trombone simülasyonu)
    "fail_tone":       "sine=frequency=400:duration=0.3[a];sine=frequency=200:duration=0.5[b];[a][b]concat=n=2:v=0:a=1,volume={vol}",
    # Kahkaha/alkış: beyaz gürültü patlaması (crowd simülasyonu)
    "crowd_laugh":     "anoisesrc=color=white:duration=1.5:amplitude=0.3,volume={vol}",
}


def _mix_audio_effect(source_path: str, out_path: str, category: str, intensity: float, timestamp: Optional[float]) -> bool:
    """
    FFmpeg lavfi ses efektini videoya amix ile karıştırır.
    timestamp=None → videonun başına ekle
    timestamp=N    → N. saniyeden itibaren efekti başlat
    """
    audio_template = EFFECT_AUDIO.get(category)
    if not audio_template:
        return False  # Ses efekti tanımsız, sessizce atla

    vol = round(max(0.1, min(intensity * 1.5, 2.0)), 2)
    audio_filter = audio_template.replace("{vol}", str(vol))

    # timestamp varsa adelay ile kaydır
    delay_ms = int((timestamp or 0) * 1000)
    if delay_ms > 0:
        audio_filter = f"{audio_filter},adelay={delay_ms}|{delay_ms}"

    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-i", source_path,
        "-f", "lavfi", "-i", audio_filter,
        "-filter_complex",
        "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=2[aout]",
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0

class EffectRequest(BaseModel):
    category: str
    intensity: float = 0.8
    timestamp: Optional[float] = None

# _get_user_plan → services/security.py get_plan_from_request() ile değiştirildi
# API key üzerinden plan belirlenir, kolayca atlatılamaz


@app.post("/api/effects/{job_id}")
async def apply_effect(request: Request, job_id: str, body: EffectRequest):
    verify_job_owner(job_id, request)
    # Plan kontrolü — efektler Pro özelliği
    user_plan = get_plan_from_request(request)
    if user_plan != "pro":
        raise HTTPException(
            status_code=403,
            detail="Efekt uygulama Pro plan gerektirir. Clipla-Y Pro'ya geçin.",
        )
    log_event(request, "apply_effect", {"job_id": job_id, "category": body.category})

    # Find latest output video for this job
    output_candidates = []
    for fname in os.listdir(OUTPUT_DIR):
        if fname.startswith(job_id) and fname.endswith(".mp4"):
            fpath = os.path.join(OUTPUT_DIR, fname)
            output_candidates.append((os.path.getmtime(fpath), fpath, fname))

    if not output_candidates:
        # Fall back to uploaded video
        job_dir = os.path.join(BASE_DIR, "jobs", job_id)
        if os.path.exists(job_dir):
            for fname in os.listdir(job_dir):
                if fname.lower().endswith((".mp4", ".mov", ".avi", ".mkv", ".webm")):
                    source_path = os.path.join(job_dir, fname)
                    break
            else:
                raise HTTPException(status_code=404, detail="Video bulunamadı")
        else:
            raise HTTPException(status_code=404, detail=f"Job bulunamadı: {job_id}")
    else:
        output_candidates.sort(reverse=True)
        source_path = output_candidates[0][1]

    vf_filter = EFFECT_FILTERS.get(body.category, "")
    if not vf_filter:
        raise HTTPException(status_code=400, detail=f"Bilinmeyen efekt: {body.category}")

    # ── 1. Görsel filtre uygula ──────────────────────────────────
    out_name = f"{job_id}_fx_{body.category}.mp4"
    out_path = os.path.join(OUTPUT_DIR, out_name)

    cmd = [
        "ffmpeg", "-y", "-i", source_path,
        "-vf", vf_filter,
        "-c:a", "copy",
        "-movflags", "+faststart",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Efekt uygulanamadı: {result.stderr[-300:]}")

    # ── 2. Ses efekti karıştır (varsa) ──────────────────────────
    audio_out_name = f"{job_id}_fx_{body.category}_audio.mp4"
    audio_out_path = os.path.join(OUTPUT_DIR, audio_out_name)
    audio_mixed = _mix_audio_effect(out_path, audio_out_path, body.category, body.intensity, body.timestamp)
    if audio_mixed:
        final_name = audio_out_name
        final_path = audio_out_path
        logger.info(f"[effects] Ses efekti karıştırıldı: {body.category}")
    else:
        final_name = out_name
        final_path = out_path

    download_url = f"/outputs/{final_name}"
    output_url = f"{PUBLIC_BASE_URL}{download_url}"

    await sb.save_effect(job_id, body.category, body.intensity, output_url, body.timestamp)
    await sb.log_event("effect_applied", job_id, {"category": body.category})

    return {
        "status": "ok",
        "category": body.category,
        "intensity": body.intensity,
        "download_url": download_url,
        "output_url": output_url,
    }

# =========================================================
# ⏱ AUTO-EDIT STATUS (frontend polling için)
# =========================================================
# Her adım için ara dosya varlığına bakarak gerçek ilerleme hesaplar:
#   0%  → hiçbir şey yok   (yeni başladı)
#  25%  → _auto.mp4 var    (ham render tamam)
#  55%  → _auto_aspect.mp4 (aspect ratio uygulandı)
#  75%  → _spd / _norm     (hız/ses normalize)
# 100%  → tamamlandı

_EDIT_STEPS: list[tuple[str, int, str]] = [
    ("{job_id}_auto_music.mp4", 100, "Müzik eklendi"),
    ("{job_id}_norm.mp4",        90,  "Ses normalize edildi"),
    ("{job_id}_spd.mp4",         80,  "Hız ayarlandı"),
    ("{job_id}_auto_aspect.mp4", 55,  "Platform formatı uygulandı"),
    ("{job_id}_auto.mp4",        25,  "Ham render tamamlandı"),
]

@app.get("/api/auto-edit/{job_id}/status")
def auto_edit_status(job_id: str, request: Request):
    verify_job_owner(job_id, request)
    # En son / en gelişmiş çıktı dosyasını ara
    for template, progress, step in _EDIT_STEPS:
        candidate = template.replace("{job_id}", job_id)
        fpath = os.path.join(OUTPUT_DIR, candidate)
        if os.path.exists(fpath):
            download_url = f"/outputs/{candidate}"
            # Müzikli veya son adım ise done
            is_done = progress == 100 or template in [
                "{job_id}_auto_music.mp4",
                "{job_id}_auto_aspect.mp4",
                "{job_id}_norm.mp4",
                "{job_id}_spd.mp4",
                "{job_id}_auto.mp4",
            ]
            # Son çıktıyı bul — en yüksek progress'li tamamlanmış dosya
            # done: en gelişmiş çıktı dosyası zaten teslim edilmeli
            if progress == 25:
                # Sadece ham render var, daha gelişmiş olmayabilir, done kabul et
                return {
                    "job_id":       job_id,
                    "status":       "done",
                    "progress":     100,
                    "step":         "Tamamlandı",
                    "download_url": download_url,
                    "output_url":   f"{PUBLIC_BASE_URL}{download_url}",
                }
            return {
                "job_id":       job_id,
                "status":       "done",
                "progress":     100,
                "step":         "Tamamlandı",
                "download_url": download_url,
                "output_url":   f"{PUBLIC_BASE_URL}{download_url}",
            }

    # Transcript dosyası varsa analiz aşamasındayız
    transcript_path = os.path.join(OUTPUT_DIR, f"{job_id}_transcript.json")
    if os.path.exists(transcript_path):
        return {"job_id": job_id, "status": "processing", "progress": 40, "step": "Ses analiz ediliyor"}

    # Video yüklendi, işlem henüz başlamadı veya ilk aşamada
    upload_path = os.path.join(UPLOAD_DIR, f"{job_id}.mp4")
    job_dir_path = os.path.join(BASE_DIR, "jobs", job_id)
    if os.path.exists(upload_path) or os.path.isdir(job_dir_path):
        return {"job_id": job_id, "status": "processing", "progress": 10, "step": "İşlem başlatılıyor"}

    return {"job_id": job_id, "status": "processing", "progress": 5, "step": "Sıraya alındı"}


# =========================================================
# 🔊 NEURAL TTS (edge-tts — Microsoft tr-TR-EmelNeural)
# =========================================================

class TTSRequest(BaseModel):
    text: str = Field(..., max_length=1000)
    voice: str = "tr-TR-EmelNeural"   # veya tr-TR-AhmetNeural
    rate: str = "+0%"                  # "+0%" normal, "-10%" daha yavaş
    volume: str = "+0%"

@app.post("/api/tts")
@limiter.limit("60/minute")
async def neural_tts(request: Request, body: TTSRequest):
    """edge-tts ile Microsoft Neural sesi üret, MP3 stream döndür."""
    try:
        import edge_tts
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="edge-tts kurulu değil. Backend'de: pip install edge-tts"
        )
    # Metin temizle — emoji ve markdown kaldır
    import re as _re
    clean = _re.sub(r'[^\w\s.,!?;:\'"()-]', '', body.text or '').strip()
    if not clean:
        raise HTTPException(status_code=422, detail="Boş metin")

    communicate = edge_tts.Communicate(clean, body.voice, rate=body.rate, volume=body.volume)
    audio_chunks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.append(chunk["data"])

    if not audio_chunks:
        raise HTTPException(status_code=500, detail="TTS ses üretilemedi")

    audio_data = b"".join(audio_chunks)
    return Response(content=audio_data, media_type="audio/mpeg")


# =========================================================
# ⏪ UNDO / EDIT HISTORY
# =========================================================

@app.get("/api/edit-history/{job_id}")
def get_edit_history(job_id: str, request: Request):
    """Edit versiyonlarını döndür."""
    verify_job_owner(job_id, request)
    versions = EDIT_VERSIONS.get(job_id, [])
    return {"job_id": job_id, "version_count": len(versions), "versions": versions}


@app.post("/api/undo/{job_id}")
def undo_edit(job_id: str, request: Request):
    """Son edit'i geri al — bir önceki versiyona dön."""
    verify_job_owner(job_id, request)
    versions = EDIT_VERSIONS.get(job_id, [])
    if len(versions) < 2:
        raise HTTPException(status_code=400, detail="Geri alınacak önceki versiyon yok.")
    versions.pop()  # Son versiyonu çıkar
    prev = versions[-1]
    prev_path = os.path.join(OUTPUT_DIR, prev["file"])
    if not os.path.exists(prev_path):
        raise HTTPException(status_code=404, detail="Önceki versiyon dosyası bulunamadı.")
    return {
        "job_id":       job_id,
        "version":      prev["version"],
        "command":      prev["command"],
        "duration":     prev["duration"],
        "download_url": f"/outputs/{prev['file']}",
        "output_url":   f"{PUBLIC_BASE_URL}/outputs/{prev['file']}",
        "versions_remaining": len(versions),
    }


# =========================================================
# 🔍 VIDEO PREVIEW DESCRIPTION
# =========================================================

@app.get("/api/preview/{job_id}")
def get_video_preview(job_id: str, request: Request):
    """Mevcut video durumunu sesli okumaya uygun metin olarak döndür."""
    verify_job_owner(job_id, request)
    versions = EDIT_VERSIONS.get(job_id, [])
    job_ctx = JOB_CONTEXT.get(job_id, {})
    applied = job_ctx.get("applied_edits", [])
    duration = job_ctx.get("duration_sec")

    if not versions:
        description = "Henüz herhangi bir düzenleme yapılmadı."
    else:
        last = versions[-1]
        ver_num = last["version"]
        out_dur = last["duration"]
        cmd = last["command"] or "Komut belirtilmedi"
        description = (
            f"Versiyon {ver_num}. "
            f"Son komut: {cmd}. "
            f"Çıktı süresi: {out_dur} saniye. "
        )
        if len(versions) > 1:
            description += f"Toplam {len(versions)} versiyon var, geri alabilirsin."

    return {
        "job_id":      job_id,
        "description": description,
        "versions":    len(versions),
        "applied_edits": applied,
        "current_duration": duration,
    }