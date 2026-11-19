// The owner's kill switch: state, the banner, and the confirmation dialog.
// The same as `firm kill` / `firm resume`: recorded in the ledger, sent to
// the risk desk over the bus, confirmed when the desk records it.
import { api } from "./api.js";
import { h, icon, clear, timeAgo } from "./dom.js";
import { openModal, toast, field } from "./ui.js";

let state = { state: { global: null, markets: {} }, markets: [] };
const listeners = new Set();

export function onKillChange(fn) {
  listeners.add(fn);
}

export function killState() {
  return state;
}

export async function refreshKill() {
  try {
    state = await api.get("/api/killswitch");
  } catch {
    return state;
  }
  renderBanner();
  for (const fn of listeners) fn(state);
  return state;
}

function active() {
  const out = [];
  if (state.state.global) out.push({ market: null, ...state.state.global });
  for (const [m, v] of Object.entries(state.state.markets)) out.push({ market: m, ...v });
  return out;
}

function renderBanner() {
  const banner = document.getElementById("kill-banner");
  const on = active();
  if (!on.length) {
    banner.hidden = true;
    return;
  }
  const names = on.map((k) => k.market || "every market").join(", ");
  clear(banner).append(
    h("div", { class: "kill-banner-inner" },
      icon("shield", 16),
      h("strong", {}, "Kill switch ON"),
      h("span", {}, `${names}: no new buying. Sells that reduce a position still go through.`),
      h("button", { class: "btn btn-sm kill-banner-btn", type: "button",
        onclick: () => { location.hash = "#trading"; document.querySelector('.tab[data-view="trading"]').click(); } },
        "Open Risk"),
    ),
  );
  banner.hidden = false;
}

// The Risk panel's kill-switch block.
export function killBlock(runMarkets) {
  const markets = [...new Set([...runMarkets, ...Object.keys(state.state.markets)])].sort();
  const row = (label, market, on) =>
    h("li", { class: `kill-row ${on ? "is-on" : ""}` },
      h("span", { class: `dot ${on ? "dot-bad" : "dot-good"}` }),
      h("span", { class: "kill-name" }, h("strong", {}, label),
        on ? h("span", { class: "muted small" }, ` · ${on.reason} · `, timeAgo(on.issued_at)) : h("span", { class: "muted small" }, " · trading allowed")),
      on
        ? h("button", { class: "btn btn-ghost btn-xs", type: "button", onclick: () => openKill("resume", market) }, icon("play", 13), "Resume")
        : h("button", { class: "btn btn-danger btn-xs", type: "button", onclick: () => openKill("kill", market) }, icon("shield", 13), "Kill"),
    );
  return h("div", {},
    h("ul", { class: "kill-list" },
      markets.map((m) => row(m, m, state.state.markets[m])),
      row("All markets", null, state.state.global),
    ),
    h("p", { class: "muted small kill-note" },
      "A kill stops new buying through the risk desk; sells that reduce a position still go through. It survives a restart of the paper run. Same as ",
      h("code", {}, "firm kill"), " / ", h("code", {}, "firm resume"), "."),
  );
}

export function openKill(action, market) {
  const kill = action === "kill";
  const target = market || "ALL";
  const label = market || "every market";
  const reason = h("input", { type: "text", maxlength: 300, required: true,
    placeholder: kill ? "e.g. feed looks wrong; stop until I check" : "e.g. checked, all fine" });
  const confirm = h("input", { type: "text", autocomplete: "off", spellcheck: "false", placeholder: target });
  const error = h("p", { class: "form-error", role: "alert" });
  const { form } = openModal({
    title: kill ? `Kill switch: ${label}` : `Resume trading: ${label}`,
    subtitle: kill
      ? "The risk desk stops all new buying here, at once and until you resume. Sells that reduce a position still go through; nothing already filled is undone."
      : "The risk desk allows new buying here again, at the next bar.",
    size: "md",
    body: h("div", { class: "form" },
      kill ? h("ul", { class: "kill-effects" },
        h("li", {}, "Recorded in the ledger and sent to the paper run's risk desk over the bus."),
        h("li", {}, "You get an urgent alert when the desk applies it."),
        h("li", {}, "It stays on after a restart of the paper run, until you resume.")) : null,
      field("Reason (recorded)", reason),
      field(h("span", {}, "Type ", h("code", {}, target), " to confirm"), confirm),
      error,
    ),
    actions: [
      { label: "Cancel", onClick: (_e, close) => close() },
      { label: kill ? `Kill ${label}` : `Resume ${label}`, kind: kill ? "danger" : "primary", submit: true,
        onClick: async (_e, close) => {
          if (!reason.value.trim()) return (error.textContent = "A reason is required.");
          if (confirm.value.trim() !== target) return (error.textContent = `Type ${target} exactly to confirm.`);
          const btn = form.querySelector(".modal-foot [type=submit]");
          btn.disabled = true;
          btn.textContent = "Waiting for the risk desk…";
          try {
            const r = await api.post("/api/killswitch", { action, market, reason: reason.value.trim(), confirm: target });
            close();
            if (r.confirmed) {
              toast(kill ? `Kill switch ON for ${label}: confirmed by the risk desk.` : `Trading resumed for ${label}: confirmed by the risk desk.`, "ok", 6000);
            } else if (r.sent) {
              toast("Sent on the bus but not confirmed yet: is the paper run running? It applies as soon as it reads the bus, and at its next start in any case.", "warn", 9000);
            } else {
              toast(kill
                ? "Recorded, but the bus isn't running, so a running paper run hasn't been told. It applies at the next start. To stop a run now: Ctrl-C in its terminal."
                : "Recorded; it applies when the paper run next starts (the bus isn't running).", "warn", 11000);
            }
            await refreshKill();
          } catch (err) {
            btn.disabled = false;
            btn.textContent = kill ? `Kill ${label}` : `Resume ${label}`;
            error.textContent = err.message;
          }
        } },
    ],
  });
  // the confirm button stays disabled until the target is typed
  const submit = form.querySelector(".modal-foot [type=submit]");
  const check = () => { submit.disabled = confirm.value.trim() !== target; };
  confirm.addEventListener("input", check);
  check();
}
