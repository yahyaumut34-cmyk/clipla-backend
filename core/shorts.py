"""
api/shorts.py — POST /api/shorts/{job_id}

Loads job video + transcript, runs shorts selection + render,
returns list of short clips with URLs.
"""

import os
import json
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.shorts import generate_shorts_plan, ShortClip
from core.shorts_render import render_all_shorts
from core.render import get_duration  # reuse existing helper

router = APIRouter(prefix="/api", tags=["shorts"])

JOBS_DIR = os.environ.get("JOBS_DIR", "jobs")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000")


class ShortsRequest(BaseModel):
    add_fade: bool = True
    count_min: int = 3
    count_max: int = 5


class ShortClipResponse(BaseModel):
    index: int
    start: float
    end: float
    duration: float
    score: float
    url: Optional[str] = None
    path: Optional[str] = None
    error: Optional[str] = None


class ShortsResponse(BaseModel):
    job_id: str
    status: str
    shorts: List[ShortClipResponse]
    total_clips: int
    error: Optional[str] = None


def find_job_video(job_dir: str) -> Optional[str]:
    for ext in [".mp4", ".mov", ".avi", ".mkv", ".webm"]:
        for f in Path(job_dir).iterdir():
            if f.suffix.lower() == ext and "short" not in f.name and "final" not in f.name:
                return str(f)
    return None


def load_transcript_segments(job_dir: str) -> Optional[list]:
    for fname in ["transcript.json", "transcription.json", "whisper_output.json"]:
        path = os.path.join(job_dir, fname)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("segments") or data.get("words") or []
    return None


def clip_to_url(clip: ShortClip, job_id: str) -> Optional[str]:
    if not clip.path:
        return None
    filename = os.path.basename(clip.path)
    return f"{PUBLIC_BASE_URL}/outputs/{job_id}/shorts/{filename}"


@router.post("/shorts/{job_id}", response_model=ShortsResponse)
async def generate_shorts(job_id: str, body: ShortsRequest = ShortsRequest()):
    job_dir = os.path.join(JOBS_DIR, job_id)
    if not os.path.isdir(job_dir):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    input_video = find_job_video(job_dir)
    if not input_video:
        raise HTTPException(status_code=422, detail=f"No input video found in {job_dir}")

    segments = load_transcript_segments(job_dir)
    if not segments:
        raise HTTPException(status_code=422, detail="No transcript found. Run transcription first.")

    try:
        total_duration = get_duration(input_video)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not read video duration: {e}")

    try:
        clips = generate_shorts_plan(segments=segments, total_duration=total_duration)
    except Exception as e:
        return ShortsResponse(job_id=job_id, status="error", shorts=[], total_clips=0,
                              error=f"Shorts planning failed: {e}")

    if not clips:
        return ShortsResponse(job_id=job_id, status="no_clips", shorts=[], total_clips=0,
                              error="No suitable clips found.")

    shorts_output_dir = os.path.join(job_dir, "shorts")
    try:
        clips = render_all_shorts(
            input_video=input_video,
            clips=clips,
            output_dir=shorts_output_dir,
            job_id=job_id,
            add_fade=body.add_fade,
        )
    except Exception as e:
        return ShortsResponse(job_id=job_id, status="error", shorts=[], total_clips=0,
                              error=f"Render failed: {e}")

    clip_responses = []
    for i, clip in enumerate(clips, start=1):
        clip_responses.append(ShortClipResponse(
            index=i,
            start=round(clip.start, 3),
            end=round(clip.end, 3),
            duration=round(clip.duration, 2),
            score=clip.score,
            url=clip_to_url(clip, job_id),
            path=clip.path,
            error=None if clip.path else "render_failed",
        ))

    return ShortsResponse(job_id=job_id, status="done", shorts=clip_responses,
                          total_clips=len(clips))


@router.post("/shorts/{job_id}/plan")
async def shorts_plan_only(job_id: str):
    """Score-only preview: returns selected clip ranges without rendering."""
    job_dir = os.path.join(JOBS_DIR, job_id)
    if not os.path.isdir(job_dir):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    input_video = find_job_video(job_dir)
    if not input_video:
        raise HTTPException(status_code=422, detail="No video found")

    segments = load_transcript_segments(job_dir)
    if not segments:
        raise HTTPException(status_code=422, detail="No transcript found")

    total_duration = get_duration(input_video)
    clips = generate_shorts_plan(segments=segments, total_duration=total_duration)

    return {
        "job_id": job_id,
        "total_duration": round(total_duration, 2),
        "clips_selected": len(clips),
        "clips": [
            {
                "index": i,
                "start": round(c.start, 3),
                "end": round(c.end, 3),
                "duration": round(c.duration, 2),
                "score": c.score,
                "segment_indices": c.segment_indices,
            }
            for i, c in enumerate(clips, start=1)
        ],
    }
