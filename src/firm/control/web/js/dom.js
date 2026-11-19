// DOM helpers. Data is only ever inserted as text (never innerHTML), so a
// brief or a ledger reason can't inject markup.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const c of [children].flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export function clear(el) {
  el.replaceChildren();
  return el;
}

const SVG = "http://www.w3.org/2000/svg";
const ICONS = {
  plus: "M12 5v14M5 12h14",
  pause: "M9 6v12M15 6v12",
  play: "M8 5l11 7-11 7z",
  redirect: "M4 12h12M12 6l6 6-6 6",
  close: "M6 6l12 12M18 6L6 18",
  edit: "M4 20h4L19 9l-4-4L4 16zM14 6l4 4",
  copy: "M9 9h10v10H9zM5 15V5h10",
  refresh: "M20 11a8 8 0 10-2.3 5.7M20 5v6h-6",
  sun: "M12 7a5 5 0 100 10 5 5 0 000-10zM12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4",
  moon: "M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z",
  layout: "M3 3h8v8H3zM13 3h8v5h-8zM13 10h8v11h-8zM3 13h8v8H3z",
  reset: "M3 12a9 9 0 109-9 9 9 0 00-6.4 2.6L3 8M3 3v5h5",
  chevron: "M9 6l6 6-6 6",
  search: "M11 4a7 7 0 100 14 7 7 0 000-14zM21 21l-4.3-4.3",
  brief: "M5 4h14v16H5zM8 8h8M8 12h8M8 16h5",
  shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z",
  bolt: "M13 2L4 14h7l-1 8 9-12h-7z",
  info: "M12 8h.01M11 12h1v5h1M12 3a9 9 0 100 18 9 9 0 000-18z",
  external: "M14 4h6v6M20 4l-9 9M18 14v6H4V6h6",
};

export function icon(name, size = 16) {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", "icon");
  const path = document.createElementNS(SVG, "path");
  path.setAttribute("d", ICONS[name] || ICONS.info);
  svg.append(path);
  return svg;
}

const numberFmt = new Map();
function nf(digits) {
  if (!numberFmt.has(digits)) {
    numberFmt.set(
      digits,
      new Intl.NumberFormat("en-GB", { minimumFractionDigits: 0, maximumFractionDigits: digits }),
    );
  }
  return numberFmt.get(digits);
}

export const fmt = {
  num(v, digits = 2) {
    if (v === null || v === undefined || v === "") return "–";
    const n = Number(v);
    return Number.isFinite(n) ? nf(digits).format(n) : String(v);
  },
  int(v) {
    return fmt.num(v, 0);
  },
  compact(v) {
    const n = Number(v);
    if (!Number.isFinite(n)) return "–";
    return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(n);
  },
  money(v, currency) {
    if (v === null || v === undefined) return "–";
    const n = Number(v);
    const sign = currency === "GBP" ? "£" : currency === "USD" ? "$" : currency === "EUR" ? "€" : "";
    const body = nf(n !== 0 && Math.abs(n) < 1 ? 6 : 2).format(Math.abs(n));
    const text = sign ? `${sign}${body}` : `${body} ${currency || ""}`.trim();
    return n < 0 ? `−${text}` : text;
  },
  utc(iso) {
    if (!iso) return "–";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toISOString().replace("T", " ").slice(0, 19) + " UTC";
  },
  time(iso) {
    if (!iso) return "–";
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? String(iso) : d.toISOString().slice(11, 19);
  },
  ago(iso) {
    if (!iso) return "–";
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (!Number.isFinite(s)) return String(iso);
    if (s < 0) return "just now";
    if (s < 60) return `${Math.floor(s)}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    return `${Math.floor(s / 86400)} d ago`;
  },
  short(id, n = 12) {
    if (!id) return "–";
    return id.length > n ? `${id.slice(0, n)}…` : id;
  },
  pct(used, total) {
    if (!total) return 0;
    return Math.max(0, Math.min(100, (100 * Number(used)) / Number(total)));
  },
};

// Elements that show a relative time ("3 min ago") and keep it current.
export function timeAgo(iso) {
  const el = h("time", { datetime: iso || "", title: fmt.utc(iso), class: "ago" }, fmt.ago(iso));
  el.dataset.ago = iso || "";
  return el;
}

export function refreshAges(root = document) {
  for (const el of root.querySelectorAll("time.ago[data-ago]")) {
    if (el.dataset.ago) el.textContent = fmt.ago(el.dataset.ago);
  }
}

export function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = h("textarea", { class: "sr-only" }, text);
    document.body.append(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}
