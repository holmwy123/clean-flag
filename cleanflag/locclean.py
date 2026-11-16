"""Clean swear words out of the game's subtitle/menu text packages (works on the player's own English text)."""
import ctypes, mmap, re, sys
from cleanflag import captions as C
from cleanflag.loc import Loc
from cleanflag.words import WordFilter

def make_how(wf):
    """how a flagged word is handled: a swear glued to another word keeps the other word, otherwise it is cut."""
    def how(u, raw):
        word = re.sub(r"[^\w']", "", raw)
        if "frac" in u:
            cs = wf.compound_split(word)
            if cs:
                rest = cs[1]
                if len(rest) < 3 or rest.lower() in ("ing", "ting", "ted", "ed", "er", "ers", "ery", "ey", "ty", "tier", "est", "ier"):
                    return ("cut", None)
                return ("replace", rest[:1].upper() + rest[1:] if raw[:1].isupper() else rest)
        m = re.search(r"(['’]s)$", raw)
        if m and not u.get("phrase"):              # "That Shitbird's been" -> "That's been"
            return ("replace", "\x04" + m.group(1))
        return ("cut", None)
    return how


TEXT_FIDS = (2217383002072, 2217383002073)       # interface text, subtitle text


def load_packages(libooz, forge_path):
    C._LIB = ctypes.CDLL(libooz); C._LIB.ooz_decompress.restype = ctypes.c_int
    f = open(forge_path, "rb"); m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    _, ents = C.read_index(m)
    idx = {e[1]: e for e in ents}
    out = {}
    for fid in TEXT_FIDS:
        o, _, sz, ex = idx[fid]
        pre, blks = C.read_entry(m, o, sz)
        out[fid] = (Loc(b"".join(blks[1][1])), ex)
    return out


TAG = re.compile(r"(\[\[[^\]]*\]\]|\[[^\]]*\]|<[^>]*>)")        # [stage directions], [*quote tags*], <silence:123> timing tags stay untouched


def _tidy(s):
    s = re.sub(r"\b(that|this|it|he|she|who|what|there|here|where)\s*\x04", lambda m: m.group(1), s, flags=re.I)
    s = re.sub(r"\s*\x04['\u2019]s", "", s)                  # "The Bastard's been" -> "The been" (matches the audio)
    s = re.sub(r"\.{4,}", "...", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\(\s*[!?.,;:]*\s*\)", "", s)                  # parentheses left empty
    s = re.sub(r"\(\s*[,;:]+\s*", "(", s)                       # "(, Kenway!)" -> "(Kenway!)"
    s = re.sub(r"[,;:]+\s*([)\]])", r"\1", s)                     # "(Stop right there,)" -> "(Stop right there)"
    s = re.sub(r"\s+([,.!?;:])", r"\1", s)
    s = re.sub(r"([.!?])\s*[,;:]+\s*([a-z])", lambda m: m.group(1) + " " + m.group(2).upper(), s)
    s = re.sub(r"([.!?])\s*[,;:]+", r"\1", s)
    s = re.sub(r",+\s*([.!?])", r"\1", s)
    s = re.sub(r"([,;:])\s*([,;:])", r"\1", s)
    s = re.sub(r"(?<![.])\.\.(?![.])", ".", s)                    # ".." from a cut between two sentences
    s = re.sub(r"([!?])\.(?![.])", r"\1", s)                      # "!." -> "!"
    s = re.sub(r"(?<!\.)\.([!?])", r"\1", s)                     # ".!" -> "!" (but "...!" stays)
    s = re.sub(r"\(\s+", "(", s)                                 # "( Pirate!)" -> "(Pirate!)"
    s = re.sub(r"([!?])\1+", r"\1", s)
    return s


def _cap_fix(new, old):
    """after a cut, a sentence that now starts lowercase gets its capital back (only where the original did not already read that way)"""
    def up(m):
        ctx = m.group(0)
        return m.group(1) + m.group(2).upper() if ctx not in old else ctx
    new = re.sub(r"([.!?]\s+)([a-z])", up, new)
    new = re.sub(r"(\(\s*)([a-z])", up, new)                      # "(no...!)" -> "(No...!)"
    parts = TAG.split(new)                       # first visible word of the line
    for i in range(0, len(parts), 2):
        if parts[i].strip():
            m = re.search(r"[A-Za-z]", parts[i])
            if m and parts[i][m.start()].islower() and re.search(r"[A-Z]", re.sub(r"<[^>]*>|\[[^\]]*\]", "", old).strip()[:1] or ""):
                parts[i] = parts[i][:m.start()] + parts[i][m.start()].upper() + parts[i][m.start() + 1:]
            break
    return "".join(parts)


def clean_text(t, wf, how):
    """cleaned text for one subtitle line, or the same text if nothing is flagged"""
    parts = TAG.split(t)
    out, changed = [], False
    for i, p in enumerate(parts):
        if i % 2 or not p.strip():
            out.append(p); continue
        lead, trail = p[:len(p) - len(p.lstrip())], p[len(p.rstrip()):]
        r = C.clean_caption(p.strip(), wf, how, False)
        if r is None:
            out.append(p); continue
        out.append(lead + r.strip() + trail); changed = True
    if not changed:
        return t
    return _cap_fix(_tidy("".join(out)).strip(), t)


def make_how_mark(wf):
    """visible test: every removed swear shows as (bleep)"""
    base = make_how(wf)
    def how(u, raw):
        a = base(u, raw)
        return a if a[0] == "replace" else ("replace", "(bleep)")
    return how


def clean_lines(texts, mode="clean"):
    """returns (new_texts, [(old,new)])"""
    wf = WordFilter(); how = make_how_mark(wf) if mode == "mark" else make_how(wf)
    new, log = [], []
    for t in texts:
        if mode == "upper":      # visible test: every subtitle reads [TEST] (tags untouched; shorter, so it always fits)
            n = "".join(p if i % 2 or not p.strip() else "[TEST]" for i, p in enumerate(TAG.split(t)))
        else:
            n = clean_text(t, wf, how)
        if n != t:
            log.append((t, n))
        new.append(n)
    return new, log
