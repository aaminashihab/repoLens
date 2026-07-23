import json
import os
import tempfile
from pathlib import Path
from typing import Any

class JobService:
    """Store and retrieve background job statuses."""

    def __init__(self, storage_path: Path = Path("storage/jobs")) -> None:
        self._storage_path = storage_path
        self._storage_path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate_job_id(job_id: str) -> None:
        from app.core.validation import validate_safe_id
        validate_safe_id(job_id, "Job ID")

    def update_job_status(self, job_id: str, status: str, error: str | None = None) -> None:
        self._validate_job_id(job_id)
        job_file = self._storage_path / f"{job_id}.json"
        data: dict[str, Any] = {"status": status}
        if error is not None:
            data["error"] = error
        
        # BUG-NEW-4 FIX: Use a cryptographically unique temp file per write to
        # prevent race conditions when concurrent threads update the same job.
        # os.replace() is atomic on POSIX and Windows (same drive).
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=self._storage_path, prefix=f"{job_id}_", suffix=".json.tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f)
            except Exception:
                os.unlink(tmp_path)
                raise
            os.replace(tmp_path, job_file)
        except Exception:
            raise

    def get_job_status(self, job_id: str) -> dict[str, Any] | None:
        self._validate_job_id(job_id)
        job_file = self._storage_path / f"{job_id}.json"
        if not job_file.is_file():
            return None
        return json.loads(job_file.read_text(encoding="utf-8"))

    def delete_job_status(self, job_id: str) -> bool:
        self._validate_job_id(job_id)
        job_file = self._storage_path / f"{job_id}.json"
        if not job_file.is_file():
            return False
        job_file.unlink(missing_ok=True)
        return True
