import { useRef, useState } from "react";
import { api } from "../lib/api";

interface Step {
  state: string;
  message: string;
  at: string;
}

export default function PreparePage() {
  const [compileExpert, setCompileExpert] = useState(true);
  const [finalize, setFinalize] = useState(false);
  const [steps, setSteps] = useState<Step[]>([]);
  const [running, setRunning] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const pollRef = useRef<number | null>(null);

  async function start() {
    setSteps([]);
    setDone(null);
    setRunning(true);
    try {
      const { job_id } = await api.prepare(compileExpert, finalize);
      poll(job_id);
    } catch (e: any) {
      setSteps([{ state: "failed", message: String(e.message || e), at: "" }]);
      setRunning(false);
    }
  }

  function poll(jobId: string) {
    const tick = async () => {
      try {
        const job = await api.job(jobId);
        setSteps(job.progress || []);
        if (["ready", "failed", "cancelled"].includes(job.state)) {
          setRunning(false);
          setDone(job.state);
          if (pollRef.current) window.clearInterval(pollRef.current);
        }
      } catch {
        /* ignore transient errors */
      }
    };
    tick();
    pollRef.current = window.setInterval(tick, 700);
  }

  return (
    <div className="space-y-5">
      <div className="rounded-xl border border-slate-200 bg-white p-5">
        <h2 className="text-lg font-semibold">Prepare for a trip</h2>
        <p className="mt-1 text-sm text-slate-500">
          Compile experts and build a local knowledge pack while you still have
          connectivity. Everything afterward runs offline.
        </p>

        <div className="mt-4 space-y-3 text-sm">
          <div className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2">
            <span>Destination</span>
            <span className="font-medium">South Korea</span>
          </div>
          <label className="flex items-center gap-3">
            <input
              type="checkbox"
              checked={compileExpert}
              onChange={(e) => setCompileExpert(e.target.checked)}
            />
            <span>
              Compile the heard-expression resolver (needs internet now; improves
              phonetic matches offline)
            </span>
          </label>
          <label className="flex items-center gap-3 text-slate-600">
            <input
              type="checkbox"
              checked={finalize}
              disabled={!compileExpert}
              onChange={(e) => setFinalize(e.target.checked)}
            />
            <span>
              Finalize with the finetuned compiler (slower, higher accuracy)
            </span>
          </label>
        </div>

        <button
          onClick={start}
          disabled={running}
          className="mt-4 rounded-xl bg-ink px-5 py-2.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {running ? "Preparing…" : "Compile offline assistant"}
        </button>
      </div>

      {steps.length > 0 && (
        <div className="rounded-xl border border-slate-200 bg-white p-5">
          <h3 className="text-sm font-semibold">Progress</h3>
          <ol className="mt-3 space-y-2">
            {steps.map((s, i) => (
              <li key={i} className="flex gap-3 text-sm">
                <span className="w-40 shrink-0 font-mono text-xs text-slate-400">
                  {s.state}
                </span>
                <span className="text-slate-700">{s.message}</span>
              </li>
            ))}
          </ol>
          {done && (
            <p
              className={`mt-3 text-sm font-medium ${
                done === "ready" ? "text-emerald-700" : "text-rose-700"
              }`}
            >
              {done === "ready"
                ? "Pack ready. You can go offline now."
                : `Preparation ${done}.`}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
