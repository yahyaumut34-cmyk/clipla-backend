"""
Clipla Background Music System
- Arka plan müziği ekleme (mood tabanlı)
- Mevcut music/ klasöründeki royalty-free dosyaları kullanır
- Endpoint: POST /api/music/{job_id}
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
import os
import subprocess
import logging
import glob
from services.security import get_plan_from_request, verify_job_owner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["music"])

BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR      = os.environ.get("OUTPUT_DIR", os.path.join(BASE_DIR, "..", "outputs"))
JOBS_DIR        = os.environ.get("JOBS_DIR",   os.path.join(BASE_DIR, "..", "jobs"))
MUSIC_DIR       = os.path.join(BASE_DIR, "..", "music")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000")

# Mood → klasör adı eşlemesi
MUSIC_MOODS = {
    "calm":      {"dir": "calm",      "label": "Sakin"},
    "energetic": {"dir": "energetic", "label": "Enerjik"},
    "minimal":   {"dir": "minimal",   "label": "Minimal"},
    "rhythmic":  {"dir": "rhythmic",  "label": "Ritmik"},
    "slow":      {"dir": "slow",      "label": "Yavaş"},
}


class MusicRequest(BaseModel):
    mood: str
    start_time: float = 0.0       # müziğin videodan başlayacağı saniye
    end_time: Optional[float] = None  # None = video sonuna kadar
    volume: float = 0.22              # 0.1–1.0 (konuşma sesi baskın kalsın)


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

    job_dir = os.path.join(JOBS_DIR, job_id)
    if os.path.isdir(job_dir):
        for fname in os.listdir(job_dir):
            if fname.lower().endswith((".mp4", ".mov", ".avi", ".mkv", ".webm")):
                return os.path.join(job_dir, fname)

    raise HTTPException(status_code=404, detail=f"Video bulunamadı: {job_id}")


def _find_music_file(mood: str) -> str:
    """Mood klasöründeki ilk ses dosyasını döndür."""
    mood_info = MUSIC_MOODS.get(mood)
    if not mood_info:
        raise HTTPException(status_code=400, detail=f"Bilinmeyen müzik ruh hali: {mood}")

    mood_dir = os.path.join(MUSIC_DIR, mood_info["dir"])
    if not os.path.isdir(mood_dir):
        raise HTTPException(status_code=404, detail=f"Müzik klasörü bulunamadı: {mood}")

    # .mp3 veya .wav dosyası bul (çift uzantı toleransı dahil)
    for ext in ("*.mp3", "*.wav", "*.m4a", "*.mp3.mp3"):
        matches = glob.glob(os.path.join(mood_dir, ext))
        if matches:
            return matches[0]

    raise HTTPException(status_code=404, detail=f"{mood} için müzik dosyası bulunamadı")


def _get_video_duration(video_path: str) -> Optional[float]:
    """FFprobe ile video süresini saniye olarak döndür."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=15,
        )
        return float(result.stdout.strip()) if result.returncode == 0 else None
    except Exception:
        return None


def _mix_music(
    source_path: str,
    music_path: str,
    out_path: str,
    start_time: float,
    end_time: Optional[float],
    volume: float,
) -> bool:
    """
    Arka plan müziğini videoya karıştır.
    Müzik video süresinden kısaysa döngüye alınır.
    """
    vol = round(max(0.05, min(volume, 1.0)), 3)

    # Müziğin başlayacağı gecikme (videodan)
    delay_ms = int(start_time * 1000)

    if end_time is not None:
        duration = end_time - start_time
        # Müziği kırp ve geciktir
        music_filter = (
            f"[1:a]aloop=loop=-1:size=2e+09,atrim=start=0:duration={duration:.2f},"
            f"asetpts=PTS-STARTPTS,volume={vol},"
            f"adelay={delay_ms}|{delay_ms},apad[bgm];"
            f"[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=3[aout]"
        )
    else:
        # Video sonuna kadar müziği döngüye al
        music_filter = (
            f"[1:a]aloop=loop=-1:size=2e+09,volume={vol},"
            f"adelay={delay_ms}|{delay_ms}[bgm];"
            f"[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=3[aout]"
        )

    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-i", source_path,
        "-stream_loop", "-1",
        "-i", music_path,
        "-filter_complex", music_filter,
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        out_path,
    ]

    logger.info(f"[music] Karıştırılıyor: {os.path.basename(music_path)}, vol={vol}, start={start_time}s")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        logger.error(f"[music] FFmpeg hatası: {result.stderr[-500:]}")
        return False
    return os.path.isfile(out_path) and os.path.getsize(out_path) > 0


@router.post("/music/{job_id}")
async def add_music(job_id: str, body: MusicRequest, request: Request):
    verify_job_owner(job_id, request)
    if get_plan_from_request(request) != "pro":
        raise HTTPException(status_code=403, detail="Arka plan müziği Pro plan gerektirir.")
    if body.mood not in MUSIC_MOODS:
        valid = list(MUSIC_MOODS.keys())
        raise HTTPException(status_code=400, detail=f"Bilinmeyen mood: {body.mood}. Geçerli: {valid}")

    source_path = _find_latest_video(job_id)
    music_path  = _find_music_file(body.mood)
    mood_label  = MUSIC_MOODS[body.mood]["label"]

    ts_str = f"_{int(body.start_time)}s" if body.start_time > 0 else ""
    out_name = f"{job_id}_bgm_{body.mood}{ts_str}.mp4"
    out_path = os.path.join(OUTPUT_DIR, out_name)

    success = _mix_music(
        source_path, music_path, out_path,
        body.start_time, body.end_time, body.volume,
    )

    if not success:
        raise HTTPException(status_code=500, detail="Müzik karıştırılamadı")

    download_url = f"/outputs/{out_name}"
    output_url   = f"{PUBLIC_BASE_URL}{download_url}"

    logger.info(f"[music] Tamamlandı: {mood_label} → {out_name}")

    return {
        "status": "ok",
        "mood": body.mood,
        "mood_label": mood_label,
        "start_time": body.start_time,
        "end_time": body.end_time,
        "volume": body.volume,
        "download_url": download_url,
        "output_url": output_url,
    }


@router.get("/music/list")
def list_music():
    """Mevcut müzik ruh hallerini ve dosya durumunu döndür."""
    result = []
    for mood, info in MUSIC_MOODS.items():
        mood_dir = os.path.join(MUSIC_DIR, info["dir"])
        has_file = False
        if os.path.isdir(mood_dir):
            for ext in ("*.mp3", "*.wav", "*.m4a", "*.mp3.mp3"):
                if glob.glob(os.path.join(mood_dir, ext)):
                    has_file = True
                    break
        result.append({"mood": mood, "label": info["label"], "available": has_file})
    return {"moods": result}
