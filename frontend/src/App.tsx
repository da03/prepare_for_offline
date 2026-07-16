import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  TravelHeader,
  type PrimaryPage,
} from "./components/TravelHeader";
import {
  AskPage,
  type AnswerSource,
  type AnswerTurn,
} from "./components/AskPage";
import { HistoryDrawer } from "./components/HistoryDrawer";
import {
  PreparePage,
  type PreparePhase,
  type PrepareRequest,
} from "./components/PreparePage";
import { PreferencesSheet } from "./components/PreferencesSheet";
import {
  ApiError,
  api,
  streamAsk,
  type AppSettings,
  type AskResponse,
  type AskStreamEvent,
  type ConversationMessage,
  type DiscoverySummary,
  type SearchProviderStatus,
  type SettingsUpdate,
  type TravelJob,
  type Trip,
  type TripAttachment,
  type TripCoverage,
  type TripPatch,
} from "./lib/api";
import {
  coverageSources,
  isTripReady,
  mergeCoverage,
  sourceId,
  tripDestination,
  tripEvent,
  tripId,
} from "./lib/travel";

const MAX_FOLLOW_UPS = 3;

const defaultSettings: AppSettings = {
  theme: "system",
  active_context_id: null,
  active_trip_id: null,
  privacy_mode: "local_only",
  default_storage_budget_mb: 1200,
  show_advanced: false,
  optimize_in_background: true,
  search_mode: "automatic",
  ask_history_window: 3,
};

const fallbackStarters = [
  "Where and when do I pick up my badge?",
  "How do I get from the airport to my hotel?",
  "What should I know about local transit?",
  "What’s on my schedule the first morning?",
];

const defaultSearchStatus: SearchProviderStatus = {
  provider: "brave",
  configured: false,
  managed_by_environment: false,
};

interface Notice {
  tone: "error" | "success" | "info";
  message: string;
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : {};
}

function firstString(...values: unknown[]): string {
  return (
    values.find(
      (value): value is string =>
        typeof value === "string" && value.trim().length > 0,
    )?.trim() ?? ""
  );
}

function firstNumber(...values: unknown[]): number | null {
  const value = values.find(
    (candidate): candidate is number =>
      typeof candidate === "number" && Number.isFinite(candidate),
  );
  return value ?? null;
}

function normalizeSources(value: unknown): AnswerSource[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item, index): AnswerSource | null => {
      if (typeof item === "string" && item.trim()) {
        return { source_id: `source-${index}`, title: item.trim() };
      }
      const source = asRecord(item);
      const title = firstString(
        source.title,
        source.name,
        source.publisher,
        source.label,
      );
      if (!title) return null;
      return {
        source_id: firstString(source.source_id, source.id) || undefined,
        title,
        publisher: firstString(source.publisher) || undefined,
        snippet: firstString(source.snippet, source.description) || undefined,
        freshness:
          firstString(source.freshness, source.updated_at) || undefined,
      };
    })
    .filter((source): source is AnswerSource => source !== null);
}

function eventResult(event: AskStreamEvent): Record<string, unknown> {
  return asRecord(event.result);
}

function eventString(event: AskStreamEvent, ...keys: string[]): string {
  const result = eventResult(event);
  for (const key of keys) {
    const value = firstString(event[key], result[key]);
    if (value) return value;
  }
  return "";
}

function eventSources(event: AskStreamEvent): AnswerSource[] {
  const result = eventResult(event);
  return normalizeSources(
    event.sources ?? event.citations ?? result.sources ?? result.citations,
  );
}

function eventFreshness(event: AskStreamEvent): string {
  const result = eventResult(event);
  const direct = firstString(event.freshness, result.freshness);
  if (direct) return direct;
  if (event.stale === true || result.stale === true) return "May be out of date";
  return "";
}

function safeSemanticLabel(value: unknown): string {
  if (typeof value !== "string") return "";
  const label = value.trim().replace(/\s+/g, " ");
  if (!label || label.length > 64) return "";
  if (
    /(model|prompt|trace|branch|expert|compiler|program tree|capabilit|token|embedding|latency|json|router)/i.test(
      label,
    )
  ) {
    return "";
  }
  return label;
}

function semanticSourceStatus(
  event: AskStreamEvent,
  index: number,
  trip: Trip,
): string {
  const source = asRecord(event.source);
  const label = safeSemanticLabel(
    event.semantic_status ??
      event.source_label ??
      event.publisher ??
      source.publisher ??
      source.title ??
      event.title ??
      event.label,
  );
  if (label) {
    return /^checking\b/i.test(label) ? label : `Checking ${label}`;
  }
  if (index === 1) {
    const eventName = safeSemanticLabel(tripEvent(trip));
    if (eventName) return `Checking ${eventName} guide`;
  }
  return "Checking local travel sources";
}

function appendUnique(values: string[], value: string): string[] {
  return value && !values.includes(value) ? [...values, value] : values;
}

function supportValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function userFacingError(caught: unknown, fallback: string): string {
  if (caught instanceof ApiError) {
    if (caught.status === 401 || caught.status === 403) {
      return "PAW could not connect to its local service.";
    }
    const message = caught.message.trim();
    if (
      message &&
      message.length <= 180 &&
      !/(traceback|exception|stack|pydantic|sql|json|internal server)/i.test(
        message,
      )
    ) {
      return message;
    }
  }
  if (caught instanceof TypeError && /fetch/i.test(caught.message)) {
    return "PAW’s local service is not available right now.";
  }
  return fallback;
}

async function deviceAllowsBackgroundOptimization(): Promise<boolean> {
  const connection = (
    navigator as Navigator & {
      connection?: { saveData?: boolean; effectiveType?: string };
    }
  ).connection;
  if (
    connection?.saveData ||
    ["slow-2g", "2g"].includes(connection?.effectiveType ?? "")
  ) {
    return false;
  }
  const getBattery = (
    navigator as Navigator & {
      getBattery?: () => Promise<{ charging: boolean; level: number }>;
    }
  ).getBattery;
  if (getBattery) {
    try {
      const battery = await getBattery.call(navigator);
      if (!battery.charging && battery.level < 0.2) return false;
    } catch {
      // Battery status is optional; keep the user preference.
    }
  }
  return true;
}

function normalizeQuestions(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      if (typeof item === "string") return item.trim();
      const question = asRecord(item);
      return firstString(question.question, question.text, question.title);
    })
    .filter((question): question is string => Boolean(question))
    .slice(0, 4);
}

function upsertTrip(trips: Trip[], nextTrip: Trip): Trip[] {
  const id = tripId(nextTrip);
  if (!id) return trips;
  const index = trips.findIndex((trip) => tripId(trip) === id);
  if (index === -1) return [nextTrip, ...trips];
  return trips.map((trip, tripIndex) =>
    tripIndex === index ? { ...trip, ...nextTrip } : trip,
  );
}

function turnsFromMessages(messages: ConversationMessage[]): AnswerTurn[] {
  const turns: AnswerTurn[] = [];
  let question = "";

  for (const message of messages) {
    if (message.role === "user") {
      question = message.content;
      continue;
    }
    if (!question) continue;

    const payload = asRecord(message.payload);
    const sources = normalizeSources(
      message.sources?.length ? message.sources : payload.sources,
    );
    const stale = payload.stale === true;
    const answerMode = firstString(payload.answer_mode);
    const branchSteps = Array.isArray(payload.branches)
      ? payload.branches
          .map((branch) => safeSemanticLabel(asRecord(branch).label))
          .filter(Boolean)
          .map((label) => `Checked ${label}`)
      : [];
    turns.push({
      id: message.message_id,
      question,
      answer: message.content,
      state: answerMode === "abstained" ? "abstained" : "complete",
      status: "",
      support: supportValue(payload.support),
      freshness:
        firstString(
          payload.freshness,
          ...sources.map((source) => source.freshness),
        ) || (stale ? "May be out of date" : null),
      sources,
      buildSteps: [
        ...branchSteps,
        ...(sources.length
          ? [
              `Combined ${sources.length} saved ${
                sources.length === 1 ? "source" : "sources"
              }`,
            ]
          : []),
      ],
      refined: payload.refined === true || payload.changed === true,
      startsNewTopic: payload.new_topic === true,
    });
    question = "";
  }
  return turns;
}

function coverageFromDiscovery(
  summary: DiscoverySummary,
  current: TripCoverage | null,
): TripCoverage {
  return {
    ...(current ?? {}),
    ...(summary.coverage ?? {}),
    ...(summary.sources?.length ? { sources: summary.sources } : {}),
    ...(summary.publishers ? { publishers: summary.publishers } : {}),
    ...(summary.freshness ? { freshness: summary.freshness } : {}),
    ...(typeof summary.size_bytes === "number"
      ? { size_bytes: summary.size_bytes }
      : {}),
    ...(typeof summary.estimated_size_bytes === "number"
      ? { estimated_size_bytes: summary.estimated_size_bytes }
      : {}),
    ...(typeof summary.preparation_time_estimate_s === "number"
      ? {
          preparation_time_estimate_s:
            summary.preparation_time_estimate_s,
        }
      : {}),
  };
}

function travelSourceIds(
  coverage: TripCoverage | null,
  trip: Trip,
): string[] {
  return coverageSources(coverage, trip).map(sourceId).filter(Boolean);
}

function dataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error(`Could not read ${file.name}.`));
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.readAsDataURL(file);
  });
}

async function attachmentFromFile(file: File): Promise<TripAttachment> {
  const textLike =
    file.type.startsWith("text/") ||
    /\.(txt|md|csv|json|ics)$/i.test(file.name);
  return {
    name: file.name,
    kind: "file",
    content: textLike ? await file.text() : await dataUrl(file),
    media_type: file.type || "application/octet-stream",
    size_bytes: file.size,
    encoding: textLike ? "utf-8" : "data-url",
  };
}

function jobState(job: TravelJob): string {
  return firstString(job.state, job.status)
    .toLowerCase()
    .replace(/[\s-]+/g, "_");
}

function jobProgress(job: TravelJob): number {
  const direct = firstNumber(
    job.progress_percent,
    job.percent,
    typeof job.progress === "number" ? job.progress : null,
  );
  if (direct !== null) {
    return Math.max(0, Math.min(100, direct <= 1 ? direct * 100 : direct));
  }
  const byState: Record<string, number> = {
    queued: 42,
    planning: 46,
    searching: 56,
    compiling: 52,
    downloading: 60,
    fetching: 60,
    processing_documents: 72,
    processing: 72,
    indexing: 82,
    testing: 92,
    finalizing: 95,
    ready: 100,
    completed: 100,
  };
  return byState[jobState(job)] ?? 45;
}

function jobStatus(job: TravelJob): string {
  const state = jobState(job);
  if (state === "planning" || state === "queued") {
    return "Planning offline coverage";
  }
  if (state === "searching") {
    return "Finding current official trip sources";
  }
  if (state === "downloading" || state === "fetching" || state === "compiling") {
    return "Saving trusted travel sources";
  }
  if (
    state === "processing_documents" ||
    state === "processing" ||
    state === "indexing"
  ) {
    return "Organizing trip information";
  }
  if (state === "testing" || state === "finalizing") {
    return "Checking offline answers";
  }
  return "Preparing your offline trip";
}

export default function App() {
  const [page, setPage] = useState<PrimaryPage>("ask");
  const [settings, setSettings] = useState<AppSettings>(defaultSettings);
  const [trips, setTrips] = useState<Trip[]>([]);
  const [activeTripId, setActiveTripId] = useState("");
  const [starters, setStarters] = useState<string[]>(fallbackStarters);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<Notice | null>(null);

  const [conversations, setConversations] = useState<
    Awaited<ReturnType<typeof api.conversations>>["conversations"]
  >([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [turns, setTurns] = useState<AnswerTurn[]>([]);
  const [composerText, setComposerText] = useState("");
  const [asking, setAsking] = useState(false);
  const [followUpDepth, setFollowUpDepth] = useState(0);
  const [nextStartsNewTopic, setNextStartsNewTopic] = useState(false);
  const askAbortRef = useRef<AbortController | null>(null);

  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [searchStatus, setSearchStatus] =
    useState<SearchProviderStatus>(defaultSearchStatus);

  const [prepareTrip, setPrepareTrip] = useState<Trip | null>(null);
  const [coverage, setCoverage] = useState<TripCoverage | null>(null);
  const [preparePhase, setPreparePhase] = useState<PreparePhase>("idle");
  const [prepareStatus, setPrepareStatus] = useState("Ready to prepare");
  const [prepareProgress, setPrepareProgress] = useState(0);
  const [prepareError, setPrepareError] = useState<string | null>(null);
  const [blockingQuestion, setBlockingQuestion] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const prepareRunRef = useRef(0);
  const lastPrepareRequestRef = useRef<PrepareRequest | null>(null);

  const activeTrip = useMemo(
    () => trips.find((trip) => tripId(trip) === activeTripId) ?? null,
    [activeTripId, trips],
  );

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = settings.theme;
    root.style.colorScheme =
      settings.theme === "system" ? "light dark" : settings.theme;
  }, [settings.theme]);

  useEffect(() => {
    return () => askAbortRef.current?.abort();
  }, []);

  useEffect(() => {
    let active = true;

    async function bootstrap() {
      try {
        const [settingsResult, tripsResult, conversationResult, searchResult] =
          await Promise.all([
            api.settings().catch(() => defaultSettings),
            api.trips(),
            api.conversations({ limit: 100 }).catch(() => ({
              conversations: [],
            })),
            api.searchStatus().catch(() => defaultSearchStatus),
          ]);
        if (!active) return;

        const tripList = tripsResult.trips.filter((trip) => tripId(trip));
        const configuredId = firstString(
          settingsResult.active_trip_id,
          settingsResult.active_context_id,
        );
        const selectedId = tripList.some(
          (trip) => tripId(trip) === configuredId,
        )
          ? configuredId
          : tripId(tripList[0]);

        let selectedTrip =
          tripList.find((trip) => tripId(trip) === selectedId) ?? null;
        let starterQuestions = fallbackStarters;
        if (selectedId) {
          const [detailResult, starterResult] = await Promise.allSettled([
            api.trip(selectedId),
            api.tripStarters(selectedId),
          ]);
          if (!active) return;
          if (detailResult.status === "fulfilled") {
            selectedTrip = detailResult.value;
          }
          if (starterResult.status === "fulfilled") {
            const parsed = normalizeQuestions(starterResult.value.questions);
            if (parsed.length) starterQuestions = parsed;
          }
        }

        const resolvedTrips = selectedTrip
          ? upsertTrip(tripList, selectedTrip)
          : tripList;
        setSettings(settingsResult);
        setTrips(resolvedTrips);
        setActiveTripId(selectedId);
        setStarters(starterQuestions);
        setConversations(conversationResult.conversations);
        setSearchStatus(searchResult);
        setPrepareTrip(selectedTrip);
        setCoverage(selectedTrip?.coverage ?? null);
        setPreparePhase(
          selectedTrip && isTripReady(selectedTrip) ? "ready" : "idle",
        );
        setPrepareProgress(
          selectedTrip && isTripReady(selectedTrip) ? 100 : 0,
        );
      } catch (caught) {
        if (active) {
          setNotice({
            tone: "error",
            message: userFacingError(
              caught,
              "PAW could not open your saved trips.",
            ),
          });
        }
      } finally {
        if (active) setLoading(false);
      }
    }

    void bootstrap();
    return () => {
      active = false;
    };
  }, []);

  const refreshConversations = useCallback(async (query = "") => {
    setHistoryLoading(true);
    try {
      const result = await api.conversations({
        query: query.trim() || undefined,
        limit: 100,
      });
      setConversations(result.conversations);
    } catch (caught) {
      setNotice({
        tone: "error",
        message: userFacingError(caught, "Could not load conversation history."),
      });
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  async function activateTrip(id: string) {
    if (!id || id === activeTripId) return;
    setActiveTripId(id);
    setConversationId(null);
    setTurns([]);
    setFollowUpDepth(0);
    setNextStartsNewTopic(false);
    setComposerText("");

    const listedTrip = trips.find((trip) => tripId(trip) === id) ?? null;
    try {
      const [detailResult, starterResult] = await Promise.allSettled([
        api.trip(id),
        api.tripStarters(id),
      ]);
      const detail =
        detailResult.status === "fulfilled" ? detailResult.value : listedTrip;
      if (detail) {
        setTrips((current) => upsertTrip(current, detail));
        setPrepareTrip(detail);
        setCoverage(detail.coverage ?? null);
        setPreparePhase(isTripReady(detail) ? "ready" : "idle");
        setPrepareProgress(isTripReady(detail) ? 100 : 0);
      }
      const questions =
        starterResult.status === "fulfilled"
          ? normalizeQuestions(starterResult.value.questions)
          : [];
      setStarters(questions.length ? questions : fallbackStarters);
      void api
        .updateSettings({ active_context_id: id })
        .then(setSettings)
        .catch(() => undefined);
    } catch (caught) {
      setNotice({
        tone: "error",
        message: userFacingError(caught, "Could not switch trips."),
      });
    }
  }

  function updateTurn(
    id: string,
    update: (turn: AnswerTurn) => AnswerTurn,
  ) {
    setTurns((current) =>
      current.map((turn) => (turn.id === id ? update(turn) : turn)),
    );
  }

  async function submitQuestion(rawQuestion: string) {
    const question = rawQuestion.trim();
    const currentTrip = activeTrip;
    const currentTripId = tripId(currentTrip);
    if (!question || !currentTrip || !currentTripId || asking) return;

    const hadPrevious = turns.length > 0;
    const startsNewTopic =
      hadPrevious &&
      (nextStartsNewTopic || followUpDepth >= MAX_FOLLOW_UPS);
    const turnId = `answer-${Date.now()}-${Math.random()
      .toString(36)
      .slice(2)}`;
    setTurns((current) => [
      ...current,
      {
        id: turnId,
        question,
        answer: "",
        state: "working",
        status: "Checking your trip",
        sources: [],
        buildSteps: [],
        refined: false,
        startsNewTopic,
      },
    ]);
    setComposerText("");
    setAsking(true);
    setNotice(null);

    let streamConversationId = conversationId;
    if (!streamConversationId) {
      try {
        const created = await api.createConversation({
          context_id: currentTripId,
          title: question.slice(0, 80),
        });
        streamConversationId = created.conversation_id;
        setConversationId(created.conversation_id);
      } catch {
        // The ask endpoint can create and persist a conversation itself.
      }
    }

    const input = {
      text: question,
      trip_id: currentTripId,
      ...(streamConversationId
        ? { conversation_id: streamConversationId }
        : {}),
      ...(startsNewTopic ? { new_topic: true } : {}),
    };

    let branchStarted = 0;
    let branchComplete = 0;
    let terminal = false;
    let succeeded = false;

    const setWorkingStatus = (status: string) => {
      updateTurn(turnId, (turn) => ({
        ...turn,
        status,
        buildSteps: appendUnique(turn.buildSteps, status),
      }));
    };

    const finishFromResponse = (response: AskResponse) => {
      const answer = firstString(response.answer, response.message);
      const sources = normalizeSources(response.sources);
      updateTurn(turnId, (turn) => ({
        ...turn,
        answer:
          answer ||
          turn.answer ||
          "I don’t have enough saved information to answer that reliably.",
        state: answer || turn.answer ? "complete" : "abstained",
        status: "",
        support: supportValue(response.support) ?? turn.support,
        freshness:
          firstString(response.freshness) ||
          (response.stale ? "May be out of date" : turn.freshness),
        sources: sources.length ? sources : turn.sources,
        buildSteps:
          sources.length || turn.sources.length
            ? appendUnique(
                turn.buildSteps,
                `Combined ${
                  sources.length || turn.sources.length
                } saved ${
                  (sources.length || turn.sources.length) === 1
                    ? "source"
                    : "sources"
                }`,
              )
            : turn.buildSteps,
        refined:
          turn.refined ||
          response.refined === true ||
          response.changed === true ||
          Boolean(answer && turn.answer && answer !== turn.answer),
      }));
      if (response.conversation_id) {
        setConversationId(response.conversation_id);
      }
      terminal = true;
      succeeded = true;
    };

    const handleEvent = (event: AskStreamEvent) => {
      const returnedConversationId = eventString(
        event,
        "conversation_id",
      );
      if (returnedConversationId) {
        setConversationId(returnedConversationId);
      }

      switch (event.type) {
        case "route":
          setWorkingStatus("Checking your schedule");
          break;
        case "branch_started":
          branchStarted += 1;
          setWorkingStatus(
            semanticSourceStatus(event, branchStarted, currentTrip),
          );
          break;
        case "branch_complete": {
          branchComplete += 1;
          const count =
            firstNumber(
              event.source_count,
              event.completed_count,
              event.count,
            ) ?? branchComplete;
          setWorkingStatus(
            `Combining ${Math.max(1, Math.round(count))} local ${
              Math.round(count) === 1 ? "source" : "sources"
            }`,
          );
          break;
        }
        case "answer_update": {
          const delta = eventString(event, "delta");
          const full = eventString(
            event,
            "answer",
            "text",
            "content",
            "partial_answer",
          );
          updateTurn(turnId, (turn) => ({
            ...turn,
            answer: delta ? `${turn.answer}${delta}` : full || turn.answer,
            status:
              branchComplete > 0
                ? `Combining ${branchComplete} local ${
                    branchComplete === 1 ? "source" : "sources"
                  }`
                : "Building your answer",
          }));
          break;
        }
        case "final": {
          const answer = eventString(
            event,
            "answer",
            "text",
            "content",
            "message",
          );
          const sources = eventSources(event);
          const support = supportValue(
            event.support ?? eventResult(event).support,
          );
          const freshness =
            event.requires_refresh === true
              ? "Check latest when online"
              : eventFreshness(event);
          updateTurn(turnId, (turn) => {
            const finalAnswer = answer || turn.answer;
            const sourceCount = sources.length || turn.sources.length;
            return {
              ...turn,
              answer:
                finalAnswer ||
                "I don’t have enough saved information to answer that reliably.",
              state: finalAnswer ? "complete" : "abstained",
              status: "",
              support: support ?? turn.support,
              freshness: freshness || turn.freshness,
              sources: sources.length ? sources : turn.sources,
              buildSteps: sourceCount
                ? appendUnique(
                    turn.buildSteps,
                    `Combined ${sourceCount} saved ${
                      sourceCount === 1 ? "source" : "sources"
                    }`,
                  )
                : turn.buildSteps,
              refined:
                turn.refined ||
                event.refined === true ||
                event.changed === true ||
                Boolean(answer && turn.answer && answer !== turn.answer),
            };
          });
          terminal = true;
          succeeded = true;
          break;
        }
        case "abstain":
          updateTurn(turnId, (turn) => ({
            ...turn,
            answer:
              eventString(event, "answer") ||
              "I don’t have enough saved information to answer that reliably.",
            state: "abstained",
            status: "",
          }));
          terminal = true;
          succeeded = true;
          break;
      }
    };

    const controller = new AbortController();
    askAbortRef.current = controller;
    try {
      await streamAsk(input, handleEvent, controller.signal);
      if (!terminal) {
        updateTurn(turnId, (turn) => ({
          ...turn,
          answer:
            turn.answer ||
            "I couldn’t finish that answer. Please try the question again.",
          state: turn.answer ? "complete" : "error",
          status: "",
        }));
        succeeded = true;
      }
    } catch (caught) {
      const canFallback =
        caught instanceof ApiError &&
        [404, 405, 406, 415, 501].includes(caught.status);
      if (canFallback) {
        try {
          finishFromResponse(await api.ask(input));
        } catch (fallbackError) {
          updateTurn(turnId, (turn) => ({
            ...turn,
            answer: userFacingError(
              fallbackError,
              "I couldn’t answer that from the saved trip right now.",
            ),
            state: "error",
            status: "",
          }));
        }
      } else if ((caught as Error)?.name !== "AbortError") {
        updateTurn(turnId, (turn) => ({
          ...turn,
          answer: userFacingError(
            caught,
            "I couldn’t answer that from the saved trip right now.",
          ),
          state: "error",
          status: "",
        }));
      }
    } finally {
      askAbortRef.current = null;
      setAsking(false);
      if (succeeded) {
        if (!hadPrevious || startsNewTopic) {
          setFollowUpDepth(0);
          setNextStartsNewTopic(false);
        } else {
          const nextDepth = Math.min(
            MAX_FOLLOW_UPS,
            followUpDepth + 1,
          );
          setFollowUpDepth(nextDepth);
          setNextStartsNewTopic(nextDepth >= MAX_FOLLOW_UPS);
        }
      }
      void refreshConversations();
    }
  }

  async function selectConversation(targetId: string) {
    try {
      const conversation = await api.conversation(targetId);
      const targetTripId = firstString(
        conversation.trip_id,
        conversation.context_id,
      );
      if (targetTripId && targetTripId !== activeTripId) {
        await activateTrip(targetTripId);
      }
      setConversationId(conversation.conversation_id);
      setTurns(turnsFromMessages(conversation.messages));
      setFollowUpDepth(0);
      setNextStartsNewTopic(false);
      setPage("ask");
      setHistoryOpen(false);
    } catch (caught) {
      setNotice({
        tone: "error",
        message: userFacingError(caught, "Could not open that conversation."),
      });
    }
  }

  function newConversation() {
    setConversationId(null);
    setTurns([]);
    setFollowUpDepth(0);
    setNextStartsNewTopic(false);
    setComposerText("");
    setPage("ask");
    setHistoryOpen(false);
  }

  async function deleteConversation(targetId: string) {
    await api.deleteConversation(targetId);
    if (targetId === conversationId) newConversation();
    await refreshConversations();
  }

  async function startTripJob(
    targetTrip: Trip,
    selectedSourceIds: string[],
    run: number,
  ) {
    const id = tripId(targetTrip);
    if (!id || run !== prepareRunRef.current) return;
    setPreparePhase("starting");
    setPrepareStatus("Starting offline preparation");
    setPrepareProgress(38);
    const optimize =
      (settings.optimize_in_background ?? true) &&
      (await deviceAllowsBackgroundOptimization());
    const started = await api.prepareTrip(id, {
      ...(selectedSourceIds.length
        ? { source_ids: selectedSourceIds }
        : {}),
      optimize,
    });
    if (run !== prepareRunRef.current) return;
    setJobId(started.job_id);
    setPreparePhase("preparing");
    setPrepareStatus("Planning offline coverage");
    setPrepareProgress(42);
    const preparingTrip = { ...targetTrip, status: "preparing" };
    setPrepareTrip(preparingTrip);
    setTrips((current) => upsertTrip(current, preparingTrip));
  }

  async function discoverAndPrepare(
    targetTrip: Trip,
    initialCoverage: TripCoverage | null,
    run: number,
  ) {
    const id = tripId(targetTrip);
    if (!id || run !== prepareRunRef.current) return;
    setPreparePhase("discovering");
    setPrepareStatus("Finding trusted travel sources");
    setPrepareProgress(22);

    const discovery = await api.discoverTrip(id);
    if (run !== prepareRunRef.current) return;
    if (
      discovery.gaps?.length &&
      !(discovery.sources?.length)
    ) {
      setNotice({
        tone: "info",
        message:
          discovery.gaps[0]?.message ||
          "Current public search was unavailable; using your saved trip information.",
      });
    }
    const discoveredTrip = discovery.trip
      ? { ...targetTrip, ...discovery.trip }
      : targetTrip;
    const nextCoverage = coverageFromDiscovery(discovery, initialCoverage);
    setPrepareTrip(discoveredTrip);
    setCoverage(nextCoverage);
    setTrips((current) => upsertTrip(current, discoveredTrip));
    await startTripJob(
      discoveredTrip,
      travelSourceIds(nextCoverage, discoveredTrip),
      run,
    );
  }

  async function prepareFromDescription(request: PrepareRequest) {
    const run = ++prepareRunRef.current;
    lastPrepareRequestRef.current = request;
    setPrepareError(null);
    setBlockingQuestion(null);
    setPreparePhase("parsing");
    setPrepareStatus("Understanding your trip");
    setPrepareProgress(8);

    try {
      const fileAttachments = await Promise.all(
        request.files.map(attachmentFromFile),
      );
      if (run !== prepareRunRef.current) return;
      const attachments: TripAttachment[] = [
        ...(request.note
          ? [
              {
                name: "Travel notes",
                kind: "text" as const,
                content: request.note,
                encoding: "utf-8" as const,
              },
            ]
          : []),
        ...fileAttachments,
      ];
      const parsed = await api.parseTrip(request.text, attachments);
      if (run !== prepareRunRef.current) return;
      const parsedTrip = parsed.trip;
      const id = tripId(parsedTrip);
      if (!id) throw new Error("The parsed trip did not include an ID.");

      const parsedCoverage = mergeCoverage(
        parsedTrip.coverage,
        parsed.coverage,
      );
      setPrepareTrip(parsedTrip);
      setCoverage(parsedCoverage);
      setTrips((current) => upsertTrip(current, parsedTrip));
      setActiveTripId(id);
      void api
        .updateSettings({ active_context_id: id })
        .then(setSettings)
        .catch(() => undefined);

      if (parsed.blocking_question) {
        setBlockingQuestion(parsed.blocking_question);
        setPreparePhase("needs_input");
        setPrepareStatus("One trip detail is needed");
        setPrepareProgress(15);
        return;
      }
      await discoverAndPrepare(parsedTrip, parsedCoverage, run);
    } catch (caught) {
      if (run !== prepareRunRef.current) return;
      setPreparePhase("failed");
      setPrepareStatus("Preparation stopped");
      setPrepareError(
        userFacingError(caught, "PAW couldn’t prepare that trip. Please try again."),
      );
    }
  }

  function clarifyTrip(answer: string) {
    const lastRequest = lastPrepareRequestRef.current;
    if (!lastRequest) return;
    const id = tripId(prepareTrip);
    if (!id || !prepareTrip) {
      void prepareFromDescription({
        ...lastRequest,
        text: `${lastRequest.text}\n\nAdditional detail: ${answer}`,
      });
      return;
    }
    const update: TripPatch = tripDestination(prepareTrip)
      ? { event: answer }
      : { destination: answer };
    const run = ++prepareRunRef.current;
    setBlockingQuestion(null);
    setPreparePhase("discovering");
    void api
      .updateTrip(id, update)
      .then((saved) => {
        if (run !== prepareRunRef.current) return;
        setPrepareTrip(saved);
        setTrips((current) => upsertTrip(current, saved));
        return discoverAndPrepare(saved, saved.coverage ?? coverage, run);
      })
      .catch((caught) => {
        setPreparePhase("failed");
        setPrepareError(
          userFacingError(caught, "PAW couldn’t apply that trip detail."),
        );
      });
  }

  async function saveTrip(id: string, update: TripPatch) {
    const saved = await api.updateTrip(id, update);
    setPrepareTrip(saved);
    setTrips((current) => upsertTrip(current, saved));
    setNotice({ tone: "success", message: "Trip brief saved." });
  }

  function reprepare(sourceIds: string[]) {
    if (!prepareTrip) return;
    const run = ++prepareRunRef.current;
    setPrepareError(null);
    setBlockingQuestion(null);
    const selectedCoverage = {
      ...(coverage ?? {}),
      sources: coverageSources(coverage, prepareTrip).filter((source) =>
        sourceIds.includes(sourceId(source)),
      ),
    };
    void discoverAndPrepare(prepareTrip, selectedCoverage, run).catch((caught) => {
      if (run !== prepareRunRef.current) return;
      setPreparePhase("failed");
      setPrepareStatus("Preparation stopped");
      setPrepareError(
        userFacingError(caught, "PAW couldn’t update the offline guide."),
      );
    });
  }

  function cancelPreparation() {
    prepareRunRef.current += 1;
    const currentJobId = jobId;
    setJobId(null);
    setPreparePhase("cancelled");
    setPrepareStatus("Preparation cancelled");
    setPrepareError(null);
    if (currentJobId) void api.cancelJob(currentJobId).catch(() => undefined);
  }

  function startNewTrip() {
    setPrepareTrip(null);
    setCoverage(null);
    setPreparePhase("idle");
    setPrepareStatus("Ready to prepare");
    setPrepareProgress(0);
    setPrepareError(null);
    setBlockingQuestion(null);
    setJobId(null);
  }

  useEffect(() => {
    if (!jobId) return;
    const currentJobId = jobId;
    let active = true;
    let timer: number | null = null;
    const expectedTripId = tripId(prepareTrip);

    async function poll() {
      try {
        const job = await api.tripJob(currentJobId);
        if (!active) return;
        const state = jobState(job);
        const targetTripId = firstString(
          job.trip_id,
          job.context_id,
          expectedTripId,
        );
        if (state === "ready" || state === "completed") {
          setPreparePhase("ready");
          setPrepareStatus("Ready Offline");
          setPrepareProgress(100);
          setJobId(null);
          let readyTrip: Trip | null = prepareTrip
            ? {
                ...prepareTrip,
                status: "ready",
                ready_offline: true,
              }
            : null;
          if (targetTripId) {
            try {
              readyTrip = await api.trip(targetTripId);
            } catch {
              // The optimistic ready state still reflects the completed job.
            }
          }
          if (readyTrip) {
            setPrepareTrip(readyTrip);
            setTrips((current) => upsertTrip(current, readyTrip as Trip));
            setCoverage((current) =>
              mergeCoverage(current, (readyTrip as Trip).coverage),
            );
          }
          if (targetTripId) {
            void api
              .tripStarters(targetTripId)
              .then((result) => {
                const questions = normalizeQuestions(result.questions);
                if (questions.length) setStarters(questions);
              })
              .catch(() => undefined);
          }
          return;
        }
        if (state === "failed") {
          setPreparePhase("failed");
          setPrepareStatus("Preparation stopped");
          setPrepareError(
            "The offline guide couldn’t be completed. You can try again.",
          );
          setJobId(null);
          return;
        }
        if (state === "cancelled" || state === "canceled") {
          setPreparePhase("cancelled");
          setPrepareStatus("Preparation cancelled");
          setJobId(null);
          return;
        }

        setPreparePhase("preparing");
        setPrepareStatus(jobStatus(job));
        setPrepareProgress(jobProgress(job));
        timer = window.setTimeout(poll, 1_200);
      } catch (caught) {
        if (!active) return;
        setPreparePhase("failed");
        setPrepareStatus("Preparation stopped");
        setPrepareError(
          userFacingError(caught, "Could not check preparation progress."),
        );
        setJobId(null);
      }
    }

    void poll();
    return () => {
      active = false;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [jobId]);

  async function saveSettings(update: SettingsUpdate) {
    const next = await api.updateSettings(update);
    setSettings(next);
    setNotice({ tone: "success", message: "Settings saved." });
  }

  function changePage(nextPage: PrimaryPage) {
    if (nextPage === "prepare" && !prepareTrip && activeTrip) {
      setPrepareTrip(activeTrip);
      setCoverage(activeTrip.coverage ?? null);
      setPreparePhase(isTripReady(activeTrip) ? "ready" : "idle");
      setPrepareProgress(isTripReady(activeTrip) ? 100 : 0);
    }
    setPage(nextPage);
  }

  if (loading) {
    return (
      <div className="app-shell app-shell--loading">
        <div className="startup-state" role="status">
          <img src="/favicon.svg" alt="" />
          <span>Opening your travel guide…</span>
        </div>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <TravelHeader
        page={page}
        onPageChange={changePage}
        onOpenHistory={() => setHistoryOpen(true)}
        onOpenSettings={() => setSettingsOpen(true)}
      />

      <main className="app-main">
        {notice ? (
          <div
            className={`app-notice app-notice--${notice.tone}`}
            role={notice.tone === "error" ? "alert" : "status"}
          >
            <span>{notice.message}</span>
            <button
              type="button"
              aria-label="Dismiss notification"
              onClick={() => setNotice(null)}
            >
              ×
            </button>
          </div>
        ) : null}

        {page === "ask" ? (
          <AskPage
            trips={trips}
            activeTrip={activeTrip}
            starters={starters}
            turns={turns}
            value={composerText}
            asking={asking}
            nextStartsNewTopic={nextStartsNewTopic}
            nextFollowUp={Math.min(MAX_FOLLOW_UPS, followUpDepth + 1)}
            maxFollowUps={MAX_FOLLOW_UPS}
            onTripChange={(id) => {
              void activateTrip(id);
            }}
            onValueChange={setComposerText}
            onSubmit={(question) => {
              void submitQuestion(question);
            }}
            onNewTopic={() => setNextStartsNewTopic(true)}
            onPrepare={() => changePage("prepare")}
          />
        ) : (
          <PreparePage
            trip={prepareTrip}
            coverage={coverage}
            phase={preparePhase}
            statusText={prepareStatus}
            progress={prepareProgress}
            error={prepareError}
            blockingQuestion={blockingQuestion}
            onSubmit={(request) => {
              void prepareFromDescription(request);
            }}
            onClarify={clarifyTrip}
            onSaveTrip={saveTrip}
            onCancel={cancelPreparation}
            onNewTrip={startNewTrip}
            onReprepare={reprepare}
          />
        )}
      </main>

      {historyOpen ? (
        <HistoryDrawer
          conversations={conversations}
          activeConversationId={conversationId}
          loading={historyLoading}
          onClose={() => setHistoryOpen(false)}
          onSearch={refreshConversations}
          onSelect={(id) => {
            void selectConversation(id);
          }}
          onNew={newConversation}
          onDelete={deleteConversation}
        />
      ) : null}

      {settingsOpen ? (
        <PreferencesSheet
          settings={settings}
          searchStatus={searchStatus}
          onClose={() => setSettingsOpen(false)}
          onSave={saveSettings}
          onSaveSearchKey={async (key) => {
            const status = await api.saveSearchKey(key);
            setSearchStatus(status);
          }}
          onDeleteSearchKey={async () => {
            const status = await api.deleteSearchKey();
            setSearchStatus(status);
          }}
        />
      ) : null}
    </div>
  );
}
