// Every request carries the per-install app token. The desktop shell injects
// the API base and token; Vite uses the local bootstrap endpoint and proxy.
declare global {
  interface Window {
    __APP_TOKEN__?: string;
    __API_BASE__?: string;
  }
}

const API_BASE = window.__API_BASE__ || "";

let tokenPromise: Promise<string> | null = null;

function isTokenPayload(value: unknown): value is { token: string } {
  return (
    typeof value === "object" &&
    value !== null &&
    "token" in value &&
    typeof value.token === "string"
  );
}

async function getToken(): Promise<string> {
  if (window.__APP_TOKEN__) return window.__APP_TOKEN__;
  if (!tokenPromise) {
    tokenPromise = fetch(`${API_BASE}/api/dev/token`).then(async (response) => {
      if (!response.ok) throw new Error("Token bootstrap failed.");
      const payload: unknown = await response.json();
      if (!isTokenPayload(payload)) throw new Error("Token bootstrap returned invalid data.");
      return payload.token;
    });
  }
  return tokenPromise;
}

export function getAppToken(): Promise<string> {
  return getToken();
}

export function getApiBase(): string {
  return API_BASE || "http://127.0.0.1:8765";
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await getToken();
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-App-Token": token,
      ...(init.headers || {}),
    },
  });
  if (!response.ok) {
    const raw = await response.text();
    let message = raw || `Request failed (${response.status}).`;
    try {
      const parsed: unknown = JSON.parse(raw);
      if (
        typeof parsed === "object" &&
        parsed !== null &&
        "detail" in parsed &&
        typeof parsed.detail === "string"
      ) {
        message = parsed.detail;
      }
    } catch {
      // Keep the server's plain-text response.
    }
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}

export type Support = "high" | "medium" | "low";
export type AnswerMode =
  | "answer_card"
  | "structured_fact"
  | "generated_from_local_sources"
  | "abstained";
export type ContextType =
  | "trip"
  | "conference"
  | "course"
  | "project"
  | "emergency"
  | "custom";
export type PrivacyMode = "local_only" | "allow_online_planning";
export type PreparationQuality = "fast" | "final";
export type Theme = "system" | "light" | "dark";

export interface SourceRef {
  source_id: string;
  title: string;
  snippet: string;
}

export interface ContextRecord {
  context_id: string;
  name: string;
  context_type: ContextType;
  goal: string;
  starts_at: string | null;
  ends_at: string | null;
  languages: string[];
  interests: string[];
  expected_needs: string[];
  storage_budget_mb: number;
  privacy_mode: PrivacyMode;
  preparation_quality: PreparationQuality;
  active_pack_id: string | null;
  template_id: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ContextPack {
  pack_id: string;
  version: number;
  ready: boolean;
  is_current: boolean;
  size_bytes: number;
  created_at: string;
}

export type SourceType = "text" | "web" | "file" | "structured";

export interface ContextSource {
  source_id: string;
  context_id: string;
  title: string;
  source_type: SourceType;
  url: string | null;
  local_path: string | null;
  content: string;
  metadata: Record<string, unknown>;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface ContextDetails extends ContextRecord {
  sources: ContextSource[];
  packs: ContextPack[];
}

export interface ContextInput {
  name: string;
  context_type: ContextType;
  goal: string;
  starts_at: string | null;
  ends_at: string | null;
  languages: string[];
  interests: string[];
  expected_needs: string[];
  storage_budget_mb: number;
  privacy_mode: PrivacyMode;
  preparation_quality: PreparationQuality;
  template_id: string | null;
}

export type ContextUpdate = Partial<ContextInput>;

export interface SourceInput {
  title: string;
  source_type: SourceType;
  url?: string | null;
  local_path?: string | null;
  content?: string;
  metadata?: Record<string, unknown>;
}

export interface SourceUpdate {
  title?: string;
  content?: string;
  enabled?: boolean;
  metadata?: Record<string, unknown>;
}

export interface Template {
  template_id: string;
  name: string;
  description: string;
  context_type: ContextType;
  languages: string[];
  interests: string[];
  topics: string[];
}

export interface PrepareOptions {
  selected_source_ids?: string[];
  selected_capabilities?: string[];
  selected_topics?: string[];
  expected_questions?: string[];
  compile_expert?: boolean;
  finalize?: boolean;
  allow_online_synth?: boolean;
}

export interface PackPlan {
  context_id: string;
  name: string;
  context_type: string;
  goal: string;
  languages: string[];
  interests: string[];
  storage_budget_mb: number;
  include_base_model: boolean;
  selected_capabilities: string[];
  selected_topics: string[];
  expert_specs: string[];
  expected_questions: string[];
  dropped_topics: string[];
  selected_source_ids: string[];
  source_bytes: number;
  template_id: string | null;
  privacy_disclosures: string[];
  fits_budget: boolean;
  warnings: string[];
  storage_estimate_bytes: number;
  preparation_time_estimate_s: number;
}

export type JobState =
  | "planning"
  | "searching"
  | "compiling"
  | "downloading"
  | "processing_documents"
  | "indexing"
  | "testing"
  | "ready"
  | "failed"
  | "cancelled";

export interface JobProgress {
  state: JobState;
  message: string;
  at: string;
}

export interface PreparationJob {
  job_id: string;
  pack_id: string | null;
  context_id: string;
  state: JobState;
  plan: PrepareOptions & { context_id: string };
  progress: JobProgress[];
  error: string | null;
  updated_at: string;
}

export type UIAction =
  | "show_history"
  | "new_conversation"
  | "switch_context"
  | "create_context"
  | "prepare_context"
  | "add_source"
  | "show_context_status"
  | "show_settings"
  | "show_unresolved"
  | "show_storage"
  | "delete_context"
  | "answer_question";

export interface CommandInput {
  text: string;
  conversation_id?: string;
  context_id?: string;
  confirmed?: boolean;
}

export interface CommandResponse {
  kind: "answer" | "ui_action" | "workflow" | "clarification";
  conversation_id: string;
  message_id: string | null;
  message: string;
  action: UIAction | null;
  arguments: Record<string, unknown>;
  data: Record<string, unknown>;
  requires_confirmation: boolean;
  answer: string | null;
  support: Support | null;
  answer_mode: AnswerMode | null;
  sources: SourceRef[];
  stale: boolean;
  queued_for_verification: boolean;
}

export interface ConversationSummary {
  conversation_id: string;
  context_id: string | null;
  trip_id?: string | null;
  title: string;
  created_at: string;
  updated_at: string;
  message_count?: number;
}

export interface ConversationMessage {
  message_id: string;
  conversation_id: string;
  role: "user" | "assistant";
  kind:
    | "text"
    | "answer"
    | "ui_action"
    | "workflow"
    | "clarification"
    | "verification";
  content: string;
  payload: Record<string, unknown>;
  sources: SourceRef[];
  pack_id: string | null;
  created_at: string;
}

export interface ConversationDetails extends ConversationSummary {
  messages: ConversationMessage[];
}

export interface ConversationInput {
  context_id?: string | null;
  trip_id?: string | null;
  title?: string;
}

export interface AppSettings {
  theme: Theme;
  active_context_id: string | null;
  active_trip_id?: string | null;
  privacy_mode: PrivacyMode;
  default_storage_budget_mb: number;
  show_advanced: boolean;
  optimize_in_background?: boolean;
  search_mode?: "automatic" | "official_only" | "off";
  ask_history_window?: number;
}

export type SettingsUpdate = Partial<AppSettings>;

export interface SearchProviderStatus {
  provider: string;
  configured: boolean;
  managed_by_environment: boolean;
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
  conversation_id: string | null;
  message_id: string | null;
}

export interface VerificationResult {
  id: number;
  changed: boolean;
  offline_answer: string | null;
  verified_answer: string;
}

export interface IngestInput {
  title?: string;
  text?: string;
  html?: string;
  url?: string;
  context_id?: string;
}

export interface IngestResult {
  source_id: string;
  context_id: string;
  status: "saved" | "already_saved";
  rebuild_required?: boolean;
}

export interface TripDates {
  start?: string | null;
  end?: string | null;
}

export interface TripSource {
  source_id?: string;
  id?: string;
  title?: string;
  publisher?: string;
  freshness?: string;
  updated_at?: string;
  snippet?: string;
  enabled?: boolean;
  [key: string]: unknown;
}

export interface TripCoverage {
  areas?: unknown[];
  categories?: unknown[];
  semantic_coverage?: unknown[];
  publishers?: unknown[];
  source_publishers?: unknown[];
  sources?: TripSource[];
  freshness?: string;
  updated_at?: string;
  size_bytes?: number;
  estimated_size_bytes?: number;
  preparation_time_estimate_s?: number;
  estimated_time_seconds?: number;
  privacy?: string;
  [key: string]: unknown;
}

export interface Trip {
  trip_id?: string;
  id?: string;
  context_id?: string;
  name?: string;
  title?: string;
  event?: string | { name?: string; title?: string };
  destination?: string | { city?: string; country?: string; label?: string };
  dates?: TripDates;
  start_date?: string | null;
  end_date?: string | null;
  starts_at?: string | null;
  ends_at?: string | null;
  languages?: string[];
  needs?: string[];
  expected_needs?: string[];
  status?: string;
  ready_offline?: boolean;
  active_pack_id?: string | null;
  privacy_mode?: PrivacyMode;
  storage_budget_mb?: number;
  size_bytes?: number;
  estimated_size_bytes?: number;
  preparation_time_estimate_s?: number;
  coverage?: TripCoverage;
  sources?: TripSource[];
  created_at?: string;
  updated_at?: string;
  [key: string]: unknown;
}

export interface TripPatch {
  event?: string;
  destination?: string;
  dates?: TripDates;
  languages?: string[];
  needs?: string[];
}

export interface TripAttachment {
  name: string;
  kind: "text" | "file";
  content: string;
  media_type?: string;
  size_bytes?: number;
  encoding?: "utf-8" | "data-url";
}

export interface TripParseResult {
  trip: Trip;
  blocking_question?: string | null;
  suggested_queries?: unknown[];
  coverage?: TripCoverage;
}

export interface DiscoverySummary {
  trip?: Trip;
  coverage?: TripCoverage;
  sources?: TripSource[];
  publishers?: unknown[];
  freshness?: string;
  size_bytes?: number;
  estimated_size_bytes?: number;
  preparation_time_estimate_s?: number;
  gaps?: Array<{ code?: string; message?: string }>;
  [key: string]: unknown;
}

export interface TravelJob {
  job_id: string;
  trip_id?: string;
  context_id?: string;
  state?: string;
  status?: string;
  progress_percent?: number;
  percent?: number;
  progress?: number | JobProgress[];
  error?: string | null;
  cancel_requested?: boolean;
  updated_at?: string;
  [key: string]: unknown;
}

export interface AskInput {
  text: string;
  trip_id: string;
  conversation_id?: string;
  new_topic?: boolean;
}

export type AskStreamEventType =
  | "route"
  | "branch_started"
  | "branch_complete"
  | "answer_update"
  | "final"
  | "abstain";

export interface AskStreamEvent {
  type: AskStreamEventType;
  [key: string]: unknown;
}

export interface AskResponse {
  answer?: string;
  message?: string;
  conversation_id?: string;
  support?: Support | string;
  sources?: unknown[];
  freshness?: string;
  stale?: boolean;
  refined?: boolean;
  changed?: boolean;
  how_built?: unknown;
  [key: string]: unknown;
}

function body(value: unknown): Pick<RequestInit, "body"> {
  return { body: JSON.stringify(value) };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

async function responseError(response: Response): Promise<ApiError> {
  const raw = await response.text();
  let message = raw || `Request failed (${response.status}).`;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (isRecord(parsed) && typeof parsed.detail === "string") {
      message = parsed.detail;
    }
  } catch {
    // Keep the server's plain-text response.
  }
  return new ApiError(response.status, message);
}

function asAskEventType(value: unknown): AskStreamEventType | null {
  switch (value) {
    case "route":
    case "branch_started":
    case "branch_complete":
    case "answer_update":
    case "final":
    case "abstain":
      return value;
    default:
      return null;
  }
}

function decodeSseBlock(block: string): AskStreamEvent | null {
  let eventName = "";
  const dataLines: string[] = [];

  for (const line of block.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    let value = separator === -1 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") eventName = value;
    if (field === "data") dataLines.push(value);
  }

  if (!dataLines.length) return null;
  const rawData = dataLines.join("\n");
  let parsed: unknown = rawData;
  try {
    parsed = JSON.parse(rawData) as unknown;
  } catch {
    // Plain text is still a valid SSE data payload.
  }

  const record = isRecord(parsed) ? parsed : { text: String(parsed) };
  const nested = isRecord(record.data) ? record.data : null;
  const type =
    asAskEventType(eventName) ??
    asAskEventType(record.type) ??
    asAskEventType(record.event);
  if (!type) return null;

  return {
    ...record,
    ...(nested ?? {}),
    type,
  };
}

export async function streamAsk(
  input: AskInput,
  onEvent: (event: AskStreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const token = await getToken();
  const response = await fetch(`${API_BASE}/api/ask/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      "X-App-Token": token,
    },
    body: JSON.stringify(input),
    signal,
  });
  if (!response.ok) throw await responseError(response);
  if (!response.body) throw new ApiError(502, "The answer stream was unavailable.");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  function dispatchAvailableBlocks(final = false) {
    while (true) {
      const boundary = /\r?\n\r?\n/.exec(buffer);
      if (!boundary) break;
      const block = buffer.slice(0, boundary.index);
      buffer = buffer.slice(boundary.index + boundary[0].length);
      const event = decodeSseBlock(block);
      if (event) onEvent(event);
    }
    if (final && buffer.trim()) {
      const event = decodeSseBlock(buffer);
      if (event) onEvent(event);
      buffer = "";
    }
  }

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    dispatchAvailableBlocks();
  }
  buffer += decoder.decode();
  dispatchAvailableBlocks(true);
}

export const api = {
  trips: async () => {
    try {
      return await request<{ trips: Trip[] }>("/api/trips");
    } catch (caught) {
      if (!(caught instanceof ApiError) || caught.status !== 404) throw caught;
      const legacy = await request<{ contexts: ContextRecord[] }>("/api/contexts");
      return { trips: legacy.contexts as unknown as Trip[] };
    }
  },
  trip: async (tripId: string) => {
    try {
      const result = await request<Trip | { trip: Trip }>(
        `/api/trips/${encodeURIComponent(tripId)}`,
      );
      return isRecord(result) && isRecord(result.trip)
        ? (result.trip as Trip)
        : (result as Trip);
    } catch (caught) {
      if (!(caught instanceof ApiError) || caught.status !== 404) throw caught;
      return (await request<ContextDetails>(
        `/api/contexts/${encodeURIComponent(tripId)}`,
      )) as unknown as Trip;
    }
  },
  parseTrip: (text: string, attachments: TripAttachment[] = []) =>
    request<TripParseResult>("/api/trips/parse", {
      method: "POST",
      ...body({ text, ...(attachments.length ? { attachments } : {}) }),
    }),
  updateTrip: async (tripId: string, input: TripPatch) => {
    try {
      const result = await request<Trip | { trip: Trip }>(
        `/api/trips/${encodeURIComponent(tripId)}`,
        { method: "PATCH", ...body(input) },
      );
      return isRecord(result) && isRecord(result.trip)
        ? (result.trip as Trip)
        : (result as Trip);
    } catch (caught) {
      if (!(caught instanceof ApiError) || caught.status !== 404) throw caught;
      const legacyInput: ContextUpdate = {
        ...(input.event ? { name: input.event } : {}),
        ...(input.destination ? { goal: input.destination } : {}),
        ...(input.dates
          ? {
              starts_at: input.dates.start ?? null,
              ends_at: input.dates.end ?? null,
            }
          : {}),
        ...(input.languages ? { languages: input.languages } : {}),
        ...(input.needs ? { expected_needs: input.needs } : {}),
      };
      return (await request<ContextRecord>(
        `/api/contexts/${encodeURIComponent(tripId)}`,
        { method: "PATCH", ...body(legacyInput) },
      )) as unknown as Trip;
    }
  },
  deleteTrip: async (tripId: string) => {
    try {
      return await request<{ deleted: string }>(
        `/api/trips/${encodeURIComponent(tripId)}`,
        { method: "DELETE" },
      );
    } catch (caught) {
      if (!(caught instanceof ApiError) || caught.status !== 404) throw caught;
      return request<{ deleted: string }>(
        `/api/contexts/${encodeURIComponent(tripId)}`,
        { method: "DELETE" },
      );
    }
  },
  discoverTrip: async (tripId: string) => {
    try {
      return await request<DiscoverySummary>(
        `/api/trips/${encodeURIComponent(tripId)}/discover`,
        { method: "POST", ...body({}) },
      );
    } catch (caught) {
      if (!(caught instanceof ApiError) || caught.status !== 404) throw caught;
      const legacy = await request<PackPlan>(
        `/api/contexts/${encodeURIComponent(tripId)}/plan`,
        { method: "POST", ...body({}) },
      );
      return {
        coverage: {
          semantic_coverage: legacy.selected_topics,
          size_bytes: legacy.storage_estimate_bytes,
          preparation_time_estimate_s: legacy.preparation_time_estimate_s,
          privacy: legacy.privacy_disclosures.join(" "),
        },
      } satisfies DiscoverySummary;
    }
  },
  prepareTrip: async (
    tripId: string,
    options: { source_ids?: string[]; optimize?: boolean } = {},
  ) => {
    try {
      return await request<{ job_id: string; trip_id: string }>(
        `/api/trips/${encodeURIComponent(tripId)}/prepare`,
        { method: "POST", ...body(options) },
      );
    } catch (caught) {
      if (!(caught instanceof ApiError) || caught.status !== 404) throw caught;
      const legacy = await request<{ job_id: string; context_id: string }>(
        `/api/contexts/${encodeURIComponent(tripId)}/prepare`,
        {
          method: "POST",
          ...body({
            selected_source_ids: options.source_ids,
            finalize: options.optimize ?? true,
          }),
        },
      );
      return { job_id: legacy.job_id, trip_id: legacy.context_id };
    }
  },
  tripJob: (jobId: string) =>
    request<TravelJob>(`/api/jobs/${encodeURIComponent(jobId)}`),
  tripStarters: async (tripId: string) => {
    try {
      return await request<{ questions: unknown[] }>(
        `/api/trips/${encodeURIComponent(tripId)}/starters`,
      );
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        return { questions: [] };
      }
      throw caught;
    }
  },
  ask: (input: AskInput) =>
    request<AskResponse>("/api/ask", {
      method: "POST",
      ...body(input),
    }),
  contexts: () => request<{ contexts: ContextRecord[] }>("/api/contexts"),
  context: (contextId: string) =>
    request<ContextDetails>(`/api/contexts/${encodeURIComponent(contextId)}`),
  createContext: (input: ContextInput) =>
    request<ContextRecord>("/api/contexts", {
      method: "POST",
      ...body(input),
    }),
  updateContext: (contextId: string, input: ContextUpdate) =>
    request<ContextRecord>(`/api/contexts/${encodeURIComponent(contextId)}`, {
      method: "PATCH",
      ...body(input),
    }),
  deleteContext: (contextId: string) =>
    request<{ deleted: string }>(`/api/contexts/${encodeURIComponent(contextId)}`, {
      method: "DELETE",
    }),
  sources: (contextId: string) =>
    request<{ sources: ContextSource[] }>(
      `/api/contexts/${encodeURIComponent(contextId)}/sources`,
    ),
  addSource: (contextId: string, input: SourceInput) =>
    request<ContextSource>(
      `/api/contexts/${encodeURIComponent(contextId)}/sources`,
      { method: "POST", ...body(input) },
    ),
  updateSource: (sourceId: string, input: SourceUpdate) =>
    request<ContextSource>(`/api/sources/${encodeURIComponent(sourceId)}`, {
      method: "PATCH",
      ...body(input),
    }),
  deleteSource: (sourceId: string) =>
    request<{ deleted: string }>(`/api/sources/${encodeURIComponent(sourceId)}`, {
      method: "DELETE",
    }),
  templates: () => request<{ templates: Template[] }>("/api/templates"),
  planContext: (contextId: string, options: PrepareOptions = {}) =>
    request<PackPlan>(`/api/contexts/${encodeURIComponent(contextId)}/plan`, {
      method: "POST",
      ...body(options),
    }),
  prepareContext: (contextId: string, options: PrepareOptions = {}) =>
    request<{ job_id: string; context_id: string }>(
      `/api/contexts/${encodeURIComponent(contextId)}/prepare`,
      { method: "POST", ...body(options) },
    ),
  job: (jobId: string) =>
    request<PreparationJob>(`/api/jobs/${encodeURIComponent(jobId)}`),
  cancelJob: (jobId: string) =>
    request<{ job_id: string; cancel_requested: boolean }>(
      `/api/jobs/${encodeURIComponent(jobId)}/cancel`,
      { method: "POST" },
    ),
  command: (input: CommandInput) =>
    request<CommandResponse>("/api/command", {
      method: "POST",
      ...body(input),
    }),
  conversations: (
    options: {
      contextId?: string;
      tripId?: string;
      query?: string;
      limit?: number;
    } = {},
  ) => {
    const params = new URLSearchParams();
    if (options.contextId) params.set("context_id", options.contextId);
    if (options.tripId) params.set("trip_id", options.tripId);
    if (options.query) params.set("q", options.query);
    if (options.limit) params.set("limit", String(options.limit));
    const query = params.size ? `?${params.toString()}` : "";
    return request<{ conversations: ConversationSummary[] }>(
      `/api/conversations${query}`,
    );
  },
  createConversation: (input: ConversationInput = {}) =>
    request<ConversationSummary>("/api/conversations", {
      method: "POST",
      ...body({ title: "New conversation", ...input }),
    }),
  conversation: (conversationId: string) =>
    request<ConversationDetails>(
      `/api/conversations/${encodeURIComponent(conversationId)}`,
    ),
  updateConversation: (conversationId: string, title: string) =>
    request<ConversationSummary>(
      `/api/conversations/${encodeURIComponent(conversationId)}`,
      { method: "PATCH", ...body({ title }) },
    ),
  deleteConversation: (conversationId: string) =>
    request<{ deleted: string }>(
      `/api/conversations/${encodeURIComponent(conversationId)}`,
      { method: "DELETE" },
    ),
  settings: () => request<AppSettings>("/api/settings"),
  updateSettings: (input: SettingsUpdate) =>
    request<AppSettings>("/api/settings", {
      method: "PATCH",
      ...body(input),
    }),
  searchStatus: () =>
    request<SearchProviderStatus>("/api/settings/search"),
  saveSearchKey: (apiKey: string) =>
    request<SearchProviderStatus>("/api/settings/search", {
      method: "PUT",
      ...body({ api_key: apiKey }),
    }),
  deleteSearchKey: () =>
    request<SearchProviderStatus>("/api/settings/search", {
      method: "DELETE",
    }),
  queue: () => request<{ items: QueueItem[] }>("/api/queue"),
  verify: (id: number, verifiedAnswer: string) =>
    request<VerificationResult>(`/api/queue/${id}/verify`, {
      method: "POST",
      ...body({ verified_answer: verifiedAnswer }),
    }),
  ingest: (input: IngestInput) =>
    request<IngestResult>("/api/ingest", {
      method: "POST",
      ...body(input),
    }),
};
