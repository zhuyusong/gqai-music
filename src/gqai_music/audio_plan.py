from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any


AUDIO_PLAN_SCHEMA = "gqai-audio-plan-1.0"
SOUND_PRESETS = {
    'whoosh': {'duration_seconds': .40, 'description': '短促扫过声，用于推进与滑入'},
    'pop': {'duration_seconds': .20, 'description': '轻弹点，用于弹入与关键词'},
    'click': {'duration_seconds': .16, 'description': '轻点击，用于编号与功能列表'},
    'chime': {'duration_seconds': .55, 'description': '短双音提示，用于完成与结论'},
    'impact': {'duration_seconds': .35, 'description': '柔和低频强调，用于重卖点'},
}


def synthesize_preset(name: str, output: Path) -> dict:
    """Original deterministic PCM tones/noise; no downloaded or copied samples."""
    import math
    import random
    import struct
    import wave
    from .media import file_sha256
    if name not in SOUND_PRESETS:
        raise ValueError('未知音效预设')
    rate, duration = 48000, SOUND_PRESETS[name]['duration_seconds']
    rng = random.Random(20261001)
    samples, smooth_noise = [], 0.0
    for i in range(round(rate * duration)):
        t, p = i / rate, i / (rate * duration)
        attack = min(1, t / .006)
        if name == 'whoosh':
            smooth_noise = .72 * smooth_noise + .28 * rng.uniform(-1, 1)
            value = smooth_noise * math.sin(math.pi * p) ** 1.5
        elif name == 'pop':
            value = math.sin(2 * math.pi * (480 * t - 650 * t * t)) * math.exp(-t * 23)
        elif name == 'click':
            value = (.7 * rng.uniform(-1, 1) + .3 * math.sin(2 * math.pi * 1800 * t)) * math.exp(-t * 65)
        elif name == 'chime':
            value = .55 * math.sin(2 * math.pi * 880 * t) * math.exp(-t * 7)
            if t >= .12:
                value += .45 * math.sin(2 * math.pi * 1320 * (t - .12)) * math.exp(-(t - .12) * 9)
        else:
            value = (.8 * math.sin(2 * math.pi * (95 * t - 50 * t * t)) + .2 * rng.uniform(-1, 1)) * math.exp(-t * 16)
        samples.append(value * attack * min(1, (duration - t) / .03))
    peak = max(abs(v) for v in samples)
    pcm = b''.join(struct.pack('<h', round(v / peak * .7 * 32767)) for v in samples)
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), 'wb') as stream:
        stream.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        stream.writeframes(pcm)
    return {'uri': str(output.resolve()), 'sha256': file_sha256(output),
            'preset': name, 'origin': 'gqai-procedural-sfx-v1', 'sample_rate': rate, 'duration_seconds': duration}


def _project_clock(execution_table: dict[str, Any]) -> tuple[int, int]:
    project = execution_table.get("project")
    if not isinstance(project, dict):
        raise ValueError("执行表缺少项目时钟")
    fps = project.get("fps")
    duration = project.get("duration_frames")
    if isinstance(fps, bool) or not isinstance(fps, int) or fps <= 0:
        raise ValueError("执行表帧率无效")
    if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
        raise ValueError("执行表时长无效")
    return fps, duration


def _event_index(execution_table: dict[str, Any]) -> dict[str, dict[str, Any]]:
    effects = execution_table.get("effects")
    if not isinstance(effects, list):
        raise ValueError("执行表缺少画面事件")
    result: dict[str, dict[str, Any]] = {}
    for index, effect in enumerate(effects):
        if not isinstance(effect, dict):
            raise ValueError(f"画面事件 {index} 无效")
        event_id = effect.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            event_id = f"visual-{index:04d}"
            effect["event_id"] = event_id
        if event_id in result:
            raise ValueError(f"画面事件 ID 重复：{event_id}")
        frame_range = effect.get("range")
        if (not isinstance(frame_range, list) or len(frame_range) != 2
                or any(isinstance(item, bool) or not isinstance(item, int)
                       for item in frame_range)):
            raise ValueError(f"画面事件 {event_id} 的时间范围无效")
        result[event_id] = effect
    return result


def _infer_anchor(events: dict[str, dict[str, Any]], trigger_frame: int) -> str:
    candidates = [
        (int(effect["range"][0]), event_id)
        for event_id, effect in events.items()
        if int(effect["range"][0]) <= trigger_frame < int(effect["range"][1])
    ]
    if not candidates:
        raise ValueError(f"帧 {trigger_frame} 的音效没有可绑定的画面事件")
    return max(candidates)[1]


def separate_audio_plan(execution_table: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Move SFX details out of a legacy execution table while preserving event binding."""
    visual = deepcopy(execution_table)
    fps, duration = _project_clock(visual)
    events = _event_index(visual)
    audio = visual.get("audio")
    if not isinstance(audio, dict):
        raise ValueError("执行表音频字段无效")
    raw_sfx = audio.get("sfx_events", [])
    if not isinstance(raw_sfx, list):
        raise ValueError("执行表音效事件必须是数组")

    sound_effects = []
    for index, raw in enumerate(raw_sfx):
        if not isinstance(raw, dict):
            raise ValueError(f"音效事件 {index} 无效")
        trigger = raw.get("trigger_frame")
        if isinstance(trigger, bool) or not isinstance(trigger, int):
            raise ValueError(f"音效事件 {index} 缺少触发帧")
        anchor = raw.get("anchor_event_id")
        if not isinstance(anchor, str) or not anchor:
            anchor = _infer_anchor(events, trigger)
        if anchor not in events:
            raise ValueError(f"音效事件引用了不存在的画面事件：{anchor}")
        offset = raw.get("offset_frames", trigger - int(events[anchor]["range"][0]))
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise ValueError(f"音效事件 {index} 的相对偏移无效")
        item = {
            "sound_id": str(raw.get("sound_id") or f"sfx-{index:04d}"),
            "anchor_event_id": anchor,
            "offset_frames": offset,
            "duration_frames": int(raw["duration_frames"]),
            "volume": float(raw["volume"]),
        }
        for name in ("asset_uri", "asset_offset_seconds", "fade_in_frames", "fade_out_frames"):
            if name in raw:
                item[name] = raw[name]
        sound_effects.append(item)

    visual["audio"] = {
        "sfx_events": [],
        "external_plan": {"schema": AUDIO_PLAN_SCHEMA, "uri": "gqai-audio-plan.json"},
    }
    plan = {
        "schema": AUDIO_PLAN_SCHEMA,
        "clock": {"fps": fps, "duration_frames": duration,
                  "frame_contract": "zero-based half-open [start, end)"},
        "music_tracks": [],
        "ambience_tracks": [],
        "sound_effects": sound_effects,
        "mix": {"voice_ducking": True, "master_limiter": 0.95},
    }
    return visual, plan


def resolve_sound_effects(audio_plan: dict[str, Any], visual_table: dict[str, Any],
                          *, asset_root: Path | None = None) -> list[dict[str, Any]]:
    """Resolve event-relative SFX against the current visual timeline."""
    if audio_plan.get("schema") != AUDIO_PLAN_SCHEMA:
        raise ValueError("不支持的音频计划版本")
    fps, duration = _project_clock(visual_table)
    events = _event_index(visual_table)
    resolved = []
    for index, item in enumerate(audio_plan.get("sound_effects", [])):
        anchor = item.get("anchor_event_id")
        if anchor not in events:
            raise ValueError(f"音效事件引用了不存在的画面事件：{anchor}")
        offset = item.get("offset_frames", 0)
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise ValueError(f"音效事件 {index} 的相对偏移无效")
        trigger = int(events[anchor]["range"][0]) + offset
        event_duration = int(item.get("duration_frames", 0))
        if trigger < 0 or event_duration <= 0 or trigger + event_duration > duration:
            raise ValueError(f"音效事件 {index} 超出视频时间轴")
        asset_uri = item.get("asset_uri")
        path = None
        if isinstance(asset_uri, str) and asset_uri:
            path = Path(asset_uri)
            if asset_root is not None and not path.is_absolute():
                path = (asset_root / path).resolve()
        resolved.append({
            **deepcopy(item),
            "trigger_frame": trigger,
            "trigger_seconds": trigger / fps,
            "duration_seconds": event_duration / fps,
            "path": str(path) if path is not None else None,
            "fade_in_seconds": int(item.get("fade_in_frames", 0)) / fps,
            "fade_out_seconds": int(item.get("fade_out_frames", 0)) / fps,
        })
    return resolved


def main() -> None:
    import argparse
    import json
    from .media import atomic_json
    from .mixer import render_sound_master
    parser = argparse.ArgumentParser(description='事件绑定音效预设与独立音频后期')
    parser.add_argument('--catalog', action='store_true')
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--visual', type=Path)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.catalog:
        print(json.dumps(SOUND_PRESETS, ensure_ascii=False))
        return
    if not all((args.plan, args.visual, args.source, args.output)):
        parser.error('需要plan、visual、source和output')
    plan = json.loads(args.plan.read_text('utf-8'))
    visual = json.loads(args.visual.read_text('utf-8'))
    assets = {}
    for sound in plan['sound_effects']:
        preset = sound['preset']
        if preset not in assets:
            assets[preset] = synthesize_preset(preset, args.output.parent / 'sfx-assets' / (preset + '.wav'))
        sound['asset_uri'] = assets[preset]['uri']
        sound['asset_sha256'] = assets[preset]['sha256']
    resolved = resolve_sound_effects(plan, visual)
    atomic_json(args.output.parent / 'audio-plan-materialized.json', plan)
    manifest = render_sound_master(args.source, args.output, resolved)
    manifest['preset_assets'] = assets
    atomic_json(args.output.parent / 'sound-master-manifest.json', manifest)
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == '__main__':
    main()
