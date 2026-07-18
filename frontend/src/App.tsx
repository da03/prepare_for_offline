import { useCallback, useEffect, useRef, useState } from "react";
import { AppHeader, type PrimaryPage } from "./components/AppHeader";
import {
  AskPage,
  type AnswerTurn,
  type FollowUpTarget,
} from "./components/AskPage";
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
} from "./lib/api";

function messageTurns(messages: ConversationMessage[]): AnswerTurn[] {
  const turns: AnswerTurn[] = [];
  let question: ConversationMessage | null = null;
  for (const message of messages) {
    if (message.role === "user") {
      question = message;
    } else if (question) {
      turns.push({
        id: message.message_id,
        question: question.content,
        answer: message.content,
        state: "complete",
        status: "",
        refined: message.payload.refined === true,
        support:
          typeof message.payload.support === "string"
            ? message.payload.support
            : undefined,
        sourceLabel: factualPackTitle(message.payload),
        conversationId: message.conversation_id,
        answerMessageId: message.message_id,
        isFollowUp:
          typeof question.payload.reply_to_message_id === "string",
      });
      question = null;
    }
  }
  return turns;
}

function factualPackTitle(payload: Record<string, unknown>): string | undefined {
  const trace = payload.trace;
  if (trace && typeof trace === "object" && "factual_pack" in trace) {
    const pack = (trace as { factual_pack?: { pack_title?: unknown } })
      .factual_pack;
    if (pack && typeof pack.pack_title === "string") return pack.pack_title;
  }
  return undefined;
}

function friendlyError(caught: unknown): string {
  if (caught instanceof ApiError && caught.message.length < 180) {
    return caught.message;
  }
  return "PAW Offline could not complete that request.";
}

export default function App() {
  const [page, setPage] = useState<PrimaryPage>("ask");
  const [programs, setPrograms] = useState<PreparedProgram[]>([]);
  const [turns, setTurns] = useState<AnswerTurn[]>([]);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [followUpTarget, setFollowUpTarget] =
    useState<FollowUpTarget | null>(null);
  const [activeConversationId, setActiveConversationId] = useState<
    string | null
  >(null);
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
    void Promise.all([api.programs(), api.conversations()])
      .then(([programResult, historyResult]) => {
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
    const target = followUpTarget;
    setQuestion("");
    setAsking(true);
    setFollowUpTarget(null);
    setTurns((current) => [
      ...current,
      {
        id,
        question: text,
        answer: "",
        state: "working",
        status: "Thinking…",
        refined: false,
        isFollowUp: target !== null,
      },
    ]);
    const controller = new AbortController();
    askAbort.current = controller;
    try {
      await streamAsk(
        {
          text,
          ...(target
            ? { reply_to_message_id: target.messageId }
            : {}),
        },
        (event) => {
          if (event.conversation_id) {
            setActiveConversationId(event.conversation_id);
          }
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
              support:
                typeof event.support === "string" ? event.support : turn.support,
              sourceLabel: factualPackTitle(
                event as unknown as Record<string, unknown>,
              ),
              conversationId: event.conversation_id,
              answerMessageId: event.message_id,
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
    setActiveConversationId(id);
    setTurns(messageTurns(conversation.messages));
    setFollowUpTarget(null);
    setHistoryOpen(false);
    setPage("ask");
  }

  function beginFollowUp(turn: AnswerTurn) {
    if (!turn.answerMessageId) return;
    setFollowUpTarget({
      messageId: turn.answerMessageId,
      question: turn.question,
    });
    setPage("ask");
  }

  async function deleteConversation(id: string) {
    await api.deleteConversation(id);
    setTurns((current) =>
      current.filter((turn) => turn.conversationId !== id),
    );
    if (id === activeConversationId) {
      setActiveConversationId(null);
      setFollowUpTarget(null);
    }
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
            turns={turns}
            value={question}
            asking={asking}
            followUpTarget={followUpTarget}
            onValueChange={setQuestion}
            onSubmit={(value) => void ask(value)}
            onFollowUp={beginFollowUp}
            onCancelFollowUp={() => setFollowUpTarget(null)}
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
          activeConversationId={activeConversationId}
          loading={historyLoading}
          onClose={() => setHistoryOpen(false)}
          onSearch={(value) => void refreshHistory(value)}
          onSelect={(id) => void selectConversation(id)}
          onDelete={deleteConversation}
        />
      ) : null}
    </div>
  );
}
