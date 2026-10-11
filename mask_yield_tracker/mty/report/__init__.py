"""Batch yield report."""
from .aggregate import aggregate, result_rows  # noqa: F401
from .html import build_report, extract_report_data  # noqa: F401


def write_report(batch: dict, table: dict, modes: list[dict], out_dir, stem: str | None = None):
    """Write <stem>.html plus CSVs (yield by CD x dose, per die, all results)."""
    import csv
    from pathlib import Path

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = stem or f"{batch['id']}_report"
    results = result_rows(batch, table)
    html_path = out / f"{stem}.html"
    html_path.write_text(build_report(batch, table, modes), encoding="utf-8")
    agg = aggregate(batch, table, modes)
    yc = ["sites", "inspected", "pass", "yield", "yield_lo", "yield_hi", "coverage"]

    def dump(name, rows, cols):
        with open(out / f"{stem}_{name}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(cols + [f"n_{m}" for m in agg["mode_ids"]])
            for r in rows:
                w.writerow([r.get(c) for c in cols] + [r.get("modes", {}).get(m) for m in agg["mode_ids"]])

    dump("yield_by_cd_dose", [{**r, "cd": r["key"]} for r in agg["cd_cond"]], ["cd", "dose", "focus"] + yc)
    dump("yield_by_cd_pitch_dose", [{**r, "cd": r["key"]} for r in agg["cdpitch_cond"]], ["cd", "pitch_nm", "dose", "focus"] + yc)
    dump("dies", agg["dies"], ["die", "col", "row", "dose", "focus"] + yc)
    dump("resist_by_dose", [{**r, "grouping": dim, "bin": r["key"]} for dim in ("width", "area", "anchor")
                            for r in agg["resist"][dim]["cond"]],
         ["grouping", "bin", "dose", "focus", "resist_fail", "resist_fail_rate"] + yc)
    with open(out / f"{stem}_results.csv", "w", newline="", encoding="utf-8") as f:
        cols = ["die", "col", "row", "dose", "focus", "device_id", "name", "group", "kind", "cd_nm", "pitch_nm", "thinnest_nm",
                "resist_min_width_nm", "resist_min_area_um2", "resist_max_length_um", "status", "ts"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(results)
    return html_path
