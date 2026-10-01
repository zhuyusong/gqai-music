from pathlib import Path
import tempfile
import subprocess
import shutil
import array
import unittest
from unittest.mock import patch

from gqai_music import mixer


class MixerTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg required')
    def test_many_presets_keep_last_cue_and_measure_stereo_without_downmix_gain(self):
        from gqai_music.audio_plan import synthesize_preset, SOUND_PRESETS
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-n', '-f', 'lavfi', '-i',
                'color=c=black:s=64x64:r=30:d=31.4', '-f', 'lavfi', '-i',
                'sine=frequency=300:sample_rate=48000:duration=31.4',
                '-af', 'volume=6,pan=stereo|c0=c0|c1=c0', '-c:v', 'libx264',
                '-c:a', 'aac', '-shortest', str(source)], check=True, capture_output=True)
            presets = ['pop', 'whoosh', 'impact', 'whoosh', 'click', 'chime', 'pop']
            triggers = [112/30, 320/30, 387/30, 494/30, 676/30, 731/30, 29.0]
            assets = {name: synthesize_preset(name, root / (name + '.wav')) for name in set(presets)}
            events = [{'path': assets[name]['uri'], 'asset_sha256': assets[name]['sha256'],
                'sound_id': f'cue-{i}', 'anchor_event_id': f'title-{i}', 'trigger_seconds': trigger,
                'duration_seconds': SOUND_PRESETS[name]['duration_seconds'], 'volume': .18}
                for i, (name, trigger) in enumerate(zip(presets, triggers))]
            result = mixer.render_sound_master(source, root / 'out.mp4', events)
            evidence = result['verification']
            self.assertTrue(evidence['passed'], evidence)
            self.assertEqual(evidence['decoded_channels'], 2)
            self.assertLess(evidence['decoded_audio_peak'], 1)
            self.assertEqual(len(evidence['cues']), 7)
            self.assertGreater(evidence['cues'][-1]['stem_rms'], .001)
            self.assertGreater(evidence['cues'][-1]['output_delta_rms'], .001)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg required')
    def test_real_sound_only_mix_changes_audio_at_anchor_and_copies_video(self):
        from gqai_music.audio_plan import synthesize_preset
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, output = root / 'source.mp4', root / 'sound.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-n', '-f', 'lavfi', '-i',
                'color=c=black:s=64x64:r=30:d=2', '-f', 'lavfi', '-i', 'sine=frequency=300:duration=2',
                '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(source)], check=True, capture_output=True)
            asset = synthesize_preset('chime', root / 'chime.wav')
            result = mixer.render_sound_master(source, output, [{'path': asset['uri'], 'asset_sha256': asset['sha256'],
                'sound_id': 'chime', 'anchor_event_id': 'title-001', 'trigger_seconds': .8,
                'duration_seconds': .55, 'volume': .28}])
            self.assertTrue(result['mix']['voice_ducking'])
            self.assertEqual(result['mix']['voice_gain'], 1)
            def decode(path):
                pcm = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-vn', '-ac', '1',
                    '-ar', '48000', '-f', 'f32le', '-'], check=True, capture_output=True).stdout
                values = array.array('f')
                values.frombytes(pcm)
                return values
            before, after = decode(source), decode(output)
            rms = lambda start, stop: (sum((a-b)**2 for a,b in zip(
                before[round(start*48000):round(stop*48000)], after[round(start*48000):round(stop*48000)])) /
                round((stop-start)*48000)) ** .5
            self.assertGreater(rms(.8, 1.3), .005)
            self.assertLess(rms(.1, .6), .005)
            self.assertLess(max(abs(v) for v in after), 1)
            def video_hash(path):
                return subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:v:0',
                    '-c', 'copy', '-f', 'hash', '-hash', 'sha256', '-'], check=True, capture_output=True).stdout
            self.assertEqual(video_hash(source), video_hash(output))
            with self.assertRaisesRegex(ValueError, '覆盖'):
                mixer.render_sound_master(source, output, [])

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
