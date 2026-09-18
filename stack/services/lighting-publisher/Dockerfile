# The Living Lights belief publisher (milestone M4: shadow, no Jev, no egress).
#
# Reads Home Assistant state only from the MQTT mirror and the observer's HTTP
# API: no Home Assistant token is built in, passed in, or accepted. Runs as
# uid 10001 with a read-only root filesystem; the only writable paths are the
# journal volume (/data) and the tmpfs at /tmp.
FROM python:3.12-slim

# No .pyc writes (the filesystem is read-only) and unbuffered logs for docker
# logs / journald.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8105 \
    BIND_HOST=0.0.0.0 \
    PUBLISH_BIND_ADDR=127.0.0.1 \
    PUBLISHER_MODE=shadow \
    JOURNAL_DIR=/data

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY lighting_publisher ./lighting_publisher
COPY config ./config

EXPOSE 8105

# uid 10001 owns the journal volume on the host
# (/opt/home-ai-voice/data/lighting-publisher).
USER 10001:10001

# BIND_HOST is the address inside the container's own network namespace;
# PUBLISH_BIND_ADDR is the host address compose publishes the port on. The
# process refuses to start (exit 78, as the video labeler does) when either
# would make /healthz reachable off loopback.
CMD ["python", "-m", "lighting_publisher.main"]
