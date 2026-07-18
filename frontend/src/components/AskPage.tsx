import type { StarterQuestion } from "../lib/api";
import { Icon } from "./Icon";

export interface AnswerTurn {
  id: string;
  question: string;
  answer: string;
  state: "working" | "complete" | "error";
  status: string;
  refined: boolean;
  startsNewTopic?: boolean;
}

interface AskPageProps {
  starters: StarterQuestion[];
  turns: AnswerTurn[];
  value: string;
  asking: boolean;
  nextStartsNewTopic: boolean;
  onValueChange: (value: string) => void;
  onSubmit: (question: string) => void;
  onNewTopic: () => void;
}

export function AskPage({
  starters,
  turns,
  value,
  asking,
  nextStartsNewTopic,
  onValueChange,
  onSubmit,
  onNewTopic,
}: AskPageProps) {
  function send() {
    const question = value.trim();
    if (question && !asking) onSubmit(question);
  }

  return (
    <section className="ask-page" aria-label="Ask PAW Offline">
      <div className="ask-scroll">
        {!turns.length ? (
          <div className="starter-state">
            <div className="paw-mark" aria-hidden="true">
              <img src="/favicon.svg" alt="" />
            </div>
            <h1>Ask anything, anywhere.</h1>
            <p>PAW programs run locally, even without a connection.</p>
            <div className="starter-list" aria-label="Starter questions">
              {starters.map((starter) => (
                <button
                  key={starter.id}
                  type="button"
                  onClick={() => onSubmit(starter.text)}
                >
                  <span>{starter.text}</span>
                  <Icon name="send" size={16} />
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="answer-feed">
            {turns.map((turn) => (
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
                    aria-live="polite"
                    aria-busy={turn.state === "working"}
                  >
                    <div className="answer-card__brand" aria-hidden="true">
                      <img src="/favicon.svg" alt="" />
                    </div>
                    <div className="answer-card__body">
                      {turn.answer ? (
                        <p className="answer-copy">{turn.answer}</p>
                      ) : null}
                      {turn.state === "working" ? (
                        <div className="semantic-status" role="status">
                          <span className="status-pulse" aria-hidden="true" />
                          {turn.status || "Thinking…"}
                        </div>
                      ) : null}
                      {turn.state === "error" ? (
                        <p className="form-error">{turn.status}</p>
                      ) : null}
                    </div>
                  </div>
                </article>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="ask-composer-region">
        {turns.length ? (
          <button
            className="new-topic-button"
            type="button"
            disabled={asking}
            onClick={onNewTopic}
          >
            {nextStartsNewTopic ? "Next message starts a new topic" : "New topic"}
          </button>
        ) : null}
        <div className="question-composer">
          <label className="sr-only" htmlFor="paw-question">
            Ask anything
          </label>
          <textarea
            id="paw-question"
            rows={1}
            maxLength={4000}
            value={value}
            placeholder="Ask anything…"
            onChange={(event) => onValueChange(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                send();
              }
            }}
          />
          <button
            className="send-button"
            type="button"
            aria-label={asking ? "Thinking" : "Send question"}
            disabled={!value.trim() || asking}
            onClick={send}
          >
            <Icon name="send" size={20} />
          </button>
        </div>
      </div>
    </section>
  );
}
