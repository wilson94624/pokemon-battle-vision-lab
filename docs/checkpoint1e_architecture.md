# Checkpoint 1E Battle Timeline Builder

Checkpoint 1E 將 frozen Checkpoint 1D BattleEvent 投影成 Action Group（行動群組）、Event Chain（事件鏈）與 Ordered Battle Timeline（依序時間線）。它不重新解析文字、不讀影片、不建立 Battle State，也不推算正式 turn。

修改前的 102-event 資料稽核、時間間隔分布與開源方案比較見 [`checkpoint1e_architecture_audit.md`](checkpoint1e_architecture_audit.md)。

## 資料流

```text
outputs/checkpoint-1d/battle_events.json (frozen)
        ↓ schema / manifest / hash / monotonic validation
Correlation Rule Registry
        ↓
Action Groups + Relation Edges
        ↓
outputs/checkpoint-1e/
        ↓
text-based Human Review cards / contact sheets
        ↓
outputs/checkpoint-1e-review/
```

## Conservative Correlation

Builder 明確區分三種情況：

1. 結構證據足夠：metadata actor／target／move／effect 等符合，且沒有 intervening major action，建立 `auto_accepted` relation 並收入同一 group。
2. 只有時間與 event type 相容：建立跨 group 的 `TEMPORALLY_ADJACENT` relation，標記 `needs_review`，不宣稱因果。
3. metadata 衝突或沒有可靠候選：不建立 relation；不能獨立解釋的 consequence 明確標為 `unlinked`。

時間 gap 只負責限制搜尋窗口。它不會單獨把事件合併。

## Timeline Data Model

每個 group 至少包含：

```json
{
  "timeline_id": "timeline-0001",
  "sequence": 1,
  "start_time": 0.0,
  "end_time": 0.0,
  "primary_event_id": "battle-event-0001",
  "event_ids": [],
  "relation_edge_ids": [],
  "group_type": "ACTION_CHAIN",
  "confidence": 0.0,
  "review_status": "auto_accepted",
  "review_reasons": [],
  "source_event_count": 0,
  "human_review": {}
}
```

`group_type`：

- `ACTION_CHAIN`：不同類型但有高信心關聯的事件鏈。
- `EVENT_BATCH`：同一 residual／counter 對多個 target 的 sibling events。
- `STANDALONE_ACTION`：獨立 MOVE、SWITCH、TRANSFORMATION 或 BATTLE_RESULT。
- `STANDALONE_EVENT`：可獨立解釋的 condition、residual 或 effect lifecycle。
- `UNLINKED_EVENT`：需要來源但無法可靠關聯的事件。

Group 依 `start_time` 排序；group 內 `event_ids` 永遠保持 Checkpoint 1D source order。

## Relation Data Model

```json
{
  "relation_id": "relation-0001",
  "from_event_id": "battle-event-0001",
  "to_event_id": "battle-event-0002",
  "relation_type": "RESULT_OF",
  "rule_id": "move.explicit_target_result",
  "confidence": 0.95,
  "evidence": ["time_gap_sec=0.500000", "metadata_match:target=A"],
  "review_status": "auto_accepted",
  "group_id": "timeline-0001",
  "human_review": {}
}
```

最小 relation taxonomy：

- `RESULT_OF`
- `DAMAGE_FROM`
- `STATUS_FROM`
- `STAT_CHANGE_FROM`
- `TRIGGERED_BY`
- `FOLLOWED_BY`
- `SAME_ACTION`
- `TEMPORALLY_ADJACENT`

沒有新增泛化但語意不明的 `UNKNOWN_RELATION`；無法關聯時直接保持 unlinked。

## Major Action

`MOVE`、`SWITCH`、`TRANSFORMATION`、`BATTLE_RESULT` 是 chain barrier：新事件出現後，舊 major action 不得跨越它繼續吸附 consequence。`ABILITY`、`ITEM`、`WEATHER`、`SIDE_CONDITION`、`FIELD_EFFECT` 可獨立，也可在 metadata 足夠時附著，不固定當作 primary。

## Rule Registry

每條 `CorrelationRule` 都明確定義：

- `rule_id`
- source／target event types
- `maximum_time_gap_sec`
- required／optional metadata matches
- relation type
- base confidence
- stop-on-major policy
- ambiguity behavior

高信心規則包括 transformation phase、switch target→ability actor、move target→result／damage／status／stat、move name→effect、recoil、item self-damage、damage→faint、multi-target residual batches，以及 battle result sequence。

時間型規則只能產生 `needs_review`。若雙方都提供同一 metadata 欄位但內容不同，規則立即拒絕，不會用時間覆蓋 conflict。

## Confidence Policy

```text
relation confidence
= 80% structural rule confidence
+ 20% min(source event confidence, target event confidence)
+ optional metadata match bonus（上限 0.99）
```

- `confidence >= 0.85` 且 rule behavior 為 `link`：`auto_accepted`，可合併 group。
- temporal-only rule：固定 `needs_review`，即使 event confidence 很高也不合併。
- metadata conflict：拒絕 relation。

## Multi-target

一個 MOVE 可以作為多個 sibling edges 的共同 parent；每個 DAMAGE_RESULT／STATUS／FAINT 保持自己的 event ID 與 target。Group 內不把 target A 的 consequence 當成 target B 的 parent。每個 source event 恰好出現在一個 group，但可以參與多條 relation edge。

## Human Review

`outputs/checkpoint-1e-review/` 包含：

- `group_reviews.json`
- `needs_review_relations.json`
- `unlinked_events.json`
- `review_manifest.json`
- `cards/groups/`
- `cards/relations/`
- `contact_sheets/groups/`
- `contact_sheets/needs_review/`
- `contact_sheets/contact_sheet_index.json`

Review cards 是由 1D JSON 產生的文字證據卡，不讀影片。每張 group card 顯示 source BattleEvents、PTS、raw text、metadata、primary event、relations、confidence 與 review reasons。每條 `needs_review` relation 另有 from／to 對照卡。

人工欄位預設全部為 `null`：

- `human_action`
- `human_decision`
- `human_relation_type`
- `human_primary_event_id`
- `human_group_id`
- `reviewed_by`
- `reviewed_at`
- `review_note`

## CLI

```bash
.venv/bin/pokemon-battle-vision checkpoint-1e \
  --project-root . \
  --events outputs/checkpoint-1d/battle_events.json \
  --output outputs/checkpoint-1e \
  --review-output outputs/checkpoint-1e-review
```

CLI 驗證 1D schema、manifest、events hash、event count 與 timestamp ordering；不重新執行 Parser、OCR 或 scanner。兩個 output 皆使用非點號 staging 與 transactional replacement，失敗時保留上一版。

## Scope Boundary

Checkpoint 1E 明確不輸出 `turn`，不重建 HP、active Pokémon、speed order、move target 或勝負原因；也不做 Rule Checker、Replay Analysis、Tactical Analysis、GUI 或 Checkpoint 1F。
