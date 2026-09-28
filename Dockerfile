# Multi-stage production Dockerfile for LiveKit Voice AI Agent
FROM python:3.12-slim

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    PORT=8000 \
    HOST=0.0.0.0 \
    ENV=production

# Install system dependencies needed for audio processing & native extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ffmpeg \
    libopus0 \
    libopus-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv for ultra-fast package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy dependency specifications first to leverage Docker layer caching
COPY pyproject.toml uv.lock ./

# Install Python dependencies
RUN uv sync --frozen --no-dev --no-install-project

# Pre-download VAD and Turn-Detector models during build so the container starts immediately
RUN uv run python -m livekit.agents download-files

# Copy the rest of the application code
COPY . .

# Expose web server port
EXPOSE 8000

# Docker healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Default command runs both the token/web server and the voice agent
CMD ["uv", "run", "run_all.py"]
