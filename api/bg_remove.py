"""
Background Removal — POST /api/bg-remove/{job_id}
chromakey modu: FFmpeg colorkey filtresi (yeşil/mavi ekran).
ai modu: rembg ile AI tabanlı frame-by-frame kaldırma.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
import os
import subprocess
import tempfile
import shutil
import logging
from pathlib import Path
from services.security import verify_job_owner

router = APIRouter(prefix="/api", tags=["bg-remove"])
logger = logging.getLogger(__name__)

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", os.path.join(BASE_DIR, "..", "outputs"))
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "..", "uploads"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000")


class BgRemoveRequest(BaseModel):
    mode: str = "chromakey"       # "chromakey" | "ai"
    chroma_color: str = "green"   # "green" | "blue" | "white" (chromakey mode)
    similarity: float = 0.25      # chromakey benzerlik eşiği (0.01-1.0)
    blend: float = 0.05           # chromakey blend yumuşatma
    bg_color: str = "#000000"     # yeni arka plan rengi (hex veya "transparent")
    # ai mode
    fps_limit: int = 6            # AI modda işlenecek maks FPS (performans için)


def _find_video(job_id: str) -> Optional[str]:
    for suffix in ["_auto_aspect.mp4", "_auto.mp4", "_subtitled_tr.mp4", "_cut.mp4"]:
        p = os.path.join(OUTPUT_DIR, f"{job_id}{suffix}")
        if os.path.exists(p):
            return p
    up = os.path.join(UPLOAD_DIR, f"{job_id}.mp4")
    if os.path.exists(up):
        return up
    return None


def _hex_to_ffmpeg_color(hex_color: str) -> str:
    """#RRGGBB → 0xRRGGBB (FFmpeg formatı)"""
    c = hex_color.lstrip("#")
    if len(c) == 3:
        c = "".join(x * 2 for x in c)
    return f"0x{c.upper()}"


def _apply_chromakey(video_path: str, out_path: str, body: BgRemoveRequest) -> bool:
    chroma_map = {"green": "0x00FF00", "blue": "0x0000FF", "white": "0xFFFFFF"}
    chroma_hex = chroma_map.get(body.chroma_color, "0x00FF00")
    bg_hex = _hex_to_ffmpeg_color(body.bg_color) if body.bg_color != "transparent" else None

    if bg_hex:
        # chromakey + arka plan rengi
        vf = (
            f"[0:v]chromakey={chroma_hex}:{body.similarity:.2f}:{body.blend:.2f}[ck];"
            f"color=c={bg_hex}:size=hd1080[bg];"
            f"[bg][ck]overlay"
        )
        cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-i", video_path,
            "-filter_complex", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "copy", "-movflags", "+faststart",
            out_path,
        ]
    else:
        # Alpha channel ile şeffaf PNG (WebM desteklenmiyor, MP4 alpha desteklemez)
        # Bunun yerine siyah arka plan kullan
        vf = (
            f"[0:v]chromakey={chroma_hex}:{body.similarity:.2f}:{body.blend:.2f}[ck];"
            f"color=c=black:size=hd1080[bg];"
            f"[bg][ck]overlay"
        )
        cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-i", video_path,
            "-filter_complex", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "copy", "-movflags", "+faststart",
            out_path,
        ]

    r = subprocess.run(cmd, capture_output=True, text=True)
    return os.path.exists(out_path) and os.path.getsize(out_path) > 0


def _apply_ai_removal(video_path: str, out_path: str, body: BgRemoveRequest) -> tuple[bool, str]:
    """rembg ile AI arka plan kaldırma (frame-by-frame)."""
    try:
        from rembg import remove, new_session
        from PIL import Image
        import io
    except ImportError:
        return False, "rembg veya Pillow kurulu değil. 'pip install rembg' çalıştırın."

    bg_hex = body.bg_color.lstrip("#") if body.bg_color != "transparent" else None
    if bg_hex and len(bg_hex) == 3:
        bg_hex = "".join(x * 2 for x in bg_hex)
    bg_rgb = tuple(int(bg_hex[i:i+2], 16) for i in (0, 2, 4)) if bg_hex else None

    tmp_dir = tempfile.mkdtemp(prefix="bgrm_")
    frames_in  = os.path.join(tmp_dir, "in_%06d.png")
    frames_out = os.path.join(tmp_dir, "out_%06d.png")

    try:
        # Kareleri çıkar (fps_limit ile sınırla)
        r1 = subprocess.run([
            "ffmpeg", "-hide_banner", "-y",
            "-i", video_path,
            "-vf", f"fps={body.fps_limit}",
            frames_in,
        ], capture_output=True, text=True)

        in_files = sorted(Path(tmp_dir).glob("in_*.png"))
        if not in_files:
            return False, "Kare çıkarılamadı: " + r1.stderr[-200:]

        session = new_session("u2net_human_seg")  # insan odaklı model

        for f in in_files:
            img = Image.open(f).convert("RGBA")
            result = remove(img, session=session)

            if bg_rgb:
                bg = Image.new("RGBA", result.size, (*bg_rgb, 255))
                bg.paste(result, mask=result.split()[3])
                final = bg.convert("RGB")
            else:
                final = result.convert("RGB")

            out_f = str(f).replace("in_", "out_")
            final.save(out_f)

        # Kareleri videoya dönüştür
        r2 = subprocess.run([
            "ffmpeg", "-hide_banner", "-y",
            "-framerate", str(body.fps_limit),
            "-i", frames_out,
            "-i", video_path,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "copy", "-movflags", "+faststart",
            out_path,
        ], capture_output=True, text=True)

        ok = os.path.exists(out_path) and os.path.getsize(out_path) > 0
        return ok, ("" if ok else r2.stderr[-300:])

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.post("/bg-remove/{job_id}")
async def remove_background(request: Request, job_id: str, body: BgRemoveRequest):
    verify_job_owner(job_id, request)

    video_path = _find_video(job_id)
    if not video_path:
        raise HTTPException(status_code=404, detail="İşlenmiş video bulunamadı. Önce auto-edit çalıştırın.")

    out_filename = f"{job_id}_bgrm_{body.mode}.mp4"
    out_path = os.path.join(OUTPUT_DIR, out_filename)

    if body.mode == "chromakey":
        ok = _apply_chromakey(video_path, out_path, body)
        if not ok:
            raise HTTPException(status_code=500, detail="Chromakey uygulanamadı.")
    elif body.mode == "ai":
        ok, err = _apply_ai_removal(video_path, out_path, body)
        if not ok:
            raise HTTPException(status_code=500, detail=f"AI arka plan kaldırma başarısız: {err}")
    else:
        raise HTTPException(status_code=400, detail=f"Bilinmeyen mod: {body.mode}. 'chromakey' veya 'ai' kullanın.")

    return {
        "job_id": job_id,
        "mode": body.mode,
        "download_url": f"{PUBLIC_BASE_URL}/outputs/{out_filename}",
    }
