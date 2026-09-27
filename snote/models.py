"""User-started model downloads. Importing this module never accesses the network."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import threading
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .domain import LANGUAGES, SPEECH_MODELS, Problem
from .engines import Cancelled, checkpoint
from .translation import install_pack, installed

INDEX_URL = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"
SPEECH_FILES = {"config.json", "preprocessor_config.json", "model.bin", "tokenizer.json",
                "vocabulary.txt", "vocabulary.json"}
REQUIRED_SPEECH_FILES = {"config.json", "model.bin", "tokenizer.json"}
ACTIVE_DOWNLOAD = {"fetching", "downloading", "installing"}


def speech_folder(root, name):
    folder = root / "models" / "speech" / name
    ready = any((folder / marker).is_file() for marker in ("snote-ready.json", "sidenote-ready.json"))
    if ready and all((folder / f).is_file() for f in REQUIRED_SPEECH_FILES):
        return folder
    # Preserve compatibility with the previous command-line installer.
    repo = SPEECH_MODELS[name]["repo"].replace("/", "--")
    snapshots = root / "models" / "whisper" / ("models--" + repo) / "snapshots"
    if snapshots.exists():
        for snapshot in sorted(snapshots.iterdir(), reverse=True):
            if all((snapshot / f).is_file() for f in REQUIRED_SPEECH_FILES):
                return snapshot
    return None


def check_https(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError("The model source must use HTTPS.")


class HTTPSRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ModelSource:
    def __init__(self):
        self.opener = build_opener(HTTPSRedirect())

    def open(self, url):
        check_https(url)
        return self.opener.open(Request(url, headers={"User-Agent": "SNote/0.2", "Accept-Encoding": "identity"}), timeout=30)

    def json(self, url):
        with self.open(url) as response:
            content = response.read(4 * 1024 * 1024 + 1)
        if len(content) > 4 * 1024 * 1024:
            raise ValueError("The model catalogue response is unexpectedly large.")
        return json.loads(content)


def normalize_catalog(raw):
    if not isinstance(raw, list):
        raise ValueError("The language catalogue has an unexpected format.")
    catalog = {}
    for entry in raw:
        if not isinstance(entry, dict) or entry.get("type", "translate") != "translate":
            continue
        source, target = entry.get("from_code"), entry.get("to_code")
        if source not in LANGUAGES or target not in LANGUAGES or source == target:
            continue
        version = str(entry.get("package_version", ""))
        if not re.fullmatch(r"\d+(?:\.\d+)*", version):
            continue
        links = entry.get("links", [])
        if not isinstance(links, list):
            continue
        urls = [url for url in links if isinstance(url, str) and urlsplit(url).scheme == "https"
                and urlsplit(url).hostname in {"argos-net.com", "www.argosopentech.com", "argosopentech.com"}]
        if not urls:
            continue
        model_id = f"{source}-{target}"
        previous = catalog.get(model_id)
        if previous and tuple(map(int, previous["version"].split("."))) >= tuple(map(int, version.split("."))):
            continue
        catalog[model_id] = {"id": model_id, "source": source, "target": target, "version": version,
                             "label": f"{LANGUAGES[source]} → {LANGUAGES[target]}", "url": urls[0]}
    if not catalog:
        raise ValueError("The catalogue contains no supported language pairs.")
    return catalog


class ModelManager:
    def __init__(self, root, engines, source=None, installer=install_pack):
        self.root, self.engines = root, engines
        self.source = source or ModelSource()
        self.installer = installer
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="model-download")
        self.stop = threading.Event()
        self.closed = False
        self.job = {"status": "idle", "kind": "", "id": "", "label": "", "message": "",
                    "downloaded_bytes": 0, "total_bytes": None, "progress": None}
        self.catalog = {}
        self.cache = root / "models" / "catalog.json"
        if self.cache.is_file():
            try:
                self.catalog = normalize_catalog(json.loads(self.cache.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass

    def snapshot(self):
        pairs = installed(self.root)
        with self.lock:
            return {
                "speech": [{"id": name, "label": info["label"], "note": info["note"],
                            "installed": speech_folder(self.root, name) is not None} for name, info in SPEECH_MODELS.items()],
                "translations": [{k: v for k, v in item.items() if k != "url"} |
                                 {"installed": (item["source"], item["target"]) in pairs}
                                 for item in sorted(self.catalog.values(), key=lambda p: p["label"])],
                "installed_translations": [{"source": a, "target": b,
                    "label": f"{LANGUAGES.get(a, a)} → {LANGUAGES.get(b, b)}"} for a, b in pairs],
                "catalog_loaded": bool(self.catalog), "job": deepcopy(self.job),
                **self.engines.capabilities(),
            }

    def update(self, **fields):
        with self.lock:
            self.job.update(fields)

    def start(self, kind, model_id=""):
        with self.lock:
            if self.closed or self.job["status"] in ACTIVE_DOWNLOAD:
                raise Problem("A model operation is already running. Let it finish or cancel it first.", 409)
            if kind == "speech":
                if model_id not in SPEECH_MODELS:
                    raise Problem("Choose a listed speech model.")
                if not self.engines.capabilities()["speech_installed"]:
                    raise Problem("Install requirements.txt, then restart SNote before downloading speech models.", 503)
                if speech_folder(self.root, model_id):
                    raise Problem("That speech model is already installed.", 409)
                label = SPEECH_MODELS[model_id]["label"]
            elif kind == "translation":
                if model_id not in self.catalog:
                    raise Problem("Load the language list, then choose a listed pair.")
                if not self.engines.capabilities()["translation_installed"]:
                    raise Problem("Install requirements.txt, then restart SNote before downloading translation models.", 503)
                item = self.catalog[model_id]
                if (item["source"], item["target"]) in installed(self.root):
                    raise Problem("That translation pair is already installed.", 409)
                label = item["label"]
            elif kind == "catalog":
                label = "Available translation languages"
            else:
                raise Problem("Choose speech or translation models.")
            self.stop = threading.Event()
            self.job = {"status": "fetching", "kind": kind, "id": model_id, "label": label,
                        "message": "Connecting to the model source…", "downloaded_bytes": 0,
                        "total_bytes": None, "progress": None}
            self.executor.submit(self.run, kind, model_id, self.stop)
            return self.snapshot()

    def cancel(self):
        with self.lock:
            if self.job["status"] == "installing":
                raise Problem("The download has finished. Please let the final installation step complete.", 409)
            if self.job["status"] in ACTIVE_DOWNLOAD:
                self.stop.set()
                self.job["message"] = "Cancelling the download…"
            return self.snapshot()

    def activate(self, stop):
        with self.lock:
            checkpoint(stop)
            self.job.update(status="installing", progress=100, message="Checking and installing the model…")

    def transfer(self, url, destination, stop, *, offset=0, total=None, expected_size=None, checksum=None):
        done = 0
        digest = hashlib.sha256()
        with self.source.open(url) as response:
            content_length = response.headers.get("Content-Length")
            response_size = int(content_length) if content_length else None
            expected_size = expected_size if expected_size is not None else response_size
            if total is None and expected_size is not None:
                total = offset + expected_size
            if expected_size is not None and expected_size > shutil.disk_usage(destination.parent).free:
                raise ValueError("There is not enough free disk space for this model.")
            self.update(status="downloading", total_bytes=total)
            with destination.open("wb") as output:
                while True:
                    checkpoint(stop)
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > 10 * 1024 ** 3:
                        raise ValueError("The model file is unexpectedly large.")
                    output.write(chunk)
                    digest.update(chunk)
                    progress = min(99, int(100 * (offset + done) / total)) if total else None
                    self.update(downloaded_bytes=offset + done, total_bytes=total, progress=progress)
        checkpoint(stop)
        if done == 0 or (expected_size is not None and done != expected_size):
            raise ValueError("The model download was incomplete. Please try again.")
        if checksum and digest.hexdigest() != checksum:
            raise ValueError("The model checksum did not match. Please download it again.")
        return done

    def download_speech(self, name, stop):
        repo = SPEECH_MODELS[name]["repo"]
        info = self.source.json(f"https://huggingface.co/api/models/{repo}?blobs=true")
        checkpoint(stop)
        revision = info.get("sha", "")
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("The speech model revision could not be verified.")
        files = [item for item in info.get("siblings", []) if item.get("rfilename") in SPEECH_FILES]
        if not REQUIRED_SPEECH_FILES.issubset({f["rfilename"] for f in files}):
            raise ValueError("The speech model is missing required files.")
        for item in files:
            if not isinstance(item.get("size"), int) or item["size"] <= 0:
                raise ValueError("The model source did not provide valid file sizes.")
        total = sum(item["size"] for item in files)
        directory = self.root / "models" / "speech"
        directory.mkdir(parents=True, exist_ok=True)
        if total > shutil.disk_usage(directory).free:
            raise ValueError("There is not enough free disk space for this model.")
        with tempfile.TemporaryDirectory(prefix=".download-", dir=directory) as temporary:
            folder = Path(temporary) / "model"
            folder.mkdir()
            offset = 0
            for item in files:
                filename = item["rfilename"]
                self.update(message=f"Downloading {SPEECH_MODELS[name]['label']} · {filename}")
                checksum = item.get("lfs", {}).get("sha256")
                offset += self.transfer(f"https://huggingface.co/{repo}/resolve/{revision}/{quote(filename)}",
                    folder / filename, stop, offset=offset, total=total, expected_size=item["size"], checksum=checksum)
            self.activate(stop)
            # Parse the small configuration files before publishing the directory.
            json.loads((folder / "config.json").read_text(encoding="utf-8"))
            json.loads((folder / "tokenizer.json").read_text(encoding="utf-8"))
            (folder / "snote-ready.json").write_text(json.dumps({"repo": repo, "revision": revision}), encoding="utf-8")
            destination = directory / name
            if destination.exists():
                raise ValueError("A model directory already exists. The existing files were left unchanged.")
            folder.rename(destination)

    def download_translation(self, model_id, stop):
        item = self.catalog[model_id]
        directory = self.root / "models"
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".translation-download-", dir=directory) as temporary:
            archive = Path(temporary) / "translation.argosmodel"
            self.update(message=f"Downloading {item['label']}…")
            self.transfer(item["url"], archive, stop)
            self.activate(stop)
            self.installer(self.root, archive, expected_pair=(item["source"], item["target"]))

    def run(self, kind, model_id, stop):
        try:
            checkpoint(stop)
            if kind == "catalog":
                raw = self.source.json(INDEX_URL)
                catalog = normalize_catalog(raw)
                self.activate(stop)
                self.cache.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.cache.with_suffix(".tmp")
                temporary.write_text(json.dumps(raw), encoding="utf-8")
                temporary.replace(self.cache)
                with self.lock:
                    self.catalog = catalog
            elif kind == "speech":
                self.download_speech(model_id, stop)
            else:
                self.download_translation(model_id, stop)
            self.update(status="complete", progress=100,
                        message="Language list updated." if kind == "catalog" else "Installed. Ready to use; no restart needed.")
        except Cancelled:
            self.update(status="cancelled", message="Download cancelled. Incomplete files were removed.")
        except Exception as exc:
            if stop.is_set():
                self.update(status="cancelled", message="Download cancelled. Incomplete files were removed.")
            else:
                self.update(status="error", message=f"Could not finish: {exc}")

    def close(self):
        with self.lock:
            self.closed = True
            self.stop.set()
        self.executor.shutdown(wait=True)
