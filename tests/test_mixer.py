from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from gqai_music import mixer


class MixerTests(unittest.TestCase):
    def test_validate_mix_rejects_fades_longer_than_playable_music(self):
        source = {"duration_seconds": 4}
        music = {"duration_seconds": 3}
        with self.assertRaisesRegex(ValueError, "淡入和淡出"):
            mixer.validate_mix({"fade_in_seconds": 2, "fade_out_seconds": 2,
                                "loop": False}, source, music)

    def test_render_uses_video_copy_and_voice_ducking(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, music, output = root / "source.mp4", root / "music.mp3", root / "out.mp4"
            source.write_bytes(b"video")
            music.write_bytes(b"music")
            source_media = {"duration_seconds": 10.0, "has_video": True, "has_audio": True}
            music_media = {"duration_seconds": 4.0, "has_video": False, "has_audio": True}
            output_media = {"duration_seconds": 10.0, "has_video": True, "has_audio": True}
            calls = []

            def fake_run(command, **kwargs):
                calls.append(command)
                Path(command[-1]).write_bytes(b"mixed")

            with patch.object(mixer, "probe", side_effect=[source_media, music_media, output_media]), \
                 patch.object(mixer.subprocess, "run", side_effect=fake_run):
                result = mixer.render_music_master(source, music, output, {
                    "volume_percent": 18, "video_start_seconds": 0,
                    "music_in_seconds": 0, "loop": True,
                    "fade_in_seconds": 1, "fade_out_seconds": 2,
                    "voice_ducking": True,
                })
            command = calls[0]
            self.assertIn("copy", command)
            self.assertIn("-stream_loop", command)
            self.assertIn("sidechaincompress", command[command.index("-filter_complex") + 1])
            self.assertTrue(output.is_file())
            self.assertEqual(result["mix"]["volume_percent"], 18)

    def test_render_mixes_event_sfx_after_music_ducking(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, music = root / "source.mp4", root / "music.mp3"
            sfx, output = root / "pop.wav", root / "out.mp4"
            source.write_bytes(b"video")
            music.write_bytes(b"music")
            sfx.write_bytes(b"pop")
            source_media = {"duration_seconds": 10.0, "has_video": True, "has_audio": True}
            music_media = {"duration_seconds": 4.0, "has_video": False, "has_audio": True}
            output_media = {"duration_seconds": 10.0, "has_video": True, "has_audio": True}
            calls = []

            def fake_run(command, **kwargs):
                calls.append(command)
                Path(command[-1]).write_bytes(b"mixed")

            with patch.object(mixer, "probe", side_effect=[source_media, music_media, output_media]), \
                 patch.object(mixer.subprocess, "run", side_effect=fake_run):
                result = mixer.render_music_master(source, music, output, {
                    "volume_percent": 18, "video_start_seconds": 0,
                    "music_in_seconds": 0, "loop": True,
                    "fade_in_seconds": 1, "fade_out_seconds": 2,
                    "voice_ducking": True,
                }, [{"sound_id": "pop", "anchor_event_id": "title-001",
                     "path": str(sfx), "trigger_seconds": 2.0,
                     "duration_seconds": .4, "volume": .7,
                     "fade_in_seconds": 0, "fade_out_seconds": .1}])
            command = calls[0]
            graph = command[command.index("-filter_complex") + 1]
            self.assertIn("[2:a]", graph)
            self.assertIn("adelay=2000|2000[sfx0]", graph)
            self.assertIn("[voice][bed][sfx0]amix=inputs=3", graph)
            self.assertEqual(result["sound_effects"][0]["anchor_event_id"], "title-001")


if __name__ == "__main__":
    unittest.main()
