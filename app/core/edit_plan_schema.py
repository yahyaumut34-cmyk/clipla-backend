# app/core/edit_plan_schema.py

from typing import List, Literal, Optional
from pydantic import BaseModel


class KeepSegment(BaseModel):
    start: float
    end: float
    reason: str


class CutCandidate(BaseModel):
    start: float
    end: float
    reason: str


class SilenceCandidate(BaseModel):
    start: float
    end: float
    keep: bool
    reason: str


class AudioConfig(BaseModel):
    music_tag: str
    music_ducking_db: int
    loudness: Literal["loudnorm"]


class ScoreInfo(BaseModel):
    success_probability: int  # 0-100
    confidence: Literal["low", "medium", "high"]
    reasons: List[str]


class Constraints(BaseModel):
    preserve_theme: bool = True
    no_mid_sentence_cuts: bool = True
    allow_over_target_if_needed: bool = True
    keep_reaction_silences: bool = True


class ThemeInfo(BaseModel):
    one_sentence: str
    payoff_reason: str


class EditPlanV1(BaseModel):
    preset: Literal["vertical_short", "youtube_long"]
    target_platform: str
    target_duration_sec: int

    constraints: Constraints
    theme: ThemeInfo

    keep_segments: List[KeepSegment]
    cut_candidates: List[CutCandidate]
    silence_candidates: List[SilenceCandidate]

    audio: AudioConfig
    score: ScoreInfo