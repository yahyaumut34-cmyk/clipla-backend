"""
core/render.py - Video render functions
"""

import os
import subprocess
import shutil
from pathlib import Path


class CutSegment:
    def __init__(self, start: float, end: float):
        self.start = start
        self.end = end


class CutList:
    def __init__(self, segments=None):
        self.segments = segments or []


def render_cutlist_concat(
    input_video_path: str,
    cutlist: CutList,
    output_path: str,
    workdir: str,
) -> str:
    """
    Cut and concatenate video segments using ffmpeg.
    Falls back to simple copy if no meaningful cuts.
    """
    os.makedirs(workdir, exist_ok=True)

    segments = [s for s in cutlist.segments if s.end > s.start]

    if not segments:
        shutil.copy2(input_video_path, output_path)
        return output_path

    # Single segment covering full video — just copy
    if len(segments) == 1 and segments[0].start == 0 and segments[0].end >= 9999:
        shutil.copy2(input_video_path, output_path)
        return output_path

    # Get video duration
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", input_video_path],
            capture_output=True, text=True
        )
        import json
        duration = float(json.loads(result.stdout)["format"]["duration"])
    except Exception:
        duration = 9999.0

    # Cut each segment — re-encode for compatibility
    segment_files = []
    for i, seg in enumerate(segments):
        end = min(seg.end, duration)
        start = seg.start
        if end <= start:
            continue
        seg_path = os.path.join(workdir, f"seg_{i:04d}.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start:.4f}",
            "-i", input_video_path,
            "-t", f"{end - start:.4f}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-ar", "44100",
            "-movflags", "+faststart",
            seg_path
        ]
        subprocess.run(cmd, capture_output=True)
        if os.path.exists(seg_path):
            segment_files.append(seg_path)

    if not segment_files:
        shutil.copy2(input_video_path, output_path)
        return output_path

    if len(segment_files) == 1:
        shutil.copy2(segment_files[0], output_path)
        return output_path

    # Concat list
    concat_list = os.path.join(workdir, "concat.txt")
    with open(concat_list, "w") as f:
        for sf in segment_files:
            f.write(f"file '{sf}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list,
        "-c", "copy",
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        # fallback: re-encode concat as well
        cmd[-1] = output_path
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-ar", "44100",
            "-movflags", "+faststart",
            output_path
        ]
        subprocess.run(cmd, capture_output=True)
    return output_path
