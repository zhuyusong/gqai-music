import unittest

from gqai_music.audio_plan import resolve_sound_effects, separate_audio_plan


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
