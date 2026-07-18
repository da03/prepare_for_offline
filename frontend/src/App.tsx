import { useCallback, useEffect, useRef, useState } from "react";
import { AppHeader, type PrimaryPage } from "./components/AppHeader";
import { AskPage, type AnswerTurn } from "./components/AskPage";
import { HistoryDrawer } from "./components/HistoryDrawer";
import { PreparePage } from "./components/PreparePage";
import {
  ApiError,
  api,
  streamAsk,
  type ConversationMessage,
  type ConversationSummary,
  type NeuralJob,
  type PreparedProgram,
  type StarterQuestion,
} from "./lib/api";

function messageTurns(messages: ConversationMessage[]): AnswerTurn[] {
  const turns: AnswerTurn[] = [];
  let question = "";
  for (const message of messages) {
    if (message.role === "user") {
      question = message.content;
    } else if (question) {
      turns.push({
        id: message.message_id,
        question,
        answer: message.content,
        state: "complete",
        status: "",
        refined: message.payload.refined === true,
        startsNewTopic: message.payload.new_topic === true,
      });
      question = "";
    }
  }
  return turns;
}

function friendlyError(caught: unknown): string {
  if (caught instanceof ApiError && caught.message.length < 180) {
    return caught.message;
  }
  return "PAW Offline could not complete that request.";
}

export default function App() {
  const [page, setPage] = useState<PrimaryPage>("ask");
  const [starters, setStarters] = useState<StarterQuestion[]>([]);
  const [programs, setPrograms] = useState<PreparedProgram[]>([]);
  const [turns, setTurns] = useState<AnswerTurn[]>([]);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [nextStartsNewTopic, setNextStartsNewTopic] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [job, setJob] = useState<NeuralJob | null>(null);
  const [prepareError, setPrepareError] = useState<string | null>(null);
  const askAbort = useRef<AbortController | null>(null);

  const refreshPrograms = useCallback(async () => {
    setPrograms((await api.programs()).programs);
  }, []);

  const refreshHistory = useCallback(async (query = "") => {
    setHistoryLoading(true);
    try {
      setConversations((await api.conversations(query)).conversations);
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    void Promise.all([api.starters(), api.programs(), api.conversations()])
      .then(([starterResult, programResult, historyResult]) => {
        setStarters(starterResult.starters);
        setPrograms(programResult.programs);
        setConversations(historyResult.conversations);
      })
      .catch(() => undefined);
    return () => askAbort.current?.abort();
  }, []);

  function updateTurn(
    id: string,
    update: (turn: AnswerTurn) => AnswerTurn,
  ) {
    setTurns((current) =>
      current.map((turn) => (turn.id === id ? update(turn) : turn)),
    );
  }

  async function ask(raw: string) {
    const text = raw.trim();
    if (!text || asking) return;
    const id = `turn-${Date.now()}`;
    const startsNewTopic = nextStartsNewTopic;
    setQuestion("");
    setAsking(true);
    setNextStartsNewTopic(false);
    setTurns((current) => [
      ...current,
      {
        id,
        question: text,
        answer: "",
        state: "working",
        status: "Thinking…",
        refined: false,
        startsNewTopic,
      },
    ]);
    const controller = new AbortController();
    askAbort.current = controller;
    try {
      await streamAsk(
        {
          text,
          ...(conversationId ? { conversation_id: conversationId } : {}),
          ...(startsNewTopic ? { new_topic: true } : {}),
        },
        (event) => {
          if (event.conversation_id) setConversationId(event.conversation_id);
          if (event.type === "answer_update" && event.answer) {
            updateTurn(id, (turn) => ({
              ...turn,
              answer: event.answer || turn.answer,
              status: "Thinking…",
            }));
          } else if (
            ["route", "specialist_started", "specialist_complete"].includes(
              event.type,
            )
          ) {
            updateTurn(id, (turn) => ({ ...turn, status: "Thinking…" }));
          } else if (event.type === "final") {
            updateTurn(id, (turn) => ({
              ...turn,
              answer: event.answer || turn.answer,
              state: "complete",
              status: "",
              refined: event.refined === true,
            }));
          }
        },
        controller.signal,
      );
      await refreshHistory();
    } catch (caught) {
      if (!controller.signal.aborted) {
        updateTurn(id, (turn) => ({
          ...turn,
          state: "error",
          status: friendlyError(caught),
        }));
      }
    } finally {
      if (askAbort.current === controller) askAbort.current = null;
      setAsking(false);
    }
  }

  async function selectConversation(id: string) {
    const conversation = await api.conversation(id);
    setConversationId(id);
    setTurns(messageTurns(conversation.messages));
    setHistoryOpen(false);
    setPage("ask");
  }

  function newConversation() {
    askAbort.current?.abort();
    setConversationId(null);
    setTurns([]);
    setQuestion("");
    setNextStartsNewTopic(false);
    setHistoryOpen(false);
    setPage("ask");
  }

  async function deleteConversation(id: string) {
    await api.deleteConversation(id);
    if (id === conversationId) newConversation();
    await refreshHistory();
  }

  async function prepare(prompt: string) {
    setPrepareError(null);
    try {
      setJob(await api.prepareProgram(prompt));
    } catch (caught) {
      setPrepareError(friendlyError(caught));
    }
  }

  useEffect(() => {
    if (!job || ["ready", "failed", "cancelled"].includes(job.state)) return;
    let active = true;
    const timer = window.setInterval(() => {
      void api
        .job(job.job_id)
        .then(async (next) => {
          if (!active) return;
          setJob(next);
          if (next.state === "ready") await refreshPrograms();
          if (next.state === "failed") {
            setPrepareError(next.error || "Preparation failed.");
          }
        })
        .catch((caught) => {
          if (active) setPrepareError(friendlyError(caught));
        });
    }, 1000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [job, refreshPrograms]);

  async function cancelPreparation() {
    if (!job) return;
    await api.cancelJob(job.job_id);
    setJob({ ...job, state: "cancelled", message: "Cancelled" });
  }

  async function removeProgram(programKey: string) {
    await api.deleteProgram(programKey);
    await refreshPrograms();
  }

  return (
    <div className="app-shell">
      <AppHeader
        page={page}
        onPageChange={setPage}
        onOpenHistory={() => setHistoryOpen(true)}
      />
      <main className="app-main">
        {page === "ask" ? (
          <AskPage
            starters={starters}
            turns={turns}
            value={question}
            asking={asking}
            nextStartsNewTopic={nextStartsNewTopic}
            onValueChange={setQuestion}
            onSubmit={(value) => void ask(value)}
            onNewTopic={() => setNextStartsNewTopic(true)}
          />
        ) : (
          <PreparePage
            programs={programs}
            job={job}
            error={prepareError}
            onPrepare={prepare}
            onCancel={cancelPreparation}
            onRemove={removeProgram}
          />
        )}
      </main>
      {historyOpen ? (
        <HistoryDrawer
          conversations={conversations}
          activeConversationId={conversationId}
          loading={historyLoading}
          onClose={() => setHistoryOpen(false)}
          onSearch={(value) => void refreshHistory(value)}
          onSelect={(id) => void selectConversation(id)}
          onNew={newConversation}
          onDelete={deleteConversation}
        />
      ) : null}
    </div>
  );
}
