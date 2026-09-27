import argparse
import json
import logging
import os
from pathlib import Path
import sys

from .domain import MODELS
from .engines import LocalEngines
from .server import make_server
from .translation import install_pack, installed


def main():
    parser = argparse.ArgumentParser(description="SNote — turn audio into a transcript you can work with.")
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("SNOTE_DATA_DIR", "~/.snote")).expanduser(),
                        help="Where to keep recordings, transcripts, and downloaded models.")
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="Open the local notebook server (default).")
    serve.add_argument("--port", type=int, default=8765)
    preview = commands.add_parser("preview", help="Explore the sample editor before model setup.")
    preview.add_argument("--port", type=int, default=8765)
    install = commands.add_parser("install", help="Download a speech model or import your downloaded translation pack.")
    install.add_argument("--speech", choices=MODELS)
    install.add_argument("--translation", type=Path, help="Path to a manually downloaded .argosmodel file.")
    commands.add_parser("doctor", help="Check the dependency and storage configuration.")
    args = parser.parse_args()
    root = args.data_dir.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if args.command == "install":
        if not (args.speech or args.translation):
            parser.error("Use --speech MODEL or --translation PATH_TO_ARGOSMODEL.")
        engines = LocalEngines(root)
        try:
            if args.speech:
                print(f"Downloading and checking the {args.speech} speech model…", flush=True)
                engines.load_speech(args.speech, download=True)
            if args.translation:
                data = install_pack(root, args.translation.expanduser())
                print(f"Installed translation pack: {data['from_code']} → {data['to_code']}")
            print("Model setup finished. Open SNote to use the models.")
        except Exception as exc:
            print(f"Setup failed: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.command == "doctor":
        print(json.dumps({"python": sys.version.split()[0], "data_dir": str(root),
                          "translation_pairs": [f"{a} → {b}" for a, b in installed(root)],
                          **LocalEngines(root).capabilities()}, indent=2))
        print("Models are downloaded separately. See README.md for setup and the real-audio smoke test.")
        return 0
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    engines = LocalEngines(root)
    try:
        server = make_server(root, getattr(args, "port", 8765), engines)
    except OSError as exc:
        print(f"Could not start SNote: {exc}. Try: python -m snote serve --port 8766", file=sys.stderr)
        return 1
    print(f"\nSNote is ready at http://127.0.0.1:{server.server_port}\n"
          "Open Models in the sidebar to choose and download models.\n"
          "Keep this terminal open. Press Ctrl+C to close SNote.\n", flush=True)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\nFinishing the current model operation before closing…", flush=True)
    finally:
        server.server_close()
        server.app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
