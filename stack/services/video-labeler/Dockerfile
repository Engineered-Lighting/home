FROM python:3.11-slim

# ffmpeg/ffprobe back the probe/proxy/sprite jobs; libgl1/libglib2.0-0 are
# opencv runtime deps (ultralytics pulls opencv-python).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# M2 perception stack -- pinned HERE, not in requirements.txt, so the Windows
# test venv never pulls torch (perception imports are lazy/guarded; the API
# process never loads them). cu128 wheels match the RTX 6000 Blackwell host.
# The image grows several GB at M2 -- accepted.
RUN pip install --no-cache-dir \
        --extra-index-url https://download.pytorch.org/whl/cu128 \
        torch==2.9.1 torchvision==0.24.1 \
    && pip install --no-cache-dir "ultralytics==8.3.*" "av>=14.4,<16"

# M4 embeddings stack: scikit-learn backs the cluster job (PCA +
# MiniBatchKMeans); timm/einops are the dependencies the facebookresearch/
# vjepa2 hubconf declares (torch.hub refuses to load the repo without them).
# The V-JEPA checkpoint is NOT baked in -- the embed job downloads it once
# into {DATA_DIR}/models/backbone/ (see videolabeler/embeddings/backbone.py).
RUN pip install --no-cache-dir "scikit-learn==1.*" timm einops

COPY main.py ./
COPY videolabeler ./videolabeler

EXPOSE 8099
ENV DATA_DIR=/data PORT=8099

# shell form so ${PORT} resolves at container start
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8099}"]
