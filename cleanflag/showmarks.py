"""Test mode: show every removed word in *asterisks* right where it was cut, so the edits are easy to spot.
(Square brackets can't be used: the game treats [..] at the start of a subtitle as a sound tag and hides it.)"""
import difflib
import re

TOK = re.compile(r"<[^>]*>|\[\[[^\]]*\]\]|\[[^\]]*\]|\w+|[^\w\s]|\s+", re.U)


def show(old, new):
    if old == new:
        return new
    a, b = TOK.findall(old), TOK.findall(new)
    sm = difflib.SequenceMatcher(None, [x.lower() for x in a], [x.lower() for x in b], autojunk=False)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        out += b[j1:j2]
        if op in ("delete", "replace"):
            gone = "".join(a[i1:i2]).strip()
            if re.search(r"\w", gone):
                out.append(" *" + gone + "* ")
    s = re.sub(r" {2,}", " ", "".join(out))
    s = re.sub(r"\* ([,.!?;:'’])", r"*\1", s)
    s = re.sub(r"(<[^>]*>|\]\]|\() \*", lambda m: m.group(1) + "*", s)
    return s.strip()
