"""Command line: python -m mty <command> ..."""
from __future__ import annotations

import argparse
from pathlib import Path

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "local" / "extracted"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mty", description="Mask yield tracker")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="list layers, top cells and hierarchy of a reticle file")
    p.add_argument("reticle")
    p.add_argument("--depth", type=int, default=3)
    p.add_argument("--max-children", type=int, default=12)

    p = sub.add_parser("extract", help="extract the device table from a reticle file")
    p.add_argument("reticle")
    p.add_argument("-c", "--config", required=True, help="TOML config (keep real configs in local/)")
    p.add_argument("-o", "--out", default=str(DEFAULT_OUT), help=f"output folder (default {DEFAULT_OUT})")
    p.add_argument("--stem", default="device_table", help="output file name stem")

    p = sub.add_parser("synthetic", help="write the synthetic test reticle")
    p.add_argument("out", nargs="?", default=str(DEFAULT_OUT.parent / "synthetic.gds"))

    a = ap.parse_args(argv)
    if a.cmd == "list":
        from .extract.listing import describe
        print(describe(a.reticle, a.depth, a.max_children))
    elif a.cmd == "extract":
        from .config import load_config
        from .extract import extract, summary, write_outputs
        table = extract(a.reticle, load_config(a.config))
        jp, cp = write_outputs(table, a.out, a.stem)
        print(summary(table))
        print(f"\nWrote {jp}\n      {cp}")
    elif a.cmd == "synthetic":
        from .synthetic import build
        print(f"Wrote {build(a.out)}")
    return 0
