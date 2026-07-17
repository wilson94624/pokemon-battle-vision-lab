# Pokémon Knowledge Base

`knowledge/pokemon/v1/` 是 Checkpoint 1G 使用的本機、版本化 Pokémon Knowledge Base。它只保存可追溯的結構化資料與遠端 asset metadata，不包含 sprite、icon 或 artwork 二進位檔。

## 目前版本

- KB version：`2026.07.17`
- canonical species：1,025
- forms：1,654（PokéAPI 1,579；Pokémon Champions regulation Mega forms 75）
- normalized aliases：5,039
- Pokémon Champions Regulation Set M-B eligible entries：235
- sprite／icon metadata：1,351 Pokémon records

## 採用來源

- Pokémon Showdown：採用 `pokedex.ts` 與 `aliases.ts`，revision `f0327afadabd7688829b1d3046872017a7bdc1c3`，MIT。用途限 canonical aliases 與 form aliases。
- PokéAPI：採用 species、forms 與 localized names CSV，revision `e21557dd4cd0fefe7cb1f946bf9080e38a2e3ba4`，BSD-3-Clause。`local_language_id=4` 是 `zh-hant`。
- PokeAPI/sprites：只採用 repository tree metadata，revision `bf4c47ac82c33b330e33d98b8882d1cedb2f53e7`。該 revision 沒有 repository-level license file，因此不 vendoring 或再散布影像。
- Pokémon Champions：採用官方 Regulation Set M-B 的 factual eligible roster，以及 M-A／M-B Mega availability 公告。M-B 有效期間為 `2026-06-17T02:00:00Z` 至 `2026-09-02T01:59:00Z`。

所有來源 URL、revision 與輸入 SHA-256 都保存在 `v1/manifest.json` 與 `v1/pokemon_knowledge_base.json`。

## Visual domains

每個 asset domain 分開保存，不互相冒充：

- `battle_sprite_default`
- `home_artwork`
- `official_artwork`
- `showdown_animation`
- `generation_viii_icon`（只可作 Team Preview icon reference）
- `pokemon_champions_icon`（目前 unavailable；沒有採用可再散布且逐 form 對應的官方 asset）

Meta usage、regulation availability 與 visual similarity 是不同證據。Regulation M-B 不會被用來排除畫面上實際可見的候選，也不會提高 visual match confidence。

## Runtime lookup

`PokemonKnowledgeBase.resolve_species()` 只執行 Unicode NFKC + exact normalized alias lookup，回傳 Top-K canonical species candidates、confidence、source IDs 與 regulation metadata。它不做 fuzzy embedding、不查網路、不使用 vector database，也不訓練模型。

## Open-source discovery decision

- 採用：PokéAPI、Pokémon Showdown、PokeAPI/sprites metadata，因為來源成熟、可鎖定 revision，且資料責任清楚。
- 參考：Awesome Pokémon 清單，用於比對其他 API／sprite projects。
- 未採用：二次彙整 API、Kaggle datasets、未鎖版本的 fan databases；它們沒有比上游來源更強的 provenance。
- 未採用：PokeSprite 或 PokeAPI image binaries；本輪只建立 metadata foundation，且 Pokémon Champions visual domain 尚無可靠逐 form crosswalk。

## 重建

先依 manifest 中的 source URL 與 revision 取得鎖定輸入，再執行：

```bash
.venv/bin/python tools/build_pokemon_knowledge_base.py \
  --project-root . \
  --source-dir /path/to/locked-source-files \
  --output-dir knowledge/pokemon/v1
```

Builder 會驗證所有輸入 SHA-256、JSON Schema、canonical ID 完整性、繁中名稱、aliases、regulation crosswalk 與 sprite metadata，成功後才以非點號 staging directory 替換正式版本。
