import { useState, type FormEvent } from "react";
import type { NeuralJob, PreparedProgram } from "../lib/api";
import { Icon } from "./Icon";

interface PreparePageProps {
  programs: PreparedProgram[];
  job: NeuralJob | null;
  error: string | null;
  onPrepare: (prompt: string) => Promise<void>;
  onCancel: () => Promise<void>;
  onRemove: (programKey: string) => Promise<void>;
}

function programStatus(program: PreparedProgram): string {
  if (program.status === "improving") return "Ready · improving";
  if (program.status === "failed") return "Failed";
  return program.stage === "finetuned" ? "Ready · finetuned" : "Ready";
}

function phaseLabel(state: string): string {
  if (state === "compiling_standard") return "Step 1 of 2";
  if (state === "compiling_finetuned") return "Step 2 of 2";
  return "Starting";
}

function phaseEstimate(state: string): string {
  if (state === "compiling_finetuned") return "Usually 2–5 minutes";
  return "Usually under a minute";
}

function elapsedLabel(createdAt: string): string {
  const elapsedSeconds = Math.max(
    0,
    Math.floor((Date.now() - Date.parse(createdAt)) / 1000),
  );
  if (!Number.isFinite(elapsedSeconds)) return "Preparing";
  const minutes = Math.floor(elapsedSeconds / 60);
  const seconds = elapsedSeconds % 60;
  return minutes
    ? `${minutes}m ${seconds.toString().padStart(2, "0")}s elapsed`
    : `${seconds}s elapsed`;
}

export function PreparePage({
  programs,
  job,
  error,
  onPrepare,
  onCancel,
  onRemove,
}: PreparePageProps) {
  const [prompt, setPrompt] = useState("");
  const [removing, setRemoving] = useState<string | null>(null);
  const busy =
    !!job && !["ready", "failed", "cancelled"].includes(job.state);
  const measuredProgress =
    job?.state === "compiling_finetuned" && job.progress_percent > 0;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!prompt.trim() || busy) return;
    await onPrepare(prompt.trim());
    setPrompt("");
  }

  async function remove(programKey: string) {
    setRemoving(programKey);
    try {
      await onRemove(programKey);
    } finally {
      setRemoving(null);
    }
  }

  return (
    <section className="prepare-page" aria-labelledby="prepare-heading">
      <div className="prepare-content">
        <header className="prepare-title">
          <h1 id="prepare-heading">Prepare a topic</h1>
          <p>Compile a specialist from PAW&apos;s existing knowledge.</p>
        </header>

        <form className="enrichment-form" onSubmit={submit}>
          <label className="enrichment-prompt">
            <span className="sr-only">Topic</span>
            <textarea
              data-autofocus
              rows={4}
              maxLength={1200}
              value={prompt}
              placeholder="Korean language for travel"
              onChange={(event) => setPrompt(event.target.value)}
            />
          </label>
          <button
            className="button button--primary prepare-action"
            type="submit"
            disabled={!prompt.trim() || busy}
          >
            {busy ? "Preparing…" : "Prepare"}
          </button>
        </form>

        {job ? (
          <section className="enrichment-progress" aria-live="polite">
            <div className="enrichment-progress__heading">
              <span aria-hidden="true">
                {job.state === "ready" ? (
                  <Icon name="check" size={20} />
                ) : (
                  <span className="status-pulse" />
                )}
              </span>
              <div>
                <h2>{job.state === "ready" ? "Ready" : job.message}</h2>
                <p>{job.topic_prompt}</p>
              </div>
              {busy ? (
                <strong>
                  {phaseLabel(job.state)}
                  {measuredProgress
                    ? ` · ${Math.round(job.progress_percent)}%`
                    : ""}
                </strong>
              ) : null}
            </div>
            {busy ? (
              <>
                <progress
                  aria-label="Preparation in progress"
                  max={100}
                  value={
                    measuredProgress ? job.progress_percent : undefined
                  }
                />
                <div className="enrichment-progress__details">
                  <span>{elapsedLabel(job.created_at)}</span>
                  <span>{phaseEstimate(job.state)}</span>
                </div>
                <button
                  className="button button--quiet"
                  type="button"
                  onClick={() => void onCancel()}
                >
                  Cancel
                </button>
              </>
            ) : null}
          </section>
        ) : null}

        {error ? (
          <p className="form-error" role="alert">
            {error}
          </p>
        ) : null}

        {programs.length ? (
          <section className="saved-enrichments" aria-labelledby="programs-heading">
            <div className="section-title">
              <h2 id="programs-heading">Prepared topics</h2>
            </div>
            <ul className="enrichment-list">
              {programs.map((program) => (
                <li key={program.program_key}>
                  <div className="enrichment-item__main">
                    <div>
                      <h3>{program.name}</h3>
                      <p>{programStatus(program)}</p>
                    </div>
                    <button
                      className="icon-button icon-button--danger"
                      type="button"
                      aria-label={`Remove ${program.name}`}
                      disabled={removing !== null}
                      onClick={() => void remove(program.program_key)}
                    >
                      <Icon name="trash" size={18} />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </div>
    </section>
  );
}
