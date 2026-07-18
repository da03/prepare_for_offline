declare global {
  interface Window {
    __APP_TOKEN__?: string;
    __API_BASE__?: string;
  }
}

const API_BASE = window.__API_BASE__ || "";
let tokenPromise: Promise<string> | null = null;

async function token(): Promise<string> {
  if (window.__APP_TOKEN__) return window.__APP_TOKEN__;
  if (!tokenPromise) {
    tokenPromise = fetch(`${API_BASE}/api/dev/token`)
      .then(async (response) => {
        if (!response.ok) throw new Error("PAW Offline is not available.");
        return (await response.json()) as { token: string };
      })
      .then((payload) => payload.token);
  }
  return tokenPromise;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-App-Token": await token(),
      ...(init.headers || {}),
    },
  });
  if (!response.ok) {
    const raw = await response.text();
    let message = raw || `Request failed (${response.status}).`;
    try {
      const parsed = JSON.parse(raw) as { detail?: string };
      message = parsed.detail || message;
    } catch {
      // Keep plain text.
    }
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}

function jsonBody(value: unknown): Pick<RequestInit, "body"> {
  return { body: JSON.stringify(value) };
}

export interface PreparedProgram {
  program_key: string;
  topic: string;
  name: string;
  status: "preparing" | "improving" | "ready" | "failed" | string;
  program_id?: string | null;
  compiler?: string | null;
  stage?: "standard" | "finetuned" | string | null;
  contract_score?: number | null;
  created_at: string;
  updated_at: string;
}

export interface NeuralStatus {
  ready: boolean;
  built_in_program_count: number;
  prepared_programs: PreparedProgram[];
}

export interface NeuralJob {
  job_id: string;
  program_key: string;
  topic_prompt: string;
  state: string;
  progress_percent: number;
  message: string;
  error?: string | null;
  standard_version_id?: string | null;
  finetuned_version_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface AskInput {
  text: string;
  reply_to_message_id?: string;
}

export interface AskStreamEvent {
  type: string;
  answer?: string;
  status?: string;
  labels?: string[];
  conversation_id?: string;
  message_id?: string;
  refined?: boolean;
  used_context?: boolean;
  [key: string]: unknown;
}

export interface ConversationSummary {
  conversation_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  question_count?: number;
}

export interface ConversationMessage {
  message_id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ConversationDetails extends ConversationSummary {
  messages: ConversationMessage[];
}

function decodeSse(block: string): AskStreamEvent | null {
  let data = "";
  let eventType = "";
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) eventType = line.slice(6).trim();
    if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!data) return null;
  const event = JSON.parse(data) as AskStreamEvent;
  if (!event.type && eventType) event.type = eventType;
  return event;
}

export async function streamAsk(
  input: AskInput,
  onEvent: (event: AskStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE}/api/ask/stream`, {
    method: "POST",
    signal,
    headers: {
      "Content-Type": "application/json",
      "X-App-Token": await token(),
    },
    body: JSON.stringify(input),
  });
  if (!response.ok) throw new ApiError(response.status, await response.text());
  if (!response.body) throw new Error("Answer stream is unavailable.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const event = decodeSse(buffer.slice(0, boundary));
      if (event) onEvent(event);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }
  }
  buffer += decoder.decode();
  const event = decodeSse(buffer);
  if (event) onEvent(event);
}

export const api = {
  status: () => request<NeuralStatus>("/api/neural/status"),
  programs: () =>
    request<{ programs: PreparedProgram[] }>("/api/programs"),
  prepareProgram: (prompt: string) =>
    request<NeuralJob>("/api/programs/prepare", {
      method: "POST",
      ...jsonBody({ prompt }),
    }),
  job: (jobId: string) =>
    request<NeuralJob>(`/api/neural/jobs/${encodeURIComponent(jobId)}`),
  cancelJob: (jobId: string) =>
    request<{ cancelled: boolean }>(
      `/api/neural/jobs/${encodeURIComponent(jobId)}/cancel`,
      { method: "POST" },
    ),
  deleteProgram: (programKey: string) =>
    request<{ deleted: boolean }>(
      `/api/programs/${encodeURIComponent(programKey)}`,
      { method: "DELETE" },
    ),
  conversations: (query = "") =>
    request<{ conversations: ConversationSummary[] }>(
      `/api/conversations?limit=100${query ? `&q=${encodeURIComponent(query)}` : ""}`,
    ),
  conversation: (id: string) =>
    request<ConversationDetails>(
      `/api/conversations/${encodeURIComponent(id)}`,
    ),
  deleteConversation: (id: string) =>
    request<{ deleted: string }>(
      `/api/conversations/${encodeURIComponent(id)}`,
      { method: "DELETE" },
    ),
};
