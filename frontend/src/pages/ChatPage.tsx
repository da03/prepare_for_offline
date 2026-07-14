import { useState } from "react";
import { api, ChatResponse } from "../lib/api";

interface Turn {
  question: string;
  response?: ChatResponse;
  error?: string;
  loading?: boolean;
}

const SUPPORT_STYLE: Record<string, string> = {
  high: "bg-emerald-100 text-emerald-800",
  medium: "bg-amber-100 text-amber-800",
  low: "bg-rose-100 text-rose-800",
};

const MODE_LABEL: Record<string, string> = {
  answer_card: "answer card",
  structured_fact: "local fact",
  generated_from_local_sources: "generated from local sources",
  abstained: "abstained",
};

function ResponseCard({ r }: { r: ChatResponse }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <p className="whitespace-pre-wrap text-[15px] leading-relaxed">{r.answer}</p>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
        <span className={`rounded-full px-2 py-0.5 font-medium ${SUPPORT_STYLE[r.support]}`}>
          support: {r.support}
        </span>
        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-600">
          {MODE_LABEL[r.answer_mode]}
        </span>
        {r.stale && (
          <span className="rounded-full bg-yellow-100 px-2 py-0.5 text-yellow-800">
            possibly stale
          </span>
        )}
        {r.queued_for_verification && (
          <span className="rounded-full bg-indigo-100 px-2 py-0.5 text-indigo-800">
            queued to verify online
          </span>
        )}
      </div>
      {r.sources.length > 0 && (
        <div className="mt-3 border-t border-slate-100 pt-2 text-xs text-slate-500">
          <span className="font-medium">Local sources: </span>
          {r.sources.map((s) => s.source_id).join(", ")}
        </div>
      )}
    </div>
  );
}

export default function ChatPage() {
  const [q, setQ] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);

  async function ask(e: React.FormEvent) {
    e.preventDefault();
    const question = q.trim();
    if (!question || busy) return;
    setBusy(true);
    setQ("");
    setTurns((t) => [...t, { question, loading: true }]);
    try {
      const response = await api.chat(question);
      setTurns((t) =>
        t.map((turn, i) => (i === t.length - 1 ? { question, response } : turn))
      );
    } catch (err: any) {
      setTurns((t) =>
        t.map((turn, i) =>
          i === t.length - 1 ? { question, error: String(err.message || err) } : turn
        )
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      {turns.length === 0 && (
        <div className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-center text-slate-500">
          <p className="text-sm">
            Ask anything from your offline pack. Try{" "}
            <button
              className="font-medium text-indigo-600 underline"
              onClick={() => setQ("What does simida mean?")}
            >
              "What does simida mean?"
            </button>
          </p>
        </div>
      )}

      {turns.map((turn, i) => (
        <div key={i} className="space-y-2">
          <div className="flex justify-end">
            <div className="max-w-[85%] rounded-2xl bg-ink px-4 py-2 text-sm text-white">
              {turn.question}
            </div>
          </div>
          {turn.loading && <div className="text-sm text-slate-400">Thinking locally…</div>}
          {turn.error && (
            <div className="rounded-lg bg-rose-50 p-3 text-sm text-rose-700">{turn.error}</div>
          )}
          {turn.response && <ResponseCard r={turn.response} />}
        </div>
      ))}

      <form onSubmit={ask} className="sticky bottom-4 flex gap-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Ask offline…"
          className="flex-1 rounded-xl border border-slate-300 bg-white px-4 py-3 text-sm outline-none focus:border-ink"
        />
        <button
          type="submit"
          disabled={busy}
          className="rounded-xl bg-ink px-5 py-3 text-sm font-medium text-white disabled:opacity-50"
        >
          Ask
        </button>
      </form>
    </div>
  );
}
