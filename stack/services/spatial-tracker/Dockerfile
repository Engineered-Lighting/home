FROM python:3.11-slim

# opencv-python-headless needs libglib even without GUI bits
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py tracker.py rules.py calib.py model_store.py replay.py ./

EXPOSE 8098
ENV DATA_DIR=/data PORT=8098

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8098"]
