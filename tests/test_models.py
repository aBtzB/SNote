"""Optional real-model test: see docs/validation.md for the environment variables."""

import os
from pathlib import Path
import tempfile
import threading
import unittest

from snote.engines import LocalEngines


@unittest.skipUnless(os.environ.get("SNOTE_TEST_AUDIO"), "Real-audio test needs SNOTE_TEST_AUDIO and installed models")
class RealModelTests(unittest.TestCase):
    def test_recording_produces_expected_words_and_a_translation(self):
        root = Path(os.environ.get("SNOTE_DATA_DIR", "~/.snote")).expanduser()
        engines = LocalEngines(root)
        stop = threading.Event()
        lines, language = [], []
        with tempfile.TemporaryDirectory() as directory:
            playback = Path(directory) / "playback.wav"
            duration, peaks = engines.prepare(Path(os.environ["SNOTE_TEST_AUDIO"]), playback, stop)
            self.assertGreater(duration, 0)
            self.assertEqual(len(peaks), 96)
            engines.transcribe(playback, os.environ.get("SNOTE_TEST_MODEL", "tiny"),
                               os.environ.get("SNOTE_TEST_SOURCE", "en"), stop,
                               language.append, lambda start, end, text: lines.append(text))
        self.assertTrue(lines, "No speech was detected in the supplied recording")
        text = " ".join(lines)
        expected = os.environ.get("SNOTE_EXPECT_TEXT", "")
        if expected:
            self.assertIn(expected.casefold(), text.casefold())
        translator = engines.translator(language[0], os.environ.get("SNOTE_TEST_TARGET", "es"))
        translated = translator(text)
        self.assertTrue(translated.strip())
        print(f"\nRecognized: {text}\nTranslated: {translated}\n")


if __name__ == "__main__":
    unittest.main()
