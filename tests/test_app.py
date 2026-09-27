"""Real HTTP, disk and job-queue tests. Model doubles do not test ML quality."""

from copy import deepcopy
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.parse import urlencode
import wave

from snote.domain import BUSY, MAX_UPLOAD, Problem, apply_edits, sample_project
from snote.engines import LocalEngines, checkpoint
from snote.exports import export, timestamp
from snote.server import make_server
from snote.store import Store


def audio_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 32000)
    return buffer.getvalue()


class TestEngines:
    def __init__(self):
        self.fail_translation = False
        self.silent = False
        self.gate = None
        self.started = threading.Event()

    def capabilities(self):
        return {"speech_installed": True, "translation_installed": True}

    def prepare(self, source, destination, stop):
        self.started.set()
        if self.gate:
            while not self.gate.wait(0.01):
                checkpoint(stop)
        checkpoint(stop)
        with wave.open(str(source)) as stream:
            duration = stream.getnframes() / stream.getframerate()
        destination.write_bytes(source.read_bytes())
        return duration, [0.2, 0.5, 0.1]

    def transcribe(self, path, name, language, stop, on_language, on_segment):
        on_language("en")
        if not self.silent:
            on_segment(0, 0.8, "Hello, world.")
            on_segment(1, 2, "A second line.")

    def translator(self, source, target):
        if self.fail_translation:
            raise RuntimeError("Missing translation pack for this test.")
        return lambda text: "Traducción: " + text


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engines = TestEngines()
        self.server = make_server(self.root, 0, self.engines)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def tearDown(self):
        if self.engines.gate:
            self.engines.gate.set()
        self.server.shutdown()
        self.server.server_close()
        self.server.app.close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, payload=None, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        request_headers = {"X-SNote-Request": "1"}
        if payload is not None:
            body = json.dumps(payload).encode()
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        data = response.read()
        result_headers = dict(response.getheaders())
        status = response.status
        connection.close()
        if result_headers.get("Content-Type", "").startswith("application/json"):
            data = json.loads(data)
        return status, data, result_headers

    def sample(self):
        status, project, _ = self.request("POST", "/api/sample")
        self.assertEqual(status, 201)
        return project

    def upload(self, **options):
        query = urlencode({"filename": "hello.wav", **options})
        status, result, _ = self.request("POST", "/api/projects?" + query, body=audio_bytes())
        self.assertEqual(status, 202, result)
        return result

    def wait_ready(self, project_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status, project, _ = self.request("GET", f"/api/projects/{project_id}")
            self.assertEqual(status, 200)
            if project["status"] not in BUSY:
                # Let the job release its queue entry before another submission.
                with self.server.app.jobs.lock:
                    finished = project_id not in self.server.app.jobs.stops
                if finished:
                    return project
            time.sleep(0.01)
        self.fail("The job did not finish in time")

    def test_ui_and_config_are_served_without_models(self):
        status, content, headers = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Your recordings.", content)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        for path in ("/app.js", "/styles.css", "/favicon.svg", "/api/config"):
            self.assertEqual(self.request("GET", path)[0], 200)
        self.assertEqual(self.request("GET", "/../server.py")[0], 404)

    def test_upload_process_edit_translate_export_delete(self):
        project = self.wait_ready(self.upload(target="es")["id"])
        self.assertEqual(project["status"], "ready")
        self.assertEqual(project["detected_language"], "en")
        self.assertTrue(project["has_audio"])
        self.assertEqual(len(project["segments"]), 2)
        self.assertIn("Traducción", project["segments"][0]["translation"])
        path = f"/api/projects/{project['id']}"
        project["segments"][0]["text"] = "Corrected line."
        status, saved, _ = self.request("PATCH", path, payload={"revision": project["revision"], "segments": project["segments"]})
        self.assertEqual(status, 200)
        self.assertEqual(saved["segments"][0]["translation"], "")
        self.assertEqual(self.request("GET", path + "/export?format=srt&content=both")[0], 400)
        status, _, _ = self.request("POST", path + "/translate", payload={"revision": saved["revision"], "target": "es"})
        self.assertEqual(status, 202)
        translated = self.wait_ready(project["id"])
        self.assertEqual(translated["segments"][0]["translation"], "Traducción: Corrected line.")
        status, output, headers = self.request("GET", path + "/export?format=srt&content=both")
        self.assertEqual(status, 200)
        self.assertIn(b"00:00:00,000 --> 00:00:00,800", output)
        self.assertIn("Traducción: Corrected line.", output.decode())
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(self.request("DELETE", path)[0], 200)
        self.assertFalse((self.root / "recordings" / project["id"]).exists())
        self.assertEqual(self.request("GET", path)[0], 404)

    def test_audio_supports_seeking_and_suffix_ranges(self):
        project = self.wait_ready(self.upload()["id"])
        path = f"/api/projects/{project['id']}/audio"
        status, data, headers = self.request("GET", path, headers={"Range": "bytes=0-43"})
        self.assertEqual(status, 206)
        self.assertEqual(len(data), 44)
        self.assertEqual(data[:4], b"RIFF")
        self.assertEqual(headers["Content-Range"], "bytes 0-43/64044")
        self.assertEqual(len(self.request("GET", path, headers={"Range": "bytes=-10"})[1]), 10)
        self.assertEqual(len(self.request("GET", path, headers={"Range": "bytes=64034-"})[1]), 10)
        for invalid in ("bytes=999999-", "bytes=4-2", "bytes=0-1,4-5", "bytes=-0"):
            self.assertEqual(self.request("GET", path, headers={"Range": invalid})[0], 416)

    def test_stale_revision_does_not_overwrite_a_saved_edit(self):
        project = self.sample()
        path = f"/api/projects/{project['id']}"
        first = self.request("PATCH", path, payload={"revision": 0, "title": "First edit"})
        self.assertEqual(first[0], 200)
        self.assertEqual(self.request("PATCH", path, payload={"revision": 0, "title": "Stale edit"})[0], 409)
        self.assertEqual(self.request("GET", path)[1]["title"], "First edit")

    def test_invalid_edits_are_atomic(self):
        project = self.sample()
        path = f"/api/projects/{project['id']}"
        for value in (-1, float("nan"), float("inf"), True):
            with self.subTest(value=value):
                edited = deepcopy(project["segments"])
                edited[0]["start"] = value
                status, _, _ = self.request("PATCH", path, payload={"revision": 0, "title": "Do not save", "segments": edited})
                self.assertEqual(status, 400)
                self.assertEqual(self.request("GET", path)[1]["title"], project["title"])

    def test_translation_failure_keeps_original_and_allows_retry(self):
        self.engines.fail_translation = True
        with self.assertLogs("snote.jobs", level="ERROR"):
            project = self.wait_ready(self.upload(target="es")["id"])
        self.assertEqual(project["status"], "ready")
        self.assertIn("Missing translation pack", project["warning"])
        self.assertEqual(project["segments"][0]["text"], "Hello, world.")
        self.assertEqual(project["segments"][0]["translation"], "")
        self.assertEqual(self.request("GET", f"/api/projects/{project['id']}/export?format=txt")[0], 200)

    def test_stop_job_blocks_edit_delete_and_duplicate_jobs_until_finished(self):
        self.engines.gate = threading.Event()
        project = self.upload()
        self.assertTrue(self.engines.started.wait(2))
        path = f"/api/projects/{project['id']}"
        current = self.request("GET", path)[1]
        self.assertEqual(self.request("DELETE", path)[0], 409)
        self.assertEqual(self.request("PATCH", path, payload={"revision": current["revision"], "title": "busy"})[0], 409)
        self.assertEqual(self.request("POST", path + "/retry", payload={"revision": current["revision"]})[0], 409)
        self.assertEqual(self.request("POST", path + "/cancel")[0], 200)
        self.assertEqual(self.wait_ready(project["id"])["status"], "cancelled")

    def test_queued_cancellation_does_not_start_second_recording(self):
        self.engines.gate = threading.Event()
        first = self.upload()
        self.assertTrue(self.engines.started.wait(2))
        second = self.upload()
        self.request("POST", f"/api/projects/{second['id']}/cancel")
        self.engines.gate.set()
        self.wait_ready(first["id"])
        cancelled = self.wait_ready(second["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertFalse(cancelled["has_audio"])

    def test_silent_audio_has_clear_result_and_cannot_export_empty_subtitles(self):
        self.engines.silent = True
        project = self.wait_ready(self.upload()["id"])
        self.assertIn("No speech", project["warning"])
        self.assertEqual(self.request("GET", f"/api/projects/{project['id']}/export?format=vtt")[0], 400)

    def test_invalid_audio_reports_an_error_without_losing_project(self):
        with self.assertLogs("snote.jobs", level="ERROR"):
            status, project, _ = self.request("POST", "/api/projects?filename=broken.wav", body=b"not audio")
            self.assertEqual(status, 202)
            self.assertEqual(self.wait_ready(project["id"])["status"], "error")

    def test_upload_validation_and_size_limit(self):
        self.assertEqual(self.request("POST", "/api/projects?filename=script.exe", body=b"x")[0], 400)
        self.assertEqual(self.request("POST", "/api/projects?filename=clip.wav&model=invalid", body=b"x")[0], 400)
        self.assertEqual(self.request("POST", "/api/projects?filename=clip.wav", body=b"")[0], 400)
        self.assertEqual(self.request("POST", "/api/projects?filename=clip.wav", body=b"x",
                         headers={"Content-Length": str(MAX_UPLOAD + 1)})[0], 413)
        self.assertEqual(self.request("GET", "/api/projects")[1], [])

    def test_local_origin_and_request_header_guards(self):
        for headers in ({"Host": "evil.example"}, {"Origin": "https://evil.example"},
                        {"Sec-Fetch-Site": "cross-site"}, {"X-SNote-Request": "0"}):
            self.assertEqual(self.request("POST", "/api/sample", headers=headers)[0], 403)
        self.assertEqual(self.request("GET", "/api/projects")[1], [])

    def test_json_export_preserves_unicode_and_review_state(self):
        project = self.sample()
        status, exported, _ = self.request("GET", f"/api/projects/{project['id']}/export?format=json")
        self.assertEqual(status, 200)
        self.assertTrue(exported["segments"][0]["reviewed"])
        self.assertIn("¿Adónde", exported["segments"][0]["translation"])

    def test_sample_cannot_be_transcribed_or_translated_as_real_audio(self):
        project = self.sample()
        for action, payload in (("retry", {}), ("translate", {"target": "es"})):
            self.assertEqual(self.request("POST", f"/api/projects/{project['id']}/{action}",
                             payload={"revision": project["revision"], **payload})[0], 400)
        self.assertEqual(self.request("GET", f"/api/projects/{project['id']}/audio")[0], 404)


class DomainTests(unittest.TestCase):
    def test_timestamp_rounding_carries_to_next_minute_and_hour(self):
        self.assertEqual(timestamp(59.9999), "00:01:00,000")
        self.assertEqual(timestamp(3599.9999, "."), "01:00:00.000")

    def test_vtt_escapes_markup_and_uses_dot_milliseconds(self):
        project = sample_project()
        project["segments"][0]["text"] = "<hello> & welcome\n\nback"
        content, mime = export(project, "vtt")
        self.assertTrue(content.startswith("WEBVTT\n\n1\n00:00:00.000 --> 00:00:05.400"))
        self.assertIn("&lt;hello&gt; &amp; welcome back", content)
        self.assertEqual(mime, "text/vtt")

    def test_source_correction_invalidates_translation_and_review(self):
        project = sample_project()
        rows = deepcopy(project["segments"])
        rows[0]["text"] = "New source text"
        apply_edits(project, {"segments": rows})
        self.assertEqual(project["segments"][0]["translation"], "")
        self.assertFalse(project["segments"][0]["reviewed"])

    def test_manual_translation_changed_with_source_is_preserved(self):
        project = sample_project()
        rows = deepcopy(project["segments"])
        rows[0].update(text="Hi", translation="Hola")
        apply_edits(project, {"segments": rows})
        self.assertEqual(project["segments"][0]["translation"], "Hola")

    def test_overlap_and_missing_rows_are_rejected(self):
        project = sample_project()
        rows = deepcopy(project["segments"])
        rows[1]["start"] = 1
        with self.assertRaises(Problem):
            apply_edits(project, {"segments": rows})
        with self.assertRaises(Problem):
            apply_edits(project, {"segments": rows[:-1]})

    def test_same_language_translation_needs_no_external_package(self):
        self.assertEqual(LocalEngines(Path("unused")).translator("en", "en")("Hello"), "Hello")

    def test_recovery_preserves_completed_work(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Store(Path(directory))
            project = sample_project()
            project["status"] = "translating"
            first.create(project)
            reopened = Store(Path(directory))
            reopened.recover()
            recovered = reopened.get(project["id"])
            self.assertEqual(recovered["status"], "interrupted")
            self.assertEqual(recovered["segments"], project["segments"])


if __name__ == "__main__":
    unittest.main()
