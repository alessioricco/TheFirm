// Modals, confirmations, toasts and the side drawer.
import { h, icon, clear } from "./dom.js";

const modalRoot = () => document.getElementById("modal-root");
let lastFocus = null;

export function openModal({ title, subtitle, body, actions = [], size = "md", onClose }) {
  closeModal();
  lastFocus = document.activeElement;
  const close = () => {
    closeModal();
    onClose && onClose();
  };
  const footer = h(
    "div",
    { class: "modal-foot" },
    actions.map((a) =>
      h(
        "button",
        {
          class: `btn ${a.kind === "primary" ? "btn-primary" : a.kind === "danger" ? "btn-danger" : "btn-ghost"}`,
          type: a.submit ? "submit" : "button",
          onclick: a.submit ? null : (e) => a.onClick && a.onClick(e, close),
          "data-autofocus": a.autofocus ? "true" : null,
        },
        a.label,
      ),
    ),
  );
  const form = h(
    "form",
    {
      class: `modal modal-${size}`,
      role: "dialog",
      "aria-modal": "true",
      "aria-label": title,
      novalidate: true,
      onsubmit: (e) => {
        e.preventDefault();
        const submit = actions.find((a) => a.submit);
        submit && submit.onClick && submit.onClick(e, close);
      },
    },
    h(
      "div",
      { class: "modal-head" },
      h("div", {}, h("h2", {}, title), subtitle ? h("p", { class: "modal-sub" }, subtitle) : null),
      h("button", { class: "btn btn-ghost btn-icon", type: "button", "aria-label": "Close", onclick: close }, icon("close")),
    ),
    h("div", { class: "modal-body" }, body),
    footer,
  );
  const backdrop = h(
    "div",
    {
      class: "backdrop",
      onmousedown: (e) => {
        if (e.target === backdrop) close();
      },
    },
    form,
  );
  modalRoot().append(backdrop);
  document.body.classList.add("has-modal");
  requestAnimationFrame(() => {
    backdrop.classList.add("open");
    const first =
      form.querySelector("[data-autofocus]") ||
      form.querySelector("textarea, input:not([type=checkbox]), select") ||
      form.querySelector(".modal-foot .btn");
    first && first.focus();
  });
  backdrop.addEventListener("keydown", (e) => {
    if (e.key === "Escape") close();
    if (e.key === "Tab") trapFocus(e, form);
  });
  return { close, form };
}

function trapFocus(e, root) {
  const items = [...root.querySelectorAll("button, [href], input, select, textarea")].filter(
    (el) => !el.disabled && el.offsetParent !== null,
  );
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    last.focus();
    e.preventDefault();
  } else if (!e.shiftKey && document.activeElement === last) {
    first.focus();
    e.preventDefault();
  }
}

export function closeModal() {
  const root = modalRoot();
  if (!root.firstChild) return;
  clear(root);
  document.body.classList.remove("has-modal");
  lastFocus && lastFocus.focus && lastFocus.focus();
}

export function confirmDialog({ title, message, confirm = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    openModal({
      title,
      body: h("p", { class: "confirm-text" }, message),
      size: "sm",
      onClose: () => resolve(false),
      actions: [
        { label: "Cancel", onClick: (_e, close) => (resolve(false), close()) },
        { label: confirm, kind: danger ? "danger" : "primary", submit: true, autofocus: !danger,
          onClick: (_e, close) => (resolve(true), close()) },
      ],
    });
  });
}

export function toast(message, kind = "ok", ms = 4200) {
  const root = document.getElementById("toasts");
  const el = h(
    "div",
    { class: `toast toast-${kind}` },
    icon(kind === "error" ? "close" : kind === "warn" ? "info" : "shield", 16),
    h("span", {}, message),
  );
  root.append(el);
  requestAnimationFrame(() => el.classList.add("show"));
  setTimeout(() => {
    el.classList.remove("show");
    setTimeout(() => el.remove(), 300);
  }, ms);
}

export function field(label, control, hint) {
  const id = control.id || `f-${Math.random().toString(36).slice(2, 9)}`;
  control.id = id;
  return h(
    "div",
    { class: "field" },
    h("label", { for: id }, label),
    control,
    hint ? h("p", { class: "field-hint" }, hint) : null,
  );
}

// The drawer: a panel sliding in from the right (dossier details).
const drawer = () => document.getElementById("drawer");

export function openDrawer(content) {
  const d = drawer();
  clear(d).append(
    h("div", { class: "drawer-inner" },
      h("button", { class: "btn btn-ghost btn-icon drawer-close", type: "button", "aria-label": "Close details",
        onclick: closeDrawer }, icon("close")),
      content),
  );
  d.setAttribute("aria-hidden", "false");
  d.classList.add("open");
  document.addEventListener("keydown", drawerKeys);
}

export function closeDrawer() {
  const d = drawer();
  d.classList.remove("open");
  d.setAttribute("aria-hidden", "true");
  document.removeEventListener("keydown", drawerKeys);
}

export function drawerOpenFor() {
  return drawer().classList.contains("open") ? drawer().dataset.subject || null : null;
}

export function setDrawerSubject(id) {
  drawer().dataset.subject = id;
}

function drawerKeys(e) {
  if (e.key === "Escape" && !document.body.classList.contains("has-modal")) closeDrawer();
}
