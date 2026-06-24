from __future__ import annotations

import os
import sys
from pathlib import Path

LEGACY_PROJECT_DIR_NAMES = {"remask-annotator"}


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def project_dir() -> Path:
    override = os.environ.get("MOTION_MOSAIC_HOME") or os.environ.get("REMASK_ANNOTATOR_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_path(*parts: str) -> Path:
    if is_frozen():
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        return bundle_root.joinpath(*parts)
    return project_dir().joinpath(*parts)


def project_exports_dir() -> Path:
    return project_dir() / "exports"


def resolve_project_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_dir() / path
    return path.resolve()


def is_project_export_dir(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    project_names = {project_dir().name.lower(), *LEGACY_PROJECT_DIR_NAMES}
    return resolved.name.lower() == "exports" and resolved.parent.name.lower() in project_names


def resolve_project_output_dir(value: str | Path | None) -> Path:
    if not value:
        return project_exports_dir().resolve()
    output_dir = resolve_project_path(value)
    if is_project_export_dir(output_dir):
        return project_exports_dir().resolve()
    return output_dir


def stored_project_output_dir(value: str | Path | None) -> str:
    output_dir = resolve_project_output_dir(value)
    if output_dir == project_exports_dir().resolve():
        return "exports"
    return str(output_dir)
