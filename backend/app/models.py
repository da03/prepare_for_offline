"""Pydantic request/response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Support = Literal["high", "medium", "low"]
AnswerMode = Literal[
    "answer_card",
    "structured_fact",
    "generated_from_local_sources",
    "abstained",
]


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    pack_id: str | None = None
    context_id: str | None = None
    conversation_id: str | None = None


class SourceRef(BaseModel):
    source_id: str
    title: str = ""
    snippet: str = ""
    publisher: str | None = None
    as_of: str | None = None
    freshness_class: str | None = None
    freshness: str | None = None
    expires_at: str | None = None


class ChatResponse(BaseModel):
    answer: str
    support: Support
    answer_mode: AnswerMode
    sources: list[SourceRef] = Field(default_factory=list)
    stale: bool = False
    queued_for_verification: bool = False
    expert_used: str | None = None
    debug: dict[str, Any] | None = None
    conversation_id: str | None = None
    message_id: str | None = None


ContextType = Literal["trip", "conference", "course", "project", "emergency", "custom"]
PrivacyMode = Literal["local_only", "allow_online_planning"]
PreparationQuality = Literal["fast", "final"]


class ContextCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    context_type: ContextType = "custom"
    goal: str = Field(default="", max_length=4000)
    starts_at: str | None = None
    ends_at: str | None = None
    languages: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    expected_needs: list[str] = Field(default_factory=list)
    storage_budget_mb: int = Field(default=1200, ge=50, le=50_000)
    privacy_mode: PrivacyMode = "local_only"
    preparation_quality: PreparationQuality = "fast"
    template_id: str | None = None


class ContextUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    context_type: ContextType | None = None
    goal: str | None = Field(default=None, max_length=4000)
    starts_at: str | None = None
    ends_at: str | None = None
    languages: list[str] | None = None
    interests: list[str] | None = None
    expected_needs: list[str] | None = None
    storage_budget_mb: int | None = Field(default=None, ge=50, le=50_000)
    privacy_mode: PrivacyMode | None = None
    preparation_quality: PreparationQuality | None = None
    template_id: str | None = None


class ContextSourceCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    source_type: Literal["text", "web", "file", "structured"] = "text"
    url: str | None = Field(default=None, max_length=2000)
    local_path: str | None = Field(default=None, max_length=2000)
    content: str = Field(default="", max_length=8_000_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextSourceUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    content: str | None = Field(default=None, max_length=8_000_000)
    enabled: bool | None = None
    metadata: dict[str, Any] | None = None


class ConversationCreate(BaseModel):
    context_id: str | None = None
    title: str = Field(default="New conversation", min_length=1, max_length=200)


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class SettingsUpdate(BaseModel):
    theme: Literal["system", "light", "dark"] | None = None
    active_context_id: str | None = None
    privacy_mode: PrivacyMode | None = None
    default_storage_budget_mb: int | None = Field(default=None, ge=50, le=50_000)
    show_advanced: bool | None = None
    optimize_in_background: bool | None = None
    search_mode: Literal["automatic", "official_only", "off"] | None = None
    ask_history_window: int | None = Field(default=None, ge=0, le=5)


class AttachmentInput(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    content: str = Field(default="", max_length=12_000_000)
    media_type: str = Field(default="text/plain", max_length=100)
    kind: Literal["text", "file"] = "file"
    encoding: Literal["utf-8", "data-url"] = "utf-8"
    size_bytes: int | None = Field(default=None, ge=0, le=8 * 1024 * 1024)


class TripParseRequest(BaseModel):
    text: str = Field(min_length=3, max_length=4000)
    attachments: list[AttachmentInput] = Field(default_factory=list)


class TripUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    destination: str | None = Field(default=None, max_length=200)
    event: str | None = Field(default=None, max_length=300)
    starts_at: str | None = None
    ends_at: str | None = None
    languages: list[str] | None = None
    traveler_needs: list[str] | None = None
    needs: list[str] | None = None
    dates: dict[str, str | None] | None = None
    search_enabled: bool | None = None


class TripPrepareRequest(BaseModel):
    source_ids: list[str] | None = None
    optimize: bool = True
    discover: bool = True


class AskRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    trip_id: str
    conversation_id: str | None = None
    new_topic: bool = False
    check_latest: bool = False


UIAction = Literal[
    "show_history",
    "new_conversation",
    "switch_context",
    "create_context",
    "prepare_context",
    "add_source",
    "show_context_status",
    "show_settings",
    "show_unresolved",
    "show_storage",
    "delete_context",
    "answer_question",
]


class CommandRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None
    context_id: str | None = None
    confirmed: bool = False


class CommandResponse(BaseModel):
    kind: Literal["answer", "ui_action", "workflow", "clarification"]
    conversation_id: str
    message_id: str | None = None
    message: str = ""
    action: UIAction | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    requires_confirmation: bool = False
    answer: str | None = None
    support: Support | None = None
    answer_mode: AnswerMode | None = None
    sources: list[SourceRef] = Field(default_factory=list)
    stale: bool = False
    queued_for_verification: bool = False


class PackSummary(BaseModel):
    pack_id: str
    title: str
    ready: bool
    size_bytes: int
    created_at: str
    manifest: dict[str, Any]


class QueueItem(BaseModel):
    id: int
    question: str
    offline_answer: str | None = None
    offline_support: str | None = None
    status: str
    verified_answer: str | None = None
    changed: bool | None = None
    created_at: str
    verified_at: str | None = None


class StorageInfo(BaseModel):
    home: str
    total_bytes: int
    pack_count: int
