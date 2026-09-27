from concurrent.futures import ThreadPoolExecutor
import logging
import threading
from .domain import BUSY, Problem
from .engines import Cancelled, checkpoint

log = logging.getLogger(__name__)


class Jobs:
    def __init__(self, store, engines):
        self.store, self.engines = store, engines
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="snote")
        self.lock = threading.Lock()
        self.stops = {}
        self.closed = False

    def submit(self, project_id, action="transcribe", target=None, expected_revision=None):
        with self.lock:
            if self.closed or len(self.stops) >= 8:
                raise Problem("The queue is full. Let a recording finish first.", 429)
            if project_id in self.stops:
                raise Problem("This recording is already processing.", 409)

            def queue(project):
                if project["status"] in BUSY:
                    raise Problem("This recording is already processing.", 409)
                if project["is_demo"]:
                    raise Problem("The sample has no audio. Add a recording to run the models.")
                if action == "translate" and not project["segments"]:
                    raise Problem("Transcribe the recording before translating it.")
                if action == "transcribe":
                    project.update(segments=[], detected_language="", duration=0,
                                   peaks=[], has_audio=False)
                else:
                    project["target"] = target
                    for segment in project["segments"]:
                        segment.update(translation="", reviewed=False)
                project.update(status="queued", progress=0, warning="", message="Waiting in the queue…")

            project = self.store.mutate(project_id, queue, expected_revision)
            stop = threading.Event()
            self.stops[project_id] = stop
            self.pool.submit(self.run, project_id, action, stop)
            return project

    def cancel(self, project_id):
        with self.lock:
            stop = self.stops.get(project_id)
            if stop is None:
                return self.store.get(project_id)
            stop.set()
            return self.store.update(project_id, message="Stopping after the current model operation…")

    def run(self, project_id, action, stop):
        try:
            checkpoint(stop)
            project = self.store.get(project_id)
            folder = self.store.root / "recordings" / project_id
            if action == "transcribe":
                self.store.update(project_id, status="preparing", message="Preparing the audio…")
                duration, peaks = self.engines.prepare(folder / "source", folder / "playback.wav", stop)
                checkpoint(stop)
                self.store.update(project_id, duration=duration, peaks=peaks, has_audio=True,
                                  status="transcribing", message="Listening and writing…", progress=2)

                def language(code):
                    self.store.update(project_id, detected_language=code)

                def segment(start, end, text):
                    checkpoint(stop)

                    def append(current):
                        previous_end = current["segments"][-1]["end"] if current["segments"] else 0
                        a, b = max(previous_end, round(start, 3)), min(duration, round(end, 3))
                        if b <= a:
                            return
                        current["segments"].append({"id": len(current["segments"]), "start": a,
                            "end": b, "text": text, "translation": "", "reviewed": False})
                        current["progress"] = min(78, round(2 + 76 * b / max(duration, 0.1)))

                    self.store.mutate(project_id, append)

                self.engines.transcribe(folder / "playback.wav", project["model"], project["source"],
                                        stop, language, segment)
            checkpoint(stop)
            project = self.store.get(project_id)
            warning = ""
            if not project["segments"]:
                warning = "No speech was detected. Try a clearer recording or choose the spoken language explicitly."
            elif project["target"]:
                self.store.update(project_id, status="translating", progress=80,
                                  message="Translating the transcript…")
                try:
                    translator = self.engines.translator(project["detected_language"], project["target"])
                    for index, segment in enumerate(project["segments"]):
                        checkpoint(stop)
                        result = translator(segment["text"])
                        checkpoint(stop)
                        if not isinstance(result, str) or not result.strip():
                            raise RuntimeError("The translator returned an empty line. The original transcript is saved.")
                        segment["translation"] = result.strip()
                        self.store.update(project_id, progress=80 + round(19 * (index + 1) / len(project["segments"])))
                    checkpoint(stop)
                    self.store.update(project_id, segments=project["segments"])
                except Cancelled:
                    raise
                except Exception as exc:
                    log.exception("Translation failed for %s", project_id)
                    warning = str(exc) or "Translation failed. The original transcript is saved."
            checkpoint(stop)
            self.store.update(project_id, status="ready", progress=100,
                              message="Ready to review", warning=warning)
        except Cancelled:
            self.store.update(project_id, status="cancelled", message="Stopped. Any completed transcript lines are saved.")
        except Exception as exc:
            log.exception("Processing failed for %s", project_id)
            self.store.update(project_id, status="error", message=str(exc) or "Processing failed. Check the terminal for details.")
        finally:
            with self.lock:
                self.stops.pop(project_id, None)

    def close(self):
        with self.lock:
            self.closed = True
            for stop in self.stops.values():
                stop.set()
        self.pool.shutdown(wait=True)
