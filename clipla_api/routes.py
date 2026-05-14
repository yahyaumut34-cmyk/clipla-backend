from fastapi import APIRouter, UploadFile, File, HTTPException, Body
from pydantic import BaseModel
from typing import Optional, List
import os
import uuid
import shutil

from agents.pipeline import run_edit_pipeline

router = APIRouter(prefix="/api/video", tags=["video"])

# Proje kökü: ...\backend
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # ...\backend\clipla_api
BASE_DIR = os.path.dirname(BASE_DIR)                   # ...\backend

UPLOAD_DIR = os.path.join(BASE_DIR, "storage", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _find_uploaded_file(job_id: str) -> Optional[str]:
    """
    storage/uploads klasöründe job_id ile başlayan dosyayı bulur.
    Örn: {job_id}_deneme_video.mp4
    """
    prefix = f"{job_id}_"
    try:
        for name in os.listdir(UPLOAD_DIR):
            if name.startswith(prefix):
                return os.path.join(UPLOAD_DIR, name)
    except FileNotFoundError:
        return None
    return None


@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "multi-agent-video-editing",
        "agents": ["CommandAgent", "AnalysisAgent", "StrategyAgent", "QCAgent"],
    }


@router.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    if file is None:
        raise HTTPException(status_code=400, detail="No file received")

    filename = file.filename or ""
    ext = os.path.splitext(filename.lower())[1]
    if ext not in [".mp4", ".mov", ".mkv"]:
        raise HTTPException(status_code=400, detail="Unsupported file type")

    job_id = str(uuid.uuid4())
    safe_name = filename.replace(" ", "_")
    saved_name = f"{job_id}_{safe_name}"
    save_path = os.path.join(UPLOAD_DIR, saved_name)

    try:
        with open(save_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"File save failed: {e}")

    return {
        "job_id": job_id,
        "dosya_adi": filename,
        "kaydedilen_sey": saved_name,
        "size_bytes": os.path.getsize(save_path),
        "durum": "yüklendi",
    }


@router.get("/job/{job_id}")
async def job_status(job_id: str):
    path = _find_uploaded_file(job_id)
    if not path:
        raise HTTPException(status_code=404, detail="Job file not found")

    return {
        "job_id": job_id,
        "durum": "yüklendi",
        "dosya": os.path.basename(path),
        "mesaj": "İşlemeye hazır",
    }


@router.get("/analyze/{job_id}")
async def analyze_and_score(job_id: str):
    """
    Şimdilik 'gerçek' video analizi değil:
    - deterministik, hızlı bir heuristik skor üretir
    - yatırımcı demosu için: algorithm_score + öneriler verir
    Sonra burayı gerçek analiz pipeline'a bağlayacağız.
    """
    path = _find_uploaded_file(job_id)
    if not path:
        raise HTTPException(status_code=404, detail="Job file not found")

    size_bytes = os.path.getsize(path)
    size_mb = size_bytes / (1024 * 1024)

    # Basit skor mantığı (deterministik)
    score = 60
    if 20 <= size_mb <= 200:
        score += 18
    elif size_mb < 20:
        score -= 10
    else:
        score -= 6

    # Dosya uzantısına küçük bonus
    ext = os.path.splitext(path.lower())[1]
    if ext == ".mp4":
        score += 6
    elif ext == ".mov":
        score += 3

    score = max(0, min(100, int(round(score))))

    if score >= 80:
        label = "çok iyi"
    elif score >= 65:
        label = "iyi"
    elif score >= 50:
        label = "orta"
    else:
        label = "zayıf"

    recommendations: List[str] = [
        "İlk 2-3 saniyede en güçlü anı göster (hook).",
        "Sessiz/boş kısımları kes, tempoyu artır.",
        "Jump-cut ile gereksiz duraksamaları temizle.",
        "Altyazı ekle ve ana kelimeleri vurgula.",
        "Videoyu 8-20 sn arası daha 'tüketilebilir' hale getir (Shorts/Reels).",
    ]

    return {
        "job_id": job_id,
        "algorithm_score": score,
        "label": label,
        "size_mb": round(size_mb, 2),
        "recommendations": recommendations,
        "next_step": "Komut satırını ekleyip /process ile edit plan üreteceğiz.",
    }


class ProcessBody(BaseModel):
    command: Optional[str] = None
    transcript: Optional[str] = None  # ileride Whisper bunu dolduracak


@router.post("/process/{job_id}")
def process_video(job_id: str, body: ProcessBody = Body(default=ProcessBody())):
    # job_id gerçekten var mı? (upload edilmiş dosya var mı?)
    path = _find_uploaded_file(job_id)
    if not path:
        raise HTTPException(status_code=404, detail="Job file not found")

    command = (body.command or "").strip()
    if not command:
        command = "Bu videoyu YouTube Shorts için daha viral, hızlı ve akıcı hale getir."

    transcript = body.transcript

    try:
        result = run_edit_pipeline(
            job_id=job_id,
            command=command,
            transcript=transcript,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Process failed: {e}")