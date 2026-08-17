# Air-Primary Render Orchestration

`scripts/render_air_primary.py` coordinates an inert service bundle while the
MacBook Air is the temporary WireGuard hub. It is a render orchestrator, not an
installer or failover authority. WireGuard reachability does not grant CRUD
writer leadership, storage quorum, or permission to mutate host services.

## Ownership Boundary

- Traction Control owns the ignored local declaration, deterministic staged
  inputs, prerequisite/API checks, and aggregate generation manifest.
- Clockwork owns its native user LaunchAgent renderer.
- Snowbridge owns its native macOS SMB/PF review-plan renderer. When native SMB
  is enabled, its generated plan remains under Snowbridge's ignored
  `artifacts/traction-control-air-primary/generation-N/` boundary. When it is
  disabled, the coordinator does not probe or invoke that renderer and creates
  no SMB input, log, plan, or PF artifact.
- wiring-harness owns the IP-literal, WireGuard-bound mTLS Caddy renderer.
  It attests that the declared Air `/32` is assigned to the declared exact
  `utunN` interface before it renders.

The coordinator invokes those sibling entry points in place. It does not copy
or patch their renderer code and does not bypass their repository boundaries.

## Local Configuration

Create the ignored owner-only file without overwriting an existing one:

```bash
python3 scripts/render_air_primary.py init
chmod 600 config/air-primary.local.toml
```

Replace every placeholder in `config/air-primary.local.toml`. The declaration
pins the current `utunN` interface, distinct RFC 1918 Air/mini/pro `/32`s, exact
sibling checkout paths, Python and Caddy executables, Clockwork environment
file, owner-only Snowbridge share, and owner-only certificate directory. The
fixed reviewed ports are:

| Surface | Backend | Air mTLS edge |
|---|---:|---:|
| Clockwork | `127.0.0.1:5001` | Air WireGuard IP `:8443` |
| Snowbridge web (optional) | `127.0.0.1:8080` | Air WireGuard IP `:8444` |

Snowbridge web is absent from the staged wiring registry unless
`web_backend_8080_enabled = true`. Enabling that declaration does not start or
verify the backend; it only allows wiring-harness to render the reviewed role.
Snowbridge's ignored local File Browser declaration remains owned and operated
by Snowbridge; this coordinator does not invoke it.

Native SMB plan rendering is an independent, strict switch. The tracked
example explicitly defaults `native_smb_enabled = true`, preserving the
existing fail-closed audit and render path. Setting it to `false` skips only
native SMB share/account/inventory prerequisites, the Snowbridge SMB CLI
probes and child render, and SMB/PF artifacts. It does not enable, disable, or
otherwise change `web_backend_8080_enabled`:

| Native SMB | Web backend | SMB/PF review artifacts | Snowbridge web role |
|---|---|---|---|
| `true` | `false` | rendered | omitted |
| `true` | `true` | rendered | rendered |
| `false` | `false` | omitted | omitted |
| `false` | `true` | omitted | rendered |

Native SMB disabled means the coordinator did not audit or render native SMB.
It is not evidence that host SMB or PF state is safe.

The Clockwork environment file must be mode `0600` and contain a
`CLOCKWORK_WEB_SECRET` of at least 32 characters. The coordinator reads only
that key's presence and length and never copies or reports its value.

## Validate, Then Render

```bash
python3 scripts/render_air_primary.py validate
python3 scripts/render_air_primary.py render
```

Validation checks exact sibling Git roots and records their current commits,
dirty state, and renderer-source hashes. A dirty sibling worktree is recorded,
not silently rejected, because the probed current CLI is the API being used.
Missing help flags or output shapes fail closed as API drift.

The command reports one of four failure categories with stable exit codes:

| Category | Exit | Meaning |
|---|---:|---|
| `unsafe_configuration` | 2 | insecure mode/path/value, host audit refusal, or safety invariant failure |
| `missing_prerequisite` | 3 | executable, environment, enabled-native-SMB share, certificate, or dependency is absent |
| `api_mismatch` | 4 | sibling CLI contract or rendered artifact shape changed |
| `render_failure` | 5 | an otherwise valid render could not complete |

Successful Traction Control output is written below
`artifacts/air-primary/generation-N/`, including private staged TOML, child
stdout/stderr logs, Clockwork and wiring-harness outputs, a prerequisite report,
and the aggregate hash manifest. All files are owner-only. When native SMB is
enabled, Snowbridge's two inert outputs stay in its own ignored artifact tree
and are referenced by hash. When it is disabled, the manifest records the
false setting and an explicit omission reason, hashes only the four Clockwork
and wiring-harness artifacts, and creates no Snowbridge generation.

A generation is immutable. A pre-existing Traction Control generation always
causes refusal. A matching Snowbridge generation also causes refusal when
native SMB is enabled; it is neither consulted nor modified when native SMB is
disabled. A failure after Traction Control generation creation writes
`failure-report.json` when possible and consumes that generation; inspect it
and increment the configured generation rather than deleting or editing
evidence in place.

## Explicitly Unsupported Live Steps

The coordinator never runs `sudo`, `launchctl`, a Caddy start command, SMB
sharing mutation, PF rule loading, key/certificate generation, or a WireGuard
network change. Generated LaunchAgents, Caddy configuration, PF text, and SMB
commands are review evidence only. Live activation requires a separate,
operator-supervised workflow after the relevant backend, certificate, tunnel,
firewall, rollback, and reachability checks are independently satisfied.

## Networkless Real-Renderer Regression

```bash
bash tests/test_air_primary_coordinator_podman.sh
```

The runner builds an explicit allowlisted context from the coordinator and the
current Clockwork, Snowbridge, and wiring-harness renderer sources. It never
copies host `.git` directories, ignored configuration, credentials, or the
whole portfolio. Its disposable container has no network or host mounts, uses
a read-only root and `no-new-privileges`, and drops every capability except
`NET_ADMIN`, which is used only to assign the synthetic Air `/32` to a dummy
`utun7` inside that container's isolated namespace. The synthetic Caddy binary
has no low-port file capability because the reviewed edges use `8443`/`8444`.

The five cases prove:

1. The three unchanged real child CLIs and real `caddy validate` produce the
   expected owner-only, hashed, render-only bundle without leaking the test
   secret.
2. Reusing the successful generation is refused without changing its evidence.
3. A fresh generation with an unsafe wildcard TCP 445 inventory fails in the
   Snowbridge audit before wiring-harness renders.
4. That failed generation remains consumed and its failure report remains
   unchanged on a second attempt.
5. An explicitly disabled native SMB slice produces no SMB prerequisite read,
   child invocation, log, or artifact while the independently enabled
   `127.0.0.1:8080` web role still renders through Caddy.

This regression proves Linux-container composition and fail-closed behavior.
It does not prove macOS `sharing`, PF loading, launchd activation, a real
WireGuard handshake, backend availability, or application writer failover.
