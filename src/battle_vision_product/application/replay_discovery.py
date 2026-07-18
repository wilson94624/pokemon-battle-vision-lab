"""Application service for read-only replay catalog inspection."""

from __future__ import annotations

from typing import Dict, Tuple

from ..domain.replay import ReplayInspection
from ..ports.engine_gateway import EngineGateway
from ..ports.replay_catalog import ReplayCatalog


class ReplayDiscoveryService:
    """Combine catalog discovery with Engine artifact inspection."""

    def __init__(self, catalog: ReplayCatalog, engine_gateway: EngineGateway) -> None:
        self.catalog = catalog
        self.engine_gateway = engine_gateway

    def discover(self) -> Tuple[ReplayInspection, ...]:
        inspections = []
        identities: Dict[str, str] = {}
        for workspace in self.catalog.list_workspaces():
            previous = identities.get(workspace.replay_id)
            if previous is not None:
                raise ValueError(
                    "replay_id {} 同時對應多個 workspace：{}、{}".format(
                        workspace.replay_id,
                        previous,
                        workspace.root,
                    )
                )
            identities[workspace.replay_id] = str(workspace.root)
            inspections.append(self.engine_gateway.inspect_replay(workspace))
        inspections.sort(key=lambda row: row.replay_id)
        return tuple(inspections)
