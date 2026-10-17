"""Click-to-outline proposal. Grows a 3D region from the clicked voxel."""

from __future__ import annotations

import numpy as np


def propose_mask(volume: np.ndarray, z: int, y: int, x: int, spacing_mm: tuple[float, float, float]) -> np.ndarray:
    """Return a boolean volume the same shape as `volume`."""
    depth, rows, cols = volume.shape
    if not (0 <= z < depth and 0 <= y < rows and 0 <= x < cols):
        raise ValueError("That click is outside the scan.")
    seed_hu = float(volume[z, y, x])
    if seed_hu < -700:
        raise ValueError("Click the nodule, not the air around it.")

    z_spacing, row_spacing, col_spacing = spacing_mm
    radius_mm = 40.0
    z_radius = max(2, int(round(radius_mm / z_spacing)))
    y_radius = max(2, int(round(radius_mm / row_spacing)))
    x_radius = max(2, int(round(radius_mm / col_spacing)))
    z0, z1 = max(0, z - z_radius), min(depth, z + z_radius + 1)
    y0, y1 = max(0, y - y_radius), min(rows, y + y_radius + 1)
    x0, x1 = max(0, x - x_radius), min(cols, x + x_radius + 1)
    crop = volume[z0:z1, y0:y1, x0:x1]
    seed = (z - z0, y - y0, x - x0)

    mask = _grow(crop, seed, max(seed_hu - 180.0, -400.0), min(seed_hu + 180.0, 400.0))
    if int(mask.sum()) < 30:
        mask = _grow(crop, seed, max(seed_hu - 300.0, -500.0), min(seed_hu + 300.0, 500.0))
    full = np.zeros(volume.shape, dtype=bool)
    full[z0:z1, y0:y1, x0:x1] = mask
    if int(full.sum()) < 8:
        raise ValueError("No nodule tissue was found at that click. Try the bright part of the nodule.")
    return full


def _grow(crop: np.ndarray, seed: tuple[int, int, int], low: float, high: float) -> np.ndarray:
    allowed = (crop >= low) & (crop <= high)
    if not allowed[seed]:
        allowed[seed] = True
    region = np.zeros(crop.shape, dtype=bool)
    region[seed] = True
    limit = max(crop.shape)
    for _ in range(limit):
        grown = region.copy()
        grown[1:] |= region[:-1]
        grown[:-1] |= region[1:]
        grown[:, 1:] |= region[:, :-1]
        grown[:, :-1] |= region[:, 1:]
        grown[:, :, 1:] |= region[:, :, :-1]
        grown[:, :, :-1] |= region[:, :, 1:]
        grown &= allowed
        if np.array_equal(grown, region):
            break
        region = grown
    return region


def paint(mask: np.ndarray, x: float, y: float, radius: float, add: bool) -> None:
    rows, cols = mask.shape
    yy, xx = np.ogrid[:rows, :cols]
    circle = (xx - x) ** 2 + (yy - y) ** 2 <= radius ** 2
    if add:
        mask[circle] = True
    else:
        mask[circle] = False
