// The one transport switch (SPEC §4.1). Server mode: JSON-RPC 2.0 over POST /rpc; the
// session token comes from the URL fragment, which the browser never sends to the server,
// and goes back in a header with every call. Web mode: the same requests go to the Python
// worker in this tab.
import { MODE } from "./mode.js";
import { WorkerTransport } from "./worker-client.js";

const params = new URLSearchParams(location.hash.slice(1));
export const mode = MODE;
export const token = params.get("token") || "";
export const openOnStart = params.get("open");
let nextId = 1;
let worker = null;
let statusListener = () => {};

export function onTransportStatus(listener) {
  statusListener = listener;
}

export async function rpc(method, args = {}) {
  if (MODE === "web") {
    worker ??= new WorkerTransport((text) => statusListener(text));
    return worker.call(method, args);
  }
  const response = await fetch("/rpc", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-GCAD-Token": token },
    body: JSON.stringify({ jsonrpc: "2.0", id: nextId++, method, params: args }),
  });
  if (response.status === 401) throw new Error("This page's session token is no longer valid. Open the address `gcad serve` printed.");
  const body = await response.json();
  if (body.error) throw new Error(body.error.message);
  return body.result;
}

export function decode(base64, Type) {
  const bytes = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
  return new Type(bytes.buffer);
}
