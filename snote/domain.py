from datetime import datetime, timezone
import math
import uuid

LANGUAGES = {
    "en": "English", "ar": "Arabic", "zh": "Chinese", "nl": "Dutch",
    "fr": "French", "de": "German", "hi": "Hindi", "id": "Indonesian",
    "it": "Italian", "ja": "Japanese", "ko": "Korean", "fa": "Persian",
    "pl": "Polish", "pt": "Portuguese", "ru": "Russian", "es": "Spanish",
    "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu", "vi": "Vietnamese",
}
SPEECH_MODELS = {
    "tiny": {"label": "Tiny", "repo": "Systran/faster-whisper-tiny", "note": "Lightest download. A quick first draft; start here."},
    "base": {"label": "Base", "repo": "Systran/faster-whisper-base", "note": "A modest step up in accuracy and memory use."},
    "small": {"label": "Small", "repo": "Systran/faster-whisper-small", "note": "More detail, with a larger download and slower CPU processing."},
    "medium": {"label": "Medium", "repo": "Systran/faster-whisper-medium", "note": "A large model for more capable computers."},
    "large-v3-turbo": {"label": "Large v3 Turbo", "repo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo", "note": "A larger, faster variant of Large v3. Higher memory use."},
    "large-v3": {"label": "Large v3", "repo": "Systran/faster-whisper-large-v3", "note": "The largest option here. Slow on a CPU and needs substantial memory."},
}
MODELS = tuple(SPEECH_MODELS)
BUSY = {"queued", "preparing", "transcribing", "translating"}
EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".webm", ".aac"}
MAX_UPLOAD = 100 * 1024 * 1024
MAX_DURATION = 30 * 60


class Problem(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def new_project(filename, model="tiny", source="auto", target=""):
    return {
        "id": uuid.uuid4().hex, "title": filename.rsplit(".", 1)[0],
        "filename": filename, "model": model, "source": source,
        "target": target, "detected_language": "", "duration": 0,
        "status": "new", "progress": 0, "message": "", "warning": "",
        "created_at": now(), "updated_at": now(), "revision": 0,
        "segments": [], "peaks": [], "has_audio": False, "is_demo": False,
    }


def validate_options(model, source, target):
    if model not in MODELS:
        raise Problem("Choose a speech model from the Models screen.")
    if source != "auto" and source not in LANGUAGES:
        raise Problem("Choose a supported source language.")
    if target and target not in LANGUAGES:
        raise Problem("Choose a supported translation language.")


def editable(project):
    if project["status"] in BUSY:
        raise Problem("Wait for processing to finish, or stop the job first.", 409)


def apply_edits(project, payload):
    editable(project)
    if "title" in payload:
        title = payload["title"]
        if not isinstance(title, str) or not title.strip() or len(title) > 120:
            raise Problem("Give the recording a title between 1 and 120 characters.")
        project["title"] = title.strip()
    if "segments" not in payload:
        return
    incoming = payload["segments"]
    current = project["segments"]
    if not isinstance(incoming, list) or len(incoming) != len(current):
        raise Problem("The segment list changed. Reload the recording before editing.", 409)
    cleaned = []
    last_end = 0.0
    for original, row in zip(current, incoming):
        if not isinstance(row, dict) or row.get("id") != original["id"]:
            raise Problem("Segment IDs must remain in their original order.")
        start, end = row.get("start"), row.get("end")
        if any(isinstance(t, bool) or not isinstance(t, (int, float))
               or not math.isfinite(t) for t in (start, end)):
            raise Problem("Timestamps must be finite numbers.")
        start, end = round(start, 3), round(end, 3)
        if start < last_end or end <= start or end > project["duration"] + 0.05:
            raise Problem("Segments must be ordered, non-overlapping, and within the audio.")
        text, translation = row.get("text"), row.get("translation", "")
        if not isinstance(text, str) or not text.strip() or len(text) > 5000:
            raise Problem("Each transcript line needs 1–5,000 characters.")
        if not isinstance(translation, str) or len(translation) > 5000:
            raise Problem("Translations cannot exceed 5,000 characters per line.")
        if not isinstance(row.get("reviewed"), bool):
            raise Problem("Review status must be true or false.")
        text, translation = text.strip(), translation.strip()
        source_changed = text != original["text"]
        translation_changed = translation != original["translation"]
        # An old translation is no longer trustworthy after a source correction.
        if source_changed and not translation_changed:
            translation = ""
        cleaned.append({
            "id": original["id"], "start": round(start, 3), "end": round(end, 3),
            "text": text, "translation": translation,
            "reviewed": False if source_changed else row["reviewed"],
        })
        last_end = end
    project["segments"] = cleaned


def sample_project():
    project = new_project("A quieter campus.wav", target="es")
    lines = [
        (0, 5.4, "We started with a simple question. Where do students go when they need a quiet minute?",
         "Empezamos con una pregunta sencilla. ¿Adónde van los estudiantes cuando necesitan un minuto de tranquilidad?"),
        (5.8, 12.1, "The library was the obvious answer, but it wasn't the only one. A few people mentioned the courtyard behind the engineering block.",
         "La biblioteca era la respuesta obvia, pero no la única. Algunas personas mencionaron el patio detrás del edificio de ingeniería."),
        (12.5, 18.6, "So we went there on a Tuesday afternoon, just after the lunch rush. It was surprisingly peaceful.",
         "Así que fuimos allí un martes por la tarde, justo después de la hora del almuerzo. Era sorprendentemente tranquilo."),
        (19.1, 27.2, "You could still hear the campus around you. Footsteps, a door closing, someone laughing in the distance. But none of it felt intrusive.",
         "Todavía se oía el campus alrededor. Pasos, una puerta que se cerraba, alguien riéndose a lo lejos. Pero nada resultaba molesto."),
        (27.7, 34.6, "Our next step is to map a few more spaces like this and ask students what makes each one work.",
         "Nuestro próximo paso es identificar más espacios como este y preguntar a los estudiantes qué hace que cada uno funcione."),
        (35.0, 41.8, "Sometimes a better campus doesn't need a new building. It just needs a little more attention to the spaces we already have.",
         "A veces, un campus mejor no necesita un edificio nuevo. Solo necesita un poco más de atención a los espacios que ya tenemos."),
    ]
    project.update(status="ready", progress=100, duration=41.8, detected_language="en",
                   is_demo=True, message="Sample workspace")
    project["segments"] = [
        {"id": i, "start": start, "end": end, "text": text,
         "translation": translation, "reviewed": i < 2}
        for i, (start, end, text, translation) in enumerate(lines)
    ]
    return project
