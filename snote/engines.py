"""Audio decoding and speech recognition; translation uses the shared CT2 runtime."""

import importlib.util
import wave
from array import array
from .domain import MAX_DURATION


class Cancelled(Exception):
    pass


def checkpoint(stop):
    if stop.is_set():
        raise Cancelled()


class LocalEngines:
    def __init__(self, root):
        self.root = root
        self.model = None
        self.model_name = None
        self.translation_route = None
        self.translation_models = []

    def capabilities(self):
        return {
            "speech_installed": importlib.util.find_spec("faster_whisper") is not None,
            "translation_installed": all(importlib.util.find_spec(name) is not None
                                         for name in ("sentencepiece", "ctranslate2")),
        }

    def prepare(self, source, destination, stop):
        try:
            import av
        except ImportError as exc:
            raise RuntimeError("Install the speech dependencies with: python -m pip install -r requirements.txt") from exc
        total = 0
        amplitudes = []
        try:
            with av.open(str(source)) as container, wave.open(str(destination), "wb") as output:
                if not container.streams.audio:
                    raise ValueError("This file has no audio track.")
                output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)

                def write(frame):
                    nonlocal total
                    checkpoint(stop)
                    total += frame.samples
                    if total > 16000 * MAX_DURATION:
                        raise ValueError("This notebook accepts recordings up to 30 minutes long.")
                    samples = frame.to_ndarray().tobytes()
                    output.writeframesraw(samples)
                    values = array("h", samples)
                    amplitudes.append(max((abs(v) for v in values), default=0) / 32768)

                for frame in container.decode(audio=0):
                    checkpoint(stop)
                    for converted in resampler.resample(frame):
                        write(converted)
                for converted in resampler.resample(None):
                    write(converted)
            if total == 0:
                raise ValueError("The recording is empty.")
        except (Cancelled, ValueError):
            destination.unlink(missing_ok=True)
            raise
        except Exception as exc:
            destination.unlink(missing_ok=True)
            raise ValueError("Could not decode the audio. Try exporting it as WAV or MP3.") from exc
        # Each bar summarizes a group of decoded frames; this is an amplitude overview.
        peaks = []
        for i in range(96):
            a, b = len(amplitudes) * i // 96, len(amplitudes) * (i + 1) // 96
            peaks.append(round(max(amplitudes[a:max(a + 1, b)], default=0), 3))
        return total / 16000, peaks

    def load_speech(self, name, download=False):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("Speech dependencies are missing. Run: python -m pip install -r requirements.txt") from exc
        if self.model_name != name:
            self.model = None  # Do not retain several large models in memory.
            self.model_name = None
            try:
                from .models import speech_folder
                local = speech_folder(self.root, name)
                self.model = WhisperModel(
                    str(local) if local else name, device="cpu", compute_type="int8",
                    download_root=str(self.root / "models" / "whisper"),
                    local_files_only=not download,
                )
            except Exception as exc:
                raise RuntimeError(f"Could not load the {name} speech model. Open Models and install it, or run: python -m snote install --speech {name}") from exc
            self.model_name = name
        return self.model

    def transcribe(self, path, name, language, stop, on_language, on_segment):
        checkpoint(stop)
        model = self.load_speech(name)
        checkpoint(stop)
        segments, info = model.transcribe(
            str(path), language=None if language == "auto" else language,
            task="transcribe", beam_size=5, vad_filter=True,
            condition_on_previous_text=False,
        )
        on_language(info.language)
        for segment in segments:
            checkpoint(stop)
            if segment.text.strip():
                on_segment(segment.start, segment.end, segment.text.strip())
        checkpoint(stop)

    def translator(self, source, target):
        if source == target:
            return lambda text: text
        from .translation import PairTranslator, installed, route
        paths = tuple(route(installed(self.root), source, target))
        if paths != self.translation_route:
            self.translation_models = []
            self.translation_route = None
            self.translation_models = [PairTranslator(path) for path in paths]
            self.translation_route = paths

        def translate(text):
            for model in self.translation_models:
                text = model(text)
            return text

        return translate
