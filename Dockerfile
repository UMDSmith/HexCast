FROM python:3.10-slim

WORKDIR /app

# Install runtime dependencies (ffmpeg for transcoding)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Create media directories
RUN mkdir -p media/audio media/video

# Copy application code
COPY . .

# Expose the control panel port
EXPOSE 4747

# Set environment to bind to all interfaces
ENV HOST=0.0.0.0

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python3 -c "import httpx; httpx.get('http://localhost:4747/api/media', timeout=2)" || exit 1

# Run the application
CMD ["python", "hexcast.py"]
