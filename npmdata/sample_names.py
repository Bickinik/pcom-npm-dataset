"""Draw a random sample of npm package names from the all-the-package-names tarball.

This is a bulk source (one download, about 28 MB) instead of per-package search calls.
names.json is read straight from the archive; nothing is extracted.

    python -m npmdata.sample_names cache/bulk/all-the-package-names-2.0.2619.tgz \
        --n 30000 --seed 42 --out extra_packages_random.txt

Most random names have no stable release in the 12-month window, so build_dataset
drops them; expect only a fraction of the sample to produce rows.
"""
from __future__ import annotations

import argparse
import json
import random
import tarfile
from pathlib import Path


def read_names(tarball: Path) -> list[str]:
    with tarfile.open(tarball) as archive:
        member = archive.getmember("package/names.json")
        with archive.extractfile(member) as fh:
            names = json.load(fh)
    return sorted({n for n in names if isinstance(n, str) and n.strip()})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tarball", type=Path)
    parser.add_argument("--n", type=int, default=30000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("extra_packages_random.txt"))
    args = parser.parse_args()

    names = read_names(args.tarball)
    sample = sorted(random.Random(args.seed).sample(names, min(args.n, len(names))))
    header = (
        f"# {len(sample)} names sampled uniformly (seed {args.seed}) "
        f"from {len(names)} in {args.tarball.name}\n"
    )
    args.out.write_text(header + "\n".join(sample) + "\n", encoding="utf-8")
    print(f"{len(names)} names, wrote {len(sample)} to {args.out}")


if __name__ == "__main__":
    main()
