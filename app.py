"""buffetAgent static site service for Mac mini deployment.

This is a tiny WSGI app so production can run it with gunicorn while local
checks can use the stdlib fallback:

    python app.py --host 127.0.0.1 --port 8087
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from site_config import public_base_url

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "simple-html"

mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("image/svg+xml", ".svg")


def _status_response(start_response, status: str, payload: dict, extra_headers=None):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    start_response(status, headers)
    return [body]


def _security_headers(path: str) -> list[tuple[str, str]]:
    headers = [
        ("X-Frame-Options", "SAMEORIGIN"),
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ]
    if path.endswith((".html", "/")):
        headers.append(("Cache-Control", "public, max-age=300, must-revalidate"))
    elif path.endswith((".svg", ".webmanifest", ".css", ".js", ".png", ".jpg", ".jpeg", ".ico")):
        headers.append(("Cache-Control", "public, max-age=86400"))
    else:
        headers.append(("Cache-Control", "public, max-age=1800"))
    return headers


def _request_path(environ: dict) -> str:
    raw = environ.get("RAW_URI") or environ.get("REQUEST_URI") or environ.get("PATH_INFO") or "/"
    raw = raw.split("?", 1)[0]
    path = unquote(raw)
    if not path.startswith("/"):
        path = "/" + path
    if path == "/":
        return "/index.html"
    return path


def _file_for_path(path: str) -> Path | None:
    target = (STATIC_DIR / path.lstrip("/")).resolve()
    try:
        target.relative_to(STATIC_DIR.resolve())
    except ValueError:
        return None
    if target.is_dir():
        target = target / "index.html"
    if not target.exists() or not target.is_file():
        return None
    return target


def app(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if method not in {"GET", "HEAD"}:
        return _status_response(start_response, "405 Method Not Allowed", {"error": "method_not_allowed"})

    path = _request_path(environ)
    if path == "/api/health":
        return _status_response(
            start_response,
            "200 OK",
            {
                "status": "ok",
                "service": "buffetAgent",
                "public_base_url": public_base_url(),
                "time_utc": datetime.now(timezone.utc).isoformat(),
            },
        )

    file_path = _file_for_path(path)
    if file_path is None:
        return _status_response(start_response, "404 Not Found", {"error": "not_found", "path": path})

    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    size = file_path.stat().st_size
    headers = [
        ("Content-Type", content_type),
        ("Content-Length", str(size)),
        *(_security_headers(path)),
    ]
    start_response("200 OK", headers)
    if method == "HEAD":
        return [b""]
    return file_path.open("rb")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8087")))
    args = parser.parse_args()

    from wsgiref.simple_server import make_server

    with make_server(args.host, args.port, app) as server:
        print(f"buffetAgent static site -> http://{args.host}:{args.port}")
        server.serve_forever()

