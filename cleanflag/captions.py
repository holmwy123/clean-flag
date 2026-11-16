"""Subtitle cleaning for the game's caption files (inside DataPC_boot*.forge).

Format (worked out from the game files):
  forge header @0x41a: u32 entry_count, u64 index_offset
  index entry (24 bytes): u64 offset, u64 file_id, u32 size, u32 extra
  an entry = compressed blocks (magic 33AAFB5799FA0410, u16 ver, u8 type,
             u32 max_chunk, u32 chunk_count, chunk_count x (u32 unpacked, u32 packed),
             then per chunk: u32 checksum + data; packed == unpacked means stored raw)
  caption record inside the unpacked data: u64 object_id, u32 0, u32 char_count, UTF-16LE text

Edits keep every caption the SAME length (padded with spaces) so nothing inside the file moves.
The edited file is appended to the forge, stored uncompressed, and its index entry is pointed at
the new copy. Undo = truncate the forge back and restore the 24-byte index entries.
"""
import ctypes
import json
import os
import re
import struct
from pathlib import Path

MAGIC = bytes.fromhex("33AAFB5799FA0410")
REC = re.compile(rb"(?:[\x20-\x7e]\x00){3,}")
HERE = Path(__file__).resolve().parent


# ---------------- Oodle decoding ----------------

_LIB = None


def _lib():
    global _LIB
    if _LIB is None:
        names = ["ooz.dll"] if os.name == "nt" else ["libooz.so"]
        for n in names:
            p = HERE / "bin" / n
            if p.exists():
                _LIB = ctypes.CDLL(str(p))
                break
        if _LIB is None:
            raise RuntimeError("Oodle decoder library not found in cleanflag/bin")
        _LIB.ooz_decompress.restype = ctypes.c_int
    return _LIB


def read_index(m):
    cnt, idx = struct.unpack_from("<IQ", m, 0x41a)
    return idx, [list(struct.unpack_from("<QQII", m, idx + 24 * i)) for i in range(cnt)]


def parse_blocks(m, off, size):
    """Yield (block_start, header_bytes, [(unpacked, packed, checksum, data_bytes)]) for an entry."""
    p, end = off, off + size
    while p < end:
        s = m.find(MAGIC, p, end)
        if s < 0:
            break
        mx, n = struct.unpack_from("<II", m, s + 11)
        tab = [struct.unpack_from("<II", m, s + 19 + 8 * i) for i in range(n)]
        q = s + 19 + 8 * n
        chunks = []
        for u, c in tab:
            chunks.append((u, c, bytes(m[q:q + 4]), bytes(m[q + 4:q + 4 + c])))
            q += 4 + c
        yield s, bytes(m[s:s + 19]), chunks
        p = q


def unpack_chunk(u, c, data):
    if u == c:
        return data
    if len(data) < 2 or (data[0] & 0x0F) != 0x0C or data[1] not in (6, 10, 11, 12):
        raise ValueError("unsupported compression")
    dst = ctypes.create_string_buffer(u + 64)
    if _lib().ooz_decompress(data, len(data), dst, u) != u:
        raise ValueError("decode failed")
    return dst.raw[:u]


def read_entry(m, off, size):
    """Return (prefix_bytes_before_first_block, [(header19, [unpacked chunk bytes...])])."""
    blocks = list(parse_blocks(m, off, size))
    prefix = bytes(m[off:blocks[0][0]]) if blocks else bytes(m[off:off + size])
    out = []
    for s, hdr, chunks in blocks:
        out.append((hdr, [unpack_chunk(u, c, d) for u, c, _, d in chunks], [ck for _, _, ck, _ in chunks]))
    return prefix, out


def build_entry(prefix, blocks):
    """Rebuild an entry with every chunk stored raw (packed == unpacked)."""
    out = bytearray(prefix)
    for hdr, chunks, checks in blocks:
        h = bytearray(hdr)
        struct.pack_into("<I", h, 15, len(chunks))
        out += h
        for ch in chunks:
            out += struct.pack("<II", len(ch), len(ch))
        for ch, ck in zip(chunks, checks):
            out += ck + ch
    return bytes(out)


# ---------------- caption records ----------------

def find_captions(data):
    """[(text_offset, char_count, text)] for every caption-like UTF-16 record in unpacked data."""
    out = []
    for mt in REC.finditer(data):
        s = mt.start()
        if s < 16:
            continue
        n = struct.unpack_from("<I", data, s - 4)[0]
        if n == 0 or n * 2 < len(mt.group()) - 2 or s + 2 * n > len(data):
            continue
        try:
            t = data[s:s + 2 * n].decode("utf-16-le")
        except UnicodeDecodeError:
            continue
        out.append((s, n, t))
    return out


def same_length(new, n):
    """Fit `new` into exactly n characters (pad with spaces; trim only trailing spaces if longer)."""
    new = re.sub(r"\s{2,}", " ", new).replace(" ,", ",").replace(" .", ".").replace(" !", "!").replace(" ?", "?")
    new = re.sub(r"^\s*[,;!.?]+\s*", "", new)
    new = new[:1].upper() + new[1:] if new and new[0].isalpha() and not new[:1].isupper() else new
    if len(new) > n:
        return None
    return new + " " * (n - len(new))


def _caps(text, toks, units):
    """Test-mode fallback when '[word]' won't fit: show the handled words in ALL CAPS (same length)."""
    out = text
    for u in units:
        a0, b0 = toks[u["a"]].start(), toks[u["b"]].end()
        seg = text[a0:b0]
        core_end = len(seg.rstrip(".,!?;:\"'"))
        out = out[:a0] + seg[:core_end].upper() + seg[core_end:] + out[b0:]
    return out if out != text else None


def clean_caption(text, wf, how, test_mode=False):
    """Apply word-filter units to one caption.

    how(unit_text) -> ("cut", None) | ("replace", word) | ("blank", None)  chosen per unit by the caller.
    Returns the new text (same length) or None if unchanged / doesn't fit.
    """
    toks = list(re.finditer(r"\S+", text))
    words = [t.group() for t in toks]
    units = wf.flag_units(words)
    if not units:
        return None
    levels = (0, 1) if test_mode else (None,)
    for level in levels:
        new, blanked = text, False
        for u in sorted(units, key=lambda x: -x["a"]):
            a0 = toks[u["a"]].start()
            b0 = toks[u["b"]].end()
            raw = text[a0:b0]
            lead_len = len(raw) - len(raw.lstrip("([{\"\u201c\u2018"))     # keep opening brackets/quotes
            core_end = max(lead_len, len(raw.rstrip(".,!?;:\"')]}\u201d\u2019")))   # keep trailing punctuation and closing brackets
            lead, trail = raw[:lead_len], raw[core_end:]
            act, word = how(u, raw[lead_len:core_end])
            swear = " ".join(re.sub(r"[^\w'*-]", "", words[i]) for i in u["words"])
            if act == "blank":
                blanked = True
                break
            if act == "replace":
                rep = word
                if raw[:1].isupper():
                    rep = rep[:1].upper() + rep[1:]
                if test_mode:
                    rep = rep + " [" + swear + "]" if level == 0 else rep.upper()
            else:  # cut
                rep = ("[" + swear + "]" if level == 0 else "[x]") if test_mode else ""
            new = new[:a0] + lead + rep + trail + new[b0:]
        if blanked:
            if test_mode:
                tagged = same_length("[" + text.strip() + "]", len(text))
                return tagged if tagged is not None else _caps(text, toks, units)
            return " " * len(text)
        if not test_mode and not re.search(r"[A-Za-z]", new):     # nothing left but punctuation
            return " " * len(text)
        fitted = same_length(new, len(text))
        if fitted is not None:
            return fitted
    if test_mode:
        return _caps(text, toks, units)                           # nothing fits: show original in CAPS
    # replacement didn't fit - fall back to cutting the words out instead
    cut_all = lambda u, raw: ("cut", None)  # noqa: E731
    if any(how(u, "x")[0] != "cut" for u in units):
        return clean_caption(text, wf, cut_all, False)
    return None


# ---------------- patching the game's caption files (surgical) ----------------
#
# The original entry bytes are preserved verbatim. Only chunks whose unpacked bytes
# actually changed are re-stored (uncompressed); every other chunk, block header, gap
# and padding byte is copied straight from the original file.

def map_entry(m, off, size):
    """Walk an entry. Returns (blocks, flat_unpacked_bytes).

    blocks: [{start, end, hdr, chunks: [{u, c, ck, data, flat_lo}]}] with file ranges."""
    p, end, blocks, flat = off, off + size, [], bytearray()
    while p < end:
        s = m.find(MAGIC, p, end)
        if s < 0:
            break
        n = struct.unpack_from("<I", m, s + 15)[0]
        if n == 0 or n > 100000:
            raise ValueError("unrecognised block")
        tab = [struct.unpack_from("<II", m, s + 19 + 8 * i) for i in range(n)]
        q = s + 19 + 8 * n
        chunks = []
        for u, c in tab:
            if q + 4 + c > end:
                raise ValueError("truncated block")
            data = bytes(m[q + 4:q + 4 + c])
            raw = data if u == c else unpack_chunk(u, c, data)
            chunks.append({"u": u, "c": c, "ck": bytes(m[q:q + 4]), "data": data, "flat_lo": len(flat)})
            flat += raw
            q += 4 + c
        blocks.append({"start": s, "end": q, "hdr": bytes(m[s:s + 19]), "chunks": chunks})
        p = q
    return blocks, bytes(flat)


def rebuild_surgical(m, off, size, blocks, flat_edited):
    """Rebuild the entry, re-storing only chunks whose unpacked bytes changed.
    All other bytes (blocks, gaps, padding) are copied verbatim from the original."""
    out, cur, total = bytearray(), off, 0
    for b in blocks:
        new, blk_touched = [], False
        for ch in b["chunks"]:
            raw = bytes(flat_edited[ch["flat_lo"]:ch["flat_lo"] + ch["u"]])
            orig_raw = ch["data"] if ch["u"] == ch["c"] else unpack_chunk(ch["u"], ch["c"], ch["data"])
            if raw == orig_raw:
                new.append((ch["u"], ch["c"], ch["ck"], ch["data"]))
            else:
                new.append((ch["u"], ch["u"], ch["ck"], raw))
                blk_touched = True
                total += 1
        if blk_touched:
            out += m[cur:b["start"]]
            out += b["hdr"]
            for u, c, ck, d in new:
                out += struct.pack("<II", u, c)
            for u, c, ck, d in new:
                out += ck + d
            cur = b["end"]
    out += m[cur:off + size]
    return bytes(out), total


def load_audio_report(out_dir):
    """muted_words.csv -> {oasis_id: [(word, what_happened, caption), ...]}"""
    import csv
    rep = {}
    p = Path(out_dir) / "muted_words.csv"
    if not p.exists():
        return rep
    for r in csv.DictReader(open(p, encoding="utf-8")):
        rep.setdefault(r["subtitle_id"].upper().replace("0X", "0x"), []).append(
            (r["word"], r["what_happened"], r["caption"]))
    return rep


def _voice_ids(data, known):
    out = []
    for i in range(0, len(data) - 8):
        if data[i + 7] == 0xBF:
            v = struct.unpack_from("<Q", data, i)[0]
            if v in known:
                out.append((i, v))
    return out


def pair_actions(caps, data, known_ids, report):
    ids = _voice_ids(data, known_ids)
    acts, oids = [], []
    for k, (s, n, t) in enumerate(caps):
        oid = None
        if len(ids) == len(caps):
            oid = ids[k][1]
        elif ids:
            oid = min(ids, key=lambda iv: abs(iv[0] - s))[1]
        oids.append(oid)
        acts.append(report.get(f"0x{oid:016X}") if oid is not None else None)
    return acts, oids


def make_how(line_actions):
    def how(u, raw):
        low = raw.lower()
        if line_actions:
            for word, act, cap in line_actions:
                if act.startswith("muted (stand-alone"):
                    return ("blank", None)
                w0 = re.sub(r"[^a-z' ]", "", word.lower()).strip()
                if w0 and (w0 in low or low in w0):
                    if (act.startswith("spliced") or act.startswith("muted")) and cap:
                        return ("replace", cap)
                    return ("cut", None)
        if u["phrase"] or not u["repl"]:
            return ("cut", None)
        return ("replace", u["repl"])
    return how


def patch_forge_captions(forge_path, entry_list, wf, report, known_ids, undo_path, test_mode=False, only_ids=None):
    """Surgically edit flagged captions in the listed entries. In place, verified, undoable."""
    import mmap
    forge_path = Path(forge_path)
    undo = json.loads(Path(undo_path).read_text() or "{}") if Path(undo_path).exists() else {}
    u = undo.get(forge_path.name)
    stats = {"lines": 0, "entries": 0, "chunks": 0}
    with open(forge_path, "r+b") as f:
        if u:                      # roll back any previous caption patch first
            for pos, hexbytes in u["entries"]:
                f.seek(pos)
                f.write(bytes.fromhex(hexbytes))
            f.truncate(u["orig_size"])
            f.flush()
        f.seek(0, 2)
        orig_size = f.tell()
        m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        idx, ents = read_index(m)
        changed, saved = [], []
        for ei in entry_list:
            off, fid, size, extra = ents[ei]
            try:
                blocks, flat = map_entry(m, off, size)
            except (ValueError, struct.error):
                continue
            caps = find_captions(flat)
            if not caps:
                continue
            acts, oids = pair_actions(caps, flat, known_ids, report)
            flat2 = bytearray(flat)
            touched_lines = 0
            for (s, n, t), la, oid in zip(caps, acts, oids):
                if only_ids is not None and oid not in only_ids:
                    continue
                new = clean_caption(t, wf, make_how(la), test_mode)
                if new is None:
                    continue
                enc = new.encode("utf-16-le")
                assert len(enc) == 2 * n
                flat2[s:s + 2 * n] = enc
                touched_lines += 1
            if not touched_lines:
                continue
            data, nch = rebuild_surgical(m, off, size, blocks, bytes(flat2))
            # verify before writing: the rebuilt entry must unpack to exactly the edited text
            b2, f2 = map_entry(data, 0, len(data))
            if f2 != bytes(flat2):
                print(f"  entry {ei}: verification failed - skipped")
                continue
            changed.append((ei, data))
            stats["lines"] += touched_lines
            stats["chunks"] += nch
        m.close()
        for ei, data in changed:
            f.seek(0, 2)
            new_off = f.tell()
            f.write(data)
            pos = idx + 24 * ei
            saved.append((pos, struct.pack("<QQII", *ents[ei]).hex()))
            f.seek(pos)
            f.write(struct.pack("<QQII", new_off, ents[ei][1], len(data), ents[ei][3]))
        if changed:
            stats["entries"] = len(changed)
            undo[forge_path.name] = {"orig_size": orig_size, "entries": saved}
            Path(undo_path).write_text(json.dumps(undo))
    return stats


def undo_forge_captions(game_dir, undo_path):
    undo_path = Path(undo_path)
    if not undo_path.exists() or not json.loads(undo_path.read_text() or "{}"):
        print("No caption changes recorded - nothing to undo.")
        return
    undo = json.loads(undo_path.read_text())
    for name, u in undo.items():
        p = Path(game_dir) / name
        if not p.exists():
            continue
        with open(p, "r+b") as f:
            for pos, hexbytes in u["entries"]:
                f.seek(pos)
                f.write(bytes.fromhex(hexbytes))
            f.truncate(u["orig_size"])
        print(f"  {name}: captions restored")
    try:
        undo_path.unlink()
    except OSError:
        undo_path.write_text("{}")
