"""
Clipla Smart Shorts Generator
Transcript → semantic block scoring → anlamlı klip seçimi
"""
from fastapi import APIRouter, HTTPException, Request
from services.security import get_plan_from_request, verify_job_owner
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import os, json, re, subprocess
from pathlib import Path

from services.supabase_client import sb

router = APIRouter(prefix="/api", tags=["shorts"])

JOBS_DIR       = os.environ.get("JOBS_DIR", "jobs")
OUTPUT_DIR_ENV = os.environ.get("OUTPUT_DIR", "outputs")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000")

# Short süresi limitleri
SHORT_MIN_SEC = 15.0
SHORT_MAX_SEC = 59.0
SHORT_TARGET_SEC = 45.0


class ShortsRequest(BaseModel):
    top_n: int = 5
    reencode: bool = False
    # Frontend'den gelen parametreler — scoring davranışını etkiler
    semantic_analysis: bool = True       # transcript varsa semantik analiz kullan
    require_completeness: bool = True    # cümle tamamlığı skoru faktörü
    detect_emotional_peak: bool = True   # duygusal peak bonus skoru


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def find_job_video(job_dir: str) -> Optional[str]:
    for ext in [".mp4", ".mov", ".avi", ".mkv", ".webm"]:
        for f in Path(job_dir).iterdir():
            if f.suffix.lower() == ext and "short" not in f.name and "final" not in f.name:
                return str(f)
    return None


def load_transcript_segments(job_id: str, job_dir: str) -> List[Dict]:
    """
    Önce outputs/{job_id}_transcript.json'a bak (main.py cache),
    sonra job_dir içindeki alternatif dosyalara bak.
    """
    # main.py'nin cache yolu
    candidates = [
        os.path.join(OUTPUT_DIR_ENV, f"{job_id}_transcript.json"),
        os.path.join(job_dir, "transcript.json"),
        os.path.join(job_dir, "transcription.json"),
        os.path.join(job_dir, "whisper_output.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            segs = data.get("segments", [])
            if segs:
                return segs
    return []


def get_video_duration(video_path: str) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", video_path],
            capture_output=True, text=True
        )
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


# ─────────────────────────────────────────────
# SEMANTIC BLOCK BUILDER
# Whisper segment'lerini anlamlı bloklara grupla
# ─────────────────────────────────────────────

def build_semantic_blocks(
    segments: List[Dict],
    max_gap_sec: float = 1.5,
    min_block_sec: float = 8.0,
    max_block_sec: float = 60.0,
) -> List[Dict]:
    """
    Whisper segment'lerini birbirine yakın olanları birleştirerek
    anlamlı konuşma blokları oluştur.
    Boşluk > max_gap_sec ise yeni blok başlat.
    """
    if not segments:
        return []

    blocks = []
    current = {
        "start": segments[0]["start"],
        "end":   segments[0]["end"],
        "texts": [segments[0]["text"]],
    }

    for seg in segments[1:]:
        gap = seg["start"] - current["end"]
        block_dur = current["end"] - current["start"]

        # Yeni blok koşulları: büyük boşluk veya max süre aşıldı
        if gap > max_gap_sec or block_dur >= max_block_sec:
            if block_dur >= min_block_sec:
                blocks.append({
                    "start": current["start"],
                    "end":   current["end"],
                    "text":  " ".join(current["texts"]).strip(),
                    "duration": block_dur,
                })
            # Yeni blok
            current = {
                "start": seg["start"],
                "end":   seg["end"],
                "texts": [seg["text"]],
            }
        else:
            current["end"] = seg["end"]
            current["texts"].append(seg["text"])

    # Son bloğu ekle
    block_dur = current["end"] - current["start"]
    if block_dur >= min_block_sec:
        blocks.append({
            "start": current["start"],
            "end":   current["end"],
            "text":  " ".join(current["texts"]).strip(),
            "duration": block_dur,
        })

    return blocks


# ─────────────────────────────────────────────
# SMART SCORING
# Her blok için anlam + hook + tamamlık skoru
# ─────────────────────────────────────────────

# Türkçe + İngilizce hook/insight kelimeleri
HOOK_WORDS = [
    # Türkçe
    "dikkat", "önemli", "kritik", "şaşırtıcı", "inanılmaz", "gerçek şu ki",
    "aslında", "şunu bilmelisiniz", "fark ettim", "keşfettim", "sır",
    "neden", "nasıl", "neden böyle", "yanlış anlıyorsunuz", "hata",
    "öğrendim", "denedim", "test ettim", "kanıtlandı", "araştırma",
    "en önemli", "tek şey", "değişti", "dönüştü", "sonuç",
    "öneririm", "tavsiye", "mutlaka", "kesinlikle", "asla", "her zaman",
    # English
    "actually", "secret", "truth", "mistake", "wrong", "important",
    "critical", "discovered", "realized", "research", "study", "proven",
    "never", "always", "only", "best", "worst", "changed", "result",
    "recommend", "must", "should", "why", "how", "what if",
]

FILLER_WORDS = [
    "ıı", "mmm", "şey", "hani", "yani", "ee", "um", "uh", "like", "you know",
]

SENTENCE_ENDINGS = re.compile(r'[.!?।…]')
SENTENCE_STARTERS = re.compile(
    r'\b(bu|şu|o|bir|çünkü|ama|fakat|ancak|yani|aslında|actually|because|but|so|this|that|the)\b',
    re.IGNORECASE
)


def score_block(block: Dict, position_ratio: float) -> float:
    """
    Bloğu 0-100 arası puanla.
    position_ratio: 0=başlangıç, 1=son
    """
    text = block["text"].lower()
    dur  = block["duration"]
    score = 0.0

    # 1. Süre skoru — SHORT_TARGET_SEC'e yakınsa yüksek
    dur_diff = abs(dur - SHORT_TARGET_SEC)
    if dur_diff < 5:
        score += 30
    elif dur_diff < 15:
        score += 20
    elif dur <= SHORT_MAX_SEC:
        score += 10
    else:
        score -= 10  # çok uzun

    # 2. Hook kelimesi skoru
    hook_hits = sum(1 for w in HOOK_WORDS if w in text)
    score += min(hook_hits * 8, 25)

    # 3. Filler oranı — az filler = iyi
    filler_hits = sum(1 for w in FILLER_WORDS if w in text)
    word_count = max(len(text.split()), 1)
    filler_ratio = filler_hits / word_count
    score -= filler_ratio * 20

    # 4. Cümle tamamlığı — nokta/ünlem/soru ile bitiyorsa iyi
    last_char = text.strip()[-1] if text.strip() else ""
    if last_char in ".!?":
        score += 10

    # 5. Cümle başlangıcı — güçlü başlıyorsa iyi
    words = text.strip().split()
    if words and re.match(SENTENCE_STARTERS, words[0]):
        score -= 5  # bağlaçla başlamak zayıf hook

    # 6. Kelime yoğunluğu — saniyede kelime
    wps = word_count / max(dur, 1)
    if 1.5 <= wps <= 4.0:
        score += 10  # ideal konuşma hızı
    elif wps < 0.5:
        score -= 15  # çok sessiz

    # 7. Pozisyon skoru — ortadaki içerik genelde daha değerli
    # ama giriş ve kapanış da güçlü olabilir
    if 0.15 <= position_ratio <= 0.85:
        score += 8
    elif position_ratio < 0.05:
        score += 5  # hook açılış

    # 8. Soru içeriyorsa — merak uyandırıcı
    if "?" in block["text"]:
        score += 7

    # 9. Rakam/istatistik içeriyorsa
    if re.search(r'\d+', block["text"]):
        score += 5

    return round(max(0, min(score, 100)), 2)


# ─────────────────────────────────────────────
# SHORT CANDIDATE BUILDER
# Bitişik blokları birleştirerek short adayı oluştur
# ─────────────────────────────────────────────

def build_short_candidates(
    blocks: List[Dict],
    video_duration: float,
) -> List[Dict]:
    """
    Her blok ve bitişik blok kombinasyonlarını short adayı olarak değerlendir.
    Süresi SHORT_MIN_SEC - SHORT_MAX_SEC arasında olanları al.
    """
    candidates = []
    n = len(blocks)

    for i in range(n):
        # Tek blok
        b = blocks[i]
        if SHORT_MIN_SEC <= b["duration"] <= SHORT_MAX_SEC:
            pos = b["start"] / max(video_duration, 1)
            sc = score_block(b, pos)
            candidates.append({
                "start": b["start"],
                "end":   b["end"],
                "duration": b["duration"],
                "text": b["text"],
                "score": sc,
                "block_count": 1,
            })

        # İki bitişik blok birleştir
        if i + 1 < n:
            b2 = blocks[i + 1]
            combined_dur = b2["end"] - b["start"]
            if SHORT_MIN_SEC <= combined_dur <= SHORT_MAX_SEC:
                combined_text = b["text"] + " " + b2["text"]
                pos = b["start"] / max(video_duration, 1)
                fake_block = {
                    "start": b["start"],
                    "end": b2["end"],
                    "text": combined_text,
                    "duration": combined_dur,
                }
                sc = score_block(fake_block, pos)
                sc += 5  # birleşik blok bonus — daha tam bir hikaye
                candidates.append({
                    "start": b["start"],
                    "end":   b2["end"],
                    "duration": combined_dur,
                    "text": combined_text,
                    "score": sc,
                    "block_count": 2,
                })

        # Üç bitişik blok
        if i + 2 < n:
            b2 = blocks[i + 1]
            b3 = blocks[i + 2]
            combined_dur = b3["end"] - b["start"]
            if SHORT_MIN_SEC <= combined_dur <= SHORT_MAX_SEC:
                combined_text = b["text"] + " " + b2["text"] + " " + b3["text"]
                pos = b["start"] / max(video_duration, 1)
                fake_block = {
                    "start": b["start"],
                    "end": b3["end"],
                    "text": combined_text,
                    "duration": combined_dur,
                }
                sc = score_block(fake_block, pos)
                sc += 10  # üç blok — daha eksiksiz yapı
                candidates.append({
                    "start": b["start"],
                    "end":   b3["end"],
                    "duration": combined_dur,
                    "text": combined_text,
                    "score": sc,
                    "block_count": 3,
                })

    return candidates


def select_top_shorts(
    candidates: List[Dict],
    top_n: int,
    min_gap_sec: float = 10.0,
) -> List[Dict]:
    """
    En yüksek skorlu adayları seç.
    Örtüşen adayları eleme — her short ayrı bir zaman dilimini kapsamalı.
    """
    # Skora göre sırala
    sorted_cands = sorted(candidates, key=lambda x: x["score"], reverse=True)

    selected = []
    for cand in sorted_cands:
        if len(selected) >= top_n:
            break
        # Örtüşme kontrolü
        overlap = False
        for sel in selected:
            # Başlangıç veya bitiş örtüşüyor mu?
            if not (cand["end"] + min_gap_sec <= sel["start"] or
                    cand["start"] >= sel["end"] + min_gap_sec):
                overlap = True
                break
        if not overlap:
            selected.append(cand)

    # Zamana göre sırala
    selected.sort(key=lambda x: x["start"])
    return selected


# ─────────────────────────────────────────────
# FALLBACK: Transcript yoksa basit ama daha iyi bölme
# ─────────────────────────────────────────────

def fallback_smart_split(duration: float, top_n: int) -> List[Dict]:
    """
    Transcript yoksa: başlangıç, orta güçlü nokta, kapanış seç.
    Eşit bölme yerine video yapısına göre seç.
    """
    candidates = []

    # Hook: ilk %20'nin en kısa anlamlı dilimi
    hook_end = min(duration * 0.20, SHORT_TARGET_SEC)
    if hook_end >= SHORT_MIN_SEC:
        candidates.append({"start": 0, "end": hook_end, "score": 70, "label": "hook"})

    # Orta: en değerli içerik genelde %30-70 arası
    mid_start = duration * 0.30
    mid_end = min(mid_start + SHORT_TARGET_SEC, duration * 0.70)
    if mid_end - mid_start >= SHORT_MIN_SEC:
        candidates.append({"start": mid_start, "end": mid_end, "score": 85, "label": "core"})

    # Kapanış: son %20
    outro_start = max(0, duration - SHORT_TARGET_SEC)
    outro_end = duration
    if outro_end - outro_start >= SHORT_MIN_SEC:
        candidates.append({"start": outro_start, "end": outro_end, "score": 65, "label": "outro"})

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:top_n]


# ─────────────────────────────────────────────
# FFmpeg: short render
# ─────────────────────────────────────────────

def render_short(
    input_video: str,
    output_path: str,
    start: float,
    duration: float,
) -> bool:
    """Sync-safe short render — setpts reset ile."""
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-ss", f"{start:.3f}",
        "-i", input_video,
        "-t", f"{duration:.3f}",
        "-vf", "setpts=PTS-STARTPTS",
        "-af", "asetpts=PTS-STARTPTS",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "160k",
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart",
        output_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return os.path.exists(output_path) and os.path.getsize(output_path) > 0


# ─────────────────────────────────────────────
# ENDPOINT
# ─────────────────────────────────────────────

@router.post("/shorts/{job_id}")
async def generate_shorts(request: Request, job_id: str, body: ShortsRequest):
    # Job sahipliği + plan kontrolü — API key'den belirlenir
    verify_job_owner(job_id, request)
    user_plan = get_plan_from_request(request)
    if user_plan != "pro":
        raise HTTPException(
            status_code=403,
            detail="Shorts üretimi Pro plan gerektirir. Clipla-Y Pro'ya geçin.",
        )
    job_dir = os.path.join(JOBS_DIR, job_id)
    if not os.path.isdir(job_dir):
        raise HTTPException(status_code=404, detail=f"Job {job_id} bulunamadi")

    input_video = find_job_video(job_dir)
    if not input_video:
        raise HTTPException(status_code=422, detail="Video dosyasi bulunamadi")

    shorts_dir = os.path.join(job_dir, "shorts")
    os.makedirs(shorts_dir, exist_ok=True)

    duration = get_video_duration(input_video)
    if duration <= 0:
        raise HTTPException(status_code=422, detail="Video suresi alinamadi")

    # ── Transcript yükle ──
    segments = load_transcript_segments(job_id, job_dir)

    selected = []

    # semantic_analysis=False ise transcript olsa bile fallback'e düş
    use_semantic = body.semantic_analysis and bool(segments)

    if use_semantic:
        # ── SMART MODE: transcript bazlı seçim ──
        blocks = build_semantic_blocks(
            segments,
            max_gap_sec=1.5,
            min_block_sec=8.0,
            max_block_sec=60.0,
        )

        if blocks:
            candidates = build_short_candidates(blocks, duration)

            # require_completeness: düşükse cümle tamamlığı şartını gevşet
            if not body.require_completeness:
                for c in candidates:
                    c["score"] = max(0, c["score"] - 10)  # tamamlık bonusunu azalt

            # detect_emotional_peak: yüksekse soru/ünlem cümlelerine bonus
            if body.detect_emotional_peak:
                for c in candidates:
                    if "?" in c.get("text", "") or "!" in c.get("text", ""):
                        c["score"] = min(100, c["score"] + 8)

            selected = select_top_shorts(candidates, body.top_n, min_gap_sec=5.0)

    if not selected:
        # ── FALLBACK MODE: akıllı bölme ──
        raw = fallback_smart_split(duration, body.top_n)
        selected = [
            {
                "start": r["start"],
                "end":   r["end"],
                "duration": r["end"] - r["start"],
                "text":  "",
                "score": r["score"],
            }
            for r in raw
        ]

    # ── Render ──
    clips = []
    for i, sel in enumerate(selected):
        start    = sel["start"]
        end      = sel["end"]
        clip_dur = end - start
        output_path = os.path.join(shorts_dir, f"output_short_{i+1}.mp4")

        ok = render_short(input_video, output_path, start, clip_dur)

        text_preview = sel.get("text", "")
        if len(text_preview) > 200:
            text_preview = text_preview[:200] + "…"

        clips.append({
            "index":        i + 1,
            "start":        round(start, 2),
            "end":          round(end, 2),
            "duration":     round(clip_dur, 2),
            "score":        sel.get("score", 0),
            "url":          f"{PUBLIC_BASE_URL}/jobs/{job_id}/shorts/output_short_{i+1}.mp4",
            "path":         output_path,
            "text_preview": text_preview,
            "status":       "ok" if ok else "error",
            "error":        "" if ok else "render failed",
            "smart":        bool(segments),  # transcript kullanıldı mı
        })

    await sb.save_shorts(job_id, clips)
    await sb.log_event("shorts_generated", job_id, {"count": len(clips), "mode": "smart" if segments else "fallback"})

    return {
        "job_id":      job_id,
        "total_clips": len(clips),
        "shorts":      clips,
        "mode":        "smart" if segments else "fallback",
    }


@router.get("/shorts/{job_id}/list")
async def list_shorts(job_id: str):
    shorts_dir = os.path.join(JOBS_DIR, job_id, "shorts")
    if not os.path.isdir(shorts_dir):
        return {"job_id": job_id, "shorts": []}
    files = sorted(Path(shorts_dir).glob("output_short_*.mp4"))
    return {
        "job_id": job_id,
        "shorts": [
            {
                "filename":   f.name,
                "url":        f"{PUBLIC_BASE_URL}/jobs/{job_id}/shorts/{f.name}",
                "size_bytes": f.stat().st_size,
            }
            for f in files
        ],
    }
