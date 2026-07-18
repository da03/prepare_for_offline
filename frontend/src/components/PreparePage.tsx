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
          <h1 id="prepare-heading">What should PAW get better at?</h1>
          <p>Prepare a specialist program from one topic.</p>
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
              {busy ? <strong>{job.progress_percent}%</strong> : null}
            </div>
            {busy ? (
              <>
                <progress max={100} value={job.progress_percent} />
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
