"""Shareable mute maps: which frames to silence in which voice clip.

A map holds NO game content: no audio and no dialogue. For each clip it stores the
clip's subtitle ID, a fingerprint (hash) of the original clip so we only ever edit
the exact same recording, the frame numbers to silence, and the flagged word ->
caption replacement pairs.

This module uses only Python's standard library, so applying a map needs no extra installs.
"""
import hashlib
import json
import os
import re
import string
import sys
from pathlib import Path

from .wem import build_same_size, find_clips, mute_frames

FORMAT = 2
GAME_DIR_NAMES = ["Assassin's Creed Black Flag Resynced", "Assassin's Creed Black Flag"]


def fingerprint(clip_bytes):
    return hashlib.sha256(clip_bytes).hexdigest()[:24]


BACKUP_DIR = Path(__file__).resolve().parent.parent / "backup"


def voice_archives(game_dir):
    """The ORIGINAL English voice archives. After an install the game folder holds edited
    files, so the untouched copy in the backup folder is used whenever it exists."""
    files = sorted(p for p in Path(game_dir).glob("DataPC_*sound_eng*.forge")
                   if not re.search(r"-\d+\.forge$", p.name))
    if not files:
        sys.exit(f"No English voice archives (DataPC_*sound_eng*.forge) found in: {game_dir}")
    return [BACKUP_DIR / f.name if (BACKUP_DIR / f.name).exists() else f for f in files]


# ---------------- finding the game ----------------

def _candidates():
    drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")] if os.name == "nt" else ["/"]
    parents = [
        r"Program Files (x86)\Ubisoft\Ubisoft Game Launcher\games",
        r"Program Files\Ubisoft\Ubisoft Game Launcher\games",
        r"Ubisoft Connect", r"Ubisoft\Ubisoft Game Launcher\games", r"Games\Ubisoft Connect",
        r"Program Files (x86)\Steam\steamapps\common", r"Program Files\Steam\steamapps\common",
        r"SteamLibrary\steamapps\common", r"Steam\steamapps\common", r"Games\SteamLibrary\steamapps\common",
        r"Program Files\Epic Games", r"Epic Games", r"Games\Epic Games",
    ]
    for d in drives:
        for p in parents:
            for name in GAME_DIR_NAMES:
                yield Path(d) / p / name


def find_game(saved=None):
    """Use the folder in game_folder.txt if it's valid, otherwise look in the usual places."""
    if saved:
        p = Path(saved.strip().strip('"'))
        if (p / "DataPC_boot_sound_eng.forge").exists():
            return p
    for c in _candidates():
        try:
            if (c / "DataPC_boot_sound_eng.forge").exists():
                return c
        except OSError:
            pass
    return None


# ---------------- apply (for everyone) ----------------

def apply_map(game_dir, map_file, out_dir):
    m = json.loads(Path(map_file).read_text(encoding="utf-8"))
    if m.get("format") not in (1, FORMAT):
        sys.exit("This mute map was made by a different version of the tool.")
    try:
        from .edit import apply_plan  # needs numpy + av for close-gap / splice
        smart = True
    except ImportError:
        smart = False
        if any(c.get("ops") for c in m["clips"]):
            print("Note: numpy/av aren't installed, so words will be muted instead of cut/spliced.\n")

    archives = voice_archives(game_dir)
    blobs = {a.name: a.read_bytes() for a in archives}
    index = {}                                   # (subtitle id, fingerprint) -> [(archive, off, size, wem)]
    for name, src in blobs.items():
        for off, size, w in find_clips(src):
            index.setdefault((w.oasis_id, fingerprint(src[off:off + size])), []).append((name, off, size, w))

    def donor_pcm(d):
        hit = index.get((d["id"], d["fp"]))
        if not hit:
            return None
        from .wem import decode_pcm48, Wem
        name, off, size, _ = hit[0]
        return decode_pcm48(Wem.parse(blobs[name][off:off + size]))[0]

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    edited = {a.name: bytearray(blobs[a.name]) for a in archives}
    matched = 0
    for c in m["clips"]:
        hits = index.get((c["id"], c["fp"]))
        if not hits:
            continue
        matched += 1
        for name, off, size, _ in hits:
            from .wem import Wem
            w = Wem.parse(blobs[name][off:off + size])
            try:
                if smart:
                    apply_plan(w, c, donor_pcm)
                else:
                    mute_frames(w, c["frames"])
                edited[name][off:off + size] = build_same_size(blobs[name][off:off + size], w)
            except (ValueError, StopIteration, KeyError):
                continue

    written = []
    for a in archives:
        if edited[a.name] != blobs[a.name]:
            (out / a.name).write_bytes(edited[a.name])
            written.append(a.name)
            print(f"  {a.name}: cleaned")
    missing = len(m["clips"]) - matched
    print(f"\n{matched} of {len(m['clips'])} lines in the map matched your game.")
    if missing:
        print(f"{missing} didn't match - your game version may differ from the one this map was made for.")
        print("Those lines were left untouched. Check for an updated map, or use the full scan.")
    return written
