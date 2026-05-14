# app/services/keep_segments.py
from dataclasses import dataclass
from typing import List, Tuple


@dataclass
class KeepSeg:
    start: float
    end: float
    reason: str


def build_keep_segments_theme_first(
    duration_sec: float,
    # later: transcript, key moments, etc.
) -> List[KeepSeg]:
    """
    v1 fallback:
    - Always keep hook: first 0-4s
    - Always keep payoff: last 6s (or last 20% if short)
    - Core: middle 6s (rough)
    This keeps 'meaning' more often than you'd expect until transcript/LLM is wired.
    """
    hook_end = min(4.0, duration_sec)
    payoff_len = min(6.0, max(2.0, duration_sec * 0.2))
    payoff_start = max(0.0, duration_sec - payoff_len)

    core_len = min(6.0, max(2.5, duration_sec * 0.15))
    core_start = max(hook_end, (duration_sec * 0.5) - core_len / 2)
    core_end = min(payoff_start, core_start + core_len)

    keep = [
        KeepSeg(0.0, hook_end, "hook"),
    ]
    if core_end > core_start + 0.3:
        keep.append(KeepSeg(core_start, core_end, "core_message_fallback"))
    if payoff_start < duration_sec:
        keep.append(KeepSeg(payoff_start, duration_sec, "payoff_fallback"))
    return _merge_overlaps(keep)


def _merge_overlaps(segs: List[KeepSeg]) -> List[KeepSeg]:
    segs = sorted(segs, key=lambda x: x.start)
    out: List[KeepSeg] = []
    for s in segs:
        if not out or s.start > out[-1].end:
            out.append(s)
        else:
            out[-1].end = max(out[-1].end, s.end)
            out[-1].reason = out[-1].reason + "+" + s.reason
    return out