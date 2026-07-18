"""Filesystem-backed artifact reader and replay workspace discovery."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Optional, Tuple

from ...domain.replay import ReplayLayout, ReplayWorkspaceLocation
from ...ports.artifacts import ArtifactReadError


_CHECKPOINT_DIRECTORY = re.compile(r"^checkpoint-1[a-j]$", re.IGNORECASE)


class FilesystemArtifactReader:
    """Read JSON objects while preserving the source file exactly as-is."""

    def read_json(self, path: Path) -> Mapping[str, Any]:
        try:
            with Path(path).open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactReadError("無法讀取 JSON artifact {}：{}".format(path, exc)) from exc
        if not isinstance(payload, dict):
            raise ArtifactReadError("JSON artifact 頂層必須是 object：{}".format(path))
        return payload


class FilesystemReplayCatalog:
    """Discover legacy and replay-namespaced workspaces without modifying them."""

    def __init__(
        self,
        project_root: Path,
        outputs_root: Optional[Path] = None,
        legacy_replay_id: str = "win-01",
    ) -> None:
        self.project_root = Path(project_root).resolve()
        configured_outputs = Path(outputs_root) if outputs_root is not None else Path("outputs")
        self.outputs_root = (
            configured_outputs
            if configured_outputs.is_absolute()
            else self.project_root / configured_outputs
        ).resolve()
        if not legacy_replay_id.strip():
            raise ValueError("legacy_replay_id 不可為空")
        self.legacy_replay_id = legacy_replay_id

    def list_workspaces(self) -> Tuple[ReplayWorkspaceLocation, ...]:
        if not self.outputs_root.is_dir():
            return ()

        rows = []
        if any(
            path.is_dir() and _CHECKPOINT_DIRECTORY.fullmatch(path.name)
            for path in self.outputs_root.iterdir()
        ):
            rows.append(
                ReplayWorkspaceLocation(
                    replay_id=self.legacy_replay_id,
                    root=self.outputs_root,
                    layout=ReplayLayout.LEGACY,
                )
            )

        namespaced_root = self.outputs_root / "replays"
        if namespaced_root.is_dir():
            for path in sorted(namespaced_root.iterdir(), key=lambda item: item.name):
                if path.is_dir() and not path.name.startswith("."):
                    rows.append(
                        ReplayWorkspaceLocation(
                            replay_id=path.name,
                            root=path.resolve(),
                            layout=ReplayLayout.NAMESPACED,
                        )
                    )

        rows.sort(key=lambda row: row.replay_id)
        return tuple(rows)
