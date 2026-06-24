# Motion Mosaic

Motion Mosaic is a local mosaic annotation and export tool for static images and animated GIF/WebP assets. It lets you mark mosaic rectangles once, preview them frame by frame, and export compact WebP, AVIF, or WebM outputs.

## Features

- Annotate mosaic rectangles on static images and animated sources.
- Review animated assets frame by frame.
- Export static images as WebP.
- Export animated assets as AVIF by default, with WebP and WebM also available.
- Run in a browser, an app-style Edge window, or a Windows executable.
- Keep all annotations, extracted frames, and exports local.

## Architecture

The project keeps a small Python stack:

- `annotator/index.html` contains the browser-based annotation UI.
- `annotator/server.py` exposes the local HTTP API with Bottle and serves UI assets.
- `apply_thick_mosaic.py` applies mosaic masks and handles WebP/AVIF/WebM export.
- `launch_motion_mosaic.py` starts the local service and opens an app-style Edge window.
- `app_paths.py` resolves runtime paths for source and packaged execution.
- `assets/` stores the app icon and favicon.
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
python .\launch_motion_mosaic.py
```

The same web UI remains available at `http://127.0.0.1:8788/`.

## Build Windows Exe

Build the executable:

```powershell
.\build_exe.ps1
```

Run it:

```powershell
.\MotionMosaic.exe
```

The executable starts the local service and opens the app-style window. It keeps `data/`, `exports/`, and `inputs/` next to the executable.

## Configuration

Optional environment variables:

- `MOTION_MOSAIC_HOME` changes the runtime data root.
- `MOTION_MOSAIC_HOST` changes the server host, default `127.0.0.1`.
- `MOTION_MOSAIC_PORT` changes the server port, default `8788`.
- `MOTION_MOSAIC_SERVER_ONLY=1` starts only the server.
- `MOTION_MOSAIC_OPEN_BROWSER=1` opens the default browser instead of the Edge app window.
- `MOTION_MOSAIC_NO_WINDOW=1` starts without opening a window.
- `MOTION_MOSAIC_EDGE` sets a custom Edge executable path.

The previous `REMASK_ANNOTATOR_*` variables are still accepted for local compatibility.

## Distribution

Generated files such as `MotionMosaic.exe`, `build/`, `data/`, and `exports/` are ignored by Git. Keep source code and build scripts in the repository, then publish executable builds through GitHub Releases or CI artifacts.
