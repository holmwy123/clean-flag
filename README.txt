CLEAN FLAG - a swear filter for Assassin's Creed Black Flag Resynced (PC, English)
==================================================================================

What it does
  * Cuts swear words out of the English voice lines. The rest of each line,
    the music and the sound effects are untouched.
  * Cleans the English subtitles to match what you now hear.
  * Religious words are only removed when used as exclamations ("oh God!",
    "for God's sake"). Prayers and sincere lines are kept.
  * British words like "bloody" and "bugger" are left in by default
    (turn them on in wordlist.json, category "british").

Install
  1. Close the game.
  2. Double-click INSTALL.bat. If Python isn't on your PC it installs it for you.
  3. It finds your game (Ubisoft Connect, Steam or Epic), cleans it, and backs up
     the originals into the "backup" folder here. Takes a minute or two.
  Keep this folder - it holds your backup and is needed to uninstall.

Uninstall
  Double-click UNINSTALL.bat. Ubisoft Connect's "Verify files" (or Steam's
  "Verify integrity") also restores everything.

After a game update
  Updates can replace the cleaned files. Just run INSTALL.bat again.

Settings (open in Notepad, then run INSTALL.bat again)
  settings.json  "audio_mode": "close_gap" (cut the word, default) or "mute" (silence it)
                 "captions": false to leave subtitles alone
                 "test_mode": true to show each removed word in *asterisks* in the
                 subtitles, so you can see exactly what changed
  wordlist.json  which word groups are filtered

Notes
  * No game files are included. The mod ships edit instructions (which moments
    of which voice clip to cut) and applies them to your own copy of the game.
    Each clip is checked by fingerprint, so a different game version is never
    mis-edited - unmatched clips are just left alone.
  * Made for game version 1.0.8 (TU6), English voices.
  * Ubisoft Connect: tested. Steam and Epic: should work, not yet confirmed.

Credits
  Clean Flag by holmwy - https://github.com/holmwy123/clean-flag
  Oodle decoding: ooz by powzix (https://github.com/powzix/ooz), GPL-3.0.
  Thanks to NooB and the reshax.com community for their research into the
  game's text format.
  Licensed under GPL-3.0 (see LICENSE). Not affiliated with Ubisoft.
