from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from snote.__main__ import main
from snote.domain import Problem, apply_edits, sample_project
from snote.engines import LocalEngines
from snote.translation import PairTranslator, install_pack, installed, route


class PackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def archive(self, extras=None, tokenizer=True):
        path = self.root / "test.argosmodel"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("translate-en_es/metadata.json", json.dumps({"from_code": "en", "to_code": "es"}))
            archive.writestr("translate-en_es/model/model.bin", b"model fixture")
            archive.writestr("translate-en_es/model/config.json", "{}")
            archive.writestr("translate-en_es/LICENSE", "Test fixture only")
            archive.writestr("translate-en_es/stanza/unused.bin", b"not needed")
            if tokenizer:
                archive.writestr("translate-en_es/sentencepiece.model", b"tokenizer fixture")
            for name, value in extras or []:
                archive.writestr(name, value)
        return path

    def test_import_preserves_notices_and_skips_unneeded_nlp_resources(self):
        checked = []
        data = install_pack(self.root, self.archive(), validator=lambda folder: checked.append(folder.exists()))
        self.assertEqual(data["from_code"], "en")
        self.assertEqual(checked, [True])
        folder = installed(self.root)[("en", "es")]
        self.assertTrue((folder / "LICENSE").is_file())
        self.assertTrue((folder / "model" / "config.json").is_file())
        self.assertFalse((folder / "stanza").exists())

    def test_archive_paths_cannot_escape_the_install_directory(self):
        for path in ("../outside", "/absolute", "C:/drive", "folder\\outside"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                install_pack(self.root, self.archive([(path, b"bad")]), validator=lambda _: None)
        self.assertEqual(installed(self.root), {})

    def test_symlink_archive_entry_is_rejected(self):
        link = zipfile.ZipInfo("translate-en_es/model/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(ValueError):
            install_pack(self.root, self.archive([(link, b"/tmp/target")]), validator=lambda _: None)

    def test_unsupported_tokenizer_or_invalid_model_is_not_installed(self):
        with self.assertRaises(ValueError):
            install_pack(self.root, self.archive(tokenizer=False), validator=lambda _: None)
        def cannot_load(_):
            raise RuntimeError("Invalid model")
        with self.assertRaises(RuntimeError):
            install_pack(self.root, self.archive(), validator=cannot_load)
        self.assertEqual(installed(self.root), {})
        self.assertEqual(list((self.root / "models" / "translation").iterdir()), [])

    def test_existing_pair_is_not_overwritten(self):
        path = self.archive()
        install_pack(self.root, path, validator=lambda _: None)
        with self.assertRaisesRegex(ValueError, "already installed"):
            install_pack(self.root, path, validator=lambda _: None)
        self.assertEqual(len(installed(self.root)), 1)

    def test_route_prefers_direct_and_handles_cycles_and_missing_targets(self):
        pairs = {("ar", "en"): "a", ("en", "ar"): "b", ("en", "es"): "c"}
        self.assertEqual(route(pairs, "ar", "es"), ["a", "c"])
        pairs[("ar", "es")] = "d"
        self.assertEqual(route(pairs, "ar", "es"), ["d"])
        with self.assertRaisesRegex(RuntimeError, "No installed translation route"):
            route(pairs, "ar", "fr")

    def test_translation_adapter_keeps_text_untruncated_and_removes_target_prefix(self):
        install_pack(self.root, self.archive(), validator=lambda _: None)
        folder = installed(self.root)[("en", "es")]
        (folder / "metadata.json").write_text(json.dumps({"from_code": "en", "to_code": "es", "target_prefix": "<es>"}))
        calls = []
        class Tokenizer:
            def __init__(self, **kwargs):
                pass
            def encode(self, text, out_type):
                return text.split()
            def decode(self, tokens):
                return " ".join(tokens)
        class Model:
            def __init__(self, path, **kwargs):
                calls.append(kwargs)
            def translate_batch(self, tokens, **kwargs):
                calls.append(kwargs)
                return [SimpleNamespace(hypotheses=[["<es>", "Hola"]])]
        with patch.dict("sys.modules", {"sentencepiece": SimpleNamespace(SentencePieceProcessor=Tokenizer),
                                       "ctranslate2": SimpleNamespace(Translator=Model)}):
            translator = PairTranslator(folder)
            self.assertEqual(translator("Hello"), "Hola")
            self.assertEqual(calls[0], {"device": "cpu", "compute_type": "int8"})
            self.assertEqual(calls[1]["max_input_length"], 0)
            with self.assertRaises(ValueError):
                translator("word " * 513)
            self.assertEqual(len(calls), 2)

    def test_normal_launch_opens_before_model_setup_without_downloading(self):
        server = unittest.mock.Mock()
        server.server_port = 8765
        server.serve_forever.side_effect = KeyboardInterrupt
        with patch("sys.argv", ["snote", "--data-dir", str(self.root)]), \
             patch("snote.__main__.make_server", return_value=server), \
             patch("snote.__main__.LocalEngines.load_speech") as load, \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(), 0)
            self.assertIn("Open Models", output.getvalue())
            load.assert_not_called()

    def test_submillisecond_edit_cannot_create_zero_length_subtitle(self):
        project = sample_project()
        rows = json.loads(json.dumps(project["segments"]))
        rows[0]["end"] = 0.0001
        with self.assertRaises(Problem):
            apply_edits(project, {"segments": rows})

    def test_failed_model_switch_does_not_leave_a_stale_cached_name(self):
        engine = LocalEngines(self.root)
        calls = []
        def model(name, **kwargs):
            calls.append(name)
            if name == "base":
                raise RuntimeError("Not installed")
            return object()
        with patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(WhisperModel=model)}):
            self.assertIsNotNone(engine.load_speech("tiny"))
            with self.assertRaises(RuntimeError):
                engine.load_speech("base")
            self.assertIsNotNone(engine.load_speech("tiny"))
        self.assertEqual(calls, ["tiny", "base", "tiny"])


if __name__ == "__main__":
    unittest.main()
