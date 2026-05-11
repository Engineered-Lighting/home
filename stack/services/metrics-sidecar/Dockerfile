FROM python:3.12-slim

WORKDIR /app

# pynvml needs the NVIDIA driver libraries from the host — exposed via
# the nvidia container runtime when --gpus all is set in compose.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 8092
# uvicorn worker count = 1 (tiny CPU+NVML poller — single process is fine).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8092"]
