// The dashboard: views, the live connection, the clock, layout editing and
// the theme. Everything else lives in the view modules.
import { api, whenLoggedOut } from "./api.js";
import { h, icon, clear, refreshAges, debounce } from "./dom.js";
import { setEditing, resetLayout } from "./layout.js";
import { toast } from "./ui.js";
import { mountResearch, onResearchEvent, refreshResearch } from "./research.js";
import { mountTrading, onTradingEvent, refreshTrading, themeChanged } from "./trading.js";
import { mountLedger, onLedgerEvent } from "./ledger.js";
import { mountCommands } from "./commands.js";
import { refreshKill } from "./killswitch.js";

const VIEWS = ["research", "trading", "ledger", "commands"];
const mounted = new Set();
let meta = null;
let current = "research";

// --------------------------------------------------------------- theme
function storedTheme() {
  try { return localStorage.getItem("firm.theme"); } catch { return null; }
}
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const btn = document.getElementById("theme");
  clear(btn).append(icon(theme === "dark" ? "sun" : "moon", 17));
  btn.title = theme === "dark" ? "Light theme" : "Dark theme";
  try { localStorage.setItem("firm.theme", theme); } catch { /* private mode */ }
  themeChanged();
}

// --------------------------------------------------------------- views
async function show(view, push = true) {
  if (!VIEWS.includes(view)) view = "research";
  current = view;
  for (const v of VIEWS) {
    document.getElementById(`view-${v}`).hidden = v !== view;
    const tab = document.querySelector(`.tab[data-view="${v}"]`);
    tab.setAttribute("aria-selected", String(v === view));
  }
  document.getElementById("edit-layout").hidden = !(view === "research" || view === "trading");
  if (push) history.replaceState(null, "", `#${view}`);
  if (!mounted.has(view)) {
    mounted.add(view);
    try {
      if (view === "research") await mountResearch(meta);
      if (view === "trading") await mountTrading();
      if (view === "ledger") await mountLedger();
      if (view === "commands") await mountCommands();
    } catch (err) {
      mounted.delete(view);
      toast(err.message, "error", 7000);
    }
  }
  window.dispatchEvent(new Event("resize")); // grids and charts measure themselves
}

// --------------------------------------------------------------- live
const live = { ws: null, backoff: 1000, timer: null };

function setLive(state, text) {
  const el = document.getElementById("live");
  el.dataset.state = state;
  el.querySelector(".live-text").textContent = text;
}

const refreshSoon = {
  research: debounce(() => mounted.has("research") && refreshResearch(), 400),
  trading: debounce(() => mounted.has("trading") && refreshTrading(), 400),
};

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/api/live`);
  live.ws = ws;
  setLive("connecting", "Connecting");
  ws.onopen = () => {
    live.backoff = 1000;
    setLive("live", "Live");
  };
  ws.onmessage = (msg) => {
    let e;
    try { e = JSON.parse(msg.data); } catch { return; }
    onResearchEvent(e);
    onTradingEvent(e);
    onLedgerEvent(e);
    if (["risk_halt", "owner_command"].includes(e.type)) refreshKill();
    if (["dossier", "owner_command", "trial", "verdict", "model_call"].includes(e.type)) refreshSoon.research();
    else refreshSoon.trading();
  };
  ws.onclose = () => {
    setLive("down", "Reconnecting");
    clearTimeout(live.timer);
    live.timer = setTimeout(connect, live.backoff);
    live.backoff = Math.min(live.backoff * 2, 15000);
  };
  ws.onerror = () => ws.close();
}

// --------------------------------------------------------------- misc
function tickClock() {
  const d = new Date();
  document.getElementById("clock").textContent = `${d.toISOString().slice(11, 19)} UTC`;
}

function loggedOut() {
  if (document.getElementById("logged-out")) return;
  document.body.append(
    h("div", { id: "logged-out", class: "backdrop open" },
      h("div", { class: "modal modal-sm" },
        h("div", { class: "modal-head" }, h("h2", {}, "Please log in again")),
        h("div", { class: "modal-body" },
          h("p", {}, "This browser isn't logged in to the dashboard (or the token changed). In a terminal, run:"),
          h("div", { class: "code-line" }, h("code", {}, "uv run firm dashboard url")),
          h("p", { class: "muted small" }, "and open the link it prints.")))));
}

async function boot() {
  whenLoggedOut(loggedOut);
  applyTheme(storedTheme() || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));
  document.getElementById("theme").addEventListener("click", () =>
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));

  const editBtn = document.getElementById("edit-layout");
  editBtn.prepend(icon("layout", 15));
  let editing = false;
  editBtn.addEventListener("click", () => {
    editing = !editing;
    setEditing(editing);
    editBtn.setAttribute("aria-pressed", String(editing));
    editBtn.lastChild.textContent = editing ? " Done" : " Edit layout";
    if (editing) toast("Drag panels by their title bar; resize from a corner. Layouts are saved per view.", "ok", 5000);
  });
  editBtn.lastChild.textContent = " Edit layout";

  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => show(tab.dataset.view));
  }
  document.querySelector(".tabs").addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    const i = VIEWS.indexOf(current) + (e.key === "ArrowRight" ? 1 : -1);
    const next = VIEWS[(i + VIEWS.length) % VIEWS.length];
    show(next);
    document.querySelector(`.tab[data-view="${next}"]`).focus();
  });

  for (const view of ["research", "trading"]) {
    document.getElementById(`${view}-actions`).prepend(
      h("button", { class: "btn btn-ghost btn-sm reset-layout", type: "button", title: "Back to the default layout",
        onclick: async () => { await resetLayout(view); toast("Layout reset"); } }, icon("reset", 14), "Reset layout"));
  }

  try {
    meta = await api.get("/api/meta");
  } catch (err) {
    if (err.status !== 401) toast(err.message, "error", 8000);
    return;
  }
  document.getElementById("footer-version").textContent = `The Firm ${meta.version}`;
  tickClock();
  setInterval(tickClock, 1000);
  setInterval(() => refreshAges(), 15000);
  setInterval(() => {
    if (document.hidden) return;
    if (mounted.has("research")) refreshResearch();
    if (mounted.has("trading")) refreshTrading();
  }, 30000);
  connect();
  refreshKill();
  const start = location.hash.slice(1);
  await show(VIEWS.includes(start) ? start : "research", false);
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
else boot();
