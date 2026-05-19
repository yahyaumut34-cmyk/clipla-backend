from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Literal, Optional
import re
import hashlib
import os
import json
import anthropic
from dotenv import load_dotenv
load_dotenv()

router = APIRouter(prefix="/api/command", tags=["command"])

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Edit planı çıkarma için daha güçlü model — kullanıcı niyetini doğru anlamak kritik
EDIT_PLAN_MODEL = os.getenv("EDIT_PLAN_MODEL", "claude-sonnet-4-6")

_client: Optional[anthropic.Anthropic] = None

def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client

# -------------------------
# Schema
# -------------------------
Target = Literal["youtube", "shorts"]
Preset = Literal["vertical_short", "youtube_16_9"]


class CutItem(BaseModel):
    from_: float = Field(..., alias="from")
    to_: float = Field(..., alias="to")
    reason: str = "unknown"

    class Config:
        populate_by_name = True


class EditPlanV1(BaseModel):
    version: Literal["edit_plan_v1"] = "edit_plan_v1"
    plan_id: str
    target: Target
    preset: Preset
    target_duration_sec: Optional[int] = None
    must_keep_theme: bool = True
    cuts: List[CutItem] = []
    notes: List[str] = []


class CommandBody(BaseModel):
    command_text: Optional[str] = ""
    command: Optional[str] = ""
    target: Target = "youtube"
    preset: Preset = "vertical_short"
    target_duration_sec: Optional[int] = None
    video_duration_sec: Optional[float] = None      # videonun gerçek süresi (opsiyonel)
    transcript: Optional[str] = None                # whisper transkripti (opsiyonel)


# -------------------------
# Helpers
# -------------------------
def _normalize_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _extract_duration_sec_or_none(text: str) -> Optional[int]:
    t = _normalize_text(text or "")
    m = re.search(r"(\d+)\s*(sn|saniye|sec|seconds|s)\b", t)
    if m:
        return max(5, min(600, int(m.group(1))))
    m = re.search(r"(\d+)\s*(dk|dakika|min|minutes)\b", t)
    if m:
        return max(5, min(600, int(m.group(1)) * 60))
    return None


def _infer_target(text: str, fallback: Target) -> Target:
    t = _normalize_text(text)
    if any(k in t for k in ["short", "shorts", "reels", "tiktok", "dikey", "vertical"]):
        return "shorts"
    return fallback


def _infer_preset(target: Target) -> Preset:
    return "vertical_short" if target == "shorts" else "youtube_16_9"


def _deterministic_plan_id(command_text: str, target: str, preset: str, dur) -> str:
    seed = f"{_normalize_text(command_text)}|{target}|{preset}|{dur}"
    h = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]
    return f"plan_{h}"


def _has_any(t: str, keywords: List[str]) -> bool:
    return any(k in t for k in keywords)


def _build_notes_fallback(command_text: str, target: Target, dur) -> List[str]:
    """Claude API yoksa keyword tabanlı fallback."""
    t = _normalize_text(command_text)
    notes = [f"cmd_norm:{t[:180]}", f"target:{target}", f"mode:keyword_fallback"]
    if dur:
        notes.append(f"target_duration_sec:{dur}")

    if _has_any(t, ["boşluk", "sessiz", "sus", "duraks", "pause"]):
        notes.append("tag:cut_silence=true")
    if _has_any(t, ["en güçlü", "hook", "güçlü cümle", "en iyi cümle", "başta güçlü"]):
        notes.append("tag:emphasize_hook=true")
    if _has_any(t, ["vurgula", "zoom", "yakınlaştır", "punch"]):
        notes.append("tag:punch_in=true")
    if _has_any(t, ["hızlandır", "hızlı", "speed up"]):
        notes.append("tag:speed_up=true")
    if _has_any(t, ["yavaşlat", "slow", "ağırdan"]):
        notes.append("tag:slow_down=true")
    if _has_any(t, ["kapanış", "sonunda", "bitir", "final", "closing"]):
        notes.append("tag:add_closing=true")
    if _has_any(t, ["müzik", "music", "beat"]):
        notes.append("tag:add_music=true")
    if _has_any(t, ["normalize", "ses seviye", "ses düzelt", "audio fix"]):
        notes.append("tag:normalize_audio=true")

    return notes


# -------------------------
# Claude API entegrasyonu
# -------------------------
SYSTEM_PROMPT = """Sen Clipla-y adlı bir video düzenleme uygulamasının komut yorumlama motorusun.
Kullanıcı bir video düzenleme komutu yazar, sen bunu yapılandırılmış JSON'a çevirirsin.

Çıktın YALNIZCA geçerli JSON olmalı, başka hiçbir şey yazma.

JSON formatı:
{
  "target_duration_sec": null veya integer (komutta süre varsa doldur, yoksa null),
  "target": "youtube" veya "shorts",
  "cut_silence": true/false,
  "emphasize_hook": true/false,
  "speed_up": true/false,
  "slow_down": true/false,
  "add_music": true/false,
  "music_mood": null veya "energetic"/"calm"/"rhythmic"/"slow",
  "normalize_audio": true/false,
  "punch_in": true/false,
  "add_closing": true/false,
  "must_say": null veya string (kullanıcı belirli bir cümle söylenmesini istiyorsa),
  "highlight_moments": [] veya ["moment1", "moment2"] (öne çıkarılacak anlar),
  "summary": "1 cümleyle ne yapılacak (Türkçe)"
}

Kurallar:
- Türkçe ve İngilizce komutları anlarsın
- "shorts", "reels", "tiktok", "dikey" → target: "shorts"
- "youtube", "yatay", "uzun video" → target: "youtube"  
- Süre belirtilmemişse target_duration_sec: null yaz (60 yazma!)
- Sessizlik kesmek varsayılan olarak true (açıkça "kesme" denmedikçe)
- Belirsiz komutlarda makul varsayımlar yap
"""


def _call_claude_api(command_text: str, video_duration: Optional[float], transcript: Optional[str]) -> Optional[dict]:
    """Claude API'yi çağırır, JSON döner. Başarısız olursa None."""
    if not ANTHROPIC_API_KEY:
        return None

    context_parts = [f"Kullanıcı komutu: {command_text}"]
    if video_duration:
        context_parts.append(f"Video süresi: {video_duration:.1f} saniye ({video_duration/60:.1f} dakika)")
    if transcript:
        preview = transcript[:500] + ("..." if len(transcript) > 500 else "")
        context_parts.append(f"Video transkripti (önizleme): {preview}")

    user_message = "\n".join(context_parts)

    try:
        client = _get_client()
        response = client.messages.create(
            model=EDIT_PLAN_MODEL,
            max_tokens=512,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_message}],
        )
        text = response.content[0].text.strip()

        # JSON temizle (bazen ```json ``` içine alır)
        text = re.sub(r"^```json\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        parsed = json.loads(text)
        return parsed
    except Exception as e:
        print(f"[DEBUG] Claude API error: {e}")
        return None


def _claude_result_to_notes(parsed: dict, target: Target, dur) -> List[str]:
    """Claude API sonucunu notes listesine çevirir."""
    notes = [f"mode:claude_api", f"target:{target}"]
    if dur:
        notes.append(f"target_duration_sec:{dur}")

    if parsed.get("cut_silence"):
        notes.append("tag:cut_silence=true")
    if parsed.get("emphasize_hook"):
        notes.append("tag:emphasize_hook=true")
    if parsed.get("punch_in"):
        notes.append("tag:punch_in=true")
    if parsed.get("speed_up"):
        notes.append("tag:speed_up=true")
    if parsed.get("slow_down"):
        notes.append("tag:slow_down=true")
    if parsed.get("add_closing"):
        notes.append("tag:add_closing=true")
    if parsed.get("add_music"):
        notes.append("tag:add_music=true")
    if parsed.get("music_mood"):
        notes.append(f"tag:music_mood={parsed['music_mood']}")
    if parsed.get("normalize_audio"):
        notes.append("tag:normalize_audio=true")
    if parsed.get("must_say"):
        safe = str(parsed["must_say"]).replace("\n", " ").strip()[:180]
        notes.append(f'tag:must_say="{safe}"')
    if parsed.get("highlight_moments"):
        for m in parsed["highlight_moments"][:5]:
            notes.append(f'tag:highlight="{str(m)[:100]}"')
    if parsed.get("summary"):
        notes.append(f'summary:{parsed["summary"][:200]}')

    return notes


# -------------------------
# Endpoint
# -------------------------
@router.get("/health")
def command_health():
    has_key = bool(ANTHROPIC_API_KEY)
    return {"status": "ok", "service": "clipla-command", "claude_api": has_key}


@router.post("", response_model=EditPlanV1)
def create_edit_plan(body: CommandBody):
    cmd = (body.command_text or "").strip()
    if not cmd:
        cmd = (body.command or "").strip()
    if not cmd:
        raise HTTPException(status_code=400, detail="command_text boş olamaz")

    # Platform ve preset
    target = _infer_target(cmd, body.target)
    preset = body.preset if body.preset else _infer_preset(target)

    # Süre — önce komuttan oku
    dur = body.target_duration_sec or _extract_duration_sec_or_none(cmd)

    # Claude API'yi dene
    claude_result = _call_claude_api(cmd, body.video_duration_sec, body.transcript)

    if claude_result:
        # Claude'dan gelen target ve süreyi önceliklendir
        if claude_result.get("target") in ("youtube", "shorts"):
            target = claude_result["target"]
            preset = _infer_preset(target)
        if dur is None and claude_result.get("target_duration_sec"):
            dur = claude_result["target_duration_sec"]
        notes = _claude_result_to_notes(claude_result, target, dur)
    else:
        # Fallback: keyword tabanlı
        notes = _build_notes_fallback(cmd, target, dur)

    plan_id = _deterministic_plan_id(cmd, target, preset, dur)

    plan = EditPlanV1(
        plan_id=plan_id,
        target=target,
        preset=preset,
        target_duration_sec=dur,
        must_keep_theme=True,
        cuts=[],
        notes=notes,
    )
    return plan
