from typing import Literal
from pydantic import BaseModel, Field, model_validator


class ChatTurn(BaseModel):
    """A single turn in the conversation history."""

    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1)


class AskRequest(BaseModel):
    """A question about the repository represented by an index."""

    index_id: str = Field(..., min_length=1, description="Repository index identifier.")
    question: str = Field(..., min_length=1, description="Natural-language repository question.")
    history: list[ChatTurn] = Field(default_factory=list)

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
