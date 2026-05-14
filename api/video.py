from fastapi import APIRouter, HTTPException, UploadFile, File
from pathlib import Path
import uuid, shutil, subprocess, json

from core.schemas import EditPlanV1
from core.compiler import plan_to_cutlist
from core.render import render_cutlist_concat

router = APIRouter(prefix="/api/video", tags=["video"])

BASE_DIR   = Path(__file__).resolve().parents[1]
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
JOBS_DIR   = BASE_DIR / "jobs"

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)


def get_video_duration(path: str) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
            capture_output=True, text=True
        )
        data = json.loads(result.stdout)
        return round(float(data["format"]["duration"]), 2)
    except Exception:
        return 0.0


@router.get("/health")
def health():
    return {"status": "ok", "service": "video"}


@router.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    print(f"[upload] filename: {file.filename}")
    print(f"[upload] content_type: {file.content_type}")

    job_id = str(uuid.uuid4())[:8]
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
    save_path = job_dir / f"input{suffix}"

    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    duration = get_video_duration(str(save_path))

    return {
        "job_id": job_id,
        "duration": duration,
        "filename": file.filename,
        "path": str(save_path),
    }


@router.post("/process/{job_id}")
def process_video(job_id: str):
    job_dir = JOBS_DIR / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job_id,
        "status": "ok",
        "insights": {
            "silence_segments_count": 0,
            "silence_seconds": 0,
        },
        "tempo_analysis": {"message": ""},
    }


@router.post("/render/{job_id}")
def render_video(job_id: str):
    job_dir = JOBS_DIR / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="Job not found")

    input_path = None
    for ext in [".mp4", ".mov", ".avi", ".mkv", ".webm"]:
        for f in job_dir.iterdir():
            if f.suffix.lower() == ext:
                input_path = f
                break

    if not input_path:
        raise HTTPException(status_code=422, detail="No video found")

    output_dir = job_dir / "output"
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / f"{job_id}_final.mp4"
    shutil.copy2(str(input_path), str(output_path))

    return {
        "job_id": job_id,
        "status": "ok",
        "download_url": f"/jobs/{job_id}/output/{job_id}_final.mp4",
    }


@router.post("/render_from_plan/{job_id}")
def render_from_plan(job_id: str, plan: EditPlanV1):
    if plan.job_id != job_id:
        raise HTTPException(status_code=400, detail="job_id mismatch")

    input_path = UPLOAD_DIR / f"{job_id}.mp4"
    if not input_path.exists():
        raise HTTPException(status_code=404, detail=f"Input video not found: {input_path}")

    cutlist = plan_to_cutlist(plan)
    if not cutlist.segments:
        raise HTTPException(status_code=400, detail="Plan produced empty cutlist")

    workdir = OUTPUT_DIR / "tmp" / f"{job_id}_{uuid.uuid4().hex[:8]}"
    workdir.mkdir(parents=True, exist_ok=True)
    out_name = f"{job_id}_plan.mp4"
    output_path = OUTPUT_DIR / out_name

    try:
        render_cutlist_concat(
            input_video_path=str(input_path),
            cutlist=cutlist,
            output_path=str(output_path),
            workdir=str(workdir),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {
        "ok": True,
        "job_id": job_id,
        "output_file": out_name,
        "download_url": f"/outputs/{out_name}",
    }