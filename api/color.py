"""
Color Grading — POST /api/color/{job_id}
FFmpeg eq filtresi ile parlaklık, kontrast, doygunluk, gamma.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
import os, subprocess, logging
from services.security import verify_job_owner

router = APIRouter(prefix="/api", tags=["color"])
logger = logging.getLogger(__name__)

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", os.path.join(BASE_DIR, "..", "outputs"))
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "..", "uploads"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000")


def _find_video(job_id: str):
    for suffix in ["_auto_aspect.mp4", "_auto.mp4", "_subtitled_tr.mp4", "_cut.mp4"]:
        p = os.path.join(OUTPUT_DIR, f"{job_id}{suffix}")
        if os.path.exists(p): return p
    up = os.path.join(UPLOAD_DIR, f"{job_id}.mp4")
    if os.path.exists(up): return up
    return None


class ColorRequest(BaseModel):
    brightness: float  = 0.0   # -1.0 – 1.0
    contrast:   float  = 1.0   # 0.0 – 3.0
    saturation: float  = 1.0   # 0.0 – 3.0
    gamma:      float  = 1.0   # 0.1 – 10.0
    preset:     Optional[str] = None  # "warm" | "cool" | "vivid" | "muted"


PRESETS = {
    "warm":    dict(brightness=0.05, contrast=1.1, saturation=1.3, gamma=0.95),
    "cool":    dict(brightness=0.0,  contrast=1.05, saturation=0.9, gamma=1.05),
    "vivid":   dict(brightness=0.05, contrast=1.2,  saturation=1.6, gamma=0.9),
    "muted":   dict(brightness=-0.05, contrast=0.9, saturation=0.6, gamma=1.1),
    "cinematic": dict(brightness=-0.05, contrast=1.15, saturation=0.85, gamma=1.0),
}


@router.post("/color/{job_id}")
async def color_grade(request: Request, job_id: str, body: ColorRequest):
    verify_job_owner(job_id, request)

    params = body.dict()
    if body.preset and body.preset in PRESETS:
        params.update(PRESETS[body.preset])

    brightness = max(-1.0, min(1.0, params["brightness"]))
    contrast   = max(0.0,  min(3.0, params["contrast"]))
    saturation = max(0.0,  min(3.0, params["saturation"]))
    gamma      = max(0.1,  min(10.0, params["gamma"]))

    video_path = _find_video(job_id)
    if not video_path:
        raise HTTPException(404, "Video bulunamadı.")

    label = body.preset or "custom"
    out_file = f"{job_id}_color_{label}.mp4"
    out_path = os.path.join(OUTPUT_DIR, out_file)

    vf = f"eq=brightness={brightness:.3f}:contrast={contrast:.3f}:saturation={saturation:.3f}:gamma={gamma:.3f}"

    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "copy",
        "-movflags", "+faststart",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise HTTPException(500, f"Renk düzenleme başarısız: {r.stderr[-300:]}")

    return {
        "job_id": job_id,
        "preset": label,
        "download_url": f"{PUBLIC_BASE_URL}/outputs/{out_file}",
    }
