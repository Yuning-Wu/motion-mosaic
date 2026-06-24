# Remask Annotator

Local tool for marking mosaic rectangles on static images and animated GIF/WebP files, then exporting static images as WebP and animated files as WebP by default or WebM when selected.

## Layout

- `annotator/` - browser UI and local HTTP service
- `apply_thick_mosaic.py` - mosaic rendering plus WebP/WebM compression
- `data/` - local config, annotations, and extracted frames
- `exports/` - default export root
- `inputs/` - optional local input folder
- `legacy/` - migrated historical files from the old task folder

`data/`, `exports/`, `inputs/`, and `legacy/` are ignored by git.

## Run web server

```powershell
python .\annotator\server.py
```

Open `http://127.0.0.1:8788/`.

## Run app window

```powershell
python .\launch_remask_annotator.py
```

This starts the local service and opens the same UI in an app-style Edge window. The web UI remains available at `http://127.0.0.1:8788/`.

## Windows exe

Build a double-clickable launcher:

```powershell
.\build_exe.ps1
```

Then run:

```powershell
.\RemaskAnnotator.exe
```

The exe starts the local service and opens an app-style Edge window. The web UI remains available at `http://127.0.0.1:8788/`. It keeps `data/`, `exports/`, and `inputs/` next to the exe.
