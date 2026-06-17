from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageSequence

WORK_DIR = Path(__file__).resolve().parent
PROJECT_DIR = WORK_DIR
DEFAULT_SOURCE_DIR = PROJECT_DIR / "inputs"
DATA_DIR = WORK_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.json"
FRAMES_DIR = DATA_DIR / "frames"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "exports"
OUTPUT_DIR = DEFAULT_OUTPUT_DIR
ANNOTATIONS_PATH = DATA_DIR / "annotations.json"
WEBPMUX = Path(r"D:\Software\DevTool\Scoop\shims\webpmux.exe")
GIFSICLE = Path(r"D:\Software\DevTool\Scoop\shims\gifsicle.exe")
CWEBP = Path(r"D:\Software\DevTool\Scoop\shims\cwebp.exe")

ANIMATED_SOURCE_SUFFIXES = {".gif", ".webp"}
STATIC_SOURCE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
SUPPORTED_SOURCE_SUFFIXES = ANIMATED_SOURCE_SUFFIXES | STATIC_SOURCE_SUFFIXES
MAX_IMAGE_SIDE = 3072
WEBP_QUALITY = 80
WEBP_METHOD = 3
DEFAULT_MAX_OUTPUT_BYTES = 1024 * 1024
STATIC_COMPRESS_CANDIDATES = [
    (80, 1.0),
    (72, 1.0),
    (64, 0.9),
    (56, 0.8),
    (48, 0.7),
    (40, 0.6),
    (32, 0.5),
]
ANIMATED_COMPRESS_CANDIDATES = [
    (80, 1.0),
    (70, 1.0),
    (60, 0.9),
    (50, 0.8),
    (42, 0.7),
    (34, 0.6),
    (28, 0.5),
]


def source_dir() -> Path:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        configured = data.get("sourceDir")
        if configured:
            return Path(configured).expanduser().resolve()
    return DEFAULT_SOURCE_DIR


def output_dir() -> Path:
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        configured = data.get("outputDir")
        if configured:
            return Path(configured).expanduser().resolve()
    return DEFAULT_OUTPUT_DIR


def source_files(root: Path) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SOURCE_SUFFIXES
    )


def asset_id_for_source(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).with_suffix("").as_posix()
    except ValueError:
        return path.stem


def source_for_asset_id(asset_id: str, root: Path) -> Path | None:
    raw_id = str(asset_id).replace("\\", "/").strip("/")
    for suffix in SUPPORTED_SOURCE_SUFFIXES:
        candidate = (root / f"{raw_id}{suffix}").resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    for source in source_files(root):
        if asset_id_for_source(source, root) == raw_id:
            return source
    return None


def find_source(asset_id: str) -> Path:
    root = source_dir()
    path = source_for_asset_id(asset_id, root)
    if path:
        return path
    suffixes = ", ".join(sorted(SUPPORTED_SOURCE_SUFFIXES))
    raise FileNotFoundError(f"No source image found for {asset_id}; supported: {suffixes}")


def temp_key_for_output(output: Path) -> str:
    try:
        rel = output.relative_to(OUTPUT_DIR).with_suffix("")
        return "__".join(rel.parts)
    except ValueError:
        return output.stem


def output_path_for_source(source: Path, suffix: str, output_root: Path | None = None) -> Path:
    root = source_dir()
    target_root = output_root or OUTPUT_DIR
    try:
        rel_parent = source.parent.relative_to(root)
    except ValueError:
        rel_parent = Path()
    return target_root / rel_parent / f"{source.stem}{suffix}.webp"


def image_is_animated(source: Path) -> bool:
    with Image.open(source) as image:
        return bool(getattr(image, "is_animated", False) and getattr(image, "n_frames", 1) > 1)


def gif_durations(source: Path) -> list[int]:
    with Image.open(source) as image:
        return [int(frame.info.get("duration") or 100) for frame in ImageSequence.Iterator(image)]


def webp_durations(source: Path) -> list[int]:
    if WEBPMUX.exists():
        result = subprocess.run(
            [str(WEBPMUX), "-info", str(source)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        durations = []
        for line in result.stdout.splitlines():
            if not re.match(r"\s*\d+:", line):
                continue
            parts = line.split()
            if len(parts) >= 7:
                durations.append(int(parts[6]))
        if durations:
            return durations
    return gif_durations(source)


def source_timing(source: Path, frame_count: int) -> tuple[list[int], int]:
    with Image.open(source) as image:
        loop = int(image.info.get("loop", 0) or 0)

    if not image_is_animated(source):
        return [100] * frame_count, loop

    durations = webp_durations(source) if source.suffix.lower() == ".webp" else gif_durations(source)
    if len(durations) < frame_count:
        durations.extend([durations[-1] if durations else 100] * (frame_count - len(durations)))
    return durations[:frame_count], loop


def thick_block_size(width: int, height: int) -> int:
    base = int(round(min(width, height) * 0.05))
    return max(28, base)


def build_mask(width: int, height: int, shapes: list[dict], block_size: int) -> np.ndarray:
    mask = np.zeros((height, width), dtype=np.uint8)
    for shape in shapes:
        points = shape.get("points") or []
        if len(points) < 2:
            continue
        if shape.get("type") == "rect":
            x1, y1 = int(points[0]["x"]), int(points[0]["y"])
            x2, y2 = int(points[1]["x"]), int(points[1]["y"])
            left, right = sorted((x1, x2))
            top, bottom = sorted((y1, y2))
            cv2.rectangle(
                mask,
                (max(0, left), max(0, top)),
                (min(width - 1, right), min(height - 1, bottom)),
                255,
                -1,
            )
        elif shape.get("type") == "polygon" and len(points) >= 3:
            poly = np.array([[int(p["x"]), int(p["y"])] for p in points], dtype=np.int32)
            cv2.fillPoly(mask, [poly], 255)

    if mask.any():
        expand = max(8, block_size // 2)
        kernel = np.ones((expand, expand), dtype=np.uint8)
        mask = cv2.dilate(mask, kernel, iterations=1)
    return mask


def apply_mosaic(frame: Image.Image, shapes: list[dict]) -> Image.Image:
    rgba = frame.convert("RGBA")
    width, height = rgba.size
    block_size = thick_block_size(width, height)
    mask = build_mask(width, height, shapes, block_size)
    if not mask.any():
        return rgba

    small = (
        max(1, width // block_size),
        max(1, height // block_size),
    )
    mosaic = rgba.resize(small, Image.Resampling.BOX).resize((width, height), Image.Resampling.NEAREST)
    src = np.array(rgba)
    pix = np.array(mosaic)
    out = np.where(mask[:, :, None] > 0, pix, src)
    return Image.fromarray(out.astype(np.uint8), "RGBA")


def fit_image_to_side(image: Image.Image, max_side: int) -> Image.Image:
    prepared = image.copy()
    if prepared.mode == "CMYK":
        prepared = prepared.convert("RGB")
    elif prepared.mode not in {"RGB", "RGBA"}:
        prepared = prepared.convert("RGBA" if "A" in prepared.getbands() else "RGB")

    if max(prepared.size) > max_side:
        prepared.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return prepared


def scaled_image(image: Image.Image, scale: float) -> Image.Image:
    prepared = image.copy()
    if scale >= 0.999:
        return prepared
    width, height = prepared.size
    next_size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    return prepared.resize(next_size, Image.Resampling.LANCZOS)


def save_static_webp(
    frame: Image.Image,
    output: Path,
    *,
    max_bytes: int | None = DEFAULT_MAX_OUTPUT_BYTES,
    lossless: bool = False,
) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    base = fit_image_to_side(frame, MAX_IMAGE_SIDE)

    if lossless:
        base.save(output, "WEBP", lossless=True, quality=100, method=WEBP_METHOD)
        return {"quality": 100, "scale": 1.0, "targetBytes": max_bytes, "size": output.stat().st_size}

    trial_dir = OUTPUT_DIR / "_webp_trials" / temp_key_for_output(output)
    shutil.rmtree(trial_dir, ignore_errors=True)
    trial_dir.mkdir(parents=True, exist_ok=True)
    best: tuple[int, Path, int, float] | None = None

    try:
        for quality, scale in STATIC_COMPRESS_CANDIDATES:
            trial = trial_dir / f"q{quality}_s{int(scale * 100)}.webp"
            scaled_image(base, scale).save(trial, "WEBP", quality=quality, method=WEBP_METHOD)
            size = trial.stat().st_size
            if best is None or size < best[0]:
                best = (size, trial, quality, scale)
            if max_bytes is None or size <= max_bytes:
                shutil.copy2(trial, output)
                return {"quality": quality, "scale": scale, "targetBytes": max_bytes, "size": output.stat().st_size}

        if best is None:
            raise RuntimeError("No static WebP candidate was generated")
        _size, trial, quality, scale = best
        shutil.copy2(trial, output)
        return {"quality": quality, "scale": scale, "targetBytes": max_bytes, "size": output.stat().st_size}
    finally:
        shutil.rmtree(trial_dir, ignore_errors=True)


def save_gif(frames: list[Image.Image], durations: list[int], loop: int, output: Path) -> None:
    paletted = [frame.convert("P", palette=Image.Palette.ADAPTIVE, colors=256) for frame in frames]
    paletted[0].save(
        output,
        save_all=True,
        append_images=paletted[1:],
        duration=durations,
        loop=loop,
        disposal=2,
        optimize=False,
    )
    if GIFSICLE.exists():
        optimized = output.with_suffix(".optimized.gif")
        subprocess.run([str(GIFSICLE), "-O3", str(output), "-o", str(optimized)], check=True)
        optimized.replace(output)


def save_webp_with_pillow(
    frames: list[Image.Image],
    durations: list[int],
    loop: int,
    output: Path,
    *,
    lossless: bool,
    quality: int,
    method: int,
    scale: float,
) -> None:
    prepared = [scaled_image(frame.convert("RGBA"), scale) for frame in frames]
    prepared[0].save(
        output,
        "WEBP",
        save_all=True,
        append_images=prepared[1:],
        duration=durations,
        loop=loop,
        lossless=lossless,
        quality=quality,
        method=method,
    )


def save_webp(
    frames: list[Image.Image],
    durations: list[int],
    loop: int,
    output: Path,
    *,
    lossless: bool = False,
    quality: int = WEBP_QUALITY,
    method: int = WEBP_METHOD,
    scale: float = 1.0,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if not CWEBP.exists() or not WEBPMUX.exists():
        save_webp_with_pillow(
            frames,
            durations,
            loop,
            output,
            lossless=lossless,
            quality=quality,
            method=method,
            scale=scale,
        )
        return

    temp_dir = OUTPUT_DIR / "_webp_frames" / temp_key_for_output(output)
    shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        args = [str(WEBPMUX)]
        for index, frame in enumerate(frames):
            frame_path = temp_dir / f"frame_{index:06d}.png"
            encoded_path = temp_dir / f"frame_{index:06d}.webp"
            scaled_image(frame.convert("RGBA"), scale).save(frame_path)
            subprocess.run(
                (
                    [
                        str(CWEBP),
                        "-quiet",
                        "-lossless",
                        "-z",
                        "6",
                        "-exact",
                        str(frame_path),
                        "-o",
                        str(encoded_path),
                    ]
                    if lossless
                    else [
                        str(CWEBP),
                        "-quiet",
                        "-q",
                        str(quality),
                        "-m",
                        str(method),
                        "-exact",
                        str(frame_path),
                        "-o",
                        str(encoded_path),
                    ]
                ),
                check=True,
            )
            args.extend(
                [
                    "-frame",
                    str(encoded_path),
                    f"+{int(durations[index])}+0+0+0-b",
                ]
            )
        args.extend(["-loop", str(loop), "-o", str(output)])
        subprocess.run(args, check=True)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def save_compressed_animated_webp(
    frames: list[Image.Image],
    durations: list[int],
    loop: int,
    output: Path,
    *,
    max_bytes: int | None = DEFAULT_MAX_OUTPUT_BYTES,
    lossless: bool = False,
) -> dict:
    if lossless:
        save_webp(frames, durations, loop, output, lossless=True, quality=100, method=WEBP_METHOD, scale=1.0)
        return {"quality": 100, "scale": 1.0, "targetBytes": max_bytes, "size": output.stat().st_size}

    trial_dir = OUTPUT_DIR / "_webp_trials" / temp_key_for_output(output)
    shutil.rmtree(trial_dir, ignore_errors=True)
    trial_dir.mkdir(parents=True, exist_ok=True)
    best: tuple[int, Path, int, float] | None = None

    try:
        for quality, scale in ANIMATED_COMPRESS_CANDIDATES:
            trial = trial_dir / f"q{quality}_s{int(scale * 100)}.webp"
            save_webp(frames, durations, loop, trial, quality=quality, method=WEBP_METHOD, scale=scale)
            size = trial.stat().st_size
            if best is None or size < best[0]:
                best = (size, trial, quality, scale)
            if max_bytes is None or size <= max_bytes:
                shutil.copy2(trial, output)
                return {"quality": quality, "scale": scale, "targetBytes": max_bytes, "size": output.stat().st_size}

        if best is None:
            raise RuntimeError("No animated WebP candidate was generated")
        _size, trial, quality, scale = best
        shutil.copy2(trial, output)
        return {"quality": quality, "scale": scale, "targetBytes": max_bytes, "size": output.stat().st_size}
    finally:
        shutil.rmtree(trial_dir, ignore_errors=True)


def process_asset_report(
    asset_id: str,
    asset_annotations: dict,
    suffix: str = "",
    webp_lossless: bool = False,
    *,
    max_bytes: int | None = DEFAULT_MAX_OUTPUT_BYTES,
    output_root: Path | None = None,
) -> dict:
    source = find_source(asset_id)
    frame_paths = sorted((FRAMES_DIR / asset_id).glob("frame_*.png"))
    if not frame_paths:
        raise FileNotFoundError(f"No extracted frames found for {asset_id}")

    frame_map = asset_annotations.get("frames", {})
    durations, loop = source_timing(source, len(frame_paths))
    animated = image_is_animated(source) and len(frame_paths) > 1
    report_output_root = output_root or output_dir()
    output = output_path_for_source(source, suffix, report_output_root)

    processed: list[Image.Image] = []
    touched = 0
    for index, frame_path in enumerate(frame_paths):
        shapes = frame_map.get(str(index), [])
        if shapes:
            touched += 1
        with Image.open(frame_path) as frame:
            processed.append(apply_mosaic(frame, shapes))

    if animated:
        encode = save_compressed_animated_webp(
            processed,
            durations,
            loop,
            output,
            max_bytes=max_bytes,
            lossless=webp_lossless,
        )
    else:
        encode = save_static_webp(processed[0], output, max_bytes=max_bytes, lossless=webp_lossless)

    try:
        output_name = output.relative_to(report_output_root).as_posix()
    except ValueError:
        output_name = output.name

    report = {
        "id": asset_id,
        "source": str(source),
        "output": str(output),
        "outputName": output_name,
        "outputRoot": str(report_output_root),
        "sourceSize": source.stat().st_size,
        "size": output.stat().st_size,
        "kind": "animated" if animated else "static",
        "frameCount": len(frame_paths),
        "censoredFrameCount": touched,
        "quality": encode.get("quality"),
        "scale": encode.get("scale"),
        "targetBytes": encode.get("targetBytes"),
    }
    print(
        f"{asset_id}: {len(frame_paths)} frames, censored {touched}, "
        f"{report['kind']} webp q={report['quality']} scale={report['scale']}, "
        f"size={report['size']}, output={output}"
    )
    return report


def process_asset(asset_id: str, asset_annotations: dict, suffix: str, webp_lossless: bool) -> Path:
    report = process_asset_report(asset_id, asset_annotations, suffix, webp_lossless)
    return Path(report["output"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", action="append", help="Only process this asset id. Can be repeated.")
    parser.add_argument("--suffix", default="")
    parser.add_argument("--webp-lossless", action="store_true")
    parser.add_argument("--target-mb", type=float, default=1.0)
    args = parser.parse_args()

    output_dir().mkdir(parents=True, exist_ok=True)
    data = json.loads(ANNOTATIONS_PATH.read_text(encoding="utf-8"))
    outputs = []
    target_bytes = int(args.target_mb * 1024 * 1024) if args.target_mb > 0 else None
    for asset_id, asset_annotations in sorted(data.get("files", {}).items()):
        if args.asset and asset_id not in set(args.asset):
            continue
        report = process_asset_report(
            asset_id,
            asset_annotations,
            args.suffix,
            args.webp_lossless,
            max_bytes=target_bytes,
        )
        outputs.append(Path(report["output"]))
    print("done")
    for path in outputs:
        print(path)


if __name__ == "__main__":
    main()
