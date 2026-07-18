"""Replay workspace catalog contract."""

from typing import Protocol, Tuple

from ..domain.replay import ReplayWorkspaceLocation


class ReplayCatalog(Protocol):
    """Discover replay workspaces without assuming their processing completeness."""

    def list_workspaces(self) -> Tuple[ReplayWorkspaceLocation, ...]:
        ...
