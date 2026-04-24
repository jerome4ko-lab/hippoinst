FROM python:3.11-slim

WORKDIR /app

# System deps: ffmpeg + Korean font + wget
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-noto-cjk \
    wget \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp binary
RUN wget -q -O /usr/local/bin/yt-dlp \
    https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
    && chmod +x /usr/local/bin/yt-dlp

# Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App source
COPY . .

# Runtime directories
RUN mkdir -p output temp

EXPOSE 8000

CMD uvicorn web.app:app --host 0.0.0.0 --port ${PORT:-8000}
