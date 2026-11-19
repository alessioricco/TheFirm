// Paper trading: runs, accounts, P&L, the price chart with fills, feeds,
// risk heartbeat and halts, live activity. Read-only: it can't place, size
// or cancel an order. The kill switch comes in the next release (M3b).
import { api } from "./api.js";
import { h, icon, clear, fmt, timeAgo, copyText } from "./dom.js";
import { mountGrid, panelAction } from "./layout.js";
import { toast } from "./ui.js";
import { activityFeed } from "./activity.js";
import { killBlock, onKillChange, refreshKill } from "./killswitch.js";

const TRADING_EVENTS = new Set([
  "fill", "order_proposal", "order_intent", "order_update", "risk_decision", "risk_halt",
  "feed_status", "market_status", "notification",
]);

const DEFAULTS = [
  { id: "run", title: "Paper run", subtitle: "from the ledger", x: 0, y: 0, w: 4, h: 4 },
  { id: "account", title: "Account", subtitle: "balances now", x: 4, y: 0, w: 4, h: 4 },
  { id: "pnl", title: "Trades & P&L", subtitle: "rebuilt like the journal", x: 8, y: 0, w: 4, h: 4 },
  { id: "chart", title: "Price", subtitle: "", x: 0, y: 4, w: 8, h: 8, minW: 4, minH: 4 },
  { id: "feeds", title: "Feeds", subtitle: "live market data", x: 8, y: 4, w: 4, h: 3 },
  { id: "risk", title: "Risk", subtitle: "kill switch, heartbeat, halts", x: 8, y: 7, w: 4, h: 6 },
  { id: "activity", title: "Trading activity", subtitle: "live from the ledger", x: 0, y: 13, w: 12, h: 4 },
];

let panels = null;
let feed = null;
let status = null;
let chart = null;
let selected = null; // "market|venue" shown in the chart

export async function mountTrading() {
  const entry = await mountGrid("trading", document.getElementById("grid-trading"), DEFAULTS);
  panels = entry.panels;
  panels.get("chart").actions.append(panelAction("Reload", "refresh", () => loadChart(true)));
  document.getElementById("trading-actions").append(
    h("button", { class: "btn btn-ghost", type: "button", onclick: () => refreshTrading(true) }, icon("refresh"), "Refresh"),
  );
  feed = activityFeed(panels.get("activity").body, {
    filter: (e) => TRADING_EVENTS.has(e.type),
    empty: "No trading activity in this ledger yet.",
  });
  onKillChange(() => status && renderRisk());
  await feed.load();
  await refreshTrading();
}

export function onTradingEvent(e) {
  if (feed && TRADING_EVENTS.has(e.type)) feed.push(e);
}

export function themeChanged() {
  if (chart) {
    chart.api.applyOptions(chartTheme());
    chart.series.applyOptions(seriesTheme());
  }
}

export async function refreshTrading(withChart = false) {
  try {
    status = await api.get("/api/paper");
  } catch (err) {
    for (const p of panels.values()) {
      if (p.id !== "activity") clear(p.body).append(h("div", { class: "empty empty-error" }, err.message));
    }
    return;
  }
  renderRun();
  renderAccount();
  renderPnl();
  renderFeeds();
  renderRisk();
  refreshKill();
  if (!selected && status.runs.length) selected = key(status.runs[status.runs.length - 1]);
  if (withChart || !chart) await loadChart();
}

const key = (r) => `${r.market}|${r.venue}`;

function noRun(body, what) {
  const cmd = "uv run firm paper run --strategy PATH:CLASS --verdict v-... --market crypto --venue coinbase --instrument BTC-USD --tf 1d";
  body.append(
    h("div", { class: "empty" },
      h("p", { class: "empty-title" }, what),
      h("p", {}, "Start a paper run from a terminal (after a passing verification):"),
      codeLine(cmd),
    ),
  );
}

export function codeLine(cmd) {
  return h("div", { class: "code-line" },
    h("code", {}, cmd),
    h("button", { class: "btn btn-ghost btn-icon btn-sm", type: "button", title: "Copy", "aria-label": "Copy command",
      onclick: async () => toast((await copyText(cmd)) ? "Copied" : "Couldn't copy", "ok", 1600) }, icon("copy", 14)),
  );
}

function renderRun() {
  const body = clear(panels.get("run").body);
  if (!status.runs.length) return noRun(body, "No paper run yet");
  for (const r of status.runs.slice().reverse()) {
    const hb = status.heartbeat[r.market];
    body.append(
      h("div", { class: "run" },
        h("div", { class: "run-head" },
          h("span", { class: `status-pill ${r.running ? "is-live" : "is-stopped"}` },
            h("span", { class: "dot" }), r.running ? "Running" : r.failure ? "Stopped by a failure" : "Stopped"),
          h("span", { class: "run-name" }, `${r.market} · ${r.venue}`),
        ),
        h("dl", { class: "dl" },
          h("dt", {}, "Verdict"), h("dd", { class: "mono", title: r.verdict_id }, fmt.short(r.verdict_id, 24)),
          h("dt", {}, "Started"), h("dd", {}, timeAgo(r.started_at)),
          r.stopped_at ? [h("dt", {}, "Stopped"), h("dd", {}, timeAgo(r.stopped_at))] : null,
          h("dt", {}, "Risk heartbeat"),
          h("dd", {}, hb ? h("span", { class: hb.state === "ok" ? "good" : "bad" }, hb.state === "ok" ? "OK" : "Lost", " · ", timeAgo(hb.at)) : h("span", { class: "muted" }, "–")),
        ),
        r.running ? h("p", { class: "muted small" }, "“Running” means the last start has no recorded stop.") : null,
        r.failure ? h("p", { class: "bad small" }, r.failure) : null,
      ),
    );
  }
}

function renderAccount() {
  const body = clear(panels.get("account").body);
  if (!status.accounts.length) return noRun(body, "No paper account yet");
  for (const a of status.accounts) {
    const balances = a.balances ? Object.entries(a.balances) : [];
    body.append(
      h("div", { class: "account" },
        h("p", { class: "muted small" }, `${a.market} · ${a.venue} · opened ${fmt.utc(a.opened_at)}`),
        balances.length
          ? h("div", { class: "balances" }, balances.map(([c, v]) =>
              h("div", { class: "balance" }, h("span", { class: "balance-cur" }, c), h("span", { class: "balance-val" }, fmt.money(v, c)))))
          : h("p", { class: "muted" }, "Balances unknown (an instrument's currencies aren't on record)."),
        h("div", { class: "account-foot" },
          h("span", {}, h("strong", {}, fmt.int(a.fills)), " fills"),
          a.last_fill
            ? h("span", { class: "muted" }, `last: ${a.last_fill.side} ${a.last_fill.quantity} ${a.last_fill.instrument} @ ${fmt.num(a.last_fill.price, 2)}`)
            : h("span", { class: "muted" }, "no fill yet"),
        ),
      ),
    );
  }
}

function renderPnl() {
  const body = clear(panels.get("pnl").body);
  const t = status.trades;
  if (t.error) {
    body.append(h("div", { class: "empty empty-error" }, `Trades can't be rebuilt from this ledger: ${t.error}`));
    return;
  }
  const pnl = Object.entries(t.net_pnl);
  body.append(
    h("div", { class: "kpis kpis-2" },
      h("div", { class: "kpi" }, h("span", { class: "kpi-label" }, "Closed trades"), h("span", { class: "kpi-value" }, fmt.int(t.closed))),
      h("div", { class: "kpi" }, h("span", { class: "kpi-label" }, "Open"), h("span", { class: "kpi-value" }, fmt.int(t.open))),
    ),
    h("div", { class: "pnl" },
      h("span", { class: "kpi-label" }, "Net P&L, closed trades"),
      pnl.length
        ? pnl.map(([c, v]) => h("span", { class: `pnl-value ${Number(v) >= 0 ? "good" : "bad"}` }, fmt.money(v, c)))
        : h("span", { class: "pnl-value muted" }, "–"),
    ),
    h("p", { class: "muted small" }, "Details per trade: ", h("code", {}, "uv run firm journal build && uv run firm journal show")),
  );
}

function renderFeeds() {
  const body = clear(panels.get("feeds").body);
  if (!status.feeds.length) {
    body.append(h("div", { class: "empty" }, "No live feed has reported yet."));
    return;
  }
  const tone = { fresh: "good", connected: "accent", stale: "warn", disconnected: "bad" };
  body.append(
    h("ul", { class: "feeds" },
      status.feeds.map((f) =>
        h("li", { class: "feed-item" },
          h("span", { class: `dot dot-${tone[f.status] || "muted"}`, title: f.status }),
          h("span", { class: "feed-name" }, h("strong", {}, f.symbol), h("span", { class: "muted" }, ` ${f.provider}`)),
          h("span", { class: `feed-state ${tone[f.status] || ""}` }, f.status),
          timeAgo(f.at),
        ),
      ),
    ),
  );
}

function renderRisk() {
  const body = clear(panels.get("risk").body);
  const hbs = Object.entries(status.heartbeat);
  body.append(
    h("div", { class: "risk-block killswitch" },
      h("h5", {}, icon("shield", 14), " Kill switch"),
      killBlock(status.runs.map((r) => r.market)),
    ),
    h("div", { class: "risk-block" },
      h("h5", {}, "Heartbeat"),
      hbs.length
        ? h("ul", { class: "plain" }, hbs.map(([m, hb]) =>
            h("li", {}, h("span", { class: `dot dot-${hb.state === "ok" ? "good" : "bad"}` }), ` ${m}: `,
              h("strong", { class: hb.state === "ok" ? "good" : "bad" }, hb.state === "ok" ? "OK" : "Lost — no new orders"), " · ", timeAgo(hb.at))))
        : h("p", { class: "muted small" }, "No heartbeat change recorded (it's only logged when lost or restored)."),
    ),
    h("div", { class: "risk-block" },
      h("h5", {}, `Halts (${status.halts.length})`),
      status.halts.length
        ? h("ul", { class: "plain" }, status.halts.slice().reverse().map((x) =>
            h("li", {}, h("span", { class: "badge badge-bad" }, x.scope), ` ${x.market || "all markets"}: ${x.reason} · `, timeAgo(x.at))))
        : h("p", { class: "muted small" }, "No risk halt recorded."),
    ),
  );
}

// ------------------------------------------------------------------- chart
function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function chartTheme() {
  return {
    layout: {
      background: { type: "solid", color: css("--surface") },
      textColor: css("--muted"),
      fontFamily: css("--font"),
      fontSize: 12,
      attributionLogo: true,
    },
    grid: { vertLines: { color: css("--grid") }, horzLines: { color: css("--grid") } },
    rightPriceScale: { borderColor: css("--border") },
    timeScale: { borderColor: css("--border") },
    crosshair: { vertLine: { color: css("--muted") }, horzLine: { color: css("--muted") } },
  };
}

function seriesTheme() {
  return {
    upColor: css("--good"), downColor: css("--bad"), borderVisible: false,
    wickUpColor: css("--good"), wickDownColor: css("--bad"),
  };
}

async function loadChart(force = false) {
  const p = panels.get("chart");
  if (!status || !status.runs.length) {
    chart && chart.api.remove();
    chart = null;
    clear(p.body);
    p.subtitle.textContent = "";
    return noRun(p.body, "No run to chart");
  }
  const runs = status.runs;
  const [market, venue] = (selected || key(runs[runs.length - 1])).split("|");
  let data;
  try {
    data = await api.get(`/api/paper/chart?market=${encodeURIComponent(market)}&venue=${encodeURIComponent(venue)}`);
  } catch (err) {
    chart && chart.api.remove();
    chart = null;
    clear(p.body).append(h("div", { class: "empty empty-error" }, err.message));
    return;
  }
  if (chart) chart.api.remove(); // a fresh chart each time: data and theme in one place
  chart = null;
  const wrap = h("div", { class: "chart" });
  const legend = h("div", { class: "chart-legend" });
  const picker = runs.length > 1
    ? h("select", { class: "select-sm", onchange: (e) => { selected = e.target.value; loadChart(true); } },
        runs.map((r) => h("option", { value: key(r), selected: key(r) === `${market}|${venue}` ? true : null }, `${r.market} · ${r.venue}`)))
    : null;
  clear(p.body).append(h("div", { class: "chart-bar" }, legend, picker), wrap);
  const last = data.candles[data.candles.length - 1];
  p.subtitle.textContent = `${data.instrument} · ${data.timeframe} · ${data.venue}`;
  if (!data.candles.length) {
    wrap.append(h("div", { class: "empty" }, "The run's dataset has no bars for this instrument."));
    return;
  }
  const LC = window.LightweightCharts;
  const priceFormatter = (v) => fmt.num(v, Math.abs(v) >= 1000 ? 0 : Math.abs(v) >= 1 ? 2 : 6);
  const api_ = LC.createChart(wrap, { autoSize: true, ...chartTheme(), localization: { priceFormatter },
    timeScale: { ...chartTheme().timeScale, timeVisible: data.timeframe !== "1d" && data.timeframe !== "1w" } });
  const series = api_.addSeries(LC.CandlestickSeries, seriesTheme());
  series.setData(data.candles);
  const first = data.candles[0].time;
  const end = last.time;
  const step = data.candles.length > 1 ? data.candles[1].time - data.candles[0].time : 86400;
  const inRange = [];
  let after = 0;
  for (const f of data.fills) {
    const t = Math.floor(new Date(f.time).getTime() / 1000);
    if (t < first || t > end + step) { if (t > end) after += 1; continue; }
    const barTime = first + Math.floor((t - first) / step) * step;
    const buy = f.side === "buy";
    inRange.push({ time: barTime, position: buy ? "belowBar" : "aboveBar", color: buy ? css("--good") : css("--bad"),
      shape: buy ? "arrowUp" : "arrowDown", text: buy ? "Buy" : "Sell" });
  }
  if (inRange.length) LC.createSeriesMarkers(series, inRange.sort((a, b) => a.time - b.time));
  api_.timeScale().fitContent();
  chart = { api: api_, series };
  legend.append(
    h("span", { class: "legend-main" }, `${data.instrument}`, h("span", { class: "muted" }, ` ${data.timeframe}`)),
    h("span", { class: "legend-price" }, fmt.num(last.close, 2)),
    h("span", { class: "muted small", title: "The dashboard reads the data store, never the venue: bars end where the verified dataset ends." },
      `history to ${fmt.utc(new Date((end + step) * 1000).toISOString()).slice(0, 10)} · ${data.fills.length} fill${data.fills.length === 1 ? "" : "s"}`
      + (after ? ` (${after} after the history ends)` : "")),
  );
}
