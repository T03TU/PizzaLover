"""Validated chat, metadata, ingestion, and evaluation wire formats."""

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
