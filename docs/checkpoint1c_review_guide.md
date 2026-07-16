# Checkpoint 1C OCR 與人工審查指南

Checkpoint 1C 第一階段只處理 frozen Checkpoint 1B 中的 `176` 個 `BATTLE_TEXT` 與 `2` 個 `TRIGGER_NOTIFICATION` candidates。每一筆原 candidate 都會保留；`rejected` 只是建議 workflow，不會刪除資料或改寫 `events.json`。

## 執行方式

```bash
.venv/bin/pokemon-battle-vision checkpoint-1c \
  --project-root . \
  --video samples/videos/win-01.mp4 \
  --checkpoint-1b-dir outputs/checkpoint-1b \
  --checkpoint-1b-review-dir outputs/checkpoint-1b-review \
  --output outputs/checkpoint-1c \
  --review-output outputs/checkpoint-1c-review
```

macOS 需具備 Xcode Command Line Tools。流程會以 `xcrun clang` 編譯 repository 內的小型 Apple Vision adapter，固定使用 `VNRecognizeTextRequestRevision3`、`accurate` 與 `zh-Hant`；不下載模型，也不呼叫網路服務。

## 輸出與欄位

`outputs/checkpoint-1c/` 保存 frame selections、全部 preprocessing variant、raw OCR JSONL、multi-frame aggregate、Text Validation、duplicate hints、事後人工 fixture evaluation 與 manifest。`outputs/checkpoint-1c-review/` 保存 `178` 張 candidate cards、分類 contact sheets 與可編輯的人工欄位。

Validation labels：

- `VALID_TEXT`：多影格／多 variant 有足夠一致的繁中文字 evidence。
- `NO_TEXT`：多影格／多 variant 一致顯示空白、非中文字雜訊、特效或不合理的中央 UI 結構。
- `UNCERTAIN`：OCR 不一致、信心不足、只讀到部分文字或 engine error；必須人工查看。

Workflow statuses：

- `auto_accepted`：高信心 `VALID_TEXT`，仍可抽查。
- `needs_review`：所有 `UNCERTAIN`、低信心有效文字及 possible duplicates。
- `rejected`：高信心 `NO_TEXT`；原始 evidence 仍完整保留。

人工欄位初始均為 `null`。`human_action` 允許 `accept`、`edit_text`、`mark_no_text`、`merge_previous`、`merge_next`、`split`；需要合併時填 `merge_with_event_id`，需要拆分時填 `split_points`，另可填 `human_text`、`human_decision`、`reviewed_at` 與 `reviewed_by`。

## 建議審查順序

1. 先看 `needs_review/TRIGGER_NOTIFICATION`，確認特性與道具通知兩行文字。
2. 看 `needs_review/BATTLE_TEXT`，優先處理 OCR disagreement、低 confidence 與 possible duplicate。
3. 看 `rejected/BATTLE_TEXT`，抽查水花、Mega 周邊 UI 等 `NO_TEXT` 是否誤拒真文字。
4. 抽查 `auto_accepted/BATTLE_TEXT` 的短訊息、淡入淡出與兩行文字。
5. 最後依 duplicate groups 判斷是否需要人工 merge；工具不會自動合併。

`references/checkpoint1c_initial_evaluation.json` 僅供 OCR 與 validation 完成後產生回歸報告。正式 frame selection、OCR、aggregation 與 validation 不會讀取或套用其中的 candidate IDs、timestamps 或人工答案。
