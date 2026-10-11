"""Single-file HTML report for one batch (inline SVG, no external resources).

The report embeds its data (batch, device summary, aggregates) as JSON so the
app can re-open it (Batches -> Import accepts the .html file) and so the CSV
buttons work offline.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from html import escape

from .. import __version__
from .aggregate import aggregate, cd_short

REPORT_SCHEMA = "mty.report"
REPORT_VERSION = 1
DATA_ID = "mty-report-data"

SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]          # validated all-pairs; more focus values fold to small multiples
INK, INK2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#7a7974", "#e4e3df", "#fcfcfb"


def _e(v) -> str:
    return escape("" if v is None else str(v))


def _pct(v, digits=0) -> str:
    return "–" if v is None else f"{100 * v:.{digits}f}%"


def _num(v, digits=2) -> str:
    if v is None:
        return "–"
    return f"{v:.{digits}f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def _cond_label(dose, focus, units, focus_varies) -> str:
    s = f"{_num(dose)}" if dose is not None else "?"
    if focus_varies:
        s += f" / {_num(focus)}"
    return s


def _seq(y):
    if y is None:
        return None, INK2
    i = min(6, int(y * 7))
    return SEQ[i], ("#fff" if i >= 3 else INK)


# ---------------------------------------------------------------- charts ----

def _x_scale(values, x0, x1):
    nums = [v for v in values if isinstance(v, (int, float))]
    if len(nums) == len(values) and len(set(nums)) > 1:
        lo, hi = min(nums), max(nums)
        return lambda v: x0 + (v - lo) / (hi - lo) * (x1 - x0), True
    idx = {v: i for i, v in enumerate(values)}
    n = max(len(values) - 1, 1)
    return lambda v: x0 + idx[v] / n * (x1 - x0) if len(values) > 1 else (x0 + x1) / 2, False


def line_chart(series: list[dict], doses: list, title: str, w=560, h=240, small=False, dose_unit="") -> str:
    """series: [{"label", "color", "points": [(dose, yield|None, tooltip)]}]; y is 0..1."""
    ml, mr, mt, mb = (34, 10, 22 if small else 26, 26 if small else 34)
    x0, x1, y0, y1 = ml, w - mr, mt, h - mb
    sx, numeric = _x_scale(doses, x0 + 6, x1 - 6)
    sy = lambda v: y1 - v * (y1 - y0)  # noqa: E731
    out = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="{_e(title)}">',
           f'<text x="{ml}" y="{14 if small else 16}" class="ct">{_e(title)}</text>']
    for t in (0, 0.25, 0.5, 0.75, 1):
        y = sy(t)
        out.append(f'<line x1="{x0}" x2="{x1}" y1="{y:.1f}" y2="{y:.1f}" stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{x0 - 4}" y="{y + 3:.1f}" class="ax" text-anchor="end">{int(t * 100)}</text>')
    step = max(1, len(doses) // (5 if small else 10))
    for i, dv in enumerate(doses):
        if i % step and i != len(doses) - 1:
            continue
        out.append(f'<text x="{sx(dv):.1f}" y="{y1 + 13}" class="ax" text-anchor="middle">{_e(_num(dv))}</text>')
    if not small:
        out.append(f'<text x="{(x0 + x1) / 2}" y="{h - 4}" class="ax" text-anchor="middle">dose {_e(dose_unit)}</text>')
        out.append(f'<text x="10" y="{(y0 + y1) / 2}" class="ax" text-anchor="middle" transform="rotate(-90 10 {(y0 + y1) / 2})">yield %</text>')
    for s in series:
        pts = [(sx(d), sy(y), tip) for d, y, tip in s["points"] if y is not None]
        if len(pts) > 1:
            path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y, _) in enumerate(pts))
            out.append(f'<path d="{path}" fill="none" stroke="{s["color"]}" stroke-width="2" stroke-linejoin="round"/>')
        for x, y, tip in pts:
            out.append(f'<g class="pt"><circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{s["color"]}" stroke="{SURFACE}" stroke-width="2"/>'
                       f'<circle cx="{x:.1f}" cy="{y:.1f}" r="10" fill="transparent"><title>{_e(tip)}</title></circle></g>')
    if len(series) > 1:
        lx = x0
        for s in series:
            out.append(f'<rect x="{lx}" y="{h - 12}" width="10" height="3" fill="{s["color"]}"/>'
                       f'<text x="{lx + 14}" y="{h - 8}" class="ax">{_e(s["label"])}</text>')
            lx += 14 + 7 * len(s["label"]) + 16
    out.append("</svg>")
    return "".join(out)


def stacked_bars(rows: list[dict], modes: list[dict], title: str, label_w=150, w=720, bar_h=18) -> str:
    """Horizontal 100 % stacked bars of failure modes (share of inspected). rows: {label, modes, inspected}."""
    gap = 6
    h = 30 + len(rows) * (bar_h + gap) + 24
    x0, x1 = label_w, w - 60
    out = [f'<svg viewBox="0 0 {w} {h}" class="chart" role="img" aria-label="{_e(title)}">',
           f'<text x="0" y="16" class="ct">{_e(title)}</text>']
    for i, r in enumerate(rows):
        y = 30 + i * (bar_h + gap)
        out.append(f'<text x="{x0 - 8}" y="{y + bar_h / 2 + 4}" class="lb" text-anchor="end">{_e(r["label"])}</text>')
        n = r["inspected"]
        if not n:
            out.append(f'<rect x="{x0}" y="{y}" width="{x1 - x0}" height="{bar_h}" fill="none" stroke="{GRID}" stroke-dasharray="3 2"/>'
                       f'<text x="{x0 + 6}" y="{y + bar_h / 2 + 4}" class="ax">not inspected</text>')
            continue
        x = x0
        for m in modes:
            c = r["modes"].get(m["id"], 0)
            if not c:
                continue
            bw = c / n * (x1 - x0)
            out.append(f'<rect x="{x:.1f}" y="{y}" width="{bw:.1f}" height="{bar_h}" fill="{m["color"]}" stroke="{SURFACE}" stroke-width="2">'
                       f'<title>{_e(r["label"])}: {_e(m["label"])} {c}/{n} ({100 * c / n:.0f}%)</title></rect>')
            if bw >= 16:
                out.append(f'<text x="{x + bw / 2:.1f}" y="{y + bar_h / 2 + 4}" text-anchor="middle" class="key">{_e(m["key"].upper())}</text>')
            x += bw
        out.append(f'<text x="{x1 + 6}" y="{y + bar_h / 2 + 4}" class="ax">n={n}</text>')
    lx = x0
    for m in modes:
        out.append(f'<rect x="{lx}" y="{h - 14}" width="12" height="12" rx="2" fill="{m["color"]}"/>'
                   f'<text x="{lx + 6}" y="{h - 5}" text-anchor="middle" class="key">{_e(m["key"].upper())}</text>'
                   f'<text x="{lx + 16}" y="{h - 4}" class="ax">{_e(m["label"])}</text>')
        lx += 22 + 7 * len(m["label"]) + 14
    out.append("</svg>")
    return "".join(out)


def wafer_map(batch: dict, dies: list[dict]) -> str:
    w = batch["wafer"]
    cell, gap, top, left = 34, 3, 22, 4
    nc, nr = w["n_cols"], w["n_rows"]
    W, H = left + nc * (cell + gap), top + nr * (cell + gap)
    by = {d["die"]: d for d in dies}
    out = [f'<svg viewBox="0 0 {W} {H}" class="chart wafer" role="img" aria-label="wafer map coloured by yield">']
    for c in range(nc):
        dose = batch["splits"]["columns"][c].get("dose") if c < len(batch["splits"]["columns"]) else None
        out.append(f'<text x="{left + c * (cell + gap) + cell / 2}" y="14" class="ax" text-anchor="middle">{_e(_num(dose) if dose is not None else c)}</text>')
    for r in range(nr):
        for c in range(nc):
            d = by.get(f"{c},{r}")
            x, y = left + c * (cell + gap), top + r * (cell + gap)
            if d is None:
                continue
            fill, ink = _seq(d["yield"])
            dash = "" if fill else ' stroke-dasharray="3 2"'
            out.append(f'<g><rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="3" fill="{fill or "#fff"}" stroke="#a9a8a2"{dash}/>'
                       f'<text x="{x + cell / 2}" y="{y + cell / 2 + 4}" text-anchor="middle" class="wv" fill="{ink}">{"" if d["yield"] is None else round(100 * d["yield"])}</text>'
                       f'<title>Die {d["die"]}  dose {_num(d["dose"])}  {d["inspected"]}/{d["sites"]} inspected  yield {_pct(d["yield"])}</title></g>')
    out.append("</svg>")
    return "".join(out)


def legend_seq() -> str:
    sw = "".join(f'<span class="sw" style="background:{c}"></span>' for c in SEQ)
    return f'<div class="legend"><span class="sw none"></span> not inspected &nbsp; yield 0% {sw} 100%</div>'


# ---------------------------------------------------------------- tables ----

def heat_table(rows_by_key: dict, row_labels: list[tuple[str, str]], conds: list[tuple], cond_labels: list[str],
               head: str) -> str:
    """rows_by_key[(rowkey, cond)] -> agg row. Cells show yield % and n; colour = yield."""
    out = ['<div class="scroll"><table class="heat"><thead><tr>', f'<th>{_e(head)}</th>']
    out += [f'<th>{_e(lbl)}</th>' for lbl in cond_labels]
    out.append("<th>all</th></tr></thead><tbody>")
    for rk, rl in row_labels:
        out.append(f"<tr><th>{_e(rl)}</th>")
        tot_p = tot_n = 0
        for c in conds:
            r = rows_by_key.get((rk, c))
            if r is None or not r["inspected"]:
                out.append('<td class="na">–</td>')
                continue
            tot_p += r["pass"]
            tot_n += r["inspected"]
            fill, ink = _seq(r["yield"])
            tip = f'{rl} @ {c[0]}: {r["pass"]}/{r["inspected"]} pass, 95% CI {_pct(r["yield_lo"])}–{_pct(r["yield_hi"])}, coverage {_pct(r["coverage"])}'
            out.append(f'<td style="background:{fill};color:{ink}" title="{_e(tip)}">{round(100 * r["yield"])}<small>{r["inspected"]}</small></td>')
        y = tot_p / tot_n if tot_n else None
        fill, ink = _seq(y)
        out.append(f'<td style="background:{fill or "transparent"};color:{ink}" class="tot">{_pct(y)}<small>{tot_n or ""}</small></td></tr>')
    out.append("</tbody></table></div>")
    return "".join(out)


def die_table(dies: list[dict], modes: list[dict], units: dict, focus_varies: bool) -> str:
    cols = ["Die", f"Dose ({units['dose_unit']})"] + ([f"Focus ({units['focus_unit']})"] if focus_varies else []) + \
        ["Inspected", "Coverage", "Yield", "95% CI"] + [m["label"] for m in modes]
    out = ['<div class="scroll"><table class="list"><thead><tr>'] + [f"<th>{_e(c)}</th>" for c in cols] + ["</tr></thead><tbody>"]
    for d in sorted(dies, key=lambda d: (d["col"], d["row"])):
        cells = [d["die"], _num(d["dose"])] + ([_num(d["focus"])] if focus_varies else []) + [
            f'{d["inspected"]}/{d["sites"]}', _pct(d["coverage"]), _pct(d["yield"], 1),
            "–" if d["yield_lo"] is None else f'{_pct(d["yield_lo"])}–{_pct(d["yield_hi"])}'] + [d["modes"][m["id"]] for m in modes]
        out.append("<tr>" + "".join(f"<td>{_e(c)}</td>" for c in cells) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


# ---------------------------------------------------------------- page ----

CSS = """
:root{color-scheme:light;--ink:#0b0b0b;--ink2:#52514e;--muted:#7a7974;--line:#d9d8d3;--surface:#fcfcfb;--bg:#f4f4f2}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,"Segoe UI",Roboto,sans-serif}
.page{max-width:1180px;margin:0 auto;padding:24px 16px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 8px}h3{font-size:14px;margin:16px 0 6px}
.sub{color:var(--ink2)}.muted{color:var(--muted)}.small{font-size:12px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:16px;margin:12px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:16px 0}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:12px}
.tile .v{font-size:24px;font-weight:600}.tile .l{font-size:12px;color:var(--ink2)}
.meta{display:grid;grid-template-columns:max-content 1fr;gap:2px 14px;font-size:13px;margin:0}
.meta dt{color:var(--muted)}.meta dd{margin:0}
svg.chart{width:100%;height:auto;display:block}svg.chart text{font-family:inherit}
.ct{font-size:13px;font-weight:600;fill:var(--ink)}.ax{font-size:10px;fill:var(--muted)}.lb{font-size:11px;fill:var(--ink2)}
.key{font-size:10px;font-weight:700;fill:#fff}.wv{font-size:10px}
.pt:hover circle:first-child{r:6}
.multiples{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:8px}
.multiples .card{margin:0;padding:8px}
.scroll{overflow-x:auto}
table.heat{border-collapse:separate;border-spacing:2px;font-size:12px}
table.heat th{font-weight:600;color:var(--ink2);text-align:right;padding:2px 6px;white-space:nowrap}
table.heat thead th{text-align:center}
table.heat td{min-width:44px;text-align:center;padding:3px 2px;border-radius:3px;font-variant-numeric:tabular-nums}
table.heat td small{display:block;font-size:9px;opacity:.75}
table.heat td.na{color:var(--muted)}table.heat td.tot{font-weight:600;border:1px solid var(--line)}
table.list{border-collapse:collapse;width:100%;font-size:12px;font-variant-numeric:tabular-nums}
table.list th,table.list td{padding:4px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
table.list th:first-child,table.list td:first-child{text-align:left}
.legend{font-size:12px;color:var(--ink2);display:flex;align-items:center;gap:4px;flex-wrap:wrap;margin:6px 0}
.sw{display:inline-block;width:14px;height:12px;border-radius:2px}.sw.none{border:1px dashed #a9a8a2}
.wafer-wrap{display:grid;grid-template-columns:minmax(260px,420px) 1fr;gap:16px;align-items:start}
.actions{display:flex;gap:8px;flex-wrap:wrap}
button{font:inherit;padding:6px 12px;border:1px solid #a9a8a2;background:#fff;border-radius:6px;cursor:pointer}
button:hover{background:var(--bg)}
details summary{cursor:pointer;color:var(--ink2);margin:8px 0}
@media (max-width:760px){.wafer-wrap{grid-template-columns:1fr}}
@media print{body{background:#fff}.page{max-width:none;padding:0}.actions{display:none}.card,.tile{break-inside:avoid;border-color:#ccc}
 h2{break-after:avoid}details{display:block}details>*{display:block}}
"""

JS = r"""
const DATA = JSON.parse(document.getElementById("mty-report-data").textContent);
function csv(rows, cols) {
  const q = (v) => v === null || v === undefined ? "" : /[",\n]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : String(v);
  return [cols.join(",")].concat(rows.map((r) => cols.map((c) => q(typeof c === "string" ? r[c] : "")).join(","))).join("\n");
}
function save(name, text) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  a.download = name; document.body.append(a); a.click(); a.remove();
}
const A = DATA.aggregates, modes = A.mode_ids, base = DATA.batch.id;
const flat = (rows) => rows.map((r) => Object.assign({}, r, Object.fromEntries(modes.map((m) => [m, r.modes[m]]))));
const yc = ["sites", "inspected", "pass", "yield", "yield_lo", "yield_hi", "coverage"];
document.getElementById("csv-cd").onclick = () => save(base + "_yield_by_cd_dose.csv",
  csv(flat(A.cd_cond).map((r) => Object.assign(r, { cd: r.key })), ["cd", "dose", "focus", ...yc, ...modes]));
document.getElementById("csv-cdp").onclick = () => save(base + "_yield_by_cd_pitch_dose.csv",
  csv(flat(A.cdpitch_cond).map((r) => Object.assign(r, { cd: r.key })), ["cd", "pitch_nm", "dose", "focus", ...yc, ...modes]));
document.getElementById("csv-die").onclick = () => save(base + "_dies.csv", csv(flat(A.dies), ["die", "col", "row", "dose", "focus", ...yc, ...modes]));
function resultRows() {
  const dev = Object.fromEntries(DATA.device_table.devices.map((d) => [d.id, d]));
  const sp = DATA.batch.splits, rows = [];
  for (const key of Object.keys(DATA.batch.results).sort()) {
    const [col, row] = key.split(",").map(Number);
    const c = sp.columns[col] || {}, r = sp.rows[row] || {};
    const focus = r.focus ?? c.focus ?? null;
    for (const id of Object.keys(DATA.batch.results[key]).sort()) {
      const d = dev[id] || {}, res = DATA.batch.results[key][id];
      rows.push({ die: key, col, row, dose: c.dose ?? null, focus, device_id: id, name: d.name, group: d.group, kind: d.kind,
        cd_nm: d.cd_nm, pitch_nm: d.pitch_nm, thinnest_nm: d.thinnest_nm, status: res.status, ts: res.ts });
    }
  }
  return rows;
}
document.getElementById("csv-res").onclick = () => save(base + "_results.csv",
  csv(resultRows(), ["die", "col", "row", "dose", "focus", "device_id", "name", "group", "kind", "cd_nm", "pitch_nm", "thinnest_nm", "status", "ts"]));
"""


def build_report(batch: dict, table: dict, modes: list[dict]) -> str:
    agg = aggregate(batch, table, modes)
    meta, sp = batch["meta"], batch["splits"]
    units = {"dose_unit": sp.get("dose_unit", ""), "focus_unit": sp.get("focus_unit", "")}
    fv = agg["focus_varies"]
    conds = [(c["dose"], c["focus"]) for c in agg["conditions"]]
    cond_labels = [_cond_label(d, f, units, fv) for d, f in conds]
    doses = sorted({d for d, _ in conds if d is not None})
    focuses = sorted({f for _, f in conds}, key=lambda v: (v is None, v or 0))
    title = " / ".join(str(meta.get(k)) for k in ("lot", "wafer") if meta.get(k)) or batch["id"]
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def series_for(rows_by_cond, label_prefix=""):
        out = []
        for i, f in enumerate(focuses):
            pts = []
            for d in doses:
                r = rows_by_cond.get((d, f))
                if r and r["inspected"]:
                    tip = (f"{label_prefix}dose {_num(d)}" + (f", focus {_num(f)}" if fv else "") +
                           f": {r['pass']}/{r['inspected']} pass = {_pct(r['yield'])} (95% CI {_pct(r['yield_lo'])}–{_pct(r['yield_hi'])})")
                    pts.append((d, r["yield"], tip))
            out.append({"label": f"focus {_num(f)}" if fv else "all", "color": SERIES[i % len(SERIES)], "points": pts})
        return out

    overall = series_for({(c["dose"], c["focus"]): c for c in agg["conditions"]})
    cd_rows = defaultdict_rows(agg["cd_cond"])
    multiples = []
    for cd in agg["cds"]:
        if not cd["inspected"]:
            continue
        s = series_for({(r["dose"], r["focus"]): r for r in cd_rows.get(cd["key"], [])}, f"{cd['label']}, ")
        multiples.append(f'<div class="card">{line_chart(s, doses, f"{cd["short"]}  ·  {cd["n_devices"]} sites/die", w=280, h=170, small=True)}</div>')

    by_key = {(r["key"], (r["dose"], r["focus"])): r for r in agg["cd_cond"]}
    cd_heat = heat_table(by_key, [(c["key"], c["short"]) for c in agg["cds"]], conds, cond_labels, "CD \\ dose")
    pitch_keys = []
    for r in agg["cdpitch_cond"]:
        k = (r["key"], r["pitch_nm"])
        if k not in pitch_keys:
            pitch_keys.append(k)
    by_kp = {((r["key"], r["pitch_nm"]), (r["dose"], r["focus"])): r for r in agg["cdpitch_cond"]}
    cdp_heat = heat_table(by_kp, [(k, f"{cd_short(k[0])}, pitch {_num(k[1]) if k[1] else '–'}") for k in pitch_keys],
                          conds, cond_labels, "CD, pitch \\ dose")

    mode_by_cd = stacked_bars([{"label": c["short"], "modes": c["modes"], "inspected": c["inspected"]} for c in agg["cds"]],
                              modes, "Outcome by CD (share of inspected, all doses)", label_w=130, w=1100)
    mode_by_dose = stacked_bars([{"label": f"dose {lbl}", "modes": c["modes"], "inspected": c["inspected"]}
                                 for lbl, c in zip(cond_labels, agg["conditions"])],
                                modes, "Outcome by dose (share of inspected, all devices)", label_w=130, w=1100)

    t = agg["total"]
    tiles = [("Dies", len(batch["wafer"]["dies"])), ("Results recorded", f'{t["inspected"]} / {t["sites"]}'),
             ("Coverage", _pct(t["coverage"], 1)), ("Overall yield", _pct(t["yield"], 1))]
    meta_rows = "".join(f"<dt>{_e(k.replace('_', ' ').capitalize())}</dt><dd>{_e(v)}</dd>" for k, v in meta.items() if v not in (None, ""))
    data = {"schema": REPORT_SCHEMA, "version": REPORT_VERSION, "generated": generated, "generator": f"mty {__version__}",
            "batch": batch, "failure_modes": modes,
            "device_table": {"created": table.get("created"), "source": {k: table["source"].get(k) for k in ("gate_file", "gate_sha256", "sd_file")},
                             "devices": [{k: d.get(k) for k in ("id", "name", "group", "kind", "cd_nm", "pitch_nm", "thinnest_nm")} for d in table["devices"]]},
            "aggregates": agg}
    payload = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")  # safe inside <script>
    warn = (f'<p class="small" style="color:#d03b3b">{agg["unknown_results"]} recorded result(s) refer to devices that are not in the current device table and are not counted.</p>'
            if agg["unknown_results"] else "")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Yield report {_e(title)}</title><style>{CSS}</style></head>
<body><div class="page">
<h1>Yield report · {_e(title)}</h1>
<div class="sub">{_e(meta.get("process_step", ""))} · {_e(meta.get("date", ""))} · reticle {_e(batch["reticle"].get("name") or batch["reticle"]["id"])}</div>
<div class="small muted">Generated {_e(generated)} by mty {__version__} · batch {_e(batch["id"])}</div>
<div class="tiles">{"".join(f'<div class="tile"><div class="v">{_e(v)}</div><div class="l">{_e(k)}</div></div>' for k, v in tiles)}</div>
<div class="card"><dl class="meta">{meta_rows}<dt>Dose unit</dt><dd>{_e(units["dose_unit"])}</dd>{f'<dt>Focus unit</dt><dd>{_e(units["focus_unit"])}</dd>' if fv else ""}</dl>{warn}
<p class="small muted">Yield = pass / inspected. Sites without a recorded result are not counted; coverage = inspected / sites. Hover a point or cell for counts and the 95% Wilson interval. Survival does not mean the CD printed on target.</p>
<div class="actions"><button onclick="window.print()">Print / save as PDF</button><button id="csv-cd">CSV: yield by CD × dose</button><button id="csv-cdp">CSV: yield by CD + pitch × dose</button><button id="csv-die">CSV: per die</button><button id="csv-res">CSV: all results</button></div>
<p class="small muted">To reopen this report in the app: Batches → Import batch… and choose this file.</p></div>

<h2>Yield vs dose, all devices</h2>
<div class="card">{line_chart(overall, doses, "Yield vs dose (all devices)", w=1100, h=300, dose_unit=units["dose_unit"])}</div>
<div class="card">{mode_by_dose}</div>

<h2>Yield vs dose per CD</h2>
<p class="small muted">One panel per CD: finger width, or for structures without fingers (long lines, pads) "thinnest" = smallest drawn width. Same 0–100% scale in every panel.</p>
<div class="multiples">{"".join(multiples) or '<p class="muted">No results recorded yet.</p>'}</div>
<h3>CD × dose</h3>
<p class="small muted">Cell = yield % (number below = devices inspected). Column header = dose{" / focus" if fv else ""}.</p>
<div class="card">{cd_heat}{legend_seq()}</div>
<details><summary>CD + pitch × dose</summary><div class="card">{cdp_heat}{legend_seq()}</div></details>

<h2>Failure modes</h2>
<div class="card">{mode_by_cd}</div>

<h2>Per die</h2>
<div class="wafer-wrap"><div class="card">{wafer_map(batch, agg["dies"])}{legend_seq()}<p class="small muted">Column header = dose. Number = yield %.</p></div>
<div class="card">{die_table(agg["dies"], modes, units, fv)}</div></div>
</div>
<script type="application/json" id="{DATA_ID}">{payload}</script>
<script>{JS}</script>
</body></html>
"""


def defaultdict_rows(rows: list[dict]) -> dict:
    out: dict = {}
    for r in rows:
        out.setdefault(r["key"], []).append(r)
    return out


def extract_report_data(html_text: str) -> dict:
    """Pull the embedded data back out of a report file."""
    start_tag = f'<script type="application/json" id="{DATA_ID}">'
    i = html_text.find(start_tag)
    if i < 0:
        raise ValueError("not an mty report (no embedded data)")
    j = html_text.find("</script>", i)
    data = json.loads(html_text[i + len(start_tag):j])
    if data.get("schema") != REPORT_SCHEMA:
        raise ValueError("not an mty report")
    if data.get("version") != REPORT_VERSION:
        raise ValueError(f"unsupported report version {data.get('version')}")
    return data
