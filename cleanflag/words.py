"""Shared word list: decides which words are 'bad' and what clean word replaces them.

The same list drives both the audio muting and the caption cleaning, so the
two always agree.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_LIST = Path(__file__).resolve().parent.parent / "wordlist.json"


@dataclass
class Hit:
    start: int        # character offset in the text
    end: int
    word: str         # the original word as written
    replacement: str  # clean word, case-matched


def _match_case(original: str, replacement: str) -> str:
    if original.isupper() and len(original) > 1:
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


class WordFilter:
    def __init__(self, path: Path = DEFAULT_LIST):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.rules = []  # (compiled regex, replacement)
        for cat in data["categories"].values():
            if not cat.get("enabled", True):
                continue
            for pattern, repl in cat["words"].items():
                self.rules.append((re.compile(rf"^(?:{pattern})$", re.IGNORECASE), repl))
        self.safe = {w.lower() for w in data.get("never_filter", [])}
        self.oaths = data["categories"].get("religious_oaths", {}).get("enabled", False)

    def compound_split(self, word: str):
        """'shitbird' -> ('shit', 'bird'): a swear glued to an ordinary word. Only the swear is removed."""
        core = re.sub(r"^[^\w]+|[^\w]+$", "", word).lower()
        if not core or core in self.safe:
            return None
        for pre in _COMPOUND_PREFIXES:
            if core.startswith(pre) and len(core) - len(pre) >= 3 and not self._plain_rule(core):
                rest = core[len(pre):]
                if rest in ("ing", "ed", "er", "ers", "ty", "hole", "holes", "s", "y", "in", "e"):
                    return None
                return pre, rest
        return None

    def _plain_rule(self, core):
        return any(rx.match(core) for rx, _ in self.rules)

    def check_word(self, word: str):
        """Return the clean replacement if this single word is flagged, else None."""
        core = re.sub(r"^[^\w*]+|[^\w*]+$", "", word)  # strip punctuation around it
        if not core or core.lower() in self.safe:
            return None
        if "*" in core and core[:1].isalpha():  # recogniser wrote a bleeped word like "f***ing"
            return _match_case(core, "blasted")
        for rx, repl in self.rules:
            if rx.match(core):
                return _match_case(core, repl)
        base = re.sub(r"['’]s$", "", core)          # possessive / contraction: "bastard's"
        if base != core and base.lower() not in self.safe:
            for rx, repl in self.rules:
                if rx.match(base):
                    return _match_case(base, repl) + "'s"
        cs = self.compound_split(core)
        if cs:
            return _match_case(core, cs[1])
        return None

    def flag_words(self, raw_words):
        """For a transcribed line: [(index, replacement)] of every flagged word,
        using the word list plus the context rules (hell as a place, religious oaths)."""
        hits = {}
        for i, w in enumerate(raw_words):
            if _norm(w) in ("hell", "hell's"):
                if self.check_word("hell") and hell_is_swear(raw_words, i):
                    hits[i] = _match_case(re.sub(r"[^\w']", "", w), "heck")
                continue
            r = self.check_word(w)
            if r:
                hits[i] = r
        if self.oaths:
            for i in oath_indices(raw_words):
                hits.setdefault(i, oath_replacement(raw_words[i]))
        return sorted(hits.items())

    def flag_units(self, raw_words):
        """Group flagged words into units that should be removed together.

        Returns [{"a": first_index, "b": last_index, "repl": caption_replacement, "phrase": bool}]
        phrase=True means the surrounding words go too ("what [the hell] are..."), so the
        line reads naturally with the unit simply removed.
        """
        flagged = dict(self.flag_words(raw_words))
        if not flagged:
            return []
        ws = [_norm(w) for w in raw_words]
        n = len(ws)
        units = []
        for i in sorted(flagged):
            a, b, phrase = i, i, False
            w = ws[i]
            # "what/who/where/how/why the hell", "what in the hell"
            if i >= 1 and ws[i - 1] == "the" and w in ("hell", "hell's", "fuck", "fucking", "heck", "blazes"):
                a, phrase = i - 1, True
                if i >= 2 and ws[i - 2] == "in" and i >= 3 and ws[i - 3] in _WH:
                    a = i - 2
            # oath lead-ins and tails: "oh God", "good Lord", "my God", "for God's sake", "in God's name"
            if w in _HOLY and i >= 1 and ws[i - 1] in ("oh", "good", "my", "ah", "dear", "sweet", "by", "for", "thank"):
                a, phrase = i - 1, True
            if w in ("fuck's", "fucks") and i + 1 < n and ws[i + 1] in ("sake", "sakes"):
                b, phrase = i + 1, True
                if i >= 1 and ws[i - 1] == "for":
                    a = i - 1
            if w in ("god's", "christ's") and i + 1 < n and ws[i + 1] in ("sake", "sakes", "name"):
                b, phrase = i + 1, True
                if ws[i - 1] == "in":
                    a = i - 1
            if w in ("hell", "hell's") and i >= 1 and ws[i - 1] in ("oh", "ah", "aw", "by", "bloody", "fucking", "holy", "burning"):
                a, phrase = i - 1, True
            # "a hell of a ..." -> "a ..."
            if w == "hell" and i + 2 < n and ws[i + 1] == "of" and ws[i + 2] in ("a", "an"):
                b, phrase = i + 2, True
            # "damn it", "damn you", "dammit all"
            if w in ("damn", "goddamn", "dammit", "damnit") and i + 1 < n and ws[i + 1] in ("it", "you", "all", "them", "him", "her"):
                b, phrase = i + 1, True
                if b + 1 < n and ws[b + 1] == "all" and ws[b] != "all":
                    b += 1
            # "son of a bitch"
            if w == "bitch" and i >= 3 and ws[i - 3:i] == ["son", "of", "a"]:
                a, phrase = i - 3, True
            u = {"a": a, "b": b, "repl": "" if phrase else flagged[i], "phrase": phrase, "words": [i]}
            cs = self.compound_split(raw_words[i]) if not self._plain_rule(re.sub(r"[^\w]", "", raw_words[i])) else None
            if cs and a == b and cs[1].rstrip("s'") not in _INSULT_TAILS:
                u["frac"] = len(cs[0]) / (len(cs[0]) + len(cs[1]))
            elif cs and a == b:
                u["repl"] = ""                   # insult compound ("shitbird", "pissant"): the whole word goes
            units.append(u)
        # merge overlapping / touching units ("bloody hell", "god damn it")
        merged = []
        for u in units:
            if merged and u["a"] <= merged[-1]["b"] + 1 and "frac" not in u and "frac" not in merged[-1]:
                m = merged[-1]
                m["b"] = max(m["b"], u["b"])
                m["phrase"] = True
                m["repl"] = m["repl"] if m["repl"] == "" else (m["repl"] + " " + u["repl"]).strip()
                m["words"] += u["words"]
            else:
                merged.append(u)
        return merged

    def find(self, text: str):
        """Find every flagged word in a line of text."""
        hits = []
        for m in re.finditer(r"[A-Za-z][A-Za-z'\-]*", text):
            word = m.group(0)
            # treat "fuckin'" / "god-damned" as whole words, but also check parts of hyphenated words
            candidates = [(m.start(), m.end(), word)]
            if "-" in word:
                pos = m.start()
                for part in word.split("-"):
                    candidates.append((pos, pos + len(part), part))
                    pos += len(part) + 1
            for s, e, w in candidates:
                repl = self.check_word(w.rstrip("'"))
                if repl:
                    hits.append(Hit(s, e, w, repl))
                    break
        return hits

    def clean_text(self, text: str, style: str = "replace") -> str:
        """style: 'replace' -> clean word, 'dash' -> em dash, 'stars' -> f***"""
        out, last = [], 0
        for h in self.find(text):
            out.append(text[last:h.start])
            if style == "replace":
                out.append(h.replacement)
            elif style == "stars":
                out.append(h.word[0] + "*" * (len(h.word) - 1))
            else:
                out.append("—")
            last = h.end
        out.append(text[last:])
        return "".join(out)


# ---------------- context rules ----------------

_COMPOUND_PREFIXES = ("shit", "fuck", "piss", "bullshit")
_WH = {"what", "who", "where", "how", "why", "when", "which"}
_HELL_EXCLAIM_PREV = {"bloody", "fucking", "oh", "ah", "aw", "holy", "burning", "by", "bloody-", "f"}


def hell_is_swear(raw_words, i):
    """'hell' as a swear (what the hell, bloody hell!, a hell of a...) vs Hell the place
    (gates of Hell, burn in Hell, send them to Hell). Places are left alone."""
    ws = [_norm(w) for w in raw_words]
    prev = ws[i - 1] if i else ""
    prev2 = ws[i - 2] if i > 1 else ""
    nxt = ws[i + 1] if i + 1 < len(ws) else ""
    raw = raw_words[i].strip()
    if prev == "the" and (prev2 in _WH or prev2 in ("in", "off", "out", "outta") or (i > 2 and ws[i - 3] in _WH)
                          or nxt in ("out", "outta", "away", "off", "up", "on", "with", "down", "over")):
        return True                                      # what the hell / what in the hell / get the hell out
    if prev in _HELL_EXCLAIM_PREV:
        return True                                      # bloody hell, oh hell, by hell
    if nxt == "of" and prev in ("a", "one"):
        return True                                      # a hell of a storm
    if i == 0 and (raw[-1:] in "!.," or len(ws) == 1) and nxt not in ("is", "was", "awaits", "opens"):
        return True                                      # "Hell!" on its own
    return False                                         # in Hell, to Hell, gates of Hell, heaven and hell...


# ---------------- context-aware religious oaths ----------------

_INSULT_TAILS = {"bird", "ant", "head", "bag", "face", "stain", "hole", "wit", "brain", "heel"}
_HOLY = {"god", "god's", "jesus", "christ", "christ's", "lord"}
_OATH_REPL = {"god": "heavens", "god's": "heaven's", "jesus": "blimey", "christ": "blimey",
              "christ's": "blimey", "lord": "heavens"}


def _norm(w):
    return re.sub(r"[^a-z']", "", w.lower())


def oath_indices(raw_words):
    """Indices of holy names used as exclamations/oaths. Sincere uses are left alone.

    Muted:  "oh God", "my God", "good Lord", "by God", "for God's sake", "in God's name",
            "thank Christ", "Christ almighty", "God damn", and a name shouted on its own
            ("Jesus!", "Christ, what...").
    Kept:   "God save the King", "may God...", "God bless", "serve God", "the Lord...",
            "my lord" (a title), "God's will/grace", "Jesus will...", etc.
    """
    ws = [_norm(w) for w in raw_words]
    out = set()
    for i, w in enumerate(ws):
        if w not in _HOLY:
            continue
        prev = ws[i - 1] if i else ""
        nxt = ws[i + 1] if i + 1 < len(ws) else ""
        raw = raw_words[i].strip()
        prev_ends_sentence = i == 0 or raw_words[i - 1].strip()[-1:] in ".!?"
        exclaimed = raw[-1:] in "!,?" or i + 1 == len(ws)
        sincere_next = nxt in ("will", "let", "is", "was", "said", "says", "loves", "saves", "save", "bless",
                               "keep", "help", "forgive", "have", "carries", "rested", "knows")
        if w == "lord":
            before = ws[i - 2] if i > 1 else ""
            if prev in ("good", "oh", "dear", "sweet") and before not in ("the", "our", "my", "your", "his", "their") \
                    and not sincere_next and (exclaimed or i <= 1):
                out.add(i)
            continue
        if w in ("god's", "christ's") and nxt not in ("sake", "sakes", "name"):
            continue
        if sincere_next and prev not in ("oh", "my", "good", "by", "for"):
            continue
        if prev in ("oh", "good", "sweet", "dear", "thank") or (prev == "my" and w != "lord"):
            if nxt not in ("save", "bless", "keep", "help", "forgive", "have"):
                out.add(i)
        elif prev == "by" and w in ("god", "christ", "jesus"):
            out.add(i)
        elif prev == "god" and w == "god":
            out.update((i - 1, i))
        elif prev == "for" and w in ("god's", "christ's") and nxt in ("sake", "sakes"):
            out.add(i)
        elif prev in ("in", "the") and w == "god's" and nxt == "name" and raw_words[i - 2:i - 1] and _norm(raw_words[i - 2]) in ("what", "who", "where", "how", "why"):
            out.add(i)
        elif nxt in ("damn", "damned", "damnit", "dammit", "almighty"):
            out.add(i)
        elif prev_ends_sentence and exclaimed and w in ("jesus", "christ", "god"):
            out.add(i)
        elif w in ("jesus", "christ") and raw[-1:] in "!.,?" and (prev_ends_sentence or raw_words[i - 1].strip()[-1:] == ","):
            out.add(i)                                   # "Christ. I've got...", "What a mess, Jesus!", "my pouch, Christ, where's"
    return sorted(out)


def oath_replacement(word):
    core = re.sub(r"^[^\w']+|[^\w']+$", "", word)
    return _match_case(core, _OATH_REPL.get(_norm(word), "blimey"))
