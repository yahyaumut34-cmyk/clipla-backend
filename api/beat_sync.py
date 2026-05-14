"""
Beat Sync — POST /api/beat-sync/{job_id}
Müzik veya video sesinden librosa ile beat tespiti → beat pozisyonlarında görsel pulse efekti.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
import os
import subprocess
import tempfile
import logging
from services.security import verify_job_owner

router = APIRouter(prefix="/api", tags=["beat-sync"])
logger = logging.getLogger(__name__)

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", os.path.join(BASE_DIR, "..", "outputs"))
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "..", "uploads"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000")


class BeatSyncRequest(BaseModel):
    effect: str = "pulse"        # "pulse" | "zoom" | "flash"
    sensitivity: float = 0.7     # 0.1 – 1.0 (beat detection threshold)
    max_beats: int = 80          # limiter


def _find_video(job_id: str) -> Optional[str]:
    for suffix in ["_auto_aspect.mp4", "_auto.mp4", "_subtitled_tr.mp4", "_cut.mp4"]:
        p = os.path.join(OUTPUT_DIR, f"{job_id}{suffix}")
        if os.path.exists(p):
            return p
    up = os.path.join(UPLOAD_DIR, f"{job_id}.mp4")
    if os.path.exists(up):
        return up
    return None


def _detect_beats(audio_path: str, sensitivity: float) -> list[float]:
    try:
        import librosa
        import numpy as np
        y, sr = librosa.load(audio_path, sr=None, mono=True)
        # onset_strength → beat_track
        hop_length = 512
        onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
        tempo, beat_frames = librosa.beat.beat_track(
            onset_envelope=onset_env, sr=sr, hop_length=hop_length,
            tightness=100, trim=False,
        )
        beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop_length).tolist()
        # sensitivity filtresi: düşük onset gücündeki beatları at
        if sensitivity < 1.0:
            strengths = onset_env[beat_frames.clip(0, len(onset_env) - 1)]
            threshold = float(np.percentile(strengths, (1 - sensitivity) * 100))
            beat_times = [t for t, s in zip(beat_times, strengths) if s >= threshold]
        return beat_times
    except ImportError:
        # librosa yoksa 120 BPM varsayılan
        logger.warning("librosa bulunamadı, 120 BPM sabit kullanılıyor")
        duration = _get_duration(audio_path)
        interval = 60.0 / 120
        return [round(i * interval, 3) for i in range(int(duration / interval))]


def _get_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
        capture_output=True, text=True,
    )
    import json
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 60.0


def _build_beat_filter(beat_times: list[float], effect: str) -> str:
    if not beat_times:
        return "null"

    if effect == "flash":
        # Kısa beyaz flash
        parts = [
            f"curves=enable='between(t,{max(0.0,t-0.02):.3f},{t+0.06:.3f})':preset=lighter"
            for t in beat_times
        ]
        return ",".join(parts)

    if effect == "zoom":
        # Hafif zoom pulse — eq saturation + hue trick (geniş uyumluluk)
        parts = [
            f"hue=s=1.8:enable='between(t,{max(0.0,t-0.03):.3f},{t+0.07:.3f})'"
            for t in beat_times
        ]
        return ",".join(parts)

    # default "pulse": saturation & brightness boost
    parts = [
        f"hue=s=2.0:enable='between(t,{max(0.0,t-0.03):.3f},{t+0.08:.3f})'"
        for t in beat_times
    ]
    return ",".join(parts)


@router.post("/beat-sync/{job_id}")
async def beat_sync(request: Request, job_id: str, body: BeatSyncRequest):
    verify_job_owner(job_id, request)

    video_path = _find_video(job_id)
    if not video_path:
        raise HTTPException(status_code=404, detail="İşlenmiş video bulunamadı. Önce auto-edit çalıştırın.")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_audio:
        audio_path = tmp_audio.name

    try:
        # Ses çıkar
        r = subprocess.run([
            "ffmpeg", "-hide_banner", "-y", "-i", video_path,
            "-vn", "-ar", "22050", "-ac", "1", "-f", "wav", audio_path,
        ], capture_output=True, text=True)
        if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
            raise HTTPException(status_code=500, detail="Ses çıkarılamadı: " + r.stderr[-200:])

        # Beat tespiti
        beat_times = _detect_beats(audio_path, body.sensitivity)
        beat_times = sorted(set(round(t, 3) for t in beat_times))[:body.max_beats]

        if not beat_times:
            raise HTTPException(status_code=422, detail="Beat tespit edilemedi.")

        # FFmpeg filtresi oluştur
        vf = _build_beat_filter(beat_times, body.effect)

        out_filename = f"{job_id}_beatsync.mp4"
        out_path = os.path.join(OUTPUT_DIR, out_filename)

        cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-i", video_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "copy",
            "-movflags", "+faststart",
            out_path,
        ]
        r2 = subprocess.run(cmd, capture_output=True, text=True)
        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise HTTPException(status_code=500, detail="Beat sync render başarısız: " + r2.stderr[-300:])

        return {
            "job_id": job_id,
            "beat_count": len(beat_times),
            "beat_times": beat_times[:20],  # preview
            "effect": body.effect,
            "download_url": f"{PUBLIC_BASE_URL}/outputs/{out_filename}",
        }

    finally:
        try:
            os.unlink(audio_path)
        except Exception:
            pass
