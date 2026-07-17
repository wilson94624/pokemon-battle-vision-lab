# Checkpoint 1J Acceptance Report

## 結論

Checkpoint 1J 工程交付已完成，正式 manifest 狀態為 `complete_pending_human_review`。此狀態是預期結果：18 筆 interpretation reviews 全部預設 `needs_review`，沒有未經授權的自動接受。Human Review Pack 已可直接使用；人工完成 worksheet 前不宣稱 review completion。

## Checkpoint 1I milestone

- Commit：`fcb69551f12136696cfdfc35abb047e2a2e0a346`
- Message：`Checkpoint 1I: add rule interpretation foundation`
- Push：未執行
- 1H direct hashes：
  - `battle_facts.json`：`5220765daae28e45d4ec4e6b806bbdd217d4bc9038be99374de1e971a5ef847c`
  - `battle_fact_relations.json`：`3707ca794c660a68f4c9281929c6bde2f56d27db60c2671e979d81b6db02f86d`
  - `checkpoint1h_manifest.json`：`591c6a3794058bf0b511be00336048602b614e7b497256333286752d29ec7dcd`

## Formal outputs

正式路徑：`outputs/checkpoint-1j/`

- Source Battle Facts：213
- Source Fact Relations：50
- Existing 1I interpretations：8
- Expanded 1J interpretations：10
- Review records／cards：18／18
- Generated certainty：16 supported、2 unresolved、0 conflicted
- Human status：18 needs_review、0 accepted、0 rejected、0 deferred
- Formal tree：29 files
- Manifest／payload schemas：9 份通過
- Review Pack refs：20 個 hashes 通過
- Deterministic formal rerun：29 files 全部 byte-identical

## Evidence-backed coverage

採用 9 個 rule IDs，產生 10 筆 interpretations：

1. `status_outcome.will_o_wisp_burn.v1`：2 筆
2. `move_failure.will_o_wisp_miss.v1`
3. `ability_consequence.intimidate_attack_drop.v1`
4. `weather.rain_dance_started.v1`
5. `damage_consequence.wave_crash_recoil.v1`
6. `side_condition.tailwind_started.v1`
7. `item_consequence.life_orb_self_damage.v1`
8. `weather.rain_ended_explicit.v1`
9. `side_condition.tailwind_ended_explicit.v1`

鬼火、威嚇與求雨只依 `TEMPORALLY_ADJACENT` 表達 sequence consistency，`causal_claim=false`。波動衝、生命寶珠與順風開始分別需要既有 active `DAMAGE_FROM`／`STATUS_FROM` evidence；其中 identity-sensitive rules 另要求 observed identity continuity。

## Rejected／deferred coverage

Rejected：

- Close Combat stat change：觀察與外部 mechanics expectation 衝突。
- Charm stat change：缺少排除 ability／rules variation 的 evidence。
- Weather Ball dynamic type：缺 target、type、outcome。
- Burn residual parent move：不得把人工拒絕的 adjacency 重建為因果。
- Perish Song future KO：沒有 observed KO。
- Complete type chart：超出現有 selected facts。

Deferred：

- Levitate／Good as Gold：沿用 1I unresolved；仍缺 matching target、outcome、ability observations。

## Knowledge versioning

- v1 data SHA-256：`ac3cfc8205c6f75ecab20b954346303c28db43e665db36ba653042fdbb0e506d`
- v1 manifest SHA-256：`7ae4ab9a6f8e476e40d4392664a94a1991b00b2a1b6a97ff53fbd0e5e12f2393`
- v2 data SHA-256：`35a374058bb6600a1cfb09047c15b30ce52b4dcacdd261072cab3b3c55127bb1`
- Migration：additive；13 個 exact added knowledge IDs
- Existing semantic payloads：全部 byte-equivalent preserved
- Existing interpretations requiring regeneration：0

Upstream selected mechanics 固定於 Pokémon Showdown revision `f0327afadabd7688829b1d3046872017a7bdc1c3`，manifest 保存 selected source file hashes。Runtime 不連網且不載入 simulator。

## Conflict policy

Production conflicted count 為 0，沒有為測試人造 production record。Formal policy 與 synthetic regressions涵蓋：

- observed conclusion
- knowledge-derived expectation
- exact conflicting fields／values
- Battle Fact／Fact Relation／knowledge evidence refs
- 七種 structured conflict categories
- reviewer decision 不覆寫原 interpretation

## Historical snapshot drift

Registry 有 7 個 exact approved tuples，全部被實際消耗：1 個 1G、6 個 1H。它們只涵蓋已獲授權的 1E Human Review metadata／連動 manifest hash changes。

- Direct frozen 1G／1H payload hashes：blocking
- Direct 1H inputs used by 1I／1J：blocking
- Unexpected drift：blocking
- Frozen 1G／1H manifests：未改寫

1H slow regression 已改成驗證 formal direct payload hashes 與六個 exact 1H approvals；不在缺少舊 metadata bytes 時假裝可重建 byte-identical historical manifest。

## Tests

- 1J targeted unit：15 passed
- 1J integration：12 passed
- 1J CLI slow smoke：1 passed
- Repository non-slow：331 passed、9 deselected
- Repository full slow（sandbox 外，Apple Vision production runtime）：9 passed、331 deselected，19:06

Sandbox 內第一次 full slow 有三個 Apple Vision tests因 `Vision request failed without NSError` 失敗；同三個 tests 在 sandbox 外通過，確認是 sandbox runtime 限制。第四個 failure 是尚未套用 registry 的舊 1H slow assertion；修正後在完整 slow suite 通過。

## Transaction／visibility／scope

- Transaction failure 保留舊 output：通過
- Staging 非點號開頭：通過
- Formal BSD hidden items：0
- `.DS_Store`／tmp／backup／` 2` conflict dirs：0
- Battle Facts created／modified：false／false
- Existing Rule Interpretations modified：false
- Observation／knowledge provenance modified：false／false
- Complete simulator／hidden inference／Replay Analysis／GUI：false

## Remaining human work and risk

1. 18 筆 review decisions 尚未由人工完成，這是目前唯一預期中的 workflow state。
2. Production baseline沒有 conflicted interpretation；conflict policy 目前由 synthetic regressions 驗證。
3. Coverage 只涵蓋現有 facts，可用規則不是完整 Pokémon mechanics database。
4. Temporal consistency interpretations 必須維持非因果語意，人工 review 不應把它們升格為 causal facts。

建議下一步先依 `review_pack/review_index.md` 逐張檢查，填寫 `review_worksheet.csv`，再以 `--review-decisions` 匯入；不要在人工完成前開始 replay-level aggregation 或策略分析。
