FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system --gid 10001 homeagent \
    && useradd --system --uid 10001 --gid homeagent --home-dir /nonexistent homeagent \
    && mkdir -p /app /runtime \
    && chown homeagent:homeagent /runtime

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=homeagent:homeagent alembic.ini ./
COPY --chown=homeagent:homeagent alembic ./alembic
COPY --chown=homeagent:homeagent app ./app
COPY --chown=homeagent:homeagent docker-entrypoint.sh ./
RUN chmod -R a+rX /app && chmod 0555 /app/docker-entrypoint.sh

USER homeagent
EXPOSE 8104
VOLUME ["/runtime"]

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["api"]
