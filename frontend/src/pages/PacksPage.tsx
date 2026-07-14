import { useEffect, useState } from "react";
import { api, getAppToken, Pack } from "../lib/api";

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export default function PacksPage() {
  const [packs, setPacks] = useState<Pack[]>([]);
  const [storage, setStorage] = useState<{ home: string; total_bytes: number } | null>(null);
  const [metrics, setMetrics] = useState<any>(null);
  const [token, setToken] = useState<string>("");
  const [showToken, setShowToken] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function load() {
    try {
      const [p, s, m, t] = await Promise.all([
        api.packs(),
        api.storage(),
        api.metrics(),
        getAppToken(),
      ]);
      setPacks(p.packs);
      setStorage(s);
      setMetrics(m.expert_loader);
      setToken(t);
    } catch (e: any) {
      setErr(String(e.message || e));
    }
  }

  useEffect(() => {
    load();
  }, []);

  return (
    <div className="space-y-4">
      {err && <div className="rounded-lg bg-rose-50 p-3 text-sm text-rose-700">{err}</div>}

      {storage && (
        <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-slate-500">Data directory</span>
            <span className="font-mono text-xs">{storage.home}</span>
          </div>
          <div className="mt-1 flex items-center justify-between">
            <span className="text-slate-500">Total pack size</span>
            <span className="font-medium">{fmtBytes(storage.total_bytes)}</span>
          </div>
        </div>
      )}

      {packs.map((p) => (
        <div key={p.pack_id} className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="flex items-start justify-between">
            <div>
              <h3 className="font-semibold">{p.title}</h3>
              <p className="text-xs text-slate-400">{p.pack_id}</p>
            </div>
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                p.ready ? "bg-emerald-100 text-emerald-800" : "bg-slate-100 text-slate-500"
              }`}
            >
              {p.ready ? "ready" : "not tested"}
            </span>
          </div>
          <div className="mt-2 flex flex-wrap gap-2 text-xs text-slate-500">
            <span>{fmtBytes(p.size_bytes)}</span>
            {typeof p.manifest?.coverage?.coverage === "number" && (
              <span className="rounded bg-emerald-50 px-2 py-0.5 text-emerald-700">
                coverage {Math.round(p.manifest.coverage.coverage * 100)}%
              </span>
            )}
            {(p.manifest?.experts || []).map((e: any) => (
              <span key={e.role} className="rounded bg-slate-100 px-2 py-0.5">
                {e.role} · {e.compiler}
              </span>
            ))}
            {(!p.manifest?.experts || p.manifest.experts.length === 0) && (
              <span className="rounded bg-slate-100 px-2 py-0.5">no compiled experts</span>
            )}
          </div>
          {p.manifest?.plan?.selected_topics && (
            <div className="mt-2 text-xs text-slate-400">
              topics: {p.manifest.plan.selected_topics.join(", ")}
            </div>
          )}
        </div>
      ))}

      <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm">
        <h3 className="font-semibold">Connect the browser extension</h3>
        <p className="mt-1 text-xs text-slate-500">
          Paste this token into the extension's side panel to let it save pages
          into your offline pack.
        </p>
        <div className="mt-2 flex items-center gap-2">
          <code className="flex-1 truncate rounded bg-slate-100 px-2 py-1 text-xs">
            {showToken ? token : "•".repeat(Math.min(token.length, 32))}
          </code>
          <button
            onClick={() => setShowToken((v) => !v)}
            className="rounded bg-slate-200 px-2 py-1 text-xs"
          >
            {showToken ? "Hide" : "Show"}
          </button>
          <button
            onClick={() => navigator.clipboard?.writeText(token)}
            className="rounded bg-ink px-2 py-1 text-xs text-white"
          >
            Copy
          </button>
        </div>
      </div>

      {metrics && (
        <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm">
          <h3 className="font-semibold">Runtime memory</h3>
          <p className="mt-1 text-xs text-slate-500">
            Expert capacity {metrics.capacity} · resident:{" "}
            {metrics.resident.length ? metrics.resident.join(", ") : "none"}
          </p>
          {metrics.loads?.length > 0 && (
            <ul className="mt-2 space-y-1 text-xs text-slate-600">
              {metrics.loads.map((l: any, i: number) => (
                <li key={i}>
                  {l.program_id.slice(0, 10)}… loaded in {l.load_seconds}s · peak RSS{" "}
                  {l.peak_rss_after_mb} MB (+{l.peak_rss_delta_mb} MB)
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
