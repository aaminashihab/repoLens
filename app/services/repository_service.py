"""Backward-compatible repository service imports.

Use :class:`app.services.clone_service.CloneService` for new code.
"""

from app.services.clone_service import (
    CloneService,
    InvalidRepositoryUrlError,
    RepositoryCloneError,
)


RepositoryService = CloneService

__all__ = ["InvalidRepositoryUrlError", "RepositoryCloneError", "RepositoryService"]
