"""Script-first motion explainers, rendered with the existing Pillow/MoviePy stack."""
import json
import math
import re
from bisect import bisect_right
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from video.scenes import scene_timings

LAYOUTS = ("text", "screenshot", "diagram", "closing")
ICONS = ("none", "terminal", "cloud", "check")


def script_to_scenes(text, title="", video_type="shorts"):
    """Keep the supplied words; paragraphs and sentences provide natural cuts."""
    if not isinstance(text, str) or not text.strip() or len(text) > 20000:
        raise ValueError("Paste a voiceover script between 1 and 20,000 characters.")
    if video_type not in ("shorts", "normal"):
        raise ValueError("Choose portrait or landscape.")
    chunks = []
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        words = paragraph.split()
        if len(words) <= 35:
            chunks.append(" ".join(words))
        else:
            for sentence in re.split(r"(?<=[.!?।])\s+", paragraph.strip()):
                words = sentence.split()
                chunks.extend(" ".join(words[i:i + 28]) for i in range(0, len(words), 28))
    if len(chunks) > 80:
        raise ValueError("Use up to 80 scenes per video; combine short paragraphs.")
    sections = []
    for i, narration in enumerate(chunks, 1):
        emphasis = list(dict.fromkeys(re.findall(r"\b[\w-]{7,}\b", narration)))[:3]
        sections.append(dict(id=i, title=" ".join(narration.split()[:6]), narration=narration,
                             layout="closing" if i == len(chunks) and i > 1 else "text",
                             emphasis=", ".join(emphasis), icon="none", diagram_nodes="",
                             visual_brief="", image_query=""))
    return dict(title=title.strip()[:160] or " ".join(text.split()[:8]), video_type=video_type,
                visual_style="motion", sections=sections, tags=[])


def validate_motion(section):
    if section.get("layout", "text") not in LAYOUTS:
        raise ValueError("Choose a supported motion layout.")
    if section.get("icon", "none") not in ICONS:
        raise ValueError("Choose a supported icon.")
    for key, limit in (("title", 160), ("emphasis", 200), ("diagram_nodes", 240)):
        if not isinstance(section.get(key, ""), str) or len(section.get(key, "")) > limit:
            raise ValueError(f"{key.replace('_', ' ').capitalize()} is too long or invalid.")
    if section.get("layout") == "diagram":
        nodes = [n.strip() for n in section.get("diagram_nodes", "").split("\n") if n.strip()]
        if not 2 <= len(nodes) <= 4 or any(len(n) > 48 for n in nodes):
            raise ValueError("A diagram needs 2–4 nodes, one per line, up to 48 characters each.")


def timeline(sections, captions, duration, events=None):
    """Use matching word events, then caption interpolation, then length estimates."""
    def tokens(text):
        return re.findall(r"\w+", text.casefold())
    expected = [w for s in sections for w in tokens(s["narration"])]
    cues = []
    mode = "estimated"
    if events:
        try:
            cues = [dict(text=e["word"], start=float(e["start_ms"]) / 1000,
                         end=float(e["end_ms"]) / 1000) for e in events]
            valid = all(math.isfinite(c["start"]) and math.isfinite(c["end"]) and
                        0 <= c["start"] < c["end"] <= duration + .5 for c in cues)
            valid = valid and all(a["start"] <= b["start"] for a, b in zip(cues, cues[1:]))
            if not valid or [w for c in cues for w in tokens(c["text"])] != expected:
                cues = []
            else:
                mode = "speech timestamps"
        except (KeyError, TypeError, ValueError):
            cues = []
    if not cues and [w for c in captions for w in tokens(c["text"])] == expected:
        cues = captions
        mode = "caption interpolation"
    ranges = scene_timings(sections, cues, duration)
    # Expand to token starts, preserving each scene's original spelling/punctuation.
    starts = []
    for cue in cues:
        words = tokens(cue["text"])
        starts.extend(cue["start"] + (cue["end"] - cue["start"]) * i / len(words)
                      for i in range(len(words)))
    cursor, result = 0, []
    for section, (begin, end) in zip(sections, ranges):
        words = section["narration"].split()
        word_starts = []
        weight = sum(max(1, len(w)) for w in words)
        consumed = 0
        for word in words:
            word_starts.append(starts[cursor] if cursor < len(starts) else
                               begin + (end - begin) * consumed / weight)
            cursor += len(tokens(word))
            consumed += max(1, len(word))
        phrases, first = [], 0
        for i, word in enumerate(words):
            if i-first < 4 and not re.search(r"[.!?;,:।]$", word) and i < len(words)-1:
                continue
            stop = i+1
            phrases.append(dict(text=" ".join(words[first:stop]), start=word_starts[first],
                                end=word_starts[stop] if stop < len(words) else end))
            first = stop
        result.append(dict(section=section, start=begin, end=end, phrases=phrases,
                           words=[dict(text=w, start=t) for w, t in zip(words, word_starts)]))
    return result, mode


def _font(size, bold=False):
    import config
    for path in config.FONT_PATHS["bold" if bold else "regular"]:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def _text_layer(text, width, height, size, emphasis="", color="#17252e"):
    """Fit complete words into the allotted area; highlight selected terms."""
    accent = set(re.findall(r"\w+", emphasis.casefold()))
    for pts in range(size, 9, -2):
        font = _font(pts, True)
        lines, line = [], []
        for word in text.split():
            if line and font.getlength(" ".join(line + [word])) > width - 24:
                lines.append(line)
                line = []
            line.append(word)
        if line:
            lines.append(line)
        if len(lines) * (pts + 14) <= height and all(font.getlength(" ".join(l)) <= width - 24 for l in lines):
            break
    layer = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(layer)
    y = max(0, (height - len(lines) * (pts + 14)) // 2)
    for line in lines:
        x = (width - font.getlength(" ".join(line))) / 2
        for word in line:
            w = font.getlength(word)
            if accent.intersection(re.findall(r"\w+", word.casefold())):
                draw.rounded_rectangle((x - 3, y, x + w + 4, y + pts + 10), radius=5, fill="#62ebce")
            highlighted = accent.intersection(re.findall(r"\w+", word.casefold()))
            draw.text((x, y), word, font=font, fill="#17252e" if highlighted else color)
            x += w + font.getlength(" ")
        y += pts + 14
    return layer


def _icon(draw, name, x, y, size):
    color = "#078d83"
    if name == "terminal":
        draw.rounded_rectangle((x, y, x + size, y + size * .7), radius=7, outline=color, width=3)
        draw.line((x + size*.18, y + size*.2, x + size*.34, y + size*.35,
                   x + size*.18, y + size*.5), fill=color, width=3)
        draw.line((x + size*.48, y + size*.5, x + size*.75, y + size*.5), fill=color, width=3)
    elif name == "check":
        draw.line((x, y + size*.35, x + size*.32, y + size*.65, x + size, y), fill=color, width=5)
    elif name == "cloud":
        draw.ellipse((x + size*.2, y, x + size*.8, y + size*.6), fill=color)
        draw.rounded_rectangle((x, y + size*.25, x + size, y + size*.7), radius=int(size*.2), fill=color)


def build_clip(script, captions, duration, size, events=None):
    """Cache static layers; animate their placement and reveal at narration boundaries."""
    from moviepy.editor import VideoClip
    import config
    w, h = size
    portrait = h > w
    scale = w / (720 if portrait else 1280)
    margin = int(w * .07)
    scenes, mode = timeline(script["sections"], captions, duration, events)
    print(f"   → Motion explainer timing: {mode}. Review the final preview for sync.")
    @lru_cache(maxsize=2)
    def prepare(index):
        scene = scenes[index]
        section = scene["section"]
        validate_motion(section)
        layout = section.get("layout", "text")
        closing = layout == "closing"
        base = Image.new("RGB", (w, h), "#152d35" if closing else "#f5f7f7")
        draw = ImageDraw.Draw(base)
        step = max(18, int(36 * scale))
        for x in range(0, w, step):
            draw.line((x, 0, x, h), fill="#1c3941" if closing else "#e9eeee")
        for y in range(0, h, step):
            draw.line((0, y, w, y), fill="#1c3941" if closing else "#e9eeee")
        draw.rectangle((0, 0, w, max(4, int(7*scale))), fill="#0da899")
        title = _text_layer(section.get("title", ""), w - 2*margin, int(h*.13), int(30*scale), color="#ffffff" if closing else "#17252e")
        base.paste(title, (margin, int(h*.08)), title)
        if getattr(config, "SHOW_CHANNEL_BRAND", True):
            draw.text((margin, int(h*.92)), config.CHANNEL_NAME, font=_font(max(10, int(18*scale))), fill="#9ebdbf" if closing else "#55676d")
        draw.text((w - margin - int(70*scale), int(h*.92)), f'{section["id"]:02d}', font=_font(max(10, int(18*scale))), fill="#9ebdbf" if closing else "#55676d")
        text_width = w - 2*margin if portrait or layout in ("text", "closing") else int(w*.44)
        text_height = int(h*(.48 if layout in ("text", "closing") else .25 if portrait else .5))
        text_x = margin
        text_y = int(h*(.27 if layout in ("text", "closing") else .23))
        phrases = [(p, _text_layer(p["text"], text_width, text_height,
                                  int((76 if portrait else 72)*scale), section.get("emphasis", ""),
                                  color="#ffffff" if closing else "#17252e"))
                   for p in scene["phrases"]]
        assets = []
        if layout == "screenshot":
            path = section.get("visual_asset", "")
            if Path(path).suffix.lower() not in (".png", ".jpg", ".jpeg") or not Path(path).is_file():
                raise ValueError(f'Scene {section["id"]}: attach a PNG/JPG for the screenshot layout.')
            box = (w - 2*margin, int(h*.32)) if portrait else (int(w*.42), int(h*.53))
            with Image.open(path) as source:
                content = ImageOps.contain(source.convert("RGB"), (box[0]-16, box[1]-16))
                card = ImageOps.expand(content, border=8, fill="white")
            assets.append((card, (margin + (box[0]-card.width)//2 if portrait else int(w*.53),
                                  int(h*.55) if portrait else int(h*.27)), scene["start"]))
        elif layout == "diagram":
            labels = [n.strip() for n in section["diagram_nodes"].splitlines() if n.strip()]
            cw = w-2*margin if portrait else int(w*.39)
            ch = int(h*(.065 if portrait else .1))
            x = margin if portrait else int(w*.54)
            y = int(h*.52) if portrait else int(h*.25)
            for i, label in enumerate(labels):
                card = Image.new("RGB", (cw, ch), "#dcf5ee")
                cd = ImageDraw.Draw(card)
                cd.rounded_rectangle((1, 1, cw-2, ch-2), radius=10, outline="#0da899", width=2)
                words = _text_layer(label, cw-12, ch, int(25*scale))
                card.paste(words, (6, 0), words)
                gap = int(h*.025)
                label_words = re.findall(r"\w+", label.casefold())
                spoken = [(token, word["start"]) for word in scene["words"]
                          for token in re.findall(r"\w+", word["text"].casefold())]
                matching = next((spoken[j][1] for j in range(len(spoken))
                                 if label_words and [word for word, _ in spoken[j:j+len(label_words)]] == label_words), None)
                at = matching if matching is not None else scene["start"] + i * (scene["end"] - scene["start"]) / len(labels)
                if i:
                    at = max(at, assets[-1][2])
                assets.append((card, (x, y+i*(ch+gap)), at))
                if i:
                    connector = Image.new("RGBA", (cw, gap))
                    dc = ImageDraw.Draw(connector)
                    dc.line((cw//2, 0, cw//2, gap), fill="#0da899", width=3)
                    dc.polygon([(cw//2-5, gap-6), (cw//2+5, gap-6), (cw//2, gap)], fill="#0da899")
                    assets.append((connector, (x, y+i*(ch+gap)-gap), at))
        _icon(draw, section.get("icon", "none"), margin, int(h*.88), int(44*scale))
        return scene, base, phrases, assets, (text_x, text_y)
    boundaries = [s["start"] for s in scenes]

    def frame(t):
        scene, base, phrases, assets, position = prepare(min(len(scenes)-1, max(0, bisect_right(boundaries, t)-1)))
        result = base.copy()
        for card, (x, y), at in assets:
            if t < at:
                continue
            progress = min(1, (t-at)/.35)
            zoom = .97 + .03 * (1-(1-progress)**3)
            card_frame = card.resize((max(1, int(card.width*zoom)), max(1, int(card.height*zoom))))
            dest = (x+(card.width-card_frame.width)//2, y+int((1-progress)*22*scale))
            result.paste(card_frame, dest, card_frame if card_frame.mode == "RGBA" else None)
        active = next(((p, layer) for p, layer in reversed(phrases) if p["start"] <= t), None)
        if active:
            p, layer = active
            progress = min(1, max(0, (t-p["start"])/.22))
            overlay = layer.copy()
            overlay.putalpha(layer.getchannel("A").point(lambda a: int(a*progress)))
            result.paste(overlay, (position[0], position[1]+int((1-progress)*30*scale)), overlay)
        return np.asarray(result)
    return VideoClip(frame, duration=duration)


def create_motion_video(script, audio_path, output_dir, video_type):
    from moviepy.editor import AudioFileClip
    import config
    from video.creator import _parse_srt, _apply_watermark
    short = video_type == "shorts"
    size = (config.SHORTS_WIDTH, config.SHORTS_HEIGHT) if short else (config.VIDEO_WIDTH, config.VIDEO_HEIGHT)
    fps = config.SHORTS_FPS if short else config.VIDEO_FPS
    folder = Path(output_dir)
    captions = _parse_srt(str(folder / "narration.srt"))
    try:
        events = json.loads((folder / "narration.words.json").read_text())
    except (OSError, ValueError):
        events = None
    path = str(folder / ("final_short.mp4" if short else "final_video.mp4"))
    audio = AudioFileClip(audio_path)
    clip = None
    try:
        clip = build_clip(script, captions, audio.duration, size, events)
        clip = _apply_watermark(clip.set_audio(audio), *size)
        clip.write_videofile(path, fps=fps, codec="libx264", audio_codec="aac", threads=4,
                             preset=getattr(config, "RENDER_PRESET", "ultrafast"), logger=None)
    finally:
        if clip is not None:
            clip.close()
        audio.close()
    return path
