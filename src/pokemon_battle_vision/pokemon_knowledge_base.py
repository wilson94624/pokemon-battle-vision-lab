"""本機版本化 Pokémon Knowledge Base loader 與可追溯 exact alias resolver。"""

import hashlib
import json
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from jsonschema import Draft202012Validator

from .errors import InputError


DEFAULT_KNOWLEDGE_BASE_PATH = Path("knowledge/pokemon/v1/pokemon_knowledge_base.json")


def normalize_pokemon_alias(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(
        character
        for character in normalized
        if character.isalnum() or character in "♀♂"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class PokemonKnowledgeBase:
    """只做 exact normalized lookup；不引入 fuzzy、meta usage 或 learned embedding。"""

    def __init__(
        self,
        data_path: Path,
        schema_path: Optional[Path] = None,
        manifest_path: Optional[Path] = None,
        manifest_schema_path: Optional[Path] = None,
    ) -> None:
        self.data_path = data_path.resolve()
        self.manifest_path = (manifest_path or self.data_path.parent / "manifest.json").resolve()
        if not self.data_path.is_file():
            raise InputError("Pokémon Knowledge Base 不存在：{}".format(self.data_path))
        if not self.manifest_path.is_file():
            raise InputError("Pokémon Knowledge Base manifest 不存在：{}".format(self.manifest_path))
        self.payload = json.loads(self.data_path.read_text(encoding="utf-8"))
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if manifest_schema_path is not None:
            manifest_schema = json.loads(manifest_schema_path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(manifest_schema)
            Draft202012Validator(manifest_schema).validate(self.manifest)
        if _sha256(self.data_path) != self.manifest["data"]["sha256"]:
            raise InputError("Pokémon Knowledge Base data hash 與 manifest 不一致")
        if self.manifest["knowledge_base_version"] != self.payload["knowledge_base_version"]:
            raise InputError("Pokémon Knowledge Base data／manifest version 不一致")
        if self.manifest["counts"] != self.payload["counts"]:
            raise InputError("Pokémon Knowledge Base data／manifest counts 不一致")
        if schema_path is not None:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(self.payload)
        self.species_by_id: Dict[int, Mapping[str, Any]] = {
            int(row["canonical_species_id"]): row for row in self.payload["species"]
        }
        self.aliases: Dict[str, Mapping[str, Any]] = {
            str(row["normalized_alias"]): row for row in self.payload["aliases"]
        }
        self.sprites_by_pokemon_id: Dict[int, Mapping[str, Any]] = {
            int(row["pokemon_id"]): row for row in self.payload["sprite_metadata"]
        }

    @classmethod
    def from_project(cls, project_root: Path) -> "PokemonKnowledgeBase":
        root = project_root.resolve()
        return cls(
            root / DEFAULT_KNOWLEDGE_BASE_PATH,
            root / "schemas/pokemon_knowledge_base.schema.json",
            manifest_schema_path=root / "schemas/pokemon_knowledge_base_manifest.schema.json",
        )

    @staticmethod
    def _confidence(source_ids: List[str], ambiguous: bool) -> float:
        if "pokeapi.zh-hant" in source_ids:
            score = 1.0
        elif "pokeapi.en" in source_ids or "pokeapi.identifier" in source_ids:
            score = 0.98
        elif "pokemon-showdown.pokedex" in source_ids:
            score = 0.96
        else:
            score = 0.92
        return round(score - (0.08 if ambiguous else 0.0), 6)

    def resolve_species(self, text: Optional[str], limit: int = 5) -> List[Dict[str, Any]]:
        if not text or limit <= 0:
            return []
        normalized = normalize_pokemon_alias(text)
        alias = self.aliases.get(normalized)
        if alias is None:
            return []
        source_ids = list(alias["source_ids"])
        species_ids = list(alias["canonical_species_ids"])
        ambiguous = len(species_ids) > 1
        candidates = []
        for species_id in species_ids:
            species = self.species_by_id[int(species_id)]
            candidates.append(
                {
                    "canonical_species_id": int(species_id),
                    "canonical_identifier": species["canonical_identifier"],
                    "traditional_chinese_name": species["traditional_chinese_name"],
                    "english_name": species["english_name"],
                    "default_pokemon_id": species["default_pokemon_id"],
                    "regulation_availability": species["regulation_availability"],
                    "confidence": self._confidence(source_ids, ambiguous),
                    "matched_normalized_alias": normalized,
                    "source_ids": source_ids,
                    "resolution_rule_id": "pokemon_kb.exact_normalized_alias.v1",
                }
            )
        return sorted(
            candidates,
            key=lambda row: (-float(row["confidence"]), int(row["canonical_species_id"])),
        )[:limit]

    def sprite_metadata(self, pokemon_id: int) -> Optional[Mapping[str, Any]]:
        return self.sprites_by_pokemon_id.get(int(pokemon_id))

    @property
    def data_sha256(self) -> str:
        return str(self.manifest["data"]["sha256"])
