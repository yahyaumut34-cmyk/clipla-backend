from pydantic import BaseModel, Field, model_validator
from typing import List, Dict, Any, Optional, Literal
from datetime import datetime
from enum import Enum


class JobStatus(str, Enum):
    UPLOADED   = "uploaded"
    PROCESSING = "processing"
    COMPLETED  = "completed"
    FAILED     = "failed"


class Platform(str, Enum):
    YOUTUBE        = "youtube"
    YOUTUBE_SHORTS = "youtube_shorts"
    TIKTOK         = "tiktok"


class EditAction(BaseModel):
    action:     Literal["cut", "speed", "audio"]
    start_time: float = Field(..., ge=0)
    end_time:   float = Field(..., ge=0)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    reason:     str   = Field(default="", max_length=200)

    @model_validator(mode="after")
    def check_times(self):
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be > start_time")
        if self.action == "speed":
            f = self.parameters.get("factor", 1.0)
            if not (0.25 <= float(f) <= 4.0):
                raise ValueError("speed factor must be 0.25-4.0")
        return self


class EditPlan(BaseModel):
    edits:                List[EditAction] = Field(default_factory=list)
    estimated_duration:   float            = Field(default=0.0, ge=0)
    optimization_summary: str              = Field(default="")
    quality_score:        int              = Field(default=75, ge=0, le=100)
    validation_notes:     List[str]        = Field(default_factory=list)


class ClaudeDirective(BaseModel):
    intent:          str             = "trim_silence"
    target_sec:      Optional[int]   = None
    platform:        Optional[str]   = None
    speed_factor:    Optional[float] = None
    keep_intro_sec:  float           = 10.0
    keep_outro_sec:  float           = 15.0
    remove_silence:  bool            = True
    aggressive_cuts: bool            = False
    summary:         str             = ""
    error:           Optional[str]   = None


class ClaudeAnalysis(BaseModel):
    hook_quality:     Literal["strong", "medium", "weak"] = "medium"
    hook_suggestion:  str       = ""
    best_moment:      str       = ""
    cut_suggestion:   str       = ""
    platform_fit:     str       = ""
    viral_score:      int       = Field(default=50, ge=0, le=100)
    one_line_summary: str       = ""
    suggestions:      List[str] = Field(default_factory=list)
    error:            Optional[str] = None


class VideoScores(BaseModel):
    info_density: float = 0.0
    pacing_score: float = 0.0
    silence_risk: float = 0.0
    kept_sec:     float = 0.0
    cuts_per_min: float = 0.0
    word_count:   int   = 0


class AutoEditRequest(BaseModel):
    command_text:        str           = ""
    platform:            Optional[str] = None
    target_duration_sec: Optional[int] = None


class DSLAction(BaseModel):
    type:       Literal["cut", "speed", "audio", "trim", "keep"]
    target:     Literal["silence", "low-energy", "high-energy", "segment", "all"]
    parameters: Dict[str, Any] = Field(default_factory=dict)
    confidence: float          = Field(default=1.0, ge=0, le=1)


class VoiceDSL(BaseModel):
    version:    str             = "1.0"
    confidence: float           = Field(..., ge=0.3, le=1)
    actions:    List[DSLAction] = Field(default_factory=list)
    metadata:   Dict[str, Any]  = Field(default_factory=dict)


class Job(BaseModel):
    job_id:       str
    filename:     str
    video_path:   str
    status:       JobStatus
    command_text: Optional[str]      = None
    platform:     Optional[Platform] = None
    error:        Optional[str]      = None
    created_at:   datetime
    completed_at: Optional[datetime] = None
