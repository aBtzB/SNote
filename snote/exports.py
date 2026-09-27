import json
from .domain import Problem


def timestamp(seconds, separator=","):
    milliseconds = max(0, round(seconds * 1000))
    seconds, milliseconds = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{milliseconds:03}"


def export(project, kind, content="original"):
    if kind not in {"txt", "srt", "vtt", "json"}:
        raise Problem("Choose TXT, SRT, VTT, or JSON.")
    if content not in {"original", "translation", "both"}:
        raise Problem("Choose original, translation, or both.")
    segments = project["segments"]
    if not segments:
        raise Problem("There are no transcript lines to export yet.")
    if kind == "json":
        return json.dumps(project, indent=2, ensure_ascii=False) + "\n", "application/json"
    if content != "original" and any(not s["translation"].strip() for s in segments):
        raise Problem("Some translations are missing. Translate or fill them in before exporting this version.")

    def line(segment):
        # Blank lines would terminate an SRT cue. Collapse internal whitespace.
        original = " ".join(segment["text"].split())
        translation = " ".join(segment["translation"].split())
        if content == "original":
            return original
        if content == "translation":
            return translation
        return original + "\n" + translation

    if kind == "txt":
        return "\n\n".join(line(s) for s in segments) + "\n", "text/plain"
    chunks = ["WEBVTT\n"] if kind == "vtt" else []
    separator = "." if kind == "vtt" else ","
    for index, segment in enumerate(segments, 1):
        body = line(segment)
        # Subtitle parsers interpret <...> as markup; transcript text stays literal.
        body = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        chunks.append(f"{index}\n{timestamp(segment['start'], separator)} --> "
                      f"{timestamp(segment['end'], separator)}\n{body}\n")
    return "\n".join(chunks), "text/vtt" if kind == "vtt" else "application/x-subrip"
