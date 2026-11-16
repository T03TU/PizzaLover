"""Validated chat, metadata, ingestion, and evaluation wire formats."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Filters(BaseModel):
    """Optional exact metadata matches and inclusive minimum star rating."""

    franchise: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=100)
    min_rating: int | None = Field(default=None, ge=1, le=5)


class ChatRequest(BaseModel):
    """One independent question; previous turns are not injected as evidence."""

    question: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    filters: Filters = Field(default_factory=Filters)
    eval_enabled: bool = True
    chat_id: UUID | None = None


class ProfileRequest(BaseModel):
    """A named profile on this trusted local app; this is not a login account."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=80, pattern=r"\S")


class Review(BaseModel):
    """A customer review using the existing corpus's column names."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    Feedback_ID: str = Field(pattern=r"^FB-[A-Za-z0-9-]+$", max_length=64)
    Franchise: str = Field(min_length=1, max_length=100)
    Location: str = Field(min_length=1, max_length=100)
    Rating: int = Field(ge=1, le=5)
    Comment: str = Field(min_length=1, max_length=10000)


class RagasScores(BaseModel):
    """Four reference-free scores; null indicates a failed or missing metric."""

    faithfulness: float | None = None
    answer_relevancy: float | None = None
    context_precision: float | None = None
    context_utilization: float | None = None


class GoogleIngestRequest(BaseModel):
    """A country and optional region search with a bounded API request cost."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    country_code: str = Field(pattern=r"^[A-Z]{2}$")
    region: str = Field(default="", max_length=120)
    max_places: int = Field(default=10, ge=1, le=20)
