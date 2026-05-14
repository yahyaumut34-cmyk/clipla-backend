"""
Clipla Subtitle System
- Whisper transcript → SRT
- Video'ya altyazı yakma (FFmpeg)
- Çoklu dil desteği
- Endpoint: POST /api/subtitles/{job_id}
"""
from fastapi import APIRouter, HTTPException, Request
from services.security import verify_job_owner, get_plan_from_request
from pydantic import BaseModel
from typing import Optional, List
import os
import json
import subprocess
import re
from pathlib import Path
from services.supabase_client import sb

router = APIRouter(prefix="/api", tags=["subtitles"])

BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
JOBS_DIR        = os.environ.get("JOBS_DIR", os.path.join(BASE_DIR, "..", "jobs"))
OUTPUT_DIR      = os.environ.get("OUTPUT_DIR", os.path.join(BASE_DIR, "..", "outputs"))
UPLOAD_DIR      = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "..", "uploads"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000")

SUPPORTED_LANGUAGES = {
    "tr": "Türkçe",
    "en": "English",
    "de": "Deutsch",
    "fr": "Français",
    "es": "Español",
    "ar": "العربية",
    "ru": "Русский",
    "ja": "日本語",
    "zh": "中文",
}


CAPTION_STYLE_PRESETS = {
    "bold": {
        "font_size": 22, "primary_color": "&H00FFFFFF", "outline_color": "&H00000000",
        "bold": 1, "outline": 2, "shadow": 1, "border_style": 1, "back_colour": None,
    },
    "neon": {
        "font_size": 20, "primary_color": "&H00FFFF00", "outline_color": "&H00FF0000",
        "bold": 1, "outline": 3, "shadow": 0, "border_style": 1, "back_colour": None,
    },
    "minimal": {
        "font_size": 14, "primary_color": "&H00FFFFFF", "outline_color": "&H00000000",
        "bold": 0, "outline": 0, "shadow": 0, "border_style": 1, "back_colour": None,
    },
    "cinematic": {
        "font_size": 16, "primary_color": "&H00FFFFFF", "outline_color": "&H00000000",
        "bold": 0, "outline": 1, "shadow": 0, "border_style": 3, "back_colour": "&H80000000",
    },
    "tiktok": {
        "font_size": 26, "primary_color": "&H0000FFFF", "outline_color": "&H00000000",
        "bold": 1, "outline": 3, "shadow": 1, "border_style": 1, "back_colour": None,
    },
}


class SubtitleRequest(BaseModel):
    language: str = "tr"
    burn_in: bool = True
    font_size: int = 16
    font_color: str = "white"
    outline_color: str = "black"
    position: str = "bottom"
    max_chars_per_line: int = 38
    style: str = "bold"


# ─────────────────────────────────────────────
# TRANSCRIPT YÜKLE
# ─────────────────────────────────────────────

def load_transcript(job_id: str) -> Optional[dict]:
    """main.py'nin cache'inden transcript'i yükle."""
    cache_path = os.path.join(OUTPUT_DIR, f"{job_id}_transcript.json")
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)

    job_dir = os.path.join(JOBS_DIR, job_id)
    for fname in ["transcript.json", "transcription.json"]:
        p = os.path.join(job_dir, fname)
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    return None


def find_job_video(job_id: str) -> Optional[str]:
    """
    Önce ORİJİNAL videoyu bulur.
    Çünkü transcript timestamp'leri orijinal videoya aittir.
    Sadece en son çare olarak auto-edit çıktısına düşer.
    """

    # 1) uploads içindeki orijinal video
    upload_mp4 = os.path.join(UPLOAD_DIR, f"{job_id}.mp4")
    if os.path.exists(upload_mp4):
        return upload_mp4

    # 2) jobs klasöründeki orijinal video
    job_dir = os.path.join(JOBS_DIR, job_id)
    if os.path.isdir(job_dir):
        for ext in [".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"]:
            for f in Path(job_dir).iterdir():
                name_lower = f.name.lower()
                if (
                    f.suffix.lower() == ext
                    and "short" not in name_lower
                    and "_auto" not in name_lower
                    and "_cut" not in name_lower
                    and "_subtitled" not in name_lower
                ):
                    return str(f)

    # 3) En son çare: işlenmiş video
    for suffix in ["_auto_aspect.mp4", "_auto.mp4", "_cut.mp4"]:
        p = os.path.join(OUTPUT_DIR, f"{job_id}{suffix}")
        if os.path.exists(p):
            return p

    return None


# ─────────────────────────────────────────────
# TIMESTAMP FORMAT
# ─────────────────────────────────────────────

def seconds_to_srt_time(seconds: float) -> str:
    """float saniye → SRT zaman formatı: HH:MM:SS,mmm"""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int(round((seconds % 1) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ─────────────────────────────────────────────
# SEGMENT BIRLEŞTIRME
# ─────────────────────────────────────────────

def merge_short_segments(
    segments: List[dict],
    min_dur: float = 0.5,
    max_dur: float = 5.0,
    max_chars: int = 42,
) -> List[dict]:
    """
    Çok kısa segmentleri birleştir.
    Uzun segmentleri max_chars'a göre böl.
    """
    if not segments:
        return []

    merged = []
    buf_start = None
    buf_end = None
    buf_texts = []
    buf_dur = 0.0

    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue

        start = float(seg["start"])
        end = float(seg["end"])
        dur = end - start

        if buf_start is None:
            buf_start = start
            buf_end = end
            buf_texts = [text]
            buf_dur = dur
        else:
            combined = " ".join(buf_texts + [text])
            if buf_dur + dur <= max_dur and len(combined) <= max_chars * 2:
                buf_end = end
                buf_dur += dur
                buf_texts.append(text)
            else:
                merged.append({
                    "start": buf_start,
                    "end": buf_end,
                    "text": " ".join(buf_texts),
                })
                buf_start = start
                buf_end = end
                buf_texts = [text]
                buf_dur = dur

    if buf_start is not None:
        merged.append({
            "start": buf_start,
            "end": buf_end,
            "text": " ".join(buf_texts),
        })

    final = []
    for seg in merged:
        text = seg["text"]
        if len(text) <= max_chars:
            final.append(seg)
        else:
            words = text.split()
            lines = []
            current = ""
            for word in words:
                extra = 1 if current else 0
                if len(current) + len(word) + extra <= max_chars:
                    current = (current + " " + word).strip()
                else:
                    if current:
                        lines.append(current)
                    current = word
            if current:
                lines.append(current)

            line_dur = (seg["end"] - seg["start"]) / max(len(lines), 1)
            for i, line in enumerate(lines):
                final.append({
                    "start": seg["start"] + i * line_dur,
                    "end": seg["start"] + (i + 1) * line_dur,
                    "text": line,
                })

    return final


# ─────────────────────────────────────────────
# SRT OLUŞTUR
# ─────────────────────────────────────────────

def segments_to_srt(segments: List[dict]) -> str:
    """Segment listesinden SRT string üret."""
    lines = []
    for i, seg in enumerate(segments, 1):
        start_t = seconds_to_srt_time(seg["start"])
        end_t = seconds_to_srt_time(seg["end"])
        text = seg["text"].strip()
        lines.append(f"{i}\n{start_t} --> {end_t}\n{text}\n")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# ÇEVİRİ
# Timestamp'leri korur, sadece metni değiştirir
# ─────────────────────────────────────────────

LANG_NAMES = {
    "tr": "Turkish",
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "ar": "Arabic",
    "ru": "Russian",
    "ja": "Japanese",
    "zh": "Chinese",
}


def translate_segments(
    segments: List[dict],
    source_lang: str,
    target_lang: str,
) -> tuple:
    """
    Önce LibreTranslate dener (ücretsiz / self-hosted).
    Sonra Anthropic dener.
    İkisi de yoksa dürüst hata döner.
    Timestamp'leri korur, sadece text değişir.

    Returns: (translated_segments, status)
    status: "ok" | "error: ..." | "not_needed"
    """
    if source_lang == target_lang:
        return segments, "not_needed"

    texts = [seg["text"] for seg in segments]

    # -------------------------------------------------
    # 1) ÜCRETSİZ / SELF-HOSTED FALLBACK: LibreTranslate
    # -------------------------------------------------
    libre_url = os.environ.get("LIBRETRANSLATE_URL", "").strip()
    libre_error = None

    if libre_url:
        try:
            import httpx

            translated_texts = []
            with httpx.Client(timeout=30.0) as client:
                for text in texts:
                    r = client.post(
                        libre_url.rstrip("/") + "/translate",
                        json={
                            "q": text,
                            "source": source_lang,
                            "target": target_lang,
                            "format": "text",
                        },
                    )
                    r.raise_for_status()
                    data = r.json()
                    translated_texts.append(data.get("translatedText", text))

            result = [
                {"start": seg["start"], "end": seg["end"], "text": str(t)}
                for seg, t in zip(segments, translated_texts)
            ]
            return result, "ok"

        except Exception as e:
            libre_error = f"LibreTranslate hata: {str(e)[:120]}"
    else:
        libre_error = "LibreTranslate ayarlı değil"

    # -------------------------------------------------
    # 2) ÜCRETLİ FALLBACK: Anthropic
    # -------------------------------------------------
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        source_name = LANG_NAMES.get(source_lang, source_lang)
        target_name = LANG_NAMES.get(target_lang, target_lang)
        texts_json = json.dumps(texts, ensure_ascii=False)

        prompt = (
            f"Translate these subtitle texts from {source_name} to {target_name}.\n"
            f"Rules:\n"
            f"- Return ONLY a JSON array of translated strings\n"
            f"- Same order, same count as input\n"
            f"- Keep short and natural for subtitles\n"
            f"- No explanations, no markdown, no extra text\n\n"
            f"Input: {texts_json}"
        )

        try:
            import httpx

            r = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 4096,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=60.0,
            )
            r.raise_for_status()

            reply = r.json()["content"][0]["text"].strip()
            reply = re.sub(r"^```[a-z]*\n?", "", reply)
            reply = re.sub(r"\n?```$", "", reply)
            translated_texts = json.loads(reply.strip())

            if not isinstance(translated_texts, list):
                return segments, "error: API geçersiz format döndürdü"
            if len(translated_texts) != len(segments):
                return segments, f"error: sayı uyuşmuyor ({len(translated_texts)} vs {len(segments)})"

            result = [
                {"start": seg["start"], "end": seg["end"], "text": str(t)}
                for seg, t in zip(segments, translated_texts)
            ]
            return result, "ok"

        except json.JSONDecodeError as e:
            return segments, f"error: JSON parse hatası — {str(e)[:80]}"
        except Exception as e:
            return segments, f"error: Anthropic hata — {str(e)[:120]}"

    # -------------------------------------------------
    # 3) HİÇBİR ÇEVİRİ YOKSA
    # -------------------------------------------------
    return segments, f"error: Çeviri servisi yok. {libre_error}"


# ─────────────────────────────────────────────
# SUBTITLE YAKMA
# ─────────────────────────────────────────────

def _build_force_style(preset: dict, position: str) -> str:
    alignment = {"bottom": "2", "top": "8", "center": "5"}.get(position, "2")
    parts = [
        f"FontSize={preset['font_size']}",
        f"PrimaryColour={preset['primary_color']}",
        f"OutlineColour={preset['outline_color']}",
        f"Bold={preset['bold']}",
        f"Outline={preset['outline']}",
        f"Shadow={preset['shadow']}",
        f"BorderStyle={preset['border_style']}",
        f"Alignment={alignment}",
        "MarginV=30",
    ]
    if preset.get("back_colour"):
        parts.append(f"BackColour={preset['back_colour']}")
    return ",".join(parts)


def burn_subtitles(
    input_video: str,
    srt_path: str,
    output_path: str,
    font_size: int = 16,
    font_color: str = "white",
    outline_color: str = "black",
    position: str = "bottom",
    style: str = "bold",
) -> bool:
    """SRT dosyasını video'ya yak."""

    preset = CAPTION_STYLE_PRESETS.get(style)
    if preset:
        force_style = _build_force_style(preset, position)
    else:
        # Fallback: legacy color/size params
        alignment = {"bottom": "2", "top": "8", "center": "5"}.get(position, "2")
        color_map = {"white": "&H00FFFFFF", "yellow": "&H0000FFFF", "cyan": "&H00FFFF00", "green": "&H0000FF00"}
        primary_color = color_map.get(font_color, "&H00FFFFFF")
        outline_col = "&H00000000" if outline_color == "black" else "&H00FFFFFF"
        force_style = (
            f"FontSize={font_size},PrimaryColour={primary_color},OutlineColour={outline_col},"
            f"Outline=1,Shadow=0,Bold=1,Alignment={alignment},MarginV=30"
        )

    srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")
    subtitle_filter = f"subtitles='{srt_escaped}':force_style='{force_style}'"

    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-i", input_video,
        "-vf", subtitle_filter,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]

    r = subprocess.run(cmd, capture_output=True, text=True)
    return os.path.exists(output_path) and os.path.getsize(output_path) > 0


# ─────────────────────────────────────────────
# ENDPOINT
# ─────────────────────────────────────────────

# TR ve EN altyazı ücretsiz; diğer diller Pro gerektirir
FREE_SUBTITLE_LANGS = {"tr", "en"}

@router.post("/subtitles/{job_id}")
async def generate_subtitles(request: Request, job_id: str, body: SubtitleRequest):
    verify_job_owner(job_id, request)
    # Gelişmiş dil kontrolü
    if body.language not in FREE_SUBTITLE_LANGS and get_plan_from_request(request) != "pro":
        raise HTTPException(
            status_code=403,
            detail=f"'{body.language}' altyazısı Pro plan gerektirir. TR ve EN ücretsizdir.",
        )
    """
    1. Transcript yükle
    2. Segment'leri düzenle
    3. Çeviri yap (gerekirse)
    4. SRT oluştur
    5. Video'ya yak
    """

    transcript = load_transcript(job_id)
    if not transcript:
        raise HTTPException(
            status_code=404,
            detail="Transcript bulunamadı. Önce video'yu işle (auto-edit çalıştır)."
        )

    segments = transcript.get("segments", [])
    if not segments:
        raise HTTPException(status_code=422, detail="Transcript boş.")

    source_lang = transcript.get("language") or "tr"

    processed = merge_short_segments(
        segments,
        min_dur=0.5,
        max_dur=5.0,
        max_chars=body.max_chars_per_line,
    )

    translation_status = "not_needed"
    if body.language != source_lang:
        translated, translation_status = translate_segments(processed, source_lang, body.language)
        if translation_status == "ok":
            processed = translated
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Çeviri başarısız: {translation_status}. Türkçe altyazı için dil seçimini 'Türkçe' yapın."
            )

    srt_content = segments_to_srt(processed)

    srt_filename = f"{job_id}_{body.language}.srt"
    srt_path = os.path.join(OUTPUT_DIR, srt_filename)
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(srt_content)

    result = {
        "job_id": job_id,
        "language": body.language,
        "source_language": source_lang,
        "translation_status": translation_status,
        "segment_count": len(processed),
        "srt_url": f"{PUBLIC_BASE_URL}/outputs/{srt_filename}",
        "srt_content": srt_content[:500] + "..." if len(srt_content) > 500 else srt_content,
    }

    if body.burn_in:
        input_video = find_job_video(job_id)
        if not input_video:
            result["burn_in_status"] = "skipped"
            result["burn_in_error"] = "Video dosyası bulunamadı"
            return result

        # Transcript zamanları orijinal videoya aittir.
        # Bu yüzden burada ekstra offset uygulamıyoruz.
        # Doğru videoyu seçtiğimiz için aynı zamanları kullanıyoruz.

        out_filename = f"{job_id}_subtitled_{body.language}.mp4"
        out_path = os.path.join(OUTPUT_DIR, out_filename)

        ok = burn_subtitles(
            input_video=input_video,
            srt_path=srt_path,
            output_path=out_path,
            font_size=body.font_size,
            font_color=body.font_color,
            outline_color=body.outline_color,
            position=body.position,
            style=body.style,
        )

        if ok:
            video_url = f"{PUBLIC_BASE_URL}/outputs/{out_filename}"
            result["video_url"] = video_url
            result["burn_in_status"] = "ok"
            await sb.save_subtitle(job_id, body.language, video_url, auto_applied=False)
            await sb.log_event("subtitle_burned", job_id, {"language": body.language})
        else:
            result["burn_in_status"] = "failed"
            result["burn_in_error"] = "FFmpeg burn-in başarısız"

    return result


@router.get("/subtitles/{job_id}/languages")
async def available_languages(job_id: str):
    """Desteklenen dilleri listele + transcript dilini döndür."""
    transcript = load_transcript(job_id)
    source_lang = transcript.get("language", "tr") if transcript else "tr"
    return {
        "source_language": source_lang,
        "supported": SUPPORTED_LANGUAGES,
    }


@router.get("/subtitles/{job_id}/srt")
async def get_srt(job_id: str, language: str = "tr"):
    """Oluşturulmuş SRT dosyasını döndür."""
    srt_path = os.path.join(OUTPUT_DIR, f"{job_id}_{language}.srt")
    if not os.path.exists(srt_path):
        raise HTTPException(status_code=404, detail="SRT bulunamadı. Önce /api/subtitles/{job_id} çağır.")
    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read()
    return {"job_id": job_id, "language": language, "srt": content}