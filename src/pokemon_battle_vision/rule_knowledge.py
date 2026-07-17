"""Checkpoint 1I 版本化、唯讀 Pokémon rule knowledge adapter。"""

import json
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from jsonschema import Draft202012Validator

from .errors import InputError
from .utils import sha256_file


DEFAULT_RULE_KNOWLEDGE_DIR = Path("knowledge/pokemon/rules/v1")


def normalize_rule_alias(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


class PokemonRuleKnowledgeBase:
    """只提供被 1I 明確採用的最小規則，不暴露 simulator 介面。"""

    def __init__(
        self,
        data_path: Path,
        manifest_path: Path,
        data_schema_path: Path,
        manifest_schema_path: Path,
    ) -> None:
        self.data_path = data_path.resolve()
        self.manifest_path = manifest_path.resolve()
        for path, label in (
            (self.data_path, "rule knowledge"),
            (self.manifest_path, "rule knowledge manifest"),
            (data_schema_path, "rule knowledge schema"),
            (manifest_schema_path, "rule knowledge manifest schema"),
        ):
            if not path.is_file():
                raise InputError("Checkpoint 1I {} 不存在：{}".format(label, path))

        self.payload = json.loads(self.data_path.read_text(encoding="utf-8"))
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self._validate_schema(data_schema_path, self.payload)
        self._validate_schema(manifest_schema_path, self.manifest)
        if sha256_file(self.data_path) != self.manifest["data"]["sha256"]:
            raise InputError("Checkpoint 1I rule knowledge hash 與 manifest 不一致")
        if self.payload["knowledge_version"] != self.manifest["knowledge_version"]:
            raise InputError("Checkpoint 1I rule knowledge version 不一致")

        self.sources = {
            str(row["source_id"]): row for row in self.payload["sources"]
        }
        self.moves_by_id = {
            str(row["move_id"]): row for row in self.payload["moves"]
        }
        self.move_aliases: Dict[str, Mapping[str, Any]] = {}
        for row in self.payload["moves"]:
            for alias in row["aliases"]:
                key = normalize_rule_alias(str(alias))
                if key in self.move_aliases:
                    raise InputError("Checkpoint 1I move alias 重複：{}".format(alias))
                self.move_aliases[key] = row
        self.species_types_by_id = {
            int(row["canonical_species_id"]): row
            for row in self.payload["species_types"]
        }
        self.ability_rules_by_id = {
            str(row["ability_id"]): row for row in self.payload["ability_rules"]
        }
        self.ability_aliases: Dict[str, Mapping[str, Any]] = {}
        for row in self.payload["ability_rules"]:
            for alias in row["aliases"]:
                key = normalize_rule_alias(str(alias))
                if key in self.ability_aliases:
                    raise InputError("Checkpoint 1I ability alias 重複：{}".format(alias))
                self.ability_aliases[key] = row
        self.explicit_rules = list(self.payload["explicit_rules"])
        self.target_rules_by_move = {
            str(row["move_id"]): row for row in self.payload["target_rules"]
        }
        self.type_chart = self.payload["type_effectiveness"]
        self.type_multipliers = {
            (str(row["attacking_type"]), str(row["defending_type"])): float(
                row["multiplier"]
            )
            for row in self.type_chart["entries"]
        }
        self.knowledge_by_id: Dict[str, Mapping[str, Any]] = {
            str(row["knowledge_id"]): row
            for collection in (
                self.payload["moves"],
                self.payload["species_types"],
                self.payload["ability_rules"],
                self.payload["explicit_rules"],
                self.payload["target_rules"],
            )
            for row in collection
        }
        self.knowledge_by_id[str(self.type_chart["knowledge_id"])] = self.type_chart
        self._validate_internal_consistency()

    @staticmethod
    def _validate_schema(path: Path, payload: Mapping[str, Any]) -> None:
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(payload)

    @classmethod
    def from_project(cls, project_root: Path) -> "PokemonRuleKnowledgeBase":
        root = project_root.resolve()
        directory = root / DEFAULT_RULE_KNOWLEDGE_DIR
        return cls(
            directory / "rule_knowledge.json",
            directory / "manifest.json",
            root / "schemas/pokemon_rule_knowledge.schema.json",
            root / "schemas/pokemon_rule_knowledge_manifest.schema.json",
        )

    def _validate_internal_consistency(self) -> None:
        counts = self.manifest["counts"]
        derived = {
            "sources": len(self.payload["sources"]),
            "moves": len(self.payload["moves"]),
            "species_types": len(self.payload["species_types"]),
            "type_effectiveness_entries": len(self.type_chart["entries"]),
            "ability_rules": len(self.payload["ability_rules"]),
            "explicit_rules": len(self.payload["explicit_rules"]),
            "target_rules": len(self.payload["target_rules"]),
        }
        if counts != derived:
            raise InputError(
                "Checkpoint 1I rule knowledge counts 不一致：{} != {}".format(
                    counts, derived
                )
            )
        identifiers = {
            "source_ids_unique": [row["source_id"] for row in self.payload["sources"]],
            "move_ids_unique": [row["move_id"] for row in self.payload["moves"]],
            "species_ids_unique": [
                row["canonical_species_id"] for row in self.payload["species_types"]
            ],
            "ability_rule_ids_unique": [
                row["ability_id"] for row in self.payload["ability_rules"]
            ],
            "rule_ids_unique": [self.type_chart["rule_id"]]
            + [row["rule_id"] for row in self.payload["ability_rules"]]
            + [row["rule_id"] for row in self.payload["explicit_rules"]]
            + [row["rule_id"] for row in self.payload["target_rules"]],
            "type_effectiveness_keys_unique": [
                (row["attacking_type"], row["defending_type"])
                for row in self.type_chart["entries"]
            ],
        }
        for validation_name, values in identifiers.items():
            if len(values) != len(set(values)):
                raise InputError(
                    "Checkpoint 1I {} 失敗".format(validation_name)
                )
        expected_validation = {
            "ability_rule_ids_unique": True,
            "data_schema_valid": True,
            "move_aliases_unique": True,
            "move_ids_unique": True,
            "rule_ids_unique": True,
            "source_ids_unique": True,
            "species_ids_unique": True,
            "type_effectiveness_keys_unique": True,
        }
        if self.manifest["validation"] != expected_validation:
            raise InputError("Checkpoint 1I rule knowledge validation manifest 不一致")
        if (
            self.payload["scope_guards"] != self.manifest["scope_guards"]
            or any(self.payload["scope_guards"].values())
        ):
            raise InputError("Checkpoint 1I rule knowledge scope guards 不一致")
        if self.manifest["data"]["path"] != self.data_path.name:
            raise InputError("Checkpoint 1I rule knowledge manifest data path 不一致")
        if len(self.knowledge_by_id) != sum(
            derived[key]
            for key in (
                "moves",
                "species_types",
                "ability_rules",
                "explicit_rules",
                "target_rules",
            )
        ) + 1:
            raise InputError("Checkpoint 1I knowledge_id 重複")
        for row in self.knowledge_by_id.values():
            for source_ref in row["source_refs"]:
                source_id = str(source_ref).split(":", 1)[0]
                if source_id not in self.sources:
                    raise InputError(
                        "Checkpoint 1I knowledge source_ref 無法解析：{}".format(
                            source_ref
                        )
                    )

    def resolve_move(self, observed_name: Optional[str]) -> Optional[Mapping[str, Any]]:
        if not observed_name:
            return None
        return self.move_aliases.get(normalize_rule_alias(observed_name))

    def resolve_ability_rule(
        self, observed_name: Optional[str]
    ) -> Optional[Mapping[str, Any]]:
        if not observed_name:
            return None
        return self.ability_aliases.get(normalize_rule_alias(observed_name))

    def ability_rule(self, ability_id: str) -> Mapping[str, Any]:
        return self.ability_rules_by_id[ability_id]

    def species_types(self, canonical_species_id: Optional[int]) -> Optional[Mapping[str, Any]]:
        if canonical_species_id is None:
            return None
        return self.species_types_by_id.get(int(canonical_species_id))

    def target_rule(self, move_id: str) -> Optional[Mapping[str, Any]]:
        return self.target_rules_by_move.get(move_id)

    def matching_explicit_rules(
        self, fact_type: str, metadata: Mapping[str, Any]
    ) -> List[Mapping[str, Any]]:
        return [
            rule
            for rule in self.explicit_rules
            if rule["fact_type"] == fact_type
            and all(metadata.get(key) == value for key, value in rule["required_metadata"].items())
        ]

    def type_multiplier(
        self, attacking_type: str, defending_types: Sequence[str]
    ) -> Tuple[float, List[Dict[str, Any]]]:
        missing = [
            defending_type
            for defending_type in defending_types
            if (attacking_type, defending_type) not in self.type_multipliers
        ]
        if missing:
            raise InputError(
                "Checkpoint 1I minimal type chart 未版本化 {} → {}".format(
                    attacking_type, missing
                )
            )
        multiplier = 1.0
        components = []
        for defending_type in defending_types:
            value = self.type_multipliers[(attacking_type, defending_type)]
            multiplier *= value
            components.append(
                {
                    "attacking_type": attacking_type,
                    "defending_type": defending_type,
                    "multiplier": value,
                }
            )
        return multiplier, components

    def supports_type_matchup(
        self, attacking_type: str, defending_types: Sequence[str]
    ) -> bool:
        return all(
            (attacking_type, defending_type) in self.type_multipliers
            for defending_type in defending_types
        )

    def knowledge(self, knowledge_id: str) -> Mapping[str, Any]:
        return self.knowledge_by_id[knowledge_id]

    @property
    def data_sha256(self) -> str:
        return str(self.manifest["data"]["sha256"])
