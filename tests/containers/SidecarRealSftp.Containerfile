FROM docker.io/library/debian:bookworm-slim

LABEL org.opencontainers.image.title="traction-control synthetic SFTP target"
LABEL org.opencontainers.image.description="Disposable key-only SFTP target for real private-sidecar integration tests"

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        openssh-server \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /usr/sbin/nologin --uid 10001 sidecarbackup \
    && passwd -d sidecarbackup \
    && install -d -m 0755 /run/sshd \
    && install -d -m 0700 -o sidecarbackup -g sidecarbackup /home/sidecarbackup/repository

COPY sidecar-real/sshd_config /etc/ssh/sshd_config
COPY sidecar-real/start-sftp-target.sh /usr/local/bin/start-sftp-target

RUN chmod 0755 /usr/local/bin/start-sftp-target \
    && chmod 0644 /etc/ssh/sshd_config

EXPOSE 22

ENTRYPOINT ["/usr/local/bin/start-sftp-target"]
