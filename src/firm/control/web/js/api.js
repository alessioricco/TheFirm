// Talking to the control API. Same-origin only; the login cookie goes along
// by itself. Changes are always JSON (the API refuses anything else).

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

let onLoggedOut = () => {};
export function whenLoggedOut(fn) {
  onLoggedOut = fn;
}

async function request(method, path, body) {
  const init = { method, credentials: "same-origin", headers: {} };
  if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError("The dashboard server isn't answering. Is `firm dashboard serve` running?", 0);
  }
  if (res.status === 401) {
    onLoggedOut();
    throw new ApiError("Not logged in", 401);
  }
  const isJson = (res.headers.get("content-type") || "").includes("application/json");
  const data = isJson ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = isJson && data && data.detail;
    const message = Array.isArray(detail)
      ? detail.map((d) => d.msg).join("; ")
      : detail || `${res.status} ${res.statusText}`;
    throw new ApiError(message, res.status);
  }
  return data;
}

export const api = {
  get: (path) => request("GET", path),
  post: (path, body = {}) => request("POST", path, body),
  put: (path, body = {}) => request("PUT", path, body),
};
