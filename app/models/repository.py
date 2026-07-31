from typing import Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, Field


class RepositoryIndexRequest(BaseModel):
    """Input required to create a repository indexing job."""

    repo_url: AnyHttpUrl = Field(
        ...,
        description="Public or private repository URL to index.",
        examples=["https://github.com/owner/repo"],
    )
    github_token: str | None = Field(
        None,
        exclude=True,
        description="Optional GitHub token for private repositories.",
    )


class RepositoryIndexResponse(BaseModel):
    """Reference returned when a repository indexing job is accepted."""

    index_id: UUID
    status: Literal["processing"]
