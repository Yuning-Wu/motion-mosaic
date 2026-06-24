from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from bottle import Bottle, HTTPResponse, request, static_file
from PIL import Image, ImageSequence

SOURCE_ROOT = Path(__file__).resolve().parent.parent
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from app_paths import is_frozen, project_dir, resource_path

ROOT = Path(__file__).resolve().parent
WORK_DIR = project_dir()
PROJECT_DIR = WORK_DIR
DATA_DIR = WORK_DIR / "data"
FRAMES_DIR = DATA_DIR / "frames"
DEFAULT_SOURCE_DIR = PROJECT_DIR / "inputs"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "exports"
ANNOTATIONS_PATH = DATA_DIR / "annotations.json"
CONFIG_PATH = DATA_DIR / "config.json"
INDEX_PATH = resource_path("annotator", "index.html")
ASSETS_DIR = resource_path("assets")
ANIMATED_SOURCE_SUFFIXES = {".gif", ".webp"}
STATIC_SOURCE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
SUPPORTED_SOURCE_SUFFIXES = ANIMATED_SOURCE_SUFFIXES | STATIC_SOURCE_SUFFIXES
DEFAULT_ANIMATED_FORMAT = "avif"
ANIMATED_OUTPUT_FORMATS = {"avif", "webp", "webm"}
SOURCE_META_FILENAME = "_source.json"
RENDER_JOBS: dict[str, dict] = {}
RENDER_JOBS_LOCK = threading.Lock()
RENDER_JOB_TTL_SECONDS = 60 * 60
STORAGE_LOCK = threading.RLock()


def write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def default_config() -> dict:
    return {
        "sourceDir": str(DEFAULT_SOURCE_DIR),
        "outputDir": str(DEFAULT_OUTPUT_DIR),
        "activeFiles": None,
    }


def load_config() -> dict:
    config = default_config()
    with STORAGE_LOCK:
        if CONFIG_PATH.exists():
            try:
                raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    config.update(raw)
            except json.JSONDecodeError:
                pass

    active_files = config.get("activeFiles")
    if active_files is None:
        config["activeFiles"] = None
    elif not isinstance(active_files, list):
        config["activeFiles"] = None
    else:
        config["activeFiles"] = [str(item) for item in active_files if str(item).strip()]
    config["sourceDir"] = str(resolve_dir(config.get("sourceDir") or DEFAULT_SOURCE_DIR))
    config["outputDir"] = str(resolve_dir(config.get("outputDir") or DEFAULT_OUTPUT_DIR))
    return config


def save_config(config: dict) -> None:
    with STORAGE_LOCK:
        write_json_atomic(CONFIG_PATH, config)


def resolve_dir(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path.resolve()


def normalize_animated_format(value: str | None) -> str:
    normalized = (value or DEFAULT_ANIMATED_FORMAT).strip().lower()
    if normalized not in ANIMATED_OUTPUT_FORMATS:
        raise ValueError(f"animatedFormat must be one of: {', '.join(sorted(ANIMATED_OUTPUT_FORMATS))}")
    return normalized


def picker_initial_dir(value: str | Path) -> Path:
    try:
        path = resolve_dir(value)
    except (OSError, RuntimeError, ValueError):
        return Path.home()
    if path.is_dir():
        return path
    for parent in path.parents:
        if parent.is_dir():
            return parent
    return Path.home()


def pick_directory(title: str, initial_dir: str | Path) -> str:
    script = r'''
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = $env:MOTION_MOSAIC_PICKER_TITLE
$dialog.SelectedPath = $env:MOTION_MOSAIC_PICKER_INITIAL_DIR
$dialog.ShowNewFolderButton = $true
$result = $dialog.ShowDialog()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Write-Output $dialog.SelectedPath
}
'''
    env = {
        **os.environ,
        "MOTION_MOSAIC_PICKER_TITLE": title,
        "MOTION_MOSAIC_PICKER_INITIAL_DIR": str(picker_initial_dir(initial_dir)),
    }
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    return result.stdout.strip()


def load_annotations() -> dict:
    with STORAGE_LOCK:
        if not ANNOTATIONS_PATH.exists():
            return {"version": 1, "files": {}}
        try:
            data = json.loads(ANNOTATIONS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"version": 1, "files": {}}
    data.setdefault("version", 1)
    data.setdefault("files", {})
    return data


def save_annotations(data: dict) -> None:
    data.setdefault("version", 1)
    data.setdefault("files", {})
    with STORAGE_LOCK:
        write_json_atomic(ANNOTATIONS_PATH, data)


def source_files(source_dir: Path) -> list[Path]:
    if not source_dir.exists() or not source_dir.is_dir():
        return []
    return sorted(
        path
        for path in source_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SOURCE_SUFFIXES
    )


def create_export_output_dir() -> Path:
    export_root = resolve_dir(load_config().get("outputDir") or DEFAULT_OUTPUT_DIR)
    if export_root.exists() and not export_root.is_dir():
        raise NotADirectoryError(f"Output path is not a directory: {export_root}")
    export_root.mkdir(parents=True, exist_ok=True)
    base_name = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
    for index in range(1000):
        name = base_name if index == 0 else f"{base_name}-{index + 1}"
        path = export_root / name
        try:
            path.mkdir(parents=True, exist_ok=False)
            return path
        except FileExistsError:
            continue
    raise RuntimeError(f"Unable to create export directory under {export_root}")


def relative_source_name(path: Path, source_dir: Path) -> str:
    try:
        return path.relative_to(source_dir).as_posix()
    except ValueError:
        return path.name


def asset_id_for_source(path: Path, source_dir: Path) -> str:
    try:
        return path.relative_to(source_dir).with_suffix("").as_posix()
    except ValueError:
        return path.stem


def source_for_asset_id(asset_id: str, source_dir: Path) -> Path | None:
    raw_id = str(asset_id).replace("\\", "/").strip("/")
    for suffix in SUPPORTED_SOURCE_SUFFIXES:
        candidate = (source_dir / f"{raw_id}{suffix}").resolve()
        try:
            candidate.relative_to(source_dir.resolve())
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    for source in source_files(source_dir):
        if asset_id_for_source(source, source_dir) == raw_id:
            return source
    return None


def source_signature(path: Path) -> dict:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size": int(stat.st_size),
        "mtimeNs": int(stat.st_mtime_ns),
    }


def source_meta_path(asset_id: str) -> Path:
    return FRAMES_DIR / asset_id / SOURCE_META_FILENAME


def read_source_meta(asset_id: str) -> dict:
    path = source_meta_path(asset_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def write_source_meta(asset_id: str, source: Path, frame_count_written: int, kind: str) -> None:
    path = source_meta_path(asset_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        **source_signature(source),
        "frameCount": frame_count_written,
        "kind": kind,
        "loadedAt": time.time(),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def frame_source_stale(asset_id: str, source: Path | None) -> bool:
    if source is None or frame_count(asset_id) <= 0:
        return False
    meta = read_source_meta(asset_id)
    if not meta:
        return True
    signature = source_signature(source)
    return any(meta.get(key) != signature[key] for key in ("path", "size", "mtimeNs"))


def frame_asset_dirs() -> list[Path]:
    if not FRAMES_DIR.exists():
        return []
    return sorted(
        path
        for path in FRAMES_DIR.rglob("*")
        if path.is_dir() and any(path.glob("frame_*.png"))
    )


def asset_id_for_frame_dir(asset_dir: Path) -> str:
    try:
        return asset_dir.relative_to(FRAMES_DIR).as_posix()
    except ValueError:
        return asset_dir.name


def source_kind(path: Path) -> str:
    if path.suffix.lower() in STATIC_SOURCE_SUFFIXES:
        return "static"
    try:
        with Image.open(path) as image:
            if getattr(image, "is_animated", False) and getattr(image, "n_frames", 1) > 1:
                return "animated"
    except OSError:
        return "unreadable"
    return "static"


def cached_source_kind(asset_id: str, path: Path) -> str:
    meta = read_source_meta(asset_id)
    kind = meta.get("kind")
    if kind in {"animated", "static", "unreadable"}:
        signature = source_signature(path)
        if all(meta.get(key) == signature[key] for key in ("path", "size", "mtimeNs")):
            return kind
    return source_kind(path)


def frame_count(asset_id: str) -> int:
    frame_dir = FRAMES_DIR / asset_id
    if not frame_dir.is_dir():
        return 0
    return len(list(frame_dir.glob("frame_*.png")))


def annotated_frame_count(asset_id: str, annotations: dict) -> int:
    frames = annotations.get("files", {}).get(asset_id, {}).get("frames", {})
    if not isinstance(frames, dict):
        return 0
    return sum(1 for shapes in frames.values() if shapes)


def source_record(
    path: Path,
    source_dir: Path,
    annotations: dict,
    active_files: set[str],
    active_files_configured: bool,
) -> dict:
    asset_id = asset_id_for_source(path, source_dir)
    relative_name = relative_source_name(path, source_dir)
    frames = frame_count(asset_id)
    stat = path.stat()
    kind = cached_source_kind(asset_id, path)
    stale = frame_source_stale(asset_id, path)
    return {
        "id": asset_id,
        "name": relative_name,
        "baseName": path.name,
        "relativePath": relative_name,
        "suffix": path.suffix.lower(),
        "kind": kind,
        "path": str(path),
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "mtimeNs": stat.st_mtime_ns,
        "imported": frames > 0,
        "stale": stale,
        "frameCount": frames,
        "annotatedFrameCount": annotated_frame_count(asset_id, annotations),
        "active": asset_id in active_files if active_files_configured else frames > 0,
    }


def frame_asset_version(asset_id: str, asset_dir: Path) -> str:
    candidates = [source_meta_path(asset_id), asset_dir]
    version = 0
    for path in candidates:
        try:
            version = max(version, int(path.stat().st_mtime_ns))
        except OSError:
            continue
    return str(version or time.time_ns())


def frame_record(
    asset_dir: Path,
    annotations: dict,
    source_dir: Path,
    source_map: dict[str, Path],
    active_files: set[str],
    active_files_configured: bool,
) -> dict | None:
    frame_files = sorted(asset_dir.glob("frame_*.png"))
    if not frame_files:
        return None
    asset_id = asset_id_for_frame_dir(asset_dir)
    source = source_map.get(asset_id)
    source_name = relative_source_name(source, source_dir) if source else ""
    version = frame_asset_version(asset_id, asset_dir)
    return {
        "id": asset_id,
        "name": asset_id,
        "sourceName": source_name,
        "baseName": source.name if source else "",
        "kind": cached_source_kind(asset_id, source) if source else "unknown",
        "missingSource": source is None,
        "sourceStale": frame_source_stale(asset_id, source),
        "frameCount": len(frame_files),
        "annotatedFrameCount": annotated_frame_count(asset_id, annotations),
        "active": asset_id in active_files if active_files_configured else True,
        "frames": [f"/frame/{quote(asset_id, safe='/')}/{quote(frame.name)}?v={version}" for frame in frame_files],
    }


def build_workspace(config: dict | None = None, annotations: dict | None = None) -> dict:
    config = config if config is not None else load_config()
    annotations = annotations if annotations is not None else load_annotations()
    source_dir = resolve_dir(config["sourceDir"])
    sources = source_files(source_dir)
    source_map = {asset_id_for_source(path, source_dir): path for path in sources}
    active_files_configured = config.get("activeFiles") is not None
    active_files = set(config.get("activeFiles") or [])

    frames = []
    for asset_dir in frame_asset_dirs():
        record = frame_record(asset_dir, annotations, source_dir, source_map, active_files, active_files_configured)
        if record:
            frames.append(record)

    return {
        "sourceDir": str(source_dir),
        "framesDir": str(FRAMES_DIR),
        "outputDir": config["outputDir"],
        "annotationsPath": str(ANNOTATIONS_PATH),
        "configPath": str(CONFIG_PATH),
        "activeFiles": sorted(active_files) if active_files_configured else None,
        "activeFilesConfigured": active_files_configured,
        "sourceFiles": [
            source_record(path, source_dir, annotations, active_files, active_files_configured)
            for path in sources
        ],
        "frames": frames,
    }


def manifest_from_workspace(workspace: dict) -> dict:
    active_files_configured = bool(workspace["activeFilesConfigured"])
    active_files = set(workspace["activeFiles"] or [])
    files = []
    for record in workspace["frames"]:
        if active_files_configured and record["id"] not in active_files:
            continue
        files.append(
            {
                "id": record["id"],
                "name": record["sourceName"] or record["name"],
                "kind": record["kind"],
                "frameCount": record["frameCount"],
                "frames": record["frames"],
                "annotatedFrameCount": record["annotatedFrameCount"],
                "missingSource": record["missingSource"],
            }
        )
    return {"files": files, "annotationsPath": str(ANNOTATIONS_PATH)}


def build_manifest() -> dict:
    return manifest_from_workspace(build_workspace())


def build_bootstrap() -> dict:
    annotations = load_annotations()
    workspace = build_workspace(annotations=annotations)
    return {
        "workspace": workspace,
        "manifest": manifest_from_workspace(workspace),
        "annotations": annotations,
    }


def safe_frame_path(asset: str, filename: str) -> Path | None:
    candidate = (FRAMES_DIR / asset / filename).resolve()
    try:
        candidate.relative_to(FRAMES_DIR.resolve())
    except ValueError:
        return None
    if candidate.is_file() and candidate.suffix.lower() == ".png":
        return candidate
    return None


def request_json() -> dict:
    raw = request.body.read()
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(data, dict):
        raise ValueError("request body must be a JSON object")
    return data


def find_source(asset_id: str, source_dir: Path) -> Path | None:
    return source_for_asset_id(asset_id, source_dir)


def extract_frames(source: Path, source_dir: Path, overwrite: bool) -> dict:
    asset_id = asset_id_for_source(source, source_dir)
    target_dir = (FRAMES_DIR / asset_id).resolve()
    target_dir.relative_to(FRAMES_DIR.resolve())

    existing = sorted(target_dir.glob("frame_*.png")) if target_dir.exists() else []
    if existing and not overwrite:
        return {"id": asset_id, "status": "skipped", "frameCount": len(existing)}

    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    frame_count_written = 0
    with Image.open(source) as image:
        is_animated = bool(getattr(image, "is_animated", False) and getattr(image, "n_frames", 1) > 1)
        if is_animated:
            for index, frame in enumerate(ImageSequence.Iterator(image)):
                frame.convert("RGBA").save(target_dir / f"frame_{index:06d}.png")
                frame_count_written = index + 1
        else:
            image.convert("RGBA").save(target_dir / "frame_000000.png")
            frame_count_written = 1

    kind = "animated" if is_animated else "static"
    write_source_meta(asset_id, source, frame_count_written, kind)

    annotations = load_annotations()
    annotations.setdefault("files", {}).setdefault(asset_id, {"frames": {}})
    save_annotations(annotations)
    return {
        "id": asset_id,
        "status": "imported",
        "frameCount": frame_count_written,
        "kind": kind,
    }


def update_active_files(imported_ids: list[str]) -> None:
    if not imported_ids:
        return
    config = load_config()
    if config.get("activeFiles") is None:
        active_files = {
            asset_id_for_frame_dir(path)
            for path in frame_asset_dirs()
        }
    else:
        active_files = set(config.get("activeFiles") or [])
    active_files.update(imported_ids)
    config["activeFiles"] = sorted(active_files)
    save_config(config)


def render_assets(
    asset_ids: list[str],
    suffix: str,
    max_bytes: int | None,
    output_root: Path,
    animated_format: str,
) -> list[dict]:
    if not is_frozen() and str(WORK_DIR) not in sys.path:
        sys.path.insert(0, str(WORK_DIR))
    import apply_thick_mosaic  # noqa: PLC0415

    annotations = load_annotations()
    files = annotations.get("files", {})
    results = []
    for asset_id in asset_ids:
        try:
            report = apply_thick_mosaic.process_asset_report(
                asset_id,
                files.get(asset_id, {"frames": {}}),
                suffix=suffix,
                max_bytes=max_bytes,
                output_root=output_root,
                animated_format=animated_format,
            )
        except Exception as exc:  # noqa: BLE001
            results.append({"id": asset_id, "status": "error", "error": str(exc)})
            continue
        report["status"] = "rendered"
        results.append(report)
    return results


def cleanup_render_jobs() -> None:
    cutoff = time.time() - RENDER_JOB_TTL_SECONDS
    with RENDER_JOBS_LOCK:
        stale = [
            job_id
            for job_id, job in RENDER_JOBS.items()
            if job.get("finishedAt") and job["finishedAt"] < cutoff
        ]
        for job_id in stale:
            RENDER_JOBS.pop(job_id, None)


def update_render_job(job_id: str, **changes) -> None:
    with RENDER_JOBS_LOCK:
        job = RENDER_JOBS.get(job_id)
        if not job:
            return
        job.update(changes)
        job["updatedAt"] = time.time()


def render_job_snapshot(job_id: str) -> dict | None:
    with RENDER_JOBS_LOCK:
        job = RENDER_JOBS.get(job_id)
        return json.loads(json.dumps(job, ensure_ascii=False)) if job else None


def run_render_job(
    job_id: str,
    asset_ids: list[str],
    suffix: str,
    max_bytes: int | None,
    output_root: Path,
    animated_format: str,
) -> None:
    if not is_frozen() and str(WORK_DIR) not in sys.path:
        sys.path.insert(0, str(WORK_DIR))
    import apply_thick_mosaic  # noqa: PLC0415

    annotations = load_annotations()
    files = annotations.get("files", {})
    results = []
    update_render_job(
        job_id,
        status="running",
        startedAt=time.time(),
        total=len(asset_ids),
        done=0,
        outputDir=str(output_root),
        animatedFormat=animated_format,
        maxBytes=max_bytes,
    )
    try:
        for index, asset_id in enumerate(asset_ids):
            update_render_job(job_id, current=asset_id, done=index)
            try:
                report = apply_thick_mosaic.process_asset_report(
                    asset_id,
                    files.get(asset_id, {"frames": {}}),
                    suffix=suffix,
                    max_bytes=max_bytes,
                    output_root=output_root,
                    animated_format=animated_format,
                )
            except Exception as exc:  # noqa: BLE001
                report = {"id": asset_id, "status": "error", "error": str(exc)}
            else:
                report["status"] = "rendered"
            results.append(report)
            update_render_job(job_id, done=index + 1, results=results)
    except Exception as exc:  # noqa: BLE001
        update_render_job(job_id, status="error", error=str(exc), finishedAt=time.time(), current=None)
        return
    update_render_job(
        job_id,
        status="done",
        done=len(asset_ids),
        current=None,
        results=results,
        outputDir=str(output_root),
        finishedAt=time.time(),
    )


def start_render_job(
    asset_ids: list[str],
    suffix: str,
    max_bytes: int | None,
    output_root: Path,
    animated_format: str,
) -> dict:
    cleanup_render_jobs()
    job_id = uuid.uuid4().hex
    now = time.time()
    with RENDER_JOBS_LOCK:
        RENDER_JOBS[job_id] = {
            "id": job_id,
            "status": "queued",
            "total": len(asset_ids),
            "done": 0,
            "current": None,
            "results": [],
            "outputDir": str(output_root),
            "animatedFormat": animated_format,
            "maxBytes": max_bytes,
            "createdAt": now,
            "updatedAt": now,
        }
    thread = threading.Thread(
        target=run_render_job,
        args=(job_id, asset_ids, suffix, max_bytes, output_root, animated_format),
        daemon=True,
    )
    thread.start()
    return render_job_snapshot(job_id) or {"id": job_id, "status": "queued"}


def parse_render_request(data: dict) -> tuple[tuple[list[str], str, int | None, str] | None, dict]:
    asset_ids = data.get("assetIds")
    if not isinstance(asset_ids, list) or not asset_ids:
        return None, {"ok": False, "error": "assetIds must be a non-empty list"}

    suffix = str(data.get("suffix") or "")
    max_bytes_raw = data.get("maxBytes", 1024 * 1024)
    try:
        max_bytes = None if max_bytes_raw is None or max_bytes_raw in (0, "0", "") else int(max_bytes_raw)
    except (TypeError, ValueError):
        return None, {"ok": False, "error": "maxBytes must be a number"}
    try:
        animated_format = normalize_animated_format(str(data.get("animatedFormat") or DEFAULT_ANIMATED_FORMAT))
    except ValueError as exc:
        return None, {"ok": False, "error": str(exc)}

    return ([str(item) for item in asset_ids], suffix, max_bytes, animated_format), {}


def json_response(data: dict, status: int = 200) -> HTTPResponse:
    return HTTPResponse(
        body=json.dumps(data, ensure_ascii=False, indent=2),
        status=status,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "no-store",
        },
    )


def file_response(path: Path, cache_control: str = "no-store"):
    result = static_file(path.name, root=str(path.parent))
    result.set_header("Cache-Control", cache_control)
    return result


def create_app() -> Bottle:
    app = Bottle()

    @app.get("/")
    @app.get("/index.html")
    def index():
        return file_response(INDEX_PATH)

    @app.get("/api/manifest")
    def api_manifest():
        return json_response(build_manifest())

    @app.get("/api/workspace")
    def api_workspace():
        return json_response(build_workspace())

    @app.get("/api/bootstrap")
    def api_bootstrap():
        return json_response(build_bootstrap())

    @app.get("/api/annotations")
    def api_annotations():
        return json_response(load_annotations())

    @app.get("/api/render/status")
    def api_render_status():
        job_id = request.query.get("jobId", "")
        if not job_id:
            return json_response({"ok": False, "error": "jobId is required"}, 400)
        job = render_job_snapshot(job_id)
        if not job:
            return json_response({"ok": False, "error": "render job not found"}, 404)
        return json_response({"ok": True, "job": job})

    @app.get("/frame/<rel:path>")
    def frame(rel: str):
        if "/" not in rel:
            return json_response({"ok": False, "error": "frame path is invalid"}, 404)
        asset, filename = rel.rsplit("/", 1)
        frame_path = safe_frame_path(asset, filename)
        if not frame_path:
            return json_response({"ok": False, "error": "frame not found"}, 404)
        return file_response(frame_path, "private, max-age=31536000, immutable")

    @app.get("/assets/<rel:path>")
    def asset(rel: str):
        target = (ASSETS_DIR / rel).resolve()
        try:
            target.relative_to(ASSETS_DIR.resolve())
        except ValueError:
            return json_response({"ok": False, "error": "asset path is invalid"}, 404)
        if not target.is_file():
            return json_response({"ok": False, "error": "asset not found"}, 404)
        return file_response(target)

    @app.post("/api/annotations")
    def save_annotations_api():
        try:
            data = request_json()
        except ValueError as exc:
            return json_response({"ok": False, "error": str(exc)}, 400)
        save_annotations(data)
        return json_response({"ok": True, "path": str(ANNOTATIONS_PATH)})

    @app.post("/api/select-directory")
    def select_directory_api():
        try:
            data = request_json()
        except ValueError as exc:
            return json_response({"ok": False, "error": str(exc)}, 400)

        config = load_config()
        kind = str(data.get("kind") or "source")
        if kind == "output":
            title = "选择导出目录"
            fallback_dir = config.get("outputDir") or DEFAULT_OUTPUT_DIR
        else:
            title = "选择图片目录"
            fallback_dir = config.get("sourceDir") or DEFAULT_SOURCE_DIR
        current_dir = str(data.get("currentDir") or fallback_dir)
        try:
            selected = pick_directory(title, current_dir)
        except Exception as exc:  # noqa: BLE001
            return json_response({"ok": False, "error": f"Directory picker failed: {exc}"}, 500)
        return json_response({"ok": True, "path": selected})

    @app.post("/api/config")
    def config_api():
        try:
            data = request_json()
        except ValueError as exc:
            return json_response({"ok": False, "error": str(exc)}, 400)

        config = load_config()
        active_only = (
            bool(data.get("activeOnly"))
            and "activeFiles" in data
            and "sourceDir" not in data
            and "outputDir" not in data
        )
        if "sourceDir" in data:
            source_dir = resolve_dir(data["sourceDir"])
            if not source_dir.is_dir():
                return json_response({"ok": False, "error": f"Source directory not found: {source_dir}"}, 400)
            previous_source_dir = resolve_dir(config.get("sourceDir") or DEFAULT_SOURCE_DIR)
            config["sourceDir"] = str(source_dir)
            if "activeFiles" not in data and source_dir != previous_source_dir:
                config["activeFiles"] = [
                    asset_id_for_source(source, source_dir)
                    for source in source_files(source_dir)
                ]
        if "outputDir" in data:
            output_dir = resolve_dir(str(data["outputDir"]).strip() or DEFAULT_OUTPUT_DIR)
            if output_dir.exists() and not output_dir.is_dir():
                return json_response({"ok": False, "error": f"Output path is not a directory: {output_dir}"}, 400)
            try:
                output_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return json_response({"ok": False, "error": f"Cannot create output directory: {exc}"}, 400)
            config["outputDir"] = str(output_dir)
        if "activeFiles" in data:
            active_files = data["activeFiles"]
            if not isinstance(active_files, list):
                return json_response({"ok": False, "error": "activeFiles must be a list"}, 400)
            config["activeFiles"] = sorted({str(item) for item in active_files if str(item).strip()})
        save_config(config)
        if active_only:
            return json_response(
                {
                    "ok": True,
                    "activeFiles": config.get("activeFiles") or [],
                    "activeFilesConfigured": config.get("activeFiles") is not None,
                }
            )
        workspace = build_workspace()
        return json_response({"ok": True, "workspace": workspace, "manifest": manifest_from_workspace(workspace)})

    @app.post("/api/extract")
    def extract_api():
        try:
            data = request_json()
        except ValueError as exc:
            return json_response({"ok": False, "error": str(exc)}, 400)

        asset_ids = data.get("assetIds")
        if not isinstance(asset_ids, list) or not asset_ids:
            return json_response({"ok": False, "error": "assetIds must be a non-empty list"}, 400)
        source_dir = resolve_dir(load_config()["sourceDir"])
        overwrite = bool(data.get("overwrite", False))
        results = []
        imported_ids = []
        for asset_id in [str(item) for item in asset_ids]:
            source = find_source(asset_id, source_dir)
            if not source:
                results.append({"id": asset_id, "status": "missing_source"})
                continue
            try:
                result = extract_frames(source, source_dir, overwrite)
            except Exception as exc:  # noqa: BLE001
                results.append({"id": asset_id, "status": "error", "error": str(exc)})
                continue
            results.append(result)
            if result["status"] in {"imported", "skipped"}:
                imported_ids.append(asset_id)
        update_active_files(imported_ids)
        workspace = build_workspace()
        return json_response(
            {"ok": True, "results": results, "workspace": workspace, "manifest": manifest_from_workspace(workspace)}
        )

    @app.post("/api/render")
    def render_api():
        try:
            data = request_json()
        except ValueError as exc:
            return json_response({"ok": False, "error": str(exc)}, 400)

        parsed, error = parse_render_request(data)
        if not parsed:
            return json_response(error, 400)
        asset_ids, suffix, max_bytes, animated_format = parsed
        output_root = create_export_output_dir()
        results = render_assets(asset_ids, suffix, max_bytes, output_root, animated_format)
        return json_response({"ok": True, "results": results, "outputDir": str(output_root)})

    @app.post("/api/render/start")
    def render_start_api():
        try:
            data = request_json()
        except ValueError as exc:
            return json_response({"ok": False, "error": str(exc)}, 400)

        parsed, error = parse_render_request(data)
        if not parsed:
            return json_response(error, 400)
        asset_ids, suffix, max_bytes, animated_format = parsed
        output_root = create_export_output_dir()
        job = start_render_job(asset_ids, suffix, max_bytes, output_root, animated_format)
        return json_response({"ok": True, "jobId": job["id"], "job": job})

    return app


def run_server(host: str = "127.0.0.1", port: int = 8788) -> None:
    app = create_app()
    print(f"Motion Mosaic listening on http://{host}:{port}")
    try:
        from waitress import serve
    except ImportError:
        app.run(host=host, port=port, server="wsgiref", quiet=True)
        return
    serve(app, host=host, port=port, threads=8)


if __name__ == "__main__":
    run_server()
