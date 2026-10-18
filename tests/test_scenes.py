"""Offline regression checks for scene review and narration-aligned visuals."""
import copy
import io
import json
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

import config
import gui
from agents import scriptwriter
from video import creator, stock
from video.scenes import scene_timings


@pytest.fixture
def draft(tmp_path, monkeypatch):
    script = {"title": "A safe investigation", "video_type": "shorts", "sections": [
        {"id": 1, "title": "Investigate", "narration": "Read the logs.", "image_query": "server", "visual_brief": "A log recording"},
        {"id": 2, "title": "Verify", "narration": "Check service health.", "image_query": "monitoring"},
    ]}
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(config, "IMAGES_DIR", str(tmp_path / "images"))
    for key, value in {"script": script, "running": False, "research": {"topic": "test"},
                       "stages": dict(gui._state["stages"]), "library_id": "original", "video_path": "old.mp4"}.items():
        monkeypatch.setitem(gui._state, key, value)
    # Restore other state mutated by saves or pipeline calls.
    for key in ["video_type", "draft_ready", "thumb_path", "audio_path", "description", "yt_url", "error", "started_at", "stop_requested", "thumb_text", "thumb_bg_index"]:
        monkeypatch.setitem(gui._state, key, gui._state.get(key))
    return gui.app.test_client(), script, tmp_path


def test_timings_follow_matching_captions_instead_of_duration_estimates():
    sections = [{"narration": "Read logs", "duration_seconds": 99}, {"narration": "Then verify"}]
    captions = [{"start": 0, "end": 1, "text": "Read logs"}, {"start": 7, "end": 9, "text": "Then verify"}]
    assert scene_timings(sections, captions, 10) == [(0, 7), (7, 10)]


def test_boundary_inside_caption_and_legacy_fallback():
    sections = [{"narration": "One two"}, {"narration": "Three four"}]
    assert scene_timings(sections, [{"start": 0, "end": 8, "text": "One two three four"}], 10) == [(0, 4), (4, 10)]
    ranges = scene_timings(sections, [], 10)
    assert ranges[0][0] == 0 and ranges[-1][1] == 10
    assert ranges[0][1] == ranges[1][0]


def test_renderer_never_uses_another_scenes_images(monkeypatch):
    monkeypatch.setattr(creator, "W", 90)
    monkeypatch.setattr(creator, "H", 160)
    monkeypatch.setattr(config, "SHOW_CHANNEL_BRAND", False)
    monkeypatch.setattr(creator, "_apply_overlay", lambda arr, **kw: arr)
    monkeypatch.setattr(creator, "_apply_vignette", lambda arr: arr)
    red = np.full((160, 90, 3), (255, 0, 0), dtype=np.uint8)
    blue = np.full((160, 90, 3), (0, 0, 255), dtype=np.uint8)
    clip = creator._build_shorts_clip([
        {"start": 0, "end": 8, "images": [red]},
        {"start": 8, "end": 10, "images": [blue]},
    ], 10, [])
    try:
        assert np.array_equal(clip.get_frame(7), red)
        assert np.array_equal(clip.get_frame(8), blue)
    finally:
        clip.close()


def test_imported_clip_holds_last_frame(monkeypatch):
    monkeypatch.setattr(creator, "W", 90)
    monkeypatch.setattr(creator, "H", 160)
    monkeypatch.setattr(config, "SHOW_CHANNEL_BRAND", False)
    times = []
    source = SimpleNamespace(duration=2, fps=10, get_frame=lambda t: times.append(t) or np.zeros((160,90,3), dtype=np.uint8))
    clip = creator._build_shorts_clip([{"start": 0, "end": 8, "images": [], "video": source}], 8, [])
    clip.get_frame(6)
    assert times[-1] == pytest.approx(1.9)
    clip.close()


def test_save_detaches_archive_and_preserves_server_asset(draft):
    client, script, path = draft
    script["sections"][0]["visual_asset"] = "/original.png"
    rows = copy.deepcopy(script["sections"])
    rows[0].update(narration="Inspect errors first.", visual_asset="/untrusted.png")
    response = client.post("/api/scenes", json={"sections": rows})
    assert response.status_code == 200
    saved = json.loads((path / "script.json").read_text())
    assert saved["sections"][0]["visual_asset"] == "/original.png"
    assert saved["sections"][0]["narration"] == "Inspect errors first."
    assert gui._state["library_id"] is None and gui._state["video_path"] is None
    assert gui._state["draft_ready"]
    assert "visual_asset" not in response.json["sections"][0]


def test_invalid_scene_edit_is_not_written(draft):
    client, script, path = draft
    rows = copy.deepcopy(script["sections"])
    rows[0]["narration"] = " "
    assert client.post("/api/scenes", json={"sections": rows}).status_code == 400
    assert not (path / "script.json").exists()
    assert client.post("/api/scenes", json={"sections": rows[::-1]}).status_code == 400


def test_upload_preview_remove_and_busy_guard(draft):
    client, script, path = draft
    data = io.BytesIO()
    Image.new("RGB", (80, 40), "teal").save(data, format="PNG")
    data.seek(0)
    assert client.post("/api/scenes/1/asset", data={"file": (data, "diagram.png")}).status_code == 200
    asset = gui._state["script"]["sections"][0]["visual_asset"]
    assert client.get("/api/scenes/1/asset").status_code == 200
    assert client.delete("/api/scenes/1/asset").json["ok"]
    assert client.get("/api/scenes/1/asset").status_code == 404
    # Removing the reference must not destroy assets belonging to older drafts.
    from pathlib import Path
    assert Path(asset).exists()
    assert client.post("/api/scenes/1/asset", data={"file": (io.BytesIO(b"not image"), "bad.png")}).status_code == 400
    gui._state["running"] = True
    assert client.post("/api/scenes", json={"sections": script["sections"]}).status_code == 409
    assert client.post("/api/scenes/1/asset", data={"file": (io.BytesIO(b"x"), "x.png")}).status_code == 409


def test_custom_asset_skips_stock_requests(draft, monkeypatch):
    _, script, path = draft
    image = path / "diagram.png"
    Image.new("RGB", (20,20)).save(image)
    for section in script["sections"]:
        section["visual_asset"] = str(image)
    monkeypatch.setattr(stock, "_fetch_images", lambda *a, **kw: pytest.fail("No stock needed"))
    assert stock.download_images(script, str(path)) == {1: [], 2: []}


def test_draft_pipeline_stops_before_visual_or_voice_calls(draft, monkeypatch):
    _, script, path = draft
    from agents import researcher
    from video import narrator
    monkeypatch.setattr(researcher, "research_topic", lambda **kw: {"topic":"test", "video_title":"test"})
    monkeypatch.setattr(scriptwriter, "write_script", lambda *a, **kw: script)
    monkeypatch.setattr(stock, "download_images", lambda *a, **kw: pytest.fail("Draft must not fetch visuals"))
    monkeypatch.setattr(narrator, "generate_narration", lambda *a, **kw: pytest.fail("Draft must not synthesize voice"))
    gui._run_pipeline(["research", "script"], "test", "shorts")
    assert gui._state["draft_ready"] and not gui._state["running"]
    assert gui._state["error"] is None
    assert (path / "script.json").exists()


def test_script_prompt_requests_practical_scenes_without_invented_results(draft, monkeypatch):
    _, script, _ = draft
    script["sections"].append({"id":3,"narration":"A useful takeaway."})
    prompts = []
    monkeypatch.setattr(scriptwriter, "generate", lambda p: prompts.append(p) or json.dumps(script))
    result = scriptwriter.write_script({"topic":"agent permissions", "video_title":"Agent permissions", "key_points":[]}, "shorts")
    assert result["video_type"] == "shorts"
    assert "Never invent personal experience" in prompts[0]
    assert "Match each visual_brief" in prompts[0]
    assert "5 scenes" in prompts[0]
