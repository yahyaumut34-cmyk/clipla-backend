# app/services/auto_edit_orchestrator.py
from typing import List

from app.core.edit_plan_schema import (
    EditPlanV1, Constraints, ThemeInfo, AudioConfig, ScoreInfo,
    KeepSegment, CutCandidate, SilenceCandidate
)
from app.services.silence_detect import detect_silences
from app.services.reaction_gate import decide_keep_reaction_silences
from app.services.keep_segments import build_keep_segments_theme_first
from app.services.auto_edit_apply import apply_edit_plan_ffmpeg

import subprocess
import json
import os


def run_auto_edit_v1(
    input_video_path: str,
    music_dir: str,
    output_dir: str,
) -> tuple[EditPlanV1, str]:
    """
    Returns (plan, output_video_path)

    music_dir should be: .../backend/music
      music/
        energetic/*.mp3
        rhythmic/*.mp3
        slow/*.mp3
        calm/*.mp3
        minimal/*.mp3
    """

    duration = _get_duration_sec(input_video_path)

    # 1) silence detect (candidates)
    silences = detect_silences(input_video_path, noise_db=-35, min_silence_dur=0.35)
    silence_pairs = [(s.start, s.end) for s in silences]

    # 2) reaction keep decisions
    decisions = decide_keep_reaction_silences(input_video_path, silence_pairs)

    silence_candidates = [
        SilenceCandidate(start=d.start, end=d.end, keep=d.keep, reason=d.reason)
        for d in decisions
    ]

    # 3) keep_segments (theme-first fallback for now)
    keep = build_keep_segments_theme_first(duration)
    keep_segments = [KeepSegment(start=s.start, end=s.end, reason=s.reason) for s in keep]

    # (Optional) cut_candidates v1 (we keep it empty for now; later we’ll add filler cuts)
    cut_candidates: List[CutCandidate] = []

    # 4) score v1 (placeholder; later we’ll compute real)
    score = ScoreInfo(
        success_probability=68,
        confidence="medium",
        reasons=["Tema korunuyor", "Reaksiyon sessizlikleri korunuyor"]
    )

    # ✅ NEW: normalize tag to one of our 5 categories
    music_tag = _normalize_music_tag("energetic")  # MVP default; later AI will decide

    plan = EditPlanV1(
        preset="vertical_short",
        target_platform="shorts_reels_tiktok",
        target_duration_sec=60,
        constraints=Constraints(
            preserve_theme=True,
            no_mid_sentence_cuts=True,
            allow_over_target_if_needed=True,
            keep_reaction_silences=True
        ),
        theme=ThemeInfo(
            one_sentence="(v1) Tema: sonuç/mesaj korunacak şekilde kısaltma.",
            payoff_reason="(v1) Payoff: sondaki sonuç segmenti korunur."
        ),
        keep_segments=keep_segments,
        cut_candidates=cut_candidates,
        silence_candidates=silence_candidates,
        audio=AudioConfig(
            music_tag=music_tag,          # ✅ was energetic_low
            music_ducking_db=-10,
            loudness="loudnorm"
        ),
        score=score
    )

    # Optional safety check: make sure music folder exists for tag
    _assert_music_folder(music_dir, music_tag)

    out_mp4 = apply_edit_plan_ffmpeg(plan, input_video_path, music_dir, output_dir)
    return plan, out_mp4


def _normalize_music_tag(tag: str) -> str:
    """
    Clipla V1 music categories (fixed):
    energetic, rhythmic, slow, calm, minimal
    """
    t = (tag or "").strip().lower()

    mapping = {
        "energetic_low": "energetic",
        "energetic_high": "energetic",
        "energetic": "energetic",
        "hareketli": "energetic",

        "rhythmic": "rhythmic",
        "ritmik": "rhythmic",

        "slow": "slow",
        "yavas": "slow",
        "yavaş": "slow",

        "calm": "calm",
        "sakin": "calm",
        "sakinlestirici": "calm",
        "sakinleştirici": "calm",

        "minimal": "minimal",
        "min": "minimal",
    }

    return mapping.get(t, "energetic")


def _assert_music_folder(music_dir: str, tag: str) -> None:
    folder = os.path.join(music_dir, tag)
    if os.path.isdir(folder):
        # ok
        return

    # legacy fallback: tag.mp3
    legacy = os.path.join(music_dir, f"{tag}.mp3")
    if os.path.exists(legacy):
        return

    raise FileNotFoundError(
        f"Music folder/file not found for tag='{tag}'.\n"
        f"Expected folder: {folder}\n"
        f"Or legacy file: {legacy}\n"
        f"Make sure your music library is: {music_dir}\\{tag}\\*.mp3"
    )


def _get_duration_sec(video_path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        video_path
    ]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {p.stderr}")
    data = json.loads(p.stdout)
    return float(data["format"]["duration"])