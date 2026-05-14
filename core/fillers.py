"""
subtitles.py — Generate SRT from Whisper segments and optionally burn into video.
"""

import subprocess
import os
from typing import List, Dict, Any


def seconds_to_srt_time(seconds: float) -> str:
    """Convert float seconds to SRT timestamp format HH:MM:SS,mmm"""
    assert seconds >= 0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def segments_to_srt(segments: List[Dict[str, Any]]) -> str:
    """
    Convert Whisper transcript segments to SRT string.
    Each segment: {'start': float, 'end': float, 'text': str}
    """
    lines = []
    for i, seg in enumerate(segments, start=1):
        text = seg.get("text", "").strip()
        if not text:
            continue
        start_ts = seconds_to_srt_time(seg["start"])
        end_ts = seconds_to_srt_time(seg["end"])
        lines.append(f"{i}\n{start_ts} --> {end_ts}\n{text}\n")
    return "\n".join(lines)


def write_srt_file(segments: List[Dict[str, Any]], output_path: str) -> str:
    """Write SRT to file and return path."""
    srt_content = segments_to_srt(segments)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(srt_content)
    return output_path


def burn_subtitles(
    input_video: str,
    srt_path: str,
    output_video: str,
    font_size: int = 20,
    font_color: str = "white",
    border_style: int = 3,
    margin_v: int = 30,
) -> str:
    """
    Burn SRT subtitles into video using ffmpeg.
    Returns output_video path.
    """
    # Escape path for ffmpeg subtitles filter (Windows-safe)
    srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")

    subtitle_filter = (
        f"subtitles='{srt_escaped}'"
        f":force_style='FontSize={font_size},"
        f"PrimaryColour=&H00FFFFFF,"
        f"OutlineColour=&H00000000,"
        f"BorderStyle={border_style},"
        f"MarginV={margin_v},"
        f"Alignment=2'"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", input_video,
        "-vf", subtitle_filter,
        "-c:a", "copy",
        "-preset", "fast",
        output_video
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg subtitle burn failed:\n{result.stderr}")
    return output_video
