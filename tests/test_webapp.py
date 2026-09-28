import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from gqai_music import webapp


class MusicWebAppTests(unittest.TestCase):
    def test_handoff_secret_is_not_exposed_and_master_returns_upstream(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(webapp, "RUNTIME_ROOT", Path(directory)):
            client = TestClient(webapp.app)
            video_media = {"duration_seconds": 12.0, "size_bytes": 5, "has_video": True,
                           "has_audio": True, "width": 1080, "height": 1920, "fps": 30}
            with patch.object(webapp, "probe", return_value=video_media):
                created = client.post(
                    "/api/projects/raw?filename=final.mp4&source_system=gqai-roughcut&"
                    f"source_job_id={'c' * 32}&return_url=http://127.0.0.1:8765&return_token=secret-token",
                    content=b"video", headers={"Content-Type": "application/octet-stream"})
            self.assertEqual(created.status_code, 200, created.text)
            self.assertNotIn("return_token", created.json())
            project_id = created.json()["project_id"]
            project = Path(directory) / "projects" / project_id
            value = json.loads((project / "project.json").read_text("utf-8"))
            (project / "music").mkdir()
            (project / "music" / "track.mp3").write_bytes(b"music")
            value["music"] = {"filename": "track.mp3", "stored_name": "music/track.mp3",
                              "sha256": "b" * 64, "media": {"duration_seconds": 20}}
            (project / "project.json").write_text(json.dumps(value), encoding="utf-8")

            def fake_render(source, music, output, mix):
                output.parent.mkdir(parents=True)
                output.write_bytes(b"master")
                return {"schema": "gqai-music-manifest-1.0",
                        "source": {"sha256": value["source"]["sha256"]},
                        "music": {"sha256": "b" * 64}, "mix": mix,
                        "output": {"sha256": "d" * 64}}

            class FakeResponse:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {"video_url": "http://127.0.0.1:8765/master.mp4",
                            "publish_url": "http://127.0.0.1:8765/publish"}

            class FakeClient:
                def __init__(self, **kwargs):
                    pass

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def post(self, url, *, params, headers, content):
                    self.__class__.observed = (url, params, b"".join(content))
                    return FakeResponse()

            with patch.object(webapp, "render_music_master", side_effect=fake_render), \
                 patch.object(webapp.httpx, "Client", FakeClient):
                rendered = client.post(f"/api/projects/{project_id}/render", json={})
            self.assertEqual(rendered.status_code, 200, rendered.text)
            self.assertEqual(rendered.json()["latest_render"]["return_sync"]["status"], "returned")
            self.assertEqual(FakeClient.observed[0],
                             f"http://127.0.0.1:8765/api/jobs/{'c' * 32}/music-master/raw")
            self.assertEqual(FakeClient.observed[1]["token"], "secret-token")
            self.assertEqual(FakeClient.observed[2], b"master")
            saved = json.loads((project / "project.json").read_text("utf-8"))
            self.assertNotIn("return_token", saved)

    def test_handoff_rejects_non_loopback_return_url(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(webapp, "RUNTIME_ROOT", Path(directory)), \
             patch.object(webapp, "probe", return_value={"duration_seconds": 1, "has_video": True}):
            response = TestClient(webapp.app).post(
                "/api/projects/raw?filename=final.mp4&source_system=gqai-roughcut&"
                f"source_job_id={'c' * 32}&return_url=https://example.com&return_token=secret",
                content=b"video", headers={"Content-Type": "application/octet-stream"})
            self.assertEqual(response.status_code, 400)

    def test_creates_independent_project_and_uploads_music(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(webapp, "RUNTIME_ROOT", Path(directory)):
            client = TestClient(webapp.app)
            video_media = {"duration_seconds": 12.0, "size_bytes": 5, "has_video": True,
                           "has_audio": True, "width": 1080, "height": 1920, "fps": 30}
            music_media = {"duration_seconds": 30.0, "size_bytes": 5,
                           "has_video": False, "has_audio": True}
            with patch.object(webapp, "probe", side_effect=[video_media, music_media]):
                created = client.post("/api/projects/raw?filename=final.mp4&source_system=gqai-roughcut",
                                      content=b"video", headers={"Content-Type": "application/octet-stream"})
                self.assertEqual(created.status_code, 200, created.text)
                project_id = created.json()["project_id"]
                uploaded = client.post(f"/api/projects/{project_id}/music/raw?filename=bed.mp3",
                                       content=b"music", headers={"Content-Type": "application/octet-stream"})
            self.assertEqual(uploaded.status_code, 200, uploaded.text)
            self.assertEqual(uploaded.json()["music"]["filename"], "bed.mp3")
            project = json.loads((Path(directory) / "projects" / project_id / "project.json").read_text("utf-8"))
            self.assertEqual(project["source_system"], "gqai-roughcut")
            self.assertEqual(project["schema"], "gqai-music-project-1.0")

    def test_render_persists_versioned_master_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(webapp, "RUNTIME_ROOT", Path(directory)):
            project_id = "a" * 32
            project = Path(directory) / "projects" / project_id
            (project / "music").mkdir(parents=True)
            (project / "source.mp4").write_bytes(b"video")
            (project / "music" / "track.mp3").write_bytes(b"music")
            (project / "project.json").write_text(json.dumps({
                "schema": "gqai-music-project-1.0", "project_id": project_id, "created_at": 1,
                "source_system": "manual", "source_job_id": "",
                "source": {"filename": "source.mp4", "stored_name": "source.mp4", "sha256": "a" * 64,
                           "media": {"duration_seconds": 10, "has_video": True, "has_audio": True}},
                "music": {"filename": "track.mp3", "stored_name": "music/track.mp3", "sha256": "b" * 64,
                          "media": {"duration_seconds": 20, "has_audio": True}},
                "mix": {},
            }), encoding="utf-8")

            def fake_render(source, music, output, mix):
                output.parent.mkdir(parents=True)
                output.write_bytes(b"master")
                return {"schema": "gqai-music-manifest-1.0",
                        "source": {"sha256": "a" * 64}, "music": {"sha256": "b" * 64},
                        "mix": mix, "output": {"sha256": "c" * 64}}

            with patch.object(webapp, "render_music_master", side_effect=fake_render):
                response = TestClient(webapp.app).post(f"/api/projects/{project_id}/render", json={
                    "volume_percent": 20, "video_start_seconds": 0, "music_in_seconds": 0,
                    "loop": True, "fade_in_seconds": 1, "fade_out_seconds": 2,
                    "voice_ducking": True,
                })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("master_url", response.json())
            self.assertTrue((project / response.json()["latest_render"]["video"]).is_file())
            self.assertTrue((project / response.json()["latest_render"]["manifest"]).is_file())


if __name__ == "__main__":
    unittest.main()

