# Portfolio sidecar SFTP target image

This image is the storage-side process for one private-mesh sidecar replica.
It exposes key-only SFTP and persists only the client-encrypted Restic
repository mounted at `/srv/portfolio-sidecar/repository`. It does not contain
WireGuard, a Podman API client, portfolio source, or coordinator credentials.

The physical repository is chrooted and appears to the SFTP client as
`/repository`. A corresponding coordinator repository URI therefore ends in
`:/repository`, while its separate target configuration supplies the SFTP
port.

The container expects two Podman mount secrets:

- `/run/secrets/sidecar-host-key`: an Ed25519 OpenSSH host private key
- `/run/secrets/sidecar-authorized-keys`: one or more plain Ed25519 public keys

The entrypoint copies them into `/run/sshd`, derives the host public key, and
rewrites each client key with OpenSSH's `restrict` option. It refuses missing,
malformed, oversized, non-Ed25519, or duplicate key material. The source
secrets remain read-only Podman mounts.

Run it rootless with a read-only root filesystem, writable tmpfs mounts at
`/run` and `/tmp`, `no-new-privileges`, and only these capabilities:
`CHOWN`, `DAC_OVERRIDE`, `SETGID`, `SETUID`, and `SYS_CHROOT`. Publish only a
high port (the default is `2222`) on the host's WireGuard address. Do not
publish the SFTP port on a public or wildcard address.

The tracked image pins its Debian base manifest. Build and review a local
image, then make the deployment configuration refer to the reviewed image by
digest. Podman secrets are copied into a container when it is created, so
recreate the container after rotating either SSH secret.

Run the isolated smoke proof with:

```bash
bash tests/test_portfolio_sidecar_sftp_image_podman.sh
```

The harness creates disposable keys, secrets, a network, a volume, a
container, and an image tag. It verifies SFTP upload/download, chrooting,
password and remote-shell rejection, the read-only root, the requested
capability set, and volume persistence across container recreation, then
removes every test artifact.
