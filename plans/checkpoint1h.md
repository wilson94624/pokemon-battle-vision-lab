# Checkpoint 1H Battle Event Reconstruction Plan

## 目標

將 frozen Checkpoint 1D／1E／1G observations 重建為 immutable、deterministic、可逐筆追溯的 Battle Facts。1H 不重跑 OCR、Battle Text parser、Timeline Builder 或 visual enrichment，也不使用 simulator／規則／meta 建立事件。

## 採用的輸入責任

- Checkpoint 1D `battle_events.json`：Battle Text primary observation。每筆 1D event 必須恰好產生一筆 text-derived Battle Fact，包括未來可能出現的 `UNKNOWN_EVENT`。
- Checkpoint 1E Timeline／Relations／Human Review：只提供 ordering、group membership 與已審查 relation。人工 rejected relation 保留為 inactive relation record，不可形成 causal claim。
- Checkpoint 1G `hp_changes.json`：每筆既有 HP change 產生一筆 `HP_CHANGED` fact；沿用既有 confidence、value type 與 `cause=unknown`，不做 damage calculation。
- Checkpoint 1G `pokemon_entities.json`：只作觀察名稱至 canonical identity 的保守對齊。
- 本機 Pokémon Knowledge Base：只對 Battle Text 已觀察名稱做 exact normalized alias resolution；不能新增 fact。
- Checkpoint 1G `decision_cycles.json`：只建立 ambiguous turn candidates 與 ambiguous `TURN_BOUNDARY` facts；`official_turn_number` 永遠為 `null`，Move Menu 不建立 `MOVE_USED`。

## 預計正式輸出

`outputs/checkpoint-1h/`：

- `battle_facts.json`
- `battle_fact_relations.json`
- `reconstructed_turns.json`
- `checkpoint1h_audit.json`
- `checkpoint1h_manifest.json`

## Fact taxonomy

1D event type／action 以純 mapping 轉為：

- `MOVE_USED`
- `MOVE_RESOLVED`
- `DAMAGE_OBSERVED`
- `SWITCH_IN`
- `ABILITY_ACTIVATED`
- `ITEM_ACTIVATED`
- `STATUS_APPLIED`／`STATUS_REMOVED`／`STATUS_CHANGED`
- `STAT_CHANGED`
- `WEATHER_STARTED`／`WEATHER_ENDED`／`WEATHER_CHANGED`
- `TERRAIN_STARTED`／`TERRAIN_ENDED`／`TERRAIN_CHANGED`
- `FIELD_EFFECT_STARTED`／`FIELD_EFFECT_ENDED`／`FIELD_EFFECT_UPDATED`
- `SIDE_CONDITION_STARTED`／`SIDE_CONDITION_ENDED`／`SIDE_CONDITION_UPDATED`
- `VOLATILE_STATUS_APPLIED`／`VOLATILE_STATUS_REMOVED`／`VOLATILE_STATUS_UPDATED`
- `TRANSFORMATION_OCCURRED`
- `KO`
- `BATTLE_ENDED`
- `UNRESOLVED_EVENT`

另由 1G observations 產生 `HP_CHANGED`，由 Decision Cycle boundary evidence 產生 `TURN_BOUNDARY`（一律 `certainty=ambiguous`）。

## Immutable Fact model

每筆 fact 必須包含：

- deterministic `fact_id` 與 `sequence`
- `fact_type`、時間區間、confidence、certainty
- 原始 observation payload 的保守 attributes
- zero-or-more normalized participants；identity resolution 與 event existence 分離
- one-or-more evidence records，含 checkpoint、artifact path、record ID、observation kind、role
- source timeline／relation／decision-cycle IDs
- deterministic reconstruction rule ID

Fact payload 不提供人工可回寫欄位；更正必須在上游 observation 或未來獨立 review layer 完成，不能 in-place mutation。

## Turn policy

- opening Decision Cycle 只保存為 `opening_segment`，不是 turn。
-其後 8 個 Decision Cycles 建立 `turn_candidate-001` 至 `turn_candidate-008`。
- `official_turn_number=null`、`is_official_turn_number=false`、`reconstruction_status=ambiguous`。
- cycle index 只作 source ordering，不冒充官方 turn number。
- Move Menu candidate IDs 只能作 boundary evidence；不得讀取 available move 當 selected move。

## Open-source discovery decision

- 採用概念：[W3C PROV-O](https://www.w3.org/TR/prov-o/) 的 derived entity／provenance bundle，落地為本專案簡單 JSON evidence records；不引入 RDF／OWL runtime。
- 採用：[JSON Schema 2020-12](https://json-schema.org/specification) 作所有正式 payload validation。
- 參考：[Pokémon Showdown SIM-PROTOCOL](https://github.com/smogon/pokemon-showdown/blob/master/sim/SIM-PROTOCOL.md) 對 move、switch、faint、damage、turn message 的分離；不解析或模擬 Showdown protocol。
- 參考：[Awesome Event Modeling](https://github.com/MateuszNaKodach/awesome-eventmodeling) 的 immutable event vocabulary。
- 拒絕：event-sourcing framework，因 1H 只需要 deterministic batch reconstruction，不需要 command bus、aggregate persistence 或 projection store。
- 拒絕：Pokémon simulator／damage calculator／legality engine，因它們可能由規則生成影片未觀察的 event。

## 驗收矩陣

- 每個 1D event 恰好對應一個 fact，無遺漏、無重複。
- 每個 1G HP change 恰好對應一個 `HP_CHANGED` fact。
- 每個 fact 至少一筆有效 evidence；所有 referenced IDs 均存在於 frozen inputs。
- Fact 按 `(timestamp, source priority, source ID)` deterministic 排序，IDs 唯一。
- 1E 50 relations 全數保留；46 accepted active、4 human-rejected inactive。
- rejected temporal adjacency 不得成為 causal relation。
- 8 個 ambiguous turn candidates；8 個 ambiguous boundary facts；沒有官方 turn number。
- Move Menu 不產生 move-used fact；KB 不增加 fact count。
- 所有 schemas、manifest hashes、source hashes、path traceability 通過。
- 兩次獨立執行 payload SHA-256 完全一致。
- transaction failure 保留上一版 output；staging 非點號。
- Frozen inputs 執行前後 hashes 完全一致。
- final output 無 BSD hidden flag、`.DS_Store`、tmp、backup 或 ` 2` conflict directory。
- 既有 checkpoint regression tests 維持相容。
