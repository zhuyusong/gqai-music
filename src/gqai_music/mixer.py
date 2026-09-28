from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Any

from .media import file_sha256, probe


def _seconds(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def validate_mix(mix: dict[str, Any], source_media: dict, music_media: dict) -> dict[str, Any]:
    volume = int(mix.get("volume_percent", 18))
    video_start = float(mix.get("video_start_seconds", 0))
    music_in = float(mix.get("music_in_seconds", 0))
    fade_in = float(mix.get("fade_in_seconds", 1.2))
    fade_out = float(mix.get("fade_out_seconds", 2.0))
    if not 0 <= volume <= 100:
        raise ValueError("音乐音量必须在 0–100 之间")
    if not 0 <= video_start < float(source_media["duration_seconds"]):
        raise ValueError("音乐进入时间必须位于成片时长内")
    if not 0 <= music_in < float(music_media["duration_seconds"]):
        raise ValueError("音乐素材入点必须位于音乐时长内")
    if not 0 <= fade_in <= 30 or not 0 <= fade_out <= 30:
        raise ValueError("淡入淡出必须在 0–30 秒之间")
    remaining = float(source_media["duration_seconds"]) - video_start
    available = float(music_media["duration_seconds"]) - music_in
    play_duration = remaining if bool(mix.get("loop", True)) else min(remaining, available)
    if play_duration <= .05:
        raise ValueError("可用音乐片段过短")
    if fade_in + fade_out > play_duration:
        raise ValueError("淡入和淡出总时长不能超过实际音乐片段")
    return {
        "volume_percent": volume,
        "video_start_seconds": round(video_start, 6),
        "music_in_seconds": round(music_in, 6),
        "loop": bool(mix.get("loop", True)),
        "fade_in_seconds": round(fade_in, 6),
        "fade_out_seconds": round(fade_out, 6),
        "voice_ducking": bool(mix.get("voice_ducking", True)),
        "play_duration_seconds": round(play_duration, 6),
    }


def render_music_master(source: Path, music: Path, output: Path,
                        mix: dict[str, Any],
                        sound_effects: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    source = source.resolve()
    music = music.resolve()
    output = output.resolve()
    source_media = probe(source)
    music_media = probe(music)
    if not source_media.get("has_video"):
        raise ValueError("交接素材不包含视频轨道")
    if not music_media.get("has_audio"):
        raise ValueError("音乐素材不包含音频轨道")
    settings = validate_mix(mix, source_media, music_media)
    source_duration = float(source_media["duration_seconds"])
    play_duration = float(settings["play_duration_seconds"])
    fade_out_start = max(0.0, play_duration - float(settings["fade_out_seconds"]))
    delay_ms = round(float(settings["video_start_seconds"]) * 1000)
    sound_effects = sound_effects or []
    sfx_chains = []
    sfx_labels = []
    sfx_inputs = []
    manifest_sfx = []
    for index, event in enumerate(sound_effects):
        path = Path(str(event.get("path") or "")).resolve()
        if not path.is_file():
            raise ValueError(f"事件音效文件不存在：{path}")
        trigger = float(event.get("trigger_seconds", 0))
        duration = float(event.get("duration_seconds", 0))
        volume = float(event.get("volume", 1))
        fade_in = float(event.get("fade_in_seconds", 0))
        fade_out = float(event.get("fade_out_seconds", 0))
        asset_offset = float(event.get("asset_offset_seconds", 0))
        if trigger < 0 or duration <= 0 or trigger + duration > source_duration + .001:
            raise ValueError(f"事件音效 {index} 超出视频时间轴")
        if volume < 0 or fade_in < 0 or fade_out < 0 or fade_in + fade_out > duration:
            raise ValueError(f"事件音效 {index} 的音量或淡化参数无效")
        input_index = index + 2
        label = f"sfx{index}"
        delay = round(trigger * 1000)
        sfx_fade_out_start = max(0.0, duration - fade_out)
        chain = (
            f"[{input_index}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            f"atrim=start={_seconds(asset_offset)}:duration={_seconds(duration)},"
            f"asetpts=PTS-STARTPTS,volume={volume:.6f}"
        )
        if fade_in:
            chain += f",afade=t=in:st=0:d={_seconds(fade_in)}"
        if fade_out:
            chain += (f",afade=t=out:st={_seconds(sfx_fade_out_start)}:"
                      f"d={_seconds(fade_out)}")
        chain += f",adelay={delay}|{delay}[{label}]"
        sfx_chains.append(chain)
        sfx_labels.append(f"[{label}]")
        sfx_inputs.extend(["-i", str(path)])
        manifest_sfx.append({
            "sound_id": event.get("sound_id", f"sfx-{index:04d}"),
            "anchor_event_id": event.get("anchor_event_id"),
            "trigger_seconds": round(trigger, 6),
            "duration_seconds": round(duration, 6),
            "volume": volume,
            "asset": {"uri": str(path), "sha256": file_sha256(path)},
        })
    music_chain = (
        f"[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"atrim=duration={_seconds(play_duration)},asetpts=PTS-STARTPTS,"
        f"volume={settings['volume_percent'] / 100:.6f},"
        f"afade=t=in:st=0:d={_seconds(float(settings['fade_in_seconds']))},"
        f"afade=t=out:st={_seconds(fade_out_start)}:d={_seconds(float(settings['fade_out_seconds']))},"
        f"adelay={delay_ms}|{delay_ms}[music]"
    )
    if source_media.get("has_audio"):
        voice_chain = "[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[voice]"
        if settings["voice_ducking"]:
            combine = ("[music][voice]sidechaincompress=threshold=0.025:ratio=8:attack=20:release=300[bed];"
                       f"[voice][bed]{''.join(sfx_labels)}amix=inputs={2 + len(sfx_labels)}:"
                       "duration=first:dropout_transition=0:normalize=0,"
                       "alimiter=limit=0.95[aout]")
        else:
            combine = (f"[voice][music]{''.join(sfx_labels)}amix=inputs={2 + len(sfx_labels)}:"
                       "duration=first:dropout_transition=0:normalize=0,"
                       "alimiter=limit=0.95[aout]")
        audio_filter = ";".join([voice_chain, music_chain, *sfx_chains, combine])
    else:
        if sfx_labels:
            combine = (f"[music]{''.join(sfx_labels)}amix=inputs={1 + len(sfx_labels)}:"
                       "duration=longest:dropout_transition=0:normalize=0,"
                       f"apad=pad_dur={_seconds(source_duration)},"
                       f"atrim=duration={_seconds(source_duration)},alimiter=limit=0.95[aout]")
            audio_filter = ";".join([music_chain, *sfx_chains, combine])
        else:
            audio_filter = (f"{music_chain};[music]apad=pad_dur={_seconds(source_duration)},"
                            f"atrim=duration={_seconds(source_duration)},alimiter=limit=0.95[aout]")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.stem + ".partial" + output.suffix)
    command = [os.environ.get("GQAI_FFMPEG", "ffmpeg"), "-hide_banner", "-nostdin",
               "-loglevel", "error", "-y", "-i", str(source)]
    if settings["loop"]:
        command.extend(["-stream_loop", "-1"])
    if settings["music_in_seconds"]:
        command.extend(["-ss", _seconds(float(settings["music_in_seconds"]))])
    command.extend(["-i", str(music), *sfx_inputs, "-filter_complex", audio_filter,
                    "-map", "0:v:0", "-map", "[aout]", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                    "-t", _seconds(source_duration), str(temporary)])
    try:
        subprocess.run(command, check=True, capture_output=True)
        result_media = probe(temporary)
        if not result_media.get("has_video") or not result_media.get("has_audio"):
            raise ValueError("音乐成片缺少视频或音频轨道")
        if abs(float(result_media["duration_seconds"]) - source_duration) > .35:
            raise ValueError("音乐成片时长与输入视频不一致")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "schema": "gqai-music-manifest-1.0",
        "source": {"uri": str(source), "sha256": file_sha256(source), "media": source_media},
        "music": {"uri": str(music), "sha256": file_sha256(music), "media": music_media},
        "sound_effects": manifest_sfx,
        "mix": settings,
        "output": {"uri": str(output), "sha256": file_sha256(output), "media": result_media},
    }
