from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ChatTurn(BaseModel):
    """A single turn in the conversation history."""

    role: Literal["user", "assistant"]
    # 4000-char cap: a single history turn carrying megabytes of text is always
    # malformed input and would bloat the LLM context window unnecessarily.
    content: str = Field(..., min_length=1, max_length=4000)


class AskRequest(BaseModel):
    """A question about the repository represented by an index."""

    index_id: str = Field(..., min_length=1, max_length=128, description="Repository index identifier.")
    # 2000-char cap prevents prompt-stuffing attacks where an adversary embeds
    # system-override instructions in a very long question field.
    question: str = Field(..., min_length=1, max_length=2000, description="Natural-language repository question.")
    history: list[ChatTurn] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def truncate_history(self) -> "AskRequest":
        if len(self.history) > 12:
            self.history = self.history[-12:]
        return self


class AskSource(BaseModel):
    """A repository symbol used to ground an answer."""

    file_path: str
    symbol_name: str
    score: float


class AskResponse(BaseModel):
    """A grounded answer and the repository symbols used to generate it."""

    answer: str
    sources: list[AskSource]
