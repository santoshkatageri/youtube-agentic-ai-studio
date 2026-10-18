"""Map scene boundaries onto the narration captions without extra API calls."""
import re


def _words(text):
    return re.findall(r"\w+", text.casefold())


def scene_timings(sections, captions, duration):
    """Use caption timing when the transcript matches, otherwise estimate by text length.

    A boundary inside a caption is interpolated, so timing is approximate at
    sub-caption resolution. Old scripts without timing metadata remain usable.
    """
    if not sections:
        return []
    tokens, starts = [], []
    for cue in captions:
        words = _words(cue["text"])
        for i, word in enumerate(words):
            tokens.append(word)
            starts.append(cue["start"] + (cue["end"] - cue["start"]) * i / len(words))
    lengths = [len(_words(s.get("narration", ""))) for s in sections]
    transcript = [w for s in sections for w in _words(s.get("narration", ""))]
    boundaries = [0.0]
    if transcript and tokens == transcript:
        cursor = 0
        for count in lengths[:-1]:
            cursor += count
            boundaries.append(min(duration, starts[cursor]) if cursor < len(starts) else duration)
    else:
        weights = [max(1, len(s.get("narration", ""))) for s in sections]
        cursor = 0
        for weight in weights[:-1]:
            cursor += weight
            boundaries.append(duration * cursor / sum(weights))
    boundaries.append(duration)
    return [(boundaries[i], boundaries[i + 1]) for i in range(len(sections))]
