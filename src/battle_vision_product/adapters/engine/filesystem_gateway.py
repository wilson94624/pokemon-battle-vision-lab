"""Read-only Engine Gateway backed by checkpoint reports and manifests."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Mapping, Optional, Sequence, Tuple

from ...domain.replay import (
    CheckpointEvidence,
    CheckpointState,
    ReplayInspection,
    ReplayStatus,
    ReplayWorkspaceLocation,
)
from ...ports.artifacts import ArtifactReadError, ArtifactReader


_CHECKPOINT_DIRECTORY = re.compile(r"^checkpoint-(1[a-j])$", re.IGNORECASE)
_TERMINAL_CHECKPOINT = "1J"
_REQUIRED_CHECKPOINTS = tuple("1{}".format(letter) for letter in "ABCDEFGHIJ")
_COMPLETE_STATUSES = {"approved", "complete", "pass"}
_INVALID_STATUS_TOKENS = ("error", "fail", "invalid")
_REVIEW_STATUS_TOKENS = ("approval", "needs_review", "pending", "review")


def _checkpoint_sort_key(checkpoint: str) -> Tuple[int, str]:
    match = re.fullmatch(r"(\d+)([A-Z])", checkpoint.upper())
    if match is None:
        return (10_000, checkpoint)
    return (int(match.group(1)), match.group(2))


def _descriptor_name(checkpoint: str) -> str:
    normalized = checkpoint.lower()
    if normalized in {"1a", "1b"}:
        return "checkpoint_{}_report.json".format(normalized)
    return "checkpoint{}_manifest.json".format(normalized)


def _state_from_status(status: Optional[str]) -> CheckpointState:
    if status is None or not status.strip():
        return CheckpointState.INCOMPLETE
    normalized = status.strip().lower()
    if normalized in _COMPLETE_STATUSES:
        return CheckpointState.COMPLETE
    if any(token in normalized for token in _INVALID_STATUS_TOKENS):
        return CheckpointState.INVALID
    if any(token in normalized for token in _REVIEW_STATUS_TOKENS):
        return CheckpointState.NEEDS_REVIEW
    return CheckpointState.INCOMPLETE


class FilesystemEngineGateway:
    """Project replay status from existing public Engine artifact evidence."""

    def __init__(self, artifact_reader: ArtifactReader) -> None:
        self.artifact_reader = artifact_reader

    def inspect_replay(self, workspace: ReplayWorkspaceLocation) -> ReplayInspection:
        checkpoint_dirs = self._checkpoint_directories(workspace.root)
        checkpoints = tuple(
            self._inspect_checkpoint(workspace, checkpoint, directory)
            for checkpoint, directory in checkpoint_dirs
        )
        status, replay_issues = self._replay_status(checkpoints)
        issues = tuple(issue for row in checkpoints for issue in row.issues) + replay_issues
        return ReplayInspection(
            replay_id=workspace.replay_id,
            workspace_path=workspace.root,
            layout=workspace.layout,
            status=status,
            checkpoints=checkpoints,
            issues=issues,
        )

    @staticmethod
    def _checkpoint_directories(root: Path) -> Tuple[Tuple[str, Path], ...]:
        if not root.is_dir():
            return ()
        rows = []
        for path in root.iterdir():
            match = _CHECKPOINT_DIRECTORY.fullmatch(path.name)
            if path.is_dir() and match is not None:
                rows.append((match.group(1).upper(), path))
        rows.sort(key=lambda row: _checkpoint_sort_key(row[0]))
        return tuple(rows)

    def _inspect_checkpoint(
        self,
        workspace: ReplayWorkspaceLocation,
        checkpoint: str,
        directory: Path,
    ) -> CheckpointEvidence:
        descriptor = directory / _descriptor_name(checkpoint)
        if not descriptor.is_file():
            return CheckpointEvidence(
                checkpoint=checkpoint,
                root=directory,
                descriptor_path=None,
                declared_status=None,
                state=CheckpointState.INCOMPLETE,
                issues=("{} 缺少 checkpoint descriptor".format(checkpoint),),
            )

        try:
            payload = self.artifact_reader.read_json(descriptor)
        except ArtifactReadError as exc:
            return CheckpointEvidence(
                checkpoint=checkpoint,
                root=directory,
                descriptor_path=descriptor,
                declared_status=None,
                state=CheckpointState.INVALID,
                evidence_paths=(descriptor,),
                issues=(str(exc),),
            )

        declared_status = self._optional_text(payload, "status")
        state = _state_from_status(declared_status)
        evidence_paths: List[Path] = [descriptor]
        issues: List[str] = []

        declared_checkpoint = self._optional_text(payload, "checkpoint")
        if declared_checkpoint is not None and declared_checkpoint.upper() != checkpoint:
            state = CheckpointState.INVALID
            issues.append(
                "{} descriptor checkpoint 不一致：{}".format(checkpoint, declared_checkpoint)
            )

        declared_replay_id = self._optional_text(payload, "replay_id")
        if declared_replay_id is not None and declared_replay_id != workspace.replay_id:
            state = CheckpointState.INVALID
            issues.append(
                "{} descriptor replay_id 不一致：{}".format(checkpoint, declared_replay_id)
            )

        if declared_status is None:
            issues.append("{} descriptor 缺少 status".format(checkpoint))
        elif state is CheckpointState.INCOMPLETE:
            issues.append("{} descriptor status 未知：{}".format(checkpoint, declared_status))

        # 1A report 本來停在人工 gate；既有 approved artifact 是完成該 gate 的證據。
        if checkpoint == "1A" and state is CheckpointState.NEEDS_REVIEW:
            state, approval_paths, approval_issues = self._apply_roi_approval(directory, state)
            evidence_paths.extend(approval_paths)
            issues.extend(approval_issues)

        return CheckpointEvidence(
            checkpoint=checkpoint,
            root=directory,
            descriptor_path=descriptor,
            declared_status=declared_status,
            state=state,
            evidence_paths=tuple(evidence_paths),
            issues=tuple(issues),
        )

    def _apply_roi_approval(
        self,
        directory: Path,
        fallback: CheckpointState,
    ) -> Tuple[CheckpointState, Tuple[Path, ...], Tuple[str, ...]]:
        approval = directory / "roi_approval.json"
        if not approval.is_file():
            return fallback, (), ()
        try:
            payload = self.artifact_reader.read_json(approval)
        except ArtifactReadError as exc:
            return CheckpointState.INVALID, (approval,), (str(exc),)
        approval_status = self._optional_text(payload, "status")
        if approval_status is not None and approval_status.lower() == "approved":
            return CheckpointState.COMPLETE, (approval,), ()
        return (
            fallback,
            (approval,),
            ("1A roi_approval status 不是 approved",),
        )

    @staticmethod
    def _optional_text(payload: Mapping[str, object], key: str) -> Optional[str]:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def _replay_status(
        checkpoints: Sequence[CheckpointEvidence],
    ) -> Tuple[ReplayStatus, Tuple[str, ...]]:
        if not checkpoints:
            return ReplayStatus.INCOMPLETE, ("尚未發現任何 checkpoint artifact",)
        if any(row.state is CheckpointState.INVALID for row in checkpoints):
            return ReplayStatus.INVALID, ()
        if any(row.state is CheckpointState.INCOMPLETE for row in checkpoints):
            return ReplayStatus.INCOMPLETE, ()
        if any(row.state is CheckpointState.NEEDS_REVIEW for row in checkpoints):
            return ReplayStatus.NEEDS_REVIEW, ()

        checkpoint_ids = tuple(row.checkpoint for row in checkpoints)
        if checkpoint_ids == _REQUIRED_CHECKPOINTS:
            return ReplayStatus.READY, ()
        latest = checkpoints[-1].checkpoint
        missing = tuple(
            checkpoint for checkpoint in _REQUIRED_CHECKPOINTS if checkpoint not in checkpoint_ids
        )
        return (
            ReplayStatus.INCOMPLETE,
            (
                "最新 checkpoint 為 {}；缺少完成證據：{}".format(
                    latest,
                    ", ".join(missing) if missing else _TERMINAL_CHECKPOINT,
                ),
            ),
        )
