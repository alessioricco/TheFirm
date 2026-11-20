// The research floor: dossiers, budgets, stages, the floor brief, live
// activity, and every steering command (start, pause, resume, redirect,
// close, floor brief), the same as `firm research ...`; and the senior quant's
// brief proposals, which the owner reads in full and approves or rejects.
import { api } from "./api.js";
import { h, icon, clear, fmt, timeAgo } from "./dom.js";
import { mountGrid, panelAction } from "./layout.js";
import { openModal, confirmDialog, toast, field, openDrawer, closeDrawer, drawerOpenFor, setDrawerSubject } from "./ui.js";
import { activityFeed } from "./activity.js";

const STAGES = ["draft", "hypothesis", "research", "submitted", "passed", "failed", "closed"];
const FINAL = new Set(["passed", "failed", "closed"]);
const STAGE_LABEL = {
  draft: "Draft", hypothesis: "Hypothesis", research: "Research", submitted: "Submitted",
  passed: "Passed", failed: "Failed", closed: "Closed",
};
const RESEARCH_EVENTS = new Set(["dossier", "owner_command", "trial", "verdict", "model_call", "oos_vault"]);

const DEFAULTS = [
  { id: "floor", title: "Floor", subtitle: "researchers and the queue", x: 0, y: 0, w: 12, h: 2, minH: 2 },
  { id: "dossiers", title: "Dossiers", subtitle: "one strategy each, from brief to verdict", x: 0, y: 2, w: 8, h: 7, minW: 5 },
  { id: "brief", title: "Floor brief", subtitle: "standing direction for every researcher", x: 8, y: 2, w: 4, h: 3 },
  { id: "stages", title: "Stages", subtitle: "where the dossiers are", x: 8, y: 5, w: 4, h: 4 },
  { id: "proposals", title: "Proposals", subtitle: "strategy ideas from the senior quant, for your go", x: 0, y: 9, w: 12, h: 5, minW: 5 },
  { id: "activity", title: "Research activity", subtitle: "live from the ledger", x: 0, y: 14, w: 12, h: 4 },
];

let state = { floor: null, meta: null, proposals: [] };
let panels = null;
let feed = null;

export async function mountResearch(meta) {
  state.meta = meta;
  const entry = await mountGrid("research", document.getElementById("grid-research"), DEFAULTS);
  panels = entry.panels;
  const actions = document.getElementById("research-actions");
  actions.append(
    h("button", { class: "btn btn-ghost", type: "button", onclick: editFloorBrief }, icon("brief"), "Floor brief"),
    h("button", { class: "btn btn-primary", type: "button", onclick: newDossier }, icon("plus"), "New dossier"),
  );
  panels.get("dossiers").actions.append(panelAction("Refresh", "refresh", () => refreshResearch()));
  panels.get("brief").actions.append(panelAction("Edit", "edit", editFloorBrief));
  panels.get("proposals").actions.append(panelAction("Refresh", "refresh", () => refreshResearch()));
  feed = activityFeed(panels.get("activity").body, {
    filter: (e) => RESEARCH_EVENTS.has(e.type),
    empty: "No research activity yet. Start a dossier with “New dossier”.",
  });
  await feed.load();
  await refreshResearch();
}

export function onResearchEvent(e) {
  if (!feed) return;
  if (RESEARCH_EVENTS.has(e.type)) feed.push(e);
}

export async function refreshResearch() {
  try {
    const [floor, proposals] = await Promise.all([api.get("/api/research"), api.get("/api/proposals")]);
    state.floor = floor;
    state.proposals = proposals.proposals;
  } catch (err) {
    renderError(err);
    return;
  }
  renderFloor();
  renderDossiers();
  renderBrief();
  renderStages();
  renderProposals();
  const open = drawerOpenFor();
  if (open) {
    const d = state.floor.dossiers.find((x) => x.dossier_id === open);
    if (d) showDossier(d, false);
    const p = state.proposals.find((x) => x.proposal_id === open);
    if (p) showProposal(p, false);
  }
}

function renderError(err) {
  for (const id of ["floor", "dossiers", "brief", "stages", "proposals"]) {
    clear(panels.get(id).body).append(h("div", { class: "empty empty-error" }, err.message));
  }
}

// ------------------------------------------------------------------ panels
function renderFloor() {
  const f = state.floor;
  const by = (pred) => f.dossiers.filter(pred).length;
  const running = by((d) => d.place === "running");
  const waiting = state.proposals.filter((p) => p.status === "proposed").length;
  const kpis = [
    { label: "Proposals for you", value: waiting, tone: waiting ? "accent" : "" },
    { label: "Running", value: `${running}`, unit: `/ ${f.researchers}`, tone: running ? "accent" : "" },
    { label: "Waiting", value: by((d) => d.place.startsWith("waiting")) },
    { label: "Paused", value: by((d) => d.place === "paused"), tone: "warn" },
    { label: "Passed", value: by((d) => d.stage === "passed"), tone: "good" },
    { label: "Failed", value: by((d) => d.stage === "failed"), tone: "bad" },
    { label: "Closed", value: by((d) => d.stage === "closed") },
    { label: "Trials used", value: fmt.int(f.dossiers.reduce((s, d) => s + d.usage.trials, 0)) },
  ];
  clear(panels.get("floor").body).append(
    h("div", { class: "kpis" },
      kpis.map((k) =>
        h("div", { class: `kpi ${k.tone ? `kpi-${k.tone}` : ""}` },
          h("span", { class: "kpi-label" }, k.label),
          h("span", { class: "kpi-value" }, String(k.value), k.unit ? h("small", {}, ` ${k.unit}`) : null),
        ),
      ),
    ),
  );
}

function meter(used, total, label, format = fmt.int) {
  const pct = fmt.pct(used, total);
  const tone = total === 0 ? (used > 0 ? "bad" : "zero") : pct >= 90 ? "bad" : pct >= 70 ? "warn" : "good";
  return h("div", { class: `meter meter-${tone}`, title: `${label}: ${format(used)} of ${format(total)}` },
    h("div", { class: "meter-track" }, h("div", { class: "meter-fill", style: { width: `${total ? pct : used > 0 ? 100 : 0}%` } })),
    h("span", { class: "meter-text" }, `${format(used)} / ${format(total)}`),
  );
}

function stageBadge(stage) {
  return h("span", { class: `badge stage-${stage}` }, STAGE_LABEL[stage] || stage);
}

function placeTag(place) {
  if (place === "running") return h("span", { class: "tag tag-live" }, h("span", { class: "dot" }), "Running");
  if (place === "paused") return h("span", { class: "tag tag-warn" }, "Paused");
  if (place.startsWith("waiting")) return h("span", { class: "tag" }, place.replace("waiting ", "Waiting ").replace(" in the queue", ""));
  return h("span", { class: "tag tag-muted" }, "–");
}

function renderDossiers() {
  const body = clear(panels.get("dossiers").body);
  const list = state.floor.dossiers;
  panels.get("dossiers").subtitle.textContent = list.length
    ? `${list.length} dossier${list.length === 1 ? "" : "s"}, oldest first`
    : "one strategy each, from brief to verdict";
  if (!list.length) {
    body.append(
      h("div", { class: "empty" },
        h("p", { class: "empty-title" }, "No dossiers yet"),
        h("p", {}, "A dossier is one researcher's record for one strategy. Open one with a brief; it joins the queue."),
        h("button", { class: "btn btn-primary", type: "button", onclick: newDossier }, icon("plus"), "New dossier"),
      ),
    );
    return;
  }
  const rows = list.map((d) =>
    h("tr", { class: "row-click", tabindex: "0", onclick: () => showDossier(d),
      onkeydown: (e) => { if (e.key === "Enter") showDossier(d); } },
      h("td", { class: "mono" }, d.dossier_id),
      h("td", {}, stageBadge(d.stage)),
      h("td", {}, placeTag(d.place)),
      h("td", { class: "cell-brief", title: d.brief }, d.brief),
      h("td", {}, budgetCell(d)),
      h("td", { class: "cell-actions", onclick: (e) => e.stopPropagation() }, rowActions(d)),
    ),
  );
  body.append(
    h("div", { class: "table-wrap" },
      h("table", { class: "table" },
        h("thead", {}, h("tr", {},
          ["Dossier", "Stage", "Place", "Brief", "Budget used", ""].map((t) => h("th", {}, t)))),
        h("tbody", {}, rows),
      ),
    ),
  );
}

function budgetCell(d) {
  const rows = [
    ["Trials", d.usage.trials, d.budget.trials, fmt.int],
    ["Tokens", d.usage.tokens, d.budget.tokens, fmt.compact],
    ["Hours", d.usage.hours, d.budget.wall_clock_hours, (v) => fmt.num(v, 1)],
  ];
  if (d.usage.cloud_gbp > 0 || d.budget.cloud_spend_gbp > 0) {
    rows.push(["Cloud", d.usage.cloud_gbp, d.budget.cloud_spend_gbp, (v) => fmt.money(v, "GBP")]);
  }
  return h("div", { class: "mini-meters" },
    rows.map(([label, used, total, f]) => {
      const pct = fmt.pct(used, total);
      const tone = pct >= 90 ? "bad" : pct >= 70 ? "warn" : "good";
      return h("div", { class: `mini mini-${tone}`, title: `${label}: ${f(used)} of ${f(total)}` },
        h("span", { class: "mini-label" }, label),
        h("span", { class: "mini-track" }, h("span", { class: "mini-fill", style: { width: `${pct}%` } })),
        h("span", { class: "mini-text" }, `${f(used)}/${f(total)}`));
    }));
}

function rowActions(d) {
  if (FINAL.has(d.stage)) return h("span", { class: "muted small" }, "final");
  return h("div", { class: "btn-group" },
    d.paused
      ? iconButton("Resume", "play", () => resume(d))
      : iconButton("Pause", "pause", () => pause(d)),
    iconButton("Redirect", "redirect", () => redirect(d)),
    iconButton("Close", "close", () => closeDossier(d), "danger"),
  );
}

function iconButton(label, name, onClick, kind = "ghost") {
  return h("button", { class: `btn btn-${kind} btn-icon btn-sm`, type: "button", title: label, "aria-label": label, onclick: onClick }, icon(name, 15));
}

function renderBrief() {
  const body = clear(panels.get("brief").body);
  const text = state.floor.floor_brief;
  if (!text) {
    body.append(h("div", { class: "empty" },
      h("p", {}, "No floor brief. Set one to steer every researcher at once, e.g. “stay uncorrelated with the daily MA strategy”."),
      h("button", { class: "btn btn-ghost", type: "button", onclick: editFloorBrief }, icon("edit"), "Set floor brief")));
    return;
  }
  body.append(h("blockquote", { class: "brief" }, text));
}

function renderStages() {
  const body = clear(panels.get("stages").body);
  const counts = Object.fromEntries(STAGES.map((s) => [s, 0]));
  for (const d of state.floor.dossiers) counts[d.stage] = (counts[d.stage] || 0) + 1;
  const max = Math.max(1, ...Object.values(counts));
  body.append(
    h("div", { class: "bars" },
      STAGES.map((s) =>
        h("div", { class: "bar-row" },
          h("span", { class: "bar-label" }, STAGE_LABEL[s]),
          h("div", { class: "bar-track" }, h("div", { class: `bar-fill stage-fill-${s}`, style: { width: `${(100 * counts[s]) / max}%` } })),
          h("span", { class: "bar-value" }, String(counts[s])),
        ),
      ),
    ),
  );
}

// ------------------------------------------------------------------ drawer
function showDossier(d, open = true) {
  setDrawerSubject(d.dossier_id);
  const section = (title, ...content) => h("section", { class: "drawer-section" }, h("h4", {}, title), ...content);
  const kv = (k, v) => h("div", { class: "kv" }, h("span", { class: "kv-k" }, k), h("span", { class: "kv-v" }, v));
  const content = h("div", {},
    h("div", { class: "drawer-head" },
      h("p", { class: "eyebrow" }, "Dossier"),
      h("h2", { class: "mono" }, d.dossier_id),
      h("div", { class: "drawer-tags" }, stageBadge(d.stage), placeTag(d.place)),
    ),
    FINAL.has(d.stage) ? null : h("div", { class: "drawer-actions" },
      d.paused
        ? h("button", { class: "btn btn-ghost", type: "button", onclick: () => resume(d) }, icon("play"), "Resume")
        : h("button", { class: "btn btn-ghost", type: "button", onclick: () => pause(d) }, icon("pause"), "Pause"),
      h("button", { class: "btn btn-ghost", type: "button", onclick: () => redirect(d) }, icon("redirect"), "Redirect"),
      h("button", { class: "btn btn-danger", type: "button", onclick: () => closeDossier(d) }, icon("close"), "Close"),
    ),
    section("Brief", h("p", { class: "prose" }, d.brief)),
    section("Scope",
      kv("Market", d.market),
      kv("Instruments", d.instruments.join(", ")),
      kv("Timeframes", d.timeframes.join(", ")),
      kv("Opened", h("span", {}, fmt.utc(d.opened_at), " · ", timeAgo(d.opened_at))),
      d.redirected_from ? kv("Redirected from", h("span", { class: "mono" }, d.redirected_from)) : null,
    ),
    section("Budget used",
      h("div", { class: "budget-grid" },
        h("span", {}, "Trials"), meter(d.usage.trials, d.budget.trials, "Trials"),
        h("span", {}, "Hours"), meter(d.usage.hours, d.budget.wall_clock_hours, "Hours", (v) => fmt.num(v, 2)),
        h("span", {}, "Tokens"), meter(d.usage.tokens, d.budget.tokens, "Tokens", fmt.compact),
        h("span", {}, "Cloud"), meter(d.usage.cloud_gbp, d.budget.cloud_spend_gbp, "Cloud spend", (v) => fmt.money(v, "GBP")),
      ),
      h("p", { class: "muted small" }, "The budget a dossier was opened with; the manager closes it when one is spent."),
    ),
    section("Hypothesis",
      d.hypothesis
        ? h("div", {}, h("p", { class: "prose" }, d.hypothesis),
            d.rationale ? h("p", { class: "prose muted" }, h("strong", {}, "Why: "), d.rationale) : null,
            h("p", { class: "muted small" }, `Written ${fmt.utc(d.hypothesis_at)}. Locked by the first backtest.`))
        : h("p", { class: "muted" }, "Not written yet. It must come before any code or backtest."),
    ),
    section(`Code (${d.code.length})`,
      d.code.length
        ? h("ul", { class: "list" }, d.code.map((c) => h("li", {}, h("span", { class: "mono" }, c.file), h("span", { class: "muted small" }, ` sha ${c.sha256.slice(0, 12)} · ${fmt.utc(c.at)}`))))
        : h("p", { class: "muted" }, "None yet. Agent code lives in the private store and runs only in the sandbox."),
    ),
    section(`Trials (${d.trial_ids.length})`,
      d.trial_ids.length
        ? h("div", { class: "chips" }, d.trial_ids.slice(-40).map((t) => h("span", { class: "chip mono" }, t)))
        : h("p", { class: "muted" }, "None yet. Every backtest is a trial in the global count."),
    ),
    section("Verdicts",
      d.verdicts.length
        ? h("ul", { class: "list" }, d.verdicts.map((v) => h("li", {}, stageBadge(v.outcome === "pass" ? "passed" : v.outcome === "fail" ? "failed" : "research"), " ", h("span", { class: "mono" }, v.verdict_id), h("span", { class: "muted small" }, ` · ${fmt.utc(v.at)}`))))
        : h("p", { class: "muted" }, "Not submitted."),
    ),
    d.notes.length ? section(`Notes (${d.notes.length})`, h("ul", { class: "list" }, d.notes.map((n) => h("li", {}, h("span", { class: "muted small" }, fmt.utc(n.at)), h("p", { class: "prose" }, n.text))))) : null,
    d.closed_reason ? section("Closed", h("p", { class: "prose" }, d.closed_reason)) : null,
  );
  if (open || drawerOpenFor()) openDrawer(content);
}

// ---------------------------------------------------------------- commands
async function act(promise, message) {
  try {
    await promise;
    toast(message);
    await refreshResearch();
    return true;
  } catch (err) {
    toast(err.message, "error", 6500);
    return false;
  }
}

function newDossier() {
  const meta = state.meta;
  const brief = h("textarea", { rows: 5, required: true, maxlength: 4000, placeholder: "What should this researcher look for, and why? e.g. Momentum on BTC after breakouts: does a 20-bar high predict the next week?" });
  const market = h("select", {}, meta.markets.map((m) => h("option", { value: m, selected: m === "crypto" ? true : null }, m)));
  const instruments = h("input", { type: "text", value: "BTC-USD", placeholder: "BTC-USD, ETH-USD", autocomplete: "off" });
  const tfBoxes = meta.timeframes.map((tf) => {
    const box = h("input", { type: "checkbox", value: tf, checked: tf === "1d" ? true : null });
    return { tf, box, el: h("label", { class: "check-chip" }, box, h("span", {}, tf)) };
  });
  const dossierId = h("input", { type: "text", placeholder: "default: d-<date>-<random>", autocomplete: "off", pattern: "[A-Za-z0-9][A-Za-z0-9_-]*" });
  const error = h("p", { class: "form-error", role: "alert" });
  openModal({
    title: "New dossier",
    subtitle: "Opens a dossier with a brief; it joins the queue and runs when a researcher is free.",
    size: "lg",
    body: h("div", { class: "form" },
      field("Brief", brief, "The researcher reads this first. Be specific about the market, the idea and what to avoid."),
      h("div", { class: "form-row" },
        field("Market", market),
        field("Instruments", instruments, "Firm IDs, separated by commas."),
      ),
      field("Timeframes", h("div", { class: "check-chips" }, tfBoxes.map((t) => t.el))),
      h("details", { class: "advanced" }, h("summary", {}, "Advanced"), field("Dossier ID", dossierId, "Letters, digits, - and _.")),
      error,
    ),
    actions: [
      { label: "Cancel", onClick: (_e, close) => close() },
      { label: "Open dossier", kind: "primary", submit: true, onClick: async (_e, close) => {
        const body = {
          brief: brief.value.trim(),
          market: market.value,
          instruments: instruments.value.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean),
          timeframes: tfBoxes.filter((t) => t.box.checked).map((t) => t.tf),
          dossier_id: dossierId.value.trim() || null,
        };
        if (!body.brief) return (error.textContent = "A brief is required.");
        if (!body.instruments.length) return (error.textContent = "At least one instrument.");
        if (!body.timeframes.length) return (error.textContent = "At least one timeframe.");
        try {
          const d = await api.post("/api/research", body);
          close();
          toast(`Dossier ${d.dossier_id} opened: ${d.place}`);
          await refreshResearch();
        } catch (err) {
          error.textContent = err.message;
        }
      } },
    ],
  });
}

function pause(d) {
  const reason = h("input", { type: "text", placeholder: "optional, recorded in the ledger", maxlength: 300 });
  openModal({
    title: `Pause ${d.dossier_id}`,
    subtitle: "It stops running and gives its place to the next dossier in the queue. Its budget doesn't run while paused.",
    size: "sm",
    body: field("Reason", reason),
    actions: [
      { label: "Cancel", onClick: (_e, close) => close() },
      { label: "Pause", kind: "primary", submit: true, onClick: async (_e, close) => {
        close();
        await act(api.post(`/api/research/${d.dossier_id}/pause`, { reason: reason.value.trim() || null }), `${d.dossier_id} paused`);
      } },
    ],
  });
}

async function resume(d) {
  await act(api.post(`/api/research/${d.dossier_id}/resume`, {}), `${d.dossier_id} resumed`);
}

function redirect(d) {
  const started = d.stage !== "draft";
  const brief = h("textarea", { rows: 5, maxlength: 4000, value: started ? "" : d.brief });
  const error = h("p", { class: "form-error", role: "alert" });
  openModal({
    title: `Redirect ${d.dossier_id}`,
    subtitle: started
      ? "Research has started, so its hypothesis is locked: this closes the dossier and opens a new, linked one with the new brief."
      : "It hasn't started yet: it simply takes the new brief.",
    size: "md",
    body: h("div", { class: "form" }, field("New brief", brief), error),
    actions: [
      { label: "Cancel", onClick: (_e, close) => close() },
      { label: started ? "Close and open new" : "Save brief", kind: "primary", submit: true, onClick: async (_e, close) => {
        if (!brief.value.trim()) return (error.textContent = "A brief is required.");
        try {
          const n = await api.post(`/api/research/${d.dossier_id}/redirect`, { brief: brief.value.trim() });
          close();
          toast(n.dossier_id === d.dossier_id ? `${d.dossier_id}: new brief` : `${d.dossier_id} closed; ${n.dossier_id} opened`);
          await refreshResearch();
        } catch (err) {
          error.textContent = err.message;
        }
      } },
    ],
  });
}

function closeDossier(d) {
  const reason = h("input", { type: "text", required: true, maxlength: 300, placeholder: "e.g. dead end: no edge after costs" });
  const error = h("p", { class: "form-error", role: "alert" });
  openModal({
    title: `Close ${d.dossier_id}?`,
    subtitle: "Closing is final: the dossier takes no more changes. A new idea needs a new dossier.",
    size: "sm",
    body: h("div", { class: "form" }, field("Reason (recorded)", reason), error),
    actions: [
      { label: "Cancel", onClick: (_e, close) => close() },
      { label: "Close dossier", kind: "danger", submit: true, onClick: async (_e, close) => {
        if (!reason.value.trim()) return (error.textContent = "A reason is required.");
        close();
        const ok = await act(api.post(`/api/research/${d.dossier_id}/close`, { reason: reason.value.trim() }), `${d.dossier_id} closed`);
        if (ok && drawerOpenFor() === d.dossier_id) closeDrawer();
      } },
    ],
  });
}

function editFloorBrief() {
  const current = state.floor ? state.floor.floor_brief || "" : "";
  const text = h("textarea", { rows: 6, maxlength: 4000, value: current, placeholder: "e.g. Prefer 1h and 4h; stay uncorrelated with the daily MA strategy." });
  openModal({
    title: "Floor brief",
    subtitle: "Standing direction for every researcher. The last one set counts.",
    size: "md",
    body: field("Brief", text),
    actions: [
      current ? { label: "Clear", kind: "danger", onClick: async (_e, close) => {
        if (!(await confirmDialog({ title: "Clear the floor brief?", message: "Researchers will no longer see it.", confirm: "Clear", danger: true }))) return;
        close();
        await act(api.put("/api/research/brief", { text: "" }), "Floor brief cleared");
      } } : null,
      { label: "Cancel", onClick: (_e, close) => close() },
      { label: "Save", kind: "primary", submit: true, onClick: async (_e, close) => {
        close();
        await act(api.put("/api/research/brief", { text: text.value }), "Floor brief saved");
      } },
    ].filter(Boolean),
  });
}

// --------------------------------------------------------------- proposals
const PROPOSAL_STATUS = {
  proposed: ["Waiting for you", "submitted"],
  approved: ["Approved", "passed"],
  rejected: ["Rejected", "closed"],
};

function proposalBadge(status) {
  const [label, tone] = PROPOSAL_STATUS[status] || [status, "draft"];
  return h("span", { class: `badge stage-${tone}` }, label);
}

function renderProposals() {
  const panel = panels.get("proposals");
  const body = clear(panel.body);
  const order = { proposed: 0, approved: 1, rejected: 2 };
  const list = [...state.proposals].sort((a, b) =>
    (order[a.status] - order[b.status]) || (a.proposed_at < b.proposed_at ? 1 : -1));
  const waiting = list.filter((p) => p.status === "proposed").length;
  panel.subtitle.textContent = list.length
    ? `${waiting} waiting for your go, ${list.length} in all`
    : "strategy ideas from the senior quant, for your go";
  if (!list.length) {
    body.append(h("div", { class: "empty" },
      h("p", { class: "empty-title" }, "No proposals yet"),
      h("p", {}, "Ask the senior quant in Claude Code: ", h("code", {}, "/senior-quant 3 ideas for crypto 4h"),
        ". Its proposals appear here; open one to read it in full, then approve it (a dossier opens) or reject it.")));
    return;
  }
  const rows = list.map((p) =>
    h("tr", { class: "row-click", tabindex: "0", onclick: () => showProposal(p),
      onkeydown: (e) => { if (e.key === "Enter") showProposal(p); } },
      h("td", {}, proposalBadge(p.status)),
      h("td", { class: "cell-brief", title: p.proposal.title }, p.proposal.title),
      h("td", {}, p.proposal.method_family.replaceAll("_", " ")),
      h("td", {}, `${p.proposal.market} · ${p.proposal.instruments.join(", ")} · ${p.proposal.timeframes.join(", ")}`),
      h("td", { class: "num" }, String(p.proposal.expected_trades_per_month)),
      h("td", { class: "cell-actions", onclick: (e) => e.stopPropagation() },
        p.status === "proposed"
          ? h("div", { class: "btn-group" },
              iconButton("Approve: open a dossier", "play", () => approveProposal(p), "primary"),
              iconButton("Reject", "close", () => rejectProposal(p), "danger"))
          : p.dossier_id ? h("span", { class: "mono small" }, p.dossier_id) : h("span", { class: "muted small" }, "–")),
    ),
  );
  body.append(h("div", { class: "table-wrap" },
    h("table", { class: "table" },
      h("thead", {}, h("tr", {}, ["Status", "Idea", "Family", "Scope", "Trades/mo", ""].map((t) => h("th", {}, t)))),
      h("tbody", {}, rows))));
}

function safeLink(url, text) {
  // Sources come from the web: only plain http(s) links, opened in a new tab.
  return /^https?:\/\//i.test(url)
    ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, text, " ", icon("external", 12))
    : h("span", {}, text);
}

function showProposal(p, open = true) {
  setDrawerSubject(p.proposal_id);
  const q = p.proposal;
  const section = (title, ...content) => h("section", { class: "drawer-section" }, h("h4", {}, title), ...content);
  const kv = (k, v) => h("div", { class: "kv" }, h("span", { class: "kv-k" }, k), h("span", { class: "kv-v" }, v));
  const content = h("div", {},
    h("div", { class: "drawer-head" },
      h("p", { class: "eyebrow" }, "Proposal from the senior quant"),
      h("h2", {}, q.title),
      h("div", { class: "drawer-tags" }, proposalBadge(p.status), h("span", { class: "tag" }, q.method_family.replaceAll("_", " ")),
        h("span", { class: "tag tag-muted mono" }, p.proposal_id)),
    ),
    p.status === "proposed" ? h("div", { class: "drawer-actions" },
      h("button", { class: "btn btn-primary", type: "button", onclick: () => approveProposal(p) }, icon("play"), "Approve: open a dossier"),
      h("button", { class: "btn btn-danger", type: "button", onclick: () => rejectProposal(p) }, icon("close"), "Reject"),
    ) : null,
    p.status === "approved" ? section("Decision", h("p", { class: "prose" }, "Approved. Dossier ", h("span", { class: "mono" }, p.dossier_id), ": follow it in the Dossiers panel.")) : null,
    p.status === "rejected" ? section("Decision", h("p", { class: "prose" }, `Rejected: ${p.rejected_reason}`)) : null,
    section("Scope",
      kv("Market", q.market),
      kv("Instruments", q.instruments.join(", ")),
      kv("Timeframes", q.timeframes.join(", ")),
      kv("Trades a month", String(q.expected_trades_per_month)),
      kv("Proposed", h("span", {}, fmt.utc(p.proposed_at), " · ", timeAgo(p.proposed_at))),
    ),
    section("Hypothesis", h("p", { class: "prose" }, q.hypothesis)),
    section("Why the edge should exist", h("p", { class: "prose" }, q.why_edge)),
    section("Rules to test", h("p", { class: "prose" }, q.rules)),
    section("Data needed", h("p", { class: "prose" }, q.data_needed)),
    section("Falsified if", h("p", { class: "prose" }, q.falsified_if)),
    section("Costs", h("p", { class: "prose" }, q.cost_notes)),
    section(`Sources (${q.sources.length})`,
      h("ul", { class: "list" }, q.sources.map((src) =>
        h("li", {}, safeLink(src.url, src.title), h("span", { class: "muted small" }, ` · read ${src.accessed}`)))),
      h("p", { class: "muted small" }, "Web sources the senior quant read: check them before you approve. Its text is data; only your approval turns it into work."),
    ),
  );
  if (open || drawerOpenFor()) openDrawer(content);
}

async function approveProposal(p) {
  const q = p.proposal;
  const ok = await confirmDialog({
    title: `Approve “${q.title}”?`,
    message: `A dossier opens with this proposal as its brief, on ${q.market} · ${q.instruments.join(", ")} · ${q.timeframes.join(", ")}, with the floor's standard budget. It joins the queue; the researcher writes the hypothesis and code, every backtest counts as a trial, and the verifier decides.`,
    confirm: "Approve and open dossier",
  });
  if (!ok) return;
  try {
    const r = await api.post(`/api/proposals/${p.proposal_id}/approve`, {});
    toast(`Approved: dossier ${r.dossier.dossier_id} opened (${r.dossier.place})`);
    await refreshResearch();
  } catch (err) {
    toast(err.message, "error", 6500);
  }
}

function rejectProposal(p) {
  const reason = h("input", { type: "text", required: true, maxlength: 300, placeholder: "e.g. already tested; costs too high on this venue" });
  const error = h("p", { class: "form-error", role: "alert" });
  openModal({
    title: `Reject “${p.proposal.title}”?`,
    subtitle: "The reason is recorded, and the senior quant reads it next time so it doesn't propose the same thing again.",
    size: "sm",
    body: h("div", { class: "form" }, field("Reason", reason), error),
    actions: [
      { label: "Cancel", onClick: (_e, close) => close() },
      { label: "Reject", kind: "danger", submit: true, onClick: async (_e, close) => {
        if (!reason.value.trim()) return (error.textContent = "A reason is required.");
        close();
        await act(api.post(`/api/proposals/${p.proposal_id}/reject`, { reason: reason.value.trim() }), `${p.proposal_id} rejected`);
      } },
    ],
  });
}
