import {
  useEffect,
  useMemo,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";
import type { Trip, TripCoverage, TripPatch } from "../lib/api";
import {
  cleanStringList,
  coverageSources,
  isTripReady,
  isoDateInput,
  sourceId,
  sourcePublisher,
  tripDestination,
  tripEnd,
  tripEvent,
  tripId,
  tripNeeds,
  tripStart,
} from "../lib/travel";
import { Icon } from "./Icon";

export type PreparePhase =
  | "idle"
  | "parsing"
  | "discovering"
  | "starting"
  | "preparing"
  | "needs_input"
  | "ready"
  | "failed"
  | "cancelled";

export interface PrepareRequest {
  text: string;
  note: string;
  files: File[];
}

interface PreparePageProps {
  trip: Trip | null;
  coverage: TripCoverage | null;
  phase: PreparePhase;
  statusText: string;
  progress: number;
  error: string | null;
  blockingQuestion: string | null;
  onSubmit: (request: PrepareRequest) => void;
  onClarify: (answer: string) => void;
  onSaveTrip: (tripId: string, update: TripPatch) => Promise<void>;
  onCancel: () => void;
  onNewTrip: () => void;
  onReprepare: (sourceIds: string[]) => void;
}

interface BriefDraft {
  event: string;
  destination: string;
  start: string;
  end: string;
  languages: string;
  needs: string;
}

function briefFromTrip(trip: Trip | null): BriefDraft {
  return {
    event: tripEvent(trip),
    destination: tripDestination(trip),
    start: isoDateInput(tripStart(trip)),
    end: isoDateInput(tripEnd(trip)),
    languages: cleanStringList(trip?.languages).join(", "),
    needs: tripNeeds(trip).join(", "),
  };
}

function splitList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function formatBytes(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    return "Estimating";
  }
  if (value < 1_000_000) return `${Math.max(1, Math.round(value / 1_000))} KB`;
  if (value < 1_000_000_000) return `${(value / 1_000_000).toFixed(value < 10_000_000 ? 1 : 0)} MB`;
  return `${(value / 1_000_000_000).toFixed(1)} GB`;
}

function formatTime(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    return "A few minutes";
  }
  if (value < 60) return "Less than a minute";
  const minutes = Math.max(1, Math.round(value / 60));
  return `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
}

function privacyLabel(trip: Trip, coverage: TripCoverage | null): string {
  const value = String(coverage?.privacy ?? trip.privacy_mode ?? "").toLowerCase();
  if (value.includes("online") || value.includes("network")) {
    return "Online for discovery, saved locally";
  }
  return "Saved locally on this Mac";
}

function freshnessLabel(coverage: TripCoverage | null): string {
  const value = coverage?.freshness ?? coverage?.updated_at;
  if (!value) return "Checked during preparation";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return `Checked ${new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
  }).format(parsed)}`;
}

export function PreparePage({
  trip,
  coverage,
  phase,
  statusText,
  progress,
  error,
  blockingQuestion,
  onSubmit,
  onClarify,
  onSaveTrip,
  onCancel,
  onNewTrip,
  onReprepare,
}: PreparePageProps) {
  const [description, setDescription] = useState("");
  const [note, setNote] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const [brief, setBrief] = useState<BriefDraft>(() => briefFromTrip(trip));
  const [savingBrief, setSavingBrief] = useState(false);
  const [briefError, setBriefError] = useState<string | null>(null);
  const [clarification, setClarification] = useState("");
  const sources = useMemo(() => coverageSources(coverage, trip), [coverage, trip]);
  const allSourceIds = useMemo(
    () => sources.map(sourceId).filter(Boolean),
    [sources],
  );
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([]);

  useEffect(() => {
    setBrief(briefFromTrip(trip));
    setBriefError(null);
  }, [trip, trip?.updated_at]);

  useEffect(() => {
    setSelectedSourceIds(allSourceIds);
  }, [allSourceIds.join("|")]);

  const initialBrief = briefFromTrip(trip);
  const briefDirty = JSON.stringify(brief) !== JSON.stringify(initialBrief);
  const busy = ["parsing", "discovering", "starting", "preparing"].includes(phase);
  const ready = Boolean(trip && (phase === "ready" || isTripReady(trip)));
  const optimizationStatus =
    typeof trip?.optimization_status === "string"
      ? trip.optimization_status
      : "";
  const improving =
    ready && ["queued", "optimizing"].includes(optimizationStatus);

  const coverageAreas = cleanStringList(
    coverage?.semantic_coverage ?? coverage?.areas ?? coverage?.categories,
  );
  const publishers = Array.from(
    new Set([
      ...cleanStringList(coverage?.source_publishers ?? coverage?.publishers),
      ...sources.map(sourcePublisher),
    ]),
  );
  const estimatedSize =
    coverage?.estimated_size_bytes ??
    coverage?.size_bytes ??
    trip?.estimated_size_bytes ??
    trip?.size_bytes;
  const estimatedTime =
    coverage?.estimated_time_seconds ??
    coverage?.preparation_time_estimate_s ??
    trip?.preparation_time_estimate_s;

  function selectFiles(event: ChangeEvent<HTMLInputElement>) {
    const incoming = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (!incoming.length) return;
    const next = [...files, ...incoming].slice(0, 5);
    if (incoming.some((file) => file.size > 8 * 1024 * 1024)) {
      setAttachmentError("Each attachment must be 8 MB or smaller.");
      return;
    }
    if (next.reduce((total, file) => total + file.size, 0) > 16 * 1024 * 1024) {
      setAttachmentError("Attachments must total 16 MB or less.");
      return;
    }
    setAttachmentError(null);
    setFiles(next);
  }

  function submitIntake(event: FormEvent) {
    event.preventDefault();
    if (!description.trim() || busy) return;
    onSubmit({ text: description.trim(), note: note.trim(), files });
  }

  async function saveBrief(event: FormEvent) {
    event.preventDefault();
    const id = tripId(trip);
    if (!id || !brief.event.trim() || !brief.destination.trim()) return;
    setSavingBrief(true);
    setBriefError(null);
    try {
      await onSaveTrip(id, {
        event: brief.event.trim(),
        destination: brief.destination.trim(),
        dates: {
          start: brief.start || null,
          end: brief.end || null,
        },
        languages: splitList(brief.languages),
        needs: splitList(brief.needs),
      });
    } catch (caught) {
      setBriefError(
        caught instanceof Error ? caught.message : "Could not save the trip brief.",
      );
    } finally {
      setSavingBrief(false);
    }
  }

  function startAnotherTrip() {
    setDescription("");
    setNote("");
    setFiles([]);
    setAttachmentError(null);
    setClarification("");
    onNewTrip();
  }

  if (!trip) {
    return (
      <section className="prepare-page prepare-page--intake" aria-labelledby="prepare-heading">
        <div className="prepare-intake">
          <span className="eyebrow">Prepare a trip</span>
          <h1 id="prepare-heading">Where are you going?</h1>
          <p>
            Describe the trip naturally. PAW will organize the details and save a useful
            travel guide for offline use.
          </p>

          <form onSubmit={submitIntake}>
            <label className="nl-trip-field">
              <span className="sr-only">Describe your trip</span>
              <textarea
                data-autofocus
                rows={5}
                maxLength={4_000}
                value={description}
                disabled={busy}
                placeholder="I’m going to ICML 2026 in Seoul"
                onChange={(event) => setDescription(event.target.value)}
              />
            </label>

            <details className="notes-attachment">
              <summary>Add travel notes <span>Optional</span></summary>
              <label>
                <span className="sr-only">Additional travel notes</span>
                <textarea
                  rows={3}
                  maxLength={8_000}
                  value={note}
                  disabled={busy}
                  placeholder="Hotel, flight, accessibility needs, dietary notes…"
                  onChange={(event) => setNote(event.target.value)}
                />
              </label>
            </details>

            <div className="attachment-row">
              <label className="button button--secondary file-button">
                <Icon name="source" size={18} />
                Attach files
                <input
                  type="file"
                  multiple
                  disabled={busy}
                  accept=".txt,.md,.pdf,.docx,.ics,text/*,application/pdf"
                  onChange={selectFiles}
                />
              </label>
              <span>Optional · up to 5 files</span>
            </div>

            {files.length ? (
              <ul className="attachment-list" aria-label="Attached files">
                {files.map((file, index) => (
                  <li key={`${file.name}-${file.lastModified}`}>
                    <span>{file.name}</span>
                    <button
                      type="button"
                      aria-label={`Remove ${file.name}`}
                      onClick={() => setFiles((current) => current.filter((_, i) => i !== index))}
                    >
                      <Icon name="close" size={15} />
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}

            {attachmentError ? (
              <p className="form-error" role="alert">
                {attachmentError}
              </p>
            ) : null}
            {error ? (
              <p className="form-error" role="alert">
                {error}
              </p>
            ) : null}

            <button
              className="button button--primary prepare-action"
              type="submit"
              disabled={!description.trim() || busy}
            >
              {busy ? statusText : "Prepare for offline"}
            </button>
            <p className="privacy-hint">
              Your saved trip stays on this Mac and works without a connection.
            </p>
          </form>
        </div>
      </section>
    );
  }

  return (
    <section className="prepare-page" aria-labelledby="trip-brief-heading">
      <div className="prepare-content">
        <header className="prepare-title-row">
          <div>
            <span className="eyebrow">Prepare</span>
            <h1 id="trip-brief-heading">{tripEvent(trip) || "Your trip"}</h1>
          </div>
          <button className="button button--quiet button--compact" type="button" disabled={busy} onClick={startAnotherTrip}>
            New trip
          </button>
        </header>

        <form className="trip-brief" onSubmit={saveBrief}>
          <div className="section-title">
            <div>
              <span className="eyebrow">Trip Brief</span>
              <h2>What PAW understood</h2>
            </div>
            {briefDirty ? (
              <button
                className="button button--secondary button--compact"
                type="submit"
                disabled={savingBrief || busy || !brief.event.trim() || !brief.destination.trim()}
              >
                {savingBrief ? "Saving…" : "Save"}
              </button>
            ) : (
              <span className="saved-label">
                <Icon name="check" size={15} />
                Saved
              </span>
            )}
          </div>

          <div className="brief-grid">
            <label className="field field--wide">
              <span>Event or purpose</span>
              <input
                value={brief.event}
                onChange={(event) => setBrief((current) => ({ ...current, event: event.target.value }))}
              />
            </label>
            <label className="field field--wide">
              <span>Destination</span>
              <input
                value={brief.destination}
                onChange={(event) => setBrief((current) => ({ ...current, destination: event.target.value }))}
              />
            </label>
            <label className="field">
              <span>Starts</span>
              <input
                type="date"
                value={brief.start}
                onChange={(event) => setBrief((current) => ({ ...current, start: event.target.value }))}
              />
            </label>
            <label className="field">
              <span>Ends</span>
              <input
                type="date"
                value={brief.end}
                min={brief.start || undefined}
                onChange={(event) => setBrief((current) => ({ ...current, end: event.target.value }))}
              />
            </label>
            <label className="field field--wide">
              <span>Languages</span>
              <input
                value={brief.languages}
                placeholder="English, Korean"
                onChange={(event) => setBrief((current) => ({ ...current, languages: event.target.value }))}
              />
            </label>
            <label className="field field--wide">
              <span>Needs</span>
              <input
                value={brief.needs}
                placeholder="Conference schedule, transit, food"
                onChange={(event) => setBrief((current) => ({ ...current, needs: event.target.value }))}
              />
            </label>
          </div>
          {briefError ? (
            <p className="form-error" role="alert">
              {briefError}
            </p>
          ) : null}
        </form>

        {blockingQuestion ? (
          <form
            className="blocking-question"
            onSubmit={(event) => {
              event.preventDefault();
              if (clarification.trim()) onClarify(clarification.trim());
            }}
          >
            <span className="eyebrow">One detail needed</span>
            <h2>{blockingQuestion}</h2>
            <div>
              <label className="sr-only" htmlFor="trip-clarification">
                Answer the trip question
              </label>
              <input
                id="trip-clarification"
                value={clarification}
                onChange={(event) => setClarification(event.target.value)}
              />
              <button className="button button--primary" type="submit" disabled={!clarification.trim()}>
                Continue
              </button>
            </div>
          </form>
        ) : null}

        <section className="coverage-section" aria-labelledby="coverage-heading">
          <div className="section-title">
            <div>
              <span className="eyebrow">Offline coverage</span>
              <h2 id="coverage-heading">Useful, current, and compact</h2>
            </div>
          </div>

          <div className="coverage-focus">
            <span>Coverage</span>
            <div>
              {(coverageAreas.length
                ? coverageAreas
                : ["Event essentials", "Local travel", "Practical needs"]
              ).slice(0, 6).map((area) => (
                <span key={area}>{area}</span>
              ))}
            </div>
          </div>

          <dl className="coverage-facts">
            <div className="coverage-fact coverage-fact--wide">
              <dt>Source publishers</dt>
              <dd>
                {publishers.length
                  ? publishers.slice(0, 4).join(", ")
                  : "Trusted publishers are being discovered"}
              </dd>
            </div>
            <div>
              <dt>Freshness</dt>
              <dd>{freshnessLabel(coverage)}</dd>
            </div>
            <div>
              <dt>Offline size</dt>
              <dd>{formatBytes(estimatedSize)}</dd>
            </div>
            <div>
              <dt>Prepare time</dt>
              <dd>{formatTime(estimatedTime)}</dd>
            </div>
            <div>
              <dt>Privacy</dt>
              <dd>{privacyLabel(trip, coverage)}</dd>
            </div>
          </dl>

          <details className="customize-coverage">
            <summary>
              <span>
                <strong>Customize coverage</strong>
                <small>Choose which publishers are saved</small>
              </span>
              <Icon name="chevron" size={18} />
            </summary>
            <div>
              {sources.length ? (
                <fieldset>
                  <legend className="sr-only">Publishers to save</legend>
                  {sources.map((source, index) => {
                    const id = sourceId(source);
                    const checked = !id || selectedSourceIds.includes(id);
                    return (
                      <label key={id || `${sourcePublisher(source)}-${index}`}>
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={!id || busy}
                          onChange={(event) => {
                            setSelectedSourceIds((current) =>
                              event.target.checked
                                ? [...new Set([...current, id])]
                                : current.filter((item) => item !== id),
                            );
                          }}
                        />
                        <span>{sourcePublisher(source)}</span>
                      </label>
                    );
                  })}
                </fieldset>
              ) : (
                <p>Publisher choices will appear after discovery.</p>
              )}
              {ready && allSourceIds.length ? (
                <button
                  className="button button--secondary"
                  type="button"
                  disabled={busy || !selectedSourceIds.length}
                  onClick={() => onReprepare(selectedSourceIds)}
                >
                  Update offline guide
                </button>
              ) : null}
            </div>
          </details>
        </section>

        <section
          className={`prepare-status ${ready ? "is-ready" : ""} ${phase === "failed" ? "has-error" : ""}`}
          aria-live="polite"
        >
          <div className="prepare-status__heading">
            <span className="prepare-status__icon" aria-hidden="true">
              {ready ? <Icon name="check" size={20} /> : <span className="status-pulse" />}
            </span>
            <div>
              <h2>{ready ? "Ready Offline" : statusText}</h2>
              <p>
                {ready
                  ? improving
                    ? "Ready now. Improving accuracy in the background…"
                    : "Your trip guide is saved on this Mac."
                  : phase === "failed"
                    ? error || "Preparation stopped before your guide was ready."
                    : "Keep PAW open while your travel information is saved."}
              </p>
            </div>
            {!ready ? <strong>{Math.round(progress)}%</strong> : null}
          </div>
          <progress max={100} value={ready ? 100 : Math.max(0, Math.min(100, progress))}>
            {Math.round(progress)}%
          </progress>
          {busy ? (
            <button className="button button--quiet button--compact" type="button" onClick={onCancel}>
              Cancel
            </button>
          ) : null}
          {!busy && !ready && !blockingQuestion && phase !== "failed" ? (
            <button
              className="button button--primary"
              type="button"
              onClick={() => onReprepare(selectedSourceIds)}
            >
              Prepare this trip
            </button>
          ) : null}
        </section>
      </div>
    </section>
  );
}
