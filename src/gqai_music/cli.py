from __future__ import annotations

import os
import webbrowser

import uvicorn


def main() -> None:
    host = os.environ.get("GQAI_MUSIC_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.environ.get("GQAI_MUSIC_PORT", "8877"))
    if host in {"127.0.0.1", "localhost"} and os.environ.get("GQAI_MUSIC_NO_BROWSER") != "1":
        webbrowser.open(f"http://127.0.0.1:{port}")
    uvicorn.run("gqai_music.webapp:app", host=host, port=port, reload=False)

