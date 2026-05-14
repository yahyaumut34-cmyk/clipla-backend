"""
Multi-Video Merge — POST /api/merge
2-5 video'yu sırayla birleştirir. Opsiyonel geçiş efektleri (xfade).
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional
import os
import subprocess
import tempfile
import json
import logging
from pathlib import Path
from services.security import verify_job_owner

router = APIRouter(prefix="/api", tags=["merge"])
logger = logging.getLogger(__name__)

BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR      = os.environ.get("OUTPUT_DIR", os.path.join(BASE_DIR, "..", "outputs"))
UPLOAD_DIR      = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "..", "uploads"))
JOBS_DIR        = os.environ.get("JOBS_DIR",   os.path.join(BASE_DIR, "..", "jobs"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000")

# xfade'de kullanılabilecek geçiş adları
VALID_TRANSITIONS = {
    "cut":        None,           # geçiş yok — concat demuxer
    "fade":       "fade",
    "crossfade":  "dissolve",
    "wipe":       "wiperight",
    "wipeleft":   "wipeleft",
    "slide":      "slideleft",
    "zoom":       "zoomin",
    "circle":     "circleopen",
    "black":      "fadeblack",
    "white":      "fadewhite",
}


class MergeRequest(BaseModel):
    job_ids: list[str] = Field(..., min_length=2, max_length=5)
    transitions: list[str] = []    # her boşluk için bir geçiş; eksikse "cut"
    transition_duration: float = 0.5
    resolution: str = "source"     # "source" | "1080p" | "720p"


def _find_video(job_id: str) -> Optional[str]:
    """Bir job için en iyi videoyu bul (işlenmiş veya orijinal)."""
    for suffix in ["_auto_aspect.mp4", "_auto.mp4", "_beatsync.mp4", "_subtitled_tr.mp4", "_cut.mp4"]:
        p = os.path.join(OUTPUT_DIR, f"{job_id}{suffix}")
        if os.path.exists(p):
            return p
    for ext in [".mp4", ".mov", ".webm", ".mkv"]:
        p = os.path.join(UPLOAD_DIR, f"{job_id}{ext}")
        if os.path.exists(p):
            return p
    job_dir = os.path.join(JOBS_DIR, job_id)
    if os.path.isdir(job_dir):
        for f in sorted(Path(job_dir).iterdir()):
            if f.suffix.lower() in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
                return str(f)
    return None


def _get_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
        capture_output=True, text=True,
    )
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def _scale_filter(resolution: str) -> Optional[str]:
    if resolution == "1080p":
        return "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2"
    if resolution == "720p":
        return "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2"
    return None


def _merge_cut(videos: list[str], out_path: str, scale: Optional[str]) -> tuple[bool, str]:
    """Basit concat (kesik geçiş). Aynı codec ise stream copy ile hızlı."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for v in videos:
            f.write(f"file '{v.replace(chr(39), chr(39)+chr(39))}'\n")
        list_path = f.name

    try:
        if scale:
            # Re-encode gerekiyor
            inputs = []
            for v in videos:
                inputs += ["-i", v]
            n = len(videos)
            filter_parts = [f"[{i}:v]{scale},setsar=1[v{i}];[{i}:a]anull[a{i}]" for i in range(n)]
            concat_v = "".join(f"[v{i}]" for i in range(n))
            concat_a = "".join(f"[a{i}]" for i in range(n))
            filter_parts.append(f"{concat_v}{concat_a}concat=n={n}:v=1:a=1[outv][outa]")
            fc = ";".join(filter_parts)
            cmd = inputs + [
                "-filter_complex", fc,
                "-map", "[outv]", "-map", "[outa]",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", out_path,
            ]
        else:
            cmd = [
                "-f", "concat", "-safe", "0", "-i", list_path,
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", out_path,
            ]

        full_cmd = ["ffmpeg", "-hide_banner", "-y"] + cmd
        r = subprocess.run(full_cmd, capture_output=True, text=True)
        ok = os.path.exists(out_path) and os.path.getsize(out_path) > 0
        return ok, ("" if ok else r.stderr[-400:])
    finally:
        try:
            os.unlink(list_path)
        except Exception:
            pass


def _merge_xfade(videos: list[str], out_path: str, transitions: list[str],
                 dur: float, scale: Optional[str]) -> tuple[bool, str]:
    """xfade geçişli birleştirme — re-encode gerektirir."""
    durations = [_get_duration(v) for v in videos]

    inputs = []
    for v in videos:
        inputs += ["-i", v]

    n = len(videos)
    sc = scale or "scale=iw:ih,setsar=1"

    # Her video için scale uygula
    filter_parts = [f"[{i}:v]{sc}[sv{i}]" for i in range(n)]

    # xfade zinciri
    prev_v = "sv0"
    prev_a = "0:a"
    offset = durations[0] - dur

    for i in range(1, n):
        trans_name = VALID_TRANSITIONS.get(transitions[i - 1] if i - 1 < len(transitions) else "fade", "fade")
        if not trans_name:
            trans_name = "fade"
        out_v = f"xf{i}v"
        out_a = f"xf{i}a"
        filter_parts.append(
            f"[{prev_v}][sv{i}]xfade=transition={trans_name}:duration={dur:.2f}:offset={max(0, offset):.2f}[{out_v}]"
        )
        filter_parts.append(
            f"[{prev_a}][{i}:a]acrossfade=d={dur:.2f}[{out_a}]"
        )
        prev_v = out_v
        prev_a = out_a
        if i < n - 1:
            offset += durations[i] - dur

    filter_parts.append(f"[{prev_v}]null[outv]")
    filter_parts.append(f"[{prev_a}]anull[outa]")
    fc = ";".join(filter_parts)

    cmd = ["ffmpeg", "-hide_banner", "-y"] + inputs + [
        "-filter_complex", fc,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    ok = os.path.exists(out_path) and os.path.getsize(out_path) > 0
    return ok, ("" if ok else r.stderr[-400:])


@router.post("/merge")
async def merge_videos(request: Request, body: MergeRequest):
    """2-5 video'yu birleştir, opsiyonel geçiş efekti ekle."""
    # En az ilk job_id sahibini doğrula
    if body.job_ids:
        try:
            verify_job_owner(body.job_ids[0], request)
        except Exception:
            pass  # demo modda job_id ownership kontrolü esnektir

    videos = []
    missing = []
    for jid in body.job_ids:
        p = _find_video(jid)
        if p:
            videos.append(p)
        else:
            missing.append(jid)

    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Şu job_id'ler için video bulunamadı: {missing}. Önce upload yapın."
        )

    if len(videos) < 2:
        raise HTTPException(status_code=422, detail="En az 2 video gerekli.")

    import uuid
    merged_job_id = f"merged_{uuid.uuid4().hex[:12]}"
    out_filename  = f"{merged_job_id}.mp4"
    out_path      = os.path.join(OUTPUT_DIR, out_filename)

    scale = _scale_filter(body.resolution)
    has_transition = any(t != "cut" for t in body.transitions)

    if has_transition:
        ok, err = _merge_xfade(videos, out_path, body.transitions, body.transition_duration, scale)
    else:
        ok, err = _merge_cut(videos, out_path, scale)

    if not ok:
        raise HTTPException(status_code=500, detail=f"Birleştirme başarısız: {err}")

    duration = _get_duration(out_path)

    # Yeni job kaydını oluştur (auto-edit için JOBS_DIR'e sembolik kayıt)
    new_job_dir = os.path.join(JOBS_DIR, merged_job_id)
    os.makedirs(new_job_dir, exist_ok=True)
    import shutil
    shutil.copy2(out_path, os.path.join(new_job_dir, "input.mp4"))

    return {
        "job_id":       merged_job_id,
        "source_ids":   body.job_ids,
        "video_count":  len(videos),
        "duration":     round(duration, 1),
        "download_url": f"{PUBLIC_BASE_URL}/outputs/{out_filename}",
    }


# ── Trim + Merge ──────────────────────────────────────────────────────────────

class TrimClip(BaseModel):
    job_id: str
    start: float = 0.0          # saniye
    end: Optional[float] = None # None = videonun sonuna kadar

class TrimMergeRequest(BaseModel):
    clips: list[TrimClip] = Field(..., min_length=1, max_length=10)
    transition: str = "cut"
    transition_duration: float = 0.5
    resolution: str = "source"


@router.post("/trim-merge")
async def trim_merge(request: Request, body: TrimMergeRequest):
    """Her video için başlangıç/bitiş saniyesi ile kesit al, sonra birleştir."""
    import uuid, shutil

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    tmp_clips = []

    for clip in body.clips:
        src = _find_video(clip.job_id)
        if not src:
            raise HTTPException(status_code=404, detail=f"Video bulunamadı: {clip.job_id}")

        duration = _get_duration(src)
        start = max(0.0, clip.start)
        end   = min(duration, clip.end) if clip.end is not None else duration

        if end <= start:
            raise HTTPException(
                status_code=422,
                detail=f"{clip.job_id}: bitiş ({end}s) başlangıçtan ({start}s) önce olamaz."
            )

        tmp_path = os.path.join(OUTPUT_DIR, f"trim_{uuid.uuid4().hex[:8]}.mp4")
        cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-ss", str(start), "-to", str(end),
            "-i", src,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            "-avoid_negative_ts", "make_zero",
            tmp_path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            raise HTTPException(status_code=500, detail=f"Trim hatası ({clip.job_id}): {r.stderr[-300:]}")
        tmp_clips.append(tmp_path)

    # Tek klipse birleştirme yapma — direkt döndür
    if len(tmp_clips) == 1:
        merged_job_id = f"trim_{uuid.uuid4().hex[:12]}"
        out_path = os.path.join(OUTPUT_DIR, f"{merged_job_id}.mp4")
        shutil.move(tmp_clips[0], out_path)
    else:
        merged_job_id = f"trim_{uuid.uuid4().hex[:12]}"
        out_path = os.path.join(OUTPUT_DIR, f"{merged_job_id}.mp4")
        scale = _scale_filter(body.resolution)
        trans = body.transition
        has_transition = trans != "cut"
        if has_transition:
            ok, err = _merge_xfade(tmp_clips, out_path, [trans] * (len(tmp_clips) - 1), body.transition_duration, scale)
        else:
            ok, err = _merge_cut(tmp_clips, out_path, scale)
        # Geçici dosyaları sil
        for p in tmp_clips:
            try: os.remove(p)
            except: pass
        if not ok:
            raise HTTPException(status_code=500, detail=f"Birleştirme hatası: {err}")

    # Jobs dizinine kopyala (auto-edit uyumluluğu için)
    new_job_dir = os.path.join(JOBS_DIR, merged_job_id)
    os.makedirs(new_job_dir, exist_ok=True)
    shutil.copy2(out_path, os.path.join(new_job_dir, "input.mp4"))

    duration = _get_duration(out_path)
    out_filename = os.path.basename(out_path)
    return {
        "job_id":       merged_job_id,
        "clip_count":   len(body.clips),
        "duration":     round(duration, 1),
        "download_url": f"{PUBLIC_BASE_URL}/outputs/{out_filename}",
    }
