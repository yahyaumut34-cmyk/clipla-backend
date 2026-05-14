"""
Speed Control — POST /api/speed/{job_id}
FFmpeg setpts + atempo ile hız kontrolü.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import os, subprocess, logging
from services.security import verify_job_owner

router = APIRouter(prefix="/api", tags=["speed"])
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


class SpeedRequest(BaseModel):
    speed: float = 1.5  # 0.25x – 4.0x


@router.post("/speed/{job_id}")
async def change_speed(request: Request, job_id: str, body: SpeedRequest):
    verify_job_owner(job_id, request)

    speed = max(0.25, min(4.0, body.speed))
    video_path = _find_video(job_id)
    if not video_path:
        raise HTTPException(404, "Video bulunamadı.")

    out_file = f"{job_id}_speed_{str(speed).replace('.','p')}.mp4"
    out_path = os.path.join(OUTPUT_DIR, out_file)

    # atempo 0.5-2.0 arasında çalışır, dışında zincirle
    def atempo_chain(s: float) -> str:
        filters = []
        while s > 2.0:
            filters.append("atempo=2.0"); s /= 2.0
        while s < 0.5:
            filters.append("atempo=0.5"); s /= 0.5
        filters.append(f"atempo={s:.4f}")
        return ",".join(filters)

    vf = f"setpts={1/speed:.6f}*PTS"
    af = atempo_chain(speed)

    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-i", video_path,
        "-vf", vf,
        "-af", af,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise HTTPException(500, f"Hız değiştirme başarısız: {r.stderr[-300:]}")

    return {
        "job_id": job_id,
        "speed": speed,
        "download_url": f"{PUBLIC_BASE_URL}/outputs/{out_file}",
    }
