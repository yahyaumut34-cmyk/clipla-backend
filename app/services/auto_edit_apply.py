# app/services/auto_edit_apply.py
import os
import random
import subprocess
import uuid
from typing import List, Tuple

from app.core.edit_plan_schema import EditPlanV1


def apply_edit_plan_ffmpeg(
    plan: EditPlanV1,
    input_video_path: str,
    music_library_dir: str,
    output_dir: str,
) -> str:
    """
    v1.1: keep-first assembly + loudnorm + music mix (loop + trim + fade + duck).
    - music_library_dir should contain subfolders: energetic/rhythmic/slow/calm/minimal
    - each subfolder should contain 3-4 mp3 variations
    Returns output mp4 path.
    """
    os.makedirs(output_dir, exist_ok=True)

    # 1) Resolve music file from tag (random pick among variations)
    music_path = _pick_music_file(music_library_dir, plan.audio.music_tag)
    if not os.path.exists(music_path):
        raise FileNotFoundError(
            f"Music not found for tag={plan.audio.music_tag}. "
            f"Looked under: {os.path.join(music_library_dir, plan.audio.music_tag)}"
        )

    # 2) Build keep timeline (seconds)
    keep = [(s.start, s.end) for s in plan.keep_segments]
    if not keep:
        raise ValueError("EditPlan has no keep_segments. Theme preservation requires keep_segments.")

    # 3) Render "keeps" -> temp video
    temp_video = os.path.join(output_dir, f"tmp_keeps_{uuid.uuid4().hex}.mp4")
    _render_keeps_concat(input_video_path, keep, temp_video)

    # 4) Loudnorm + music ducking mix -> final
    out_path = os.path.join(output_dir, f"clipla_{uuid.uuid4().hex}.mp4")
    _mix_music_and_loudnorm(
        video_path=temp_video,
        music_path=music_path,
        out_path=out_path,
        duck_db=getattr(plan.audio, "music_ducking_db", -10),
    )

    # cleanup
    try:
        os.remove(temp_video)
    except Exception:
        pass

    return out_path


def _render_keeps_concat(input_video: str, keep: List[Tuple[float, float]], out_path: str) -> None:
    """
    Fast & reliable: trim each keep into a segment file, then concat demuxer.
    """
    workdir = os.path.dirname(out_path)
    seg_files: List[str] = []

    for idx, (start, end) in enumerate(keep):
        dur = max(0.05, end - start)
        seg_path = os.path.join(workdir, f"seg_{idx}_{uuid.uuid4().hex}.mp4")
        cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-ss", str(start),
            "-i", input_video,
            "-t", str(dur),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "160k",
            seg_path
        ]
        _run(cmd)
        seg_files.append(seg_path)

    # concat list file
    list_path = os.path.join(workdir, f"concat_{uuid.uuid4().hex}.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in seg_files:
            f.write(f"file '{p.replace(\"'\", \"\\\\'\")}'\n")

    cmd_concat = [
        "ffmpeg", "-hide_banner", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_path,
        "-c", "copy",
        out_path
    ]
    _run(cmd_concat)

    # cleanup
    for p in seg_files:
        try:
            os.remove(p)
        except Exception:
            pass
    try:
        os.remove(list_path)
    except Exception:
        pass


def _mix_music_and_loudnorm(video_path: str, music_path: str, out_path: str, duck_db: int = -10) -> None:
    """
    - loudnorm the voice
    - loop background music to match video duration
    - fade in/out music
    - duck music under voice using sidechaincompress
    """
    dur = _probe_duration_seconds(video_path)
    if dur <= 0:
        raise RuntimeError(f"Could not read duration for: {video_path}")

    # Fade times (safe defaults)
    fade_in = 0.6
    fade_out = 1.2

    # If video is very short, reduce fades
    if dur < 3.0:
        fade_in = min(0.3, dur * 0.2)
        fade_out = min(0.5, dur * 0.25)

    fade_out_start = max(0.0, dur - fade_out)

    # Ducking + volume:
    # duck_db is typically negative (e.g. -10). Convert to a soft multiplier baseline.
    # We'll keep it simple: stronger ducking => lower base volume.
    # -6  => 0.35 ; -10 => 0.30 ; -14 => 0.25
    base_vol = 0.30
    if duck_db >= -7:
        base_vol = 0.35
    elif duck_db <= -13:
        base_vol = 0.25

    af = (
        # voice normalize
        "[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[a0];"
        # bg: looped input, set volume, trim to video duration, apply fades
        f"[1:a]volume={base_vol},atrim=0:{dur},asetpts=N/SR/TB,"
        f"afade=t=in:st=0:d={fade_in},afade=t=out:st={fade_out_start}:d={fade_out}[a1];"
        # duck bg under voice
        "[a1][a0]sidechaincompress=threshold=0.05:ratio=8:attack=5:release=200[bg];"
        # mix
        "[a0][bg]amix=inputs=2:duration=first:dropout_transition=2[mix]"
    )

    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-i", video_path,
        "-stream_loop", "-1",  # loop music forever; atrim will cut to duration
        "-i", music_path,
        "-filter_complex", af,
        "-map", "0:v:0",
        "-map", "[mix]",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        out_path
    ]
    _run(cmd)


def _pick_music_file(music_library_dir: str, tag: str) -> str:
    """
    v1.1:
    music_library_dir/
      energetic/*.mp3
      rhythmic/*.mp3
      slow/*.mp3
      calm/*.mp3
      minimal/*.mp3

    tag should be one of these folder names.
    Picks a random mp3 from that folder.
    """
    tag = (tag or "").strip().lower()
    folder = os.path.join(music_library_dir, tag)

    if not os.path.isdir(folder):
        # fallback: old behavior tag.mp3 in root
        legacy = os.path.join(music_library_dir, f"{tag}.mp3")
        return legacy

    candidates = [
        os.path.join(folder, f)
        for f in os.listdir(folder)
        if f.lower().endswith(".mp3")
    ]
    if not candidates:
        return os.path.join(folder, f"{tag}_01.mp3")

    return random.choice(candidates)


def _probe_duration_seconds(media_path: str) -> float:
    """
    Uses ffprobe to get duration in seconds.
    """
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        media_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed:\nCMD: {' '.join(cmd)}\nSTDERR:\n{proc.stderr}")

    try:
        return float(proc.stdout.strip())
    except Exception:
        return 0.0


def _run(cmd: List[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\nCMD: {' '.join(cmd)}\nSTDERR:\n{proc.stderr}")