from __future__ import annotations

import json
import mimetypes
import shutil
import sys
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from PIL import Image, ImageSequence

ROOT = Path(__file__).resolve().parent
WORK_DIR = ROOT.parent
PROJECT_DIR = WORK_DIR
DATA_DIR = WORK_DIR / "data"
FRAMES_DIR = DATA_DIR / "frames"
DEFAULT_SOURCE_DIR = PROJECT_DIR / "inputs"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "exports"
ANNOTATIONS_PATH = DATA_DIR / "annotations.json"
CONFIG_PATH = DATA_DIR / "config.json"
INDEX_PATH = ROOT / "index.html"
ANIMATED_SOURCE_SUFFIXES = {".gif", ".webp"}
STATIC_SOURCE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
SUPPORTED_SOURCE_SUFFIXES = ANIMATED_SOURCE_SUFFIXES | STATIC_SOURCE_SUFFIXES
RENDER_JOBS: dict[str, dict] = {}
RENDER_JOBS_LOCK = threading.Lock()
RENDER_JOB_TTL_SECONDS = 60 * 60


def default_config() -> dict:
    return {
        "sourceDir": str(DEFAULT_SOURCE_DIR),
        "outputDir": str(DEFAULT_OUTPUT_DIR),
        "activeFiles": None,
    }


def load_config() -> dict:
    config = default_config()
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
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_dir(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path.resolve()


def load_annotations() -> dict:
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
    ANNOTATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ANNOTATIONS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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
    kind = source_kind(path)
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
        "imported": frames > 0,
        "frameCount": frames,
        "annotatedFrameCount": annotated_frame_count(asset_id, annotations),
        "active": asset_id in active_files if active_files_configured else frames > 0,
    }


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
    return {
        "id": asset_id,
        "name": asset_id,
        "sourceName": source_name,
        "baseName": source.name if source else "",
        "kind": source_kind(source) if source else "unknown",
        "missingSource": source is None,
        "frameCount": len(frame_files),
        "annotatedFrameCount": annotated_frame_count(asset_id, annotations),
        "active": asset_id in active_files if active_files_configured else True,
        "frames": [f"/frame/{quote(asset_id, safe='/')}/{quote(frame.name)}" for frame in frame_files],
    }


def build_workspace() -> dict:
    config = load_config()
    annotations = load_annotations()
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


def build_manifest() -> dict:
    workspace = build_workspace()
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


def safe_frame_path(asset: str, filename: str) -> Path | None:
    candidate = (FRAMES_DIR / asset / filename).resolve()
    try:
        candidate.relative_to(FRAMES_DIR.resolve())
    except ValueError:
        return None
    if candidate.is_file() and candidate.suffix.lower() == ".png":
        return candidate
    return None


def read_request_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


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

    annotations = load_annotations()
    annotations.setdefault("files", {}).setdefault(asset_id, {"frames": {}})
    save_annotations(annotations)
    return {
        "id": asset_id,
        "status": "imported",
        "frameCount": frame_count_written,
        "kind": "animated" if is_animated else "static",
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


def render_assets(asset_ids: list[str], suffix: str, max_bytes: int | None, output_root: Path) -> list[dict]:
    if str(WORK_DIR) not in sys.path:
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


def run_render_job(job_id: str, asset_ids: list[str], suffix: str, max_bytes: int | None, output_root: Path) -> None:
    if str(WORK_DIR) not in sys.path:
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


def start_render_job(asset_ids: list[str], suffix: str, max_bytes: int | None, output_root: Path) -> dict:
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
            "createdAt": now,
            "updatedAt": now,
        }
    thread = threading.Thread(
        target=run_render_job,
        args=(job_id, asset_ids, suffix, max_bytes, output_root),
        daemon=True,
    )
    thread.start()
    return render_job_snapshot(job_id) or {"id": job_id, "status": "queued"}


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path in {"/", "/index.html"}:
            self._send_file(INDEX_PATH)
            return
        if path == "/api/manifest":
            self._send_json(200, build_manifest())
            return
        if path == "/api/workspace":
            self._send_json(200, build_workspace())
            return
        if path == "/api/annotations":
            self._send_json(200, load_annotations())
            return
        if path == "/api/render/status":
            query = parse_qs(parsed.query)
            job_id = (query.get("jobId") or [""])[0]
            if not job_id:
                self._send_json(400, {"ok": False, "error": "jobId is required"})
                return
            job = render_job_snapshot(job_id)
            if not job:
                self._send_json(404, {"ok": False, "error": "render job not found"})
                return
            self._send_json(200, {"ok": True, "job": job})
            return
        if path.startswith("/frame/"):
            rel = path[len("/frame/") :]
            if "/" in rel:
                asset, filename = rel.rsplit("/", 1)
                frame_path = safe_frame_path(asset, filename)
                if frame_path:
                    self._send_file(frame_path)
                    return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/annotations":
            try:
                data = read_request_json(self)
            except json.JSONDecodeError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            save_annotations(data)
            self._send_json(200, {"ok": True, "path": str(ANNOTATIONS_PATH)})
            return

        if path == "/api/config":
            try:
                data = read_request_json(self)
            except json.JSONDecodeError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return

            config = load_config()
            if "sourceDir" in data:
                source_dir = resolve_dir(data["sourceDir"])
                if not source_dir.is_dir():
                    self._send_json(400, {"ok": False, "error": f"Source directory not found: {source_dir}"})
                    return
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
                    self._send_json(400, {"ok": False, "error": f"Output path is not a directory: {output_dir}"})
                    return
                try:
                    output_dir.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    self._send_json(400, {"ok": False, "error": f"Cannot create output directory: {exc}"})
                    return
                config["outputDir"] = str(output_dir)
            if "activeFiles" in data:
                active_files = data["activeFiles"]
                if not isinstance(active_files, list):
                    self._send_json(400, {"ok": False, "error": "activeFiles must be a list"})
                    return
                config["activeFiles"] = sorted({str(item) for item in active_files if str(item).strip()})
            save_config(config)
            self._send_json(200, {"ok": True, "workspace": build_workspace(), "manifest": build_manifest()})
            return

        if path == "/api/extract":
            try:
                data = read_request_json(self)
            except json.JSONDecodeError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return

            asset_ids = data.get("assetIds")
            if not isinstance(asset_ids, list) or not asset_ids:
                self._send_json(400, {"ok": False, "error": "assetIds must be a non-empty list"})
                return
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
            self._send_json(200, {"ok": True, "results": results, "workspace": build_workspace(), "manifest": build_manifest()})
            return

        if path == "/api/render":
            try:
                data = read_request_json(self)
            except json.JSONDecodeError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return

            asset_ids = data.get("assetIds")
            if not isinstance(asset_ids, list) or not asset_ids:
                self._send_json(400, {"ok": False, "error": "assetIds must be a non-empty list"})
                return

            suffix = str(data.get("suffix") or "")
            max_bytes_raw = data.get("maxBytes", 1024 * 1024)
            try:
                max_bytes = None if max_bytes_raw is None or max_bytes_raw in (0, "0", "") else int(max_bytes_raw)
            except (TypeError, ValueError):
                self._send_json(400, {"ok": False, "error": "maxBytes must be a number"})
                return

            output_root = create_export_output_dir()
            results = render_assets([str(item) for item in asset_ids], suffix, max_bytes, output_root)
            self._send_json(200, {"ok": True, "results": results, "outputDir": str(output_root)})
            return

        if path == "/api/render/start":
            try:
                data = read_request_json(self)
            except json.JSONDecodeError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return

            asset_ids = data.get("assetIds")
            if not isinstance(asset_ids, list) or not asset_ids:
                self._send_json(400, {"ok": False, "error": "assetIds must be a non-empty list"})
                return

            suffix = str(data.get("suffix") or "")
            max_bytes_raw = data.get("maxBytes", 1024 * 1024)
            try:
                max_bytes = None if max_bytes_raw is None or max_bytes_raw in (0, "0", "") else int(max_bytes_raw)
            except (TypeError, ValueError):
                self._send_json(400, {"ok": False, "error": "maxBytes must be a number"})
                return

            output_root = create_export_output_dir()
            job = start_render_job([str(item) for item in asset_ids], suffix, max_bytes, output_root)
            self._send_json(200, {"ok": True, "jobId": job["id"], "job": job})
            return

        self.send_error(404)

    def log_message(self, fmt: str, *args) -> None:
        print("%s - %s" % (self.address_string(), fmt % args))


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8788), Handler)
    print("Annotator listening on http://127.0.0.1:8788")
    server.serve_forever()
