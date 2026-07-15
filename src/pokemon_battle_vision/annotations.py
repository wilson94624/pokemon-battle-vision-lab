"""只提供 acceptance 所需 schema 驗證；Checkpoint 1A 不產生 annotation draft。"""

from pathlib import Path
from typing import Any, Dict

import jsonschema

from .config import load_json
from .errors import InputError


def validate_annotation_document(document: Dict[str, Any], schema_path: Path) -> None:
    schema = load_json(schema_path)
    try:
        jsonschema.Draft202012Validator(schema).validate(document)
    except jsonschema.ValidationError as exc:
        location = "/".join(str(part) for part in exc.absolute_path) or "<root>"
        raise InputError("annotation schema 驗證失敗（{}）：{}".format(location, exc.message)) from exc
