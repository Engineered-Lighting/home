FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# ffmpeg — grabs one full-resolution JPEG from a camera's RTSP main stream
# for /reason_zoom (HA camera_proxy only serves Frigate's 720p sub-stream).
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip && \
    pip install fastapi==0.117.1 'uvicorn[standard]'==0.32.1 httpx==0.28.1 pydantic==2.9.2 Pillow==10.4.0

WORKDIR /app
COPY app.py /app/app.py

EXPOSE 8091
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8091"]
