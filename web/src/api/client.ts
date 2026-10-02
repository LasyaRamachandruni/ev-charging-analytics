// Typed client for the REST API. Types come from src/api/schema.ts, which is
// generated from the backend's OpenAPI schema (`npm run gen:api`).
import type { components } from "./schema";

type S = components["schemas"];
export type Site = S["Site"];
export type Summary = S["Summary"];
export type DailyRow = S["DailyRow"];
export type LoadProfile = S["LoadProfile"];
export type LoadSlot = S["LoadSlot"];
export type Station = S["Station"];
export type Session = S["Session"];
export type SessionPage = S["SessionPage"];

export interface Range {
  start?: string;
  end?: string;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function get<T>(path: string, params: Record<string, string | number | undefined> = {}, signal?: AbortSignal): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") qs.set(k, String(v));
  const url = qs.size ? `${path}?${qs}` : path;
  const res = await fetch(url, { signal });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

const site = (s: string) => `/api/sites/${encodeURIComponent(s)}`;

export const api = {
  sites: (signal?: AbortSignal) => get<Site[]>("/api/sites", {}, signal),
  summary: (s: string, r: Range, signal?: AbortSignal) => get<Summary>(`${site(s)}/summary`, { ...r }, signal),
  daily: (s: string, r: Range, signal?: AbortSignal) => get<DailyRow[]>(`${site(s)}/daily`, { ...r }, signal),
  loadProfile: (s: string, r: Range, signal?: AbortSignal) => get<LoadProfile>(`${site(s)}/load-profile`, { ...r }, signal),
  stations: (s: string, signal?: AbortSignal) => get<Station[]>(`${site(s)}/stations`, {}, signal),
  sessions: (s: string, r: Range & { station?: string; cursor?: string; limit?: number }, signal?: AbortSignal) =>
    get<SessionPage>(`${site(s)}/sessions`, { ...r }, signal),
};
