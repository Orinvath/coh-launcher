# Homecoming Launcher

A desktop launcher for Homecoming. It runs the official `launcher.exe` through Proton, so the official launcher still does all patching, downloading and game starting. This launcher adds a skinnable front end. It has no auth or patch code of its own.

Status: **built; automated checks run by the pipeline Verifier; independent Planner evaluation pending.** The real game launch is an owner smoke test that is still pending. Nothing in this README claims a live game launch was tested.

## Prerequisites

- Linux (Fedora/Nobara layout). Windows is not supported in this release.
- Python 3.14 with PySide6 6.11.2 (see `requirements.txt`).
- Google Chrome at `/usr/bin/google-chrome`, used only for browser links.
- `xdg-open` at `/usr/bin/xdg-open`, used to open the screenshots folder.
- Steam Proton, an installed Homecoming game, and its compatibility prefix.
- In the game folder, the official files the launcher calls: `bin/win64/launchercli.exe` and `settings/launcher/profiles/`. The launcher checks these before it enables Play.

Install the Python dependency yourself. The launcher never installs packages:

```sh
pip install -r requirements.txt
```

## Run

```sh
./run.sh
```

The app-menu entry is **COH Launcher**, with a Statesman mask icon. Its installed desktop file is `~/.local/share/applications/coh-launcher.desktop`; the project copy remains `Homecoming Launcher.desktop`. Both point to this folder’s `run.sh`. Update the paths if the project moves. Qt uses the same icon and desktop ID for taskbar association. Installation ownership and hashes are recorded in `var/desktop-integration-manifest.json`; the original project shortcut and app module are backed up under `var/desktop-integration-backup/`. The separate City of Heroes game shortcut is unchanged.

`app-run.json` holds the start command and a picture command (`python3 tools/picture.py <out.png>`) that opens the real window on a hidden screen with stand-in data, saves it, and closes everything it started.

Smoke test (fixture adapters, no game effects). It saves one screenshot inside the project and exits:

```sh
./run.sh --smoke-test --screenshot pipeline/launcher-001/screens/smoke.png
```

## Settings

Settings live in `var/config.json` inside this folder (or in the folder given with `--config-dir`). The file is written with mode 600 and is excluded from git. It is version 2. It holds paths and choices only, never passwords, tokens or the Fish Audio key. A version 1 file is read as it is and becomes version 2 the next time you press Save.

Settings has two tabs.

**General**
- Game installation: must contain `bin/win64/launchercli.exe` and `settings/launcher/profiles`.
- Proton executable: a file.
- Compatibility prefix: a folder that contains `pfx/drive_c`.
- Skin and window scale (0.75, 1, 1.25, 1.5).

A box may be left empty. On a fresh PC the three paths start from the usual Homecoming-on-Proton places in the folder named by `HOME` (the newest `Proton <version>` under `~/.local/share/Steam/steamapps/common`), or empty when nothing is there. An empty or wrong path sends you back to Settings, and Play waits for the paths.

**Add-ons**, with a Game watch section: Show watch, Start automatically, Alert chime, Speak alerts, the Fish Audio key and the Voice. See the next section.

If a saved file is unreadable, it is kept as `config.invalid-<timestamp>.json` beside it and starting values are loaded.

## Game watch

A small smart-watch face over the running game, always exactly 152 x 100 pixels. It shows the in-game time, and Day with a sun or Night with a moon. It appears only while the game is the window in front, at 20 pixels from the left and 16 from the top of the game's screen the first time, and you can drag it anywhere. It has three parts:

- the face (game time is worked out from the PC clock alone: one game day is 30 real minutes, midnight on every real hour and half hour, UTC);
- city alerts read from the game's own chat log (`accounts/<account>/Logs/chatlog <date>.txt`, read only, only new lines) and shown as a notification card, with a chime and rings around the watch. Turn on chat logging in the game's options;
- a Fish Audio voice that speaks each alert. The key is kept in the desktop's password store (the Secret Service: KDE Wallet or GNOME Keyring), never in a file. Spoken alerts are saved under `var/cache/voice` (50 MB at most).

On Linux the watch runs in a small helper program that the launcher starts with Qt's X11 platform, because the game is an X11 window. The helper closes when the watch is turned off or the launcher quits. The Linux-only pieces (the helper, the which-window-is-in-front check, finding the game program, the password store, the sound player, and looking for the starting folders) are in `launcher/linux/`; `launcher/pieces.py` says which pieces a system has.

## Tests

```sh
tests/run_all.sh
```

Every test runs on its own hidden screen (Xvfb) with stand-ins for the game, the password store and Fish Audio. They never use the real desktop, `var/config.json` or a real key.

## How the official launcher is called

Each operation starts the official executable directly, in text mode, as one shell-free argument list:

`<python> <protonPath> runinprefix <gameRoot>/bin/win64/launcher.exe -text <command> [profile]`

- Commands: `list`, `install`, `update`, `launch`. Profiles: `live`, `beta`.
- Working folder: `<gameRoot>/bin/win64`. Environment: a copy of yours, minus the three Linux Qt variables (`QT_QPA_PLATFORM`, `QT_QPA_PLATFORM_PLUGIN_PATH`, `QT_PLUGIN_PATH`), plus the two Steam compatibility variables set to the prefix.
- Output goes to a private capture file (mode 600) in `var/captures/`, not to an OS pipe. The launcher polls it for progress, and removes it when the job ends.
- **Open official launcher** starts the official GUI detached, with the same environment and no `-text`.

Rules for results:

- Exit code 0 alone is never success. Each command needs its own confirmation text, and failure text wins.
- A row with an unknown state is reported as an error. It is never treated as "not installed".
- `needsupdate` means installed with an update available. Play then runs update first, then launch.
- Progress shows the last whole percentage the official launcher prints. Until one appears, the bar is indeterminate. It shows 99% at most until the official success.
- Update and install show a receive rate and total. These come from the job's own process and its children only. See "Update telemetry" below.

## Timeouts and closing

- A timeout (90 s for list and launch; 10 min of silence for install and update) shows the official-launcher message. The process keeps running, and it keeps the busy lock until it really exits. Play and Beta stay disabled, and path settings stay locked.
- After a timeout, a late success never starts a game automatically. Press Play again.
- Closing while an operation runs hides the window. The official process is not killed. The launcher quits after the operation ends.

## Live news

- The hero and the four cards come from the official news feed. The launcher reads the feed page text in a separate, off-the-record Qt web view. It does not spoof a browser or bypass any block.
- The feed refreshes at startup and every 15 minutes. One request runs at a time, with a 20-second timeout. Only the exact feed address may load. Redirects to any other address fail.
- Items are checked before use. Links must be HTTPS on `forums.homecomingservers.com` or `homecomingservers.com`. Items with no valid link or date are skipped. A feed with no valid item counts as a failed refresh.
- The hero shows the first three items. The cards show items 2 to 5. The lead item uses the rooftop background.
- The last good feed is saved in `var/news-feed.json` (24-hour age is shown, not enforced). If a refresh fails, the saved news stays and is labelled `SAVED NEWS · <date>`.
- With no saved feed and no internet, the launcher shows a bundled snapshot of five items, labelled `OFFLINE NEWS`. The snapshot is never labelled LIVE.
- Descriptions are plain text, cut to 220 characters, and never rendered as HTML.
- The hero carousel changes slide every 8 seconds. It pauses on pointer or focus inside the hero, on a hidden page, while Settings or a dialog is open, and with reduced motion on. The three markers are buttons. Arrow keys, Home and End work on them.
- Clicking a link sends the article ID only. The launcher looks the URL up in its own list and opens it in Google Chrome in a new window. Success shows a 3-second notice. The page itself is not checked after the browser opens.

## Update telemetry

- While an update or install runs, the launcher samples the receive counter of TCP sockets once per second with `/usr/bin/ss`. The counter is read only for the job's process and the children it started, found from `/proc`.
- Other programs, the news feed and the browser are never counted. Nothing is captured or stored. Only a total and a rate leave the sampler.
- The rate is the change over the last three samples. The display uses decimal MB (1 MB = 1,000,000 bytes). If the rate cannot be measured, it shows `— MB/s`, never zero.
- Limit: if Wine moves a child process out of the job's process tree, its traffic is not counted. The rate then shows as unavailable or low. The official launcher's own percentage still shows.
- The official command line prints percentages in 10-point steps. It does not print byte counts. Byte figures come only from the socket counter above.

## Skins

A skin is a folder in `skins/`. Copy `skins/homecoming` to a new folder, edit `skin.json`, and restart the launcher. Settings lists every valid skin and any skin that failed.

`skin.json` must have exactly these keys: `version` (1), `id` (lowercase letters, digits, hyphens), `name`, `tokens`, and `assets`.

- `tokens` needs all nine colours, as six-digit hex (`#rrggbb`): `base`, `rail`, `card`, `border`, `text`, `muted`, `accent`, `accentDeep`, `online`.
- `assets` needs all seven keys: `background`, `logo`, `brand`, `feature1` to `feature4`. Each path must be relative, inside the skin folder, and must not use a link, `..`, a URL, or a `data:` URI. Each file must be PNG, JPEG or WebP and at most 20 MiB.

Skins supply data only. Scripts, custom CSS and ZIP import are not supported. Fonts are fixed under `ui/fonts`.

## Limitations

- The launcher is a frameless window. Resizing from the edges depends on your window manager.
- News depends on the official feed page being readable by the web engine. If the feed format changes, the launcher falls back to saved or bundled news.
- Telemetry shows the process tree only. Traffic from processes that were moved out of the tree is not shown.
- Shard status is read from the public endpoint. If it fails, the list shows Unknown, or Stale after an earlier success.
- The Windows adapter is not implemented.
- The real game launch has not been tested by the pipeline. It is an owner smoke test.

## Security

- No shell is ever used. Every external process gets a fixed argument list. Commands and profiles come from allowlists.
- The bridge accepts only named actions, checks each payload field, and refuses everything else. External links come only from a fixed allowlist.
- The web view loads only the launcher's own pages and skin images. It blocks remote navigation, new windows, downloads and permission prompts.
- Logs are kept in `var/logs`, capped at 256 KiB per job and 2 MiB in total. Values that look like tokens, passwords or authorization are redacted.

## Assets and licences

- Artwork is owner-supplied and listed with hashes in `skins/homecoming/SOURCE_ASSETS.md`.
- Fonts: Instrument Sans and Noto Sans ExtraCondensed, both under the SIL Open Font License 1.1. Licence texts are in `ui/fonts/`.
- No proprietary game files are packaged.
