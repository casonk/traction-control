FROM docker.io/restic/restic:0.18.0 AS restic

FROM docker.io/library/python:3.11-slim-bookworm

LABEL org.opencontainers.image.title="traction-control real sidecar coordinator test"
LABEL org.opencontainers.image.description="Synthetic coordinator for isolated real SFTP, Restic backup, and restore-drill integration"

ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/tmp/home \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/scripts

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY --from=restic /usr/bin/restic /usr/bin/restic
COPY scripts/ /opt/scripts/
COPY sidecar-real/run-real-sidecar-test.sh /usr/local/bin/run-real-sidecar-test

RUN chmod 0755 /usr/local/bin/run-real-sidecar-test \
    && chmod 0755 /opt/scripts/*.py

ENTRYPOINT ["/usr/local/bin/run-real-sidecar-test"]
