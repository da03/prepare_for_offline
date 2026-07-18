import { useEffect, useRef } from "react";
import { Icon } from "./Icon";

export interface FollowUpTarget {
  messageId: string;
  question: string;
}

export interface AnswerTurn {
  id: string;
  question: string;
  answer: string;
  state: "working" | "complete" | "error";
  status: string;
  refined: boolean;
  conversationId?: string;
  answerMessageId?: string;
  isFollowUp?: boolean;
}

interface AskPageProps {
  turns: AnswerTurn[];
  value: string;
  asking: boolean;
  followUpTarget: FollowUpTarget | null;
  onValueChange: (value: string) => void;
  onSubmit: (question: string) => void;
  onFollowUp: (turn: AnswerTurn) => void;
  onCancelFollowUp: () => void;
}

export function AskPage({
  turns,
  value,
  asking,
  followUpTarget,
  onValueChange,
  onSubmit,
  onFollowUp,
  onCancelFollowUp,
}: AskPageProps) {
  const composer = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (followUpTarget) composer.current?.focus();
  }, [followUpTarget]);

  function send() {
    const question = value.trim();
    if (question && !asking) onSubmit(question);
  }

  return (
    <section className="ask-page" aria-label="Ask PAW Offline">
      <div className="ask-scroll">
        {!turns.length ? (
          <div className="empty-state">
            <div className="paw-mark" aria-hidden="true">
              <img src="/favicon.svg" alt="" />
            </div>
            <h1>Ask anything, anywhere.</h1>
            <p>PAW programs run locally, even without a connection.</p>
          </div>
        ) : (
          <div className="answer-feed">
            {turns.map((turn) => (
              <article
                key={turn.id}
                className={`answer-turn ${turn.isFollowUp ? "answer-turn--follow-up" : ""}`}
              >
                {turn.isFollowUp ? (
                  <span className="turn-kind">Follow-up</span>
                ) : null}
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
                    {turn.state === "complete" && turn.answerMessageId ? (
                      <div className="answer-actions">
                        <button
                          className="follow-up-button"
                          type="button"
                          disabled={asking}
                          aria-label={`Follow up on: ${turn.question}`}
                          onClick={() => onFollowUp(turn)}
                        >
                          <Icon name="reply" size={16} />
                          Follow up
                        </button>
                      </div>
                    ) : null}
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}
      </div>

      <div className="ask-composer-region">
        {followUpTarget ? (
          <div className="follow-up-context" role="status">
            <div>
              <span>Following up on</span>
              <strong>{followUpTarget.question}</strong>
            </div>
            <button
              className="icon-button icon-button--compact"
              type="button"
              aria-label="Cancel follow-up"
              disabled={asking}
              onClick={onCancelFollowUp}
            >
              <Icon name="close" size={16} />
            </button>
          </div>
        ) : null}
        <div className="question-composer">
          <label className="sr-only" htmlFor="paw-question">
            Ask anything
          </label>
          <textarea
            ref={composer}
            id="paw-question"
            rows={1}
            maxLength={4000}
            value={value}
            placeholder={
              followUpTarget
                ? "Ask a follow-up…"
                : turns.length
                  ? "Ask a new question…"
                  : "Ask anything…"
            }
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
