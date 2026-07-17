from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    """A question about the repository represented by an index."""

    index_id: str = Field(..., min_length=1, description="Repository index identifier.")
    question: str = Field(..., min_length=1, description="Natural-language repository question.")


class AskSource(BaseModel):
    """A repository symbol used to ground an answer."""

    file_path: str
    symbol_name: str
    score: float


class AskResponse(BaseModel):
    """A grounded answer and the repository symbols used to generate it."""

    answer: str
    sources: list[AskSource]
