"""
Supabase istemcisi — FastAPI backend
service_role key ile çalışır (RLS bypass).

Kullanım:
    from services.supabase_client import sb

    await sb.create_job(job_id, filename, duration, info)
    await sb.save_edit_results(job_id, edit_job_id, result_dict)
"""

import os
import logging
from typing import Optional, Any
from datetime import datetime, timezone

from supabase import create_client, Client

logger = logging.getLogger(__name__)

SUPABASE_URL         = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")  # service_role key (RLS bypass)

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    logger.warning("[supabase] SUPABASE_URL veya SUPABASE_SERVICE_KEY eksik — DB işlemleri devre dışı")

_client: Optional[Client] = None

def get_client() -> Optional[Client]:
    global _client
    if _client is None and SUPABASE_URL and SUPABASE_SERVICE_KEY:
        _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _client


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── JOBS ─────────────────────────────────────────────────────────────────────

async def create_job(
    job_id: str,
    filename: str,
    duration: float,
    info: dict = None,
) -> bool:
    """Video yüklendikten sonra jobs tablosuna kayıt atar."""
    client = get_client()
    if not client:
        return False
    try:
        info = info or {}
        client.table("jobs").upsert({
            "id":         job_id,
            "filename":   filename,
            "duration":   duration,
            "codec":      info.get("codec"),
            "width":      info.get("width"),
            "height":     info.get("height"),
            "fps":        info.get("fps"),
            "resolution": info.get("resolution"),
            "status":     "uploaded",
        }).execute()
        return True
    except Exception as e:
        logger.error(f"[supabase] create_job: {e}")
        return False


async def update_job_status(job_id: str, status: str) -> bool:
    client = get_client()
    if not client:
        return False
    try:
        client.table("jobs").update({"status": status}).eq("id", job_id).execute()
        return True
    except Exception as e:
        logger.error(f"[supabase] update_job_status: {e}")
        return False


# ── EDIT JOBS ────────────────────────────────────────────────────────────────

async def create_edit_job(
    job_id: str,
    command_text: str,
    platform: str = "youtube",
    target_duration_sec: Optional[int] = None,
    remove_fillers: bool = True,
    preserve_rhythm: bool = True,
) -> Optional[str]:
    """Edit işi başlayınca edit_jobs tablosuna kaydeder, oluşan ID'yi döner."""
    client = get_client()
    if not client:
        return None
    try:
        res = client.table("edit_jobs").insert({
            "job_id":              job_id,
            "command_text":        command_text,
            "platform":            platform,
            "target_duration_sec": target_duration_sec,
            "remove_fillers":      remove_fillers,
            "preserve_rhythm":     preserve_rhythm,
            "status":              "pending",
        }).execute()
        return res.data[0]["id"] if res.data else None
    except Exception as e:
        logger.error(f"[supabase] create_edit_job: {e}")
        return None


async def update_edit_job(
    edit_job_id: str,
    status: str,
    progress: float = None,
    step: str = None,
    error_message: str = None,
) -> bool:
    client = get_client()
    if not client:
        return False
    try:
        payload: dict[str, Any] = {"status": status}
        if progress is not None:
            payload["progress"] = progress
        if step is not None:
            payload["step"] = step
        if error_message is not None:
            payload["error_message"] = error_message
        if status in ("done", "completed", "error", "failed"):
            payload["completed_at"] = _now()
        client.table("edit_jobs").update(payload).eq("id", edit_job_id).execute()
        return True
    except Exception as e:
        logger.error(f"[supabase] update_edit_job: {e}")
        return False


# ── EDIT RESULTS ──────────────────────────────────────────────────────────────

async def save_edit_results(job_id: str, edit_job_id: Optional[str], result: dict) -> bool:
    """
    Auto-edit tamamlandığında tüm sonuçları paralel kaydeder:
    video_outputs, scores, analysis_results, edit_plans
    """
    client = get_client()
    if not client:
        return False

    try:
        # video_outputs
        url = result.get("download_url") or result.get("output_url")
        if url:
            dur = result.get("duration", {})
            client.table("video_outputs").insert({
                "job_id":         job_id,
                "edit_job_id":    edit_job_id,
                "output_url":     result.get("output_url") or url,
                "download_url":   url,
                "duration":       dur.get("output"),
                "input_duration": dur.get("input"),
            }).execute()

        # scores
        scores = result.get("scores", {})
        if scores:
            client.table("scores").insert({
                "job_id":          job_id,
                "edit_job_id":     edit_job_id,
                "info_density":    scores.get("info_density"),
                "pacing_score":    scores.get("pacing_score"),
                "hook_score":      scores.get("hook_score"),
                "retention_score": scores.get("retention_score"),
                "silence_risk":    scores.get("silence_risk"),
                "kept_sec":        scores.get("kept_sec"),
                "cuts_per_min":    scores.get("cuts_per_min"),
                "word_count":      scores.get("word_count"),
            }).execute()

        # analysis_results (claude_analysis)
        ca = result.get("claude_analysis", {})
        if ca:
            client.table("analysis_results").insert({
                "job_id":           job_id,
                "edit_job_id":      edit_job_id,
                "hook_quality":     ca.get("hook_quality"),
                "hook_suggestion":  ca.get("hook_suggestion"),
                "best_moment":      ca.get("best_moment"),
                "cut_suggestion":   ca.get("cut_suggestion"),
                "platform_fit":     ca.get("platform_fit"),
                "viral_score":      ca.get("viral_score"),
                "one_line_summary": ca.get("one_line_summary"),
                "suggestions":      ca.get("suggestions", []),
                "raw_response":     ca,
            }).execute()

        # edit_plans
        ep = result.get("edit_plan", {})
        if ep:
            client.table("edit_plans").insert({
                "job_id":       job_id,
                "edit_job_id":  edit_job_id,
                "cuts_applied": ep.get("cuts_applied", 0),
                "keeps_used":   ep.get("keeps_used", 0),
                "intro_sec":    ep.get("intro_sec"),
                "outro_sec":    ep.get("outro_sec"),
                "keeps":        ep.get("keeps", []),
            }).execute()

        return True
    except Exception as e:
        logger.error(f"[supabase] save_edit_results: {e}")
        return False


# ── TRANSCRIPT ───────────────────────────────────────────────────────────────

async def save_transcript(
    job_id: str,
    model: str,
    language: str,
    full_text: str,
    segments: list,
) -> bool:
    client = get_client()
    if not client:
        return False
    try:
        client.table("transcripts").upsert({
            "job_id":     job_id,
            "model":      model,
            "language":   language,
            "full_text":  full_text,
            "segments":   segments,
            "word_count": len(full_text.split()),
        }).execute()
        return True
    except Exception as e:
        logger.error(f"[supabase] save_transcript: {e}")
        return False


# ── SUBTITLES ────────────────────────────────────────────────────────────────

async def save_subtitle(job_id: str, language: str, video_url: str, auto_applied: bool = False) -> bool:
    client = get_client()
    if not client:
        return False
    try:
        client.table("subtitles").insert({
            "job_id":       job_id,
            "language":     language,
            "video_url":    video_url,
            "status":       "completed",
            "auto_applied": auto_applied,
            "applied_at":   _now(),
        }).execute()
        return True
    except Exception as e:
        logger.error(f"[supabase] save_subtitle: {e}")
        return False


# ── EFFECTS ──────────────────────────────────────────────────────────────────

async def save_effect(
    job_id: str,
    category: str,
    intensity: float,
    video_url: str,
    timestamp: Optional[float] = None,
) -> bool:
    client = get_client()
    if not client:
        return False
    try:
        client.table("effects").insert({
            "job_id":     job_id,
            "category":   category,
            "intensity":  intensity,
            "timestamp":  timestamp,
            "video_url":  video_url,
            "status":     "completed",
            "applied_at": _now(),
        }).execute()
        return True
    except Exception as e:
        logger.error(f"[supabase] save_effect: {e}")
        return False


# ── SHORTS ───────────────────────────────────────────────────────────────────

async def save_shorts(job_id: str, shorts_list: list) -> bool:
    client = get_client()
    if not client:
        return False
    if not shorts_list:
        return True
    try:
        rows = [
            {
                "job_id":               job_id,
                "index":                s.get("index", i + 1),
                "start_sec":            s.get("start"),
                "end_sec":              s.get("end"),
                "duration":             (s.get("end", 0) - s.get("start", 0)),
                "semantic_score":       s.get("semantic_score"),
                "completeness":         s.get("completeness"),
                "overall_score":        s.get("score"),
                "emotional_peak":       s.get("emotional_peak", False),
                "narrative_structure":  s.get("narrative_structure"),
                "why_good":             s.get("why_good"),
                "text_preview":         s.get("text_preview"),
                "url":                  s.get("url"),
                "status":               s.get("status", "completed"),
                "error":                s.get("error"),
            }
            for i, s in enumerate(shorts_list)
        ]
        client.table("shorts").insert(rows).execute()
        return True
    except Exception as e:
        logger.error(f"[supabase] save_shorts: {e}")
        return False


# ── JOB CONTEXT (RAM yerine Supabase) ────────────────────────────────────────

async def save_job_context(job_id: str, video_path: str, context: dict = None) -> bool:
    """video_path ve context'i jobs tablosuna kalıcı olarak yazar."""
    client = get_client()
    if not client:
        return False
    try:
        client.table("jobs").update({
            "video_path": video_path,
            "context":    context or {},
        }).eq("id", job_id).execute()
        return True
    except Exception as e:
        logger.error(f"[supabase] save_job_context: {e}")
        return False


async def get_job_context(job_id: str) -> Optional[dict]:
    """jobs tablosundan video_path ve context'i okur."""
    client = get_client()
    if not client:
        return None
    try:
        res = client.table("jobs").select("video_path, context, last_edited_path").eq("id", job_id).single().execute()
        return res.data
    except Exception as e:
        logger.error(f"[supabase] get_job_context: {e}")
        return None


async def save_last_edited_path(job_id: str, path: str) -> bool:
    """Son edit çıktısının path'ini kaydeder."""
    client = get_client()
    if not client:
        return False
    try:
        client.table("jobs").update({"last_edited_path": path}).eq("id", job_id).execute()
        return True
    except Exception as e:
        logger.error(f"[supabase] save_last_edited_path: {e}")
        return False


async def save_edit_version(job_id: str, version: int, command_text: str, output_file: str, duration: float) -> bool:
    """Edit versiyonunu edit_versions tablosuna kaydeder."""
    client = get_client()
    if not client:
        return False
    try:
        client.table("edit_versions").insert({
            "job_id":       job_id,
            "version":      version,
            "command_text": command_text,
            "output_file":  output_file,
            "duration":     duration,
        }).execute()
        return True
    except Exception as e:
        logger.error(f"[supabase] save_edit_version: {e}")
        return False


async def get_edit_versions(job_id: str) -> list:
    """Job'a ait tüm edit versiyonlarını döner."""
    client = get_client()
    if not client:
        return []
    try:
        res = client.table("edit_versions").select("*").eq("job_id", job_id).order("version").execute()
        return res.data or []
    except Exception as e:
        logger.error(f"[supabase] get_edit_versions: {e}")
        return []


# ── ANALYTICS ────────────────────────────────────────────────────────────────

async def log_event(event: str, job_id: Optional[str] = None, extra: dict = None) -> None:
    """Asenkron olmayan versiyon — fire-and-forget."""
    client = get_client()
    if not client:
        return
    try:
        client.table("analytics").insert({
            "event":   event,
            "job_id":  job_id,
            "extra":   extra or {},
        }).execute()
    except Exception as e:
        logger.warning(f"[supabase] log_event: {e}")


# Singleton alias
sb = type("SupabaseService", (), {
    "create_job":           staticmethod(create_job),
    "update_job_status":    staticmethod(update_job_status),
    "create_edit_job":      staticmethod(create_edit_job),
    "update_edit_job":      staticmethod(update_edit_job),
    "save_edit_results":    staticmethod(save_edit_results),
    "save_transcript":      staticmethod(save_transcript),
    "save_subtitle":        staticmethod(save_subtitle),
    "save_effect":          staticmethod(save_effect),
    "save_shorts":          staticmethod(save_shorts),
    "log_event":            staticmethod(log_event),
    "save_job_context":     staticmethod(save_job_context),
    "get_job_context":      staticmethod(get_job_context),
    "save_last_edited_path":staticmethod(save_last_edited_path),
    "save_edit_version":    staticmethod(save_edit_version),
    "get_edit_versions":    staticmethod(get_edit_versions),
})()
