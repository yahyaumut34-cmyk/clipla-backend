FROM python:3.11-slim

# ── Sistem bağımlılıkları (FFmpeg + Whisper için) ────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── Çalışma dizini ───────────────────────────────────────────────────────────
WORKDIR /app

# ── Python bağımlılıkları (önce — layer cache için) ─────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Uygulama kodu ────────────────────────────────────────────────────────────
COPY . .

# ── Çalışma dizinleri ────────────────────────────────────────────────────────
RUN mkdir -p uploads outputs jobs sounds music

# ── Whisper modelini önceden indir (build sırasında — startup'ı hızlandırır) ─
# İsteğe bağlı: büyük modeller için build süresi artar
# ARG WHISPER_MODEL=small
# RUN python -c "from faster_whisper import WhisperModel; WhisperModel('${WHISPER_MODEL}')"

# ── Port ─────────────────────────────────────────────────────────────────────
EXPOSE 8000

# ── Healthcheck ──────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=5 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1

# ── Başlatma ─────────────────────────────────────────────────────────────────
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
