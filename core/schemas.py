from pydantic import BaseModel, Field
from typing import Optional, Literal


class EditPlanV1(BaseModel):
    cut_silence: bool = True
    remove_fillers: bool = True
    add_subtitles: bool = False
    subtitle_mode: Literal["srt", "burned"] = "burned"
    add_music: bool = False
    music_style: Optional[str] = None
    target_duration_sec: Optional[int] = None
    output_aspect: Optional[Literal["9:16", "16:9", "1:1"]] = None
    silence_threshold_db: float = -35.0
    silence_min_duration_sec: float = 0.5
    filler_words: list[str] = Field(default_factory=lambda: [
        "ıı", "ııı", "şey", "yani", "eee", "hmm", "hımm",
        "şey işte", "böyle", "anladın mı"
    ])


class AutoEditRequest(BaseModel):
    edit_plan: Optional[EditPlanV1] = None
    voice_command: Optional[str] = None


class AutoEditResponse(BaseModel):
    job_id: str
    status: str
    output_url: Optional[str] = None
    output_path: Optional[str] = None
    srt_url: Optional[str] = None
    duration_sec: Optional[float] = None
    cuts_applied: int = 0
    fillers_removed: int = 0
    error: Optional[str] = None
