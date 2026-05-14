# app/services/reaction_gate.py
import subprocess
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class CandidateDecision:
    start: float
    end: float
    keep: bool
    reason: str


def decide_keep_reaction_silences(
    video_path: str,
    silence_segments: List[Tuple[float, float]],
    motion_threshold: float = 0.015,
) -> List[CandidateDecision]:
    """
    Very pragmatic v1:
    - For each silence segment, sample a low-fps stream and estimate "motion energy".
    - If motion is above threshold -> keep (likely reaction/visual value).
    - Else -> cut.
    """
    decisions: List[CandidateDecision] = []
    for (s, e) in silence_segments:
        motion = _estimate_motion_energy(video_path, s, e)
        if motion >= motion_threshold:
            decisions.append(CandidateDecision(s, e, True, "visual_motion"))
        else:
            decisions.append(CandidateDecision(s, e, False, "low_visual_activity"))
    return decisions


def _estimate_motion_energy(video_path: str, start: float, end: float) -> float:
    """
    Uses ffmpeg 'select' + 'tblend' trick to get a rough motion metric.
    We avoid heavy CV libs in v1. This is cheap and surprisingly useful.
    """
    dur = max(0.05, end - start)
    # Downscale + low fps to speed up
    vf = (
        f"trim=start={start}:duration={dur},"
        "fps=6,scale=160:-1,"
        "tblend=all_mode=difference,format=gray,"
        "signalstats"
    )
    cmd = [
        "ffmpeg", "-hide_banner",
        "-ss", str(start),
        "-i", video_path,
        "-t", str(dur),
        "-vf", vf,
        "-f", "null", "NUL" if _is_windows() else "/dev/null",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    text = proc.stderr or ""
    # signalstats prints YAVG sometimes; we use a simple parse fallback
    # We'll look for "YAVG:" and take last seen value
    yavg = None
    for line in text.splitlines():
        if "YAVG:" in line:
            try:
                part = line.split("YAVG:")[1].strip()
                val = part.split()[0]
                yavg = float(val)
            except Exception:
                pass
    # Normalize (0..255) -> (0..1)
    if yavg is None:
        return 0.0
    return max(0.0, min(1.0, yavg / 255.0))


def _is_windows() -> bool:
    import os
    return os.name == "nt"