# Validation notes

This distinguishes tested application behavior from model quality. No recognition accuracy, translation quality, or speed benchmark is claimed.

## Automated application checks

Run from the project root:

```sh
python -m unittest discover -s tests -v
node --check snote/static/app.js
```

The application tests start the real local HTTP server on an ephemeral port and use temporary SQLite databases and audio directories. They exercise uploads, the worker queue, edit conflicts, invalid edits, cancellation, restart recovery, audio byte ranges, exports, and local-origin checks. Model doubles provide deterministic transcript and translation results. This makes failures reproducible without downloading model weights.

Download tests use deterministic byte streams to check progress totals, checksums, incomplete files, cancellation cleanup, retry, duplicate requests, cached catalogues, and translation-pair validation. HTTP tests also cover the model endpoints and request guards. These tests do not contact the actual model hosts.

The verification environment ran Python 3.12.14. **44 automated tests passed; one optional real-model test was skipped.** JavaScript syntax and the interface's element references also checked successfully. The environment could not download model dependencies or weights, and had no browser executable available for a rendered UI check. Consequently, real downloads, model inference, browser playback, visual layout, and operating-system installation still require checks on the target computer. The sample text is illustrative, not an inference fixture.

## Real-audio smoke test

Install the dependencies following the README. In Models, download Tiny and the English → Spanish translation pack. Make a short English WAV or MP3 recording containing the phrase “This is a test recording.” Choose a quiet setting and one speaker.

On macOS/Linux, from an activated environment:

```sh
export SNOTE_TEST_AUDIO="/absolute/path/to/your-recording.wav"
export SNOTE_EXPECT_TEXT="test recording"
python -m unittest discover -s tests -p test_models.py -v
```

On Windows PowerShell:

```powershell
$env:SNOTE_TEST_AUDIO = "C:\path\to\your-recording.wav"
$env:SNOTE_EXPECT_TEXT = "test recording"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_models.py -v
```

Optional variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SNOTE_DATA_DIR` | `~/.snote` | Use the directory where you installed the models |
| `SNOTE_TEST_MODEL` | `tiny` | Speech model to exercise |
| `SNOTE_TEST_SOURCE` | `en` | Spoken language or `auto` |
| `SNOTE_TEST_TARGET` | `es` | Installed translation target |
| `SNOTE_EXPECT_TEXT` | Empty | Case-insensitive phrase to require in the recognized text |

The test decodes the actual audio, runs Whisper, checks for text and the optional expected phrase, then runs the installed translation pack through CTranslate2 and checks for a non-empty result. Read the printed source and translation yourself. A non-empty translation is an integration check, not proof of semantic accuracy. Unset the test variables afterwards if you want the normal suite to run without model inference.

## Browser checks

Run these in the browser and operating system you plan to demo:

First, use a fresh data directory to check model setup: the app should open without models and make no external requests until you choose a download or Load language list. Download Tiny, observe progress, and confirm it becomes selectable without a restart. Load the translation list, search for a pair, and install it. Start another speech download, cancel it, and confirm it is not marked installed; retry and let it finish. Close and reopen the Models panel during a download to check that progress continues. Restart the server and confirm installed models and the language list persist.

1. Open the sample. Edit a line, save it, reload, and confirm the correction persists.
2. Search for a word in each language. Toggle **Needs review** and mark a visible line reviewed.
3. Export TXT, SRT, VTT, and JSON. Verify accented characters and timestamps in a text editor.
4. Upload your short recording. Play it normally and from several timestamp buttons. Confirm the active line changes with playback.
5. Correct the source. Save, confirm the unchanged translation clears, then regenerate it.
6. Open two tabs and confirm that a stale save is rejected.
7. Try a missing translation route. Confirm the original transcript can still be edited and exported.
8. Start a job, stop it, and then retry it. Delete the test recording when finished.
9. Check a narrow window, keyboard focus, dialog closing, and a right-to-left transcript if you will use Arabic, Persian, or Urdu.

After model setup, disconnect from the network and process the short clip again to verify your particular installation has all required cached resources. Keep this result alongside your actual machine specifications if you publish performance figures.
