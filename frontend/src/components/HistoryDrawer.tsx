import { useEffect, useState } from "react";
import type { ConversationSummary } from "../lib/api";
import { Icon } from "./Icon";
import { SurfaceDialog } from "./SurfaceDialog";

interface HistoryDrawerProps {
  conversations: ConversationSummary[];
  activeConversationId: string | null;
  loading: boolean;
  onClose: () => void;
  onSearch: (query: string) => void;
  onSelect: (conversationId: string) => void;
  onDelete: (conversationId: string) => Promise<void>;
}

function formatUpdated(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const today = new Date();
  const sameDay = date.toDateString() === today.toDateString();
  return new Intl.DateTimeFormat(undefined, {
    ...(sameDay
      ? { hour: "numeric", minute: "2-digit" }
      : { month: "short", day: "numeric" }),
  }).format(date);
}

export function HistoryDrawer({
  conversations,
  activeConversationId,
  loading,
  onClose,
  onSearch,
  onSelect,
  onDelete,
}: HistoryDrawerProps) {
  const [query, setQuery] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => onSearch(query.trim()), 180);
    return () => window.clearTimeout(timer);
  }, [onSearch, query]);

  async function remove(conversationId: string) {
    setDeletingId(conversationId);
    setError(null);
    try {
      await onDelete(conversationId);
      setConfirmDeleteId(null);
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Could not delete this item.",
      );
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <SurfaceDialog
      title="History"
      description="Review previous questions and their follow-ups."
      variant="drawer"
      onClose={onClose}
    >
      <div className="drawer-tools">
        <div className="search-field">
          <Icon name="search" size={18} />
          <label className="sr-only" htmlFor="history-search">
            Search previous questions
          </label>
          <input
            data-autofocus
            id="history-search"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search history"
          />
        </div>
      </div>

      {error ? (
        <p className="form-error" role="alert">
          {error}
        </p>
      ) : null}

      <div className="history-list" aria-busy={loading}>
        {loading && !conversations.length ? (
          <p className="inline-empty">Loading conversations…</p>
        ) : null}
        {!loading && !conversations.length ? (
          <p className="inline-empty">
            {query
              ? "No questions match your search."
              : "No saved questions yet."}
          </p>
        ) : null}
        {conversations.map((conversation) => {
          const active = conversation.conversation_id === activeConversationId;
          const confirming = confirmDeleteId === conversation.conversation_id;
          return (
            <article
              key={conversation.conversation_id}
              className={`history-item ${active ? "is-active" : ""}`}
            >
              {confirming ? (
                <div className="history-confirm">
                  <p>
                    Delete <strong>{conversation.title}</strong> and its
                    follow-ups?
                  </p>
                  <div className="button-row">
                    <button
                      className="button button--quiet button--small"
                      type="button"
                      disabled={deletingId !== null}
                      onClick={() => setConfirmDeleteId(null)}
                    >
                      Cancel
                    </button>
                    <button
                      className="button button--danger button--small"
                      type="button"
                      disabled={deletingId !== null}
                      onClick={() => remove(conversation.conversation_id)}
                    >
                      {deletingId === conversation.conversation_id
                        ? "Deleting…"
                        : "Delete"}
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <button
                    className="history-main"
                    type="button"
                    aria-current={active ? "true" : undefined}
                    onClick={() => onSelect(conversation.conversation_id)}
                  >
                    <span className="history-title">{conversation.title}</span>
                    <span className="history-meta">
                      {conversation.question_count ?? 0}{" "}
                      {(conversation.question_count ?? 0) === 1
                        ? "question"
                        : "questions"}{" "}
                      ·{" "}
                      {formatUpdated(conversation.updated_at)}
                    </span>
                  </button>
                  <button
                    className="icon-button icon-button--compact history-delete"
                    type="button"
                    aria-label={`Delete ${conversation.title}`}
                    onClick={() => setConfirmDeleteId(conversation.conversation_id)}
                  >
                    <Icon name="trash" size={17} />
                  </button>
                </>
              )}
            </article>
          );
        })}
      </div>
    </SurfaceDialog>
  );
}
