from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageSequence

from app_paths import project_dir

WORK_DIR = project_dir()
PROJECT_DIR = WORK_DIR
DEFAULT_SOURCE_DIR = PROJECT_DIR / "inputs"
DATA_DIR = WORK_DIR / "data"
CONFIG_PATH = DATA_DIR / "config.json"
FRAMES_DIR = DATA_DIR / "frames"
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "exports"
OUTPUT_DIR = DEFAULT_OUTPUT_DIR
ANNOTATIONS_PATH = DATA_DIR / "annotations.json"
WEBPMUX = Path(r"D:\Software\DevTool\Scoop\shims\webpmux.exe")
FFMPEG = Path(r"D:\Software\DevTool\Scoop\shims\ffmpeg.exe")

ANIMATED_SOURCE_SUFFIXES = {".gif", ".webp"}
STATIC_SOURCE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
SUPPORTED_SOURCE_SUFFIXES = ANIMATED_SOURCE_SUFFIXES | STATIC_SOURCE_SUFFIXES
MAX_IMAGE_SIDE = 3072
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
ANIMATED_WEBP_COMPRESS_CANDIDATES = [
    (80, 1.0),
    (70, 1.0),
    (60, 0.9),
    (50, 0.8),
    (42, 0.7),
    (34, 0.6),
    (28, 0.5),
]
WEBM_COMPRESS_CANDIDATES = [
    (32, 1.0),
    (38, 1.0),
    (44, 0.9),
    (50, 0.8),
    (56, 0.7),
    (63, 0.6),
]
DEFAULT_ANIMATED_FORMAT = "webp"
ANIMATED_OUTPUT_FORMATS = {"webp", "webm"}


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


def output_path_for_source(
    source: Path,
    suffix: str,
    output_root: Path | None = None,
    extension: str = ".webp",
) -> Path:
    root = source_dir()
    target_root = output_root or OUTPUT_DIR
    try:
        rel_parent = source.parent.relative_to(root)
    except ValueError:
        rel_parent = Path()
    normalized_extension = extension if extension.startswith(".") else f".{extension}"
    return target_root / rel_parent / f"{source.stem}{suffix}{normalized_extension}"


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


def build_mask(width: int, height: int, shapes: list[dict], block_size: int) -> Image.Image:
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    for shape in shapes:
        points = shape.get("points") or []
        if len(points) < 2:
            continue
        if shape.get("type") == "rect":
            x1, y1 = int(points[0]["x"]), int(points[0]["y"])
            x2, y2 = int(points[1]["x"]), int(points[1]["y"])
            left, right = sorted((x1, x2))
            top, bottom = sorted((y1, y2))
            draw.rectangle(
                (
                    max(0, left),
                    max(0, top),
                    min(width - 1, right),
                    min(height - 1, bottom),
                ),
                fill=255,
            )
        elif shape.get("type") == "polygon" and len(points) >= 3:
            draw.polygon([(int(p["x"]), int(p["y"])) for p in points], fill=255)

    if mask.getbbox():
        expand = max(8, block_size // 2)
        filter_size = expand + 1 if expand % 2 == 0 else expand
        mask = mask.filter(ImageFilter.MaxFilter(filter_size))
    return mask


def apply_mosaic(frame: Image.Image, shapes: list[dict]) -> Image.Image:
    rgba = frame.convert("RGBA")
    width, height = rgba.size
    block_size = thick_block_size(width, height)
    mask = build_mask(width, height, shapes, block_size)
    if not mask.getbbox():
        return rgba

    small = (
        max(1, width // block_size),
        max(1, height // block_size),
    )
    mosaic = rgba.resize(small, Image.Resampling.BOX).resize((width, height), Image.Resampling.NEAREST)
    return Image.composite(mosaic, rgba, mask)


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


def normalize_animated_format(value: str | None) -> str:
    normalized = (value or DEFAULT_ANIMATED_FORMAT).strip().lower()
    if normalized not in ANIMATED_OUTPUT_FORMATS:
        raise ValueError(f"animated format must be one of: {', '.join(sorted(ANIMATED_OUTPUT_FORMATS))}")
    return normalized


def save_animated_webp(
    frames: list[Image.Image],
    durations: list[int],
    loop: int,
    output: Path,
    *,
    quality: int,
    scale: float,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    prepared = [scaled_image(frame.convert("RGBA"), scale) for frame in frames]
    if not prepared:
        raise RuntimeError("No animated WebP frames to save")
    prepared[0].save(
        output,
        "WEBP",
        save_all=True,
        append_images=prepared[1:],
        duration=durations,
        loop=loop,
        quality=quality,
        method=WEBP_METHOD,
    )


def save_compressed_animated_webp(
    frames: list[Image.Image],
    durations: list[int],
    loop: int,
    output: Path,
    *,
    max_bytes: int | None = DEFAULT_MAX_OUTPUT_BYTES,
) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    trial_dir = OUTPUT_DIR / "_webp_trials" / temp_key_for_output(output)
    shutil.rmtree(trial_dir, ignore_errors=True)
    trial_dir.mkdir(parents=True, exist_ok=True)
    best: tuple[int, Path, int, float] | None = None

    try:
        for quality, scale in ANIMATED_WEBP_COMPRESS_CANDIDATES:
            trial = trial_dir / f"q{quality}_s{int(scale * 100)}.webp"
            save_animated_webp(frames, durations, loop, trial, quality=quality, scale=scale)
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


def ffmpeg_path() -> str:
    if FFMPEG.exists():
        return str(FFMPEG)
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise FileNotFoundError("ffmpeg is required to export animated files as WebM")


def even_size(size: tuple[int, int]) -> tuple[int, int]:
    width, height = size
    return max(2, width - width % 2), max(2, height - height % 2)


def scaled_webm_frame(frame: Image.Image, scale: float) -> Image.Image:
    prepared = frame.convert("RGBA")
    if scale < 0.999:
        width, height = prepared.size
        prepared = prepared.resize(
            (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
            Image.Resampling.LANCZOS,
        )
    next_size = even_size(prepared.size)
    if prepared.size != next_size:
        prepared = prepared.resize(next_size, Image.Resampling.LANCZOS)
    return prepared


def frame_has_alpha(frame: Image.Image) -> bool:
    if "A" not in frame.getbands():
        return False
    alpha = frame.getchannel("A")
    return alpha.getextrema()[0] < 255


def write_concat_manifest(frame_paths: list[Path], durations: list[int], manifest: Path) -> None:
    lines: list[str] = []
    for index, frame_path in enumerate(frame_paths):
        safe_path = frame_path.as_posix().replace("'", "'\\''")
        lines.append(f"file '{safe_path}'")
        lines.append(f"duration {max(1, int(durations[index])) / 1000:.6f}")
    if frame_paths:
        safe_path = frame_paths[-1].as_posix().replace("'", "'\\''")
        lines.append(f"file '{safe_path}'")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_webm(
    frames: list[Image.Image],
    durations: list[int],
    output: Path,
    *,
    crf: int,
    scale: float,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = OUTPUT_DIR / "_webm_frames" / temp_key_for_output(output)
    shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        frame_paths = []
        has_alpha = False
        for index, frame in enumerate(frames):
            prepared = scaled_webm_frame(frame, scale)
            has_alpha = has_alpha or frame_has_alpha(prepared)
            frame_path = temp_dir / f"frame_{index:06d}.png"
            prepared.save(frame_path)
            frame_paths.append(frame_path)

        manifest = temp_dir / "frames.txt"
        write_concat_manifest(frame_paths, durations, manifest)
        args = [
            ffmpeg_path(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest),
            "-an",
            "-c:v",
            "libvpx-vp9",
            "-b:v",
            "0",
            "-crf",
            str(crf),
            "-deadline",
            "good",
            "-cpu-used",
            "4",
            "-row-mt",
            "1",
            "-pix_fmt",
            "yuva420p" if has_alpha else "yuv420p",
            str(output),
        ]
        if has_alpha:
            args[-3:-3] = ["-auto-alt-ref", "0"]
        subprocess.run(args, check=True)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def save_compressed_animated_webm(
    frames: list[Image.Image],
    durations: list[int],
    output: Path,
    *,
    max_bytes: int | None = DEFAULT_MAX_OUTPUT_BYTES,
) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    trial_dir = OUTPUT_DIR / "_webm_trials" / temp_key_for_output(output)
    shutil.rmtree(trial_dir, ignore_errors=True)
    trial_dir.mkdir(parents=True, exist_ok=True)
    best: tuple[int, Path, int, float] | None = None

    try:
        for crf, scale in WEBM_COMPRESS_CANDIDATES:
            trial = trial_dir / f"crf{crf}_s{int(scale * 100)}.webm"
            save_webm(frames, durations, trial, crf=crf, scale=scale)
            size = trial.stat().st_size
            if best is None or size < best[0]:
                best = (size, trial, crf, scale)
            if max_bytes is None or size <= max_bytes:
                shutil.copy2(trial, output)
                return {"crf": crf, "scale": scale, "targetBytes": max_bytes, "size": output.stat().st_size}

        if best is None:
            raise RuntimeError("No animated WebM candidate was generated")
        _size, trial, crf, scale = best
        shutil.copy2(trial, output)
        return {"crf": crf, "scale": scale, "targetBytes": max_bytes, "size": output.stat().st_size}
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
    animated_format: str = DEFAULT_ANIMATED_FORMAT,
) -> dict:
    source = find_source(asset_id)
    frame_paths = sorted((FRAMES_DIR / asset_id).glob("frame_*.png"))
    if not frame_paths:
        raise FileNotFoundError(f"No extracted frames found for {asset_id}")

    requested_animated_format = normalize_animated_format(animated_format)
    frame_map = asset_annotations.get("frames", {})
    durations, loop = source_timing(source, len(frame_paths))
    animated = image_is_animated(source) and len(frame_paths) > 1
    report_output_root = output_root or output_dir()
    output_format = requested_animated_format if animated else "webp"
    output = output_path_for_source(source, suffix, report_output_root, f".{output_format}")

    processed: list[Image.Image] = []
    touched = 0
    for index, frame_path in enumerate(frame_paths):
        shapes = frame_map.get(str(index), [])
        if shapes:
            touched += 1
        with Image.open(frame_path) as frame:
            processed.append(apply_mosaic(frame, shapes))

    if animated and output_format == "webm":
        encode = save_compressed_animated_webm(
            processed,
            durations,
            output,
            max_bytes=max_bytes,
        )
    elif animated:
        encode = save_compressed_animated_webp(
            processed,
            durations,
            loop,
            output,
            max_bytes=max_bytes,
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
        "format": output_format,
        "frameCount": len(frame_paths),
        "censoredFrameCount": touched,
        "quality": encode.get("quality"),
        "crf": encode.get("crf"),
        "scale": encode.get("scale"),
        "targetBytes": encode.get("targetBytes"),
    }
    codec_detail = f"crf={report['crf']}" if animated and output_format == "webm" else f"q={report['quality']}"
    print(
        f"{asset_id}: {len(frame_paths)} frames, censored {touched}, "
        f"{report['kind']} {report['format']} {codec_detail} scale={report['scale']}, "
        f"size={report['size']}, output={output}"
    )
    return report


def process_asset(
    asset_id: str,
    asset_annotations: dict,
    suffix: str,
    webp_lossless: bool,
    animated_format: str = DEFAULT_ANIMATED_FORMAT,
) -> Path:
    report = process_asset_report(asset_id, asset_annotations, suffix, webp_lossless, animated_format=animated_format)
    return Path(report["output"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset", action="append", help="Only process this asset id. Can be repeated.")
    parser.add_argument("--suffix", default="")
    parser.add_argument("--webp-lossless", action="store_true")
    parser.add_argument("--animated-format", choices=sorted(ANIMATED_OUTPUT_FORMATS), default=DEFAULT_ANIMATED_FORMAT)
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
            animated_format=args.animated_format,
        )
        outputs.append(Path(report["output"]))
    print("done")
    for path in outputs:
        print(path)


if __name__ == "__main__":
    main()
