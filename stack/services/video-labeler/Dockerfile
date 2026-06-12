FROM python:3.11-slim

# ffmpeg/ffprobe back the probe/proxy/sprite jobs. No torch/ultralytics in M0 —
# the image stays slim until the gpu-lane milestones land.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py ./
COPY videolabeler ./videolabeler

EXPOSE 8099
ENV DATA_DIR=/data PORT=8099

# shell form so ${PORT} resolves at container start
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8099}"]
