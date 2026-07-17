# Checkpoint 1H Battle Event Reconstruction

## 目的與層級

Checkpoint 1H 是 `Observation → Battle Fact` 的第一個正式落地層。它將 frozen 上游 observations 轉成 deterministic、immutable、machine-readable Battle Facts，但不進入 Inference 或 Analysis。

```text
1D Battle Text parsed events ───────────────┐
1E reviewed Timeline／Relations ────────────┼─> deterministic reconstruction
1G HP changes／entities／decision cycles ───┘              │
local versioned KB（identity only）─────────────────────────┤
                                                            ↓
Battle Facts + reviewed fact relations + ambiguous turn candidates
```

資訊只向下流動。1H 不回寫 1D、1E 或 1G；外部知識不能建立 fact，也不能改寫 observation。

## 輸入責任

- `outputs/checkpoint-1d/battle_events.json`：Battle Text primary source。102 筆 event 各自恰好建立一筆 fact；未識別的新 taxonomy 仍建立 `UNRESOLVED_EVENT`，不會消失。
- `outputs/checkpoint-1e/battle_timeline.json`：提供 ordering 與 group membership。
- `outputs/checkpoint-1e/timeline_relations.json` 與完成審查的 review records：50 條 relation 全數保留；46 條 accepted 為 active，4 條 rejected 為 inactive。
- `outputs/checkpoint-1g/hp_changes.json`：103 筆既存 change 各自建立一筆 `HP_CHANGED`，完整沿用 confidence、value 與 `cause=unknown`，不做傷害計算。
- `outputs/checkpoint-1g/pokemon_entities.json`：只協助對齊已觀察 participant。
- `outputs/checkpoint-1g/decision_cycles.json` 與 `move_menu_observations.json`：只建立 ambiguous boundary 與 turn candidates。
- `knowledge/pokemon/v1/`：只做 exact normalized species identity resolution。1H 輸出不帶 `regulation_availability`。

所有輸入先通過 schema、manifest SHA-256、cross-reference、ID uniqueness 與 Human Review completion gates；產生完成前再驗一次 hashes。

## Fact 模型與 taxonomy

每筆 fact 包含 deterministic `fact_id`／`sequence`、時間區間、`fact_type`、certainty、confidence、participants、原始 parsed attributes、source timeline／relation／cycle IDs、重建規則，以及至少一筆 evidence record。Evidence 指向具體 checkpoint、artifact path 與 record ID；不提供可直接修改 fact 的人工欄位。

主要 taxonomy：

- 行動：`MOVE_USED`、`MOVE_RESOLVED`、`SWITCH_IN`
- 觸發：`ABILITY_ACTIVATED`、`ITEM_ACTIVATED`、`TRANSFORMATION_OCCURRED`
- 狀態：`STATUS_*`、`STAT_CHANGED`、`VOLATILE_STATUS_*`
- 場地：`WEATHER_*`、`TERRAIN_*`、`FIELD_EFFECT_*`、`SIDE_CONDITION_*`
- 結果：`DAMAGE_OBSERVED`、`HP_CHANGED`、`KO`、`BATTLE_ENDED`
- 結構：`TURN_BOUNDARY`、`UNRESOLVED_EVENT`

`DAMAGE_OBSERVED` 是 Battle Text observation，`HP_CHANGED` 是 visual observation；兩者可以時間接近，但 1H 不會未經證據自動合併或計算因果。

## Relation policy

1E relation 以相同順序映射到 fact IDs。人工 rejected record 不刪除，而是：

```json
{
  "active": false,
  "causal_claim": false,
  "review_resolution": "rejected"
}
```

`TEMPORALLY_ADJACENT` 即使人工接受，也只代表 active ordering link，`causal_claim=false`。只有 accepted `RESULT_OF`、`DAMAGE_FROM`、`STATUS_FROM`、`STAT_CHANGE_FROM` 與 `TRIGGERED_BY` 可保留上游已建立的 causal claim；1H 不創造新 relation。

## Turn policy

1G 的第一個 opening cycle 只輸出 `opening_segment`，不是 turn。其後 8 個 cycles 建立 8 個 turn candidates 與 8 個 `TURN_BOUNDARY` facts：

- `official_turn_number=null`
- `is_official_turn_number=false`
- `reconstruction_status=ambiguous`
- Move Menu candidate IDs 只存在於 boundary evidence
- available moves 不會成為 `MOVE_USED`，也不會填入 selected move

Source cycle index 只保存 ordering provenance，不能解讀成遊戲顯示的官方回合編號。

## 模組責任

- `checkpoint1h_inputs.py`：frozen manifests、schemas、hashes、Human Review completion 與 cross-reference gates。
- `battle_fact_identity.py`：保守 identity resolution；event existence 與 identity certainty 分離。
- `battle_fact_reconstruction.py`：純 event mapping、fact ordering、relation projection 與 ambiguous turn construction。
- `battle_fact_models.py`：frozen dataclasses 與 deterministic serialization。
- `checkpoint1h.py`：orchestration、consistency gates、schemas、manifest 與 transaction。

正式排序固定為 `(timestamp, source priority, source record ID)`；同一 timestamp 時依 boundary、Battle Text、HP change 排序。輸出不含產生時間或隨機 ID，因此獨立重跑 hashes 相同。

## 正式輸出

```text
outputs/checkpoint-1h/
├── battle_facts.json
├── battle_fact_relations.json
├── reconstructed_turns.json
├── checkpoint1h_audit.json
└── checkpoint1h_manifest.json
```

目前正式 baseline：213 facts、50 relations、8 ambiguous turn candidates。每個 payload 使用 JSON Schema Draft 2020-12 驗證；manifest 記錄所有 output 與 frozen source hashes。Output 使用非點號 staging 的 transactional replacement，失敗保留上一版，完成後清除 BSD hidden flag 與 `.DS_Store`。

## 開源研究決策

- 採用 [W3C PROV-O](https://www.w3.org/TR/prov-o/) 的 derived entity／provenance 概念，實作成簡單 JSON evidence records；不引入 RDF／OWL runtime。
- 採用 [JSON Schema 2020-12](https://json-schema.org/specification) 驗證正式 payload。
- 參考 [Pokémon Showdown SIM-PROTOCOL](https://github.com/smogon/pokemon-showdown/blob/master/sim/SIM-PROTOCOL.md) 對 move、switch、faint、damage 與 explicit turn message 的分離；不解析或模擬 Showdown protocol。
- 參考 [Awesome Event Modeling](https://github.com/MateuszNaKodach/awesome-eventmodeling) 的 immutable event vocabulary。
- 拒絕 event-sourcing framework、simulator、damage calculator 與 legality engine，避免由規則建立影片未觀察的 facts。

## 刻意不做

本階段不執行 Replay Analysis、AI Coach、策略評估、simulator inference、damage calculation、Regulation legality、BattleEvent parser 重跑、Timeline regrouping、GUI、部署或模型訓練。未知與 ambiguous 資料會原樣保留，不以 game knowledge 補造。
