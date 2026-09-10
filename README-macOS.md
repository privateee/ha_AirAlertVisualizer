# Running DroneVisualizer on macOS

The app is plain Python (FastAPI + Uvicorn) with no Windows-specific code, so
it runs on macOS unchanged. `DroneVisualizer.command` is the Mac equivalent of
`DroneVisualizer.bat` on Windows: it creates a private virtual environment on
first run, installs the dependencies, then starts the server and opens your
browser.

Works on both Apple Silicon (M-series) and Intel Macs.

---

## 1. Install Python 3.11 or newer

macOS ships an old Python that won't work. Get a current one:

**Homebrew** (recommended)

```bash
brew install python@3.12
```

**or** download the "macOS 64-bit universal2 installer" from
<https://www.python.org/downloads/macos/> and run it.

Check it worked:

```bash
python3 --version      # must say 3.11.x or higher
```

---

## 2. Get the project

If you cloned it with `git`, you already have it. Otherwise download the ZIP
from GitHub and unzip it somewhere permanent (e.g. `~/Apps/DroneVisualizer`) —
**not** the Downloads folder.

---

## 3. Start it

Double-click **`DroneVisualizer.command`** in Finder.

* **First run** takes a minute or two while it builds `.venv/` and downloads
  packages. A Terminal window shows the progress.
* When you see `Uvicorn running on http://127.0.0.1:8750`, your browser opens
  the map automatically.
* Leave the Terminal window open while you use the app.
* To **stop**: press `Ctrl-C` in that window, or just close it.
* Later runs start in a second or two (no re-install).

### "DroneVisualizer.command can't be opened" (Gatekeeper)

If you downloaded the project as a ZIP, macOS quarantines the script. The first
time only:

* **Right-click** `DroneVisualizer.command` -> **Open** -> **Open** in the dialog.

or clear the flag from Terminal:

```bash
xattr -d com.apple.quarantine DroneVisualizer.command
```

If double-click still does nothing, make sure it's executable:

```bash
chmod +x DroneVisualizer.command
```

---

## Running from the command line instead

```bash
cd /path/to/DroneVisualizer
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m dronevis run
```

Other commands (same as on any platform):

```bash
./.venv/bin/python -m dronevis ingest              # fetch new posts once
./.venv/bin/python -m dronevis reparse --since-hours 6
./.venv/bin/python -m dronevis parse "1x shahed course on Kyiv"
./.venv/bin/python -m dronevis stats
```

---

## Configuration

Copy `config.example.yaml` to `config.yaml` and edit it (it's git-ignored).
Everything can also be set with `DRONEVIS_*` environment variables, e.g. in the
Terminal before launching:

```bash
export DRONEVIS_CHANNELS="war_monitor,kpszsu"
export DRONEVIS_AREA_CENTER="49.99,36.23"
export DRONEVIS_PORT=8750
```

By default the server listens on `127.0.0.1` (this Mac only). To reach it from
your phone on the same Wi-Fi, set `server.host: 0.0.0.0` in `config.yaml` (or
`export DRONEVIS_HOST=0.0.0.0`) and open `http://<your-mac-ip>:8750` on the
phone. The SQLite database lives in `data/dronevis.db`.

---

## Notes

* No admin rights needed; nothing is installed system-wide - it all lives in
  the project's `.venv/` folder. Delete that folder to reset.
* A frozen single-file `.app` (PyInstaller) is possible but must be built on a
  Mac and code-signed to avoid Gatekeeper friction; the `.command` script
  avoids all of that.
