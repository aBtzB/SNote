# SNote

A local audio notebook for interviews, lectures, and voice notes. Turn a recording into a timestamped transcript, translate it, and review both versions side by side.

SNote combines faster-whisper speech recognition with local Argos translation models. Its focus is the review step: play a line, correct the words, regenerate the translation, and export a transcript you have checked. It runs in your browser through a local Python server.

## What it does

- Transcribes audio in its original language.
- Translates the transcript using language packs you choose.
- Downloads speech and translation models through a **Models** screen, with progress, cancellation, and retry.
- Lets you edit both versions, search text, and mark individual lines reviewed.
- Plays audio from a timestamp and highlights the current line.
- Exports TXT, SRT, VTT, and JSON.
- Keeps recordings and transcripts on your computer using SQLite and local audio files.

There are no API keys or subscriptions. Model files are downloaded separately; recordings are processed locally. Opening the app or the Models panel does not start any downloads.

## First run

Install **Python 3.11 or 3.12**, extract the project, and open a terminal in the folder containing this README and `requirements.txt`.

You install the Python dependencies yourself. Once the app opens, you choose and download the models inside it.

### Windows — PowerShell

~~~powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m snote
~~~

If you installed Python 3.11, use `py -3.11` for the first command. These commands use the environment directly, so you do not need to activate it or change PowerShell's execution policy.

### macOS / Linux

~~~sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m snote
~~~

Use Python 3.11 instead if that is your installed version. You can use `python3` if its version is 3.11 or 3.12. Some Linux distributions package the venv module separately.

Open **http://127.0.0.1:8765** in your browser. Keep the terminal running while you use the app. Do not open index.html directly.

### Choose your models

1. Open **Models** in the sidebar.
2. Under **Speech recognition**, download **Tiny** for a modest first setup. Its model files are approximately 78 MB. Base, Small, Medium, Large v3 Turbo, and Large v3 are also available.
3. Under **Translation**, click **Load language list**. Search for a direction such as **English → Spanish**, then click **Download**.
4. Wait for the model to show **Installed**. You can use it immediately; no restart is needed.
5. Close Models, choose **New recording**, select an installed speech model, and add your audio.

A translation pack is optional if you only want transcription. Packs are directional: English → Spanish does not install Spanish → English. The list is limited to this edition's supported languages; availability depends on the Argos catalogue.

Only your selected models are installed. The panel shows actual transferred bytes and a percentage when the source supplies the total size. Closing the panel lets the download continue; keep the server running. Cancellation can take until the current network read returns. Retrying starts the download from the beginning.

Larger speech models can require several GB of storage and considerably more RAM. All inference in this edition uses the CPU. Python dependencies and translation packs add to the total download size. You do not need a separate FFmpeg installation, PyTorch, Node.js, or database server.

You can choose **Open a sample workspace** before installing models. It contains labelled illustrative text without audio; it demonstrates the editor, not recognition quality.

### Later runs

Open a terminal in the same project folder and run just:

Windows:

~~~powershell
.\.venv\Scripts\python.exe -m snote
~~~

macOS / Linux:

~~~sh
.venv/bin/python -m snote
~~~

Then visit **http://127.0.0.1:8765**. Dependencies and models stay installed. Press **Ctrl+C** in the terminal to stop the server; an active model operation may take time to finish.

## Try a short recording

Start with 15–30 seconds of clear speech, one speaker, and one language.

1. Add the audio using **New recording**. Select the spoken language if you know it.
2. Select a translation target for which you installed a pack, or leave translation off.
3. Click a timestamp to listen from that point.
4. Correct the source text and choose **Save changes** (Ctrl+S / Cmd+S).
5. Choose **Translate** after correcting the source.
6. Review both versions and export your saved transcript.

Changing source text clears a translation that you left unchanged and resets that line's review mark. If you correct both versions together, your new translation is preserved. Re-translation replaces existing translations and clears review marks.

## Scope and limits

| Area | Current behavior |
| --- | --- |
| Input | WAV, MP3, M4A, FLAC, OGG, OPUS, AAC, WEBM; codec support varies |
| Recording limits | 100 MB per upload; 30 minutes per recording |
| Speech | Six multilingual Whisper model sizes, CPU INT8 |
| Translation | SentencePiece Argos packs run through CTranslate2 |
| Language routes | Direct packs preferred; can chain already installed packs |
| Downloads | One at a time, separate from the inference queue |
| Processing | One inference worker; up to eight active or queued jobs |
| Edit safety | Explicit save, unsaved-change prompt, revision checks between tabs |
| Exports | Original, translated, or bilingual TXT/SRT/VTT; full JSON data |

Translation exports require every line to have a translation. Exports include all saved lines regardless of search filters. JSON is a data export, not an importable backup.

This version does not identify speakers, record your microphone, translate live calls, or generate a translated voice. It translates the text produced by recognition. Names, accents, overlapping speakers, and mixed-language audio need review. Segment-by-segment translation can lose context, and edited lines over 512 translation tokens are rejected rather than silently truncated.

If translation fails, the original transcript stays available. Install the missing pack and translate again without repeating recognition. Older BPE-tokenized translation packs are not supported; the installer checks compatibility before accepting a pack.

## Where your data lives

Recordings, the database, and installed models are saved under `~/.snote` by default (`C:\Users\your-name\.snote` on Windows). Deleting a recording removes its audio and database entry. Models remain available for other recordings.

If you used the earlier Sidenote edition, open your existing recordings and models with `python -m snote --data-dir ~/.sidenote`. The old speech-model installation markers remain supported. For a custom directory, supply that path instead. The environment variable for choosing a directory is now `SNOTE_DATA_DIR`.

To choose a different directory or port:

~~~sh
python -m snote --data-dir ./data serve --port 8766
~~~

Here and in the developer commands below, **python** means the Python executable in your environment. Use the same data directory whenever you return to those recordings and models. Run one server process per data directory. The server binds to localhost and is intended for one local user; it has no account system or encrypted storage.

Once your chosen models are installed, transcription and translation work offline. Refreshing the model list or downloading another model needs an internet connection.

## Project layout

| File | Responsibility |
| --- | --- |
| snote/server.py | Local HTTP routes, streamed uploads, audio byte ranges |
| snote/models.py | Model catalogue, downloads, progress, cancellation, installation |
| snote/jobs.py | Inference queue, partial results, cancellation |
| snote/engines.py | Audio decoding and recognition |
| snote/translation.py | Pack validation, tokenization, translation routes |
| snote/store.py | SQLite persistence and revision checks |
| snote/domain.py | Data validation and source-edit rules |
| snote/exports.py | Text and subtitle output |
| snote/static/ | Plain HTML, CSS, and JavaScript; no build step |
| tests/ | Application checks and optional real-model smoke test |

See [architecture](docs/architecture.md), [validation](docs/validation.md), and [project notes](docs/portfolio.md). [GitHub setup text](docs/GITHUB.md) includes an About description and suggested topics.

## Tests

The main suite does not download dependencies or models:

~~~sh
python -m unittest discover -s tests -v
~~~

If Node.js is available, check the browser script syntax:

~~~sh
node --check snote/static/app.js
~~~

The tests use deterministic inference and network substitutes. They exercise application behavior, not recognition accuracy. Real downloads, model inference, browser playback, and operating-system installation need verification on the target computer. See [validation notes](docs/validation.md) for the optional real-audio test.

## Troubleshooting

| Symptom | Try this |
| --- | --- |
| Upload button is disabled | Install dependencies, restart if you just installed them, then download a speech model in Models |
| Download fails | Read the message, check your connection and free disk space, then choose Retry |
| Speech model cannot load | Check the terminal and available RAM; try a smaller installed model |
| Translation route is missing | Download a pack for the spoken language → target direction; then translate again |
| Wrong language detected | Create a new recording with the spoken language selected explicitly |
| Installation fails on Python 3.13 or newer | Create a Python 3.11 or 3.12 environment |
| Port 8765 is in use | Stop the earlier process, or use another port and a separate data directory |
| Browser connection fails | Keep the terminal running and use the printed localhost address |
| Another tab changed the transcript | Copy unsaved text, then reload the recording before saving |

Run the dependency and storage check with:

~~~sh
python -m snote doctor
~~~

### Optional command-line model setup

The UI is the usual installation path. These commands remain available for existing workflows:

~~~sh
python -m snote install --speech tiny
python -m snote install --translation "/path/to/downloaded-pack.argosmodel"
~~~

The speech command downloads and loads the selected model. The translation command imports a local file from the [official Argos package index](https://www.argosopentech.com/argospm/index/). Only SentencePiece packs are supported. Previously installed SNote models remain usable.

## Acknowledgments and license

The starting idea came from [Blue-B/WhisperSubTranslate](https://github.com/Blue-B/WhisperSubTranslate). SNote is an independent implementation focused on audio review. No source files, branding, or interface assets from that repository were copied.

Recognition uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper), based on [OpenAI Whisper](https://github.com/openai/whisper), through [CTranslate2](https://github.com/OpenNMT/CTranslate2). Translation uses [Argos Translate](https://github.com/argosopentech/argos-translate) model packs and [SentencePiece](https://github.com/google/sentencepiece). Audio decoding uses [PyAV](https://pyav.org/). These projects supply the models and inference tools; SNote supplies the application around them. The Tiny size estimate comes from its [model files](https://huggingface.co/Systran/faster-whisper-tiny/tree/main).

The application source is MIT licensed. Downloaded models and third-party software keep their own licenses. Model weights and dependency binaries are not bundled with this repository.
