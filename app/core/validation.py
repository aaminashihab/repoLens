import re

# Allowlist: alphanumeric, underscore, hyphen only. Max 128 characters.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")

# Windows reserved device names that must never appear as file/dir names.
# On Windows, Path("storage/indexes/NUL") opens the null device, not a file.
_WINDOWS_RESERVED_RE = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", re.IGNORECASE
)


def validate_safe_id(id: str, label: str) -> None:
    """Validate that the given ID is safe for use as a file/directory name.

    Enforces an allowlist (alphanumeric, underscore, hyphen) and explicitly
    blocks Windows reserved device names (NUL, CON, COM1–COM9, etc.) which
    otherwise silently misdirect file I/O on Windows hosts.
    """
    if not isinstance(id, str) or not _SAFE_ID_RE.match(id):
        raise ValueError(
            f"{label} must contain only letters, digits, underscores, or "
            f"hyphens and be between 1 and 128 characters."
        )
    if _WINDOWS_RESERVED_RE.match(id):
        raise ValueError(
            f"{label} must not be a Windows reserved device name (e.g. NUL, CON, COM1)."
        )
