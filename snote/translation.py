"""Run an installed Argos SentencePiece pack with the shared CT2 runtime.

Whisper already brings CTranslate2. Short transcript segments can be translated
directly without installing a second NLP pipeline for sentence segmentation.
"""

from collections import deque
import json
from pathlib import PurePosixPath
import re
import shutil
import stat
import tempfile
import zipfile


def metadata(folder):
    data = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("type", "translate") != "translate":
        raise ValueError("This is not a translation model pack.")
    for key in ("from_code", "to_code"):
        if not isinstance(data.get(key), str) or not re.fullmatch(r"[a-z]{2,3}", data[key]):
            raise ValueError("The pack needs valid source and target language codes.")
    if not isinstance(data.get("target_prefix", ""), str):
        raise ValueError("The model's target prefix is invalid.")
    if not (folder / "sentencepiece.model").is_file() or not (folder / "model" / "model.bin").is_file():
        raise ValueError("Choose an Argos pack containing sentencepiece.model and model/model.bin. BPE packs are not supported in this edition.")
    return data


class PairTranslator:
    def __init__(self, folder):
        data = metadata(folder)
        try:
            import ctranslate2
            import sentencepiece
        except ImportError as exc:
            raise RuntimeError("Install the audio dependencies first: python -m pip install -r requirements.txt") from exc
        self.prefix = data.get("target_prefix", "")
        self.tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(folder / "sentencepiece.model"))
        self.model = ctranslate2.Translator(str(folder / "model"), device="cpu", compute_type="int8")

    def __call__(self, text):
        tokens = self.tokenizer.encode(text, out_type=str)
        if not tokens:
            return ""
        if len(tokens) > 512:
            raise ValueError("A transcript line is too long to translate in one pass. Shorten that line to fewer than 512 model tokens, then translate again.")
        options = {"beam_size": 4, "replace_unknowns": True,
                   "max_input_length": 0, "max_decoding_length": 1536}
        if self.prefix:
            options["target_prefix"] = [[self.prefix]]
        result = self.model.translate_batch([tokens], **options)[0]
        translated = self.tokenizer.decode(result.hypotheses[0])
        if self.prefix and translated.startswith(self.prefix):
            translated = translated[len(self.prefix):]
        return translated.strip()


def installed(root):
    pairs = {}
    directory = root / "models" / "translation"
    if directory.exists():
        for folder in sorted(directory.iterdir()):
            if folder.is_dir() and not folder.name.startswith("."):
                try:
                    data = metadata(folder)
                    pairs[(data["from_code"], data["to_code"])] = folder
                except (ValueError, OSError):
                    continue
    return pairs


def route(pairs, source, target):
    queue = deque([(source, [])])
    seen = {source}
    while queue:
        language, steps = queue.popleft()
        if language == target:
            return steps
        for (start, end), path in pairs.items():
            if start == language and end not in seen:
                seen.add(end)
                queue.append((end, steps + [path]))
    raise RuntimeError(
        f"No installed translation route from {source} to {target}. "
        "Open Models, load the language list, and download the language pair you need."
    )


def install_pack(root, archive, validator=PairTranslator, expected_pair=None):
    if archive.suffix.lower() != ".argosmodel" or not archive.is_file():
        raise ValueError("Choose the .argosmodel file you downloaded from the Argos package index.")
    directory = root / "models" / "translation"
    directory.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if len(entries) > 10000 or sum(item.file_size for item in entries) > 1024 ** 3:
            raise ValueError("This model archive is too large for this edition.")
        for item in entries:
            path = PurePosixPath(item.filename)
            if (path.is_absolute() or ".." in path.parts or "\\" in item.filename
                    or ":" in item.filename or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError("This model archive contains an unsafe path.")
        candidates = [item for item in entries if PurePosixPath(item.filename).name == "metadata.json"
                      and len(PurePosixPath(item.filename).parts) <= 2]
        if len(candidates) != 1:
            raise ValueError("The archive must contain exactly one translation pack.")
        prefix = PurePosixPath(candidates[0].filename).parent
        with tempfile.TemporaryDirectory(prefix=".install-", dir=directory) as temporary:
            from pathlib import Path
            folder = Path(temporary) / "pack"
            folder.mkdir()
            for item in entries:
                path = PurePosixPath(item.filename)
                if not path.is_relative_to(prefix):
                    continue
                relative = path.relative_to(prefix)
                if not relative.parts or item.is_dir():
                    continue
                # Retain the model, tokenizer, metadata, and notices. Extra NLP
                # resources inside a pack are not needed by this segment-based app.
                if relative.parts[0] != "model" and relative.name not in {
                    "metadata.json", "sentencepiece.model", "README.md", "LICENSE", "LICENSE.txt"
                }:
                    continue
                destination = folder.joinpath(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with package.open(item) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
            data = metadata(folder)
            if expected_pair and (data["from_code"], data["to_code"]) != expected_pair:
                raise ValueError("The downloaded pack does not match the requested language pair.")
            destination = directory / f"{data['from_code']}-{data['to_code']}"
            if destination.exists():
                raise ValueError(f"{data['from_code']} → {data['to_code']} is already installed. No files were changed.")
            validator(folder)  # Load the actual model before accepting an installation.
            folder.rename(destination)
    return data
