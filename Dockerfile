FROM python:3.14.7-slim

WORKDIR /app

# Unbuffered output so cron/docker logs appear immediately
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py VehicleClient.py SpritMonitorClient.py ./

# Run as non-root user for security
RUN useradd -m appuser && chown -R appuser /app
USER appuser

CMD ["python", "main.py"]
