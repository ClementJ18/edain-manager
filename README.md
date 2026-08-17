# edain-manager

Internal Flask app for the Edain Mod team. It drives the Taiga bug board around a
release, and offers a public page that applies pySAGE's engine patches to an
uploaded `game.dat`.

The release build pipeline that used to live here (packing `.big` archives and
uploading them) has been removed; see git history if it is ever needed again.

## Pages

| Page | Who | What |
| --- | --- | --- |
| `/release`, `/beta` | team | Rotate the "current bugs" epic and move the tickets on: `fixed-internally` → `in-test` for a beta, `awaiting-release` → `done` for a release. |
| `/bugs` | team | The fixed-bug list from the last release. |
| `/patch` | anyone | Apply pySAGE patches to an uploaded `game.dat`. Public on purpose — the patches help any ROTWK mod, and attribution is what is asked in return. |
| `/webhook/<secret>` | Taiga | Relays ticket creations and comments to Discord. |

`cli.py` handles one-off board maintenance: `sort`, `attach_tickets`,
`auto_move_tested`.

## Setup

```sh
python -m venv env
env/Scripts/activate          # Windows; use env/bin/activate elsewhere
pip install -r requirements.txt
cp taiga/template_config.py taiga/config.py
```

Fill in `taiga/config.py` — it is gitignored and holds the Discord OAuth and Taiga
credentials, the role IDs, and the board's status mappings. Two entries are easy to
get wrong:

- `APP_SECRET` — signs Flask sessions, so it must be real random bytes.
- `DEBUG` — bypasses **all** Discord authorization. Local use only.

`pysage-tools` installs from git and needs Python >= 3.12. Without it the app still
runs and `/patch` reports itself unavailable.

## Running

```sh
python app.py                 # dev server on https://127.0.0.1:5000 (self-signed)
```

`CLIENT_CALLBACK` must match the Discord application's redirect URI exactly.

In production it runs as a gunicorn systemd unit behind nginx:

```ini
[Service]
User=pi
Group=www-data
WorkingDirectory=/home/pi/edain-manager
ExecStart=/home/pi/edain-manager/env/bin/gunicorn --workers 1 --bind unix:edain-manager.sock -m 007 app:app
```

Logs go to the journal (`journalctl -u <unit> -f`).

**Keep `--workers 1`.** Flows are serialised by an in-process lock, so extra
workers would let two of them move the same tickets at once.

nginx needs `client_max_body_size` at least as large as `patching.MAX_UPLOAD_BYTES`
(128M), or it returns its own 413 before Flask sees the upload — and its default is
1M. The limit covers the whole submission rather than each file, and picking patches
for `game.dat`, the launcher and `Worldbuilder.exe` at once sends all three (~40M).

## Notes

- Flows run in a background thread, so a submission returns immediately and
  progress is appended to `release_log.txt`.
- `report.txt` (behind `/bugs`) is written by the release flow, so it is absent
  until one has run.
- Patched binaries sit in `$TMPDIR/edain-patcher` for 30 minutes and are swept
  when someone next visits `/patch`.
