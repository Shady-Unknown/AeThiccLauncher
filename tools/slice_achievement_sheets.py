#!/usr/bin/env python3
"""Slice a launcher achievement grid into one PNG per achievement.

The grid is read left-to-right, top-to-bottom and mapped to the order of the
``achievements`` array in the definitions file. This is the build-time step
described in ``plan.md`` section 13.7: the runtime only loads the single PNGs.

Typical usage (from the repository root):

    python tools/slice_achievement_sheets.py

Defaults:
    image        .media/achievements_launcher_grid.webp
    definitions  data/launcher_achievements.json if present, otherwise
                 Audit/launcher_achievements.draft.json
    output       data/achievements
    grid         8 columns x 5 rows
    size         256 x 256 PNG

The outer background is detected from the image border and made transparent
with a flood fill, so the dark sheet background and the gaps disappear while
dark pixels inside an icon are preserved. Use --keep-background to disable it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import deque
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_IMAGE = ROOT / ".media" / "achievements_launcher_grid.webp"
DEFAULT_OUTPUT = ROOT / "data" / "achievements"
DEFINITION_CANDIDATES = (
    ROOT / "data" / "launcher_achievements.json",
    ROOT / "Audit" / "launcher_achievements.draft.json",
)
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Slice a launcher achievement grid into individual PNGs."
    )
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE,
                        help=f"source grid (default: {DEFAULT_IMAGE.relative_to(ROOT)})")
    parser.add_argument("--definitions", type=Path, default=None,
                        help="JSON file containing the achievements array in grid order")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT,
                        help=f"output directory (default: {DEFAULT_OUTPUT.relative_to(ROOT)})")
    parser.add_argument("--cols", type=int, default=8, help="grid columns (default: 8)")
    parser.add_argument("--rows", type=int, default=5, help="grid rows (default: 5)")
    parser.add_argument("--size", type=int, default=256, help="output square size (default: 256)")
    parser.add_argument("--tolerance", type=int, default=18,
                        help="background color tolerance, 0-255 (default: 18)")
    parser.add_argument("--padding", type=int, default=2,
                        help="pixels added around each cell before resizing (default: 2)")
    parser.add_argument("--keep-background", action="store_true",
                        help="keep the sheet background instead of making it transparent")
    parser.add_argument("--preview", type=Path, default=None,
                        help="optional contact sheet output path")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the mapping without writing files")
    return parser.parse_args()


def resolve_definitions(explicit: Path | None) -> Path:
    if explicit is not None:
        path = explicit if explicit.is_absolute() else ROOT / explicit
        if not path.exists():
            raise FileNotFoundError(f"definitions file not found: {path}")
        return path
    for candidate in DEFINITION_CANDIDATES:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "no definitions file found; expected data/launcher_achievements.json "
        "or Audit/launcher_achievements.draft.json"
    )


def load_ids(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    achievements = data.get("achievements")
    if not isinstance(achievements, list):
        raise ValueError(f"{path}: missing 'achievements' array")

    ids: list[str] = []
    for index, entry in enumerate(achievements, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: achievement #{index} is not an object")
        achievement_id = str(entry.get("id") or "").strip()
        if not achievement_id:
            raise ValueError(f"{path}: achievement #{index} has no id")
        if not SAFE_ID.match(achievement_id):
            raise ValueError(f"{path}: unsafe achievement id {achievement_id!r}")
        ids.append(achievement_id)
    return ids


def is_background(pixel: tuple[int, int, int, int], bg: tuple[int, int, int],
                  tolerance: int) -> bool:
    if pixel[3] == 0:
        return True
    return (
        abs(pixel[0] - bg[0]) <= tolerance
        and abs(pixel[1] - bg[1]) <= tolerance
        and abs(pixel[2] - bg[2]) <= tolerance
    )


def detect_background(image: Image.Image) -> tuple[int, int, int]:
    """Return the median color of the image border."""
    pixels = image.load()
    width, height = image.size
    samples: list[tuple[int, int, int]] = []
    for x in range(width):
        samples.append(pixels[x, 0][:3])
        samples.append(pixels[x, height - 1][:3])
    for y in range(1, height - 1):
        samples.append(pixels[0, y][:3])
        samples.append(pixels[width - 1, y][:3])
    samples.sort()
    return samples[len(samples) // 2]


def make_background_transparent(image: Image.Image, tolerance: int) -> Image.Image:
    """Remove the border-connected background, keeping dark pixels inside icons."""
    rgba = image.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()
    bg = detect_background(rgba)
    alpha = Image.new("L", (width, height), 255)
    alpha_pixels = alpha.load()
    visited = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()

    def enqueue(x: int, y: int) -> None:
        index = y * width + x
        if not visited[index] and is_background(pixels[x, y], bg, tolerance):
            visited[index] = 1
            queue.append((x, y))

    for x in range(width):
        enqueue(x, 0)
        enqueue(x, height - 1)
    for y in range(1, height - 1):
        enqueue(0, y)
        enqueue(width - 1, y)

    while queue:
        x, y = queue.popleft()
        alpha_pixels[x, y] = 0
        if x > 0:
            enqueue(x - 1, y)
        if x + 1 < width:
            enqueue(x + 1, y)
        if y > 0:
            enqueue(x, y - 1)
        if y + 1 < height:
            enqueue(x, y + 1)

    rgba.putalpha(alpha)
    return rgba


def find_bands(flags: list[bool]) -> list[tuple[int, int]]:
    """Return inclusive (start, end) runs of True values."""
    bands: list[tuple[int, int]] = []
    start = None
    for index, value in enumerate(flags):
        if value and start is None:
            start = index
        elif not value and start is not None:
            bands.append((start, index - 1))
            start = None
    if start is not None:
        bands.append((start, len(flags) - 1))
    return bands


def detect_cells(image: Image.Image, cols: int, rows: int,
                 tolerance: int) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Detect the cell bands by projecting non-background pixels."""
    rgba = image.convert("RGBA")
    width, height = rgba.size
    pixels = rgba.load()
    alpha_min, _ = rgba.getchannel("A").getextrema()
    use_alpha = alpha_min < 255
    bg = detect_background(rgba) if not use_alpha else (0, 0, 0)

    def is_fg(x: int, y: int) -> bool:
        pixel = pixels[x, y]
        if use_alpha:
            # Transparent sheets: the gap between cells is alpha 0. A tiny
            # threshold ignores near-transparent shadows that could bridge cells.
            return pixel[3] > 8
        return not (
            abs(pixel[0] - bg[0]) <= tolerance
            and abs(pixel[1] - bg[1]) <= tolerance
            and abs(pixel[2] - bg[2]) <= tolerance
        )

    column_flags = [any(is_fg(x, y) for y in range(height)) for x in range(width)]
    row_flags = [any(is_fg(x, y) for x in range(width)) for y in range(height)]
    column_bands = find_bands(column_flags)
    row_bands = find_bands(row_flags)

    if len(column_bands) != cols or len(row_bands) != rows:
        raise ValueError(
            f"detected a {len(column_bands)}x{len(row_bands)} grid, expected "
            f"{cols}x{rows}. Try a different --tolerance or pass the correct "
            "--cols/--rows."
        )
    return column_bands, row_bands


def make_preview(images: list[Path], cols: int, rows: int, output: Path) -> None:
    """Small contact sheet, useful for eyeballing a sliced set."""
    cell = 128
    gap = 8
    canvas = Image.new("RGBA", (cols * cell + (cols + 1) * gap,
                                rows * cell + (rows + 1) * gap),
                       (29, 30, 36, 255))
    for index, path in enumerate(images):
        with Image.open(path) as icon:
            thumb = icon.convert("RGBA").resize((cell, cell), Image.Resampling.LANCZOS)
        column = index % cols
        row = index // cols
        x = gap + column * (cell + gap)
        y = gap + row * (cell + gap)
        canvas.alpha_composite(thumb, (x, y))
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="PNG", optimize=True)


def main() -> int:
    args = parse_args()
    if args.cols <= 0 or args.rows <= 0:
        print("error: --cols and --rows must be positive", file=sys.stderr)
        return 2
    if args.size <= 0:
        print("error: --size must be positive", file=sys.stderr)
        return 2

    try:
        image_path = args.image if args.image.is_absolute() else ROOT / args.image
        if not image_path.exists():
            raise FileNotFoundError(f"image not found: {image_path}")
        definitions_path = resolve_definitions(args.definitions)
        ids = load_ids(definitions_path)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    expected = args.cols * args.rows
    if len(ids) != expected:
        print(
            f"error: {definitions_path.name} contains {len(ids)} achievements, "
            f"but the grid is {args.cols}x{args.rows} = {expected} cells",
            file=sys.stderr,
        )
        return 2
    if len(set(ids)) != len(ids):
        print("error: duplicate achievement ids in the definitions file", file=sys.stderr)
        return 2

    with Image.open(image_path) as source:
        source.load()
        try:
            column_bands, row_bands = detect_cells(source, args.cols, args.rows,
                                                   args.tolerance)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

        sheet = source.convert("RGBA")
        if not args.keep_background:
            sheet = make_background_transparent(sheet, args.tolerance)

        output_dir = args.out if args.out.is_absolute() else ROOT / args.out
        if not args.dry_run:
            output_dir.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        for index, achievement_id in enumerate(ids):
            column = index % args.cols
            row = index // args.cols
            left, right = column_bands[column]
            top, bottom = row_bands[row]
            left = max(0, left - args.padding)
            top = max(0, top - args.padding)
            right = min(sheet.width, right + 1 + args.padding)
            bottom = min(sheet.height, bottom + 1 + args.padding)

            cell = sheet.crop((left, top, right, bottom))
            cell = cell.resize((args.size, args.size), Image.Resampling.LANCZOS)
            destination = output_dir / f"{achievement_id}.png"
            if args.dry_run:
                print(
                    f"{index + 1:02d} {achievement_id:24s} "
                    f"row {row + 1} col {column + 1} -> {left},{top},{right},{bottom}"
                )
            else:
                cell.save(destination, format="PNG", optimize=True)
                written.append(destination)

    if args.dry_run:
        print(f"dry run: {len(ids)} cells, no files written")
        return 0

    print(
        f"sliced {len(written)} achievements from {image_path.name} "
        f"({args.cols}x{args.rows}, {args.size}x{args.size}) -> "
        f"{output_dir}"
    )
    if args.preview is not None:
        preview_path = args.preview if args.preview.is_absolute() else ROOT / args.preview
        make_preview(written, args.cols, args.rows, preview_path)
        print(f"preview: {preview_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
