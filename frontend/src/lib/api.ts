// Lightweight API client. Every request carries the per-install app token.
// In dev the token is fetched from the localhost-only /api/dev/token bootstrap;
// in the packaged app it is injected as window.__APP_TOKEN__.

declare global {
  interface Window {
    __APP_TOKEN__?: string;
    __API_BASE__?: string;
  }
}

// In the packaged (Tauri) app the shell injects the API base + token; in dev
// the base is empty (Vite proxies /api) and the token comes from the localhost
// bootstrap endpoint.
const API_BASE = window.__API_BASE__ || "";

let tokenPromise: Promise<string> | null = null;

async function getToken(): Promise<string> {
  if (window.__APP_TOKEN__) return window.__APP_TOKEN__;
  if (!tokenPromise) {
    tokenPromise = fetch(`${API_BASE}/api/dev/token`)
      .then((r) => {
        if (!r.ok) throw new Error("token bootstrap failed");
        return r.json();
      })
      .then((d) => d.token as string);
  }
  return tokenPromise;
}

export async function getAppToken(): Promise<string> {
  return getToken();
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await getToken();
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-App-Token": token,
      ...(init.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export interface SourceRef {
  source_id: string;
  title: string;
  snippet: string;
}

export interface ChatResponse {
  answer: string;
  support: "high" | "medium" | "low";
  answer_mode: "answer_card" | "structured_fact" | "generated_from_local_sources" | "abstained";
  sources: SourceRef[];
  stale: boolean;
  queued_for_verification: boolean;
  expert_used: string | null;
  debug?: Record<string, unknown>;
}

export interface Pack {
  pack_id: string;
  title: string;
  ready: boolean;
  size_bytes: number;
  created_at: string;
  manifest: Record<string, any>;
}

export interface QueueItem {
  id: number;
  question: string;
  offline_answer: string | null;
  offline_support: string | null;
  offline_sources: string[];
  status: string;
  verified_answer: string | null;
  changed: boolean | null;
  created_at: string;
  verified_at: string | null;
}

export const api = {
  chat: (question: string, pack_id?: string) =>
    request<ChatResponse>("/api/chat", {
      method: "POST",
      body: JSON.stringify({ question, pack_id }),
    }),
  packs: () => request<{ packs: Pack[] }>("/api/packs"),
  storage: () => request<{ home: string; total_bytes: number; pack_count: number }>("/api/storage"),
  metrics: () => request<{ expert_loader: any }>("/api/metrics"),
  prepare: (compile_expert: boolean, finalize: boolean) =>
    request<{ job_id: string; pack_id: string }>("/api/prepare", {
      method: "POST",
      body: JSON.stringify({ compile_expert, finalize }),
    }),
  job: (jobId: string) => request<any>(`/api/jobs/${jobId}`),
  queue: () => request<{ items: QueueItem[] }>("/api/queue"),
  verify: (id: number, verified_answer: string) =>
    request<any>(`/api/queue/${id}/verify`, {
      method: "POST",
      body: JSON.stringify({ verified_answer }),
    }),
};
