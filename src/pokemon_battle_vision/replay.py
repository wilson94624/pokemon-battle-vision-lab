"""Replay-specific identity and output path helpers.

The engine's data model is replay-agnostic; this module keeps the small amount
of per-replay naming metadata out of checkpoint implementations.  The legacy
``win-01`` layout remains the default for backwards compatibility.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


DEFAULT_REPLAY_ID = "win-01"


def normalize_replay_id(value: Optional[str]) -> str:
    replay_id = (value or DEFAULT_REPLAY_ID).strip()
    if not replay_id:
        raise ValueError("replay_id 不可為空")
    if replay_id in {".", ".."} or any(char in replay_id for char in "/\\"):
        raise ValueError("replay_id 不可包含路徑分隔符")
    return replay_id


def resolve_project_path(project_root: Path, path: Path) -> Path:
    """Resolve CLI paths relative to ``--project-root`` consistently."""
    candidate = Path(path)
    return (candidate if candidate.is_absolute() else Path(project_root) / candidate).resolve()


@dataclass(frozen=True)
class ReplayContext:
    """A stable identifier plus optional namespace for generated artifacts."""

    replay_id: str = DEFAULT_REPLAY_ID
    output_root: Optional[Path] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "replay_id", normalize_replay_id(self.replay_id))
        if self.output_root is not None:
            object.__setattr__(self, "output_root", Path(self.output_root))

    def checkpoint_dir(self, project_root: Path, checkpoint: str) -> Path:
        """Return the conventional output directory for this replay."""
        root = Path(project_root)
        if self.output_root is not None:
            base = self.output_root if self.output_root.is_absolute() else root / self.output_root
        elif self.replay_id == DEFAULT_REPLAY_ID:
            base = root / "outputs"
        else:
            base = root / "outputs" / "replays" / self.replay_id
        return base / checkpoint

    def annotate(self, payload: dict) -> dict:
        payload.setdefault("replay_id", self.replay_id)
        return payload
