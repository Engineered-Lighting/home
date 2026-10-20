FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY lighting_brain ./lighting_brain

# Private files are mounted read-only at run time, never baked in:
#   /config/geometry.json  (LIGHTING_BRAIN_GEOMETRY)
#   /config/tokens.json    (LIGHTING_BRAIN_TOKENS)
#   /config/settings.json  (LIGHTING_BRAIN_SETTINGS, optional)
# The brain binds 127.0.0.1 by default; run with `--network host` so that is
# the host's loopback (see README "Run in a container").
ENV PYTHONUNBUFFERED=1 \
    LIGHTING_BRAIN_PORT=8101 \
    LIGHTING_BRAIN_GEOMETRY=/config/geometry.json \
    LIGHTING_BRAIN_TOKENS=/config/tokens.json

USER nobody
EXPOSE 8101

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ.get('LIGHTING_BRAIN_PORT', '8101'), timeout=4).status == 200 else 1)"]

CMD ["python", "-m", "lighting_brain"]
