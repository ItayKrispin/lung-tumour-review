"""DICOM geometry, lung-window images, and run-length masks."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageDraw

LUNG_CENTER = -600.0
LUNG_WIDTH = 1500.0


def rle_encode(mask: np.ndarray) -> list[list[int]]:
    flat = np.ascontiguousarray(mask, dtype=np.uint8).reshape(-1)
    if flat.size == 0 or not flat.any():
        return []
    padded = np.concatenate(([0], flat, [0]))
    changes = np.diff(padded.astype(np.int16))
    starts = np.flatnonzero(changes == 1)
    ends = np.flatnonzero(changes == -1)
    return [[int(start), int(end - start)] for start, end in zip(starts, ends)]


def rle_decode(runs: list[list[int]], rows: int, cols: int) -> np.ndarray:
    flat = np.zeros(rows * cols, dtype=bool)
    for start, length in runs:
        flat[start : start + length] = True
    return flat.reshape(rows, cols)


def rle_to_mask_slices(slice_runs: dict[int, list[list[int]]], rows: int, cols: int) -> dict[int, np.ndarray]:
    return {index: rle_decode(runs, rows, cols) for index, runs in slice_runs.items() if runs}


def lung_png(hu: np.ndarray) -> bytes:
    low = LUNG_CENTER - LUNG_WIDTH / 2.0
    high = LUNG_CENTER + LUNG_WIDTH / 2.0
    scaled = np.clip((hu - low) / (high - low), 0, 1)
    image = Image.fromarray((scaled * 255).astype(np.uint8), mode="L")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def patient_xy_to_pixel(x: float, y: float, z: float, origin: list[float], spacing: list[float], orientation: list[float]) -> tuple[float, float]:
    """Map a patient-space point onto one axial slice. Returns (column, row)."""
    row_dir = np.array(orientation[:3], dtype=np.float64)
    col_dir = np.array(orientation[3:], dtype=np.float64)
    delta = np.array([x, y, z], dtype=np.float64) - np.array(origin, dtype=np.float64)
    column_spacing = float(spacing[1])
    row_spacing = float(spacing[0])
    column = float(np.dot(delta, row_dir) / column_spacing)
    row = float(np.dot(delta, col_dir) / row_spacing)
    return column, row


def fill_polygons(polygons: list[list[tuple[float, float]]], rows: int, cols: int) -> np.ndarray:
    """Rasterize closed contours. A contour whose center already lies inside is a hole."""
    mask = np.zeros((rows, cols), dtype=bool)
    for polygon in polygons:
        if len(polygon) < 3:
            continue
        points = [(int(round(x)), int(round(y))) for x, y in polygon]
        layer = Image.new("L", (cols, rows), 0)
        ImageDraw.Draw(layer).polygon(points, fill=1)
        filled = np.asarray(layer, dtype=bool)
        center_x = int(np.clip(round(sum(p[0] for p in points) / len(points)), 0, cols - 1))
        center_y = int(np.clip(round(sum(p[1] for p in points) / len(points)), 0, rows - 1))
        if mask[center_y, center_x]:
            mask[filled] = False
        else:
            mask |= filled
    return mask


def slice_spacing_mm(zs: list[float]) -> float:
    if len(zs) < 2:
        return 1.25
    diffs = np.abs(np.diff(np.sort(np.asarray(zs, dtype=np.float64))))
    diffs = diffs[diffs > 1e-3]
    if diffs.size == 0:
        return 1.25
    return float(np.median(diffs))
