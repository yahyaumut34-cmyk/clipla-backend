"""
Text Overlay — POST /api/text/{job_id}
FFmpeg drawtext filtresi ile video üzerine metin ekleme.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
import os, subprocess, logging
from services.security import verify_job_owner

router = APIRouter(prefix="/api", tags=["text"])
logger = logging.getLogger(__name__)

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", os.path.join(BASE_DIR, "..", "outputs"))
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "..", "uploads"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000")

POS_MAP = {
    "center": ("(w-text_w)/2", "(h-text_h)/2"),
    "top":    ("(w-text_w)/2", "60"),
    "bottom": ("(w-text_w)/2", "h-text_h-60"),
    "top_left":     ("40", "40"),
    "bottom_left":  ("40", "h-text_h-40"),
    "top_right":    ("w-text_w-40", "40"),
    "bottom_right": ("w-text_w-40", "h-text_h-40"),
}


def _find_video(job_id: str):
    for suffix in ["_auto_aspect.mp4", "_auto.mp4", "_subtitled_tr.mp4", "_cut.mp4"]:
        p = os.path.join(OUTPUT_DIR, f"{job_id}{suffix}")
        if os.path.exists(p): return p
    up = os.path.join(UPLOAD_DIR, f"{job_id}.mp4")
    if os.path.exists(up): return up
    return None


class TextRequest(BaseModel):
    text:       str
    position:   str   = "bottom"       # center|top|bottom|top_left|bottom_right vb.
    font_size:  int   = 48
    color:      str   = "white"        # white | yellow | red | #RRGGBB
    bold:       bool  = True
    start_sec:  Optional[float] = None  # gösterim başlangıcı (None = tüm video)
    end_sec:    Optional[float] = None  # gösterim bitişi


@router.post("/text/{job_id}")
async def add_text_overlay(request: Request, job_id: str, body: TextRequest):
    verify_job_owner(job_id, request)

    video_path = _find_video(job_id)
    if not video_path:
        raise HTTPException(404, "Video bulunamadı.")

    x, y = POS_MAP.get(body.position, POS_MAP["bottom"])

    # Metni FFmpeg için escape et
    safe_text = body.text.replace("'", "\\'").replace(":", "\\:").replace("\\", "\\\\")

    # Font weight — FFmpeg fontweight desteği yok, bold için fontfile kullanılmalı ama
    # basit yaklaşım: box arka planı ekle
    drawtext = (
        f"drawtext=text='{safe_text}'"
        f":x={x}:y={y}"
        f":fontsize={body.font_size}"
        f":fontcolor={body.color}"
        f":box=1:boxcolor=black@0.45:boxborderw=8"
    )

    if body.start_sec is not None and body.end_sec is not None:
        drawtext += f":enable='between(t,{body.start_sec},{body.end_sec})'"
    elif body.start_sec is not None:
        drawtext += f":enable='gte(t,{body.start_sec})'"

    out_file = f"{job_id}_text.mp4"
    out_path = os.path.join(OUTPUT_DIR, out_file)

    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-i", video_path,
        "-vf", drawtext,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "copy",
        "-movflags", "+faststart",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise HTTPException(500, f"Metin ekleme başarısız: {r.stderr[-300:]}")

    return {
        "job_id": job_id,
        "text": body.text,
        "download_url": f"{PUBLIC_BASE_URL}/outputs/{out_file}",
    }
