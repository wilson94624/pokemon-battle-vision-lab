# Checkpoint 1I Rule Interpretation Foundation

## 1. 架構位置

Checkpoint 1I 是 `Observation → Battle Fact → Interpretation` 的第一個正式 Interpretation 層。資料只往下游流動：

```text
Checkpoint 1H immutable Battle Facts
            +
versioned minimal Rule Knowledge
            ↓
Checkpoint 1I Rule Interpretations
```

Interpretation 只能引用 `battle-fact-*` 與 `battle-fact-relation-*`。它不修改 1H JSON、不建立新的 observed Battle Fact、不改寫 observation provenance，也不把衍生結論放回 `attributes`。

## 2. Knowledge／integration audit

本 checkpoint 先稽核既有 identity Knowledge Base、Pokémon Showdown 與 `@pkmn` 生態。採用決策記錄在 `knowledge/pokemon/rules/v1/rule_knowledge.json` 的 `sources`：

- **adopted — Pokémon Showdown pinned data**：只擷取三個 moves、兩個 species types、五個必要 type-chart entries、兩個 ability rules 與明確 rule metadata。revision 固定為 `f0327afadabd7688829b1d3046872017a7bdc1c3`，原始檔 SHA-256 一併保存。
- **adapted — `@pkmn/data` interface concept**：採用 generation-scoped、read-only lookup 的分層概念；不加入 Node runtime，也不複製其完整資料模型。
- **referenced — Pokémon official Levitate page、PokéAPI v2 docs**：只用於語意與 typed resource shape 交叉確認，不在 runtime 呼叫網路。
- **rejected — 完整 Pokémon Showdown simulator、`@pkmn/engine`、`@pkmn/dmg`**：這些介面會把範圍擴張到完整模擬、damage calculation 或 battle mechanics execution，超出最小 foundation。

另查核 GitHub 的 Pokémon Showdown topics／ecosystem indexes；沒有找到比 pinned Showdown data＋小型 adapter 更貼合本 checkpoint 的維護中 Awesome list。可見方案多偏 simulator、usage statistics、client 或戰術分析，因此只記錄為 reviewed，未採用。

沒有採用 legality engine、damage calculator、decision engine、完整 simulator 或雲端服務。

## 3. Versioned knowledge representation

正式 knowledge 位於：

```text
knowledge/pokemon/rules/v1/
├── rule_knowledge.json
└── manifest.json
```

`manifest.json` 固定 knowledge version、資料 hash、source counts 與 scope guards。`PokemonRuleKnowledgeBase` 啟動時驗證兩份 schemas、data hash、version、counts、aliases、`knowledge_id` 與 `source_refs`。它只提供 lookup／type multiplier，不暴露 simulator interface。

目前最小資料：

- moves：Bullet Punch、Earthquake、Helping Hand
- species types：Sylveon、Whimsicott
- type effectiveness：只涵蓋正式案例與 Ground immunity regression 所需 entries
- ability rules：Levitate、Good as Gold
- explicit rules：Disable prevents move、Protect blocks move
- target rule：Helping Hand `adjacentAlly`

未知 move、species、ability 或 type entry 不會由其它資料猜測；缺失條件進入 `unresolved`。

## 4. Interpretation record

每筆 `RuleInterpretation` 明確分成三個區塊：

1. `observed_evidence`：引用既有 1H fact 與其 observation record IDs。
2. `knowledge_evidence`：保存 `knowledge_id`、version、knowledge SHA-256、內部 path 與 upstream `source_refs`。
3. `conclusion`：保存衍生 code、摘要與 derived values。

另外保存 deterministic interpretation ID／sequence／timestamp、referenced Battle Fact／Fact Relation IDs、interpretation type、rule ID／version、逐項 `required_observations`、certainty、confidence 與 unresolved reason。

只有所有必要 observations 都 `satisfied` 時才可輸出 `supported`。Knowledge 本身永遠不能補齊 target、ability、move 或 identity。

## 5. Initial supported rules

### Type effectiveness

必須同時存在 active 1H relation、已觀察 MOVE_USED、已觀察 target identity、版本化 species types 與明確 effectiveness outcome。Dual types 以乘法組合；若觀察與目前 knowledge 衝突，保留 `conflicted`，不改寫觀察。

### Ability immunity

只有以下條件全部存在才 `supported`：

- move 已觀察且 rule knowledge 可解析
- move type／category 符合 ability rule
- explicit immune／no-effect／failed outcome 已觀察
- target 已觀察
- matching ability 已在同一 target identity 上觀察
- Good as Gold 類規則另要求 source 與 target 是不同 Pokémon

Formal battle 未觀察到 Levitate 或 Good as Gold，因此兩筆 interpretation 正確保留 `unresolved`。Synthetic regressions 則證明 evidence 完整時可以 deterministic resolve。

### Explicit result rules

Protect 與 Disable 只在既有 `MOVE_RESOLVED.parsed_metadata` 明確符合 `result/effect` 時解釋，不靠鄰近時間補因果。Protect 可引用 active move→result relation；兩筆 unlinked Disable result 仍能以其自身明確 observation 解釋，但不重建遠距 parent relation。

### Target validity

Helping Hand 需要 observed actor、target、同側 participant、active relation 與 `helping_hand_ready` 明確成功結果。結論只說遊戲已接受 target、與 `adjacentAlly` rule 一致；`visual_geometry_observed=false`，不宣稱畫面量測過 adjacency。

## 6. Formal result set

`outputs/checkpoint-1i/` 目前有 8 筆小型可審查 records：

- 2 × type effectiveness：supported
- 1 × Helping Hand target validity：supported
- 1 × Protect explicit outcome：supported
- 2 × Disable explicit outcome：supported
- 1 × Good as Gold ability immunity：unresolved
- 1 × Levitate ability immunity：unresolved

這 8 筆由 fact attributes、active relations 與 knowledge applicability 選出；production code 沒有 hardcode fact IDs 或 timestamps。

## 7. Input gate 與 provenance drift

1I 的直接權威輸入是 frozen 1H manifest 與其四個 outputs。啟動時逐一驗證 schemas 與 manifest hashes，並在產生前後重驗直接 inputs 未改變。

目前 1H manifest 保存的六個 1E review upstream snapshot hashes 與 1I 產生當下的現況不同，原因是 1H 完成後另一次人工審查只更新 `reviewed_at` 與 review manifest hashes。`current_sha256` 是 1I 產生時的 informational snapshot，不是後續 1E metadata 的永久 live assertion。1H Battle Facts／Fact Relations 本身沒有重建或修改。1I 將這些 upstream snapshot differences 透明列在 `checkpoint1i_audit.json`，但不以它們改寫 frozen 1H；direct 1H output hash gate 仍是 blocking。

## 8. Outputs 與 transaction

```text
outputs/checkpoint-1i/
├── rule_interpretations.json
├── interpretation_review.json
├── checkpoint1i_audit.json
└── checkpoint1i_manifest.json
```

輸出使用既有 `OutputTransaction`：非點號 staging → 完整 schema/hash validation → atomic replace → hidden flag validation。失敗時保留上一版，不留下 tmp、backup、`.DS_Store` 或 ` 2` conflict directory。

## 9. 明確限制

- 不是完整 Pokémon Champions mechanics coverage。
- 不計算 damage、speed、accuracy、priority、legal movesets 或 hidden information。
- 不推斷未觀察 ability、item、target、intent 或 move choice。
- 不重建 official turn，也不開始 Replay Analysis 或 AI Coach。
- Minimal type chart 只支援本 checkpoint 的選定 regressions；新規則必須先版本化 knowledge 並新增 evidence gates。

建議下一個 checkpoint 先擴充人工可驗證的 interpretation coverage 與 conflict review policy，再考慮任何 replay-level aggregation；不得直接跳到戰術評估。
