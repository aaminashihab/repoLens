"""Git repository acquisition service."""

import logging
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from git import GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo


logger = logging.getLogger(__name__)


class InvalidRepositoryUrlError(ValueError):
    """Raised when a repository URL is not a supported GitHub HTTPS URL."""


class RepositoryCloneError(RuntimeError):
    """Raised when Git cannot clone a validated repository."""


class RepositoryService:
    """Clone GitHub repositories into isolated temporary directories."""

    _SUPPORTED_HOSTS = {"github.com", "www.github.com"}

    def clone_repository(self, repo_url: str) -> Path:
        """Clone ``repo_url`` and return the path of its local working tree.

        The caller owns the returned temporary directory and is responsible for
        removing it after any later indexing work has completed.
        """
        normalized_url = self._validate_github_url(repo_url)
        working_directory = Path(tempfile.mkdtemp(prefix="repolens-"))
        repository_path = working_directory / "repository"

        logger.info("Cloning GitHub repository", extra={"repo_url": normalized_url})
        try:
            Repo.clone_from(normalized_url, repository_path)
        except (GitCommandError, InvalidGitRepositoryError, NoSuchPathError) as exc:
            shutil.rmtree(working_directory, ignore_errors=True)
            logger.exception(
                "Failed to clone GitHub repository",
                extra={"repo_url": normalized_url},
            )
            raise RepositoryCloneError(
                "Unable to clone the GitHub repository. Confirm that the URL is "
                "correct and that the repository is accessible."
            ) from exc
        except OSError as exc:
            shutil.rmtree(working_directory, ignore_errors=True)
            logger.exception("Failed to prepare repository working directory")
            raise RepositoryCloneError(
                "Unable to prepare a local working directory for the repository."
            ) from exc

        logger.info(
            "GitHub repository cloned successfully",
            extra={"repo_url": normalized_url, "repository_path": str(repository_path)},
        )
        return repository_path

    @classmethod
    def _validate_github_url(cls, repo_url: str) -> str:
        """Validate and normalize a public GitHub HTTPS repository URL."""
        parsed_url = urlparse(repo_url)
        if (
            parsed_url.scheme != "https"
            or parsed_url.hostname not in cls._SUPPORTED_HOSTS
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

        if not owner or not repository:
            raise InvalidRepositoryUrlError(
                "Repository URL must include both an owner and repository name."
            )

        return f"https://github.com/{owner}/{repository}.git"
