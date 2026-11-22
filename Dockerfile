# Standard lightweight Python base image
FROM python:3.11-slim

# Configure Python execution:
# - PYTHONDONTWRITEBYTECODE: Avoid writing .pyc files inside the image
# - PYTHONUNBUFFERED: Stream stdout/stderr immediately to logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    OLLAMA_BASE_URL=http://localhost:11434

# Working directory within the container
WORKDIR /app

# Install minimal OS packages needed for builds and network healthchecks
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Layer caching: copy dependency specification first
COPY requirements.txt .

# Install dependencies into container
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source tree
COPY . .

# Ensure storage directories exist for persistence mounts
RUN mkdir -p /app/data /app/results

# Expose FastAPI port
EXPOSE 8000

# Container healthcheck testing web server responsiveness
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

# Start Uvicorn listening on all network interfaces
CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8000"]
