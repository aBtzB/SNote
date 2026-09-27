"""Loopback-only HTTP adapter. The application and tests share the same routes."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
from pathlib import Path
import re
import shutil
from urllib.parse import parse_qs, urlsplit

from .domain import (EXTENSIONS, LANGUAGES, MAX_UPLOAD, MODELS, Problem,
                     apply_edits, new_project, sample_project, validate_options)
from .engines import LocalEngines
from .exports import export
from .jobs import Jobs
from .models import ModelManager
from .store import Store

STATIC = Path(__file__).parent / "static"
log = logging.getLogger(__name__)


def revision(payload):
    value = payload.get("revision")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Problem("Include the recording revision when saving or starting a job.")
    return value


def summary(project):
    return {**{k: v for k, v in project.items() if k not in {"segments", "peaks"}},
            "segment_count": len(project["segments"]),
            "reviewed_count": sum(s["reviewed"] for s in project["segments"])}


class Application:
    def __init__(self, root, engines=None):
        self.store = Store(root)
        self.store.recover()
        self.engines = engines or LocalEngines(root)
        self.jobs = Jobs(self.store, self.engines)
        self.models = ModelManager(root, self.engines)

    def close(self):
        self.models.close()
        self.jobs.close()


class Handler(BaseHTTPRequestHandler):
    server_version = "SNote"
    sys_version = ""

    @property
    def app(self):
        return self.server.app

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, pattern, *args):
        log.debug(pattern, *args)

    def guard(self, mutation):
        host = self.headers.get("Host", "")
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            allowed |= {"127.0.0.1", "localhost"}
        if host not in allowed:
            raise Problem("SNote only accepts requests from localhost.", 403)
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{host}":
            raise Problem("Cross-origin requests are not allowed.", 403)
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise Problem("Cross-site requests are not allowed.", 403)
        if mutation and self.headers.get("X-SNote-Request") != "1":
            raise Problem("Missing application request header.", 403)

    def send_headers(self, code, content_type, length, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def respond(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_headers(status, "application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def size(self, maximum):
        if self.headers.get("Transfer-Encoding"):
            raise Problem("Use a fixed-length request body.")
        try:
            size = int(self.headers["Content-Length"])
        except (TypeError, ValueError):
            raise Problem("A Content-Length header is required.", 411) from None
        if size <= 0:
            raise Problem("The file or request body is empty.")
        if size > maximum:
            raise Problem(f"The request exceeds the {maximum // (1024 * 1024)} MB limit.", 413)
        return size

    def body(self):
        if self.headers.get_content_type() != "application/json":
            raise Problem("Send JSON for this request.", 415)
        size = self.size(8 * 1024 * 1024)
        try:
            value = json.loads(self.rfile.read(size))
        except (UnicodeDecodeError, ValueError):
            raise Problem("The request body is not valid JSON.") from None
        if not isinstance(value, dict):
            raise Problem("Send a JSON object.")
        return value

    def file(self, path, content_type=None, ranges=False):
        if not path.is_file():
            raise Problem("File not found.", 404)
        total = path.stat().st_size
        start, end, code = 0, total - 1, 200
        extra = {"Accept-Ranges": "bytes"} if ranges else {}
        requested = self.headers.get("Range") if ranges else None
        if requested:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
            try:
                if not match or not any(match.groups()) or total == 0:
                    raise ValueError
                a, b = match.groups()
                if not a:
                    if int(b) <= 0:
                        raise ValueError
                    start = max(0, total - int(b))
                else:
                    start = int(a)
                    end = min(int(b), end) if b else end
                if start > end or start >= total:
                    raise ValueError
            except ValueError:
                self.send_headers(416, "text/plain", 0, {"Content-Range": f"bytes */{total}"})
                return
            code = 206
            extra["Content-Range"] = f"bytes {start}-{end}/{total}"
        length = max(0, end - start + 1)
        mime = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_headers(code, mime, length, extra)
        with path.open("rb") as stream:
            stream.seek(start)
            while length > 0:
                chunk = stream.read(min(length, 64 * 1024))
                if not chunk:
                    break
                self.wfile.write(chunk)
                length -= len(chunk)

    def upload(self, query):
        if not self.app.engines.capabilities()["speech_installed"]:
            raise Problem("Install the audio dependencies first. Open Models for the commands.", 503)
        filename = query.get("filename", "").replace("\\", "/").rsplit("/", 1)[-1]
        if not filename or len(filename) > 200 or any(ord(c) < 32 for c in filename):
            raise Problem("Choose an audio file with a valid filename.")
        if Path(filename).suffix.lower() not in EXTENSIONS:
            raise Problem("Choose WAV, MP3, M4A, FLAC, OGG, OPUS, AAC, or WEBM audio.")
        model, source, target = query.get("model", "tiny"), query.get("source", "auto"), query.get("target", "")
        validate_options(model, source, target)
        size = self.size(MAX_UPLOAD)
        project = new_project(filename, model, source, target)
        folder = self.app.store.root / "recordings" / project["id"]
        folder.mkdir(parents=True)
        created = False
        try:
            with (folder / "source").open("wb") as output:
                remaining = size
                while remaining:
                    chunk = self.rfile.read(min(remaining, 64 * 1024))
                    if not chunk:
                        raise Problem("The upload was interrupted. Please try again.")
                    output.write(chunk)
                    remaining -= len(chunk)
            self.app.store.create(project)
            created = True
            queued = self.app.jobs.submit(project["id"])
        except Exception:
            if created:
                self.app.store.delete(project["id"])
            shutil.rmtree(folder, ignore_errors=True)
            raise
        self.respond(queued, 202)

    def route(self):
        self.guard(self.command != "GET")
        parsed = urlsplit(self.path)
        path = parsed.path
        query = {key: values[0] for key, values in parse_qs(parsed.query, keep_blank_values=True).items()}
        if self.command == "GET":
            static = {"/": "index.html", "/app.js": "app.js", "/styles.css": "styles.css", "/favicon.svg": "favicon.svg"}
            if path in static:
                return self.file(STATIC / static[path])
            if path == "/api/config":
                return self.respond({"languages": LANGUAGES, "models": MODELS,
                    "max_upload": MAX_UPLOAD, **self.app.engines.capabilities()})
            if path == "/api/projects":
                return self.respond([summary(p) for p in self.app.store.list()])
            if path == "/api/models":
                return self.respond(self.app.models.snapshot())
        if self.command == "POST" and path == "/api/models/catalog":
            return self.respond(self.app.models.start("catalog"), 202)
        if self.command == "POST" and path == "/api/models/download":
            payload = self.body()
            kind, model_id = payload.get("kind"), payload.get("id")
            if kind not in ("speech", "translation") or not isinstance(model_id, str):
                raise Problem("Choose a model from the list.")
            return self.respond(self.app.models.start(kind, model_id), 202)
        if self.command == "POST" and path == "/api/models/cancel":
            return self.respond(self.app.models.cancel())
        if self.command == "POST" and path == "/api/projects":
            return self.upload(query)
        if self.command == "POST" and path == "/api/sample":
            return self.respond(self.app.store.create(sample_project()), 201)
        match = re.fullmatch(r"/api/projects/([a-f0-9]{32})(?:/(audio|export|translate|retry|cancel))?", path)
        if not match:
            raise Problem("Page not found.", 404)
        project_id, action = match.groups()
        project = self.app.store.get(project_id)
        if self.command == "GET" and action is None:
            return self.respond(project)
        if self.command == "GET" and action == "audio":
            if not project["has_audio"]:
                raise Problem("No playable audio is available for this recording.", 404)
            return self.file(self.app.store.root / "recordings" / project_id / "playback.wav", "audio/wav", ranges=True)
        if self.command == "GET" and action == "export":
            kind = query.get("format", "txt")
            result, mime = export(project, kind, query.get("content", "original"))
            body = result.encode("utf-8")
            name = re.sub(r"[^a-zA-Z0-9_-]+", "-", project["title"]).strip("-")[:70] or "transcript"
            self.send_headers(200, mime + "; charset=utf-8", len(body),
                              {"Content-Disposition": f'attachment; filename="{name}.{kind}"'})
            return self.wfile.write(body)
        if self.command == "PATCH" and action is None:
            payload = self.body()
            return self.respond(self.app.store.mutate(project_id, lambda p: apply_edits(p, payload), revision(payload)))
        if self.command == "DELETE" and action is None:
            self.app.store.delete(project_id)
            shutil.rmtree(self.app.store.root / "recordings" / project_id, ignore_errors=True)
            return self.respond({"deleted": True})
        if self.command == "POST" and action == "cancel":
            return self.respond(self.app.jobs.cancel(project_id))
        if self.command == "POST" and action in {"translate", "retry"}:
            payload = self.body()
            target = payload.get("target", "")
            if action == "translate" and (not isinstance(target, str) or target not in LANGUAGES):
                raise Problem("Choose a translation language.")
            return self.respond(self.app.jobs.submit(project_id,
                "translate" if action == "translate" else "transcribe", target, revision(payload)), 202)
        raise Problem("Method not allowed.", 405)

    def dispatch(self):
        try:
            self.route()
        except Problem as exc:
            self.respond({"error": str(exc)}, exc.status)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            log.exception("Request failed")
            self.respond({"error": "Something went wrong. See the terminal for details."}, 500)

    do_GET = dispatch
    do_POST = dispatch
    do_PATCH = dispatch
    do_DELETE = dispatch


def make_server(root, port=8765, engines=None):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.app = Application(root, engines)
    return server
