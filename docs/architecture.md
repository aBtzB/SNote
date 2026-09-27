# How the notebook fits together

## One recording, one review document

Each recording has a project ID, its processing status, language settings, and an ordered list of transcript segments. A segment stores start/end times, original text, translation, and a review mark. The sample uses the same editor and storage paths as real recordings; it cannot be submitted to the model worker.

The browser sends an upload directly as a binary request. The server streams it to a generated directory name. This avoids loading the whole upload into memory and does not use the supplied filename as a path. PyAV decodes the audio into a mono, 16 kHz WAV. That normalized copy becomes both the recognition input and the browser playback source.

The speech adapter always asks Whisper to **transcribe**. Whisper's built-in speech-to-English translation is a different operation; using it here would lose the original-language text. The installed Argos model pack handles translation through CTranslate2 after the source transcript has been saved.

## Why these choices

| Choice | Reason | Tradeoff |
| --- | --- | --- |
| Python standard-library server | The editor preview runs without installing a web framework | Intended for one local user; a hosted version needs a different server and authentication |
| Plain browser JavaScript | Easy to inspect and change; no bundler or package installation | State and rendering are managed explicitly |
| SQLite document per recording | Atomic updates and revision checks without a database service | Updating a segment rewrites the document; this is acceptable for the 30-minute limit |
| One model worker | Keeps inference off the request thread and limits simultaneous memory usage | Long jobs delay the queue |
| One cached speech model | Avoids keeping several model sizes resident | Switching model size reloads the model |
| CPU INT8 inference | A practical baseline without CUDA setup | Slower than suitable GPUs; no hardware-independent speed promise |
| Tiny speech model by default | A modest first model download | More recognition errors than larger models on difficult audio |
| Local translation packs | No API keys or transcript upload | Pair coverage and quality vary; indirect routes can lose meaning |
| CTranslate2 shared by both stages | Avoids another large NLP dependency stack | The translation adapter supports SentencePiece packs only and relies on the transcript's existing segmentation |
| Explicit model downloads in the UI | The user chooses the speech models and language pairs they need | Downloads need an internet connection; inference works offline afterwards |
| Explicit saving | Corrections form a deliberate, reviewable edit | Unsaved changes require a leave-page prompt |

## Jobs and partial results

The queue accepts up to eight active or pending jobs. A transcript job progresses through `queued`, `preparing`, `transcribing`, and optionally `translating`, then `ready`. Speech segments are persisted as they arrive. Progress is an approximate stage/position indicator, not a time estimate.

Translation results are collected separately and committed together. If one translation fails, the original transcript remains available and a warning explains the problem. A user can install the missing pack through Models and translate again without repeating transcription or restarting the app.

Cancellation sets an event. Decoding and transcription check it between frames or segments; translation checks it between lines. Native model operations are not forcibly terminated. Partial speech lines remain saved. On startup, an unfinished job is marked `interrupted`; the app does not guess whether to resume a half-completed inference run.

Retrying transcription is an explicit destructive operation on the derived transcript, so the UI confirms that it replaces existing edits. The original uploaded audio is retained for retries.

## Edits, revisions, and derived text

Every stored change increments a revision number. Saving from the browser includes the revision the user loaded. The store takes an SQLite write transaction, checks that revision, applies validation, and commits once. A mismatch returns HTTP 409 and leaves the current document untouched.

Source edits invalidate an unchanged translation. That rule lives in `domain.py`, so API clients cannot bypass it by avoiding the browser. Re-translation deliberately clears existing translation review marks. Timestamps must be finite, non-overlapping, ordered, and within the recording. The editor exposes text and review state; timing validation also protects the API.

## Local boundary

The server binds to `127.0.0.1`. It checks the Host and Origin, rejects cross-site requests, and requires an application header on mutations. It does not enable CORS. Static resources are an explicit route map, and file access uses server-generated recording IDs. Browser text is assigned through DOM text/value properties rather than interpolated HTML.

The normal launcher opens the app before model setup. Python dependencies are installed separately; the Models screen explains what is missing and only downloads after an explicit user action. Inference uses local files. The optional command-line installer remains available.

`models.py` manages a separate single download worker. Speech downloads use a fixed repository list, a resolved revision, expected byte counts, and SHA-256 checksums when supplied by the provider. Temporary files become an installed directory only after the download checks pass. The speech runtime is loaded when processing audio, not while installing a large model.

Translation choices come from the official Argos catalogue, restricted to supported languages and approved source hosts. The catalogue is cached locally. The importer rejects unsafe archive paths, incorrect language pairs, unsupported tokenizers, and incompatible translation models before accepting an installation. It keeps required model files and notices without a separate sentence-splitting toolkit. Failed refreshes preserve the previous catalogue; cancelled and failed downloads remove their temporary files. Downloads restart from the beginning on retry. The app does not fetch a catalogue when opening the model panel.

Local inference does not encrypt files or isolate them from other software running under the same OS account.

## Where to extend it

The engine adapter is the boundary for changing recognition or translation libraries. The export module is the boundary for another output format. To add speaker labels, start by adding a segment field and its editor control before integrating a diarization model. To host the app, first replace the local HTTP adapter, introduce user ownership, and move model work into separate worker processes.
