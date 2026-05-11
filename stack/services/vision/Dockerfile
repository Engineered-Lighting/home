FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN pip install --upgrade pip && \
    pip install fastapi==0.117.1 'uvicorn[standard]'==0.32.1 httpx==0.28.1 pydantic==2.9.2

WORKDIR /app
COPY app.py /app/app.py

EXPOSE 8091
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8091"]
