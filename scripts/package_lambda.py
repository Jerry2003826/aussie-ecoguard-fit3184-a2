"""Create a stable, Linux-compatible zip and reject oversized Lambda packages."""
from pathlib import Path
import sys
from zipfile import ZipFile, ZIP_DEFLATED, ZipInfo

root, destination = map(Path, sys.argv[1:])
files = sorted(p for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc', '.pyo'})
total = sum(p.stat().st_size for p in files)
if total > 250 * 1024 * 1024:
    raise SystemExit('Uncompressed deployment exceeds Lambda 250 MiB package limit.')
destination.parent.mkdir(parents=True, exist_ok=True)
with ZipFile(destination, 'w', ZIP_DEFLATED, compresslevel=6) as archive:
    for path in files:
        info = ZipInfo(path.relative_to(root).as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        archive.writestr(info, path.read_bytes())
print(f'{destination}: {len(files)} files, {total:,} bytes unpacked, {destination.stat().st_size:,} bytes zipped')
