"""Clean the English subtitles inside DataPC_boot_patch_01.forge, in place.

The subtitle package is rebuilt from YOUR copy of the game (no game text ships with the mod),
written over its own slot in the archive, and an exact undo record is saved first.
"""
import hashlib, json, mmap, struct
from pathlib import Path
from . import captions as C
from . import locclean
from .loc import Loc, make_entry

FID = 2217383002073                      # English subtitles
FORGE = "DataPC_boot_patch_01.forge"


def _header(path):
    """forge header: index (TOC) position, stored as u64 in the data header that the file header points to"""
    with open(path, "rb") as f:
        head = f.read(0x4A0)
    if head[:8] != b"scimitar":
        raise ValueError("not a forge archive")
    odh = struct.unpack_from("<Q", head, 13)[0]
    n = struct.unpack_from("<I", head, odh)[0]
    toc_off = struct.unpack_from("<Q", head, odh + 4)[0]
    return odh, n, toc_off


def _toc(path):
    odh, n, toc_off = _header(path)
    with open(path, "rb") as f:
        f.seek(toc_off); toc = f.read(n * 24)
    pos = next((i for i in range(n) if struct.unpack_from("<Q", toc, i * 24 + 8)[0] == FID), None)
    return toc_off, toc, n, pos


def _undo(path, undo_file, quiet=False):
    u = json.loads(Path(undo_file).read_text())
    if u.get("mode") == "append":
        return _undo_append(path, undo_file, u, quiet)
    toc_off, toc, n, pos = _toc(path)
    with open(path, "r+b") as f:
        f.seek(u["off"]); cur = f.read(u["written_len"])
        if hashlib.sha256(cur).hexdigest() != u["written_sha"]:
            if not quiet:
                print("  Subtitles: the game file was replaced since (update or Verify files), nothing to undo.")
            Path(undo_file).unlink(); return False
        f.seek(u["off"]); f.write(bytes.fromhex(u["record"]))
        f.seek(u["toc_off"] + u["pos"] * 24); f.write(bytes.fromhex(u["tocentry"]))
    Path(undo_file).unlink()
    if not quiet:
        print("  Subtitles restored to the original.")
    return True


def _undo_append(path, undo_file, u, quiet):
    with open(path, "r+b") as f:
        f.seek(u["new_toc_off"] - u["blob_len"]); cur = f.read(u["blob_len"])
        if hashlib.sha256(cur).hexdigest() != u["written_sha"]:
            if not quiet:
                print("  Subtitles: the game file was replaced since (update or Verify files), nothing to undo.")
            Path(undo_file).unlink(); return False
        f.seek(u["toc_off"]); f.write(bytes.fromhex(u["tail"]))          # original index + tail back in place
        f.seek(u["hdr_pos"]); f.write(bytes.fromhex(u["hdr"]))            # original index position
        f.truncate(u["orig_size"])
    Path(undo_file).unlink()
    if not quiet:
        print("  Subtitles restored to the original.")
    return True


def _install_append(path, toc_off, toc, pos, blob, undo_file):
    """No room in the old slot: put the new package where the index was, then the index (and the
    bytes after it) right behind it, and point the header at the new index position."""
    import os
    odh, n, _ = _header(path)
    size0 = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(toc_off); tail = f.read(size0 - toc_off)          # index + whatever follows it
        f.seek(odh + 4); hdr = f.read(8)
    blob_len = (len(blob) + 15) // 16 * 16
    padded = blob + b"\x00" * (blob_len - len(blob))
    new_toc_off = toc_off + blob_len
    e = bytearray(tail[pos * 24: pos * 24 + 24])
    struct.pack_into("<Q", e, 0, toc_off); struct.pack_into("<I", e, 16, len(blob))
    newtail = bytearray(tail); newtail[pos * 24: pos * 24 + 24] = e
    Path(undo_file).parent.mkdir(parents=True, exist_ok=True)
    Path(undo_file).write_text(json.dumps({"mode": "append", "toc_off": toc_off, "tail": tail.hex(), "hdr_pos": odh + 4,
                                           "hdr": hdr.hex(), "orig_size": size0, "new_toc_off": new_toc_off,
                                           "blob_len": blob_len, "written_sha": hashlib.sha256(padded).hexdigest()}))
    with open(path, "r+b") as f:
        f.seek(toc_off); f.write(padded); f.write(bytes(newtail))
        f.seek(odh + 4); f.write(struct.pack("<Q", new_toc_off))
    return True


def install(game, undo_file, force_append=False, test=False):
    path = Path(game) / FORGE
    if not path.exists():
        print(f"  Subtitles: {FORGE} not found, skipped."); return
    if Path(undo_file).exists():                 # already cleaned once: start again from the original
        _undo(path, undo_file, quiet=True)
    toc_off, toc, n, pos = _toc(path)
    if pos is None:
        print("  Subtitles: English subtitle package not found (different game version?), skipped."); return
    off, _, size, _ = struct.unpack_from("<QQII", toc, pos * 24)
    nxt = min([struct.unpack_from("<Q", toc, i * 24)[0] for i in range(n) if struct.unpack_from("<Q", toc, i * 24)[0] > off] + [toc_off])
    with open(path, "rb") as f:
        m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        _, blks = C.read_entry(m, off, size)
        data = b"".join(blks[1][1])
        space = size
        if nxt - off > size and not any(m[off + size: nxt]):
            space = nxt - off                    # zero padding after the entry is free room
        record = bytes(m[off: off + space])
        m.close()
    loc = Loc(data)
    texts = loc.texts()
    new, log = locclean.clean_lines(texts, "clean")
    if test:
        from .showmarks import show
        new = [show(o, x) for o, x in zip(texts, new)]
    rebuilt, _ = loc.rebuild(new)
    if Loc(rebuilt).texts() != new:
        print("  Subtitles: rebuild check failed, left untouched."); return
    blob = make_entry(FID, rebuilt)
    if len(blob) > space or force_append:
        _install_append(path, toc_off, toc, pos, blob, undo_file)
        print(f"  Subtitles: {len(log)} lines cleaned." + (" TEST MODE: removed words are shown in *asterisks*." if test else "")); return
    oldtoc = toc[pos * 24: pos * 24 + 24]
    newtoc = bytearray(oldtoc); struct.pack_into("<I", newtoc, 16, len(blob))
    written = blob + b"\x00" * (space - len(blob))
    Path(undo_file).parent.mkdir(parents=True, exist_ok=True)
    Path(undo_file).write_text(json.dumps({"off": off, "record": record.hex(), "pos": pos, "toc_off": toc_off,
                                           "tocentry": oldtoc.hex(), "written_len": len(written),
                                           "written_sha": hashlib.sha256(written).hexdigest()}))
    with open(path, "r+b") as f:
        f.seek(off); f.write(written)
        f.seek(toc_off + pos * 24); f.write(bytes(newtoc))
    print(f"  Subtitles: {len(log)} lines cleaned." + (" TEST MODE: removed words are shown in *asterisks*." if test else ""))


def uninstall(game, undo_file):
    path = Path(game) / FORGE
    if Path(undo_file).exists() and path.exists():
        _undo(path, undo_file)
