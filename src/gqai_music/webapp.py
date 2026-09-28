from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
from urllib.parse import urlsplit
import uuid

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .media import atomic_json, file_sha256, probe
from .mixer import render_music_master


PACKAGE_ROOT = Path(__file__).resolve().parent
WEB_ROOT = PACKAGE_ROOT / "web"
RUNTIME_ROOT = Path(os.environ.get("GQAI_MUSIC_RUNTIME", "runtime")).resolve()
PROJECT_PATTERN = re.compile(r"^[0-9a-f]{32}$")
ALLOWED_VIDEO = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
ALLOWED_AUDIO = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}

app = FastAPI(title="GQAI Music", version="0.1.0")
app.mount("/assets", StaticFiles(directory=WEB_ROOT), name="assets")


class MixRequest(BaseModel):
    volume_percent: int = Field(default=18, ge=0, le=100)
    video_start_seconds: float = Field(default=0, ge=0)
    music_in_seconds: float = Field(default=0, ge=0)
    loop: bool = True
    fade_in_seconds: float = Field(default=1.2, ge=0, le=30)
    fade_out_seconds: float = Field(default=2.0, ge=0, le=30)
    voice_ducking: bool = True


def _return_endpoint(return_url: str, source_job_id: str, return_token: str) -> str:
    if not return_url and not return_token:
        return ""
    if not return_url or not return_token or not re.fullmatch(r"[0-9a-f]{32}", source_job_id):
        raise HTTPException(status_code=400, detail="上游回传信息不完整")
    parsed = urlsplit(return_url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise HTTPException(status_code=400, detail="只允许回传到本机粗剪服务")
    try:
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="粗剪服务端口无效") from exc
    authority = parsed.hostname
    if authority == "::1":
        authority = "[::1]"
    if port:
        authority += f":{port}"
    return f"http://{authority}/api/jobs/{source_job_id}/music-master/raw"


def _return_master(directory: Path, value: dict, output: Path,
                   manifest_path: Path) -> dict:
    endpoint = str(value.get("return_endpoint", ""))
    token = str(value.get("return_token", ""))
    if not endpoint or not token:
        return {"status": "standalone"}
    try:
        with output.open("rb") as stream, httpx.Client(
                timeout=httpx.Timeout(600, connect=3), trust_env=False) as client:
            def chunks():
                while data := stream.read(1024 * 1024):
                    yield data

            response = client.post(
                endpoint,
                params={"filename": output.name,
                        "source_sha256": value["source"]["sha256"],
                        "manifest_sha256": file_sha256(manifest_path),
                        "token": token},
                headers={"Content-Type": "application/octet-stream"},
                content=chunks(),
            )
            response.raise_for_status()
            returned = response.json()
    except (OSError, ValueError, KeyError, httpx.HTTPError) as exc:
        return {"status": "failed", "detail": "音乐母版已生成，但暂时无法回传粗剪系统",
                "error_type": type(exc).__name__}
    value.pop("return_token", None)
    return {"status": "returned", "video_url": returned.get("video_url", ""),
            "publish_url": returned.get("publish_url", "")}


def _project_dir(project_id: str) -> Path:
    if not PROJECT_PATTERN.fullmatch(project_id):
        raise HTTPException(status_code=404, detail="音乐任务不存在")
    path = RUNTIME_ROOT / "projects" / project_id
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="音乐任务不存在")
    return path


def _metadata(project_id: str) -> tuple[Path, dict]:
    directory = _project_dir(project_id)
    try:
        value = json.loads((directory / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="音乐任务数据损坏") from exc
    return directory, value


async def _save_raw(request: Request, destination: Path, *, limit: int) -> None:
    size = 0
    with destination.open("wb") as output:
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                output.close()
                destination.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="文件过大")
            output.write(chunk)


def _public(project_id: str, directory: Path, value: dict) -> dict:
    result = dict(value)
    result.pop("return_token", None)
    result.pop("return_endpoint", None)
    result["source_url"] = f"/api/projects/{project_id}/files/{value['source']['stored_name']}"
    music = value.get("music")
    if isinstance(music, dict) and (directory / music.get("stored_name", "")).is_file():
        result["music_url"] = f"/api/projects/{project_id}/files/{music['stored_name']}"
    render = value.get("latest_render")
    if isinstance(render, dict):
        relative = render.get("video")
        if isinstance(relative, str) and (directory / relative).is_file():
            result["master_url"] = f"/api/projects/{project_id}/files/{relative}"
            result["manifest_url"] = f"/api/projects/{project_id}/files/{render['manifest']}"
    return result


@app.middleware("http")
async def no_stale_ui(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/assets/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/", include_in_schema=False)
def home() -> HTMLResponse:
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "styles.css"):
        digest = hashlib.sha256((WEB_ROOT / name).read_bytes()).hexdigest()[:12]
        html = html.replace(f'/assets/{name}"', f'/assets/{name}?v={digest}"')
    return HTMLResponse(html)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "service": "gqai-music",
            "ffmpeg": shutil.which(os.environ.get("GQAI_FFMPEG", "ffmpeg")) is not None,
            "ffprobe": shutil.which(os.environ.get("GQAI_FFPROBE", "ffprobe")) is not None}


@app.get("/api/projects")
def list_projects() -> dict:
    values = []
    root = RUNTIME_ROOT / "projects"
    if root.is_dir():
        for path in root.iterdir():
            try:
                item = json.loads((path / "project.json").read_text(encoding="utf-8"))
                values.append({"project_id": item["project_id"], "created_at": item["created_at"],
                               "source_filename": item["source"]["filename"],
                               "music_filename": item.get("music", {}).get("filename"),
                               "rendered": bool(item.get("latest_render"))})
            except (OSError, ValueError, KeyError):
                continue
    values.sort(key=lambda item: item["created_at"], reverse=True)
    return {"items": values[:30]}


@app.post("/api/projects/raw")
async def create_project(request: Request, filename: str,
                         source_system: str = "manual", source_job_id: str = "",
                         return_url: str = "", return_token: str = "") -> dict:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_VIDEO:
        raise HTTPException(status_code=415, detail="不支持的视频格式")
    project_id = uuid.uuid4().hex
    directory = RUNTIME_ROOT / "projects" / project_id
    directory.mkdir(parents=True)
    source = directory / f"source{suffix}"
    try:
        await _save_raw(request, source, limit=10 * 1024 * 1024 * 1024)
        media = probe(source)
        if not media.get("has_video"):
            raise ValueError("交接文件不包含视频轨道")
        return_endpoint = _return_endpoint(return_url, source_job_id, return_token)
        value = {
            "schema": "gqai-music-project-1.0", "project_id": project_id,
            "created_at": time.time(), "source_system": source_system[:80],
            "source_job_id": source_job_id[:128],
            "source": {"filename": Path(filename).name, "stored_name": source.name,
                       "sha256": file_sha256(source), "media": media},
            "mix": {"volume_percent": 18, "video_start_seconds": 0,
                    "music_in_seconds": 0, "loop": True, "fade_in_seconds": 1.2,
                    "fade_out_seconds": 2.0, "voice_ducking": True},
        }
        if return_endpoint:
            value.update(return_endpoint=return_endpoint, return_token=return_token)
        atomic_json(directory / "project.json", value)
    except Exception as exc:
        shutil.rmtree(directory, ignore_errors=True)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _public(project_id, directory, value)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict:
    directory, value = _metadata(project_id)
    return _public(project_id, directory, value)


@app.post("/api/projects/{project_id}/music/raw")
async def upload_music(project_id: str, request: Request, filename: str) -> dict:
    directory, value = _metadata(project_id)
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_AUDIO:
        raise HTTPException(status_code=415, detail="不支持的音乐格式")
    music_dir = directory / "music"
    music_dir.mkdir(exist_ok=True)
    target = music_dir / f"track-{uuid.uuid4().hex}{suffix}"
    try:
        await _save_raw(request, target, limit=1024 * 1024 * 1024)
        media = probe(target)
        if not media.get("has_audio"):
            raise ValueError("文件不包含音频轨道")
    except Exception as exc:
        target.unlink(missing_ok=True)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    value["music"] = {"filename": Path(filename).name,
                      "stored_name": target.relative_to(directory).as_posix(),
                      "sha256": file_sha256(target), "media": media}
    value.pop("latest_render", None)
    atomic_json(directory / "project.json", value)
    return _public(project_id, directory, value)


@app.post("/api/projects/{project_id}/render")
def render_project(project_id: str, request: MixRequest) -> dict:
    directory, value = _metadata(project_id)
    music = value.get("music")
    if not isinstance(music, dict):
        raise HTTPException(status_code=409, detail="请先导入背景音乐")
    source = (directory / value["source"]["stored_name"]).resolve()
    track = (directory / music["stored_name"]).resolve()
    version = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    render_dir = directory / "renders" / version
    output = render_dir / "music-master.mp4"
    manifest_path = render_dir / "gqai-music-manifest.json"
    try:
        manifest = render_music_master(source, track, output, request.model_dump())
        manifest.update(project_id=project_id, created_at=time.time(),
                        source_system=value.get("source_system", "manual"),
                        source_job_id=value.get("source_job_id", ""))
        atomic_json(manifest_path, manifest)
    except Exception as exc:
        shutil.rmtree(render_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    value["mix"] = request.model_dump()
    value["latest_render"] = {
        "video": output.relative_to(directory).as_posix(),
        "manifest": manifest_path.relative_to(directory).as_posix(),
        "output_sha256": manifest["output"]["sha256"], "created_at": manifest["created_at"],
    }
    value["latest_render"]["return_sync"] = _return_master(
        directory, value, output, manifest_path)
    atomic_json(directory / "project.json", value)
    return {**_public(project_id, directory, value), "manifest": manifest}


@app.post("/api/projects/{project_id}/return")
def return_project(project_id: str) -> dict:
    directory, value = _metadata(project_id)
    render = value.get("latest_render")
    if not isinstance(render, dict):
        raise HTTPException(status_code=409, detail="请先生成音乐母版")
    output = (directory / str(render.get("video", ""))).resolve()
    manifest_path = (directory / str(render.get("manifest", ""))).resolve()
    if directory.resolve() not in output.parents or directory.resolve() not in manifest_path.parents:
        raise HTTPException(status_code=409, detail="音乐母版路径无效")
    sync = _return_master(directory, value, output, manifest_path)
    render["return_sync"] = sync
    atomic_json(directory / "project.json", value)
    if sync["status"] == "failed":
        raise HTTPException(status_code=502, detail=sync["detail"])
    return {**_public(project_id, directory, value), "return_sync": sync}


@app.get("/api/projects/{project_id}/files/{file_path:path}")
def project_file(project_id: str, file_path: str, download: bool = False) -> FileResponse:
    directory = _project_dir(project_id).resolve()
    target = (directory / file_path).resolve()
    if directory not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    if download:
        return FileResponse(target, filename=target.name, content_disposition_type="attachment")
    return FileResponse(target)

