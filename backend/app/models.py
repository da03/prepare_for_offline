"""PAW Offline API models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    reply_to_message_id: str | None = Field(default=None, max_length=128)
