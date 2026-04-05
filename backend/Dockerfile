# FinSight Backend Dockerfile
# Base: python:3.11-slim (Debian Bookworm)
# Installs: Tesseract OCR, Poppler (PDF conversion), Python deps

FROM python:3.11-slim

# ── System labels ─────────────────────────────────────────────────────────────
LABEL maintainer="FinSight Team"
LABEL description="FinSight Flask API with OCR support"

# ── System packages ───────────────────────────────────────────────────────────
# tesseract-ocr    → OCR engine
# tesseract-ocr-eng → English language data
# poppler-utils    → pdf2image needs pdftoppm from poppler
# libpq-dev        → psycopg2 build dependencies (not needed for -binary but good practice)
# curl             → healthcheck / debugging

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
    libpq-dev \
    curl \
    gcc \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Working directory ─────────────────────────────────────────────────────────
WORKDIR /app

# ── Python dependencies ───────────────────────────────────────────────────────
# Copy requirements first so Docker layer caching works —
# dependency layer only rebuilds when requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ── Application code ──────────────────────────────────────────────────────────
COPY . .

# ── Runtime configuration ─────────────────────────────────────────────────────
# All secrets injected via --env-file .env at runtime — never baked into image
ENV FLASK_ENV=production
ENV FLASK_PORT=5000
ENV PYTHONUNBUFFERED=1
ENV TESSERACT_CMD=/usr/bin/tesseract

# ── Expose port ───────────────────────────────────────────────────────────────
EXPOSE 5000

# ── Healthcheck ───────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# ── Entrypoint ────────────────────────────────────────────────────────────────
# gunicorn with 4 worker processes; adjust workers based on instance size
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "4", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "app:app"]
