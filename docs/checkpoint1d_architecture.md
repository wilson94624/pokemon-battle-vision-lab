# Checkpoint 1D — Battle Event Parser（MVP）

## 架構結論

Checkpoint 1D 只把已接受的 Checkpoint 1C 文字轉成 `BattleEvent` 中介格式，不建立回合、不保存戰鬥狀態，也不依規則反推 OCR。資料流固定為：

```text
checkpoint1c_review.json
  → acceptance gate
  → conservative normalization
  → ordered rule registry
  → BattleEvent / UNKNOWN_EVENT
  → battle_events.json
```

後續 Turn Timeline、Battle State、Rule Checker、Replay Review 或分析模組只能依賴 `battle_events.json`，不應直接讀取 OCR raw results。

## 開源研究與採用決策

主要借鏡來源：

- [Pokémon Showdown SIM-PROTOCOL](https://github.com/smogon/pokemon-showdown/blob/master/sim/SIM-PROTOCOL.md)：成熟地區分 `move`、`switch`、`faint` 等 major action，以及 status、boost、weather、field、item、ability 等 minor action。本專案沿用其事件邊界與分類觀念。
- [`@pkmn/protocol`](https://github.com/pkmn/ps/tree/main/protocol)：先把文字協定轉成 typed object，再交給 handler。本專案採用同樣的 registry／typed result 分層，但來源是 OCR，不直接相容 Showdown pipe protocol。
- [`@pkmn/stats`](https://github.com/pkmn/stats)：把 battle log parsing 與統計／分析分開。本專案同樣禁止 Parser 直接建立統計或 Battle State。
- [`pkmn/engine`](https://github.com/pkmn/engine)：能輸出 Showdown protocol，但本身是 simulator／state engine，超出本 Goal，未導入。
- GitHub Pokémon Showdown topic 與相關 awesome 索引可找到 replay analyzers，但大多直接消費 Showdown structured log，沒有可直接套用於繁中畫面 OCR 的成熟 schema。

結論是採用 Showdown 的 taxonomy，不採用其 wire format。OCR 事件必須多保留 `candidate_id`、時間區間、原文、正規化文字、confidence 與 1C provenance；解析失敗也不能丟棄。

## BattleEvent Schema

每筆事件包含：

- `id`：1D 穩定流水號。
- `timestamp`：MVP 明確使用 candidate `start_time`，不推算 turn。
- `start_time`／`end_time`：保留視覺候選區間。
- `candidate_id`：回溯 Checkpoint 1C 的主鍵。
- `event_type`：typed event 或 `UNKNOWN_EVENT`。
- `raw_text`：實際送入 Parser 的人工文字或 OCR 文字。
- `normalized_text`：只做 Unicode、空白與換行正規化。
- `confidence`：文字、接受決策與 rule specificity 的保守下界。
- `source`：1C input type、文字來源、接受來源與 confidence provenance。
- `metadata`：事件特有欄位，例如 `actor`、`targets`、`move`、`ability`、`item`、`status`、`stat`、`weather`、`terrain`、`effect` 與 `action`。

正式 JSON Schema 位於 `schemas/battle_event.schema.json`。

## Acceptance Gate

人工結果永遠優先：

1. `human_decision=accepted`：納入，標記 `human_accepted`。
2. `human_decision=duplicate/rejected`：不送 Parser；原始 candidate 完整保留在 1C。
3. 無人工覆寫且 `workflow_status=auto_accepted`：納入，標記 `auto_accepted`。
4. 仍有未決 `needs_review`：整個 1D 失敗，禁止默認接受。
5. `human_text` 非空時優先使用，否則使用 `ocr_text`。

Duplicate 必須指向一筆實際納入的 Accepted candidate，避免 silent dangling reference。

## Event Types（Parser 0.2.0）

目前 schema 固定支援：

- `MOVE`
- `MOVE_RESULT`
- `DAMAGE_RESULT`
- `ABILITY`
- `ITEM`
- `STATUS`
- `STAT_CHANGE`
- `WEATHER`
- `TERRAIN`
- `FIELD_EFFECT`
- `SIDE_CONDITION`
- `VOLATILE_STATUS`
- `TRANSFORMATION`
- `SWITCH`
- `FAINT`
- `BATTLE_RESULT`
- `UNKNOWN_EVENT`

`FIELD_EFFECT` 嚴格保留給全場條件；單側效果使用 `SIDE_CONDITION`，寶可夢個體的暫時效果使用 `VOLATILE_STATUS`。`MOVE_RESULT` 透過 `metadata.result` 表示 miss、critical、effectiveness、block 等結果，避免為每種結果建立過細 Event Type。完整 1D.1 audit 與 ROI 決策見 [`checkpoint1d1_quality_audit.md`](checkpoint1d1_quality_audit.md)。

## Parser 與 Normalization

規則集中在 ordered registry。每條規則包含 `rule_id`、event type、compiled regex、metadata builder、rule confidence 與允許的 1C input type；高專一規則排在廣義規則前。新增語法時應新增小規則與測試，不應擴張成巨大 `if/elif`。

Normalization 採保守策略：NFKC、移除行內空白、保留換行。`對手的` 只轉成明示的 `side=opponent`，不依 Pokémon 名稱、招式效果或前後事件猜測陣營。所有名稱保留繁中畫面用字，不做 Pokédex ID 映射。

## UNKNOWN_EVENT 策略

沒有規則可靠命中的訊息輸出為 `UNKNOWN_EVENT`，並完整保留 `raw_text`、`normalized_text`、candidate 時間、confidence provenance 與 `rule_id=unknown.unmatched`。MVP 不使用模糊比對、LLM、上下文狀態或遊戲知識把未知訊息強行分類。

## 明確非範圍

Checkpoint 1D MVP 不包含 Turn Builder、Battle State、SVO chain、傷害歸因、勝負分析、Replay Review、AI、GUI，也不會重新執行 OCR 或 candidate scanner。
