ARG POSTGRES_BASE_IMAGE=postgres:17.10-bookworm@sha256:17b6c778de50f4bb9a878c36e736110fbcd9b7020377d6fdfdf20f7c0347e40a
FROM ${POSTGRES_BASE_IMAGE}

RUN apt-get update \
    && apt-get install --no-install-recommends -y pgbackrest ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/spool/pgbackrest \
    && chown postgres:postgres /var/spool/pgbackrest
