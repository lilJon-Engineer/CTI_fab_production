import { api, state, toast } from "./api.js";
import { renderReticles } from "./reticles.js";
import { renderBatches } from "./batches.js";
import { renderInspect } from "./inspect.js";

const views = { reticles: renderReticles, batches: renderBatches, inspect: renderInspect };
let cleanup = null;

async function route() {
  const [, name = "batches", ...rest] = (location.hash || "#/batches").split("/");
  const view = views[name] ? name : "batches";
  document.querySelectorAll(".topbar nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  if (cleanup) { cleanup(); cleanup = null; }
  const root = document.getElementById("view");
  root.replaceChildren();
  try {
    cleanup = (await views[view](root, rest.map(decodeURIComponent))) || null;
  } catch (e) {
    console.error(e);
    toast(e.message, true);
  }
}

async function start() {
  state.settings = await api.settings();
  state.modes = state.settings.failure_modes;
  state.modeById = Object.fromEntries(state.modes.map((m) => [m.id, m]));
  window.addEventListener("hashchange", route);
  route();
}

start().catch((e) => toast(e.message, true));
