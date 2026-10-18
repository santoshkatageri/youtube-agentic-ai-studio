"""Script preservation, scene safety and actual local motion rendering."""
import copy
import io
import json

import numpy as np
import pytest
from PIL import Image

import config
import gui
from video.motion import build_clip, script_to_scenes, timeline


@pytest.fixture
def studio(tmp_path, monkeypatch):
    for key, value in copy.deepcopy(gui._state).items():
        monkeypatch.setitem(gui._state, key, value)
    gui._state['running'] = False
    monkeypatch.setattr(config, 'OUTPUT_DIR', str(tmp_path))
    monkeypatch.setattr(config, 'IMAGES_DIR', str(tmp_path / 'images'))
    return gui.app.test_client(), tmp_path


def test_script_is_not_rewritten_and_paragraphs_form_scenes():
    text = 'Start with the logs. Keep the evidence.\n\nThen compare the deployment.'
    script = script_to_scenes(text, 'Evidence', 'normal')
    assert ' '.join(s['narration'] for s in script['sections']) == ' '.join(text.split())
    assert len(script['sections']) == 2
    assert script['sections'][-1]['layout'] == 'closing'
    assert script['video_type'] == 'normal'
    long = ' '.join(f'word{i}' for i in range(90))
    assert ' '.join(s['narration'] for s in script_to_scenes(long)['sections']) == long


def test_word_timestamps_determine_phrase_and_scene_cuts():
    sections = [{'narration': 'Read the logs before changing anything.'}, {'narration': 'Then verify.'}]
    words = 'Read the logs before changing anything. Then verify.'.split()
    events = [dict(word=w, start_ms=1000*i, end_ms=1000*i+500) for i, w in enumerate(words)]
    scenes, mode = timeline(sections, [], 9, events)
    assert mode == 'speech timestamps'
    assert scenes[0]['phrases'][1]['start'] == 5
    assert scenes[1]['start'] == 6
    assert scenes[-1]['end'] == 9
    # Edited narration must never reuse timings for the previous transcript.
    scenes, mode = timeline([{'narration': 'A different draft.'}], [], 9, events)
    assert mode == 'estimated' and scenes[0]['end'] == 9


def test_caption_fallback_and_missing_timing_cover_full_audio():
    sections = [{'narration': 'Read logs.'}, {'narration': 'Verify service.'}]
    cues = [{'text': 'Read logs.', 'start': 0, 'end': 1}, {'text': 'Verify service.', 'start': 4, 'end': 5}]
    scenes, mode = timeline(sections, cues, 6)
    assert mode == 'caption interpolation' and scenes[1]['start'] == 4
    scenes, mode = timeline(sections, [], 6)
    assert mode == 'estimated' and scenes[0]['start'] == 0 and scenes[-1]['end'] == 6
    assert scenes[0]['end'] == scenes[1]['start']


def test_draft_preview_edits_and_server_owned_assets(studio):
    client, folder = studio
    assert client.post('/api/script-draft', json={'script': 'Observe the failure.\n\nVerify recovery.', 'video_type': 'normal'}).json['ok']
    response = client.get('/api/scenes').json
    assert response['visual_style'] == 'motion' and response['video_type'] == 'normal'
    assert client.get('/api/scenes/1/preview').mimetype == 'image/png'
    rows = response['sections']
    rows[0].update(layout='diagram', diagram_nodes='Observe\nCompare\nVerify', icon='terminal', emphasis='failure', visual_asset='/etc/passwd')
    assert client.post('/api/scenes', json={'sections': rows}).json['ok']
    saved = json.loads((folder / 'script.json').read_text())
    assert saved['video_type'] == 'normal'
    assert saved['sections'][0]['diagram_nodes'] == 'Observe\nCompare\nVerify'
    assert 'visual_asset' not in saved['sections'][0]
    assert client.get('/api/scenes/1/preview').status_code == 200
    rows[0]['layout'] = 'unsupported'
    assert client.post('/api/scenes', json={'sections': rows}).status_code == 400
    rows[0].update(layout='diagram', diagram_nodes='Only one')
    assert client.post('/api/scenes', json={'sections': rows}).status_code == 400


def test_screenshot_validates_before_voice_and_busy_guard(studio):
    client, folder = studio
    client.post('/api/script-draft', json={'script': 'Inspect this screenshot.'})
    rows = client.get('/api/scenes').json['sections']
    rows[0]['layout'] = 'screenshot'
    client.post('/api/scenes', json={'sections': rows})
    response = client.post('/api/run', json={'steps': ['narration', 'video']})
    assert response.status_code == 400 and 'PNG/JPG' in response.json['error']
    image = io.BytesIO()
    Image.new('RGB', (200, 100), 'orange').save(image, 'PNG')
    image.seek(0)
    assert client.post('/api/scenes/1/asset', data={'file': (image, 'screen.png')}).json['ok']
    assert client.get('/api/scenes/1/preview').status_code == 200
    gui._state['running'] = True
    assert client.post('/api/script-draft', json={'script': 'Do not interrupt.'}).status_code == 409
    assert client.get('/api/scenes/1/preview').status_code == 409


def test_invalid_script_is_rejected_without_saving(studio):
    client, folder = studio
    for text in (' ', None, 123, 'x'*20001):
        assert client.post('/api/script-draft', json={'script': text}).status_code == 400
    assert not (folder / 'script.json').exists()


@pytest.mark.parametrize('size', [(360, 640), (640, 360)])
def test_rendered_frames_animate_all_layouts_without_network(size, tmp_path):
    script = script_to_scenes('One useful lesson.\n\nInspect the screenshot.\n\nCode build deploy.\n\nVerify the result.')
    image = tmp_path / 'screen.png'
    Image.new('RGB', (480, 270), 'orange').save(image)
    script['sections'][1].update(layout='screenshot', visual_asset=str(image))
    script['sections'][2].update(layout='diagram', diagram_nodes='Code\nBuild\nDeploy', icon='terminal')
    clip = build_clip(script, [], 12, size)
    try:
        assert clip.get_frame(.5).shape == (size[1], size[0], 3)
        assert not np.array_equal(clip.get_frame(.03), clip.get_frame(.5))
        for t in (3.8, 6.8, 8.8, 11.8):
            assert clip.get_frame(t).dtype == np.uint8
        assert not np.array_equal(clip.get_frame(6.8), clip.get_frame(8.8))
    finally:
        clip.close()


def test_motion_pipeline_skips_stock_and_research(studio, monkeypatch):
    from video import stock, narrator, creator
    from agents import researcher, scriptwriter
    client, folder = studio
    client.post('/api/script-draft', json={'script': 'Start with evidence.\n\nThen verify.'})
    def forbidden(*args, **kwargs):
        pytest.fail('Script-first motion must not rewrite narration or fetch stock')
    monkeypatch.setattr(stock, 'download_images', forbidden)
    monkeypatch.setattr(researcher, 'research_topic', forbidden)
    monkeypatch.setattr(scriptwriter, 'write_script', forbidden)
    audio = folder / 'narration.mp3'
    audio.write_bytes(b'test')
    def voice(script, output_dir):
        assert script['sections'][0]['narration'] == 'Start with evidence.'
        return str(audio)
    monkeypatch.setattr(narrator, 'generate_narration', voice)
    video = folder / 'final_short.mp4'
    video.write_bytes(b'test')
    monkeypatch.setattr(creator, 'create_video', lambda *a, **kw: str(video))
    monkeypatch.setattr(gui, '_make_thumbnail', lambda *a: None)
    monkeypatch.setattr(gui, '_archive_project', lambda: None)
    gui._run_pipeline(['narration', 'video'], video_type='shorts')
    assert gui._state['error'] is None and gui._state['video_path'] == str(video)


def test_narrator_requests_word_boundaries_and_keeps_supplied_instructions(tmp_path, monkeypatch):
    import asyncio
    from video import narrator
    received = {}
    class Speech:
        def __init__(self, text, voice, *, rate, pitch, boundary='SentenceBoundary'):
            received.update(text=text, boundary=boundary)
        async def stream(self):
            yield {'type': 'audio', 'data': b'audio'}
            for i, word in enumerate(received['text'].split()):
                yield {'type': 'WordBoundary', 'text': word, 'offset': i*10000000, 'duration': 5000000}
    monkeypatch.setattr(narrator.edge_tts, 'Communicate', Speech)
    monkeypatch.setattr(narrator, '_get_audio_duration_ms', lambda path: 6000)
    script = script_to_scenes('Start with evidence.\n\nOpen with the logs.')
    narrator.generate_narration(script, str(tmp_path))
    assert received['boundary'] == 'WordBoundary'
    assert received['text'] == 'Start with evidence. Open with the logs.'
    events = json.loads((tmp_path / 'narration.words.json').read_text())
    assert [e['word'] for e in events] == received['text'].split()
