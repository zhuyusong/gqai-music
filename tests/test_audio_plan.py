import unittest
from pathlib import Path
import tempfile
import wave
import struct

from gqai_music.audio_plan import resolve_sound_effects, separate_audio_plan, SOUND_PRESETS, synthesize_preset


def legacy_table():
    return {
        "schema": "gqai-execution-table-1.0",
        "project": {"width": 1080, "height": 1920, "fps": 30,
                    "duration_frames": 300,
                    "frame_contract": "zero-based half-open [start, end)"},
        "assets": {},
        "effects": [{"event_id": "title-001", "effect_id": "REFERENCE_TEXT@1",
                     "range": [30, 90], "params": {"text": "重点"}}],
        "subtitles": [],
        "audio": {"sfx_events": [{"trigger_frame": 31, "duration_frames": 12,
                                    "volume": 0.7, "asset_uri": "sfx/pop.wav"}]},
    }


class AudioPlanTests(unittest.TestCase):
    def test_local_presets_are_deterministic_non_silent_pcm_with_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            for name, info in SOUND_PRESETS.items():
                first = synthesize_preset(name, Path(folder) / (name + '.wav'))
                second = synthesize_preset(name, Path(folder) / (name + '-copy.wav'))
                self.assertEqual(first['sha256'], second['sha256'])
                self.assertEqual(first['origin'], 'gqai-procedural-sfx-v1')
                with wave.open(first['uri']) as stream:
                    self.assertEqual(stream.getframerate(), 48000)
                    self.assertAlmostEqual(stream.getnframes() / 48000, info['duration_seconds'])
                    samples = struct.unpack('<' + 'h' * stream.getnframes(), stream.readframes(stream.getnframes()))
                    self.assertGreater(max(abs(v) for v in samples), 1000)
                    self.assertLessEqual(max(abs(v) for v in samples), 23000)
        with self.assertRaisesRegex(ValueError, '未知'):
            synthesize_preset('../bad', Path('unused.wav'))
    def test_sfx_is_extracted_and_bound_to_visual_event(self):
        visual, plan = separate_audio_plan(legacy_table())
        self.assertEqual(visual["audio"]["sfx_events"], [])
        self.assertEqual(visual["audio"]["external_plan"]["schema"],
                         "gqai-audio-plan-1.0")
        self.assertEqual(plan["sound_effects"][0]["anchor_event_id"], "title-001")
        self.assertEqual(plan["sound_effects"][0]["offset_frames"], 1)

    def test_sfx_follows_text_animation_when_visual_timing_moves(self):
        visual, plan = separate_audio_plan(legacy_table())
        visual["effects"][0]["range"] = [120, 180]
        resolved = resolve_sound_effects(plan, visual)
        self.assertEqual(resolved[0]["trigger_frame"], 121)
        self.assertAlmostEqual(resolved[0]["trigger_seconds"], 121 / 30)

    def test_dangling_visual_anchor_is_rejected(self):
        visual, plan = separate_audio_plan(legacy_table())
        plan["sound_effects"][0]["anchor_event_id"] = "deleted-title"
        with self.assertRaisesRegex(ValueError, "不存在的画面事件"):
            resolve_sound_effects(plan, visual)


if __name__ == "__main__":
    unittest.main()
