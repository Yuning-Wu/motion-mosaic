# Remask Annotator

Local tool for marking mosaic rectangles on static images and animated GIF/WebP files, then exporting compressed WebP files.

## Layout

- `annotator/` - browser UI and local HTTP service
- `apply_thick_mosaic.py` - mosaic rendering and WebP compression
- `data/` - local config, annotations, and extracted frames
- `exports/` - default export root
- `inputs/` - optional local input folder
- `legacy/` - migrated historical files from the old task folder

`data/`, `exports/`, `inputs/`, and `legacy/` are ignored by git.

## Run

```powershell
python .\annotator\server.py
```

Open `http://127.0.0.1:8788/`.
