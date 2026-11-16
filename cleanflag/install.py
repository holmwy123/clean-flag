"""One-click install / uninstall of the edited voice files.

install:   backs up each original game file to clean-flag\\backup (only the first time,
           so the backup is always the real original), then copies the edited file in.
uninstall: copies the originals back from the backup.

Standard library only.
"""
import re
import shutil
import sys
from pathlib import Path


def _copy(src, dst):
    try:
        shutil.copy2(src, dst)
    except PermissionError:
        sys.exit(f"\nCan't write {dst.name} - is the game (or Ubisoft Connect's verify) still running? "
                 "Close it and try again.")


def install(game_dir, out_dir, backup_dir):
    game, out, bak = Path(game_dir), Path(out_dir), Path(backup_dir)
    files = sorted(p for p in out.glob("*.forge") if not re.search(r"-\d+\.forge$", p.name))
    if not files:
        sys.exit("No edited files found. Run the build (or apply the mute map) first.")
    bak.mkdir(parents=True, exist_ok=True)

    strays = [p.name for p in game.glob("DataPC_*sound_eng*-[0-9].forge")]
    if strays:
        print("Warning: these extra copies in your game folder may override the mod:")
        for s in strays:
            print("   " + s)
        print("Move them out of the game folder if voices sound wrong.\n")

    for f in files:
        target = game / f.name
        saved = bak / f.name
        if not saved.exists():
            manual = game / (f.name + ".bak")          # left over from a manual install
            original = manual if manual.exists() else target
            if not original.exists():
                print(f"  skipped {f.name}: not in your game folder")
                continue
            _copy(original, saved)
            print(f"  backed up original {f.name}")
        _copy(f, target)
        print(f"  installed {f.name}")
    print(f"\nDone! Originals are saved in: {bak.resolve()}")
    print("Launch the game as normal. To undo, run the uninstaller.")


def uninstall(game_dir, backup_dir):
    game, bak = Path(game_dir), Path(backup_dir)
    files = sorted(bak.glob("*.forge"))
    if not files:
        sys.exit("Nothing to restore - no backup found.")
    for f in files:
        _copy(f, game / f.name)
        print(f"  restored original {f.name}")
    print("\nThe game is back to its original voice files.")
    print("(The backup is kept. If anything still seems off, use Verify files in Ubisoft Connect.)")
