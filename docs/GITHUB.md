# GitHub setup text

## Repository name

`snote-audio`

## About description

Local audio transcription and translation with selectable model downloads, an editable bilingual transcript, and subtitle exports.

## Topics

`python` `javascript` `audio-transcription` `speech-recognition` `translation` `faster-whisper` `ctranslate2` `offline` `subtitles` `sqlite`

## Longer project description

SNote is a local tool for turning interviews, lectures, and voice notes into reviewed transcripts. Users choose speech and translation models in the app, upload a recording, and edit the original text alongside its translation. Timestamp playback helps check uncertain words, while review marks track the lines that have been checked. Finished transcripts can be exported as text, subtitles, or JSON.

The application uses Python, SQLite, and plain JavaScript. Speech recognition runs through faster-whisper; translation uses Argos model packs with CTranslate2 and SentencePiece. After model installation, recordings can be processed offline without an API key.

## What to put in the repository

The root README is ready to use. Include the source, tests, documentation, requirements, and license from this archive. The supplied .gitignore excludes local environments, audio, models, and databases.

After running the app, add your own screenshots of the Models screen and a reviewed transcript. Use a recording you can share. Include a short example export and describe any changes or evaluation you personally make. Keep the acknowledgments and report measured results only.

## CV starting point

After running and understanding the project, adapt this to your actual contribution:

> Developed a local audio review application in Python and JavaScript, integrating Whisper transcription and local translation models with selectable model downloads, timestamped editing, SQLite persistence, and subtitle exports.

Add a specific feature you implemented or a result you measured. The project integrates existing speech and translation models; it does not train them.
