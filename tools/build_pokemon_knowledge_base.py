#!/usr/bin/env python3
"""由鎖定的成熟來源建立本機、可追溯 Pokémon Knowledge Base。"""

import argparse
import csv
import hashlib
import html
import json
import os
import re
import shutil
import stat
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
from uuid import uuid4

from jsonschema import Draft202012Validator


KB_VERSION = "2026.07.17"
POKEAPI_REVISION = "e21557dd4cd0fefe7cb1f946bf9080e38a2e3ba4"
SHOWDOWN_REVISION = "f0327afadabd7688829b1d3046872017a7bdc1c3"
SPRITES_REVISION = "bf4c47ac82c33b330e33d98b8882d1cedb2f53e7"
REGULATION_ID = "pokemon-champions-m-b"

SOURCE_FILES = {
    "pokemon_species.csv": "9878f19c0637095cdd9a4134b4aac8fb2b64776d3bdc599aa68f15c3a011b87c",
    "pokemon_species_names.csv": "5d7b06270a31bdc51424be6032fc04a3a3c0ae7ac40af4aea69c1a2954ca1e7e",
    "pokemon.csv": "16c81c33188b0eac403aa2f759fcbe9e42c611f722d263f5b5a6a5bff9f8ce6b",
    "pokemon_forms.csv": "404f77a5033ed01df2b2cfb229f465bd086b7bbaa6b813834c228e4fbc578694",
    "pokemon_form_names.csv": "f496066d02fab12c18d10cce0af2f748d09cb7e296ecc784cc8682f8c4da8625",
    "pokeapi_languages.csv": "fbb60019a6a461783d5671a995d5f590db61792a273e90faa0ed630d102a19b8",
    "showdown_pokedex.ts": "0aba8712ababae8e356bdd9d6c6ffa73a5169d39a3ac831a853ae88fe653e6bc",
    "showdown_aliases.ts": "74fb99dad6085eb0d6e4ac42e58c1d6d6427a46adc40c7d87579cc4bf9851707",
    "sprites_default_tree.json": "4304d593ebe32c346d89d3692602cebc9216a0f964d3cb2b81153939c5aee6b9",
    "sprites_home_tree.json": "1ed7dc3378e5dde52382a7f05645b3a10d5b90c943e8cf765e6c643e1ba0fb3c",
    "sprites_official_artwork_tree.json": "5bc080be8fd6ccc1ae209f941204a22b0815d70d87fa96cafb48aac9c053d464",
    "sprites_showdown_tree.json": "7307fdb277308856b69e75dab5ef0d55596a815c116c38678d372ad276fb4d6e",
    "sprites_gen8_icons_tree.json": "e50ccffb16cbb9834fc8d105007b2725fe48e711bae7ffc93c2fca595a7b5159",
    "champions_regulation_mb.html": "8b0c6db8dcd403bb1f5453c1c6f9ac35c80192762219a308c80023375a93d617",
    "champions_regulation_ma_news.html": "9ade165187c7fa492786ba6d2ed4c720c617c0445680b3d7fe61b2d0e2448e3d",
    "champions_regulation_mb_news.html": "62e2971931025ae8cf9cfff9d07835363e69a4dd0ea676bb495695b53cf04084",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def normalize_alias(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum() or character in "♀♂")


def boolean(value: str) -> bool:
    return value == "1"


def optional_int(value: str) -> Optional[int]:
    return int(value) if value else None


def verify_sources(source_dir: Path) -> Dict[str, str]:
    hashes = {}
    for filename, expected in SOURCE_FILES.items():
        path = source_dir / filename
        if not path.is_file():
            raise RuntimeError("缺少 Knowledge Base source：{}".format(path))
        actual = sha256_file(path)
        if actual != expected:
            raise RuntimeError("Knowledge Base source hash 不符：{} {} != {}".format(filename, actual, expected))
        hashes[filename] = actual
    return hashes


def localized_names(rows: Sequence[Mapping[str, str]], id_field: str) -> Dict[int, Dict[str, str]]:
    result: DefaultDict[int, Dict[str, str]] = defaultdict(dict)
    for row in rows:
        language_id = str(row["local_language_id"])
        if language_id not in {"4", "9"}:
            continue
        name = str(row.get("name") or row.get("pokemon_name") or row.get("form_name") or "").strip()
        if name:
            result[int(row[id_field])]["zh-Hant" if language_id == "4" else "en"] = name
    return dict(result)


SHOWDOWN_ENTRY = re.compile(r'^\t(?:"((?:[^"\\]|\\.)+)"|([A-Za-z0-9]+)):\s*\{$')
SHOWDOWN_NUM = re.compile(r"^\t\tnum:\s*(-?[0-9]+),")
SHOWDOWN_NAME = re.compile(r'^\t\tname:\s*"((?:[^"\\]|\\.)+)",')
SHOWDOWN_ALIAS = re.compile(r'^\t(?:"((?:[^"\\]|\\.)+)"|([A-Za-z0-9]+)):\s*"((?:[^"\\]|\\.)+)",')


def decode_js_string(value: str) -> str:
    return json.loads('"{}"'.format(value))


def parse_showdown_pokedex(path: Path) -> Dict[str, Dict[str, Any]]:
    entries = {}
    current: Optional[Dict[str, Any]] = None
    for line in path.read_text(encoding="utf-8").splitlines():
        start = SHOWDOWN_ENTRY.match(line)
        if start:
            identifier = decode_js_string(start.group(1)) if start.group(1) else start.group(2)
            current = {"showdown_id": identifier, "name": None, "species_id": None}
            continue
        if current is None:
            continue
        number = SHOWDOWN_NUM.match(line)
        if number:
            current["species_id"] = int(number.group(1))
            continue
        name = SHOWDOWN_NAME.match(line)
        if name:
            current["name"] = decode_js_string(name.group(1))
            continue
        if line == "\t},":
            species_id = current.get("species_id")
            if species_id and 1 <= int(species_id) <= 1025 and current.get("name"):
                entries[normalize_alias(str(current["showdown_id"]))] = dict(current)
                entries[normalize_alias(str(current["name"]))] = dict(current)
            current = None
    return entries


def parse_showdown_aliases(path: Path, targets: Mapping[str, Mapping[str, Any]]) -> List[Tuple[str, int]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = SHOWDOWN_ALIAS.match(line)
        if not match:
            continue
        alias = decode_js_string(match.group(1)) if match.group(1) else str(match.group(2))
        target_name = decode_js_string(match.group(3))
        target = targets.get(normalize_alias(target_name))
        if target:
            rows.append((alias, int(target["species_id"])))
    return rows


def parse_regulation_roster(path: Path) -> List[Dict[str, Any]]:
    source = path.read_text(encoding="utf-8")
    match = re.search(r"const pokemons = (\[.*?\]);const noPrefix", source, re.DOTALL)
    if not match:
        raise RuntimeError("無法解析 Pokémon Champions M-B eligible roster")
    payload = json.loads(match.group(1))
    rows = []
    for code, enabled, name in payload:
        if int(enabled) != 1:
            continue
        species_text, form_code = str(code).split("-", 1)
        rows.append(
            {
                "regulation_id": REGULATION_ID,
                "champions_code": str(code),
                "canonical_species_id": int(species_text),
                "champions_form_code": form_code,
                "official_english_name": str(name),
            }
        )
    return rows


def parse_mega_names(path: Path) -> List[str]:
    source = path.read_text(encoding="utf-8")
    match = re.search(r"<h4>(.*?)</h4>", source, re.DOTALL | re.IGNORECASE)
    if not match:
        raise RuntimeError("無法解析 Pokémon Champions Mega list：{}".format(path))
    value = re.sub(r"<br\s*/?>", "\n", match.group(1), flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    return [
        line.strip().replace("\u200b", "")
        for line in html.unescape(value).splitlines()
        if line.strip().replace("\u200b", "")
    ]


FORM_KEYWORDS = {
    "alolan": "alola",
    "galarian": "galar",
    "hisuian": "hisui",
    "paldean": "paldea",
    "medium": "average",
    "combat": "combat",
    "blaze": "blaze",
    "aqua": "aqua",
    "heat": "heat",
    "wash": "wash",
    "frost": "frost",
    "fan": "fan",
    "mow": "mow",
    "male": "male",
    "female": "female",
    "midday": "midday",
    "midnight": "midnight",
    "dusk": "dusk",
    "small": "small",
    "large": "large",
    "jumbo": "super",
}


def match_regulation_pokemon(
    row: Mapping[str, Any],
    pokemon_by_species: Mapping[int, Sequence[Mapping[str, str]]],
) -> Tuple[Optional[int], str]:
    candidates = list(pokemon_by_species.get(int(row["canonical_species_id"]), []))
    if row["champions_form_code"] == "000":
        default = [candidate for candidate in candidates if boolean(str(candidate["is_default"]))]
        return (int(default[0]["id"]), "default_form_exact") if len(default) == 1 else (None, "species_only")
    words = set(re.findall(r"[A-Za-z]+", str(row["official_english_name"]).lower()))
    keywords = {mapped for source, mapped in FORM_KEYWORDS.items() if source in words}
    if not keywords:
        return None, "species_only"
    matched = [
        candidate
        for candidate in candidates
        if keywords.issubset(set(str(candidate["identifier"]).split("-")))
    ]
    if len(matched) == 1:
        return int(matched[0]["id"]), "form_identifier_keywords_exact"
    return None, "species_only"


def tree_files(path: Path) -> Set[str]:
    payload = load_json(path)
    return {str(row["path"]) for row in payload["tree"] if row.get("type") == "blob"}


def asset_domain(
    pokemon_id: int,
    files: Set[str],
    prefix: str,
    extension: str,
    visual_domain: str,
) -> Dict[str, Any]:
    filename = "{}.{}".format(pokemon_id, extension)
    available = filename in files
    relative = "{}{}".format(prefix, filename)
    return {
        "visual_domain": visual_domain,
        "available": available,
        "repository_path": relative if available else None,
        "pinned_raw_url": (
            "https://raw.githubusercontent.com/PokeAPI/sprites/{}/{}".format(SPRITES_REVISION, relative)
            if available
            else None
        ),
    }


def build_payload(source_dir: Path, source_hashes: Mapping[str, str]) -> Dict[str, Any]:
    species_rows = load_csv(source_dir / "pokemon_species.csv")
    species_names = localized_names(load_csv(source_dir / "pokemon_species_names.csv"), "pokemon_species_id")
    pokemon_rows = load_csv(source_dir / "pokemon.csv")
    form_rows = load_csv(source_dir / "pokemon_forms.csv")
    form_names = localized_names(load_csv(source_dir / "pokemon_form_names.csv"), "pokemon_form_id")
    pokemon_by_id = {int(row["id"]): row for row in pokemon_rows}
    pokemon_by_species: DefaultDict[int, List[Mapping[str, str]]] = defaultdict(list)
    for row in pokemon_rows:
        pokemon_by_species[int(row["species_id"])].append(row)

    regulation_entries = parse_regulation_roster(source_dir / "champions_regulation_mb.html")
    for row in regulation_entries:
        pokemon_id, mapping_status = match_regulation_pokemon(row, pokemon_by_species)
        row["pokeapi_pokemon_id"] = pokemon_id
        row["mapping_status"] = mapping_status
        row["availability"] = "eligible"
        row["source_id"] = "pokemon-champions.regulation-m-b"
    codes_by_species: DefaultDict[int, List[str]] = defaultdict(list)
    for row in regulation_entries:
        codes_by_species[int(row["canonical_species_id"])].append(str(row["champions_code"]))

    species = []
    default_pokemon_by_species = {}
    for row in species_rows:
        species_id = int(row["id"])
        names = species_names.get(species_id, {})
        defaults = [candidate for candidate in pokemon_by_species[species_id] if boolean(candidate["is_default"])]
        default_pokemon_id = int(defaults[0]["id"]) if len(defaults) == 1 else None
        default_pokemon_by_species[species_id] = default_pokemon_id
        codes = sorted(codes_by_species.get(species_id, []))
        species.append(
            {
                "canonical_species_id": species_id,
                "canonical_identifier": row["identifier"],
                "generation_id": int(row["generation_id"]),
                "english_name": names.get("en"),
                "traditional_chinese_name": names.get("zh-Hant"),
                "default_pokemon_id": default_pokemon_id,
                "regulation_availability": {
                    "regulation_id": REGULATION_ID,
                    "status": "eligible" if codes else "not_eligible",
                    "eligible_champions_codes": codes,
                    "basis": "official_exhaustive_eligible_roster",
                },
            }
        )

    mapped_regulation_ids = {
        int(row["pokeapi_pokemon_id"])
        for row in regulation_entries
        if row["pokeapi_pokemon_id"] is not None
    }
    forms = []
    for row in form_rows:
        form_id = int(row["id"])
        pokemon_id = int(row["pokemon_id"])
        pokemon = pokemon_by_id[pokemon_id]
        species_id = int(pokemon["species_id"])
        names = form_names.get(form_id, {})
        if pokemon_id in mapped_regulation_ids:
            regulation_status = "eligible"
        elif not codes_by_species.get(species_id):
            regulation_status = "not_eligible"
        else:
            regulation_status = "unknown_form_crosswalk"
        forms.append(
            {
                "form_key": "pokeapi-form-{}".format(form_id),
                "source_id": "pokeapi.forms",
                "canonical_species_id": species_id,
                "pokemon_id": pokemon_id,
                "form_id": form_id,
                "pokemon_identifier": pokemon["identifier"],
                "form_identifier": row["form_identifier"] or None,
                "english_form_name": names.get("en"),
                "traditional_chinese_form_name": names.get("zh-Hant"),
                "is_default": boolean(row["is_default"]),
                "is_battle_only": boolean(row["is_battle_only"]),
                "is_mega": boolean(row["is_mega"]),
                "form_order": int(row["form_order"]),
                "regulation_availability": {
                    "regulation_id": REGULATION_ID,
                    "status": regulation_status,
                },
            }
        )

    english_to_species = {
        normalize_alias(str(row["english_name"])): int(row["canonical_species_id"])
        for row in species
        if row["english_name"]
    }
    old_megas = parse_mega_names(source_dir / "champions_regulation_ma_news.html")
    new_megas = parse_mega_names(source_dir / "champions_regulation_mb_news.html")
    for official_name in [*old_megas, *new_megas]:
        remainder = official_name.removeprefix("Mega ")
        matches = sorted(
            (
                (len(name), species_id, name)
                for name, species_id in english_to_species.items()
                if normalize_alias(remainder).startswith(name)
            ),
            reverse=True,
        )
        if not matches:
            raise RuntimeError("Mega form 無法對應 canonical species：{}".format(official_name))
        _, species_id, _ = matches[0]
        source_id = (
            "pokemon-champions.regulation-m-b-new-megas"
            if official_name in new_megas
            else "pokemon-champions.regulation-m-a-carried-forward"
        )
        forms.append(
            {
                "form_key": "champions-mega-{}".format(normalize_alias(official_name)),
                "source_id": source_id,
                "canonical_species_id": species_id,
                "pokemon_id": None,
                "form_id": None,
                "pokemon_identifier": None,
                "form_identifier": normalize_alias(official_name),
                "english_form_name": official_name,
                "traditional_chinese_form_name": None,
                "is_default": False,
                "is_battle_only": True,
                "is_mega": True,
                "form_order": 0,
                "regulation_availability": {
                    "regulation_id": REGULATION_ID,
                    "status": "eligible",
                    "basis": (
                        "official_m_b_newly_allowed"
                        if official_name in new_megas
                        else "carried_forward_from_official_m_a_under_m_b_newly_allowed_wording"
                    ),
                },
            }
        )

    alias_index: DefaultDict[str, Dict[str, Any]] = defaultdict(
        lambda: {"aliases": set(), "canonical_species_ids": set(), "source_ids": set()}
    )

    def add_alias(value: Optional[str], species_id: int, source_id: str) -> None:
        if not value:
            return
        key = normalize_alias(value)
        if not key:
            return
        alias_index[key]["aliases"].add(value)
        alias_index[key]["canonical_species_ids"].add(species_id)
        alias_index[key]["source_ids"].add(source_id)

    for row in species:
        species_id = int(row["canonical_species_id"])
        add_alias(row["canonical_identifier"], species_id, "pokeapi.identifier")
        add_alias(row["english_name"], species_id, "pokeapi.en")
        add_alias(row["traditional_chinese_name"], species_id, "pokeapi.zh-hant")
    for row in forms:
        species_id = int(row["canonical_species_id"])
        add_alias(row.get("pokemon_identifier"), species_id, str(row["source_id"]))
        add_alias(row.get("english_form_name"), species_id, str(row["source_id"]))
        add_alias(row.get("traditional_chinese_form_name"), species_id, str(row["source_id"]))
    showdown_targets = parse_showdown_pokedex(source_dir / "showdown_pokedex.ts")
    for target in showdown_targets.values():
        add_alias(str(target["showdown_id"]), int(target["species_id"]), "pokemon-showdown.pokedex")
        add_alias(str(target["name"]), int(target["species_id"]), "pokemon-showdown.pokedex")
    for value, species_id in parse_showdown_aliases(source_dir / "showdown_aliases.ts", showdown_targets):
        add_alias(value, species_id, "pokemon-showdown.aliases")
    aliases = [
        {
            "normalized_alias": key,
            "aliases": sorted(value["aliases"], key=lambda item: (normalize_alias(item), item)),
            "canonical_species_ids": sorted(value["canonical_species_ids"]),
            "source_ids": sorted(value["source_ids"]),
        }
        for key, value in sorted(alias_index.items())
    ]

    default_files = tree_files(source_dir / "sprites_default_tree.json")
    home_files = tree_files(source_dir / "sprites_home_tree.json")
    artwork_files = tree_files(source_dir / "sprites_official_artwork_tree.json")
    showdown_files = tree_files(source_dir / "sprites_showdown_tree.json")
    icon_files = tree_files(source_dir / "sprites_gen8_icons_tree.json")
    sprite_metadata = []
    for row in sorted(pokemon_rows, key=lambda item: int(item["id"])):
        pokemon_id = int(row["id"])
        sprite_metadata.append(
            {
                "pokemon_id": pokemon_id,
                "canonical_species_id": int(row["species_id"]),
                "pokemon_identifier": row["identifier"],
                "source_revision": SPRITES_REVISION,
                "domains": {
                    "battle_sprite_default": asset_domain(
                        pokemon_id, default_files, "sprites/pokemon/", "png", "battle_sprite"
                    ),
                    "home_artwork": asset_domain(
                        pokemon_id, home_files, "sprites/pokemon/other/home/", "png", "home_artwork"
                    ),
                    "official_artwork": asset_domain(
                        pokemon_id,
                        artwork_files,
                        "sprites/pokemon/other/official-artwork/",
                        "png",
                        "official_artwork",
                    ),
                    "showdown_animation": asset_domain(
                        pokemon_id,
                        showdown_files,
                        "sprites/pokemon/other/showdown/",
                        "gif",
                        "showdown_animation",
                    ),
                    "generation_viii_icon": asset_domain(
                        pokemon_id,
                        icon_files,
                        "sprites/pokemon/versions/generation-viii/icons/",
                        "png",
                        "team_preview_icon_reference",
                    ),
                    "pokemon_champions_icon": {
                        "visual_domain": "pokemon_champions_team_preview_icon",
                        "available": False,
                        "repository_path": None,
                        "pinned_raw_url": None,
                        "reason": "未採用可再散布且逐 form 對應的官方 Champions icon asset",
                    },
                },
            }
        )

    sources = [
        {
            "source_id": "pokeapi",
            "status": "adopted",
            "url": "https://github.com/PokeAPI/pokeapi",
            "revision": POKEAPI_REVISION,
            "license": "BSD-3-Clause",
            "usage": ["canonical_species_ids", "forms", "traditional_chinese_names"],
            "input_sha256": {name: source_hashes[name] for name in SOURCE_FILES if name.startswith("pokemon_") or name == "pokeapi_languages.csv"},
        },
        {
            "source_id": "pokemon-showdown",
            "status": "adopted",
            "url": "https://github.com/smogon/pokemon-showdown",
            "revision": SHOWDOWN_REVISION,
            "license": "MIT",
            "usage": ["canonical_aliases", "form_aliases"],
            "input_sha256": {
                "showdown_pokedex.ts": source_hashes["showdown_pokedex.ts"],
                "showdown_aliases.ts": source_hashes["showdown_aliases.ts"],
            },
        },
        {
            "source_id": "pokeapi-sprites",
            "status": "metadata_only",
            "url": "https://github.com/PokeAPI/sprites",
            "revision": SPRITES_REVISION,
            "license": "not_declared_at_pinned_commit",
            "usage": ["sprite_path_metadata", "icon_path_metadata"],
            "input_sha256": {name: source_hashes[name] for name in SOURCE_FILES if name.startswith("sprites_")},
        },
        {
            "source_id": "pokemon-champions",
            "status": "adopted_factual_metadata_only",
            "url": "https://champions-news.pokemon-home.com/en/page/776.html",
            "revision": "regulation-m-b-2026-06-17",
            "license": "official_site_all_rights_reserved",
            "usage": ["regulation_availability", "mega_form_availability"],
            "input_sha256": {name: source_hashes[name] for name in SOURCE_FILES if name.startswith("champions_")},
        },
    ]
    payload = {
        "schema_version": "0.1.0",
        "knowledge_base_version": KB_VERSION,
        "kind": "pokemon_knowledge_base",
        "language": "zh-Hant",
        "current_regulation": {
            "regulation_id": REGULATION_ID,
            "display_name": "Pokémon Champions Regulation Set M-B",
            "effective_from": "2026-06-17T02:00:00Z",
            "effective_until": "2026-09-02T01:59:00Z",
            "official_url": "https://champions-news.pokemon-home.com/en/page/776.html",
            "roster_url": "https://web-view.app.pokemonchampions.jp/battle/pages/events/rs178066986988lmoqpm/en/pokemon.html",
            "availability_semantics": "官方頁面聲明只有 eligible roster 列出項目可參加；未列入 species 標為 not_eligible。",
        },
        "sources": sources,
        "counts": {
            "species": len(species),
            "forms": len(forms),
            "pokeapi_forms": len(form_rows),
            "champions_mega_forms": len(old_megas) + len(new_megas),
            "aliases": len(aliases),
            "regulation_entries": len(regulation_entries),
            "sprite_metadata": len(sprite_metadata),
        },
        "species": species,
        "forms": sorted(forms, key=lambda row: str(row["form_key"])),
        "aliases": aliases,
        "regulation_entries": sorted(regulation_entries, key=lambda row: str(row["champions_code"])),
        "sprite_metadata": sprite_metadata,
    }
    return payload


def validate_payload(payload: Mapping[str, Any], schema_path: Path) -> Dict[str, bool]:
    schema = load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)
    species = list(payload["species"])
    forms = list(payload["forms"])
    aliases = list(payload["aliases"])
    regulation = list(payload["regulation_entries"])
    sprite_metadata = list(payload["sprite_metadata"])
    species_ids = [int(row["canonical_species_id"]) for row in species]
    form_keys = [str(row["form_key"]) for row in forms]
    normalized_aliases = [str(row["normalized_alias"]) for row in aliases]
    regulation_codes = [str(row["champions_code"]) for row in regulation]
    sprite_ids = [int(row["pokemon_id"]) for row in sprite_metadata]
    alias_map = {str(row["normalized_alias"]): row for row in aliases}
    checks = {
        "schema_valid": True,
        "species_ids_unique": len(species_ids) == len(set(species_ids)) == 1025,
        "species_ids_complete": species_ids == list(range(1, 1026)),
        "traditional_chinese_names_complete": all(row["traditional_chinese_name"] for row in species),
        "form_keys_unique": len(form_keys) == len(set(form_keys)),
        "pokeapi_form_count_complete": payload["counts"]["pokeapi_forms"] == 1579,
        "aliases_unique": len(normalized_aliases) == len(set(normalized_aliases)),
        "regulation_codes_unique": len(regulation_codes) == len(set(regulation_codes)),
        "regulation_species_traceable": all(int(row["canonical_species_id"]) in set(species_ids) for row in regulation),
        "sprite_ids_unique": len(sprite_ids) == len(set(sprite_ids)) == 1351,
        "known_zh_hant_aliases_present": all(
            expected in alias_map and species_id in alias_map[expected]["canonical_species_ids"]
            for expected, species_id in {
                "勾魂眼": 302,
                "蚊香蛙皇": 186,
                "烈咬陸鯊": 445,
                "賽富豪": 1000,
            }.items()
        ),
    }
    if not all(checks.values()):
        raise RuntimeError("Knowledge Base validation failed：{}".format(checks))
    return checks


def replace_directory(target: Path, writer) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    staging = target.parent / "{}.tmp-{}".format(target.name, token)
    backup = target.parent / "{}.backup-{}".format(target.name, token)
    conflict = target.with_name(target.name + " 2")

    def visible_tree(path: Path) -> None:
        items = [path, *path.rglob("*")]
        for item in items:
            if item.name == ".DS_Store" and item.is_file():
                item.unlink()
        hidden_flag = getattr(stat, "UF_HIDDEN", 0)
        if hidden_flag and hasattr(os, "chflags"):
            for item in [path, *path.rglob("*")]:
                flags = int(item.lstat().st_flags)
                if flags & hidden_flag:
                    os.chflags(str(item), flags & ~hidden_flag, follow_symlinks=False)
        hidden = [
            str(item)
            for item in [path, *path.rglob("*")]
            if hidden_flag and int(item.lstat().st_flags) & hidden_flag
        ]
        if hidden:
            raise RuntimeError("Knowledge Base output 含 BSD hidden flag：{}".format(hidden[:5]))

    try:
        staging.mkdir()
        writer(staging)
        visible_tree(staging)
        if conflict.exists():
            if not conflict.is_dir() or any(conflict.iterdir()):
                raise RuntimeError("拒絕覆蓋非空白 Knowledge Base 衝突目錄：{}".format(conflict))
            conflict.rmdir()
        if target.exists():
            os.replace(str(target), str(backup))
        os.replace(str(staging), str(target))
        visible_tree(target)
        if backup.exists():
            shutil.rmtree(str(backup))
    except Exception:
        if target.exists() and backup.exists():
            shutil.rmtree(str(target))
        if backup.exists() and not target.exists():
            os.replace(str(backup), str(target))
        raise
    finally:
        if staging.exists():
            shutil.rmtree(str(staging))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("knowledge/pokemon/v1"))
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    source_dir = args.source_dir.resolve()
    output_dir = args.output_dir if args.output_dir.is_absolute() else project_root / args.output_dir
    source_hashes = verify_sources(source_dir)
    payload = build_payload(source_dir, source_hashes)
    checks = validate_payload(payload, project_root / "schemas/pokemon_knowledge_base.schema.json")

    def write(staging: Path) -> None:
        data_path = staging / "pokemon_knowledge_base.json"
        write_json(data_path, payload)
        manifest = {
            "schema_version": "0.1.0",
            "knowledge_base_version": KB_VERSION,
            "kind": "pokemon_knowledge_base_manifest",
            "status": "complete",
            "data": {
                "path": "pokemon_knowledge_base.json",
                "sha256": sha256_file(data_path),
                "schema": "schemas/pokemon_knowledge_base.schema.json",
            },
            "counts": payload["counts"],
            "sources": payload["sources"],
            "validation": checks,
            "scope_guards": {
                "image_assets_vendored": False,
                "embeddings_created": False,
                "vector_database_created": False,
                "model_trained": False,
                "meta_usage_used_as_visual_evidence": False,
            },
        }
        manifest_schema = load_json(project_root / "schemas/pokemon_knowledge_base_manifest.schema.json")
        Draft202012Validator.check_schema(manifest_schema)
        Draft202012Validator(manifest_schema).validate(manifest)
        write_json(staging / "manifest.json", manifest)

    replace_directory(output_dir, write)
    print(json.dumps({"output": str(output_dir), "counts": payload["counts"], "validation": checks}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
