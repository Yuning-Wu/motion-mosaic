# Motion Mosaic

Motion Mosaic is a local mosaic annotation and export tool for static images and animated GIF/WebP assets. It lets you mark mosaic rectangles once, preview them frame by frame, and export compact WebP, AVIF, or WebM outputs.

It is designed as a single-user local desktop utility. The built-in HTTP server binds to `127.0.0.1` by default, stores data on the local machine, and is not intended to be exposed as a shared multi-user web service.

## Features

- Annotate mosaic rectangles and polygons on static images and animated sources.
- Automatically import and refresh selected source files.
- Review animated assets frame by frame.
- Scrub the timeline to jump directly to the matching animation frame.
- Keep the full image fitted in the workspace, with Ctrl + mouse wheel zoom when needed.
- Apply the current frame's selected mosaic regions to a target range in one click.
- Export static images as WebP.
- Export animated assets as AVIF by default, with WebP and WebM also available.
- Configure export format, automatic target-size compression, and shortcuts from the settings panel.
- Preview the final rendered output directly after export.
- Run in a native desktop window or through the local web UI.
- Keep all annotations, extracted frames, and exports local.

## Quick Start

For normal use, download or build `MotionMosaic.exe`, place it in a fixed folder, and double-click it. Then choose a source image folder, mark the mosaic areas, mark finished files as complete, and export completed files. Outputs are written to the configured export directory, which defaults to `exports` next to the executable.

For development, install the Python dependencies and run the launcher:

```powershell
python -m pip install -r requirements.txt
python .\launch_motion_mosaic.py
```

## Architecture

The project keeps a small Python stack:

- `annotator/index.html` contains the browser-based annotation UI.
- `annotator/server.py` exposes the local HTTP API with Bottle and serves UI assets.
- `apply_thick_mosaic.py` applies mosaic masks and handles WebP/AVIF/WebM export.
- `launch_motion_mosaic.py` starts the local service and opens a pywebview desktop window.
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
- Microsoft Edge WebView2 Runtime, for the native desktop window on Windows
- ffmpeg, required for animated AVIF/WebM export. Static WebP and animated WebP export use Pillow and do not require ffmpeg.

Motion Mosaic looks for ffmpeg in this order:

1. `MOTION_MOSAIC_FFMPEG`, if set.
2. `bin\ffmpeg.exe` next to the project or packaged executable.
3. `ffmpeg` on the system `PATH`.

Optional: `webpmux` can improve animated WebP frame-duration detection. It is found through `MOTION_MOSAIC_WEBPMUX`, `bin\webpmux.exe`, or the system `PATH`.

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

Start the desktop window:

```powershell
python .\launch_motion_mosaic.py
```

The same web UI remains available at `http://127.0.0.1:8788/`.

`MOTION_MOSAIC_HOST` and `MOTION_MOSAIC_PORT` are honored by both the desktop launcher and direct server mode.

## Build Windows Exe

Build the executable:

```powershell
.\build_exe.ps1
```

By default, the build bundles `ffmpeg.exe` into the one-file executable when it can find ffmpeg locally. This keeps AVIF/WebM export working on a colleague's computer without installing Python or ffmpeg. If ffmpeg is not on `PATH`, pass it explicitly:

```powershell
.\build_exe.ps1 -FfmpegPath C:\tools\ffmpeg\bin\ffmpeg.exe
```

Use `-NoBundleTools` only for developer builds that should rely on the local machine environment.

Run it:

```powershell
.\MotionMosaic.exe
```

The executable starts the local service and opens the native desktop window. If the native WebView backend is unavailable, it falls back to the default browser. It keeps `data/`, `exports/`, and `inputs/` next to the executable.

Recommended update flow after code changes:

```powershell
git pull
.\build_exe.ps1 -SkipInstall
.\MotionMosaic.exe
```

Close any running `MotionMosaic.exe` window before rebuilding, because Windows may keep the executable locked while it is running.

Package a novice-friendly ZIP:

```powershell
.\package_release.ps1
```

The ZIP contains `MotionMosaic.exe` and `同事使用说明.md`.

## Configuration

Optional environment variables:

- `MOTION_MOSAIC_HOME` changes the runtime data root.
- `MOTION_MOSAIC_HOST` changes the server host, default `127.0.0.1`.
- `MOTION_MOSAIC_PORT` changes the server port, default `8788`.
- `MOTION_MOSAIC_SERVER_ONLY=1` starts only the server.
- `MOTION_MOSAIC_OPEN_BROWSER=1` opens the default browser instead of the desktop window.
- `MOTION_MOSAIC_NO_WINDOW=1` starts without opening a window.
- `MOTION_MOSAIC_FFMPEG` points to a custom ffmpeg executable.
- `MOTION_MOSAIC_WEBPMUX` points to a custom webpmux executable.

The previous `REMASK_ANNOTATOR_*` variables are still accepted for local compatibility.

## Troubleshooting

- If the desktop window cannot start, the app falls back to the default browser while still running locally.
- If port `8788` is already in use, set `MOTION_MOSAIC_PORT` before starting the launcher.
- If animated AVIF/WebM export fails, confirm that `ffmpeg.exe` is bundled in the build or available through `MOTION_MOSAIC_FFMPEG`, `bin\ffmpeg.exe`, or `PATH`.
- If a source image was changed after import, use the UI refresh/import action so extracted frames match the current file.

## Distribution

Generated files such as `MotionMosaic.exe`, `build/`, `data/`, and `exports/` are ignored by Git. Keep source code and build scripts in the repository, then publish executable builds through GitHub Releases or CI artifacts.
