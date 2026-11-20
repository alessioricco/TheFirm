// The Data view: every bar series in the store, how recent it is, and
// downloads (update the downloaded series, or fetch a new one). Downloads
// run on the server in the background, one at a time; this page polls the
// job and shows its output. Same code as `firm data update` / `fetch`.
import { api } from "./api.js";
import { h, icon, clear, fmt } from "./dom.js";
import { openModal, confirmDialog, toast, field } from "./ui.js";

const state = { series: [], providers: [], jobs: [], running: null, shown: null, poll: null };
let els = null;

export async function mountData() {
  const root = document.getElementById("data-root");
  els = {
    kpis: h("div", { class: "kpis" }),
    tbody: h("tbody"),
    count: h("span", { class: "muted small" }),
    updateAll: h("button", { class: "btn btn-primary btn-sm", type: "button", onclick: () => updateDialog(null) },
      icon("refresh", 14), "Update all"),
    job: h("div", { class: "card job-card", hidden: true }),
  };
  clear(root).append(
    els.kpis,
    h("div", { class: "card card-flat data-card" },
      h("div", { class: "toolbar" },
        els.updateAll,
        h("button", { class: "btn btn-ghost btn-sm", type: "button", onclick: fetchDialog }, icon("plus", 14), "Download a series"),
        h("span", { class: "spacer" }),
        els.count,
        h("button", { class: "btn btn-ghost btn-icon", type: "button", title: "Reload", "aria-label": "Reload",
          onclick: () => refreshData() }, icon("reset", 14))),
      h("div", { class: "table-wrap table-tall" },
        h("table", { class: "table table-data" },
          h("thead", {}, h("tr", {}, ["Series", "Instrument", "Captured", "Captures", "From", "Last close (UTC)", "Age", "Next update", ""]
            .map((t) => h("th", {}, t)))),
          els.tbody))),
    els.job,
  );
  await refreshData();
}

export async function refreshData() {
  if (!els) return;
  const data = await api.get("/api/data");
  Object.assign(state, { series: data.series, providers: data.providers, jobs: data.jobs, running: data.running });
  if (data.running) watch(data.running);
  else if (!state.shown && data.jobs.length) showJob(data.jobs[0].job_id);
  render();
}

function sourceBadge(methods) {
  const m = methods.join("/");
  const cls = m === "api" ? "stage-passed" : m === "synthetic" ? "stage-closed" : "stage-submitted";
  const label = m === "api" ? "downloaded" : m === "legacy_import" ? "imported" : m;
  return h("span", { class: `badge ${cls}`, title: m }, label);
}

function render() {
  const s = state.series;
  const api_ = s.filter((x) => x.updatable);
  const due = api_.filter((x) => x.requests > 0);
  clear(els.kpis).append(
    kpi("Series", fmt.int(s.length)),
    kpi("Downloaded", fmt.int(api_.length)),
    kpi("Need an update", fmt.int(due.length), due.length ? "kpi-warn" : "kpi-good"),
    kpi("Download", state.running ? "running" : "idle", state.running ? "kpi-accent" : ""),
  );
  els.count.textContent = `${fmt.int(s.length)} series`;
  els.updateAll.disabled = !!state.running || !api_.length;
  clear(els.tbody);
  if (!s.length) {
    els.tbody.append(h("tr", {}, h("td", { colspan: 9, class: "empty" },
      "The store has no bar series yet. Use “Download a series” to fetch one.")));
    return;
  }
  for (const x of s) {
    const canUpdate = x.updatable && x.requests > 0 && !state.running;
    els.tbody.append(h("tr", {},
      h("td", { class: "mono nowrap" }, x.series),
      h("td", { class: "mono nowrap" }, x.instrument ? `${x.venue}:${x.instrument}` : h("span", { class: "muted" }, "no record")),
      h("td", {}, sourceBadge(x.methods)),
      h("td", { class: "num" }, fmt.int(x.captures)),
      h("td", { class: "mono nowrap" }, x.first_open ? x.first_open.slice(0, 10) : "–"),
      h("td", { class: "mono nowrap" }, x.last_close ? fmt.utc(x.last_close).replace(" UTC", "").slice(0, 16) : "–"),
      h("td", { class: "nowrap" }, x.age),
      h("td", { class: "cell-reason small" }, x.plan),
      h("td", { class: "nowrap" }, x.updatable
        ? h("button", { class: "btn btn-ghost btn-xs", type: "button", disabled: canUpdate ? null : true,
          title: canUpdate ? "Fetch the new bars of this series" : x.plan, onclick: () => updateDialog(x) },
          icon("refresh", 13), h("span", {}, "Update"))
        : null),
    ));
  }
}

function kpi(label, value, cls = "") {
  return h("div", { class: `kpi ${cls}` }, h("span", { class: "kpi-label" }, label), h("span", { class: "kpi-value" }, value));
}

// --------------------------------------------------------------- update
async function updateDialog(one) {
  const todo = one ? [one] : state.series.filter((x) => x.updatable && x.requests > 0);
  if (!todo.length) return toast("Every downloaded series is up to date.");
  const requests = todo.reduce((n, x) => n + x.requests, 0);
  const ok = await confirmDialog({
    title: one ? `Update ${one.series}` : "Update all downloaded series",
    message: `${todo.length} series, about ${requests} request(s) to public market-data endpoints. ` +
      "Only the new bars are fetched (plus the last two again, to catch revisions); each series becomes a new dataset version. " +
      "Synthetic and imported series are left alone.",
    confirm: "Download",
  });
  if (!ok) return;
  const body = one ? { provider: one.provider, symbol: one.symbol, tf: one.tf } : {};
  try {
    const job = await api.post("/api/data/update", body);
    toast("Download started");
    watch(job.job_id);
    await refreshData();
  } catch (err) {
    toast(err.message, "error", 7000);
  }
}

// --------------------------------------------------------------- fetch
function fetchDialog() {
  const providers = state.providers;
  const provider = h("select", {}, providers.map((p) => h("option", { value: p.name }, p.name)));
  const note = h("p", { class: "field-hint" });
  const tf = h("select", {});
  const symbol = h("input", { type: "text", placeholder: "as the provider spells it: BTCUSDT, BTC-USD, XBTUSD, NVDA", autocomplete: "off", required: true });
  const instrument = h("input", { type: "text", placeholder: "firm ID: BTC-USDT, BTC-USD, NVDA.XNAS", autocomplete: "off", required: true });
  const assetClass = h("select", {}, h("option", { value: "crypto" }, "crypto"), h("option", { value: "equity" }, "equity (shares)"));
  const venue = h("input", { type: "text", placeholder: "default: the provider; shares: e.g. xnas", autocomplete: "off" });
  const quote = h("input", { type: "text", placeholder: "USD", autocomplete: "off", maxlength: 5 });
  const since = h("input", { type: "date" });
  const until = h("input", { type: "date" });
  const listed = h("input", { type: "date" });
  const equityOnly = h("div", { class: "form-row", hidden: true }, field("Venue", venue), field("Quote currency", quote));
  const error = h("p", { class: "form-error", role: "alert" });

  const syncProvider = () => {
    const p = providers.find((x) => x.name === provider.value);
    note.textContent = p ? `${p.description}. ${p.history_note}` : "";
    const keep = tf.value || "1d";
    clear(tf).append(...(p ? p.timeframes : []).map((t) => h("option", { value: t, selected: t === keep ? true : null }, t)));
  };
  provider.addEventListener("change", syncProvider);
  assetClass.addEventListener("change", () => { equityOnly.hidden = assetClass.value !== "equity"; });
  syncProvider();

  openModal({
    title: "Download a series",
    subtitle: "Public market data, no keys. Every response is stored exactly as received, then normalised into a new dataset version.",
    size: "lg",
    body: h("div", { class: "form" },
      h("div", { class: "form-row" }, h("div", {}, field("Provider", provider), note), field("Timeframe", tf)),
      h("div", { class: "form-row" }, field("Provider symbol", symbol), field("Firm instrument", instrument)),
      h("div", { class: "form-row" }, field("Asset class", assetClass),
        field("Listing date", listed, "Needed the first time an instrument is recorded. If you don't know it, look it up: don't guess.")),
      equityOnly,
      h("div", { class: "form-row" }, field("From", since, "Empty: the provider's latest page only."), field("Until", until, "Empty: now.")),
      error,
    ),
    actions: [
      { label: "Cancel", onClick: (_e, close) => close() },
      { label: "Download", kind: "primary", submit: true, onClick: async (_e, close) => {
        const body = {
          provider: provider.value, tf: tf.value,
          symbol: symbol.value.trim(), instrument: instrument.value.trim().toUpperCase(),
          asset_class: assetClass.value,
          venue: assetClass.value === "equity" ? venue.value.trim() || null : null,
          quote: assetClass.value === "equity" ? quote.value.trim().toUpperCase() || null : null,
          since: since.value || null, until: until.value || null, listed_at: listed.value || null,
        };
        if (!body.symbol || !body.instrument) return (error.textContent = "The provider symbol and the firm instrument are required.");
        try {
          const job = await api.post("/api/data/fetch", body);
          close();
          toast("Download started");
          watch(job.job_id);
          await refreshData();
        } catch (err) {
          error.textContent = err.message;
        }
      } },
    ],
  });
}

// --------------------------------------------------------------- jobs
function watch(jobId) {
  state.shown = jobId;
  clearInterval(state.poll);
  const tick = async () => {
    let job;
    try { job = await api.get(`/api/data/jobs/${jobId}`); } catch { return; }
    renderJob(job);
    if (job.status !== "running") {
      clearInterval(state.poll);
      state.poll = null;
      toast(job.status === "done" ? `Download finished: ${job.summary}` : `Download stopped: ${job.summary}`,
        job.status === "done" ? "ok" : "error", 7000);
      await refreshData();
    }
  };
  tick();
  state.poll = setInterval(tick, 1000);
}

async function showJob(jobId) {
  state.shown = jobId;
  try { renderJob(await api.get(`/api/data/jobs/${jobId}`)); } catch { /* gone after a restart */ }
}

function renderJob(job) {
  const badge = job.status === "running" ? "stage-research" : job.status === "done" ? "stage-passed" : "stage-failed";
  const picker = state.jobs.length > 1
    ? h("select", { class: "select-sm", "aria-label": "Earlier downloads", onchange: (e) => showJob(e.target.value) },
      state.jobs.map((j) => h("option", { value: j.job_id, selected: j.job_id === job.job_id ? true : null },
        `${fmt.utc(j.started_at).slice(5, 16)} ${j.kind} (${j.status})`)))
    : null;
  const log = h("pre", { class: "job-log" },
    job.lines.map((l) => h("span", { class: `log-${l.level}` }, `${l.text}\n`)));
  els.job.hidden = false;
  clear(els.job).append(
    h("div", { class: "job-head" },
      h("h3", {}, job.status === "running" ? "Download in progress" : "Last download"),
      h("span", { class: `badge ${badge}` }, job.status),
      h("span", { class: "muted small" }, `${job.kind} · started ${fmt.utc(job.started_at)}`),
      h("span", { class: "spacer" }),
      picker),
    job.summary ? h("p", { class: "job-summary" }, job.summary) : null,
    log,
  );
  log.scrollTop = log.scrollHeight;
}
