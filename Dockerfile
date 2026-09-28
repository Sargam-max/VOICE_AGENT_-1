# Multi-stage production Dockerfile for LiveKit Voice AI Agent
FROM python:3.12-slim

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
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

# Install uv for package management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy dependency specifications first to leverage Docker layer caching
COPY pyproject.toml uv.lock ./

# Install Python dependencies
RUN uv sync --frozen --no-dev --no-install-project

# Pre-download VAD and Turn-Detector models during build so container boots instantly
RUN uv run python -m livekit.agents download-files

# Copy the rest of the application code
COPY . .

# Expose web server port (Railway routes to $PORT or default 8000)
EXPOSE 8000

# Run both the token/web server and voice agent
CMD ["uv", "run", "run_all.py"]
