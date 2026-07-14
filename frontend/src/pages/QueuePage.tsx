import { useEffect, useState } from "react";
import { api, QueueItem } from "../lib/api";

export default function QueuePage() {
  const [items, setItems] = useState<QueueItem[]>([]);
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [err, setErr] = useState<string | null>(null);

  async function load() {
    try {
      const { items } = await api.queue();
      setItems(items);
    } catch (e: any) {
      setErr(String(e.message || e));
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function verify(id: number) {
    const draft = drafts[id]?.trim();
    if (!draft) return;
    await api.verify(id, draft);
    await load();
  }

  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-500">
        Questions the assistant could not confidently answer offline. When you're
        back online, record a verified answer; the app shows whether it changed.
      </p>
      {err && <div className="rounded-lg bg-rose-50 p-3 text-sm text-rose-700">{err}</div>}
      {items.length === 0 && (
        <div className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-center text-sm text-slate-400">
          Nothing queued.
        </div>
      )}
      {items.map((it) => (
        <div key={it.id} className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="flex items-center justify-between">
            <h3 className="font-medium">{it.question}</h3>
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                it.status === "verified"
                  ? "bg-emerald-100 text-emerald-800"
                  : "bg-amber-100 text-amber-800"
              }`}
            >
              {it.status}
            </span>
          </div>
          {it.offline_answer && (
            <p className="mt-2 text-sm text-slate-600">
              <span className="font-medium">Offline:</span> {it.offline_answer}
            </p>
          )}

          {it.status === "verified" ? (
            <div className="mt-2 space-y-1 text-sm">
              <p>
                <span className="font-medium">Verified:</span> {it.verified_answer}
              </p>
              <p
                className={`text-xs font-medium ${
                  it.changed ? "text-rose-700" : "text-emerald-700"
                }`}
              >
                {it.changed ? "Changed from the offline answer" : "Matches the offline answer"}
              </p>
            </div>
          ) : (
            <div className="mt-3 flex gap-2">
              <input
                value={drafts[it.id] || ""}
                onChange={(e) => setDrafts((d) => ({ ...d, [it.id]: e.target.value }))}
                placeholder="Verified answer (online)…"
                className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-ink"
              />
              <button
                onClick={() => verify(it.id)}
                className="rounded-lg bg-ink px-4 py-2 text-sm font-medium text-white"
              >
                Save
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
