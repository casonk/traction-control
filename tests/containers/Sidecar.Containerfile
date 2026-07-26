FROM docker.io/library/python:3.11-slim-bookworm

LABEL org.opencontainers.image.title="traction-control private-sidecar regression test"
LABEL org.opencontainers.image.description="Offline Linux tests for hosted-encrypted and mesh-only ignored-state sidecars"

ENV DEBIAN_FRONTEND=noninteractive \
    HOME=/tmp/home \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/scripts

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
    && rm -rf /var/lib/apt/lists/*

COPY scripts/ /opt/scripts/
COPY tests/ /opt/tests/

USER 65532:65532

ENTRYPOINT ["python3", "-m", "unittest", "discover", "-s", "/opt/tests", "-p", "test_portfolio_sidecar*.py", "-v"]
