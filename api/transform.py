"""
Transform — POST /api/transform/{job_id}
Döndürme, kırpma ve en-boy oranı değiştirme.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
import os, subprocess, logging
from services.security import verify_job_owner

router = APIRouter(prefix="/api", tags=["transform"])
logger = logging.getLogger(__name__)

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", os.path.join(BASE_DIR, "..", "outputs"))
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "..", "uploads"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000")

RATIO_MAP = {
    "9:16":  ("1080", "1920"),   # TikTok / Reels / Shorts
    "16:9":  ("1920", "1080"),   # YouTube / Desktop
    "1:1":   ("1080", "1080"),   # Instagram kare
    "4:5":   ("1080", "1350"),   # Instagram portrait
    "4:3":   ("1440", "1080"),   # Klasik
}


def _find_video(job_id: str):
    for suffix in ["_auto_aspect.mp4", "_auto.mp4", "_subtitled_tr.mp4", "_cut.mp4"]:
        p = os.path.join(OUTPUT_DIR, f"{job_id}{suffix}")
        if os.path.exists(p): return p
    up = os.path.join(UPLOAD_DIR, f"{job_id}.mp4")
    if os.path.exists(up): return up
    return None


class TransformRequest(BaseModel):
    rotate:       Optional[int]   = None    # 90 | 180 | 270
    aspect_ratio: Optional[str]   = None    # "9:16" | "16:9" | "1:1" | "4:5"
    flip:         Optional[str]   = None    # "horizontal" | "vertical"


@router.post("/transform/{job_id}")
async def transform_video(request: Request, job_id: str, body: TransformRequest):
    verify_job_owner(job_id, request)

    video_path = _find_video(job_id)
    if not video_path:
        raise HTTPException(404, "Video bulunamadı.")

    filters = []
    label_parts = []

    # Döndür
    if body.rotate in (90, 180, 270):
        if body.rotate == 90:
            filters.append("transpose=1")
        elif body.rotate == 180:
            filters.append("transpose=2,transpose=2")
        elif body.rotate == 270:
            filters.append("transpose=2")
        label_parts.append(f"rot{body.rotate}")

    # Flip
    if body.flip == "horizontal":
        filters.append("hflip")
        label_parts.append("hflip")
    elif body.flip == "vertical":
        filters.append("vflip")
        label_parts.append("vflip")

    # En-boy oranı
    if body.aspect_ratio and body.aspect_ratio in RATIO_MAP:
        w, h = RATIO_MAP[body.aspect_ratio]
        filters.append(
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black"
        )
        label_parts.append(body.aspect_ratio.replace(":", "x"))

    if not filters:
        raise HTTPException(400, "En az bir işlem seçin: rotate, aspect_ratio veya flip.")

    label = "_".join(label_parts) or "transform"
    out_file = f"{job_id}_{label}.mp4"
    out_path = os.path.join(OUTPUT_DIR, out_file)

    vf = ",".join(filters)
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
        raise HTTPException(500, f"Dönüşüm başarısız: {r.stderr[-300:]}")

    return {
        "job_id": job_id,
        "operations": label_parts,
        "download_url": f"{PUBLIC_BASE_URL}/outputs/{out_file}",
    }
