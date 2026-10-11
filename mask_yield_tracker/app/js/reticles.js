import { api, guard, h, toast } from "./api.js";

export async function renderReticles(root, [selected]) {
  const list = await api.reticles();

  const fileInput = h("input", { type: "file", accept: ".gds,.gds2,.gdsii,.oas,.oasis" });
  const nameInput = h("input", { type: "text", placeholder: "e.g. gate + S/D set, rev B" });
  const upBtn = h("button", { class: "btn primary" }, "Upload");
  upBtn.onclick = guard(async () => {
    const f = fileInput.files[0];
    if (!f) return toast("Choose a .gds or .oas file first", true);
    upBtn.disabled = true; upBtn.textContent = "Uploading…";
    try {
      const r = await api.uploadReticle(f, nameInput.value.trim());
      toast("Uploaded. Check the suggested config, then extract.");
      location.hash = `#/reticles/${encodeURIComponent(r.meta.id)}`;
    } finally { upBtn.disabled = false; upBtn.textContent = "Upload"; }
  });

  root.append(
    h("div", { class: "card" },
      h("h2", {}, "Reticles"),
      h("p", { class: "muted small" },
        "Files are stored in your local workspace only (local/workspace, gitignored). ",
        "After upload the app lists the layout's layers and cells and suggests a config."),
      h("div", { class: "row" },
        h("label", { class: "field" }, "Layout file", fileInput),
        h("label", { class: "field" }, "Name (optional)", nameInput),
        h("div", { style: { alignSelf: "flex-end" } }, upBtn)),
      list.length
        ? h("table", { class: "list", style: { marginTop: "12px" } },
          h("tr", {}, h("th", {}, "Name"), h("th", {}, "File"), h("th", {}, "Uploaded"), h("th", {}, "Sites")),
          list.map((r) => h("tr", { class: "clickable", onclick: () => (location.hash = `#/reticles/${encodeURIComponent(r.id)}`) },
            h("td", {}, r.name), h("td", {}, r.file), h("td", {}, (r.created || "").slice(0, 16).replace("T", " ")),
            h("td", {}, r.extracted ? r.extracted.n_devices : h("span", { class: "muted" }, "not extracted")))))
        : h("p", { class: "muted" }, "No reticles yet.")),
  );

  if (selected) root.append(await reticleDetail(selected));
}

async function reticleDetail(id) {
  const d = await api.reticle(id);
  const cfg = h("textarea", { class: "code", spellcheck: "false" });
  cfg.value = d.config;
  const summary = h("pre", { class: "box" }, d.summary || "Not extracted yet.");
  const info = h("div", { class: "small muted" });
  const setInfo = (m) => {
    info.textContent = m.extracted
      ? `Extracted ${m.extracted.created.replace("T", " ").slice(0, 16)} · ${m.extracted.n_devices} sites`
      : "Not extracted yet";
  };
  setInfo(d.meta);

  const save = h("button", { class: "btn" }, "Save config");
  save.onclick = guard(async () => { await api.saveConfig(id, cfg.value); toast("Config saved"); });
  const run = h("button", { class: "btn primary" }, "Save & extract");
  run.onclick = guard(async () => {
    run.disabled = true; run.textContent = "Extracting…";
    try {
      await api.saveConfig(id, cfg.value);
      const r = await api.extract(id);
      summary.textContent = r.summary;
      setInfo(r.meta);
      toast(`Extracted ${r.meta.extracted.n_devices} sites`);
    } finally { run.disabled = false; run.textContent = "Save & extract"; }
  });
  const del = h("button", { class: "btn danger" }, "Delete reticle");
  del.onclick = guard(async () => {
    if (!confirm(`Delete reticle "${d.meta.name}" and its files from the workspace?`)) return;
    await api.deleteReticle(id);
    toast("Deleted");
    location.hash = "#/reticles";
  });

  return h("div", { class: "row" },
    h("div", { class: "card grow", style: { minWidth: "420px" } },
      h("h2", {}, d.meta.name),
      h("p", { class: "small muted" }, `${d.meta.file} · ${(d.meta.size / 1e6).toFixed(1)} MB`),
      h("h3", {}, "Config"),
      h("p", { class: "small muted" },
        "Gate and S/D top cells, layer/datatype, cell suffixes and device groups. ",
        "Groups with split = true become one site per sub-device (PCM)."),
      cfg,
      h("div", { class: "row", style: { marginTop: "8px" } }, save, run,
        d.meta.extracted ? h("a", { class: "btn", href: `/api/reticles/${encodeURIComponent(id)}/devices.csv` }, "Device table CSV") : null,
        h("span", { style: { marginLeft: "auto" } }, del))),
    h("div", { class: "card grow", style: { minWidth: "420px" } },
      h("h3", { style: { marginTop: 0 } }, "Extraction summary"), info, summary,
      h("h3", {}, "Layout contents"), h("pre", { class: "box" }, d.listing)));
}
