"""Minimal read-only Engine Gateway contract for Milestone 2A."""

from typing import Protocol

from ..domain.replay import ReplayInspection, ReplayWorkspaceLocation


class EngineGateway(Protocol):
    """Inspect supported Engine artifacts without importing checkpoint internals."""

    def inspect_replay(self, workspace: ReplayWorkspaceLocation) -> ReplayInspection:
        ...
