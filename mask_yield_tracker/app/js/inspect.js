import { api, fmt, guard, h, state, toast } from "./api.js";

const SEQ = ["--seq-100", "--seq-200", "--seq-300", "--seq-400", "--seq-500", "--seq-600", "--seq-700"];
const MIN_PX = 5;          // smallest drawn size of a device mark
const RING_PX = 9;         // locator ring for tiny highlighted devices
const LS_VIEW = "mty.view";

function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function loadView() { try { return JSON.parse(localStorage.getItem(LS_VIEW)) || {}; } catch { return {}; } }
function saveView(v) { try { localStorage.setItem(LS_VIEW, JSON.stringify(v)); } catch { /* private mode */ } }

export async function renderInspect(root, [bid]) {
  if (!bid) {
    const list = await api.batches();
    const last = (() => { try { return localStorage.getItem("mty.lastBatch"); } catch { return null; } })();
    const pick = list.find((b) => b.id === last) || list[0];
    if (pick) { location.replace(`#/inspect/${encodeURIComponent(pick.id)}`); return; }
    root.append(h("div", { class: "card" }, h("p", {}, "No batches yet. Create one on the Batches page.")));
    return;
  }
  try { localStorage.setItem("mty.lastBatch", bid); } catch { /* ignore */ }

  const batch = await api.batch(bid);
  const table = await api.devices(batch.reticle.id);
  const devices = table.devices;
  const devById = Object.fromEntries(devices.map((d) => [d.id, d]));
  const modes = state.modes;
  const keyToMode = Object.fromEntries(modes.map((m) => [m.key, m.id]));
  const passIds = new Set(modes.filter((m) => m.pass).map((m) => m.id));

  const view = { rot: 0, mirror: false, advance: true, ...loadView() };
  let outlines = null;            // drawn gate geometry per device (loaded in the background)
  const GEOM_PX = 28;             // draw real geometry once a device is at least this big on screen
  const S = {
    die: batch.wafer.dies[0],
    filter: { cd: "", group: "", kind: "", text: "", open: false },
    selected: null,
    scale: 1, panX: 0, panY: 0,
    hover: null,
  };
  let matching = devices;        // current filter result, in inspection order
  let baseMatching = devices;    // same without the 'not yet inspected' option (used by the wafer map)
  let filterActive = false;

  // ---------- geometry: layout um -> display (rotation / mirror about field center) ----------
  const [fx0, fy0, fx1, fy1] = table.field.bbox_um;
  const fcx = (fx0 + fx1) / 2, fcy = (fy0 + fy1) / 2;
  function toDisp(x, y) {
    let u = x - fcx, v = y - fcy;
    if (view.mirror) u = -u;
    for (let i = 0; i < view.rot; i++) [u, v] = [-v, u];
    return [u, v];
  }
  const canvas = h("canvas");
  const tip = h("div", { class: "tip" });
  const wrap = h("div", { class: "canvas-wrap" }, canvas, tip);
  let W = 0, H = 0, dpr = 1;
  function toScreen(x, y) {
    const [u, v] = toDisp(x, y);
    return [W / 2 + u * S.scale + S.panX, H / 2 - v * S.scale + S.panY];
  }
  function screenBox(b) {
    const [ax, ay] = toScreen(b[0], b[1]);
    const [bx, by] = toScreen(b[2], b[3]);
    let x0 = Math.min(ax, bx), x1 = Math.max(ax, bx), y0 = Math.min(ay, by), y1 = Math.max(ay, by);
    if (x1 - x0 < MIN_PX) { const c = (x0 + x1) / 2; x0 = c - MIN_PX / 2; x1 = c + MIN_PX / 2; }
    if (y1 - y0 < MIN_PX) { const c = (y0 + y1) / 2; y0 = c - MIN_PX / 2; y1 = c + MIN_PX / 2; }
    return [x0, y0, x1, y1];
  }
  function fit() {
    const [a, b] = toDisp(fx0, fy0), [c, d] = toDisp(fx1, fy1);
    const w = Math.abs(c - a), hh = Math.abs(d - b);
    S.scale = Math.min(W / w, H / hh) * 0.94;
    S.panX = 0; S.panY = 0;
    draw();
  }
  function centerOn(dev, zoom = false) {
    const [cx, cy] = dev.center_um;
    if (zoom) {
      const bw = dev.bbox_um[2] - dev.bbox_um[0], bh = dev.bbox_um[3] - dev.bbox_um[1];
      S.scale = Math.min(W / Math.max(bw, 1), H / Math.max(bh, 1)) * 0.3;
    }
    const [u, v] = toDisp(cx, cy);
    S.panX = -u * S.scale; S.panY = v * S.scale;
    draw();
  }
  function ensureVisible(dev) {
    const [x0, y0, x1, y1] = screenBox(dev.bbox_um);
    if (x0 < 20 || y0 < 20 || x1 > W - 20 || y1 > H - 20) centerOn(dev);
  }

  // ---------- results ----------
  const dieKey = (d = S.die) => `${d[0]},${d[1]}`;
  const resultsOf = (d = S.die) => batch.results[dieKey(d)] || {};
  const statusOf = (id) => resultsOf()[id]?.status || null;
  const saveStatus = document.getElementById("save-status");

  async function setStatus(ids, status) {
    const key = dieKey();
    const before = structuredClone(batch.results[key] || {});
    const cur = (batch.results[key] = batch.results[key] || {});
    for (const id of ids) { if (status) cur[id] = { status, ts: new Date().toISOString() }; else delete cur[id]; }
    draw(); drawWafer(); drawPanel();
    saveStatus.textContent = "Saving…";
    try {
      const r = await api.record(batch.id, S.die, ids.map((id) => ({ device_id: id, status })));
      batch.results[key] = r.results;
      saveStatus.textContent = `Saved ${new Date().toLocaleTimeString()}`;
    } catch (e) {
      batch.results[key] = before;
      saveStatus.textContent = "Not saved";
      toast(`Not saved: ${e.message}`, true);
      draw(); drawWafer(); drawPanel();
    }
  }

  function mark(status) {
    if (!S.selected) return toast("Click a device first", true);
    const id = S.selected;
    setStatus([id], status);
    if (status && view.advance) step(1, true);
  }

  // ---------- filter ----------
  const cds = [...new Set(devices.map((d) => d.cd_nm).filter((v) => v !== null))].sort((a, b) => a - b);
  const thin = [...new Set(devices.filter((d) => d.cd_nm === null).map((d) => d.thinnest_nm).filter((v) => v !== null))].sort((a, b) => a - b);
  const groups = [...new Set(devices.map((d) => d.group))];
  const kinds = [...new Set(devices.map((d) => d.kind))];

  function orderKey(d) {
    const [u, v] = toDisp(d.center_um[0], d.center_um[1]);
    return [groups.indexOf(d.group), -Math.round(v / 50), u];
  }
  function applyFilter() {
    const f = S.filter;
    const text = f.text.trim().toLowerCase();
    filterActive = !!(f.cd || f.group || f.kind || text);
    const res = resultsOf();
    matching = devices.filter((d) => {
      if (f.cd) {
        const [kind, val] = f.cd.split(":");
        if (kind === "cd" && d.cd_nm !== Number(val)) return false;
        if (kind === "th" && !(d.cd_nm === null && d.thinnest_nm === Number(val))) return false;
      }
      if (f.group && d.group !== f.group) return false;
      if (f.kind && d.kind !== f.kind) return false;
      if (text && !d.name.toLowerCase().includes(text)) return false;
      return true;
    });
    baseMatching = matching;
    if (f.open) matching = matching.filter((d) => !res[d.id]);
    matching = matching.map((d) => [orderKey(d), d]).sort((a, b) => {
      for (let i = 0; i < 3; i++) if (a[0][i] !== b[0][i]) return a[0][i] - b[0][i];
      return 0;
    }).map((x) => x[1]);
  }
  const matchSet = () => new Set(matching.map((d) => d.id));

  function step(dir, skipMarked = false) {
    if (!matching.length) return;
    let i = matching.findIndex((d) => d.id === S.selected);
    const res = resultsOf();
    for (let n = 0; n < matching.length; n++) {
      i = (i + dir + matching.length) % matching.length;
      if (!skipMarked || !res[matching[i].id]) break;
    }
    select(matching[i].id, true);
  }
  function select(id, reveal = false) {
    S.selected = id;
    if (id && reveal) ensureVisible(devById[id]);
    draw(); drawPanel();
  }

  // ---------- canvas drawing ----------
  function resize() {
    dpr = window.devicePixelRatio || 1;
    const r = wrap.getBoundingClientRect();
    const firstTime = !W;
    W = r.width; H = r.height;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    if (firstTime) fit(); else draw();
  }
  const groupBoxes = groups.map((g) => {
    const ds = devices.filter((d) => d.group === g);
    return { g, box: [Math.min(...ds.map((d) => d.bbox_um[0])), Math.min(...ds.map((d) => d.bbox_um[1])),
      Math.max(...ds.map((d) => d.bbox_um[2])), Math.max(...ds.map((d) => d.bbox_um[3]))] };
  }).filter(({ box }) => (box[2] - box[0]) * (box[3] - box[1]) < 0.5 * (fx1 - fx0) * (fy1 - fy0)); // skip groups spread over the field

  function draw() {
    if (!W) return;
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const c = { surface: cssVar("--surface-2"), border: cssVar("--border"), strong: cssVar("--border-strong"),
      muted: cssVar("--text-muted"), text: cssVar("--text-secondary"), hi: cssVar("--highlight"), accent: cssVar("--accent") };
    // field outline
    let [x0, y0, x1, y1] = screenBox(table.field.bbox_um);
    ctx.fillStyle = c.surface; ctx.fillRect(x0, y0, x1 - x0, y1 - y0);
    ctx.strokeStyle = c.strong; ctx.lineWidth = 1; ctx.strokeRect(x0 + 0.5, y0 + 0.5, x1 - x0, y1 - y0);
    // groups
    ctx.font = "11px system-ui, sans-serif";
    ctx.setLineDash([4, 3]);
    for (const { g, box } of groupBoxes) {
      [x0, y0, x1, y1] = screenBox(box);
      ctx.strokeStyle = c.border; ctx.strokeRect(x0 - 4, y0 - 4, x1 - x0 + 8, y1 - y0 + 8);
      ctx.fillStyle = c.muted; ctx.fillText(g, x0 - 4, y0 - 7);
    }
    ctx.setLineDash([]);
    const res = resultsOf();
    const ms = matchSet();
    const rings = [];
    // non-matching first (dimmed), then matching on top
    for (const pass of [0, 1]) {
      for (const d of devices) {
        const isMatch = !filterActive || ms.has(d.id);
        if ((pass === 1) !== (filterActive && isMatch)) continue;
        [x0, y0, x1, y1] = screenBox(d.bbox_um);
        if (x1 < 0 || y1 < 0 || x0 > W || y0 > H) continue;
        const st = res[d.id]?.status;
        const mode = st && state.modeById[st];
        ctx.globalAlpha = filterActive && !isMatch ? 0.25 : 1;
        if (outlines && outlines[d.id] && x1 - x0 >= GEOM_PX && y1 - y0 >= GEOM_PX) {
          drawGeometry(ctx, d, mode, c, filterActive && isMatch, [x0, y0, x1, y1]);
          continue;
        }
        ctx.fillStyle = mode ? mode.color : c.surface;
        ctx.fillRect(x0, y0, x1 - x0, y1 - y0);
        ctx.strokeStyle = filterActive && isMatch ? c.hi : mode ? mode.color : c.strong;
        ctx.lineWidth = filterActive && isMatch ? 2 : 1;
        ctx.strokeRect(x0, y0, x1 - x0, y1 - y0);
        if (mode && x1 - x0 >= 14 && y1 - y0 >= 14) {
          ctx.fillStyle = "#fff"; ctx.font = "bold 11px system-ui, sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
          ctx.fillText(mode.key.toUpperCase(), (x0 + x1) / 2, (y0 + y1) / 2);
          ctx.textAlign = "start"; ctx.textBaseline = "alphabetic";
        }
        if (filterActive && isMatch && (x1 - x0 < 12 || y1 - y0 < 12)) rings.push([(x0 + x1) / 2, (y0 + y1) / 2]);
      }
    }
    ctx.globalAlpha = 1;
    ctx.strokeStyle = c.hi; ctx.lineWidth = 1.5;
    for (const [x, y] of rings) { ctx.beginPath(); ctx.arc(x, y, RING_PX, 0, Math.PI * 2); ctx.stroke(); }
    if (S.selected) {
      [x0, y0, x1, y1] = screenBox(devById[S.selected].bbox_um);
      ctx.strokeStyle = c.accent; ctx.lineWidth = 3;
      ctx.strokeRect(x0 - 4, y0 - 4, x1 - x0 + 8, y1 - y0 + 8);
    }
    // scale bar
    const targetPx = 120, um = targetPx / S.scale;
    const nice = [1, 2, 5].flatMap((m) => [1, 10, 100, 1000, 10000].map((e) => m * e)).filter((v) => v <= um).pop() || 1;
    const px = nice * S.scale;
    ctx.fillStyle = c.text; ctx.fillRect(12, H - 18, px, 3);
    ctx.font = "11px system-ui, sans-serif";
    ctx.fillText(nice >= 1000 ? `${nice / 1000} mm` : `${nice} µm`, 12, H - 24);
  }

  function drawGeometry(ctx, d, mode, c, emphasize, [x0, y0, x1, y1]) {
    // status as a light wash + border, real gate polygons in ink, key letter as a corner badge
    if (mode) {
      ctx.save(); ctx.globalAlpha *= 0.18; ctx.fillStyle = mode.color; ctx.fillRect(x0, y0, x1 - x0, y1 - y0); ctx.restore();
    }
    ctx.fillStyle = c.text;
    ctx.beginPath();
    for (const poly of outlines[d.id]) {
      for (let i = 0; i < poly.length; i += 2) {
        const [sx, sy] = toScreen(poly[i], poly[i + 1]);
        if (i === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
      }
      ctx.closePath();
    }
    ctx.fill("nonzero");
    ctx.strokeStyle = emphasize ? c.hi : mode ? mode.color : c.strong;
    ctx.lineWidth = mode || emphasize ? 2 : 1;
    ctx.setLineDash(mode ? [] : [4, 3]);
    ctx.strokeRect(x0 - 3, y0 - 3, x1 - x0 + 6, y1 - y0 + 6);
    ctx.setLineDash([]);
    if (mode) {
      ctx.fillStyle = mode.color; ctx.fillRect(x0 - 3, y0 - 19, 16, 16);
      ctx.fillStyle = "#fff"; ctx.font = "bold 11px system-ui, sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(mode.key.toUpperCase(), x0 + 5, y0 - 11);
      ctx.textAlign = "start"; ctx.textBaseline = "alphabetic";
    }
    if (y1 - y0 > 60) { ctx.fillStyle = c.muted; ctx.font = "11px system-ui, sans-serif"; ctx.fillText(d.id, x0 + 16, y0 - 7); }
  }

  function hit(mx, my) {
    let best = null, bestA = Infinity;
    const ms = matchSet();
    for (const d of devices) {
      const [x0, y0, x1, y1] = screenBox(d.bbox_um);
      const pad = 3;
      if (mx < x0 - pad || mx > x1 + pad || my < y0 - pad || my > y1 + pad) continue;
      let a = (x1 - x0) * (y1 - y0);
      if (filterActive && !ms.has(d.id)) a *= 1e6;     // prefer highlighted devices
      if (a < bestA) { bestA = a; best = d; }
    }
    return best;
  }

  // ---------- canvas interaction ----------
  let drag = null;
  canvas.addEventListener("mousedown", (e) => { drag = { x: e.clientX, y: e.clientY, px: S.panX, py: S.panY, moved: false }; });
  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);
  function onMove(e) {
    if (drag) {
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      if (drag.moved) { S.panX = drag.px + dx; S.panY = drag.py + dy; draw(); }
      return;
    }
    if (e.target !== canvas) return;
    const r = canvas.getBoundingClientRect();
    const d = hit(e.clientX - r.left, e.clientY - r.top);
    if (!d) { tip.style.display = "none"; return; }
    const st = statusOf(d.id);
    tip.replaceChildren(h("b", {}, d.name), d.placements_total > 1 ? ` #${d.placement}` : "", h("br"),
      d.cd_nm !== null ? `CD ${fmt(d.cd_nm)} nm · ${d.n_fingers} finger${d.n_fingers === 1 ? "" : "s"}` : `${d.kind} · thinnest ${fmt(d.thinnest_nm)} nm`,
      h("br"), st ? state.modeById[st].label : "not inspected");
    tip.style.display = "block";
    tip.style.left = Math.min(e.clientX - r.left + 14, W - 200) + "px";
    tip.style.top = (e.clientY - r.top + 14) + "px";
  }
  function onUp(e) {
    if (drag && !drag.moved && e.target === canvas) {
      const r = canvas.getBoundingClientRect();
      const d = hit(e.clientX - r.left, e.clientY - r.top);
      select(d ? d.id : null);
    }
    drag = null;
  }
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = canvas.getBoundingClientRect();
    const mx = e.clientX - r.left - W / 2 - S.panX, my = e.clientY - r.top - H / 2 - S.panY;
    const k = Math.exp(-e.deltaY * 0.0015);
    S.scale *= k; S.panX -= mx * (k - 1); S.panY -= my * (k - 1);
    draw();
  }, { passive: false });
  canvas.addEventListener("dblclick", (e) => {
    const r = canvas.getBoundingClientRect();
    const d = hit(e.clientX - r.left, e.clientY - r.top);
    if (d) centerOn(d, true);
  });
  canvas.addEventListener("mouseleave", () => (tip.style.display = "none"));

  // ---------- wafer map ----------
  const waferBox = h("div", { class: "wafer" });
  function dieStats(d) {
    const res = resultsOf(d);
    let n = 0, pass = 0;
    for (const dev of baseMatching) {
      const st = res[dev.id]?.status;
      if (!st) continue;
      n++; if (passIds.has(st)) pass++;
    }
    return { n, pass, total: baseMatching.length };
  }
  function drawWafer() {
    const w = batch.wafer, ns = "http://www.w3.org/2000/svg";
    const cell = 26, gap = 2, top = 18, left = 4;
    const cols = [...new Set(w.dies.map((d) => d[0]))].sort((a, b) => a - b);
    const rows = [...new Set(w.dies.map((d) => d[1]))].sort((a, b) => a - b);
    const c0 = cols[0], r0 = rows[0];
    const nc = cols.at(-1) - c0 + 1, nr = rows.at(-1) - r0 + 1;
    const svg = document.createElementNS(ns, "svg");
    const SW = left + nc * (cell + gap), SH = top + nr * (cell + gap) + 4;
    svg.setAttribute("viewBox", `0 0 ${SW} ${SH}`);
    const el = (tag, a, text) => { const e = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(a)) e.setAttribute(k, v); if (text !== undefined) e.textContent = text; return e; };
    for (let c = 0; c < nc; c++) {
      const dose = batch.splits.columns[c + c0]?.dose;
      svg.append(el("text", { x: left + c * (cell + gap) + cell / 2, y: 12, "text-anchor": "middle", "font-size": 8, fill: cssVar("--text-muted") },
        dose === null || dose === undefined ? c + c0 : dose));
    }
    for (const d of w.dies) {
      const st = dieStats(d);
      const x = left + (d[0] - c0) * (cell + gap), y = top + (d[1] - r0) * (cell + gap);
      const g = el("g", { class: "die" });
      const y01 = st.n ? st.pass / st.n : null;
      const stepIdx = y01 === null ? -1 : Math.min(6, Math.floor(y01 * 7));
      const fill = stepIdx < 0 ? cssVar("--surface-2") : cssVar(SEQ[stepIdx]);
      const cur = d[0] === S.die[0] && d[1] === S.die[1];
      g.append(el("rect", { x, y, width: cell, height: cell, rx: 3, fill,
        stroke: cur ? cssVar("--highlight") : cssVar("--border-strong"), "stroke-width": cur ? 2.5 : 1,
        "stroke-dasharray": stepIdx < 0 && !cur ? "3 2" : "" }));
      g.append(el("text", { x: x + cell / 2, y: y + cell / 2 + 3, "text-anchor": "middle", "font-size": 8,
        fill: stepIdx >= 3 ? "#fff" : cssVar("--text-secondary") }, y01 === null ? "" : Math.round(y01 * 100)));
      const [dose, focus] = doseFocus(d);
      g.append(el("title", {}, `Die ${d[0]},${d[1]}  dose ${fmt(dose, 2)} ${batch.splits.dose_unit}` +
        (focus !== null ? `  focus ${focus} ${batch.splits.focus_unit}` : "") +
        `\n${st.n}/${st.total} inspected${st.n ? `, yield ${Math.round((st.pass / st.n) * 100)}%` : ""}`));
      g.addEventListener("click", () => gotoDie(d));
      svg.append(g);
    }
    waferBox.replaceChildren(svg,
      h("div", { class: "legend" },
        h("span", {}, h("span", { class: "swatch none" }), "none inspected"),
        h("span", {}, "yield 0%", ...SEQ.map((v) => h("span", { class: "swatch", style: { background: `var(${v})`, width: "10px" } })), "100%")),
      h("p", { class: "small muted", style: { margin: "4px 0 0" } },
        `Column header = dose (${batch.splits.dose_unit}). Yield = pass / inspected for ${filterActive ? "the highlighted devices" : "all devices"}; number in a die = yield %.`));
  }
  function doseFocus(d) {
    const c = batch.splits.columns[d[0]] || {}, r = batch.splits.rows[d[1]] || {};
    return [c.dose ?? null, r.focus ?? c.focus ?? null];
  }
  function gotoDie(d) {
    S.die = d;
    tip.style.display = "none";
    applyFilter();
    drawWafer(); draw(); drawPanel(); drawToolbar();
  }
  function stepDie(dir) {
    const i = batch.wafer.dies.findIndex((d) => d[0] === S.die[0] && d[1] === S.die[1]);
    gotoDie(batch.wafer.dies[(i + dir + batch.wafer.dies.length) % batch.wafer.dies.length]);
  }

  // ---------- filter controls ----------
  const cdSel = h("select", {},
    h("option", { value: "" }, "All CDs"),
    h("optgroup", { label: "Finger CD (nm)" }, cds.map((v) => h("option", { value: `cd:${v}` }, `${fmt(v)} nm (${devices.filter((d) => d.cd_nm === v).length})`))),
    thin.length ? h("optgroup", { label: "Lines / other: thinnest (nm)" }, thin.map((v) => h("option", { value: `th:${v}` }, `${fmt(v)} nm thinnest`))) : null);
  const groupSel = h("select", {}, h("option", { value: "" }, "All groups"), groups.map((g) => h("option", { value: g }, g)));
  const kindSel = h("select", {}, h("option", { value: "" }, "All kinds"), kinds.map((k) => h("option", { value: k }, k.replace("_", " "))));
  const textIn = h("input", { type: "search", placeholder: "device name contains…" });
  const openChk = h("input", { type: "checkbox" });
  const onFilter = () => {
    Object.assign(S.filter, { cd: cdSel.value, group: groupSel.value, kind: kindSel.value, text: textIn.value, open: openChk.checked });
    applyFilter();
    if (S.selected && !matchSet().has(S.selected) && filterActive) S.selected = null;
    draw(); drawWafer(); drawPanel();
  };
  [cdSel, groupSel, kindSel, openChk].forEach((e) => e.addEventListener("change", onFilter));
  textIn.addEventListener("input", onFilter);

  // ---------- right panel ----------
  const panel = h("div");
  const advChk = h("input", { type: "checkbox", checked: view.advance });
  advChk.onchange = () => { view.advance = advChk.checked; saveView(view); };
  function drawPanel() {
    const res = resultsOf();
    const done = devices.filter((d) => res[d.id]).length;
    const dsel = S.selected && devById[S.selected];
    const st = dsel ? res[dsel.id]?.status : null;
    const unmarked = matching.filter((d) => !res[d.id]);
    const counts = Object.fromEntries(modes.map((m) => [m.id, 0]));
    for (const d of devices) { const s = res[d.id]?.status; if (s in counts) counts[s]++; }
    const note = h("textarea", { rows: 2, style: { width: "100%" }, placeholder: "note for this die" });
    note.value = batch.die_notes?.[dieKey()] || "";
    note.onchange = guard(async () => {
      batch.die_notes = { ...(batch.die_notes || {}), [dieKey()]: note.value };
      if (!note.value) delete batch.die_notes[dieKey()];
      await api.updateBatch(batch.id, { die_notes: batch.die_notes });
      saveStatus.textContent = `Saved ${new Date().toLocaleTimeString()}`;
    });

    panel.replaceChildren(h("div", {},
      h("div", { class: "small" }, `Die ${dieKey()}: ${done}/${devices.length} inspected`),
      h("div", { class: "progress" }, h("div", { style: { width: `${(100 * done) / devices.length}%` } })),
      h("div", { class: "legend" }, modes.map((m) => h("span", {}, h("span", { class: "swatch", style: { background: m.color } }, m.key.toUpperCase()), `${m.label} ${counts[m.id]}`))),
      note,
      h("h3", {}, "Selected device"),
      dsel ? h("div", {},
        h("dl", { class: "kv" },
          h("dt", {}, "Name"), h("dd", {}, h("b", {}, dsel.name), dsel.placements_total > 1 ? `  (#${dsel.placement} of ${dsel.placements_total})` : ""),
          h("dt", {}, "Group"), h("dd", {}, dsel.group),
          h("dt", {}, "Kind"), h("dd", {}, dsel.kind.replace("_", " ")),
          h("dt", {}, "CD"), h("dd", {}, dsel.cd_nm !== null ? `${fmt(dsel.cd_nm)} nm` : "–"),
          h("dt", {}, "Fingers"), h("dd", {}, dsel.n_fingers || "–", dsel.pitch_nm ? ` · pitch ${fmt(dsel.pitch_nm)} nm` : ""),
          h("dt", {}, "Thinnest"), h("dd", {}, `${fmt(dsel.thinnest_nm)} nm`),
          h("dt", {}, "Position"), h("dd", {}, `${fmt(dsel.center_um[0], 1)}, ${fmt(dsel.center_um[1], 1)} µm`),
          dsel.flags.length ? [h("dt", {}, "Flags"), h("dd", {}, dsel.flags.join(", "))] : null),
        h("div", { class: "status-buttons" },
          modes.map((m) => h("button", { class: "btn" + (st === m.id ? " on" : ""), onclick: () => mark(m.id), title: m.description },
            h("span", { class: "swatch", style: { background: m.color } }, m.key.toUpperCase()), m.label, " ", h("kbd", {}, m.key.toUpperCase()))),
          h("button", { class: "btn", onclick: () => mark(null) }, h("span", { class: "swatch none" }, "–"), "Clear ", h("kbd", {}, "U"))))
        : h("p", { class: "small muted" }, "Click a device on the field. Keys: ",
          modes.map((m) => [h("kbd", {}, m.key.toUpperCase()), ` ${m.label.toLowerCase()} `]), h("kbd", {}, "U"), " clear, ",
          h("kbd", {}, "N"), "/", h("kbd", {}, "B"), " next/previous device, ", h("kbd", {}, "]"), "/", h("kbd", {}, "["), " next/previous die, ",
          h("kbd", {}, "F"), " fit, ", h("kbd", {}, "Z"), " zoom to device."),
      h("label", { class: "small" }, advChk, " after marking, jump to the next unmarked device"),
      h("h3", {}, filterActive ? `Highlighted devices (${matching.length})` : `All devices (${matching.length})`),
      unmarked.length && modes.some((m) => m.pass)
        ? h("button", {
          class: "btn small", onclick: () => {
            const passMode = modes.find((m) => m.pass);
            if (confirm(`Mark ${unmarked.length} unmarked ${filterActive ? "highlighted " : ""}device(s) in die ${dieKey()} as ${passMode.label}?`)) setStatus(unmarked.map((d) => d.id), passMode.id);
          },
        }, `Mark ${unmarked.length} unmarked as ${modes.find((m) => m.pass).label}`) : null,
      h("div", { class: "devlist" }, matching.slice(0, 2000).map((d) => {
        const s = res[d.id]?.status, m = s && state.modeById[s];
        return h("div", { class: d.id === S.selected ? "sel" : "", onclick: () => select(d.id, true) },
          m ? h("span", { class: "swatch", style: { background: m.color } }, m.key.toUpperCase()) : h("span", { class: "swatch none" }),
          d.id, h("span", { class: "cd" }, d.cd_nm !== null ? `${fmt(d.cd_nm)} nm` : `~${fmt(d.thinnest_nm)}`));
      }))));
    panel.querySelector(".devlist .sel")?.scrollIntoView({ block: "nearest" });
  }

  // ---------- toolbar ----------
  const toolbar = h("div", { class: "die-toolbar" });
  function drawToolbar() {
    const [dose, focus] = doseFocus(S.die);
    toolbar.replaceChildren(
      h("button", { class: "btn", onclick: () => stepDie(-1), title: "previous die [" }, "◀"),
      h("span", { class: "title" }, `Die ${dieKey()}`),
      h("span", { class: "muted small" }, `dose ${fmt(dose, 2)} ${batch.splits.dose_unit}` + (focus !== null ? ` · focus ${focus} ${batch.splits.focus_unit}` : "")),
      h("button", { class: "btn", onclick: () => stepDie(1), title: "next die ]" }, "▶"),
      h("span", { style: { marginLeft: "auto" } }),
      h("button", { class: "btn", onclick: fit, title: "F" }, "Fit"),
      h("button", { class: "btn", title: "rotate the field view 90°", onclick: () => { view.rot = (view.rot + 1) % 4; saveView(view); applyFilter(); fit(); drawPanel(); } }, `Rotate ${view.rot * 90}°`),
      h("button", { class: "btn", title: "mirror the field view left-right", onclick: () => { view.mirror = !view.mirror; saveView(view); applyFilter(); fit(); drawPanel(); } }, view.mirror ? "Mirrored" : "Mirror"));
  }

  // ---------- keyboard ----------
  function onKey(e) {
    if (e.target.closest && e.target.closest("input, textarea, select")) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const k = e.key.toLowerCase();
    if (keyToMode[k]) { mark(keyToMode[k]); e.preventDefault(); }
    else if (k === "u" || k === "backspace" || k === "delete") { mark(null); e.preventDefault(); }
    else if (k === "n") step(1);
    else if (k === "b") step(-1);
    else if (k === "]") stepDie(1);
    else if (k === "[") stepDie(-1);
    else if (k === "f") fit();
    else if (k === "z" && S.selected) centerOn(devById[S.selected], true);
    else if (k === "escape") select(null);
  }
  window.addEventListener("keydown", onKey);

  // ---------- layout ----------
  const stale = batch.reticle.gate_sha256 !== table.source.gate_sha256 || batch.reticle.device_table_created !== table.created;
  const unknown = Object.values(batch.results).flatMap((r) => Object.keys(r)).filter((id) => !devById[id]).length;
  const m = batch.meta;
  root.append(h("div", { class: "inspect" },
    h("section", {},
      h("div", { class: "small" }, h("b", {}, [m.lot, m.wafer].filter(Boolean).join(" / ") || batch.id)),
      h("div", { class: "small muted" }, [m.process_step, m.date, m.operator].filter(Boolean).join(" · ")),
      h("div", { class: "small", style: { margin: "4px 0 8px" } },
        h("a", { href: `#/batches/edit/${encodeURIComponent(batch.id)}` }, "edit batch"), " · ",
        h("a", { href: `/api/batches/${encodeURIComponent(batch.id)}/report`, target: "_blank", rel: "noopener" }, "report"), " · ",
        h("a", { href: `/api/batches/${encodeURIComponent(batch.id)}/report?download=1` }, "download report")),
      stale ? h("p", { class: "small", style: { color: "var(--danger)" } },
        `The reticle was re-extracted after this batch was created.${unknown ? ` ${unknown} recorded result(s) refer to devices that no longer exist and are hidden.` : ""}`) : null,
      waferBox,
      h("div", { class: "filters" },
        h("label", { class: "field" }, "CD lookup", cdSel),
        h("label", { class: "field" }, "Group", groupSel),
        h("label", { class: "field" }, "Kind", kindSel),
        h("label", { class: "field" }, "Name", textIn),
        h("label", { class: "small" }, openChk, " only devices not yet inspected in this die"))),
    h("section", { class: "center" }, toolbar, wrap),
    h("section", {}, panel)));

  applyFilter();
  drawToolbar(); drawWafer(); drawPanel();
  api.geometry(batch.reticle.id).then((g) => { outlines = g.outlines_um; draw(); })
    .catch(() => toast("No drawn geometry for this reticle; re-extract it to see device shapes when zoomed in", true));
  const ro = new ResizeObserver(resize);
  ro.observe(wrap);

  return () => {
    ro.disconnect();
    window.removeEventListener("keydown", onKey);
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
  };
}
