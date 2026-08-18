"""A local server so the page can trigger a scan on demand.

The browser cannot clone a repository, so scanning has to happen here. The
split of work matters for how it feels to use: opening the page reads
whatever was scanned last, from disk, instantly. Nothing touches GitHub
until the Update button is pressed.

Binds to the loopback interface only -- this exposes the contents of your
source repositories, and there is no authentication.
"""

import json
import os
import posixpath
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from . import config as config_mod
from . import extract
from .model import SOURCES

CACHE_NAME = "keys.json"


def _cache_path(cfg):
    cache = config_mod.resolve(cfg, cfg.get("repo_cache", ".cache/repos"))
    return os.path.join(os.path.dirname(cache) or ".", CACHE_NAME)


def read_cache(cfg):
    try:
        with open(_cache_path(cfg), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def write_cache(cfg, payload):
    path = _cache_path(cfg)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return path


def scan(cfg, fetch=True):
    """Clone/refresh each source repository and extract its keys."""
    out = {"scanned_at": time.strftime("%Y-%m-%d %H:%M"), "sources": {}}
    cache_dir = config_mod.resolve(cfg, cfg.get("repo_cache", ".cache/repos"))

    for name in SOURCES:
        spec = cfg["sources"].get(name, {})
        entry = {"repo": spec.get("repo") or spec.get("root", ""),
                 "ref": spec.get("ref") or "", "keys": {}, "error": ""}
        if spec.get("kind") != "code":
            entry["note"] = "not a code source"
            out["sources"][name] = entry
            continue
        try:
            root = extract.ensure_repo(dict(spec, name=name), cache_dir, update=fetch)
            if not os.path.isdir(root):
                raise RuntimeError("repository directory not found: %s" % root)
            entry["keys"] = extract.code_keys(
                dict(spec, root=root), cfg["patterns"], cfg["exclude_dirs"]
            )
            entry["commit"] = extract.repo_head(root)
        except (RuntimeError, OSError) as exc:
            entry["error"] = str(exc)
        out["sources"][name] = entry
    return out


def make_handler(cfg, web_dir):
    lock = threading.Lock()

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            SimpleHTTPRequestHandler.__init__(self, *a, directory=web_dir, **kw)

        def log_message(self, fmt, *a):
            # The default logger writes a line per asset; only the API calls
            # are worth seeing while the page is being used.
            if "/api/" in (self.path or ""):
                SimpleHTTPRequestHandler.log_message(self, fmt, *a)

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def guess_type(self, path):
            """Declare the charset on text responses.

            SimpleHTTPRequestHandler sends bare `text/html`, and a browser
            with no charset falls back to its locale default -- Windows-1252
            here -- so every em dash in a UTF-8 page arrives as "a€".
            """
            base = SimpleHTTPRequestHandler.guess_type(self, path)
            kind = base.split(";", 1)[0].strip() if isinstance(base, str) else base
            if kind in ("text/html", "text/css", "text/plain", "text/csv",
                        "text/javascript", "application/javascript",
                        "application/json"):
                return kind + "; charset=utf-8"
            return base

        def translate_path(self, path):
            # Serve index.html at the root without exposing anything above
            # web_dir; SimpleHTTPRequestHandler already confines to
            # `directory`, this only maps "/" onto the page.
            if posixpath.splitext(path.split("?", 1)[0])[1] == "":
                return os.path.join(web_dir, "index.html")
            return SimpleHTTPRequestHandler.translate_path(self, path)

        def do_GET(self):
            if self.path.startswith("/api/keys"):
                cached = read_cache(cfg)
                return self._json(cached or {"sources": {}, "scanned_at": ""})
            if self.path.startswith("/api/"):
                return self._json({"error": "unknown endpoint"}, 404)
            return SimpleHTTPRequestHandler.do_GET(self)

        def do_POST(self):
            if not self.path.startswith("/api/scan"):
                return self._json({"error": "unknown endpoint"}, 404)
            # One scan at a time: two concurrent git resets on the same
            # clone would fight over the working tree.
            if not lock.acquire(blocking=False):
                return self._json({"error": "a scan is already running"}, 409)
            try:
                fetch = "no-fetch" not in self.path
                payload = scan(cfg, fetch=fetch)
                write_cache(cfg, payload)
                return self._json(payload)
            except Exception as exc:  # surfaced in the page, not swallowed
                return self._json({"error": str(exc)}, 500)
            finally:
                lock.release()

    return Handler


def serve(cfg, host="127.0.0.1", port=8765, web_dir=None):
    web_dir = web_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"
    )
    httpd = ThreadingHTTPServer((host, port), make_handler(cfg, web_dir))
    return httpd
