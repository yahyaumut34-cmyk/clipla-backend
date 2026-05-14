"""
core/shorts_render.py — Cut short clips from original video using ffmpeg.

Uses -ss / -to for fast seeking. Re-encodes to ensure clean cuts
at exact timestamps (avoids keyframe issues).
"""

import os
import subprocess
from typing import List, Optional

from core.shorts import ShortClip


def render_short_clip(
    input_video: str,
    clip: ShortClip,
    output_path: str,
    add_fade: bool = True,
    fade_duration: float = 0.3,
) -> str:
    """
    Cut a single short clip from input_video between clip.start and clip.end.
    Optionally adds a fade-in at start and fade-out at end.
    Returns output_path on success, raises RuntimeError on failure.
    """
    duration = clip.end - clip.start

    if add_fade and duration > fade_duration * 3:
        fade_out_start = duration - fade_duration
        vf = (
            f"fade=t=in:st=0:d={fade_duration},"
            f"fade=t=out:st={fade_out_start:.3f}:d={fade_duration}"
        )
        af = (
            f"afade=t=in:st=0:d={fade_duration},"
            f"afade=t=out:st={fade_out_start:.3f}:d={fade_duration}"
        )
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{clip.start:.4f}",
            "-to", f"{clip.end:.4f}",
            "-i", input_video,
            "-vf", vf,
            "-af", af,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{clip.start:.4f}",
            "-to", f"{clip.end:.4f}",
            "-i", input_video,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed for clip {clip.start:.2f}–{clip.end:.2f}:\n{result.stderr}"
        )

    return output_path


def render_all_shorts(
    input_video: str,
    clips: List[ShortClip],
    output_dir: str,
    job_id: str,
    add_fade: bool = True,
) -> List[ShortClip]:
    """
    Render all selected short clips to output_dir.
    Mutates clip.path on success.
    Returns the clips list with paths filled in.
    """
    os.makedirs(output_dir, exist_ok=True)

    for i, clip in enumerate(clips, start=1):
        filename = f"output_short_{i}.mp4"
        output_path = os.path.join(output_dir, filename)

        try:
            render_short_clip(
                input_video=input_video,
                clip=clip,
                output_path=output_path,
                add_fade=add_fade,
            )
            clip.path = output_path
        except RuntimeError as e:
            clip.path = None
            print(f"[shorts_render] WARNING: clip {i} failed: {e}")

    return clips
