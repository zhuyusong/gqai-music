from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
from typing import Any


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe(path: Path) -> dict[str, Any]:
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    completed = subprocess.run([
        os.environ.get("GQAI_FFPROBE", "ffprobe"), "-v", "error",
        "-show_entries", "format=duration,size:stream=codec_type,width,height,avg_frame_rate",
        "-of", "json", str(path),
    ], check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    payload = json.loads(completed.stdout)
    streams = payload.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    duration = float(payload.get("format", {}).get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("媒体时长无效")
    result: dict[str, Any] = {
        "duration_seconds": round(duration, 6),
        "size_bytes": int(payload.get("format", {}).get("size") or path.stat().st_size),
        "has_audio": any(item.get("codec_type") == "audio" for item in streams),
        "has_video": video is not None,
    }
    if video:
        numerator, denominator = map(int, str(video.get("avg_frame_rate", "0/1")).split("/"))
        result.update(width=int(video["width"]), height=int(video["height"]),
                      fps=numerator / denominator if denominator else 0)
    return result


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)

