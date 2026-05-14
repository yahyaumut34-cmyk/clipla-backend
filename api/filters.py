"""
Visual Filters — POST /api/filters/{job_id}
Sinematik, vintage, siyah-beyaz vb. hazır görsel filtreler.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import os, subprocess, logging
from services.security import verify_job_owner

router = APIRouter(prefix="/api", tags=["filters"])
logger = logging.getLogger(__name__)

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", os.path.join(BASE_DIR, "..", "outputs"))
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "..", "uploads"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000")

FILTERS = {
    "bw": {
        "label": "Siyah & Beyaz",
        "vf": "hue=s=0,eq=contrast=1.15:brightness=-0.05",
    },
    "vintage": {
        "label": "Vintage",
        "vf": (
            "curves=r='0/0.1 0.5/0.6 1/0.9':"
            "g='0/0 0.5/0.5 1/0.85':"
            "b='0/0.1 0.5/0.45 1/0.75',"
            "vignette=PI/4"
        ),
    },
    "cinematic": {
        "label": "Sinematik",
        "vf": (
            "eq=contrast=1.2:saturation=0.8:brightness=-0.05:gamma=1.05,"
            "vignette=PI/5"
        ),
    },
    "warm": {
        "label": "Sıcak",
        "vf": "curves=r='0/0 1/1.1':b='0/0 1/0.85',eq=saturation=1.2",
    },
    "cool": {
        "label": "Soğuk",
        "vf": "curves=b='0/0.05 1/1.1':r='0/0 1/0.9',eq=saturation=0.9",
    },
    "fade": {
        "label": "Soluk",
        "vf": "eq=contrast=0.85:brightness=0.08:saturation=0.75,vignette=PI/6",
    },
    "sharp": {
        "label": "Keskin",
        "vf": "unsharp=5:5:1.5:5:5:0.0,eq=contrast=1.1",
    },
    "drama": {
        "label": "Dramatik",
        "vf": (
            "eq=contrast=1.4:saturation=1.3:brightness=-0.1:gamma=0.85,"
            "vignette=PI/3"
        ),
    },
}


def _find_video(job_id: str):
    for suffix in ["_auto_aspect.mp4", "_auto.mp4", "_subtitled_tr.mp4", "_cut.mp4"]:
        p = os.path.join(OUTPUT_DIR, f"{job_id}{suffix}")
        if os.path.exists(p): return p
    up = os.path.join(UPLOAD_DIR, f"{job_id}.mp4")
    if os.path.exists(up): return up
    return None


class FilterRequest(BaseModel):
    filter_name: str  # "bw" | "vintage" | "cinematic" | "warm" | "cool" | "fade" | "sharp" | "drama"


@router.post("/filters/{job_id}")
async def apply_filter(request: Request, job_id: str, body: FilterRequest):
    verify_job_owner(job_id, request)

    flt = FILTERS.get(body.filter_name)
    if not flt:
        raise HTTPException(400, f"Geçersiz filtre. Geçerli: {list(FILTERS.keys())}")

    video_path = _find_video(job_id)
    if not video_path:
        raise HTTPException(404, "Video bulunamadı.")

    out_file = f"{job_id}_filter_{body.filter_name}.mp4"
    out_path = os.path.join(OUTPUT_DIR, out_file)

    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-i", video_path,
        "-vf", flt["vf"],
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "copy",
        "-movflags", "+faststart",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise HTTPException(500, f"Filtre başarısız: {r.stderr[-300:]}")

    return {
        "job_id": job_id,
        "filter": body.filter_name,
        "label": flt["label"],
        "download_url": f"{PUBLIC_BASE_URL}/outputs/{out_file}",
    }


@router.get("/filters/list")
async def list_filters():
    return {k: v["label"] for k, v in FILTERS.items()}
