"""
Reverse — POST /api/reverse/{job_id}
FFmpeg reverse + areverse filtresi ile videoyu tersine çevirir.
"""
from fastapi import APIRouter, HTTPException, Request
import os, subprocess, logging
from services.security import verify_job_owner

router = APIRouter(prefix="/api", tags=["reverse"])
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


@router.post("/reverse/{job_id}")
async def reverse_video(request: Request, job_id: str):
    verify_job_owner(job_id, request)

    video_path = _find_video(job_id)
    if not video_path:
        raise HTTPException(404, "Video bulunamadı.")

    out_file = f"{job_id}_reversed.mp4"
    out_path = os.path.join(OUTPUT_DIR, out_file)

    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-i", video_path,
        "-vf", "reverse",
        "-af", "areverse",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise HTTPException(500, f"Ters çevirme başarısız: {r.stderr[-300:]}")

    return {
        "job_id": job_id,
        "download_url": f"{PUBLIC_BASE_URL}/outputs/{out_file}",
    }
