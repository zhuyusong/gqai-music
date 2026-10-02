from __future__ import annotations

import os
import math
from pathlib import Path
import subprocess
from typing import Any

from .media import file_sha256, probe


def render_sound_master(source: Path, output: Path, sound_effects: list[dict]) -> dict:
    """SFX-only master: preserve picture, duck the sound bed, do not boost voice."""
    source, output = source.resolve(), output.resolve()
    if source == output or output.exists():
        raise ValueError('音效后期不允许覆盖源视频或已有输出')
    media = probe(source)
    if not media.get('has_video') or not media.get('has_audio'):
        raise ValueError('音效母版需要视频与原口播轨道')
    if not sound_effects:
        raise ValueError('音效母版没有音效事件')
    duration = float(media['duration_seconds'])
    rate = 48000
    total_samples = round(duration * rate)
    inputs, chains, labels, manifest_events = [], [], [], []
    for i, event in enumerate(sound_effects):
        path = Path(event.get('path') or '').resolve()
        values = [event.get('trigger_seconds'), event.get('duration_seconds'), event.get('volume')]
        if (not path.is_file() or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)):
            raise ValueError('音效资源或数值无效')
        trigger, length, volume = values
        if not 0 <= trigger < duration or not 0 < length <= duration - trigger + .001 or not 0 < volume <= .5:
            raise ValueError('音效超出时间轴或音量上限')
        if event.get('asset_sha256') and file_sha256(path) != event['asset_sha256']:
            raise ValueError('音效资源哈希不匹配')
        sound_media = probe(path)
        if not sound_media.get('has_audio') or sound_media['duration_seconds'] + .02 < length:
            raise ValueError('音效资源时长不足')
        inputs.extend(['-i', str(path)])
        label, delay = f'sfx{i}', round(trigger * rate)
        chains.append(f'[{i+1}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,'
                      f'atrim=end_sample={round(length * rate)},asetpts=N/SR/TB,volume={volume:.6f},'
                      f'adelay={delay}S|{delay}S,apad,atrim=end_sample={total_samples},'
                      f'asetpts=N/SR/TB[{label}]')
        labels.append(f'[{label}]')
        manifest_events.append({k: v for k, v in event.items() if k != 'path'} | {
            'asset': {'uri': str(path), 'sha256': file_sha256(path)}})
    chains.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest[sfxbed]")
    # Split sidechain explicitly. Limiter level=false avoids the default gain boost;
    # latency compensation keeps the sound transient on its requested video frame.
    graph = ';'.join(['[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,'
                      f'apad,atrim=end_sample={total_samples},asetpts=N/SR/TB,'
                      'asplit=2[voice][sidechain]', *chains,
                      '[sfxbed][sidechain]sidechaincompress=threshold=0.04:ratio=4:attack=8:release=140[ducked]',
                      '[ducked]asplit=2[bedmix][sfxstem]',
                      '[voice][bedmix]amix=inputs=2:normalize=0:duration=first,'
                      'alimiter=limit=0.95:level=false:latency=true[aout]'])
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [os.environ.get('GQAI_FFMPEG', 'ffmpeg'), '-v', 'error', '-nostdin', '-n', '-i', str(source),
               *inputs, '-filter_complex', graph, '-map', '0:v:0', '-map', '[aout]',
               '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-t', str(duration),
               '-movflags', '+faststart', str(output), '-map', '[sfxstem]', '-c:a', 'pcm_s16le',
               '-t', str(duration), str(output.parent / 'sfx-only.wav')]
    subprocess.run(command, check=True, capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    final = probe(output)
    if not final.get('has_audio') or abs(final['duration_seconds'] - duration) > .1:
        raise ValueError('音效母版媒体验证失败')
    return {'schema': 'gqai-sound-master-1.0', 'source_sha256': file_sha256(source),
            'output': {'uri': str(output), 'sha256': file_sha256(output), 'media': final},
            'sound_effects': manifest_events, 'verification': _sound_evidence(source, output, output.parent / 'sfx-only.wav', sound_effects),
            'mix': {'voice_ducking': True, 'master_limiter': .95,
                                                   'voice_gain': 1, 'video_codec': 'copy'}}


def _sound_evidence(source: Path, output: Path, stem: Path, events: list[dict]) -> dict:
    import array
    ffmpeg = os.environ.get('GQAI_FFMPEG', 'ffmpeg')
    rate, channels = 48000, 2
    def decode(path):
        # Keep independent stereo samples: FFmpeg's mono downmix can add 3dB
        # for correlated channels and falsely report clipping above full scale.
        pcm = subprocess.run([ffmpeg, '-v', 'error', '-i', str(path), '-vn', '-ac', str(channels),
            '-ar', '48000', '-f', 'f32le', '-'], capture_output=True, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        values = array.array('f')
        values.frombytes(pcm)
        return values
    before, after, sound = decode(source), decode(output), decode(stem)
    rms = lambda values: (sum(v*v for v in values) / max(1, len(values))) ** .5
    cue_evidence = []
    for event in events:
        left = round(event['trigger_seconds'] * rate) * channels
        right = round((event['trigger_seconds'] + event['duration_seconds']) * rate) * channels
        cue_evidence.append({'sound_id': event['sound_id'], 'trigger_seconds': event['trigger_seconds'],
            'stem_rms': round(rms(sound[left:right]), 6), 'voice_rms': round(rms(before[left:right]), 6),
            'output_delta_rms': round(rms([a-b for a,b in zip(after[left:right], before[left:right])]), 6)})
    def picture_hash(path):
        return subprocess.run([ffmpeg, '-v', 'error', '-i', str(path), '-map', '0:v:0', '-c', 'copy',
            '-f', 'hash', '-hash', 'sha256', '-'], capture_output=True, check=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
    channel_peaks = [max((abs(v) for v in after[i::channels]), default=0) for i in range(channels)]
    peak = max(channel_peaks)
    unchanged = picture_hash(source) == picture_hash(output)
    return {'scope': 'decoded_audio_and_video_bitstream_not_subjective_listening',
            'picture_bitstream_unchanged': unchanged, 'decoded_audio_peak': round(peak, 6),
            'decoded_channel_peaks': [round(v, 6) for v in channel_peaks],
            'decoded_channels': channels,
            'sound_stem': str(stem), 'cues': cue_evidence,
            'passed': unchanged and peak < 1 and all(c['stem_rms'] > 0 and c['output_delta_rms'] > 0 for c in cue_evidence)}


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
        subprocess.run(command, check=True, capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
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
