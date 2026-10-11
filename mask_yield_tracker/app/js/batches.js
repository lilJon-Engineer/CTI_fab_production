import { api, download, guard, h, state, toast } from "./api.js";

export async function renderBatches(root, [mode, id]) {
  if (mode === "new" || mode === "edit") return renderEditor(root, mode === "edit" ? id : null);
  const [list, reticles] = await Promise.all([api.batches(), api.reticles()]);

  const importInput = h("input", { type: "file", accept: ".json", style: { display: "none" } });
  importInput.onchange = guard(async () => {
    const f = importInput.files[0];
    if (!f) return;
    const b = await api.importBatch(JSON.parse(await f.text()));
    toast(`Imported ${b.id}`);
    root.replaceChildren();
    renderBatches(root, []);
  });

  const extracted = reticles.filter((r) => r.extracted);
  root.append(h("div", { class: "card" },
    h("div", { class: "row" },
      h("h2", { class: "grow" }, "Batches"),
      h("button", { class: "btn", onclick: () => importInput.click() }, "Import batch…"), importInput,
      h("button", {
        class: "btn primary", disabled: !extracted.length,
        title: extracted.length ? "" : "Upload and extract a reticle first",
        onclick: () => (location.hash = "#/batches/new"),
      }, "New batch")),
    !extracted.length ? h("p", { class: "muted" }, "Start on the Reticles page: upload your layout and extract the device table.") : null,
    list.length ? h("table", { class: "list" },
      h("tr", {}, ["Lot", "Wafer", "Process step", "Date", "Reticle", "Dies", "Results", "Modified", ""].map((t) => h("th", {}, t))),
      list.map((b) => h("tr", {},
        h("td", {}, b.meta.lot || "–"), h("td", {}, b.meta.wafer || "–"), h("td", {}, b.meta.process_step || "–"),
        h("td", {}, b.meta.date || "–"), h("td", {}, b.reticle.name || b.reticle.id), h("td", {}, b.n_dies),
        h("td", {}, b.n_results), h("td", { class: "small muted" }, (b.modified || "").slice(0, 16).replace("T", " ")),
        h("td", { style: { whiteSpace: "nowrap" } },
          h("button", { class: "btn primary", onclick: () => (location.hash = `#/inspect/${encodeURIComponent(b.id)}`) }, "Inspect"), " ",
          h("button", { class: "btn", onclick: () => (location.hash = `#/batches/edit/${encodeURIComponent(b.id)}`) }, "Edit"), " ",
          h("button", { class: "btn", onclick: guard(async () => download(`${b.id}.batch.json`, JSON.stringify(await api.batch(b.id), null, 1))) }, "Export"), " ",
          h("button", {
            class: "btn danger", onclick: guard(async () => {
              if (!confirm(`Delete batch ${b.id}? (moved to batches/trash in the workspace)`)) return;
              await api.deleteBatch(b.id); toast("Deleted"); location.reload();
            }),
          }, "Delete")))))
      : h("p", { class: "muted" }, "No batches yet.")));
}

function num(v) { return v === "" || v === null || v === undefined || isNaN(Number(v)) ? null : Number(v); }

async function renderEditor(root, id) {
  const reticles = (await api.reticles()).filter((r) => r.extracted);
  const batch = id ? await api.batch(id) : null;
  const meta = { ...(batch?.meta || { date: new Date().toISOString().slice(0, 10) }) };
  const wafer = structuredClone(batch?.wafer || state.settings.default_wafer);
  const splits = structuredClone(batch?.splits || { dose_unit: "mJ/cm2", focus_unit: "um", columns: [], rows: [] });
  let reticleId = batch?.reticle.id || reticles[0]?.id;
  let field = null; // field size in mm, from the device table

  const loadField = guard(async () => {
    if (!reticleId) return;
    const t = await api.devices(reticleId);
    const [x0, y0, x1, y1] = t.field.bbox_um;
    field = { w: (x1 - x0) / 1000, h: (y1 - y0) / 1000 };
    fieldNote.textContent = `Reticle field ${field.w.toFixed(2)} × ${field.h.toFixed(2)} mm`;
  });

  // ---- metadata ----
  const metaInputs = state.settings.meta_fields.map((f) => {
    const input = f.type === "textarea" ? h("textarea", { rows: 3 })
      : f.type === "select" ? h("select", {}, (f.options || []).map((o) => h("option", { value: o }, o)))
        : h("input", { type: f.type === "number" ? "number" : f.type === "date" ? "date" : "text" });
    input.value = meta[f.id] ?? "";
    input.oninput = () => (meta[f.id] = input.value);
    return h("label", { class: "field" + (f.type === "textarea" ? " wide" : "") }, f.label + (f.required ? " *" : ""), input);
  });
  const extraKeys = Object.keys(meta).filter((k) => !state.settings.meta_fields.some((f) => f.id === k));

  const reticleSel = h("select", { disabled: !!batch }, reticles.map((r) => h("option", { value: r.id }, `${r.name} (${r.extracted.n_devices} sites)`)));
  reticleSel.value = reticleId || "";
  reticleSel.onchange = () => { reticleId = reticleSel.value; loadField(); };

  // ---- wafer grid ----
  const fieldNote = h("span", { class: "small muted" });
  const waferBox = h("div");
  const splitsBox = h("div", { class: "splits" });
  const winputs = [
    ["diameter_mm", "Wafer diameter (mm)"], ["edge_exclusion_mm", "Edge exclusion (mm)"],
    ["step_x_mm", "Die step X (mm)"], ["step_y_mm", "Die step Y (mm)"],
    ["n_cols", "Columns"], ["n_rows", "Rows"], ["offset_x_mm", "Grid offset X (mm)"], ["offset_y_mm", "Grid offset Y (mm)"],
  ].map(([k, label]) => {
    const i = h("input", { type: "number", step: "any", value: wafer[k] ?? 0 });
    i.onchange = () => {
      wafer[k] = Number(i.value);
      if (k === "n_cols" || k === "n_rows") wafer.dies = wafer.dies.filter(([c, r]) => c < wafer.n_cols && r < wafer.n_rows);
      drawWafer(); drawSplits();
    };
    return h("label", { class: "field" }, label, i);
  });

  function drawWafer() {
    const S = 420, pad = 10;
    const span = Math.max(wafer.diameter_mm, wafer.n_cols * wafer.step_x_mm, wafer.n_rows * wafer.step_y_mm);
    const k = (S - 2 * pad) / span;
    const cx = S / 2, cy = S / 2;
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${S} ${S}`);
    svg.style.maxWidth = "420px";
    const el = (tag, a) => { const e = document.createElementNS(ns, tag); for (const [n, v] of Object.entries(a)) e.setAttribute(n, v); svg.append(e); return e; };
    el("circle", { cx, cy, r: (wafer.diameter_mm / 2) * k, fill: "var(--surface-2)", stroke: "var(--border-strong)" });
    el("circle", { cx, cy, r: Math.max(0, wafer.diameter_mm / 2 - (wafer.edge_exclusion_mm || 0)) * k, fill: "none", stroke: "var(--border-strong)", "stroke-dasharray": "4 3" });
    const inc = new Set(wafer.dies.map(([c, r]) => `${c},${r}`));
    for (let r = 0; r < wafer.n_rows; r++) for (let c = 0; c < wafer.n_cols; c++) {
      const x = (c - (wafer.n_cols - 1) / 2) * wafer.step_x_mm + (wafer.offset_x_mm || 0);
      const y = ((wafer.n_rows - 1) / 2 - r) * wafer.step_y_mm + (wafer.offset_y_mm || 0);
      const on = inc.has(`${c},${r}`);
      const g = document.createElementNS(ns, "g");
      g.style.cursor = "pointer";
      const rect = document.createElementNS(ns, "rect");
      const w = wafer.step_x_mm * k - 2, hh = wafer.step_y_mm * k - 2;
      Object.entries({ x: cx + x * k - w / 2, y: cy - y * k - hh / 2, width: w, height: hh, rx: 2,
        fill: on ? "var(--seq-200)" : "transparent", stroke: on ? "var(--seq-500)" : "var(--border)" }).forEach(([n, v]) => rect.setAttribute(n, v));
      const t = document.createElementNS(ns, "text");
      Object.entries({ x: cx + x * k, y: cy - y * k + 3, "text-anchor": "middle", "font-size": Math.min(10, w / 3),
        fill: on ? "var(--text-primary)" : "var(--text-muted)" }).forEach(([n, v]) => t.setAttribute(n, v));
      t.textContent = `${c},${r}`;
      g.append(rect, t);
      g.onclick = () => {
        const key = `${c},${r}`;
        wafer.dies = inc.has(key) ? wafer.dies.filter(([a, b]) => `${a},${b}` !== key) : [...wafer.dies, [c, r]];
        drawWafer();
      };
      svg.append(g);
    }
    waferBox.replaceChildren(svg, h("div", { class: "small muted" },
      `${wafer.dies.length} dies selected · click a die to toggle · (0,0) is top-left; columns are dose steps`));
  }

  // ---- splits ----
  function drawSplits() {
    const cols = Array.from({ length: wafer.n_cols }, (_, i) => splits.columns[i] || { dose: null, focus: null });
    const rows = Array.from({ length: wafer.n_rows }, (_, i) => splits.rows[i] || { focus: null });
    splits.columns = cols; splits.rows = rows;
    const cell = (obj, key) => {
      const i = h("input", { type: "number", step: "any", value: obj[key] ?? "" });
      i.onchange = () => (obj[key] = num(i.value));
      return h("td", {}, i);
    };
    const unit = (key) => {
      const i = h("input", { type: "text", value: splits[key], style: { width: "80px" } });
      i.onchange = () => (splits[key] = i.value);
      return i;
    };
    const start = h("input", { type: "number", step: "any", placeholder: "start", style: { width: "80px" } });
    const step = h("input", { type: "number", step: "any", placeholder: "step", style: { width: "80px" } });
    const fill = h("button", { class: "btn" }, "Fill doses");
    fill.onclick = () => {
      if (start.value === "" || step.value === "") return toast("Enter a start dose and a step", true);
      cols.forEach((c, i) => (c.dose = +(Number(start.value) + i * Number(step.value)).toFixed(6)));
      drawSplits();
    };
    splitsBox.replaceChildren(
      h("div", { class: "row small", style: { alignItems: "center", marginBottom: "6px" } },
        "Dose unit", unit("dose_unit"), "Focus unit", unit("focus_unit"), " | Linear fill:", start, step, fill),
      h("table", {},
        h("tr", {}, h("th", {}, "Column"), cols.map((_, i) => h("th", {}, i))),
        h("tr", {}, h("th", {}, "Dose"), cols.map((c) => cell(c, "dose"))),
        h("tr", {}, h("th", {}, "Focus"), cols.map((c) => cell(c, "focus")))),
      h("table", { style: { marginTop: "8px" } },
        h("tr", {}, h("th", {}, "Row"), rows.map((_, i) => h("th", {}, i))),
        h("tr", {}, h("th", {}, "Focus"), rows.map((r) => cell(r, "focus")))),
      h("p", { class: "small muted" }, "Leave focus empty when it does not vary. A row focus value overrides the column value for that die."));
  }

  const autoBtn = h("button", { class: "btn" }, "Select dies inside wafer");
  autoBtn.onclick = guard(async () => {
    wafer.dies = await api.diesOnWafer(wafer, field?.w, field?.h);
    drawWafer();
  });

  const saveBtn = h("button", { class: "btn primary" }, batch ? "Save batch" : "Create batch");
  saveBtn.onclick = guard(async () => {
    const missing = state.settings.meta_fields.filter((f) => f.required && !String(meta[f.id] ?? "").trim()).map((f) => f.label);
    if (missing.length) return toast(`Fill in: ${missing.join(", ")}`, true);
    if (!wafer.dies.length) return toast("Select at least one die", true);
    let b;
    if (batch) {
      try {
        b = await api.updateBatch(batch.id, { meta, wafer, splits });
      } catch (e) {
        if (!/no longer in the batch/.test(e.message) || !confirm(e.message + "\n\nDrop those results?")) throw e;
        b = await api.updateBatch(batch.id, { meta, wafer, splits, drop_orphan_results: true });
      }
    } else {
      b = await api.createBatch({ reticle_id: reticleId, meta, wafer, splits });
    }
    toast("Saved");
    location.hash = `#/inspect/${encodeURIComponent(b.id)}`;
  });

  root.append(
    h("div", { class: "card" },
      h("h2", {}, batch ? `Edit batch ${batch.id}` : "New batch"),
      h("div", { class: "form-grid" },
        h("label", { class: "field" }, "Reticle", reticleSel), ...metaInputs),
      extraKeys.length ? h("p", { class: "small muted" }, `Other fields kept from this batch: ${extraKeys.join(", ")}`) : null),
    h("div", { class: "row" },
      h("div", { class: "card", style: { flex: "0 0 auto" } },
        h("h3", { style: { marginTop: 0 } }, "Wafer grid"), fieldNote,
        h("div", { class: "form-grid", style: { maxWidth: "420px", margin: "8px 0" } }, ...winputs),
        h("div", { class: "row", style: { marginBottom: "8px" } }, autoBtn,
          h("button", { class: "btn", onclick: () => { wafer.dies = []; drawWafer(); } }, "Clear"),
          h("button", { class: "btn", onclick: () => { wafer.dies = []; for (let r = 0; r < wafer.n_rows; r++) for (let c = 0; c < wafer.n_cols; c++) wafer.dies.push([c, r]); drawWafer(); } }, "All")),
        waferBox),
      h("div", { class: "card grow" },
        h("h3", { style: { marginTop: 0 } }, "Exposure splits"), splitsBox)),
    h("div", { class: "row" }, saveBtn,
      h("button", { class: "btn", onclick: () => history.back() }, "Cancel")));

  drawWafer();
  drawSplits();
  loadField();
}
