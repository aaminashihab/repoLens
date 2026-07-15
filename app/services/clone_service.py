"""GitHub repository cloning utilities."""

import logging
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from git import GitError, Repo


logger = logging.getLogger(__name__)


class InvalidRepositoryUrlError(ValueError):
    """Raised when a repository URL is not a supported GitHub HTTPS URL."""


class RepositoryCloneError(RuntimeError):
    """Raised when a validated repository cannot be cloned."""


class CloneService:
    """Clone GitHub repositories into isolated temporary directories."""

    _SUPPORTED_HOSTS = {"github.com", "www.github.com"}
    _GITHUB_PATH_PART = re.compile(r"^[A-Za-z0-9_.-]+$")

    def clone_repository(self, repo_url: str) -> Path:
        """Clone a GitHub repository and return its local working-tree path.

        The returned directory is created under a system temporary directory.
        Its caller is responsible for removing the parent temporary directory
        after repository processing has completed.
        """
        normalized_url = self._validate_github_url(repo_url)
        logger.info("Cloning GitHub repository", extra={"repo_url": normalized_url})
        working_directory: Path | None = None
        try:
            working_directory = Path(tempfile.mkdtemp(prefix="repolens-"))
            repository_path = working_directory / "repository"
            Repo.clone_from(normalized_url, repository_path)
        except (GitError, OSError) as exc:
            if working_directory is not None:
                shutil.rmtree(working_directory, ignore_errors=True)
            logger.exception(
                "Failed to clone GitHub repository",
                extra={"repo_url": normalized_url},
            )
            raise RepositoryCloneError(
                "Unable to clone the GitHub repository. Confirm that the URL is "
                "correct and that the repository is accessible."
            ) from exc

        logger.info(
            "GitHub repository cloned successfully",
            extra={"repo_url": normalized_url, "repository_path": str(repository_path)},
        )
        return repository_path

    @classmethod
    def _validate_github_url(cls, repo_url: str) -> str:
        """Validate and normalize a GitHub HTTPS repository URL."""
        if not isinstance(repo_url, str):
            raise InvalidRepositoryUrlError("Repository URL must be a string.")

        try:
            parsed_url = urlparse(repo_url)
            port = parsed_url.port
        except ValueError as exc:
            raise InvalidRepositoryUrlError("Repository URL is malformed.") from exc

        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname not in cls._SUPPORTED_HOSTS
            or port is not None
            or parsed_url.username is not None
            or parsed_url.password is not None
        ):
            raise InvalidRepositoryUrlError(
                "Repository URL must be an HTTPS URL hosted on github.com."
            )

        repository_parts = [part for part in parsed_url.path.split("/") if part]
        if len(repository_parts) != 2 or parsed_url.query or parsed_url.fragment:
            raise InvalidRepositoryUrlError(
                "Repository URL must use the format https://github.com/owner/repository."
            )

        owner, repository = repository_parts
        if repository.endswith(".git"):
            repository = repository.removesuffix(".git")

        if not owner or not repository or not all(
            cls._GITHUB_PATH_PART.fullmatch(part) for part in (owner, repository)
        ):
            raise InvalidRepositoryUrlError(
                "Repository URL must include a valid owner and repository name."
            )

        return f"https://github.com/{owner}/{repository}.git"
