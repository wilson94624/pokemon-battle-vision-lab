# Checkpoint 1F Battle State Architecture Audit

本文件是 Checkpoint 1F 修改前的唯讀稽核。資料來源為已凍結的 Checkpoint 1D `battle_events.json`、Checkpoint 1E `battle_timeline.json`／`timeline_relations.json`，以及已完成人工審查的 Checkpoint 1E review JSON。本輪不把沒有出現在事件 metadata 的遊戲知識補進 state。

## 1. Frozen input 摘要

| 項目 | 結果 |
| --- | ---: |
| BattleEvents | 102 |
| Timeline Groups | 70 |
| Relations | 50 |
| Auto-accepted relations | 32 |
| Human accepted relations | 14 |
| Human rejected relations | 4 |
| Accepted unlinked groups | 2 |
| Remaining needs-review relations | 0 |
| Remaining unreviewed unlinked groups | 0 |

人工拒絕的 `relation-0019`、`relation-0030`、`relation-0036`、`relation-0041` 都只是 MOVE 與後續灼傷殘餘傷害的時間相鄰，不可用來建立因果 state transition。`timeline-0056`、`timeline-0063` 已確認維持 unlinked，可獨立記錄「因定身法無法使出地震」的 observation，但不可回連 `timeline-0054`。

事件類型分布：

| Event type | 數量 | State feasibility |
| --- | ---: | --- |
| MOVE | 29 | 通常為 no-op；不可由招式名稱推測效果 |
| VOLATILE_STATUS | 21 | target 明確，可追蹤開始、結束與明示 counter |
| DAMAGE_RESULT | 13 | 不重建 HP，也不由 residual damage 反推 status |
| MOVE_RESULT | 8 | 大多為 no-op；兩個 accepted-unlinked prevention 保留 unresolved observation |
| SWITCH | 6 | 可登記 Pokémon 與 active evidence，但 metadata 沒有明示 side／slot |
| STAT_CHANGE | 6 | target／targets、stat、direction、magnitude 皆可用 |
| FAINT | 4 | target 明確；2 筆缺 side，但可在名稱唯一時保守解析 entity |
| TRANSFORMATION | 4 | actor 與 phase 明確，可追蹤 activation／completion chain |
| STATUS | 2 | target、side、status 明確，可可靠套用 |
| WEATHER | 2 | start／end 與 weather 明確，可可靠套用 |
| SIDE_CONDITION | 2 | side、effect、start／end 明確，可可靠套用 |
| BATTLE_RESULT | 2 | forfeit 與 loser 明確；winner／player identity 不完整 |
| ABILITY | 1 | actor、ability 明確，只記錄已觀察到的 ability evidence |
| ITEM | 1 | actor、item 明確，只記錄已觀察到的 item evidence |
| FIELD_EFFECT | 1 | 全場滅亡之歌 activation 與 counter=3 明確，不推算回合 |

## 2. 開源方案研究

### Pokémon Showdown 與 SIM-PROTOCOL

[Pokémon Showdown](https://github.com/smogon/pokemon-showdown) 將 `Battle`、`Side`、`Pokemon`、`Field` 分離；effect state 依類型附著於 Pokémon status／volatiles、side conditions、field weather／terrain。這個責任切分適合借鏡。其 [SIM-PROTOCOL](https://github.com/smogon/pokemon-showdown/blob/master/sim/SIM-PROTOCOL.md) 以 `switch`、`faint`、`-status`、`-boost`、`-weather`、`-sidestart`、`-start` 等明確訊息更新 state，也明確把 target、side、來源 effect 放在協定中。

不直接採用 Showdown simulator：它需要完整 team、choice、position、turn、HP 與 protocol stream，而本專案只有從影片文字取得的稀疏 observation。用 simulator 補齊缺失欄位會把假設偽裝成事實。

Stat stage 採 `[-6, +6]` clamp；Showdown 的 [`Pokemon.calculateStat`](https://github.com/smogon/pokemon-showdown/blob/master/sim/pokemon.ts) 也將 boost 限制在此範圍。1F 只 clamp 已明示的累積 stage，不由 move data 產生 stage。

### @pkmn/protocol、@pkmn/client、@pkmn/data／types

[`@pkmn/protocol`](https://github.com/pkmn/ps/tree/main/protocol) 把文字 protocol 解析成 typed objects，並用 Handler 分派各類訊息；[`@pkmn/client`](https://github.com/pkmn/ps/blob/main/client/README.md) 則把 protocol 與 request 中「所有已知資訊」累積成 Battle state。1F 採用其 typed event handler／registry、state 與 parser 分離，以及只累積已知資訊的概念。

不直接安裝 `@pkmn/*`：本專案輸入不是 Showdown protocol，且 Python pipeline 不需要 Pokémon data layer。`@pkmn/data`／`@pkmn/types` 的完整物種與規則資料在本輪反而可能誘使 projector 依種族知識補值。

### poke-env

[`poke-env` Battle object](https://poke-env.readthedocs.io/en/stable/modules/battle.html) 分別維護 active Pokémon、team、weather、side conditions 與勝負，說明這些是合理的 state boundary。它同樣依賴完整 Showdown message stream、team preview 與 request，並包含 available moves／orders 等本輪禁止的資訊，因此只借鏡資料責任，不導入 runtime。

### @smogon/calc

[`@smogon/calc`](https://github.com/smogon/damage-calc) 需要 generation、attacker、defender、move，並可接受 field／side state。現有 evidence 缺少 HP、EV／IV、完整 item／ability、move target 與 speed order；本輪不做 damage calculation，因此不採用。

### Event Sourcing、Reducer 與 immutable snapshot

Event Sourcing（事件溯源）的核心是保留不可變事件流，state 由事件重放得到；Reducer（狀態歸約器）可表示為 `(state, event) -> state + delta`。1F 採用：

1. frozen BattleEvent／Timeline 不修改；
2. 每個 Timeline Group 產生一個 delta；
3. 每個 delta 後保存 immutable snapshot；
4. reducer registry 依 event type 分派；
5. unresolved／conflict 是正式輸出，不以例外或靜默覆寫消失；
6. 人工 review 欄位與自動 projection 分離。

不導入通用 event-sourcing framework 或 finite-state-machine library：目前只有單一 deterministic projector，外部 framework 會增加 persistence／aggregate／command bus 假設，卻無法解決稀疏 evidence 的 unknown 語意。

## 3. 23 項 feasibility 結論

1. **可可靠重建**：明示 status、volatile observation／counter、stat stage change、weather start/end、side condition start/end、transformation observation、已觸發 ability/item、faint observation、battle result 的明示部分。
2. **只能部分重建**：active set、Pokémon side、完整 roster、transformation identity chain、battle winner。只能保存已觀察 entity 與尚未解析 side。
3. **完全不能重建**：精確／百分比 HP、PP、EV／IV、完整 moveset、未明示 item／ability、speed order、正式 turn、left/right slot、choice／target selection、完整 team roster。
4. **初始在場 Pokémon**：第一批 SWITCH 提供 4 個名稱，可知道它們被送上場，但沒有明示 side／slot；因此只能建立 partial active evidence。
5. **TEAM_PREVIEW／SELECTED_FOUR**：未進入 BattleEvent／Timeline。102 events 的 candidate source 只有 `battle_text` 與 `trigger_notification`。
6. **起始策略**：從 `timeline-0001`、`timeline-0002` 的 SWITCH 開始 partial state，不回讀 ROI、候選或影片。
7. **SWITCH metadata**：`targets` 可辨識 Pokémon；沒有 `side`。`rule_id=switch.go/sent_out` 與 trainer 只能算 contextual hint，本輪不當成明示 side。
8. **Active Pokémon**：可追蹤「某 entity 被觀察為 active」與 faint 後 inactive；不能保證任一 timestamp 的完整雙方 active set，也不猜 slot。
9. **Fainted Pokémon**：可追蹤 4 筆明示 faint；缺 side 時僅在 entity 名稱唯一時套用，否則 unresolved。
10. **Non-volatile status**：2 筆 burn 可可靠套用；沒有明示 cure event，不能假設解除。
11. **Volatile status**：守住、定身法、再來一次、滅亡計時可追蹤；沒有 end event 的 effect 不推算 duration。
12. **Stat stages**：6 events 可追蹤；`防禦、特防` 拆為兩個明示 stat，累積值 clamp 至 `[-6,+6]`。
13. **Weather**：雨開始／停止可可靠追蹤；不推算剩餘回合。
14. **Side conditions**：對手順風開始／停止可可靠追蹤；不推算 duration。
15. **Transformation**：兩組 activation／completion 可追蹤；只記錄 event 明示 actor／form，不由物種或圖示猜型態。
16. **缺 target 事件**：MOVE_RESULT critical、WEATHER、SIDE_CONDITION、FIELD_EFFECT、BATTLE_RESULT 等部分類型沒有 Pokémon target；只有其本身 metadata 足以支援的 field／battle operation 才套用。
17. **Accepted relation 的價值**：`SAME_ACTION` transformation／perish counters、`STATUS_FROM`、`STAT_CHANGE_FROM`、`RESULT_OF` 可提供 provenance 與 chain context；`TEMPORALLY_ADJACENT` 即使人工 accepted，也不自動變成因果 state operation。
18. **Rejected relations**：4 條全部從 accepted relation graph 排除，兩端 event 仍各自投影；輸出 audit 必須列出排除證據。
19. **Accepted unlinked events**：`timeline-0056`、`timeline-0063` 產生獨立 unresolved observation，不建立 parent relation。
20. **正式 turn number**：沒有 turn marker，不足以建立；state 欄位標為 `not_applicable`。
21. **HP**：沒有可信數值事件，本輪完全不重建；DAMAGE_RESULT 只作 no-op evidence。
22. **Move choice／speed order／slot**：資訊不足，全部不重建。
23. **Manifest 必須標示**：partial initial state、SWITCH side 缺失、team preview 未入事件、HP／turn／slot／speed／choice 不支援、volatile duration 不推算、accepted temporal adjacency 不等於因果、rejected relation 排除、unlinked 獨立處理。

## 4. 架構決策

1. 以 `state-0000` 作 unknown initial snapshot；70 個 groups 各產生一個 delta 與後續 snapshot，因此正式輸出預期 71 snapshots、70 deltas。
2. 每個 field 使用 `known`、`unknown`、`conflicted`、`not_applicable` knowledge state；collection 另外保存 `knowledge` 與 provenance。
3. Pokémon 先以名稱建立 entity；side 缺失時放在 `unassigned_pokemon`。日後 event 明示 side，且同名 entity 唯一時才以 `RESOLVE_ENTITY_SIDE` 遷移；有歧義就 conflict。
4. 每個 event 都由 reducer registry 處理。沒有持久 state 影響的 MOVE／DAMAGE_RESULT 是明確 no-op；缺少必要 metadata 才是 unresolved。
5. Snapshot confidence 由目前已知 facts 的 confidence 平均；completeness 由集中式、公開權重計算，兩者不互相替代。
6. 低 completeness 本身不會把每張卡都標成 needs review；只有重要 state transition、unresolved、conflict、active ambiguity、rejected relation separation 或 accepted-unlinked 才建立 review reason。
7. Review Pack 顯示 State Before → Event → Delta → State After；不讀影片、不重新 OCR／Parser／Timeline。

## 5. 已知風險

- 名稱不是全域可靠 identity；同名 Pokémon 出現在雙方或同 side 時必須停止自動合併並產生 conflict。
- 缺少 switch-out／slot 證據時，active set 只能表示 observed members，不代表完整 lineup。
- 未收到 effect end event 時，不能用時間或回合知識自動清除 volatile／side effect。
- `TEMPORALLY_ADJACENT` relation 只保留人工相關性，不能作狀態因果。
- 目前 battle result 能知道 forfeit 與 loser，但無法安全把 trainer name 映射成 player／opponent winner。
