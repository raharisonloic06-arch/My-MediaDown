# ── Stage 1: Build React frontend ──────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python backend + static files ──────────────────────────────────────
FROM python:3.12-slim

# System dependencies: ffmpeg is required by yt-dlp for merging/conversion
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend
COPY backend/ ./backend/

# Copy built frontend into a static directory served by FastAPI
COPY --from=frontend-builder /app/frontend/dist ./static/

# Mount point for temp downloads
RUN mkdir -p /tmp/mediadown

# Serve static files from FastAPI (add StaticFiles mount in main.py for prod)
ENV PYTHONPATH=/app
EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
