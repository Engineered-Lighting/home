FROM python:3.12-alpine

RUN pip install --no-cache-dir "paho-mqtt>=2.1" "websocket-client>=1.7"

WORKDIR /app
COPY app.py zonelog.py anticipate.py eval_logger.py /app/

CMD ["python3", "app.py"]
