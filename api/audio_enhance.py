"""
Audio Enhancement — POST /api/enhance-audio/{job_id}
FFmpeg filtreleri ile ses kalitesi iyileştirme: gürültü azaltma, ses güçlendirme, normalleştirme.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
import os
import subprocess
import logging
from services.security import verify_job_owner

router = APIRouter(prefix="/api", tags=["audio-enhance"])
logger = logging.getLogger(__name__)

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", os.path.join(BASE_DIR, "..", "outputs"))
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "..", "uploads"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000")

# Hazır ses iyileştirme profilleri
ENHANCE_PROFILES = {
    "clean": {
        # Temel temizleme: gürültü azalt + normalize
        "af": "afftdn=nf=-25,highpass=f=80,lowpass=f=8000,loudnorm=I=-16:TP=-1.5:LRA=11",
        "label": "Temiz Ses",
    },
    "voice_boost": {
        # Konuşma güçlendirme: mid-frekans artışı + temizleme
        "af": (
            "afftdn=nf=-20,"
            "highpass=f=100,"
            "equalizer=f=250:width_type=o:width=1.5:g=-3,"   # boomy azalt
            "equalizer=f=1000:width_type=o:width=2:g=4,"     # konuşma güçlendir
            "equalizer=f=3000:width_type=o:width=1.5:g=3,"   # netlik artır
            "loudnorm=I=-14:TP=-1:LRA=9"
        ),
        "label": "Konuşma Güçlendir",
    },
    "loud": {
        # Maksimum ses seviyesi
        "af": "afftdn=nf=-15,loudnorm=I=-10:TP=-0.5:LRA=7",
        "label": "Ses Güçlü",
    },
    "denoise_only": {
        # Sadece gürültü azaltma
        "af": "afftdn=nf=-30,highpass=f=60",
        "label": "Sadece Gürültü Temizle",
    },
    "podcast": {
        # Podcast kalitesi: geniş dinamik aralık, net konuşma
        "af": (
            "afftdn=nf=-20,"
            "highpass=f=80,"
            "acompressor=threshold=-18dB:ratio=4:attack=5:release=50:makeup=2dB,"
            "equalizer=f=200:width_type=o:width=1:g=-2,"
            "equalizer=f=2500:width_type=o:width=1.5:g=2,"
            "loudnorm=I=-16:TP=-1.5:LRA=11"
        ),
        "label": "Podcast Kalitesi",
    },
}


def _find_video(job_id: str) -> Optional[str]:
    for suffix in ["_auto_aspect.mp4", "_auto.mp4", "_subtitled_tr.mp4", "_cut.mp4"]:
        p = os.path.join(OUTPUT_DIR, f"{job_id}{suffix}")
        if os.path.exists(p):
            return p
    up = os.path.join(UPLOAD_DIR, f"{job_id}.mp4")
    if os.path.exists(up):
        return up
    return None


class AudioEnhanceRequest(BaseModel):
    profile: str = "clean"    # "clean" | "voice_boost" | "loud" | "denoise_only" | "podcast"
    custom_af: Optional[str] = None   # özel FFmpeg af filtresi (override)


@router.post("/enhance-audio/{job_id}")
async def enhance_audio(request: Request, job_id: str, body: AudioEnhanceRequest):
    verify_job_owner(job_id, request)

    video_path = _find_video(job_id)
    if not video_path:
        raise HTTPException(status_code=404, detail="İşlenmiş video bulunamadı. Önce auto-edit çalıştırın.")

    if body.custom_af:
        af_filter = body.custom_af
        profile_label = "Özel Filtre"
    else:
        profile = ENHANCE_PROFILES.get(body.profile)
        if not profile:
            valid = list(ENHANCE_PROFILES.keys())
            raise HTTPException(status_code=400, detail=f"Geçersiz profil. Geçerli: {valid}")
        af_filter = profile["af"]
        profile_label = profile["label"]

    out_filename = f"{job_id}_enhanced_{body.profile}.mp4"
    out_path = os.path.join(OUTPUT_DIR, out_filename)

    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-i", video_path,
        "-af", af_filter,
        "-c:v", "copy",          # video kopyala, sadece ses işle (hızlı)
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        out_path,
    ]

    r = subprocess.run(cmd, capture_output=True, text=True)
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise HTTPException(
            status_code=500,
            detail=f"Ses iyileştirme başarısız: {r.stderr[-300:]}",
        )

    return {
        "job_id": job_id,
        "profile": body.profile,
        "profile_label": profile_label,
        "download_url": f"{PUBLIC_BASE_URL}/outputs/{out_filename}",
    }


@router.get("/enhance-audio/profiles")
async def list_profiles():
    return {k: v["label"] for k, v in ENHANCE_PROFILES.items()}
