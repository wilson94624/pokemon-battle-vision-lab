# Checkpoint 1B Human Review Pack 使用說明

Review Pack 忠實視覺化本次重掃後的 `events.json` 與 BATTLE_TEXT diagnostics。產生 Review Pack 時不重跑 detector、不改 candidate 類型或邊界、不做 OCR，也不建立 Checkpoint 1C 資料。

## 建立資料包

```bash
.venv/bin/pokemon-battle-vision build-review-pack \
  --project-root . \
  --video samples/videos/win-01.mp4 \
  --events outputs/checkpoint-1b/events.json \
  --frames outputs/checkpoint-1b/frames.jsonl \
  --diagnostics outputs/checkpoint-1b-debug/battle_text_diagnostics.jsonl \
  --checkpoint-1a-dir outputs/checkpoint-1a \
  --roi-config configs/roi_2868x1320.json \
  --output outputs/checkpoint-1b-review \
  --coverage-interval-sec 0.5
```

影格身分只使用 Checkpoint 1A 的 `ffprobe.best_effort_timestamp_time` index 與既有 ordinal mapping。產生器以一次完整順序解碼擷取 evidence，不使用 `frame_index / fps`、`CAP_PROP_POS_MSEC` 或 random seek。

## BATTLE_TEXT 代表影格

一般 event type 仍使用 start／middle／end。BATTLE_TEXT 改用：

1. `start`
2. `first_strong_positive`
3. `peak_score_structure`
4. `last_strong_positive`
5. `end`

同一 frame 同時負責多個角色時只顯示一次。主要代表影格是 proposal score 與 text-structure strength 的加權 peak，不再固定使用 middle。Duration 超過 3 秒時，review image 另加最多 10 張、約每 0.5 秒一張的 evidence strip；每張標示 PTS、score、evidence level 與 timeline decision。

## TRIGGER_NOTIFICATION 代表影格

Trigger candidate 使用：

1. `start`
2. `peak_evidence`
3. `end`

短 candidate 的角色若落在同一 frame，只顯示一次。主要代表影格取實際 side 的最高 `combined_score`，不使用固定 middle。Review image 與 contact sheet 會顯示由 frozen canonical ROI 推導的 analysis context，並列出 `side`、`panel_score`、`text_score`、`icon_score` 與 `combined_score`；canonical ROI 仍保留供人工核對，ROI config 本身沒有改動。

## 建議審查順序

1. `trigger_notification_round1_regression/`：先確認約 114 秒的能力通知由 missed 變 covered，且約 451 秒的道具通知仍 preserved。
2. `contact_sheets/TRIGGER_NOTIFICATION/`：快速檢查是否有非通知 UI、角色輪廓或場地特效 false positives。
3. `battle_text_round1_regression/`：確認 16 個空候選移除、`0006/0007/0014` boundary、`0033` split、`0021/0022` same-layout reopen merge 與 `0035` peak frame。
4. `coverage_review/`：以 0.5 秒全片概覽尋找 `NO_CANDIDATE` 中的可能漏檢。
5. `battle_text_recall_audit/`：逐 0.1 秒檢查 17 個 known windows 的 score、decision 與 ROI crop。
6. `contact_sheets/BATTLE_TEXT/` 與 individual review images：檢查剩餘空抓、重複、split／merge 與 boundary。
7. 最後在 `candidate_review.csv` 或 `candidate_review.json` 填人工欄位；不要回寫 `events.json`。

`TRIGGER_NOTIFICATION` 只呈現 candidate `visible_rois` 的實際側別。`SELECTED_FOUR` 使用 Frozen config 的正式 ROI ID `selected_four`。

## 人工欄位

`human_status`：`pending`、`correct`、`false_positive`、`wrong_type`、`needs_split`、`needs_merge`、`uncertain`。

`boundary_quality`：空字串、`good`、`starts_too_early`、`starts_too_late`、`ends_too_early`、`ends_too_late`、`both_inaccurate`、`uncertain`。

`corrected_type` 可留空或填六種現有 event type；`merge_with_candidate_id` 可留空；`split_required` 是 boolean；`notes` 是自由文字。所有欄位預設仍為 pending，工具不會自動寫人工結論。

## 輸出與索引

```text
outputs/checkpoint-1b-review/
├── review_manifest.json
├── candidate_review.csv
├── candidate_review.json
├── candidates/<TYPE>/*__review.jpg
├── contact_sheets/<TYPE>/*_contact_*.jpg
├── contact_sheets/contact_sheet_index.json
├── coverage_review/coverage_*.jpg
├── coverage_review/coverage_index.json
├── battle_text_recall_audit/battle_text_recall_*.jpg
├── battle_text_recall_audit/battle_text_recall_audit_index.json
├── battle_text_round1_regression/
│   ├── round1_mapping.json
│   ├── round1_mapping.csv
│   ├── round1_visual_index.json
│   └── round1_regression_*.jpg
├── trigger_notification_round1_regression/
│   ├── round1_mapping.json
│   ├── round1_mapping.csv
│   ├── round1_visual_index.json
│   └── trigger_notification_round1_*.jpg
└── battle_text_recall_summary.json
```

Candidate contact sheet 每頁 12 個 tiles；coverage 與 dense audit 每頁 16 個 tiles；round-1 regression 每頁 4 個舊／新比較 tiles。所有 index 都保存 page、tile、candidate 或 PTS 對應。`recall_gate` 固定為 `pending_human_review`，不會自動宣稱全片 recall 完成。
