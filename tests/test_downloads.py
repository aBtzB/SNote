"""Exercise downloads using deterministic streams, without external network calls."""

from functools import partial
import hashlib
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.parse import urlsplit
import zipfile

from snote.domain import Problem
from snote.models import ModelManager, INDEX_URL, normalize_catalog, speech_folder
from snote.server import make_server
from snote.translation import install_pack, installed


class Capabilities:
    def capabilities(self):
        return {"speech_installed": True, "translation_installed": True}


class Response(io.BytesIO):
    def __init__(self, data, extra_length=0, after_read=None):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) + extra_length)}
        self.after_read = after_read

    def read(self, size=-1):
        data = super().read(size)
        if self.after_read:
            self.after_read()
        return data


class Source:
    def __init__(self):
        self.calls = []
        self.files = {"config.json": b"{}", "tokenizer.json": b"{}", "model.bin": b"model bytes"}
        self.catalog = [{"from_code": "en", "to_code": "es", "package_version": "1.0",
                         "links": ["https://argos-net.com/v1/test.argosmodel"]}]
        self.bad_hash = False
        self.extra_length = 0
        self.after_read = None
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("pair/metadata.json", json.dumps({"from_code": "en", "to_code": "es"}))
            archive.writestr("pair/sentencepiece.model", b"test tokenizer")
            archive.writestr("pair/model/model.bin", b"test model")
        self.pack = output.getvalue()

    def json(self, url):
        self.calls.append(url)
        if url == INDEX_URL:
            return self.catalog
        return {"sha": "a" * 40, "siblings": [{"rfilename": name, "size": len(data),
            "lfs": {"sha256": "0" * 64 if self.bad_hash else hashlib.sha256(data).hexdigest()}}
            for name, data in self.files.items()]}

    def open(self, url):
        self.calls.append(url)
        filename = Path(urlsplit(url).path).name
        data = self.pack if filename.endswith(".argosmodel") else self.files[filename]
        return Response(data, self.extra_length, self.after_read)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = Source()
        self.manager = ModelManager(self.root, Capabilities(), source=self.source,
            installer=partial(install_pack, validator=lambda _: None))

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def finish(self):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = self.manager.snapshot()["job"]
            if job["status"] not in {"fetching", "downloading", "installing"}:
                return job
            time.sleep(0.005)
        self.fail("Model operation did not finish")

    def load_catalog(self):
        self.manager.start("catalog")
        self.assertEqual(self.finish()["status"], "complete")

    def test_opening_manager_makes_no_network_requests(self):
        self.assertEqual(len(self.manager.snapshot()["speech"]), 6)
        self.assertEqual(self.source.calls, [])

    def test_speech_download_checks_bytes_and_installs_atomically(self):
        self.manager.start("speech", "tiny")
        job = self.finish()
        self.assertEqual(job["status"], "complete", job)
        self.assertEqual(job["downloaded_bytes"], sum(map(len, self.source.files.values())))
        self.assertEqual(job["progress"], 100)
        self.assertIsNotNone(speech_folder(self.root, "tiny"))
        with self.assertRaises(Problem):
            self.manager.start("speech", "tiny")
        self.assertFalse(any((self.root / "models" / "speech").glob(".download-*")))

    def test_failed_checksum_is_not_marked_installed_and_retry_works(self):
        self.source.bad_hash = True
        self.manager.start("speech", "base")
        self.assertEqual(self.finish()["status"], "error")
        self.assertIsNone(speech_folder(self.root, "base"))
        self.source.bad_hash = False
        self.manager.start("speech", "base")
        self.assertEqual(self.finish()["status"], "complete")

    def test_catalog_is_cached_and_failed_refresh_keeps_previous_choices(self):
        self.load_catalog()
        self.assertTrue(self.manager.snapshot()["catalog_loaded"])
        self.source.catalog = {"unexpected": True}
        self.manager.start("catalog")
        self.assertEqual(self.finish()["status"], "error")
        self.assertEqual(self.manager.snapshot()["translations"][0]["id"], "en-es")
        reopened = ModelManager(self.root, Capabilities(), source=self.source)
        try:
            self.assertTrue(reopened.snapshot()["catalog_loaded"])
        finally:
            reopened.close()

    def test_translation_download_imports_the_selected_pair_and_cleans_archive(self):
        self.load_catalog()
        self.manager.start("translation", "en-es")
        self.assertEqual(self.finish()["status"], "complete")
        self.assertIn(("en", "es"), installed(self.root))
        self.assertTrue(self.manager.snapshot()["translations"][0]["installed"])
        self.assertFalse(any((self.root / "models").glob(".translation-download-*")))

    def test_incomplete_download_is_rejected(self):
        self.load_catalog()
        self.source.extra_length = 10
        self.manager.start("translation", "en-es")
        job = self.finish()
        self.assertEqual(job["status"], "error")
        self.assertIn("incomplete", job["message"])
        self.assertEqual(installed(self.root), {})

    def test_cancellation_cleans_partial_files(self):
        self.source.after_read = lambda: self.manager.stop.set()
        self.manager.start("speech", "small")
        self.assertEqual(self.finish()["status"], "cancelled")
        self.assertIsNone(speech_folder(self.root, "small"))
        self.assertFalse(any((self.root / "models" / "speech").glob(".download-*")))

    def test_unknown_model_or_client_url_is_rejected(self):
        for kind, model_id in (("speech", "../../other"), ("translation", "https://evil.example/model"), ("other", "tiny")):
            with self.assertRaises(Problem):
                self.manager.start(kind, model_id)
        self.assertEqual(self.source.calls, [])

    def test_model_routes_start_downloads_and_enforce_request_guards(self):
        server = make_server(self.root, 0, Capabilities())
        server.app.models.close()
        server.app.models = self.manager
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()

        def request(method, path, payload=None, trusted=True):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            headers = {"Content-Type": "application/json"}
            if trusted:
                headers["X-SNote-Request"] = "1"
            try:
                connection.request(method, path, body=json.dumps(payload) if payload is not None else None, headers=headers)
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        try:
            self.assertEqual(request("GET", "/api/models")[0], 200)
            self.assertEqual(self.source.calls, [])
            self.assertEqual(request("POST", "/api/models/catalog", trusted=False)[0], 403)
            self.assertEqual(request("POST", "/api/models/download", {"kind": "other", "id": "tiny"})[0], 400)
            self.assertEqual(self.source.calls, [])
            self.assertEqual(request("POST", "/api/models/download", {"kind": "speech", "id": "tiny"})[0], 202)
            self.assertEqual(self.finish()["status"], "complete")
            self.assertTrue(request("GET", "/api/models")[1]["speech"][0]["installed"])
            self.assertEqual(request("POST", "/api/models/download", {"kind": "speech", "id": "tiny"})[0], 409)
            self.assertEqual(request("POST", "/api/models/catalog")[0], 202)
            self.assertEqual(self.finish()["status"], "complete")
            self.assertEqual(request("GET", "/api/models")[1]["translations"][0]["id"], "en-es")
            self.assertEqual(request("POST", "/api/models/cancel")[0], 200)
        finally:
            server.shutdown()
            server.server_close()
            server.app.close()
            thread.join(timeout=2)

    def test_second_download_cannot_replace_an_active_job(self):
        entered, release = threading.Event(), threading.Event()
        def pause_read():
            entered.set()
            release.wait(3)
        self.source.after_read = pause_read
        try:
            self.manager.start("speech", "tiny")
            self.assertTrue(entered.wait(2))
            with self.assertRaises(Problem) as failure:
                self.manager.start("speech", "base")
            self.assertEqual(failure.exception.status, 409)
            self.assertEqual(self.manager.snapshot()["job"]["id"], "tiny")
            self.manager.cancel()
        finally:
            release.set()
        self.assertEqual(self.finish()["status"], "cancelled")
        self.assertIsNone(speech_folder(self.root, "tiny"))

    def test_download_requires_dependencies_but_catalog_does_not(self):
        self.manager.engines.capabilities = lambda: {"speech_installed": False, "translation_installed": False}
        self.load_catalog()
        for kind, model_id in (("speech", "tiny"), ("translation", "en-es")):
            with self.assertRaises(Problem) as failure:
                self.manager.start(kind, model_id)
            self.assertEqual(failure.exception.status, 503)

    def test_pair_mismatch_does_not_install_unrequested_model(self):
        self.source.catalog[0]["to_code"] = "fr"
        self.load_catalog()
        self.manager.start("translation", "en-fr")
        self.assertEqual(self.finish()["status"], "error")
        self.assertEqual(installed(self.root), {})

    def test_catalog_ignores_unapproved_download_hosts(self):
        with self.assertRaises(ValueError):
            normalize_catalog([{"from_code": "en", "to_code": "es", "package_version": "1.0",
                                "links": ["https://evil.example/pack.argosmodel"]}])


if __name__ == "__main__":
    unittest.main()
