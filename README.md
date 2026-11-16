# Clean Flag

**A swear filter for Assassin's Creed Black Flag Resynced (PC, English voices and subtitles).**

Clean Flag removes profanity from the game's English voice lines and cleans the subtitles to match, so you can enjoy the story with family around or just without the language. Nothing else changes. Music, sound effects and the rest of every line stay exactly as they were.

- About **515 swears** removed across **503 voice lines**, and **368 subtitle lines** cleaned to match.
- **Smart about religious words.** "Oh God!" and "for Christ's sake" are removed, but prayers, blessings and sincere lines like "God save the King" are kept.
- **British words like "bloody" and "bugger" are kept** by default. You can turn them on.
- **One-click install and uninstall.** The originals are backed up automatically.
- **Ships no game files.** The mod contains only edit instructions and applies them to your own copy of the game.

## Install

1. Download `CleanFlag.zip` from [Releases](https://github.com/holmwy123/clean-flag/releases) (or Nexus Mods) and unzip it somewhere permanent, for example `Documents\CleanFlag`.
2. Close the game.
3. Double-click **`INSTALL.bat`**.
   - If Python isn't installed, it installs it for you with Windows' built-in `winget`.
   - It finds your game automatically (Ubisoft Connect, Steam or Epic). If it can't, it asks you to paste the game folder.
   - It takes a minute or two.
4. Play.

Keep the CleanFlag folder. It holds the backup of your original files.

## Uninstall

Double-click **`UNINSTALL.bat`**. "Verify files" in Ubisoft Connect (or Steam's "Verify integrity of game files") also restores everything.

## After a game update

An update may replace the cleaned files. Run `INSTALL.bat` again. Each voice clip is checked by fingerprint before it's edited, so a clip that changed in an update is left untouched instead of being edited in the wrong place.

## Settings

Open these in Notepad, then run `INSTALL.bat` again.

| File | Setting | What it does |
|---|---|---|
| `settings.json` | `"audio_mode": "close_gap"` | Cuts the word out and closes the gap (default). |
| `settings.json` | `"audio_mode": "mute"` | Silences the word instead. |
| `settings.json` | `"captions": false` | Leaves the subtitles alone. |
| `settings.json` | `"test_mode": true` | Subtitles show each removed word in `*asterisks*` where it was cut, so you can see exactly what changed. |
| `wordlist.json` | `"enabled": true/false` | Turns word groups on or off (strong, mild, british, innuendo, religious oaths). |

A line that is nothing but a swear ("Damn!") is muted and gets no subtitle.

## How it works

- **Voice lines.** The game's voice clips were transcribed with speech recognition, every flagged word was timed to within about 20 ms with forced alignment, and the result was checked against the subtitles. `mute_map.json` lists which moments of which clip to cut, identified by the clip's subtitle ID and a fingerprint of the original recording. No audio is included.
- **Subtitles.** At install time, the English subtitle package is read from your own `DataPC_boot_patch_01.forge`, cleaned with the same word rules, re-encoded with the game's own text dictionary (unchanged lines stay byte for byte), and written back with an exact undo record. No game text is included.

## Compatibility

- Made for game version **1.0.8 (TU6)**, English voices.
- **Ubisoft Connect: tested. Steam and Epic: should work, not yet confirmed.** If you try it on Steam or Epic, please report the "X of 503 lines matched" number the installer prints.
- Windows only. Steam Deck and Linux aren't supported.
- Only edits the English voice archives and the English subtitle package. Other mods that don't touch those files work alongside it.

## Credits

- Clean Flag by **holmwy**.
- Oodle decompression: [ooz](https://github.com/powzix/ooz) by powzix (GPL-3.0).
- Thanks to **NooB** and the reshax.com community for their research into the game's text format.

## License

GPL-3.0. See `LICENSE`. This mod is not affiliated with or endorsed by Ubisoft. Assassin's Creed is a trademark of Ubisoft Entertainment.
