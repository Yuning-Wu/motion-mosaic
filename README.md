# Remask Annotator

Remask Annotator is a local annotation and mosaic export tool for images and short animated assets. It runs as a lightweight local web service and can also open the same UI in an app-style desktop window.

## Features

- Annotate mosaic rectangles on static images and animated GIF/WebP sources.
- Export static images as WebP.
- Export animated assets as AVIF by default, with WebP and WebM also available.
- Keep annotations, config, extracted frames, and exports in local folders.
- Run as a normal browser app, an app-style Edge window, or a single Windows executable.

## Architecture

The project intentionally keeps a small Python stack:

- `annotator/index.html` contains the browser-based annotation UI.
- `annotator/server.py` exposes the local HTTP API with Bottle and serves the UI/files.
- `apply_thick_mosaic.py` applies mosaic masks and handles WebP/AVIF/WebM export.
- `launch_remask_annotator.py` starts the local service and opens an app-style Edge window.
- `app_paths.py` resolves runtime paths for source and packaged execution.
- `build_exe.ps1` builds a Windows executable with PyInstaller.

Runtime data is kept out of Git:

- `data/` stores config, annotations, and extracted frames.
- `exports/` stores rendered outputs.
- `inputs/` is an optional local input folder.
- `legacy/` is reserved for migrated local files.

## Requirements

- Python 3.10+
- Microsoft Edge, for app-style window mode on Windows
- ffmpeg, required for animated AVIF/WebM export

Install Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

## Run

Start the local web server:

```powershell
python .\annotator\server.py
```

Open:

```text
http://127.0.0.1:8788/
```

Start the app-style desktop window:

```powershell
python .\launch_remask_annotator.py
```

The same web UI remains available at `http://127.0.0.1:8788/`.

## Build Windows Exe

Build the executable:

```powershell
.\build_exe.ps1
```

Run it:

```powershell
.\RemaskAnnotator.exe
```

The executable starts the local service and opens the app-style window. It keeps `data/`, `exports/`, and `inputs/` next to the executable.

## Configuration

Optional environment variables:

- `REMASK_ANNOTATOR_HOME` changes the runtime data root.
- `REMASK_ANNOTATOR_HOST` changes the server host, default `127.0.0.1`.
- `REMASK_ANNOTATOR_PORT` changes the server port, default `8788`.
- `REMASK_ANNOTATOR_SERVER_ONLY=1` starts only the server.
- `REMASK_ANNOTATOR_OPEN_BROWSER=1` opens the default browser instead of the Edge app window.
- `REMASK_ANNOTATOR_NO_WINDOW=1` starts without opening a window.
- `REMASK_ANNOTATOR_EDGE` sets a custom Edge executable path.

## Distribution

Generated files such as `RemaskAnnotator.exe`, `build/`, `data/`, and `exports/` are ignored by Git. Keep source code and build scripts in the repository, then publish executable builds through GitHub Releases or CI artifacts.
