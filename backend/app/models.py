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


class SourceRef(BaseModel):
    source_id: str
    title: str = ""
    snippet: str = ""


class ChatResponse(BaseModel):
    answer: str
    support: Support
    answer_mode: AnswerMode
    sources: list[SourceRef] = []
    stale: bool = False
    queued_for_verification: bool = False
    expert_used: str | None = None
    debug: dict[str, Any] | None = None


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
