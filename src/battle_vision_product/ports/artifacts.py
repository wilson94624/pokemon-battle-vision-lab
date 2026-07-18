"""Read-only artifact access contract."""

from pathlib import Path
from typing import Any, Mapping, Protocol


class ArtifactReadError(RuntimeError):
    """A product-facing error for unreadable or structurally invalid artifacts."""


class ArtifactReader(Protocol):
    """Read Engine artifacts without exposing storage details to the domain layer."""

    def read_json(self, path: Path) -> Mapping[str, Any]:
        ...
