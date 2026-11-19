// The ledger, newest first: filter by type, search, expand an event's
// payload, load older events, and new ones arrive live.
import { api } from "./api.js";
import { h, icon, clear, fmt } from "./dom.js";
import { typeBadge, TYPE_LABEL } from "./activity.js";

const PAGE = 200;
let events = []; // newest first
let oldest = null;
let typeFilter = "";
let search = "";
let tbody = null;
let countEl = null;
let moreBtn = null;
let typeSelect = null;

export async function mountLedger() {
  const root = document.getElementById("ledger-root");
  typeSelect = h("select", { class: "select-sm", "aria-label": "Event type",
    onchange: (e) => { typeFilter = e.target.value; render(); } }, h("option", { value: "" }, "All types"));
  const searchBox = h("input", { type: "search", class: "input-sm", placeholder: "Search reasons…", "aria-label": "Search",
    oninput: (e) => { search = e.target.value.toLowerCase(); render(); } });
  countEl = h("span", { class: "muted small" });
  moreBtn = h("button", { class: "btn btn-ghost", type: "button", onclick: loadOlder }, "Load older events");
  tbody = h("tbody");
  clear(root).append(
    h("div", { class: "toolbar" },
      h("div", { class: "search" }, icon("search", 14), searchBox),
      typeSelect, h("span", { class: "spacer" }), countEl),
    h("div", { class: "table-wrap table-tall" },
      h("table", { class: "table table-ledger" },
        h("thead", {}, h("tr", {}, ["#", "Recorded (UTC)", "Type", "Reason"].map((t) => h("th", {}, t)))),
        tbody)),
    h("div", { class: "toolbar toolbar-foot" }, moreBtn),
  );
  const first = await api.get(`/api/ledger?limit=${PAGE}`);
  events = first.slice().reverse();
  oldest = first.length ? first[0].sequence : null;
  moreBtn.hidden = !oldest;
  render();
}

export function onLedgerEvent(e) {
  if (!tbody) return;
  if (events.length && events[0].sequence >= e.sequence) return;
  events.unshift(e);
  render(true);
}

async function loadOlder() {
  if (oldest === null || oldest <= 0) return;
  moreBtn.disabled = true;
  const page = await api.get(`/api/ledger?before=${oldest}&limit=${PAGE}`);
  events.push(...page.slice().reverse());
  oldest = page.length ? page[0].sequence : 0;
  moreBtn.disabled = false;
  moreBtn.hidden = oldest <= 0 || !page.length;
  render();
}

function render(fresh = false) {
  const types = [...new Set(events.map((e) => e.type))].sort();
  const current = typeSelect.value;
  clear(typeSelect).append(
    h("option", { value: "" }, "All types"),
    types.map((t) => h("option", { value: t, selected: t === current ? true : null }, TYPE_LABEL[t] || t)),
  );
  const shown = events.filter((e) => (!typeFilter || e.type === typeFilter) && (!search || e.reason.toLowerCase().includes(search)));
  countEl.textContent = `${fmt.int(shown.length)} of ${fmt.int(events.length)} loaded`;
  clear(tbody);
  if (!shown.length) {
    tbody.append(h("tr", {}, h("td", { colspan: 4, class: "empty" }, events.length ? "No event matches." : "The ledger is empty.")));
    return;
  }
  shown.slice(0, 1000).forEach((e, i) => {
    const detail = h("tr", { class: "payload-row", hidden: true },
      h("td", { colspan: 4 }, h("pre", { class: "payload" }, JSON.stringify(e.payload, null, 2))));
    const row = h("tr", { class: `row-click${fresh && i === 0 ? " flash" : ""}`, tabindex: "0",
      onclick: () => { detail.hidden = !detail.hidden; row.classList.toggle("expanded", !detail.hidden); },
      onkeydown: (ev) => { if (ev.key === "Enter") row.click(); } },
      h("td", { class: "mono muted" }, String(e.sequence)),
      h("td", { class: "mono nowrap" }, fmt.utc(e.recorded_at).replace(" UTC", "")),
      h("td", {}, typeBadge(e.type)),
      h("td", { class: "cell-reason" }, e.reason),
    );
    tbody.append(row, detail);
  });
}
