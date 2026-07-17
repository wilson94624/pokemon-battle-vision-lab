# Checkpoint 1I Acceptance Report

## 結果

Checkpoint 1I Rule Interpretation Foundation 已完成正式輸出。它只讀 frozen 1H Battle Facts／Fact Relations 與版本化 minimal rule knowledge，產生獨立 interpretation records，沒有建立或修改 Battle Fact。

正式結果：

- source Battle Facts：213
- source Fact Relations：50
- Rule Interpretations：8
- `supported`：6
- `unresolved`：2
- `conflicted`：0

## Deliverable audit

| Requirement | Authoritative evidence | Result |
|---|---|---|
| 1G／1H hygiene 與 milestone commit | `docs/checkpoint1g_1h_milestone_hygiene.md`; commit `df1367a` | PASS |
| Architecture／integration audit | `docs/checkpoint1i_architecture.md` | PASS |
| Versioned rule knowledge | `knowledge/pokemon/rules/v1/`; data／manifest schemas and hash gate | PASS |
| Minimal deterministic engine | `rule_interpretation.py`; no fact-ID／timestamp exceptions | PASS |
| Interpretation schemas／formal outputs | four `checkpoint1i_*.schema.json`; `outputs/checkpoint-1i/` | PASS |
| Unit／integration regressions | targeted 1I suite: 29 passed | PASS |
| Small reviewable result set | `interpretation_review.json`: 8 records | PASS |
| Frozen 1H direct inputs unchanged | facts `5220765d…`; relations `3707ca79…`; before／after identical | PASS |
| Transaction／visibility／cleanup | hidden=0; tmp=0; backup=0; conflict=0 | PASS |
| Scope exclusions | manifest／audit scope guards all false | PASS |

## Supported formal cases

- Bullet Punch → Sylveon super-effective：type chart consistent
- Bullet Punch → Whimsicott super-effective：dual-type multiplier consistent
- Helping Hand → accepted ally target：target validity supported，未宣稱觀察到格位 geometry
- Close Combat → explicit Protect result：supported
- 兩筆 explicit Disable prevented Earthquake result：supported，沒有重建遠距 parent relation

## Unresolved formal cases

- Helping Hand／Good as Gold：現有 facts 沒有 failure outcome，也沒有 observed Good as Gold。
- Earthquake／Levitate：現有 facts 沒有 target、immune outcome 或 observed Levitate。

Synthetic unit regressions證明上述 ability rules 在 move、target、explicit failure、matching observed ability 與 identity 全部存在時可 deterministic resolve；缺任一必要 observation 時維持 unresolved。

## Test evidence

- 1I unit＋integration＋slow deterministic suite：`30 passed in 2.24s`
- non-slow repository suite，排除兩個已知歷史 snapshot tests：`302 passed, 10 deselected`
- 完整 non-slow run：`302 passed, 2 failed, 8 deselected in 42.02s`

兩個既有失敗分別位於 `test_checkpoint1g.py` 與 `test_checkpoint1h.py` 的 `test_frozen_source_hashes_still_match_manifest`。原因是 1G／1H 完成後，使用者另行核准更新 1E Human Review 的 `reviewed_at` 與 manifest hashes；不是 1I 改動或 direct 1H output drift。為保留 frozen 1G／1H，本 checkpoint 沒有改寫其 manifests，也沒有放寬 direct payload gates。六個差異已逐 path 寫入 `checkpoint1i_audit.json`；其中 `current_sha256` 是 1I 產生時的 informational snapshot，後續核准的 1E metadata 更新不會回頭改寫 frozen 1I audit。

## Integration decisions

- adopted：pinned Pokémon Showdown selected data
- adapted：`@pkmn/data` read-only generation-scoped lookup concept
- referenced：Pokémon official Levitate semantics、PokéAPI typed resource shapes
- reviewed but not adopted：GitHub Pokémon Showdown ecosystem／topics indexes
- rejected：完整 Showdown simulator、`@pkmn/engine`、`@pkmn/dmg`、legality／damage／decision engines

## Final exclusions

- Battle Facts created／modified：false
- observation provenance rewritten：false
- simulator／damage／legality／decision engine：false
- Replay Analysis／AI Coach：false
- milestone commit：本報告隨 Checkpoint 1I milestone commit 納入版本控制
- push：未執行
