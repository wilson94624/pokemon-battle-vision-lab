"""Replay catalog domain records derived from immutable Engine artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple


class ReplayLayout(str, Enum):
    """Artifact layout recognized by the initial product catalog."""

    LEGACY = "legacy"
    NAMESPACED = "namespaced"


class CheckpointState(str, Enum):
    """Effective state of one checkpoint descriptor and its review evidence."""

    COMPLETE = "complete"
    NEEDS_REVIEW = "needs_review"
    INCOMPLETE = "incomplete"
    INVALID = "invalid"


class ReplayStatus(str, Enum):
    """Product-facing replay status; never inferred from replay-specific record counts."""

    READY = "ready"
    NEEDS_REVIEW = "needs_review"
    INCOMPLETE = "incomplete"
    INVALID = "invalid"


@dataclass(frozen=True)
class ReplayWorkspaceLocation:
    """Stable replay identity plus the existing artifact root."""

    replay_id: str
    root: Path
    layout: ReplayLayout

    def __post_init__(self) -> None:
        if not self.replay_id.strip():
            raise ValueError("replay_id 不可為空")


@dataclass(frozen=True)
class CheckpointEvidence:
    """Read-only descriptor evidence for one discovered checkpoint directory."""

    checkpoint: str
    root: Path
    descriptor_path: Optional[Path]
    declared_status: Optional[str]
    state: CheckpointState
    evidence_paths: Tuple[Path, ...] = ()
    issues: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplayInspection:
    """Replay status projected from the currently available Engine artifacts."""

    replay_id: str
    workspace_path: Path
    layout: ReplayLayout
    status: ReplayStatus
    checkpoints: Tuple[CheckpointEvidence, ...]
    issues: Tuple[str, ...] = ()

    @property
    def latest_checkpoint(self) -> Optional[str]:
        if not self.checkpoints:
            return None
        return self.checkpoints[-1].checkpoint

    @property
    def checkpoint_ids(self) -> Tuple[str, ...]:
        return tuple(row.checkpoint for row in self.checkpoints)
