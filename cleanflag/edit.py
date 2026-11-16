"""Decide and apply what happens to each flagged word: mute, close the gap, or splice.

settings.json:
  audio_mode:       "mute" | "close_gap" | "splice"
  splice_fallback:  what to do when no good donor word is found ("close_gap" or "mute")
  splice_min_match: 0..1, how closely the donor's voice must match (higher = stricter)
  splice_options:   flagged word -> real words the actors say elsewhere that can stand in
"""
import json
import re
from pathlib import Path

import numpy as np

DEFAULT_SETTINGS = {
    "audio_mode": "close_gap",
    "splice_fallback": "close_gap",
    "splice_min_match": 0.9,
    "splice_options": {
        "damn": ["blast", "curse"], "damned": ["cursed", "blasted"], "goddamn": ["blasted", "cursed"],
        "goddamned": ["blasted", "cursed"], "hell": ["blazes"],
        "fuck": ["blast", "curse"], "fucking": ["blasted", "cursed"], "fuckin": ["blasted", "cursed"],
        "fucked": ["finished", "done"],
        "shit": ["rubbish", "nonsense"], "bullshit": ["nonsense", "rubbish"], "shite": ["rubbish", "nonsense"],
        "bastard": ["scoundrel", "villain", "dog", "rat", "coward", "devil", "fool"],
        "bastards": ["scoundrels", "villains", "dogs", "rats", "cowards", "devils", "fools"],
        "bitch": ["wretch", "witch"], "prick": ["fool", "dog"], "cunt": ["wretch", "dog"],
        "ass": ["fool"], "arse": ["fool"], "asshole": ["fool", "scoundrel"], "arsehole": ["fool", "scoundrel"],
        "whore": ["wench"], "whores": ["wenches"], "strumpet": ["wench"],
    },
}

FILLER = {"oh", "ah", "ahh", "argh", "agh", "ugh", "it", "you", "them", "him", "her", "this", "that", "all",
          "me", "so", "well", "aye", "eh", "what", "no", "yes", "a", "the", "and", "i", "we", "us"}


def load_settings(here):
    s = dict(DEFAULT_SETTINGS)
    p = Path(here) / "settings.json"
    if p.exists():
        s.update(json.loads(p.read_text(encoding="utf-8")))
    return s


def norm(w):
    return re.sub(r"[^a-z']", "", w.lower()).strip("'")


def is_standalone(words, flagged_idx):
    """True if the line is basically just the swear (e.g. "Damn!", "Bloody hell!", "Damn it all!")."""
    rest = [norm(w) for i, (w, _, _) in enumerate(words) if i not in flagged_idx]
    rest = [r for r in rest if r]
    return len(words) <= 5 and all(r in FILLER for r in rest)


# ---------------- PCM operations ----------------

def _fade(n):
    return np.linspace(0.0, 1.0, max(n, 1), dtype="float32")[:, None]


def cut_ranges(pcm, ranges, xfade=0.012, sr=48000):
    """Remove each (start, end) and join the halves with a short crossfade. Length is kept by
    adding silence at the end. Returns the new pcm."""
    out = pcm.copy()
    for s, e in sorted(ranges, reverse=True):
        a, b = max(0, int(s * sr)), min(len(out), int(e * sr))
        if b <= a:
            continue
        x = int(xfade * sr)
        x = min(x, a, len(out) - b)
        head, tail = out[:a], out[b:]
        if x > 0:
            f = _fade(x)
            mid = head[-x:] * (1 - f) + tail[:x] * f
            joined = np.vstack([head[:-x], mid, tail[x:]])
        else:
            joined = np.vstack([head, tail])
        out = np.vstack([joined, np.zeros((len(pcm) - len(joined), pcm.shape[1]), "float32")])
    return out[:len(pcm)]


def splice_in(pcm, s, e, donor, xfade=0.012, sr=48000):
    """Replace (s, e) with `donor` audio. Returns new pcm or None if it won't fit the clip length."""
    a, b = max(0, int(s * sr)), min(len(pcm), int(e * sr))
    d = donor.astype("float32").copy()
    x = int(xfade * sr)
    if len(d) > 2 * x:
        f = _fade(x)
        d[:x] *= f
        d[-x:] *= f[::-1]
    new = np.vstack([pcm[:a], d, pcm[b:]])
    if len(new) > len(pcm):
        extra = new[len(pcm):]
        if extra.size and np.sqrt((extra ** 2).mean()) > 0.003:   # would cut off real speech
            return None
        new = new[:len(pcm)]
    else:
        new = np.vstack([new, np.zeros((len(pcm) - len(new), pcm.shape[1]), "float32")])
    return new


def trim_silence(seg, sr=48000, thresh=0.01):
    """Trim quiet edges of a donor word."""
    env = np.abs(seg).max(axis=1)
    idx = np.where(env > thresh)[0]
    if not len(idx):
        return seg
    a = max(0, idx[0] - int(0.01 * sr))
    b = min(len(seg), idx[-1] + int(0.02 * sr))
    return seg[a:b]


# ---------------- planning ----------------

class Library:
    """All English voice clips across the archives, addressable by 'archive:offset' key."""

    def __init__(self, archives):
        from .wem import find_clips
        self.blobs, self.clips, self.by_fp, self._pcm, self._vp = {}, {}, {}, {}, {}
        for arc in archives:
            src = arc.read_bytes()
            self.blobs[arc.name] = src
            for off, size, w in find_clips(src):
                self.clips[f"{arc.name}:{off}"] = (arc.name, off, size, w)

    def pcm(self, key):
        from .wem import decode_pcm48
        if key not in self._pcm:
            if len(self._pcm) > 400:
                self._pcm.clear()
            self._pcm[key] = decode_pcm48(self.clips[key][3])[0]
        return self._pcm[key]

    def voice(self, key):
        from .voice import voice_print
        if key not in self._vp:
            self._vp[key] = voice_print(self.pcm(key))
        return self._vp[key]

    def fingerprint(self, key):
        from .mutemap import fingerprint
        arc, off, size, _ = self.clips[key]
        return fingerprint(self.blobs[arc][off:off + size])


def plan_clip(key, words, units, partial, settings, lib, donors):
    """Decide what to do with one clip.

    words: [[word, start, end], ...] from the scan
    units: from WordFilter.flag_units - each is a word or phrase to remove/replace
    Returns (plan, actions); actions are (text, start, end, what_happened, caption) for the report.
    """
    from .voice import similarity
    from .wem import frames_for_spans
    mode = settings["audio_mode"]
    covered = {i for u in units for i in range(u["a"], u["b"] + 1)}
    standalone = is_standalone(words, covered)
    w = lib.clips[key][3]

    def text(u):
        return " ".join(words[i][0] for i in range(u["a"], u["b"] + 1))

    def span(u, swear_only=False):
        if swear_only:
            return min(words[i][1] for i in u["words"]), max(words[i][2] for i in u["words"])
        return words[u["a"]][1], words[u["b"]][2]

    def mspan(u):
        a, b = span(u, True)
        if u.get("frac"):                       # compound like "shitbird": only the swear part
            b = a + (b - a) * u["frac"]
        return a, b

    mute_plan = {"frames": frames_for_spans(w, [mspan(u) for u in units], 60),
                 "spans": [list(mspan(u)) for u in units]}
    if mode == "mute" or standalone or partial:
        why = "muted" + (" (stand-alone line)" if standalone and mode != "mute" else "")
        return mute_plan, [(text(u), *span(u, True), why, "" if standalone else u["repl"]) for u in units]

    ops, actions = [], []
    target_voice = lib.voice(key) if mode == "splice" else None
    for u in units:
        s, e = span(u)
        if u.get("frac"):
            ms, me = mspan(u)
            ops.append({"op": "mute", "t": [ms, me]})
            actions.append((text(u), ms, me, "muted (swear part only)", u["repl"]))
            continue
        if u["a"] == 0 and mode != "mute":      # swear opens the line: bleep it, keep the rest in place
            ops.append({"op": "mute", "t": [s, e]})
            actions.append((text(u), s, e, "muted (line start, timing kept)", "" if u["phrase"] else u["repl"]))
            continue
        # cut from the gap after the previous word to the gap before the next word,
        # so no onset or tail of the removed word survives the cut
        prev_end = words[u["a"] - 1][2] if u["a"] > 0 else None
        next_start = words[u["b"] + 1][1] if u["b"] + 1 < len(words) else None
        s0 = (s - 0.30) if prev_end is None else max(prev_end + 0.02, s - 0.20)
        s0 = max(0.0, min(s0, s))
        e0 = (e + 0.45) if next_start is None else min(next_start - 0.02, e + 0.30)
        e0 = max(e0, e)
        done = False
        if mode == "splice" and not u["phrase"]:
            word = words[u["words"][0]][0]
            best = None
            for cand in settings["splice_options"].get(norm(word), []):
                for dkey, ds, de in donors.get(cand, [])[:60]:
                    if dkey not in lib.clips:
                        continue
                    ratio = (de - ds) / max(e - s, 0.05)
                    if not 0.5 <= ratio <= 2.0:
                        continue
                    score = similarity(target_voice, lib.voice(dkey))
                    if best is None or score > best[0]:
                        best = (score, cand, dkey, ds, de)
            if best and best[0] >= settings["splice_min_match"]:
                score, cand, dkey, ds, de = best
                ops.append({"op": "splice", "t": [s0, e0], "donor": {"id": lib.clips[dkey][3].oasis_id,
                            "fp": lib.fingerprint(dkey), "key": dkey, "t": [max(0.0, ds - 0.03), de + 0.04]}})
                actions.append((text(u), s, e, f"spliced '{cand}' (voice match {score:.2f})", cand))
                done = True
        if not done:
            fb = "close_gap" if mode == "close_gap" or u["phrase"] else settings["splice_fallback"]
            if fb == "close_gap":
                ops.append({"op": "cut", "t": [s0, e0]})
                actions.append((text(u), s, e, "phrase removed" if u["phrase"] else "gap closed", u["repl"] if not u["phrase"] else ""))
            else:
                ops.append({"op": "mute", "t": [s, e]})
                actions.append((text(u), s, e, "muted", u["repl"]))
    return {"ops": ops, "frames": mute_plan["frames"], "spans": mute_plan["spans"]}, actions


# ---------------- applying ----------------

def _beep(pcm, t, sr=48000, dur=0.22):
    """Test mode: mix a clear tone at time t so every edit is audible."""
    a = int(max(0, t) * sr)
    n = min(int(dur * sr), len(pcm) - a)
    if n <= 0:
        return pcm
    x = np.arange(n) / sr
    tone = (0.25 * np.sin(2 * np.pi * 1000 * x) * np.hanning(n)).astype("float32")[:, None]
    out = pcm.copy()
    out[a:a + n] = out[a:a + n] * 0.3 + tone
    return out


def apply_plan(w, plan, get_donor_pcm, beep=False):
    """Apply a plan to clip `w` in place. Falls back to plain muting if anything doesn't fit.
    get_donor_pcm(donor_dict) -> pcm or None. Returns the action actually taken."""
    from .wem import decode_pcm48, mute_frames, replace_pcm
    ops = plan.get("ops")
    if ops:
        try:
            pcm, good = decode_pcm48(w)
            if good < len(w.packets()):
                raise ValueError("partial clip")
            new = pcm
            for op in sorted(ops, key=lambda o: -o["t"][0]):
                s, e = op["t"]
                if op["op"] == "cut":
                    new = cut_ranges(new, [(s, e)])
                elif op["op"] == "mute":
                    a, b = int(max(0, s - 0.03) * 48000), int((e + 0.03) * 48000)
                    new = new.copy()
                    new[a:b] = 0
                elif op["op"] == "splice":
                    d = get_donor_pcm(op["donor"])
                    seg = None
                    if d is not None:
                        ds, de = op["donor"]["t"]
                        seg = trim_silence(d[int(ds * 48000):int(de * 48000)])
                        # match loudness to the word being replaced
                        orig = new[int(s * 48000):int(e * 48000)]
                        r_t = np.sqrt((orig ** 2).mean()) if orig.size else 0
                        r_d = np.sqrt((seg ** 2).mean()) if seg.size else 0
                        if r_t > 0 and r_d > 0:
                            seg = seg * min(2.5, max(0.4, r_t / r_d))
                        if seg.shape[1] != new.shape[1]:
                            seg = np.repeat(seg.mean(axis=1, keepdims=True), new.shape[1], axis=1)
                    spliced = splice_in(new, s, e, seg) if seg is not None else None
                    new = spliced if spliced is not None else cut_ranges(new, [(s, e)])
            if beep:
                for op in ops:
                    new = _beep(new, op["t"][0])
            if replace_pcm(w, new.astype("float32")):
                return "edited"
        except Exception:  # noqa: BLE001  - anything odd: fall back to muting
            pass
    if beep and plan.get("spans"):
        try:
            pcm, good = decode_pcm48(w)
            if good >= len(w.packets()):
                new = pcm.copy()
                for a, b in plan["spans"]:
                    new[int(max(0, a - 0.03) * 48000):int((b + 0.03) * 48000)] = 0
                    new = _beep(new, a)
                if replace_pcm(w, new.astype("float32")):
                    return "muted"
        except Exception:  # noqa: BLE001
            pass
    mute_frames(w, plan.get("frames", []))
    return "muted"
