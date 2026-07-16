# Checkpoint 1F Battle State Reconstruction

Checkpoint 1F 是 frozen Checkpoint 1D／1E 之上的保守 state projector。輸入只有 102 個 BattleEvents、70 個已審查 Timeline Groups 與 50 條 Relations；不讀影片、不執行 OCR、不重跑 parser 或 Timeline Builder。

## 輸入契約與安全邊界

CLI 在投影前驗證 1D／1E schemas、manifests、檔案 SHA-256 與 1E 人工審查完成狀態。32 條 auto-accepted relations 與 14 條人工 accepted relations 可作 provenance；4 條人工 rejected relations 明確從關聯圖排除。`timeline-0056`、`timeline-0063` 只保留為 accepted-unlinked observation，不建立推測 parent。

輸出採 paired transactional replacement：主輸出與 Review Pack 都先在非點號 staging directory 完整建立、驗證，成功後一起替換；任何一側失敗都保留上一版正式輸出。成功後驗證沒有 BSD `hidden` flag、`.DS_Store`、暫存、backup 或名稱帶 ` 2` 的衝突目錄。

## Snapshot 與 knowledge model

`state-0000` 是全 unknown 的初始 snapshot。70 個 Timeline Groups 各產生一個 delta 與一個後續 immutable snapshot，因此共有 70 deltas、71 snapshots。每個 snapshot 指向 `previous_snapshot_id`，且保存 source timeline／event IDs、confidence、completeness、unknown fields、conflicts、unresolved updates 與獨立人工審查欄位。

每個 state field 使用以下 knowledge state：

- `known`：由明示 event metadata 支援。
- `unknown`：輸入沒有足夠 evidence。
- `conflicted`：已知值與新 evidence 衝突，另產生 conflict record。
- `not_applicable`：1F 明確不支援，例如 exact HP、official turn、move choice、speed order、active slot。

Pokémon 以可追溯 entity ID 表示。缺少 side 的 SWITCH 先登記到 `battle.unassigned_pokemon`；之後只有在名稱唯一且 side 明示時才以 `RESOLVE_ENTITY_SIDE` 移入 `player_side` 或 `opponent_side`。歧義不會被猜測或覆寫。

## Reducer registry 與 state operations

`ReducerRegistry` 以 `event_type` 分派小型 reducer；每個 `ReducerSpec` 集中描述 required／optional metadata、operation policy、confidence policy、conflict policy 與 unknown policy。正式 operations 包括：

- entity／active：`REGISTER_POKEMON`、`RESOLVE_ENTITY_SIDE`、`SET_ACTIVE`、`SET_INACTIVE`、`MARK_FAINTED`
- Pokémon state：`SET_STATUS`、`CLEAR_STATUS`、`ADD_VOLATILE`、`REMOVE_VOLATILE`、`CHANGE_STAT_STAGE`
- field／side：`SET_WEATHER`、`CLEAR_WEATHER`、`ADD_SIDE_CONDITION`、`REMOVE_SIDE_CONDITION`、`ADD_FIELD_EFFECT`
- observation：`SET_TRANSFORMATION`、`SET_KNOWN_ABILITY`、`SET_KNOWN_ITEM`、`SET_BATTLE_RESULT`

Stat stage 只累積 event 明示的變化並 clamp 到 `[-6,+6]`；初始 absolute stage 仍是 unknown。沒有持久 state 影響的 MOVE、MOVE_RESULT、DAMAGE_RESULT 會產生明確 no-op reason。必要 metadata 不足、accepted-unlinked 或不支援 event 才進入 unresolved。互斥 fact 出現不同已知值時產生 conflict record，原值不會被靜默覆寫。

## Confidence、completeness 與人工審查

Operation confidence 由 source event confidence 乘集中式 rule factor；snapshot confidence 是目前已知 facts 的平均。Completeness 使用公開權重衡量「目前可觀察且已知的 state 範圍」，與 confidence 分開；低 completeness 不代表已知 fact 不可信。

Review Pack 對每個 group 顯示 State Before → Event → Delta → State After，並列出 operations、no-op／unresolved／conflict、review reasons、confidence 與 completeness。以下情況會進入 `needs_review`：

- unresolved 或 conflict；
- accepted-unlinked observation；
- rejected relation 的兩端必須保持分離；
- completeness 偏低時的重要 state transition；
- active/entity identity 仍有歧義。

每張卡與 contact sheet index 都可回溯至 timeline ID、delta ID、snapshot IDs 與 source event IDs。所有人工欄位預設 `null`，generator 不替人工做結論。

正式人工審查已於 2026-07-17 完成：46 張 generator 標記為 `needs_review` 的 cards 全部接受，remaining needs-review 為 0。人工結論只寫入 `state_review_records.json`、相關 review indexes、`review_summary.json`、`review_statistics.json` 與 manifest completion metadata；既有 snapshots、deltas、Timeline、BattleEvents 與 rejected relations 均未改寫。`timeline-0018` 明確記錄唱反調使近身戰的防禦／特防下降反轉為提高；`timeline-0056`、`timeline-0063` 保持 unresolved／accepted-unlinked，不新增 parent relation。

## 正式輸出

`outputs/checkpoint-1f/`：

- `battle_state_snapshots.json`
- `state_deltas.json`
- `state_conflicts.json`
- `state_audit.json`
- `checkpoint1f_manifest.json`

`outputs/checkpoint-1f-review/`：

- `review_manifest.json`
- `state_review_records.json`
- `cards/`
- `contact_sheets/`
- `indexes/`

## 已知限制

1F 不能可靠重建精確／百分比 HP、完整 roster、PP、move set、EV／IV、未明示 item／ability、正式 turn、slot、speed order、move choice 或完整 active lineup。缺少 end event 的 volatile／field effect 不會按遊戲規則自動消失；純時間相鄰 relation 不會被提升為因果。這些 unknown 是輸入 evidence 的限制，不由 simulator、damage calculator 或遊戲資料庫補齊。
