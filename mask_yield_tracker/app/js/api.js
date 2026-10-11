// Thin wrapper over the local server API.
async function call(method, url, body, raw = false) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    if (raw) opt.body = body;
    else { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  }
  const r = await fetch(url, opt);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `${r.status} ${r.statusText}`);
  return data;
}

export const api = {
  settings: () => call("GET", "/api/settings"),
  reticles: () => call("GET", "/api/reticles"),
  reticle: (id) => call("GET", `/api/reticles/${encodeURIComponent(id)}`),
  uploadReticle: (file, name) =>
    call("POST", `/api/reticles?filename=${encodeURIComponent(file.name)}&name=${encodeURIComponent(name || "")}`, file, true),
  saveConfig: (id, text) => call("PUT", `/api/reticles/${encodeURIComponent(id)}/config`, text, true),
  extract: (id) => call("POST", `/api/reticles/${encodeURIComponent(id)}/extract`),
  deleteReticle: (id) => call("DELETE", `/api/reticles/${encodeURIComponent(id)}`),
  devices: (id) => call("GET", `/api/reticles/${encodeURIComponent(id)}/devices`),
  geometry: (id) => call("GET", `/api/reticles/${encodeURIComponent(id)}/geometry`),
  batches: () => call("GET", "/api/batches"),
  batch: (id) => call("GET", `/api/batches/${encodeURIComponent(id)}`),
  createBatch: (body) => call("POST", "/api/batches", body),
  updateBatch: (id, body) => call("PUT", `/api/batches/${encodeURIComponent(id)}`, body),
  deleteBatch: (id) => call("DELETE", `/api/batches/${encodeURIComponent(id)}`),
  importBatch: (doc) => call("POST", "/api/batches/import", doc),
  record: (id, die, updates) => call("POST", `/api/batches/${encodeURIComponent(id)}/results`, { die, updates }),
  diesOnWafer: (wafer, fw, fh) => call("POST", "/api/wafer/dies", { wafer, field_w_mm: fw, field_h_mm: fh }),
};

// ---- small DOM helpers shared by the views ----
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function toast(msg, isError = false) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "show" + (isError ? " error" : "");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => (t.className = ""), isError ? 6000 : 2500);
}

export function guard(fn) {
  return async (...args) => {
    try { return await fn(...args); } catch (e) { console.error(e); toast(e.message, true); }
  };
}

export function download(filename, text, type = "application/json") {
  const a = h("a", { href: URL.createObjectURL(new Blob([text], { type })), download: filename });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

export function fmt(v, digits = 0) {
  return v === null || v === undefined || v === "" ? "–" : Number(v).toFixed(digits);
}

export const state = { settings: null, modes: [], modeById: {} };
