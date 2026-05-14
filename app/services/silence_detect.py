# app/services/silence_detect.py
import re
import subprocess
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class SilenceSegment:
    start: float
    end: float


_SILENCE_START_RE = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9.]+)")


def detect_silences(
    video_path: str,
    noise_db: int = -35,
    min_silence_dur: float = 0.35,
) -> List[SilenceSegment]:
    """
    Uses ffmpeg silencedetect to find silence segments.
    Returns list of (start,end) in seconds.
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-i",
        video_path,
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_silence_dur}",
        "-f",
        "null",
        "NUL" if _is_windows() else "/dev/null",
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    text = proc.stderr or ""  # silencedetect logs go to stderr

    starts: List[float] = []
    ends: List[float] = []
    for line in text.splitlines():
        m1 = _SILENCE_START_RE.search(line)
        if m1:
            starts.append(float(m1.group(1)))
            continue
        m2 = _SILENCE_END_RE.search(line)
        if m2:
            ends.append(float(m2.group(1)))

    # Pair starts/ends safely
    segs: List[SilenceSegment] = []
    i = j = 0
    while i < len(starts) and j < len(ends):
        if ends[j] <= starts[i]:
            j += 1
            continue
        segs.append(SilenceSegment(start=starts[i], end=ends[j]))
        i += 1
        j += 1

    # Filter weird/zero segments
    segs = [s for s in segs if s.end > s.start + 0.05]
    return segs


def _is_windows() -> bool:
    import os
    return os.name == "nt"