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

    p = sub.add_parser("report", help="write the HTML + CSV report for one batch")
    p.add_argument("batch", help="batch .json (export from the app) or a report .html")
    p.add_argument("-d", "--devices", required=True, help="device_table.json of the batch's reticle")
    p.add_argument("-o", "--out", default=str(DEFAULT_OUT.parent / "reports"))

    p = sub.add_parser("serve", help="run the local inspection app (http://127.0.0.1:PORT)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--workspace", default=None, help="data folder (default local/workspace)")
    p.add_argument("--defaults", default=None, help="TOML with default config sections (default local/defaults.toml)")
    p.add_argument("--no-browser", action="store_true")

    p = sub.add_parser("synthetic", help="write the synthetic test reticle")
    p.add_argument("out", nargs="?", default=str(DEFAULT_OUT.parent / "synthetic.gds"))

    a = ap.parse_args(argv)
    if a.cmd == "list":
        from .extract.listing import describe
        print(describe(a.reticle, a.depth, a.max_children))
    elif a.cmd == "extract":
        from .config import load_config
        from .extract import extract, summary, write_outputs
        geometry: dict = {}
        table = extract(a.reticle, load_config(a.config), geometry)
        jp, cp = write_outputs(table, a.out, a.stem, geometry)
        print(summary(table))
        print(f"\nWrote {jp}\n      {cp}")
    elif a.cmd == "report":
        import json
        from .batch import load_failure_modes, migrate
        from .report import extract_report_data, write_report
        text = Path(a.batch).read_text(encoding="utf-8-sig")
        batch = migrate(json.loads(text) if text.lstrip().startswith("{") else extract_report_data(text)["batch"])
        table = json.loads(Path(a.devices).read_text(encoding="utf-8"))
        if table["source"]["gate_sha256"] != batch["reticle"]["gate_sha256"]:
            print("warning: device table comes from a different reticle file than the batch")
        print(f"Wrote {write_report(batch, table, load_failure_modes(), a.out)} (+ CSVs)")
    elif a.cmd == "serve":
        from .server import DEFAULT_DEFAULTS, DEFAULT_WORKSPACE, serve
        serve(a.workspace or DEFAULT_WORKSPACE, a.port, not a.no_browser, a.defaults or DEFAULT_DEFAULTS)
    elif a.cmd == "synthetic":
        from .synthetic import build
        print(f"Wrote {build(a.out)}")
    return 0
