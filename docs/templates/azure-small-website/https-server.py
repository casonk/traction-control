#!/usr/bin/env python3
"""Serve /var/www/html over HTTPS and redirect port 80."""

import ssl
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = "/var/www/html"
CERT = "/etc/azure-website/fullchain.pem"
KEY = "/etc/azure-website/privkey.pem"


class RedirectHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _redirect(self):
        host = (self.headers.get("Host") or "").split(":")[0] or "127.0.0.1"
        target = f"https://{host}{self.path}"
        body = f"Moved to {target}\n".encode()
        self.send_response(301)
        self.send_header("Location", target)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._redirect()

    def do_HEAD(self):
        host = (self.headers.get("Host") or "").split(":")[0] or "127.0.0.1"
        target = f"https://{host}{self.path}"
        self.send_response(301)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()


class FileHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)


def serve_http():
    ThreadingHTTPServer(("0.0.0.0", 80), RedirectHandler).serve_forever()


def serve_https():
    server = ThreadingHTTPServer(("0.0.0.0", 443), FileHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(CERT, KEY)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    threading.Thread(target=serve_http, daemon=True).start()
    serve_https()
