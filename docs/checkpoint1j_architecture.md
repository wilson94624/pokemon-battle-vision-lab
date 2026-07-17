# Checkpoint 1J Interpretation Review and Rule Coverage

## 1. 責任與不可變邊界

Checkpoint 1J 是 Interpretation 層的人工審查與有限 coverage 擴充，不是新的 observation 或 Battle Fact generator。它只讀：

- `outputs/checkpoint-1h/battle_facts.json`
- `outputs/checkpoint-1h/battle_fact_relations.json`
- `outputs/checkpoint-1i/rule_interpretations.json`
- `knowledge/pokemon/rules/v1/` 與 additive `v2/`
- exact approved metadata drift registry

1H facts／relations、1I interpretations、certainty、conclusion、observation provenance 與 knowledge provenance 都是 immutable。Human Review 是另一份資料；它只能引用 interpretation ID 與 canonical payload SHA-256，不能攜帶可編輯 conclusion。

## 2. Review record

每筆 `InterpretationReviewRecord` 保存：

- deterministic review record ID
- interpretation ID、origin、payload SHA-256
- generated certainty 的唯讀副本
- `accepted`／`rejected`／`needs_review`／`deferred`
- reviewer、reviewed timestamp、reason
- structured issue codes
- conflict category（只供 conflicted records）
- interpretation／knowledge／review schema versions
- review card path
- conflicted record 的完整 conflict context

`needs_review` 的 reviewer／timestamp／reason 必須為 `null`。其他狀態必須有三者。`certainty=unresolved` 可以被接受，但必須有 `unresolved_outcome_correct`，表示 reviewer 接受「維持 unresolved」而不是把它改成 supported。

## 3. Review Pack 與操作流程

正式 pack 位於：

```text
outputs/checkpoint-1j/review_pack/
├── review_index.md
├── review_worksheet.csv
└── cards/
    └── interpretation-review-0001.md ... 0018.md
```

每張卡列出 referenced Battle Facts、raw text／parsed metadata／participants／evidence、Fact Relations、required observations、knowledge evidence、derived conclusion、certainty、unresolved reason與 conflict context。Card 與 JSON 都是唯讀；人工只編輯 worksheet 的 review 欄位，再用 `--review-decisions` 匯入。

CSV importer 會逐列驗證 `review_record_id`、`interpretation_id`、payload hash 與 certainty。未知、重複、遺漏 record，或任何 immutable 欄位變動都會中止。輸入 worksheet 即使位於即將被替換的正式 output tree，也會在 transaction 開始前完整讀取與驗證。

## 4. Conflict policy

Production baseline 沒有 artificial conflicted record。未來若 interpretation 的 observed conclusion 與 knowledge expectation 衝突，review record 必須保存：

- observed conclusion
- knowledge-derived expectation
- exact conflicting fields 與兩側 values
- Battle Fact／Fact Relation／knowledge evidence refs
- reviewer status 與 structured conflict category

允許類別包括 observation／identity／knowledge／rule-engine error suspected、version mismatch、insufficient evidence 與 unresolved other。Reviewer 只能分類、拒絕或延後，不能選一側覆寫 immutable interpretation。

## 5. Additive knowledge v2

`knowledge/pokemon/rules/v1/` 完整保留，data SHA-256 為 `ac3cfc8205c6f75ecab20b954346303c28db43e665db36ba653042fdbb0e506d`，manifest SHA-256 為 `7ae4ab9a6f8e476e40d4392664a94a1991b00b2a1b6a97ff53fbd0e5e12f2393`。

v2 新增 4 個 selected moves、7 個 linked observation rules 與 2 個 explicit lifecycle rules。Migration manifest 保存 previous version paths／hashes、13 個 exact added knowledge IDs、pinned source revision／file hashes，以及 `required_existing_interpretation_ids=[]`。既有 v1 interpretations 不需重建；v2 只產生新的 coverage。

上游 mechanics reference 固定為 Pokémon Showdown revision `f0327afadabd7688829b1d3046872017a7bdc1c3` 的 selected `moves.ts`、`abilities.ts` 與 `items.ts`。Runtime 不連網、不載入完整 simulator，也不把 knowledge 當成 observation。

## 6. Rule coverage 與因果限制

Expansion selector 只使用 fact type、explicit parsed metadata、participant identity 與 active relation type；production code 沒有 fact ID 或 timestamp exceptions。

正式新增 10 筆 interpretations，涵蓋 9 個 rule IDs：Will-O-Wisp burn／miss、Intimidate attack drop、Rain Dance rain start、Wave Crash recoil、Tailwind start、Life Orb self-damage、explicit rain end、explicit Tailwind end。

`TEMPORALLY_ADJACENT` 只表示順序一致；這四類 conclusion 固定 `relation_semantics=consistency_only`、`causal_claim=false`。Wave Crash／Life Orb 需要 active `DAMAGE_FROM` 與 observed identity continuity；Tailwind start 需要 active `STATUS_FROM`，才允許 `causal_claim=true`。Explicit end facts 只說 observed lifecycle end，不推算 duration。

Coverage audit 明確記錄六個 rejected candidates 與一個 deferred candidate。Rejected cases 不會產生 interpretation；deferred Levitate／Good as Gold 繼續由 1I 的 unresolved records 表達。

## 7. Historical snapshot drift

`references/approved_upstream_metadata_drift.json` 是 exact tuple allowlist：

```text
(consumer checkpoint, source path, frozen snapshot hash, approved current hash)
```

相同 hash 直接通過；不同 hash 必須精確命中 registry。現有 7 筆 records只核准 Checkpoint 1E Human Review 的人工 metadata／連動 manifest hashes。Registry 未列入的 drift 會失敗；direct frozen 1G／1H payload hashes 與 direct 1H inputs 永遠不走 allowlist，仍為 blocking gates。既有 frozen manifests 不會被重寫。

## 8. Formal outputs

```text
outputs/checkpoint-1j/
├── expanded_rule_interpretations.json
├── interpretation_review_records.json
├── review_summary.json
├── review_statistics.json
├── conflict_review_policy.json
├── rule_coverage_audit.json
├── historical_snapshot_drift_audit.json
├── checkpoint1j_audit.json
├── checkpoint1j_manifest.json
└── review_pack/
```

首次正式結果為 8 個既有 interpretations＋10 個新 interpretations、18 個 review records、16 supported／2 unresolved／0 conflicted，狀態是 `complete_pending_human_review`。

## 9. Transaction、determinism 與限制

輸出先寫入同層非點號 staging，逐份 schema／hash／direct-input validation 成功後才 atomic replace。失敗保留上一版；正式 tree 會清除並驗證 BSD hidden flag，不留下 `.DS_Store`、tmp、backup 或 ` 2` conflict directory。

沒有 review decisions 時輸出完全 deterministic。人工 timestamp 是 worksheet input 的一部分，只影響獨立 review payload，不改變 interpretations。

1J 不建立 Battle Facts、不修正 OCR／Timeline、不做完整 type chart、simulator、damage calculation、hidden-information inference、Replay Analysis、GUI 或戰術分析。
