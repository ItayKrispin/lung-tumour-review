#!/usr/bin/env python3
"""Download DICOM series listed in TCIA .tcia manifests."""

import argparse
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.request import urlopen

API = "https://services.cancerimagingarchive.net/nbia-api/services/v1/getImage?SeriesInstanceUID={uid}"


def series_uids(manifest: Path) -> list[str]:
    uids = []
    for line in manifest.read_text().splitlines():
        line = line.strip()
        if line and line[0].isdigit():
            uids.append(line)
    return uids


def already_extracted(dest: Path) -> bool:
    return dest.is_dir() and any(dest.glob("*.dcm"))


def download_one(uid: str, dest_root: Path, zip_root: Path, retries: int = 4) -> str:
    dest = dest_root / uid
    if already_extracted(dest):
        return f"skip {uid}"

    dest.mkdir(parents=True, exist_ok=True)
    zip_path = zip_root / f"{uid}.zip"
    url = API.format(uid=uid)
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            with urlopen(url, timeout=180) as response:
                zip_path.write_bytes(response.read())
            if zip_path.stat().st_size < 200 or zip_path.read_bytes()[:2] != b"PK":
                raise RuntimeError(f"not a zip ({zip_path.stat().st_size} bytes)")
            with zipfile.ZipFile(zip_path) as archive:
                for info in archive.infolist():
                    if info.is_dir() or not info.filename.lower().endswith(".dcm"):
                        continue
                    target = dest / Path(info.filename).name
                    with archive.open(info) as src, target.open("wb") as out:
                        out.write(src.read())
            zip_path.unlink(missing_ok=True)
            n = len(list(dest.glob("*.dcm")))
            if n == 0:
                raise RuntimeError("zip contained no DICOM files")
            return f"ok {uid} ({n} files)"
        except Exception as exc:
            last_error = exc
            zip_path.unlink(missing_ok=True)
            time.sleep(min(8, attempt * 2))
    return f"FAIL {uid}: {last_error}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    uids = series_uids(args.manifest)
    zip_root = Path("data/_zips")
    zip_root.mkdir(parents=True, exist_ok=True)
    args.dest.mkdir(parents=True, exist_ok=True)
    print(f"{args.manifest.name}: {len(uids)} series -> {args.dest}", flush=True)
    done = 0
    failures = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(download_one, uid, args.dest, zip_root) for uid in uids]
        for future in as_completed(futures):
            done += 1
            message = future.result()
            if message.startswith("FAIL"):
                failures.append(message)
            print(f"[{done}/{len(uids)}] {message}", flush=True)
    print(f"finished {args.dest} failures={len(failures)}", flush=True)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
