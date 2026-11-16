"""Clean Flag - profanity filter for Assassin's Creed Black Flag Resynced.

Commands:
  scan                 Transcribe every English voice clip (slow, once, resumable)
  build                Mute flagged words -> edited archive copies (fast)
  export-map [file]    Save a shareable mute map (no game content) from your scan
  apply-map  [file]    Clean your game using a mute map (no scan, no extra installs)
  preview [n]          Write before/after WAVs of some edited lines to output\\preview
  install              Back up originals and copy the edited files into the game
  uninstall            Put the original files back

The game folder is read from game_folder.txt, or found automatically, or given with --game.
  check  "<some sentence>"           Quick test of the word list
  text   <in_file> <out_file>        Clean a text file line by line
  probe  <game_folder>               Report the game's file formats (read-only)

The game folder is only ever read. You copy the output files in yourself.
"""
import argparse
from pathlib import Path

from .words import DEFAULT_LIST, WordFilter

HERE = Path(__file__).resolve().parent.parent


def main():
    p = argparse.ArgumentParser(prog="cleanflag", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--wordlist", default=str(DEFAULT_LIST))
    p.add_argument("--work", default=str(HERE / "work"), help="where scan results are kept")
    p.add_argument("--game", help="game folder (otherwise game_folder.txt, otherwise auto-detect)")
    p.add_argument("--out", default=str(HERE / "output"), help="where edited files are written")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan"); s.add_argument("--model", default="large-v3-turbo")
    s = sub.add_parser("build"); s.add_argument("--pad-ms", type=int, default=60)
    s = sub.add_parser("export-map"); s.add_argument("file", nargs="?", default=str(HERE / "mute_map.json")); s.add_argument("--pad-ms", type=int, default=60)
    s = sub.add_parser("apply-map"); s.add_argument("file", nargs="?", default=str(HERE / "mute_map.json"))
    sub.add_parser("install"); sub.add_parser("uninstall"); sub.add_parser("captions")
    sub.add_parser("setup"); sub.add_parser("remove")
    s = sub.add_parser("preview"); s.add_argument("count", nargs="?", type=int, default=12)
    s = sub.add_parser("check"); s.add_argument("sentence"); s.add_argument("--style", default="replace", choices=["replace", "dash", "stars"])
    s = sub.add_parser("text"); s.add_argument("in_file"); s.add_argument("out_file"); s.add_argument("--style", default="replace", choices=["replace", "dash", "stars"])
    s = sub.add_parser("probe"); s.add_argument("game_folder"); s.add_argument("--report", default="probe_report.txt")
    a = p.parse_args()

    if a.cmd in ("scan", "build", "export-map", "apply-map", "install", "uninstall", "preview", "captions", "setup", "remove"):
        from .mutemap import find_game
        saved = a.game
        gf = HERE / "game_folder.txt"
        if not saved and gf.exists():
            saved = next((ln for ln in gf.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")), None)
        game = find_game(saved)
        while game is None and a.cmd in ("setup", "remove"):
            print("Couldn't find the game automatically.")
            ans = input("Paste the game's folder path (the one with ACBlackFlag.exe), or press Enter to quit: ").strip().strip('"')
            if not ans:
                raise SystemExit(1)
            game = find_game(ans)
            if game:
                gf.write_text(str(game) + "\n", encoding="utf-8")
        if game is None:
            raise SystemExit("Couldn't find the game. Put its folder path on a line in game_folder.txt "
                             "(the folder that contains DataPC_boot_sound_eng.forge) and try again.")
        print(f"Game folder: {game}\n")

    if a.cmd == "setup":
        from .mutemap import apply_map
        from .install import install
        from .edit import load_settings
        from . import subs
        print("Step 1 of 2: voice lines")
        apply_map(game, HERE / "mute_map.json", a.out)
        install(game, a.out, HERE / "backup")
        if load_settings(HERE).get("captions", True):
            print("\nStep 2 of 2: subtitles")
            subs.install(game, HERE / "backup" / "subtitles_undo.json", test=bool(load_settings(HERE).get("test_mode")))
        print("\nAll done. Start the game as normal.")
        return
    if a.cmd == "remove":
        from .install import uninstall
        from . import subs
        subs.uninstall(game, HERE / "backup" / "subtitles_undo.json")
        uninstall(game, HERE / "backup")
        return
    if a.cmd == "scan":
        from .game import scan
        scan(game, a.work, a.model)
    elif a.cmd == "build":
        from .game import build
        build(game, a.work, a.out, a.wordlist, HERE, HERE / "mute_map.json")
    elif a.cmd == "export-map":
        from .game import build
        build(game, a.work, a.out, a.wordlist, HERE, a.file)
    elif a.cmd == "apply-map":
        from .mutemap import apply_map
        apply_map(game, a.file, a.out)
    elif a.cmd == "preview":
        from .preview import preview
        preview(game, a.out, a.count)
    elif a.cmd == "captions":
        import json as _json
        from .edit import load_settings as _ls
        if not _ls(HERE).get("captions", False):
            print('Subtitle cleaning is currently disabled ("captions": false in settings.json).')
            return
        from .captions import load_audio_report, patch_forge_captions
        from .edit import load_settings
        wf = WordFilter(a.wordlist)
        report = load_audio_report(a.out)
        known = set()
        tr = Path(a.work) / "transcripts.jsonl"
        if tr.exists():
            for line in open(tr, encoding="utf-8"):
                r = _json.loads(line)
                if r.get("oasis"):
                    known.add(int(r["oasis"], 16))
        test = bool(load_settings(HERE).get("test_mode"))
        only = None
        scope = Path(a.work) / "caption_test_ids.json"
        if scope.exists():
            only = {int(x, 16) for x in _json.loads(scope.read_text())}
            print(f"TEST SCOPE: only the opening scene's {len(only)} lines will be touched.")
            print("(Delete work\\caption_test_ids.json and rerun for the full game.)")
        undo = HERE / "backup" / "captions_undo.json"
        undo.parent.mkdir(exist_ok=True)
        total = {"lines": 0, "entries": 0}
        for tag, fname in (("patch", "DataPC_boot_patch_01.forge"), ("boot", "DataPC_boot.forge")):
            lst = Path(a.work) / f"cap_entries_{tag}.jsonl"
            fp = Path(game) / fname
            if not lst.exists() or not fp.exists():
                print(f"  {fname}: no caption list yet - skipped")
                continue
            ents = [_json.loads(l)["entry"] for l in open(lst)]
            st = patch_forge_captions(fp, ents, wf, report, known, undo, test, only)
            print(f"  {fname}: {st['lines']} captions cleaned in {st['entries']} files")
            total["lines"] += st["lines"]
        print(f"Captions done{' (TEST MODE markers on)' if test else ''}.")
    elif a.cmd == "install":
        from .install import install
        install(game, a.out, HERE / "backup")
    elif a.cmd == "uninstall":
        from .install import uninstall
        uninstall(game, HERE / "backup")
        from .captions import undo_forge_captions
        undo_forge_captions(game, HERE / "backup" / "captions_undo.json")
    elif a.cmd == "check":
        wf = WordFilter(a.wordlist)
        print("found:   ", [h.word for h in wf.find(a.sentence)])
        print("captions:", wf.clean_text(a.sentence, a.style))
    elif a.cmd == "text":
        wf = WordFilter(a.wordlist)
        lines = Path(a.in_file).read_text(encoding="utf-8").splitlines(keepends=True)
        out = [wf.clean_text(x, a.style) for x in lines]
        Path(a.out_file).write_text("".join(out), encoding="utf-8")
        print(f"{sum(x != y for x, y in zip(lines, out))} of {len(lines)} lines changed -> {a.out_file}")
    elif a.cmd == "probe":
        from .probe import probe
        r = probe(a.game_folder)
        Path(a.report).write_text(r, encoding="utf-8")
        print(r)


if __name__ == "__main__":
    main()
