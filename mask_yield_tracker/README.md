# Mask yield tracker

Track photolithography yield by gate CD across dose (and later focus) splits,
from optical inspection of every device on the reticle.

Status: steps 1–3 are in place: **reticle extractor**, **local inspection app
with batch files**, and the **batch yield report**.

## Data privacy

Reticle data never goes into git. The repo holds code, templates and a
synthetic example only.

- `*.gds`, `*.gds2`, `*.gdsii`, `*.oas`, `*.oasis` are gitignored everywhere.
- `local/` (except its README) and any `extracted/`, `batches/` or `reports/`
  folder are gitignored. Real configs are named `*.local.toml` and are ignored too.
- `tests/test_privacy.py` fails if a layout file or local output is ever tracked.

Keep your reticle, real config, device tables and batch data under
`mask_yield_tracker/local/`.

## Setup (Windows 11, Python 3.11+; tested target 3.14)

```powershell
cd mask_yield_tracker
py -3.14 -m venv .venv
.venv\Scripts\activate
pip install -e .[test]
pytest
```

`klayout` installs from PyPI as a wheel; no KLayout GUI is needed.

## The inspection app

```powershell
python -m mty serve            # opens http://127.0.0.1:8765/ in your browser
```

The server runs on your machine only (bound to 127.0.0.1; requests from other
sites are refused). All data lives in `local/workspace/` (gitignored):
uploaded reticles, configs, device tables, drawn geometry and batch files.

1. **Reticles**: upload a .gds/.oas. The app lists layers and cells and writes a
   suggested config (top cells, layer, suffixes, groups). Edit it, then
   *Save & extract*. Optional: put default `[text]` / `[split]` sections in
   `local/defaults.toml` so every new reticle starts with your patterns.
2. **Batches → New batch**: pick the reticle; fill in lot, wafer, process step,
   date, operator, notes (fields come from `config/batch_meta.json`, so add your
   own there); set the wafer grid (diameter, edge exclusion, die step, columns,
   rows) and click dies to include them (*Select dies inside wafer* uses the
   reticle field size); enter the dose per column (linear fill helper) and focus
   per column or row when it varies.
3. **Inspect**: the wafer map shows each die coloured by yield of the devices
   in view. Click a die; the field shows every site at its true position. Wheel
   zooms, drag pans, double-click zooms to a device; when a device is large
   enough its real gate geometry is drawn.
   *CD lookup* highlights every device with that finger CD (or thinnest width
   for lines); small highlighted devices get a locator ring. Click a device and
   press a key to record the result. Every click is saved immediately.

| key | action |
|---|---|
| P / L / M / S | pass / lifting / missing / skating (keys come from `config/failure_modes.json`) |
| U, Backspace | clear the result |
| N / B | next / previous highlighted device |
| ] / [ | next / previous die (the selected device stays selected) |
| F / Z / Esc | fit field / zoom to device / deselect |

*Rotate* and *Mirror* change only the field view, to match what you see under
the microscope. Batches can be exported to and imported from JSON
(`schemas/batch.v1.schema.json`).

## Batch report

*Batches → Report* (or *report* in the Inspect view) opens the report for one
batch; *download report* saves it as a single offline `.html` file:

- summary tiles (dies, results recorded, coverage, overall yield) and batch metadata
- yield vs dose for all devices, and outcome mix (pass / failure modes) per dose
- yield vs dose per CD as small multiples on a common 0–100 % scale
- CD × dose heatmap (yield %, devices inspected), and CD + pitch × dose
- outcome mix per CD
- per-die wafer map and table (dose, focus, coverage, yield, 95 % interval, counts per mode)

Yield = pass / inspected; sites without a result are left out and reported as
coverage. CDs are finger widths; sites without fingers are grouped by their
thinnest drawn width ("thinnest 350 nm"). When focus varies, each chart shows
one line per focus value and the heatmap columns are dose / focus.

*Print / save as PDF* prints a clean PDF; the CSV buttons export yield by CD ×
dose, by CD + pitch × dose, per die, and every recorded result. The report
embeds the batch, so *Batches → Import batch…* accepts a report `.html` to
reopen it (the reticle must be in the workspace; it is re-linked by file hash
if its id differs).

From the command line (export the batch first):

```powershell
python -m mty report local\batch.json -d local\workspace\reticles\<id>\device_table.json
```
writes the HTML and the CSVs to `local/reports/`.

## Extracting the device table from the command line

1. See what is in the file:
   ```powershell
   python -m mty list local\my_reticle.gds --depth 3
   ```
   This prints layers (layer/datatype and shape counts), top cells and the cell
   hierarchy, marking cells without geometry.
2. Copy `config/reticle.example.toml` to `local\my_reticle.local.toml` and set
   the gate/S-D top cells, layer/datatype, cell suffixes and device groups.
3. Extract:
   ```powershell
   python -m mty extract local\my_reticle.gds -c local\my_reticle.local.toml
   ```
   Writes `local/extracted/device_table.json`, `.csv` and `geometry.json` (drawn
   outlines for the app) and prints a summary
   (sites per group/kind, sites per CD, thinnest dimension for the rest, flags).

Try it on the synthetic reticle: `python -m mty synthetic` then
`python -m mty extract local\synthetic.gds -c config\reticle.example.toml`.

### How sites are found

- **Groups**: each `[[groups]]` entry names a container cell stem (e.g.
  `Logic_top`). The reticle suffix is appended per reticle (`Logic_top_Gate`,
  `Logic_top_SD`). Every child placement of the container is one **site**;
  array instances are expanded, and repeated placements of the same cell are
  separate sites (`XA03#1`, `XA03#2`, ... ordered top-to-bottom, left-to-right).
- **Split groups** (`split = true`, used for PCM): each geometry-bearing
  sub-cell of a device is its own site (`PCMA_01/SubA`), so every site has
  one CD. Shapes drawn directly in the device cell (routing, long lines) become
  `<device>/_wires`. Pads/frames/vias are skipped via `[split].exclude_patterns`.
- **Text**: cells matching `[text].cell_patterns` (letters, digits, `X$$...` glyph cells)
  are ignored together with everything below them.
- **Geometry** (gate layer only, in the field frame, so rotated placements are
  handled): paths, boxes and polygons are reduced to line-like elements.
  3-point polygons (serifs) are ignored. Straight, high-aspect elements are
  grouped by width and direction; the narrowest group with 2+ members is the
  finger array → `cd_nm`, `n_fingers`, `pitch_nm`, `orientation`. A single
  narrow line gives `single_line`. Anything 100 µm+ long without a finger array
  is a `long_line`. Every site also gets `thinnest_nm` (the smallest drawn width,
  polygon widths measured with a width check) and a width histogram.
- **Isolated/dense** is provisional (`space >= dense_space_factor * CD` →
  isolated); pitch is recorded but not used further yet.
- **S/D pairing**: same device name in the S/D reticle first (nearest placement,
  coverage of the gate bbox recorded); otherwise the smallest S/D site covering
  at least `min_coverage` of the gate site. Unpaired sites are flagged.

### Flags

| flag | meaning |
|---|---|
| `no_sd_match` | no S/D site found (normal for gate-only litho structures) |
| `sd_matched_by_name_without_overlap` | S/D site with the same name exists but its geometry does not overlap the gate geometry |
| `sd_name_differs_from_gate` | paired by overlap to a differently named S/D site |
| `irregular_pitch` | finger spacing varies by more than 5 % |
| `collinear_fingers` | several finger segments share one axis position |
| `narrower_single_line_present` | a narrower single line exists next to the finger array |
| `mixed_widths` | long line / other site drawn with several widths (thinnest is reported) |

## Files

```
config/reticle.example.toml       config template (synthetic values)
config/failure_modes.json         pass / lifting / missing / skating (extend here)
config/batch_meta.json            batch metadata fields (extend here)
schemas/device_table.v1.schema.json
schemas/batch.v1.schema.json
mty/server.py                     local web server + JSON API (stdlib only)
mty/workspace.py                  on-disk workspace (reticles, batches)
mty/batch.py                      batch schema: create, normalize, migrate, record results
mty/extract/suggest.py            config suggestion for an uploaded layout
mty/report/aggregate.py           yield / failure-mode aggregation, Wilson intervals
mty/report/html.py                single-file HTML report (inline SVG) with embedded data
app/                              browser UI (plain JS, works offline)
mty/config.py                     config loading + validation
mty/extract/sites.py              hierarchy walk, groups, splitting, text exclusion
mty/extract/geometry.py           CD / fingers / pitch / thinnest dimension
mty/extract/extractor.py          pairing, ids, JSON/CSV output, summary
mty/extract/listing.py            `mty list`
mty/synthetic.py                  synthetic two-reticle GDS for tests
tests/                            pytest suite (synthetic data only)
local/                            your data (gitignored)
```

## Roadmap

1. ~~Reticle extractor~~
2. ~~Local app: reticle upload/extract, wafer grid, wafer map, die view, CD
   lookup, click-to-record, batch files with import/export~~
3. ~~Batch report: HTML/PDF/CSV, re-openable~~
4. Next ideas: per-process-step projects, comparing batches, focus-exposure
   matrix views once focus splits are used.
