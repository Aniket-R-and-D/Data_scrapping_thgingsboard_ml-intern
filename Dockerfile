# syntax=docker/dockerfile:1
# Production Dockerfile for SEPLE NovaEdge AI Operations Engine
# Target Platforms: AWS EC2, AWS Lightsail, AWS ECS (Fargate)

FROM python:3.12-slim AS base

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

# Install system dependencies (libgomp1 is strictly required by LightGBM for OpenMP on Linux)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install lightweight production Python dependencies
COPY requirements-prod.txt /app/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements-prod.txt

# Copy core application source code
COPY send_daily_risk_digest.py /app/
COPY src/ /app/src/
COPY scripts/ /app/scripts/

# Copy lightweight trained production models (joblib bundles < 30MB total)
COPY models/gbm_outage_oct2026_365d/ /app/models/gbm_outage_oct2026_365d/
COPY models/isolation_forest_365d/ /app/models/isolation_forest_365d/

# Create persistent reports directory
RUN mkdir -p /app/reports

# Create non-root system user for security
RUN useradd -u 1001 -m appuser && \
    chown -R appuser:appuser /app
USER appuser

# Health check to ensure Python and dependencies can import without error
HEALTHCHECK --interval=60s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import lightgbm, sklearn, joblib, requests; print('healthy')" || exit 1

# Default command: Runs autonomous 24/7 scheduler daemon
# Can be overridden with: ["python", "send_daily_risk_digest.py", "--sync-tb", "--slack", "--email"]
CMD ["python", "scripts/cloud_runner.py", "--daemon"]
