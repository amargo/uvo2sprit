FROM python:3.13.5-slim

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Run as non-root user for security
RUN useradd -m appuser && chown -R appuser /app
USER appuser

CMD ["python", "main.py"]
