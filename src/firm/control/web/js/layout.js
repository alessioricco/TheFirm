// Panels on a GridStack grid, one grid per named view, layouts saved on the
// server (`/api/layouts/<view>`). Locked by default; "Edit layout" unlocks
// moving and resizing, so a click on a panel never moves it by accident.
import { api } from "./api.js";
import { h, icon, debounce } from "./dom.js";

const grids = new Map(); // view -> { grid, panels, defaults }

export function panel(def) {
  // def: { id, title, subtitle, x, y, w, h, minW, minH }
  const body = h("div", { class: "panel-body" });
  const actions = h("div", { class: "panel-actions" });
  const subtitle = h("span", { class: "panel-sub" }, def.subtitle || "");
  const content = h(
    "div",
    { class: "grid-stack-item-content panel" },
    h("div", { class: "panel-head" },
      h("div", { class: "panel-titles" }, h("h3", {}, def.title), subtitle),
      actions),
    body,
  );
  const item = h("div", {
    class: "grid-stack-item",
    "gs-id": def.id,
    "gs-x": def.x, "gs-y": def.y, "gs-w": def.w, "gs-h": def.h,
    "gs-min-w": def.minW || 2, "gs-min-h": def.minH || 2,
  }, content);
  return { id: def.id, item, body, actions, subtitle, def };
}

export async function mountGrid(view, el, defs) {
  const panels = defs.map(panel);
  let saved = null;
  try {
    saved = (await api.get(`/api/layouts/${view}`)).items;
  } catch {
    saved = null;
  }
  const byId = new Map((saved || []).map((s) => [s.id, s]));
  for (const p of panels) {
    const s = byId.get(p.id);
    if (s) {
      p.item.setAttribute("gs-x", s.x);
      p.item.setAttribute("gs-y", s.y);
      p.item.setAttribute("gs-w", s.w);
      p.item.setAttribute("gs-h", s.h);
    }
    el.append(p.item);
  }
  const grid = window.GridStack.init(
    {
      column: 12,
      cellHeight: 72,
      margin: 8,
      float: false,
      animate: true,
      staticGrid: true,
      handle: ".panel-head",
      columnOpts: { breakpointForWindow: true, breakpoints: [{ w: 980, c: 1 }] },
    },
    el,
  );
  const save = debounce(async () => {
    const items = grid.save(false).map(({ id, x, y, w, h: ht }) => ({ id, x, y, w, h: ht }));
    try {
      await api.put(`/api/layouts/${view}`, { items });
    } catch {
      /* a layout that didn't save is not worth an alert */
    }
  }, 600);
  grid.on("change", () => {
    if (!grid.opts.staticGrid) save();
  });
  const entry = { grid, panels: new Map(panels.map((p) => [p.id, p])), defs, save };
  grids.set(view, entry);
  return entry;
}

export function setEditing(on) {
  for (const { grid } of grids.values()) grid.setStatic(!on);
  document.body.classList.toggle("editing", on);
}

export async function resetLayout(view) {
  const entry = grids.get(view);
  if (!entry) return;
  entry.grid.batchUpdate();
  for (const d of entry.defs) {
    const p = entry.panels.get(d.id);
    entry.grid.update(p.item, { x: d.x, y: d.y, w: d.w, h: d.h });
  }
  entry.grid.batchUpdate(false);
  const items = entry.defs.map(({ id, x, y, w, h: ht }) => ({ id, x, y, w, h: ht }));
  await api.put(`/api/layouts/${view}`, { items });
}

export function panelAction(label, iconName, onClick, kind = "ghost") {
  return h(
    "button",
    { class: `btn btn-${kind} btn-xs`, type: "button", onclick: onClick, title: label },
    icon(iconName, 14),
    h("span", {}, label),
  );
}
