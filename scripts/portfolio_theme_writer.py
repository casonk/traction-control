#!/usr/bin/env python3
"""Answer POST /_portfolio/global: make one theme the global theme every site follows.

A site's top bar posts here (same origin, through the shared ingress) when "Everywhere" is
chosen. The service binds to 127.0.0.1 only and the ingress decides who may reach it (mTLS on
the home ingress). It adds two checks of its own so a page on another origin cannot drive it:
the request must carry ``X-Portfolio-Global: 1`` (a custom header forces a CORS preflight, which
this service never grants), its ``Host`` must be loopback or the name of a site the shared Caddy
fronts (a DNS-rebinding page sends its own hostname, which matches its own ``Origin``), and, when
the browser sends ``Origin``, its host must be the ``Host`` the request was addressed to. The body is ``{"theme": "<name>"}``; the name must be one of the
offered themes, and anything else changes nothing.

    python3 scripts/portfolio_theme_writer.py [--port 8767]
"""

from __future__ import annotations

import argparse
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "themes"))  # portfolio_site
import portfolio_site as seam  # noqa: E402
import portfolio_themes as pt  # noqa: E402

PATH = "/_portfolio/global"
MAX_BODY = 1024
DEFAULT_PORT = 8767


def allowed_hosts() -> frozenset[str]:
    """The names this writer is reached by: every site the shared Caddy fronts.

    Each Caddy site proxies ``/_portfolio/global`` here with its own ``Host``, so the list is
    the wiring-harness registry's hostnames plus the central store's (and
    ``PORTFOLIO_ALLOWED_HOSTS``); loopback is always allowed.
    """
    portfolio_root = Path(__file__).resolve().parents[3]
    from_env = seam.Site(Path("."), None, "none", {}).allowed_hosts()  # PORTFOLIO_ALLOWED_HOSTS
    return seam.registry_hostnames(portfolio_root) | seam.store_hostnames() | from_env


def host_ok(headers) -> bool:
    """DNS-rebinding guard: the request's own Host must be a name this writer is reached by."""
    return seam.host_allowed(headers.get("Host", ""), allowed_hosts())


def same_origin(headers) -> bool:
    origin = headers.get("Origin")
    return origin is None or urlsplit(origin).netloc == headers.get("Host", "")


def handle(headers, body: bytes) -> tuple[int, dict]:
    """The status and JSON reply for one POST; the only place a request reaches the theme layer."""
    if headers.get("X-Portfolio-Global") != "1" or not host_ok(headers) or not same_origin(headers):
        return HTTPStatus.FORBIDDEN, {"error": "not allowed"}
    try:
        name = json.loads(body).get("theme")
    except (ValueError, AttributeError):
        return HTTPStatus.BAD_REQUEST, {"error": "send {\"theme\": name}"}
    if not isinstance(name, str):
        return HTTPStatus.BAD_REQUEST, {"error": "send {\"theme\": name}"}
    try:
        pt.set_global(name)
    except pt.LocalThemeError as exc:
        return HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)}
    return HTTPStatus.OK, {"global": name}


class Handler(BaseHTTPRequestHandler):
    server_version = "portfolio-theme-writer"

    def _reply(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != PATH:
            return self._reply(HTTPStatus.NOT_FOUND, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY:
            return self._reply(HTTPStatus.BAD_REQUEST, {"error": "bad body"})
        status, payload = handle(self.headers, self.rfile.read(length))
        self._reply(status, payload)

    def log_message(self, *args) -> None:  # keep names of themes out of logs; status is enough
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
