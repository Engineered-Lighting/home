ARG POSTGRES_BASE_IMAGE=postgres:17.10-bookworm@sha256:17b6c778de50f4bb9a878c36e736110fbcd9b7020377d6fdfdf20f7c0347e40a
FROM ${POSTGRES_BASE_IMAGE}
ARG POSTGRES_BASE_IMAGE

# The restore-drill operator resolves this image to its immutable local image
# ID and requires this label to match HOME_AGENT_POSTGRES_IMAGE. A mutable tag
# can therefore never silently change the PostgreSQL base used for recovery.
LABEL org.opencontainers.image.base.name="${POSTGRES_BASE_IMAGE}" \
      org.engineeredlighting.home-agent.role="postgres-pgbackrest"

RUN apt-get update \
    && apt-get install --no-install-recommends -y pgbackrest ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/spool/pgbackrest /run/pgbackrest-sftp \
    && chown postgres:postgres /var/spool/pgbackrest /run/pgbackrest-sftp
