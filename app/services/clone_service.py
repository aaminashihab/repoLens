"""GitHub repository cloning utilities."""

from collections.abc import Generator
from contextlib import contextmanager
import logging
import os
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

    def _authenticated_url(self, normalized_url: str, token: str | None) -> str:
        """Splice token@ right after https:// if a token is provided."""
        if token:
            return normalized_url.replace("https://", f"https://{token}@")
        return normalized_url

    def clone_repository(self, repo_url: str, github_token: str | None = None) -> Path:
        """Clone a GitHub repository and return its local working-tree path.

        The returned directory is created under a system temporary directory.
        Its caller is responsible for removing the parent temporary directory
        after repository processing has completed.
        """
        normalized_url = self.validate_github_url(repo_url)
        token = github_token if github_token is not None else os.getenv("GITHUB_TOKEN")
        auth_url = self._authenticated_url(normalized_url, token)

        logger.info("Cloning GitHub repository", extra={"repo_url": normalized_url})
        working_directory: Path | None = None
        try:
            working_directory = Path(tempfile.mkdtemp(prefix="repolens-"))
            repository_path = working_directory / "repository"
            Repo.clone_from(auth_url, repository_path)
        except (GitError, OSError) as exc:
            if working_directory is not None:
                shutil.rmtree(working_directory, ignore_errors=True)
            exc_str = str(exc)
            if token:
                exc_str = re.sub(re.escape(token), "***", exc_str)
            logger.error(
                "Failed to clone GitHub repository: %s",
                exc_str,
                extra={"repo_url": normalized_url},
            )
            raise RepositoryCloneError(
                "Unable to clone the GitHub repository. Confirm that the URL is "
                "correct and that the repository is accessible."
            ) from None

        logger.info(
            "GitHub repository cloned successfully",
            extra={"repo_url": normalized_url, "repository_path": str(repository_path)},
        )
        return repository_path

    @contextmanager
    def clone_repository_context(self, repo_url: str, github_token: str | None = None) -> Generator[Path, None, None]:
        """Clone a GitHub repository and yield its local working-tree path,
        ensuring cleanup of the parent temporary directory upon exit.
        """
        repository_path = self.clone_repository(repo_url, github_token=github_token)
        try:
            yield repository_path
        finally:
            shutil.rmtree(repository_path.parent, ignore_errors=True)

    @classmethod
    def validate_github_url(cls, repo_url: str) -> str:
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
