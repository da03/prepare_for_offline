import type { KeyboardEvent } from "react";
import type { Support, Trip } from "../lib/api";
import {
  isTripReady,
  tripDestination,
  tripId,
  tripLabel,
  tripStatusLabel,
} from "../lib/travel";
import { Icon } from "./Icon";

export interface AnswerSource {
  source_id?: string;
  title: string;
  publisher?: string;
  snippet?: string;
  freshness?: string;
}

export interface AnswerTurn {
  id: string;
  question: string;
  answer: string;
  state: "working" | "complete" | "abstained" | "error";
  status: string;
  support?: Support | string | null;
  freshness?: string | null;
  sources: AnswerSource[];
  buildSteps: string[];
  refined: boolean;
  startsNewTopic?: boolean;
}

interface AskPageProps {
  trips: Trip[];
  activeTrip: Trip | null;
  starters: string[];
  turns: AnswerTurn[];
  value: string;
  asking: boolean;
  nextStartsNewTopic: boolean;
  nextFollowUp: number;
  maxFollowUps: number;
  onTripChange: (tripId: string) => void;
  onValueChange: (value: string) => void;
  onSubmit: (question: string) => void;
  onNewTopic: () => void;
  onPrepare: () => void;
}

function supportLabel(value: AnswerTurn["support"]): string {
  switch (String(value ?? "").toLowerCase()) {
    case "high":
    case "strong":
      return "Strong support";
    case "medium":
    case "supported":
      return "Supported";
    case "low":
    case "limited":
      return "Limited support";
    default:
      return value ? String(value) : "";
  }
}

function sourceLabel(source: AnswerSource): string {
  if (source.publisher && source.publisher !== source.title) {
    return `${source.publisher} · ${source.title}`;
  }
  return source.publisher || source.title;
}

export function AskPage({
  trips,
  activeTrip,
  starters,
  turns,
  value,
  asking,
  nextStartsNewTopic,
  nextFollowUp,
  maxFollowUps,
  onTripChange,
  onValueChange,
  onSubmit,
  onNewTopic,
  onPrepare,
}: AskPageProps) {
  const ready = isTripReady(activeTrip);
  const destination = tripDestination(activeTrip);
  const latestTurn = turns[turns.length - 1];

  function send() {
    const question = value.trim();
    if (!question || asking || !activeTrip) return;
    onSubmit(question);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  }

  return (
    <section className="ask-page" aria-label="Ask about your trip">
      <div className="trip-bar">
        <label className="trip-picker">
          <span className="sr-only">Active trip</span>
          <select
            value={tripId(activeTrip)}
            disabled={!trips.length || asking}
            onChange={(event) => onTripChange(event.target.value)}
          >
            {!trips.length ? <option value="">No trips yet</option> : null}
            {trips.map((trip) => (
              <option key={tripId(trip)} value={tripId(trip)}>
                {tripLabel(trip)}
              </option>
            ))}
          </select>
          <Icon name="chevron" size={16} />
        </label>

        {activeTrip ? (
          <button
            className={`readiness-pill ${ready ? "is-ready" : ""}`}
            type="button"
            onClick={onPrepare}
            aria-label={`${tripStatusLabel(activeTrip)}. Open preparation.`}
          >
            <span aria-hidden="true" />
            {tripStatusLabel(activeTrip)}
          </button>
        ) : (
          <button className="button button--primary button--compact" type="button" onClick={onPrepare}>
            Prepare a trip
          </button>
        )}
      </div>

      <div className="ask-scroll">
        {!activeTrip ? (
          <div className="ask-empty">
            <div className="paw-mark" aria-hidden="true">
              <img src="/favicon.svg" alt="" />
            </div>
            <h1>Prepare once. Travel with answers.</h1>
            <p>Tell PAW where you’re going, then ask from your saved travel guide anywhere.</p>
            <button className="button button--primary" type="button" onClick={onPrepare}>
              Prepare your first trip
            </button>
          </div>
        ) : !turns.length ? (
          <div className="starter-state">
            <span className="eyebrow">{ready ? "Ready Offline" : "Trip assistant"}</span>
            <h1>
              What do you need
              {destination ? ` in ${destination}?` : " for your trip?"}
            </h1>
            <p>
              {ready
                ? "Answers use the travel information saved on this Mac."
                : "You can ask now, but prepare this trip for dependable offline answers."}
            </p>
            <div className="starter-list" aria-label="Starter questions">
              {starters.slice(0, 4).map((question) => (
                <button
                  key={question}
                  type="button"
                  disabled={asking}
                  onClick={() => onSubmit(question)}
                >
                  <span>{question}</span>
                  <Icon name="send" size={16} />
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="answer-feed">
            {turns.map((turn) => {
              const current = turn.id === latestTurn?.id;
              const support = supportLabel(turn.support);
              const steps = Array.from(new Set(turn.buildSteps.filter(Boolean)));
              return (
                <div key={turn.id}>
                  {turn.startsNewTopic ? (
                    <div className="topic-divider">
                      <span>New topic</span>
                    </div>
                  ) : null}
                  <article className="answer-turn">
                    <p className="question-bubble">{turn.question}</p>
                    <div
                      className={`answer-card answer-card--${turn.state}`}
                      aria-live={current ? "polite" : undefined}
                      aria-busy={turn.state === "working"}
                    >
                      <div className="answer-card__brand" aria-hidden="true">
                        <img src="/favicon.svg" alt="" />
                      </div>
                      <div className="answer-card__body">
                        {turn.state === "working" ? (
                          <div className="semantic-status" role="status">
                            <span className="status-pulse" aria-hidden="true" />
                            {turn.status}
                          </div>
                        ) : null}

                        {turn.answer ? (
                          <p className="answer-copy">{turn.answer}</p>
                        ) : turn.state === "working" ? (
                          <div className="answer-placeholder" aria-hidden="true">
                            <span />
                            <span />
                          </div>
                        ) : null}

                        {turn.refined ? (
                          <p className="refined-marker">
                            <Icon name="refresh" size={14} />
                            Refined with additional sources
                          </p>
                        ) : null}

                        {turn.state !== "working" && (support || turn.freshness || turn.sources.length) ? (
                          <div className="answer-meta" aria-label="Answer support">
                            {support ? <span>{support}</span> : null}
                            {turn.freshness ? <span>{turn.freshness}</span> : null}
                            {turn.sources.length ? (
                              <span>
                                {turn.sources.length} saved{" "}
                                {turn.sources.length === 1 ? "source" : "sources"}
                              </span>
                            ) : null}
                          </div>
                        ) : null}

                        {turn.state !== "working" && (steps.length || turn.sources.length) ? (
                          <details className="answer-built">
                            <summary>How this answer was built</summary>
                            <div>
                              {steps.length ? (
                                <ul className="build-steps">
                                  {steps.map((step) => (
                                    <li key={step}>
                                      <Icon name="check" size={15} />
                                      <span>{step}</span>
                                    </li>
                                  ))}
                                </ul>
                              ) : null}
                              {turn.sources.length ? (
                                <ul className="answer-sources" aria-label="Sources">
                                  {turn.sources.map((source, index) => (
                                    <li key={source.source_id || `${source.title}-${index}`}>
                                      <strong>{sourceLabel(source)}</strong>
                                      {source.freshness ? <span>{source.freshness}</span> : null}
                                      {source.snippet ? <p>{source.snippet}</p> : null}
                                    </li>
                                  ))}
                                </ul>
                              ) : null}
                            </div>
                          </details>
                        ) : null}
                      </div>
                    </div>
                  </article>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="ask-composer-region">
        {activeTrip && turns.length ? (
          <div className="follow-up-row">
            <span className={`context-chip ${nextStartsNewTopic ? "is-new" : ""}`}>
              {nextStartsNewTopic
                ? "Next question starts a new topic"
                : `Using previous question · follow-up ${nextFollowUp} of ${maxFollowUps}`}
            </span>
            <button
              type="button"
              disabled={asking}
              aria-pressed={nextStartsNewTopic}
              onClick={onNewTopic}
            >
              New topic
            </button>
          </div>
        ) : null}
        <div className="question-composer">
          <label className="sr-only" htmlFor="travel-question">
            Ask a question about your trip
          </label>
          <textarea
            id="travel-question"
            rows={1}
            maxLength={2_000}
            value={value}
            disabled={!activeTrip || asking}
            placeholder={
              activeTrip
                ? `Ask about ${tripDestination(activeTrip) || tripEventFallback(activeTrip)}`
                : "Prepare a trip before asking"
            }
            onChange={(event) => onValueChange(event.target.value)}
            onKeyDown={handleKeyDown}
          />
          <button
            className="send-button"
            type="button"
            disabled={!value.trim() || !activeTrip || asking}
            aria-label={asking ? "Answering" : "Send question"}
            onClick={send}
          >
            <Icon name="send" />
          </button>
        </div>
      </div>
    </section>
  );
}

function tripEventFallback(trip: Trip): string {
  return tripLabel(trip).split(" · ")[0] || "your trip";
}
