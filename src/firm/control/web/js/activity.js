// A live list of ledger events, newest first (research and trading views).
import { api } from "./api.js";
import { h, clear, fmt } from "./dom.js";

export const TYPE_LABEL = {
  dossier: "Dossier", owner_command: "Owner", trial: "Trial", trial_alias: "Trial alias",
  verdict: "Verdict", model_call: "Model", oos_vault: "OOS vault", fill: "Fill",
  order_proposal: "Proposal", order_intent: "Order", order_update: "Order update",
  risk_decision: "Risk", risk_halt: "Halt", feed_status: "Feed", market_status: "Market",
  notification: "Alert", strategy_candidate: "Candidate", opportunity_note: "Note",
};

export function typeBadge(type) {
  return h("span", { class: `type type-${type}` }, TYPE_LABEL[type] || type);
}

export function activityFeed(root, { filter, empty, max = 200 }) {
  const list = h("ol", { class: "feed" });
  const placeholder = h("div", { class: "empty" }, empty);
  clear(root).append(placeholder, list);
  let count = 0;

  function row(e) {
    return h("li", { class: "feed-row" },
      h("time", { class: "feed-time mono", datetime: e.recorded_at, title: fmt.utc(e.recorded_at) }, fmt.time(e.recorded_at)),
      typeBadge(e.type),
      h("span", { class: "feed-text", title: e.reason }, e.reason),
      h("span", { class: "feed-seq mono" }, `#${e.sequence}`),
    );
  }

  function push(e, fresh = true) {
    if (!filter(e)) return;
    placeholder.hidden = true;
    const el = row(e);
    if (fresh) el.classList.add("flash");
    list.prepend(el);
    count += 1;
    while (count > max) {
      list.lastChild.remove();
      count -= 1;
    }
  }

  async function load() {
    try {
      const events = await api.get("/api/ledger?limit=400");
      for (const e of events) push(e, false);
    } catch {
      /* the panels show their own errors */
    }
    placeholder.hidden = count > 0;
  }

  return { push, load };
}
