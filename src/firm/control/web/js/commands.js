// Every `firm` command, generated from the CLI's own parser (/api/commands),
// so this reference can't drift from the real commands. Copy an example to
// run it in a terminal; the ones the dashboard can do itself are marked.
import { api } from "./api.js";
import { h, icon, clear } from "./dom.js";
import { toast } from "./ui.js";
import { copyText } from "./dom.js";

const IN_DASHBOARD = {
  "firm research start": "Research → New dossier",
  "firm research list": "Research view",
  "firm research show": "Research → a dossier",
  "firm research pause": "Research → Pause",
  "firm research resume": "Research → Resume",
  "firm research redirect": "Research → Redirect",
  "firm research close": "Research → Close",
  "firm research brief": "Research → Floor brief",
  "firm paper status": "Trading view",
  "firm kill": "Trading → Risk → Kill",
  "firm resume": "Trading → Risk → Resume",
  "firm ledger show": "Ledger view",
};

const GROUP_INFO = {
  data: "The data store: synthetic data, dataset versions, instrument spellings.",
  verify: "Verify a strategy against an exact dataset version.",
  state: "The verifier's state: trials, out-of-sample openings, reports.",
  ledger: "The append-only, hash-chained ledger.",
  journal: "The trade journal, rebuilt from the ledger.",
  feed: "Live market data streams.",
  paper: "Paper trading: run, replay, status.",
  bus: "The message bus between desks (NATS).",
  notify: "Alerts to the owner on Telegram.",
  research: "The research floor: dossiers, briefs, budgets.",
  dashboard: "This dashboard.",
  kill: "The owner's kill switch: no new buying on a market, or everywhere.",
  resume: "Release a kill switch.",
  version: "The version.",
};

let all = [];
// The order you'd use them in: data, then verify, then paper trading, then the rest.
const GROUP_ORDER = ["data", "verify", "state", "paper", "kill", "resume", "feed", "bus", "notify", "ledger", "journal", "research", "dashboard", "version"];

export async function mountCommands() {
  const root = document.getElementById("commands-root");
  all = await api.get("/api/commands");
  const list = h("div", { class: "commands" });
  const box = h("input", { type: "search", class: "input-sm", placeholder: "Search commands and options…", "aria-label": "Search commands",
    oninput: (e) => render(list, e.target.value.toLowerCase()) });
  clear(root).append(
    h("div", { class: "toolbar card card-flat" },
      h("div", { class: "search" }, icon("search", 14), box),
      h("span", { class: "spacer" }),
      h("span", { class: "muted small" }, "Full reference with examples: "),
      h("code", {}, "docs/COMMANDS.md")),
    list,
  );
  render(list, "");
}

function example(c) {
  const parts = ["uv run", c.command];
  for (const o of c.options) {
    if (!o.required) continue;
    const flag = o.flags.find((f) => f.startsWith("--")) || null;
    const name = (flag || o.flags[0]).replace(/^--/, "").toUpperCase().replace(/-/g, "_");
    parts.push(flag ? `${flag} <${name}>` : `<${name}>`);
  }
  return parts.join(" ");
}

function render(list, q) {
  const groups = new Map();
  for (const c of all) {
    const text = `${c.command} ${c.help} ${c.options.map((o) => `${o.flags.join(" ")} ${o.help}`).join(" ")}`.toLowerCase();
    if (q && !text.includes(q)) continue;
    const g = c.command.split(" ")[1];
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(c);
  }
  const ordered = [...groups].sort(
    ([a], [b]) => (GROUP_ORDER.indexOf(a) + 100) % 100 - (GROUP_ORDER.indexOf(b) + 100) % 100,
  );
  clear(list);
  if (!groups.size) {
    list.append(h("div", { class: "empty" }, "No command matches."));
    return;
  }
  for (const [g, cmds] of ordered) {
    list.append(
      h("section", { class: "cmd-group" },
        h("div", { class: "cmd-group-head" }, h("h2", {}, `firm ${g}`), h("p", { class: "muted" }, GROUP_INFO[g] || "")),
        h("div", { class: "cmd-grid" }, cmds.map(card)),
      ),
    );
  }
}

function card(c) {
  const ex = example(c);
  const here = IN_DASHBOARD[c.command];
  return h("article", { class: "card cmd" },
    h("header", { class: "cmd-head" },
      h("h3", { class: "mono" }, c.command),
      here ? h("span", { class: "tag tag-accent", title: "You can do this in the dashboard" }, here) : null),
    c.help ? h("p", { class: "cmd-help" }, c.help) : null,
    h("div", { class: "code-line" }, h("code", {}, ex),
      h("button", { class: "btn btn-ghost btn-icon btn-sm", type: "button", title: "Copy", "aria-label": `Copy ${c.command}`,
        onclick: async () => toast((await copyText(ex)) ? "Copied" : "Couldn't copy", "ok", 1600) }, icon("copy", 14))),
    c.options.length
      ? h("details", { class: "cmd-options" },
          h("summary", {}, `${c.options.length} option${c.options.length === 1 ? "" : "s"}`),
          h("table", { class: "table table-compact" },
            h("tbody", {}, c.options.map((o) =>
              h("tr", {},
                h("td", { class: "mono nowrap" }, o.flags.join(", "), o.required ? h("span", { class: "req", title: "required" }, " *") : null),
                h("td", {}, o.help || h("span", { class: "muted" }, "–"),
                  o.default ? h("span", { class: "muted small" }, ` (default ${o.default})`) : null))))))
      : null,
  );
}
