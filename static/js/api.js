// The only place that talks to the server. Every call sends same-origin credentials; every mutation also sends
// X-Tracker: 1 and a JSON body (the server's request guard insists on both, DELETE included).
// A 423 means the idle lock is closed: it dispatches a `locked` event on `events` (main.js and lock.js listen) and
// still throws, so the screen that asked can stop drawing.

export const events = new EventTarget();

export class ApiError extends Error {
  constructor(status, message, data = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.data = data;
    this.locked = status === 423 && data?.locked === true;
  }
}

async function request(method, path, body) {
  const headers = { Accept: "application/json" };
  const init = { method, headers, credentials: "same-origin", cache: "no-store" };
  if (method !== "GET") {
    headers["X-Tracker"] = "1";
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body ?? {});
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, "Cannot reach the server.");
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null; // an HTML sign-in page from Cloudflare, or an empty body
  }
  if (res.ok) {
    if (data === null) throw new ApiError(res.status, "Unexpected response. Reload the page.");
    return data;
  }
  const err = new ApiError(res.status, data?.error ?? `Request failed (${res.status}).`, data);
  if (res.status === 423) events.dispatchEvent(new Event("locked"));
  throw err;
}

export const get = path => request("GET", path);
export const send = (method, path, body) => request(method, path, body);
