def validate_safe_id(id: str, label: str) -> None:
    """Validate that the given ID is safe and does not allow path traversal."""
    if (
        not id
        or "/" in id
        or "\\" in id
        or id in {".", ".."}
    ):
        raise ValueError(f"{label} must be a single directory or file name without path separators.")
