#!/usr/bin/env python3
"""Export a few CT slices as PNG so they can be opened in any image viewer.

DICOM stores Hounsfield units, not display colors. A lung window
(center -600 HU, width 1500 HU) is what makes nodules visible.
"""

import argparse
from pathlib import Path

import numpy as np
import pydicom
from PIL import Image, ImageDraw


LUNG_CENTER, LUNG_WIDTH = -600, 1500


def series_for_patient(ct_root: Path, patient_id: str) -> list[Path]:
    found = []
    for series_dir in sorted(p for p in ct_root.iterdir() if p.is_dir()):
        sample = next(series_dir.glob("*.dcm"), None)
        if sample is None:
            continue
        ds = pydicom.dcmread(sample, stop_before_pixels=True)
        if str(getattr(ds, "PatientID", "")) == patient_id:
            found.append(series_dir)
    return found


def load_series(series_dir: Path) -> list[pydicom.Dataset]:
    slices = [pydicom.dcmread(p) for p in series_dir.glob("*.dcm")]
    slices.sort(key=lambda ds: float(ds.ImagePositionPatient[2]))
    return slices


def to_hu(ds: pydicom.Dataset) -> np.ndarray:
    pixels = ds.pixel_array.astype(np.float32)
    return pixels * float(ds.RescaleSlope) + float(ds.RescaleIntercept)


def window_to_png(hu: np.ndarray, center: float, width: float) -> Image.Image:
    low = center - width / 2
    high = center + width / 2
    scaled = np.clip((hu - low) / (high - low), 0, 1)
    return Image.fromarray((scaled * 255).astype(np.uint8))


def mark(image: Image.Image, x: int, y: int) -> Image.Image:
    rgb = image.convert("RGB")
    draw = ImageDraw.Draw(rgb)
    r = 12
    draw.ellipse((x - r, y - r, x + r, y + r), outline=(255, 60, 60), width=2)
    return rgb


def closest_slice(slices: list[pydicom.Dataset], z_mm: float) -> tuple[int, pydicom.Dataset]:
    index = min(
        range(len(slices)),
        key=lambda i: abs(float(slices[i].ImagePositionPatient[2]) - z_mm),
    )
    return index, slices[index]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--patient", default="RIDER-1500037140")
    parser.add_argument("--ct-root", type=Path, default=Path("data/ct"))
    parser.add_argument("--out", type=Path, default=Path("data/previews"))
    parser.add_argument("--x", type=int, action="append", help="Lesion column, one per scan")
    parser.add_argument("--y", type=int, action="append", help="Lesion row, one per scan")
    parser.add_argument("--z", type=float, action="append", help="Lesion z in mm, one per scan")
    args = parser.parse_args()

    # Defaults are the first row of the RIDER lesion-notes spreadsheet.
    xs = args.x or [135, 143]
    ys = args.y or [191, 180]
    zs = args.z or [-62.5, -72.5]

    out_dir = args.out / args.patient
    out_dir.mkdir(parents=True, exist_ok=True)
    series_dirs = series_for_patient(args.ct_root, args.patient)
    if not series_dirs:
        raise SystemExit(f"no CT series for {args.patient}")

    for scan_i, series_dir in enumerate(series_dirs, start=1):
        slices = load_series(series_dir)
        z_mm = zs[min(scan_i - 1, len(zs) - 1)]
        x = xs[min(scan_i - 1, len(xs) - 1)]
        y = ys[min(scan_i - 1, len(ys) - 1)]
        index, lesion = closest_slice(slices, z_mm)
        hu = to_hu(lesion)
        stored_center = float(lesion.WindowCenter)
        stored_width = float(lesion.WindowWidth)
        actual_z = float(lesion.ImagePositionPatient[2])
        print(
            f"scan {scan_i}: {len(slices)} slices, lesion slice {index + 1} "
            f"z={actual_z:.1f} mm (asked {z_mm}), stored window {stored_center}/{stored_width}"
        )
        mark(window_to_png(hu, LUNG_CENTER, LUNG_WIDTH), x, y).save(
            out_dir / f"scan{scan_i}_lesion_lung.png"
        )
        mark(window_to_png(hu, stored_center, stored_width), x, y).save(
            out_dir / f"scan{scan_i}_lesion_as_stored.png"
        )
        for offset in (-8, 8):
            neighbor = slices[min(max(index + offset, 0), len(slices) - 1)]
            window_to_png(to_hu(neighbor), LUNG_CENTER, LUNG_WIDTH).save(
                out_dir / f"scan{scan_i}_{offset:+d}_slices_lung.png"
            )


if __name__ == "__main__":
    main()
