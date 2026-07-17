import json
from pathlib import Path
from typing import Any

class JobService:
    """Store and retrieve background job statuses."""

    def __init__(self, storage_path: Path = Path("storage/jobs")) -> None:
        self._storage_path = storage_path
        self._storage_path.mkdir(parents=True, exist_ok=True)

    def update_job_status(self, job_id: str, status: str, error: str | None = None) -> None:
        job_file = self._storage_path / f"{job_id}.json"
        data: dict[str, Any] = {"status": status}
        if error is not None:
            data["error"] = error
        
        temporary_job_file = job_file.with_suffix(".json.tmp")
        temporary_job_file.write_text(json.dumps(data), encoding="utf-8")
        temporary_job_file.replace(job_file)

    def get_job_status(self, job_id: str) -> dict[str, Any] | None:
        job_file = self._storage_path / f"{job_id}.json"
        if not job_file.is_file():
            return None
        return json.loads(job_file.read_text(encoding="utf-8"))
