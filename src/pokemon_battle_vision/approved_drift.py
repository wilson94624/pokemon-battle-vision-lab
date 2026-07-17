"""Approved upstream metadata drift 的精確 hash registry。"""

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from jsonschema import Draft202012Validator

from .errors import InputError


REGISTRY_PATH = Path("references/approved_upstream_metadata_drift.json")
REGISTRY_SCHEMA_PATH = Path("schemas/approved_upstream_metadata_drift.schema.json")


class ApprovedDriftRegistry:
    """只接受精確列入 registry 的 metadata drift；不適用 direct payload。"""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = payload
        self._records: Dict[Tuple[str, str, str, str], Mapping[str, Any]] = {}
        ids = []
        for record in payload["records"]:
            drift_id = str(record["drift_id"])
            ids.append(drift_id)
            key = (
                str(record["consumer_checkpoint"]),
                str(record["source_path"]),
                str(record["frozen_snapshot_sha256"]),
                str(record["approved_current_sha256"]),
            )
            if key in self._records:
                raise InputError("Approved drift registry 含重複 exact hash tuple")
            self._records[key] = record
        if len(ids) != len(set(ids)):
            raise InputError("Approved drift registry 的 drift_id 不可重複")

    @classmethod
    def from_project(cls, project_root: Path) -> "ApprovedDriftRegistry":
        root = project_root.resolve()
        path = root / REGISTRY_PATH
        schema_path = root / REGISTRY_SCHEMA_PATH
        if not path.is_file() or not schema_path.is_file():
            raise InputError("Approved drift registry 或 schema 不存在")
        payload = json.loads(path.read_text(encoding="utf-8"))
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=None).validate(payload)
        return cls(payload)

    def verify(
        self,
        consumer_checkpoint: str,
        source_path: str,
        frozen_snapshot_sha256: str,
        current_sha256: str,
    ) -> Optional[Mapping[str, Any]]:
        """相同 hash 不需 approval；不同時只接受 exact allowlist tuple。"""
        if current_sha256 == frozen_snapshot_sha256:
            return None
        key = (
            consumer_checkpoint,
            source_path,
            frozen_snapshot_sha256,
            current_sha256,
        )
        record = self._records.get(key)
        if record is None:
            raise InputError(
                "未核准 upstream drift：{} {} {} -> {}".format(
                    consumer_checkpoint,
                    source_path,
                    frozen_snapshot_sha256,
                    current_sha256,
                )
            )
        return record
