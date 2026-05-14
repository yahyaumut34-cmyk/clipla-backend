"""
Clipla SFX (Sound Effects) System
- Zaman damgalı ses efektleri (kahkaha, alkış, düdük, vs.)
- CC0 ses dosyası varsa kullan, yoksa FFmpeg sentezi fallback
- Endpoint: POST /api/sfx/{job_id}
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
import os
import subprocess
import logging
from pathlib import Path
from services.security import get_plan_from_request, verify_job_owner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["sfx"])

BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR      = os.environ.get("OUTPUT_DIR", os.path.join(BASE_DIR, "..", "outputs"))
JOBS_DIR        = os.environ.get("JOBS_DIR",   os.path.join(BASE_DIR, "..", "jobs"))
SOUNDS_DIR      = os.path.join(BASE_DIR, "..", "sounds")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000")

# =========================================================
# SFX Kütüphanesi
# file     : sounds/ klasöründe CC0 ses dosyası (varsa kullanılır)
# fc_source: FFmpeg filter_complex sentezi (fallback)
# =========================================================
SFX_LIBRARY: dict = {
    "laugh": {
        "file": "laugh.wav",
        "fc_source": "anoisesrc=color=pink:duration=2.5:amplitude=0.85,bandpass=f=700:width_type=h:width=500[sfx_raw]",
        "label": "Kahkaha",
    },
    "applause": {
        "file": "applause.wav",
        "fc_source": "anoisesrc=color=white:duration=3.0:amplitude=0.65,lowpass=f=6000,highpass=f=200[sfx_raw]",
        "label": "Alkış",
    },
    "airhorn": {
        "file": "airhorn.wav",
        "fc_source": "aevalsrc='0.7*(sin(2*PI*450*t)+0.5*sin(2*PI*900*t)+0.2*sin(2*PI*1350*t))':duration=0.9:c=mono[sfx_raw]",
        "label": "Düdük",
    },
    "whoosh": {
        "file": "whoosh.wav",
        "fc_source": "aevalsrc='0.65*sin(2*PI*(100+t*1200)*t)*exp(-3*t)':duration=0.6:c=mono[sfx_raw]",
        "label": "Geçiş Sesi",
    },
    "sad_trombone": {
        "file": "sad_trombone.wav",
        "fc_source": "aevalsrc='0.55*sin(2*PI*(440-t*120)*t)*exp(-1.2*t)':duration=1.6:c=mono[sfx_raw]",
        "label": "Üzgün Trompet",
    },
    "drum_hit": {
        "file": "drum_hit.wav",
        "fc_source": "aevalsrc='0.75*sin(2*PI*75*t)*exp(-18*t)':duration=0.35:c=mono[sfx_raw]",
        "label": "Davul",
    },
    "bell": {
        "file": "bell.wav",
        "fc_source": "aevalsrc='0.65*sin(2*PI*880*t)*exp(-2.8*t)':duration=1.5:c=mono[sfx_raw]",
        "label": "Zil",
    },
    "pop": {
        "file": "pop.wav",
        "fc_source": "aevalsrc='0.85*sin(2*PI*300*t)*exp(-45*t)':duration=0.15:c=mono[sfx_raw]",
        "label": "Pop",
    },
    "beep": {
        "file": "beep.wav",
        "fc_source": "sine=frequency=1000:duration=0.35[sfx_raw]",
        "label": "Bip",
    },
    "crowd_cheer": {
        "file": "crowd_cheer.wav",
        "fc_source": "anoisesrc=color=pink:duration=3.0:amplitude=0.72,highpass=f=250,lowpass=f=9000[sfx_raw]",
        "label": "Coşku",
    },
}


class SfxRequest(BaseModel):
    sfx_type: str
    timestamp: Optional[float] = None   # saniye cinsinden, None = başa ekle
    volume: float = 0.85                # 0.1 - 2.0


def _find_latest_video(job_id: str) -> str:
    """İş için en son çıktı videosunu bul."""
    candidates = []
    if os.path.isdir(OUTPUT_DIR):
        for fname in os.listdir(OUTPUT_DIR):
            if fname.startswith(job_id) and fname.endswith(".mp4"):
                fpath = os.path.join(OUTPUT_DIR, fname)
                candidates.append((os.path.getmtime(fpath), fpath))

    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]

    # Yüklenen orijinal videoya fallback
    job_dir = os.path.join(JOBS_DIR, job_id)
    if os.path.isdir(job_dir):
        for fname in os.listdir(job_dir):
            if fname.lower().endswith((".mp4", ".mov", ".avi", ".mkv", ".webm")):
                return os.path.join(job_dir, fname)

    raise HTTPException(status_code=404, detail=f"Video bulunamadı: {job_id}")


def _apply_sfx_to_video(
    source_path: str,
    out_path: str,
    sfx_type: str,
    timestamp: Optional[float],
    volume: float,
) -> bool:
    """
    Ses efektini videoya karıştır.
    CC0 dosyası varsa kullan, yoksa FFmpeg sentezi.
    """
    sfx_info = SFX_LIBRARY.get(sfx_type)
    if not sfx_info:
        return False

    vol = round(max(0.1, min(volume, 2.0)), 2)
    delay_ms = int((timestamp or 0) * 1000)

    # CC0 ses dosyası kontrolü
    sound_file = os.path.join(SOUNDS_DIR, sfx_info["file"])
    use_file = os.path.isfile(sound_file)

    if use_file:
        # Dosya tabanlı: -i sfx_file.wav
        adelay = f"[1:a]volume={vol},adelay={delay_ms}|{delay_ms},apad[sfx];[0:a][sfx]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-i", source_path,
            "-i", sound_file,
            "-filter_complex", adelay,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            out_path,
        ]
    else:
        # FFmpeg sentezi fallback
        fc_source = sfx_info["fc_source"]
        filter_complex = (
            f"{fc_source};"
            f"[sfx_raw]volume={vol},adelay={delay_ms}|{delay_ms},apad[sfx];"
            f"[0:a][sfx]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-i", source_path,
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            out_path,
        ]

    logger.info(f"[sfx] {'dosya' if use_file else 'sentez'} → {sfx_type} @ {timestamp}s")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        logger.error(f"[sfx] FFmpeg hatası: {result.stderr[-400:]}")
        return False
    return os.path.isfile(out_path) and os.path.getsize(out_path) > 0


@router.post("/sfx/{job_id}")
async def add_sfx(job_id: str, body: SfxRequest, request: Request):
    verify_job_owner(job_id, request)
    if get_plan_from_request(request) != "pro":
        raise HTTPException(status_code=403, detail="SFX ekleme Pro plan gerektirir.")
    if body.sfx_type not in SFX_LIBRARY:
        valid = list(SFX_LIBRARY.keys())
        raise HTTPException(status_code=400, detail=f"Bilinmeyen SFX: {body.sfx_type}. Geçerli: {valid}")

    source_path = _find_latest_video(job_id)

    sfx_label = SFX_LIBRARY[body.sfx_type]["label"]
    ts_str = f"_{int(body.timestamp)}s" if body.timestamp is not None else ""
    out_name = f"{job_id}_sfx_{body.sfx_type}{ts_str}.mp4"
    out_path = os.path.join(OUTPUT_DIR, out_name)

    success = _apply_sfx_to_video(
        source_path, out_path, body.sfx_type, body.timestamp, body.volume
    )

    if not success:
        raise HTTPException(status_code=500, detail=f"SFX uygulanamadı: {body.sfx_type}")

    download_url = f"/outputs/{out_name}"
    output_url = f"{PUBLIC_BASE_URL}{download_url}"

    logger.info(f"[sfx] Tamamlandı: {sfx_label} @ {body.timestamp}s → {out_name}")

    return {
        "status": "ok",
        "sfx_type": body.sfx_type,
        "sfx_label": sfx_label,
        "timestamp": body.timestamp,
        "download_url": download_url,
        "output_url": output_url,
    }


@router.get("/sfx/list")
def list_sfx():
    """Mevcut SFX tiplerini ve CC0 dosya durumunu döndür."""
    result = []
    for sfx_type, info in SFX_LIBRARY.items():
        sound_file = os.path.join(SOUNDS_DIR, info["file"])
        result.append({
            "type": sfx_type,
            "label": info["label"],
            "has_file": os.path.isfile(sound_file),
            "file": info["file"],
        })
    return {"sfx": result}
