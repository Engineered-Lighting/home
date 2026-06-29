import type { ServiceHealth } from "./types";

export type MetricsSnapshot = {
  ok?: boolean;
  text?: string;
  raw?: unknown;
};

export async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { cache: "no-store", signal });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return await response.json() as T;
}

export async function getMetrics(baseUrl: string, signal?: AbortSignal): Promise<MetricsSnapshot> {
  const base = baseUrl.replace(/\/+$/, "");
  const response = await fetch(`${base}/metrics`, { cache: "no-store", signal });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return {
    ok: true,
    text: await response.text()
  };
}

export async function getRecentConversations(baseUrl: string, signal?: AbortSignal): Promise<unknown[]> {
  const base = baseUrl.replace(/\/+$/, "");
  try {
    const data = await fetchJson<unknown>(`${base}/conversations/recent`, signal);
    return Array.isArray(data) ? data : [];
  } catch {
    return [];
  }
}

export async function probeHealth(id: string, label: string, baseUrl: string, signal?: AbortSignal): Promise<ServiceHealth> {
  const base = baseUrl.replace(/\/+$/, "");
  const start = performance.now();
  try {
    const response = await fetch(`${base}/healthz`, { cache: "no-store", signal });
    const latencyMs = Math.round(performance.now() - start);
    if (!response.ok) return { id, label, state: "degraded", detail: `HTTP ${response.status}`, latencyMs };
    return { id, label, state: "online", detail: "ready", latencyMs };
  } catch (error) {
    return {
      id,
      label,
      state: "offline",
      detail: error instanceof Error ? error.message : "offline"
    };
  }
}
