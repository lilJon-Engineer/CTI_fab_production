# Mask yield tracker

Track photolithography yield by gate CD across dose (and later focus) splits,
from optical inspection of every device on the reticle.

Status: **step 1 — reticle extractor** is in place. The inspection app and the
batch report come next (see Roadmap).

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

## Extracting the device table

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
   Writes `local/extracted/device_table.json` and `.csv` and prints a summary
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
schemas/device_table.v1.schema.json
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

1. ~~Reticle extractor~~ (this step)
2. Local app (`python -m mty serve`, browser UI on 127.0.0.1, offline): upload a
   reticle + config and extract from the UI; define wafer grid (die step,
   columns/rows, dies to inspect); wafer map → die zoom with every site at its
   true position; CD lookup highlighting; click-to-record pass/failure mode.
3. Batch file schema (lot, wafer, process step, per-column dose, per-row/column
   focus, operator, notes, extensible metadata) with save/load.
4. Report: yield vs dose per CD, failure-mode breakdown, per-die summary;
   HTML (printable to PDF) with the data embedded so the app can reopen it, plus CSV.
