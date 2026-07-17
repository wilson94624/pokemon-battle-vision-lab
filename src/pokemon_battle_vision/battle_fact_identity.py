"""Battle Text 已觀察名稱的保守 canonical identity resolution。"""

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .battle_fact_models import FactParticipant
from .pokemon_knowledge_base import PokemonKnowledgeBase, normalize_pokemon_alias


_SUBJECT_LIKE_TYPES = {"MOVE", "ABILITY", "ITEM", "SWITCH", "TRANSFORMATION"}


class FactParticipantResolver:
    """KB 只正規化名稱；沒有 observation 時本類別不會自行建立 participant。"""

    def __init__(
        self,
        knowledge_base: PokemonKnowledgeBase,
        entities: Sequence[Mapping[str, Any]],
    ) -> None:
        self.knowledge_base = knowledge_base
        self.entities = [dict(item) for item in entities]
        self.entity_by_id = {
            str(item["entity_id"]): item for item in self.entities
        }

    @staticmethod
    def _entity_side(entity: Mapping[str, Any]) -> str:
        return str(entity.get("side", {}).get("value") or "unknown")

    @staticmethod
    def _entity_names(entity: Mapping[str, Any]) -> List[str]:
        species = entity.get("species", {})
        return [
            str(value)
            for value in (species.get("value"), species.get("raw_text"))
            if value
        ]

    def _matching_entities(
        self,
        observed_name: str,
        side: str,
        canonical_species_id: Optional[int],
    ) -> List[Mapping[str, Any]]:
        normalized = normalize_pokemon_alias(observed_name)
        matches = []
        for entity in self.entities:
            entity_side = self._entity_side(entity)
            if side != "unknown" and entity_side not in {side, "unknown"}:
                continue
            species = entity.get("species", {})
            entity_species_id = species.get("canonical_species_id")
            id_matches = (
                canonical_species_id is not None
                and entity_species_id is not None
                and int(entity_species_id) == canonical_species_id
            )
            name_matches = any(
                normalize_pokemon_alias(value) == normalized
                for value in self._entity_names(entity)
            )
            if id_matches or name_matches:
                matches.append(entity)
        return sorted(matches, key=lambda item: str(item["entity_id"]))

    def resolve(
        self,
        observed_name: str,
        role: str,
        side: str = "unknown",
        confidence: float = 0.0,
        participant_kind: str = "pokemon",
        entity_id_hint: Optional[str] = None,
    ) -> FactParticipant:
        if participant_kind != "pokemon":
            return FactParticipant(
                role=role,
                participant_kind=participant_kind,
                observed_name=observed_name,
                side=side,
                entity_id=None,
                canonical_species_id=None,
                canonical_name=None,
                resolution_status="observed_only",
                confidence=round(float(confidence), 6),
            )

        hinted = self.entity_by_id.get(str(entity_id_hint)) if entity_id_hint else None
        # Knowledge Base 原始 record 另含規則可用性；1H 僅保留 identity provenance。
        species_candidates = [
            {
                key: row[key]
                for key in (
                    "canonical_species_id",
                    "canonical_identifier",
                    "traditional_chinese_name",
                    "english_name",
                    "confidence",
                    "matched_normalized_alias",
                    "source_ids",
                    "resolution_rule_id",
                )
            }
            for row in self.knowledge_base.resolve_species(observed_name, limit=5)
        ]
        canonical = species_candidates[0] if len(species_candidates) == 1 else None
        canonical_species_id = (
            int(canonical["canonical_species_id"]) if canonical else None
        )
        canonical_name = (
            str(canonical["traditional_chinese_name"]) if canonical else None
        )

        if hinted is not None:
            entity_species = hinted.get("species", {})
            hinted_species_id = entity_species.get("canonical_species_id")
            if canonical_species_id is None and hinted_species_id is not None:
                canonical_species_id = int(hinted_species_id)
                canonical_name = str(entity_species.get("value") or observed_name)
            resolved_side = self._entity_side(hinted)
            return FactParticipant(
                role=role,
                participant_kind="pokemon",
                observed_name=observed_name,
                side=resolved_side if side == "unknown" else side,
                entity_id=str(hinted["entity_id"]),
                canonical_species_id=canonical_species_id,
                canonical_name=canonical_name,
                resolution_status="entity_hint_observed",
                confidence=round(float(confidence), 6),
                entity_candidate_ids=(str(hinted["entity_id"]),),
                species_candidates=tuple(species_candidates),
                resolution_source_ids=tuple(
                    sorted({source for row in species_candidates for source in row["source_ids"]})
                ),
            )

        entity_matches = self._matching_entities(
            observed_name, side, canonical_species_id
        )
        entity_ids = tuple(str(item["entity_id"]) for item in entity_matches)
        if len(entity_matches) == 1:
            entity = entity_matches[0]
            entity_species = entity.get("species", {})
            if canonical_species_id is None and entity_species.get("canonical_species_id") is not None:
                canonical_species_id = int(entity_species["canonical_species_id"])
                canonical_name = str(entity_species.get("value") or observed_name)
            resolution_status = "canonical_entity_resolved"
            entity_id = str(entity["entity_id"])
            resolved_side = self._entity_side(entity)
        elif len(entity_matches) > 1:
            resolution_status = "ambiguous_entity_candidates"
            entity_id = None
            resolved_side = side
        elif canonical is not None:
            resolution_status = "canonical_species_resolved"
            entity_id = None
            resolved_side = side
        else:
            resolution_status = "unresolved_observed_name"
            entity_id = None
            resolved_side = side

        return FactParticipant(
            role=role,
            participant_kind="pokemon",
            observed_name=observed_name,
            side=resolved_side,
            entity_id=entity_id,
            canonical_species_id=canonical_species_id,
            canonical_name=canonical_name,
            resolution_status=resolution_status,
            confidence=round(float(confidence), 6),
            entity_candidate_ids=entity_ids,
            species_candidates=tuple(species_candidates),
            resolution_source_ids=tuple(
                sorted({source for row in species_candidates for source in row["source_ids"]})
            ),
        )

    def from_event(self, event: Mapping[str, Any]) -> Tuple[FactParticipant, ...]:
        metadata = event.get("metadata", {})
        observed_side = str(metadata.get("side") or "unknown")
        event_type = str(event.get("event_type"))
        confidence = float(event.get("confidence", 0.0))
        participants: List[FactParticipant] = []
        seen = set()

        def participant_side(role: str) -> str:
            # parser 的 side 隨事件語法描述 actor 或 target；只套用到對應角色。
            if role == "actor" and event_type in _SUBJECT_LIKE_TYPES:
                return observed_side
            if role == "target" and event_type not in _SUBJECT_LIKE_TYPES:
                return observed_side
            return "unknown"

        def add(role: str, name: Any, kind: str = "pokemon") -> None:
            if not name:
                return
            key = (role, kind, normalize_pokemon_alias(str(name)))
            if key in seen:
                return
            seen.add(key)
            participants.append(
                self.resolve(
                    str(name),
                    role,
                    participant_side(role),
                    confidence,
                    kind,
                )
            )

        add("actor", metadata.get("actor"))
        add("target", metadata.get("target"))
        target_role = (
            "actor" if event_type in _SUBJECT_LIKE_TYPES else "target"
        )
        for name in metadata.get("targets", []):
            # 單一 subject parser 可能同時填 actor 與 targets；避免重複 participant。
            if metadata.get("actor") == name or metadata.get("target") == name:
                continue
            add(target_role, name)
        add("trainer", metadata.get("trainer"), "trainer")
        add("winner", metadata.get("winner"), "trainer")
        add("loser", metadata.get("loser"), "trainer")
        return tuple(participants)

    def from_hp_change(self, change: Mapping[str, Any]) -> Tuple[FactParticipant, ...]:
        observed_name = str(change.get("identity_text") or "")
        entity_id = change.get("pokemon_entity_id")
        if not observed_name and entity_id:
            entity = self.entity_by_id.get(str(entity_id), {})
            names = self._entity_names(entity)
            observed_name = names[0] if names else str(entity_id)
        if not observed_name:
            return ()
        return (
            self.resolve(
                observed_name,
                role="subject",
                side=str(change.get("side") or "unknown"),
                confidence=float(change.get("confidence", 0.0)),
                entity_id_hint=str(entity_id) if entity_id else None,
            ),
        )
