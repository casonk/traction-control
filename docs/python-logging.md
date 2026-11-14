# Python Logging Convention

How portfolio Python services, CLIs and scheduled jobs log. The goal is one place to look
(`journalctl --user -t <repo>`), one line format, and no secrets in the journal.

Reference implementation: `session-control/src/session_control/logging_setup.py`.

## Rules

1. **Standard library `logging` only.** Each module declares
   `logger = logging.getLogger(__name__)`. Do not add a logging dependency.
2. **Configure once, at the entry point.** The CLI `main()` or service start-up calls a
   single `configure_logging(level)`. Library modules never call `basicConfig`, add
   handlers or change levels. The call is idempotent: a second call does not stack handlers.
3. **Log to stderr.** Under systemd stderr is the journal, so no log files and no rotation
   to maintain. Normal command output (results, JSON) stays on stdout; `print` is not a
   logging call.
4. **One format:** `%(asctime)s %(levelname)s %(name)s: %(message)s`.
5. **Level control:** a global `--log-level {debug,info,warning,error,critical}` option and
   a `<REPO>_LOG_LEVEL` environment variable (for example `SESSION_CONTROL_LOG_LEVEL`).
   The flag wins over the variable; the default is `INFO`. Unknown values fall back to
   `INFO` rather than failing.
6. **Journal identity:** set `SyslogIdentifier=<repo-name>` on every service and timer unit
   (Clockwork: `syslog_identifier = "<repo-name>"`). Services and their timers then share
   one `journalctl -t` filter.
7. **Redact before emitting.** Services that handle private content (sessions, messages,
   credentials, receipts, locations) attach a filter that masks secrets in the *formatted*
   message and in exception text, because both can carry tokens and request URLs. Never
   log secrets, tokens, transcripts, message bodies, private hostnames or personal data,
   and do not rely on callers having sanitised their arguments.
8. **Use lazy formatting** (`logger.info("removed %s", name)`), not f-strings.

## What to log

| Level | Use for |
|---|---|
| `ERROR` | An operation failed and the caller or user is affected (a removal aborted, a write failed). |
| `WARNING` | Something was refused or degraded without failing the request: a rejected request (bad Host, CSRF, auth), a fallback taken, a background job that failed. |
| `INFO` | Meaningful state changes and run summaries: start-up with bind address, a record removed or written, a batch finished with its counts. |
| `DEBUG` | Per-item and per-request detail. Polled endpoints and tight loops log at `DEBUG` only. |

**A swallowed exception must leave a trace.** `except Exception: return` in a fallback or
background worker is the main source of "it silently stopped working". Log it at
`WARNING` with the reason before falling back, and keep a structured record (for example a
failure file) when the work can be retried.

Include stable identifiers (ids, counts, reasons), not content.

## Tests

Assert on logs with `caplog` (or `dyno_lab.log.LogCapture`). A test that calls the CLI
`main()` installs the stderr handler on the root logger, so restore the root handlers and
level in an autouse fixture, or later tests inherit a handler bound to a stale stream.

## Adopting it in an existing repo

Most repos already use `logging.getLogger(__name__)` and a `--log-level` flag. To align one:
add the redacting filter if it handles private data, unify the format string, add the
`<REPO>_LOG_LEVEL` fallback, set `SyslogIdentifier`, and convert `print(..., file=sys.stderr)`
diagnostics to logger calls. Do this when touching the repo for other reasons; it is not a
reason to open a portfolio-wide change.
