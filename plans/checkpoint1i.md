# Checkpoint 1I Rule Interpretation Foundation Plan

## 不可跨越的層級

1I 位於 `Battle Fact → Interpretation`。它只讀 frozen 1H facts／relations，輸出獨立 immutable interpretation records；不能回寫、建立或刪除 Battle Fact，不能改寫 observation provenance，也不能把 knowledge conclusion 標成 observation。

## 現有 evidence audit

- 1H 有 213 facts；正式能力 observation 只有 `威嚇`，沒有 `飄浮` 或 `黃金之軀`。
- 可解釋的 type outcome：兩組 `子彈拳 → 效果絕佳`，targets 為 `仙子伊布`、`風妖精`。
- 可解釋的明示規則：兩筆 `因定身法而無法使出地震`。
- 可解釋的 target/outcome：`幫助 → helping_hand_ready`、`近身戰 → 守住`。
- `地震` fact 沒有 target／immune outcome／observed Levitate，因此只能輸出 unresolved。
- `幫助` target 有 identity，但沒有 failure outcome 或 observed Good as Gold，因此 ability immunity 只能 unresolved。

## Knowledge integration 決策

- **adopted**：Pokémon Showdown pinned revision `f0327afadabd7688829b1d3046872017a7bdc1c3` 的 selected move、ability、typechart、species type fields，裁成最小版本化 JSON。
- **adapted**：`@pkmn/data` 的 generation-scoped lookup 與 data-layer separation，實作成 Python read-only adapter；不加入 Node dependency。
- **referenced**：Pokémon 官方 Pokédex 對 Levitate 的免疫敘述；PokéAPI v2 的 Move／Type／Pokémon typed resource shape。
- **rejected**：完整 Showdown simulator、`@pkmn/engine`、`@pkmn/dmg`、TeamValidator／legality engine。它們超出解釋既存 facts 所需，且可能引入模擬結果。

## 最小 ruleset

- type effectiveness：`Steel → Fairy`，包含 dual-type neutral multiplier aggregation。
- ability immunity：observed Levitate + Ground move；observed Good as Gold + other-Pokémon Status move。
- explicit failure：Battle Fact 同時明示 `result=prevented`、`effect=定身法`。
- target validity：Helping Hand 的 observed actor／target／success outcome 與 `adjacentAlly` rule。
- move-vs-target outcome：explicit Protect result。

Unknown move、target、identity、ability 或 outcome 一律 unresolved。

## 正式輸出

`outputs/checkpoint-1i/`：

- `rule_interpretations.json`
- `interpretation_review.json`
- `checkpoint1i_audit.json`
- `checkpoint1i_manifest.json`

預期現有影片 result set：8 records、6 resolved、2 unresolved。

## Acceptance matrix

- 所有 interpretation IDs deterministic、唯一、順序穩定。
- 每筆至少一個 existing 1H fact ID；所有 ID 均可解析。
- observed evidence、knowledge evidence、derived conclusion 三層分離。
- 1H facts／relations／manifest hashes 執行前後完全不變。
- synthetic regression：Ground move + observed Levitate + explicit immune outcome 可解釋。
- synthetic regression：Helping Hand + observed Good as Gold target + explicit failure outcome 可解釋。
- missing ability／target／move／identity cases 保持 unresolved。
- 正式 baseline 不宣稱 Levitate 或 Good as Gold 已在影片中觀察。
- 不包含 damage、legality、simulator、AI Coach、strategy 或 Replay Analysis。
- schemas、output hashes、knowledge hashes、transaction failure、macOS visibility 全部通過。
