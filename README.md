# Pokémon Battle Vision Lab

本 repository 是本地端 Python Computer Vision 研究型 prototype。目前實作 **Milestone 1 — Checkpoint 1A 至 1D.1**：1A 建立並核准 ROI Frozen Baseline；1B 建立 UI event candidates；1C 執行本機 Apple Vision OCR、Text Validation 與 Human Review；1D／1D.1 將已接受文字轉成經 quality audit 的 BattleEvent 中介格式。

本專案不是網站、即時助手或戰術分析工具。Checkpoint 1D 只建立 BattleEvent IR，不建立回合、Battle State、Replay Analysis 或戰術語意。

## 唯一支援 profile

- macOS
- Python `3.9.6`
- Pokémon Champions 雙打、繁體中文 UI、固定 UI 縮放
- rotation 後 display resolution：`2868×1320`
- 已測試 FFmpeg／ffprobe：`8.1.2`

不同 FFmpeg 版本若 capability probe 通過會以 warning 繼續；不同 display resolution 則會寫出 `metadata.json` 與 `compatibility_report.json` 後以非零 exit code 停止，不會 resize，也不會繼續 PTS、ROI 或 anchors。

## 安裝

先確認外部 dependency：

```bash
brew install ffmpeg
ffmpeg -version
ffprobe -version
```

建立隔離環境並安裝固定版本：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

`pyproject.toml` 固定 Python 與套件版本；目前不宣稱支援其他 Python 版本。

## 執行 Checkpoint 1A

輸出目錄必須不存在或為空，避免新舊證據與 hashes 混用：

```bash
.venv/bin/pokemon-battle-vision checkpoint-1a \
  --project-root . \
  --video samples/videos/win-01.mp4 \
  --known-frames references/win01_known_frames.json \
  --match-reference references/win01_match_reference.json \
  --screenshots-dir samples/screenshots \
  --roi-config configs/roi_2868x1320.json \
  --output outputs/checkpoint-1a \
  --interval-sec 30
```

時間權威只有 ffprobe 的 `best_effort_timestamp_time`。流程不使用 `frame_index / fps`、`CAP_PROP_POS_MSEC` 或 random seek。OpenCV 只負責 pixels，且 `CAP_PROP_ORIENTATION_AUTO` 會關閉，再依 ffprobe rotation 明確旋轉。

六張 reference screenshots 只在各自 tolerance window 內協助挑選穩定且清楚的 anchor 代表幀；不參與完整分析範圍、segmentation、threshold tuning 或 state detection。

主要輸出：

```text
outputs/checkpoint-1a/
├── environment_report.json
├── metadata.json
├── compatibility_report.json
├── input_image_report.json
├── frame_timestamps.npz
├── pts_validation_report.json
├── decode_alignment_report.json
├── anchor_report.json
├── anchors/*.png
├── contact_frames/*.png
├── contact_sheets/*.jpg
├── contact_sheet_index.json
├── roi_pixel_conversion.json
├── roi_overlays/*.png
├── roi_overlay_manifest.json
└── checkpoint_1a_report.json
```

所有 `.jpeg` sample 實際為 PNG；工具會依 magic bytes 正確讀取，並在 `input_image_report.json` 記錄 `INPUT_FORMAT_MISMATCH`，不會因錯誤副檔名停止。新產物會在寫入後重新驗證 magic bytes。

## ROI 人工核准 gate

`configs/roi_2868x1320.json` 是由 design-reference screenshots 推導的 normalized 初稿，不是 ground truth。主流程把 ROI 映射到 rotation 後 raw video anchors，產生六張 anchor overlays，另有一張 trigger notification 正例 overlay；核准集合共七張。產生流程仍會先以 `pending_human_approval` 停止，只有獨立 approval command 能建立核准紀錄。

請人工逐張檢查：

- ROI 不含 screenshot 黑邊與人工彩框。
- team preview、selected four、雙方 status、move menu、battle text、result 都完整覆蓋。
- `player_team_details.jpeg` 只作外部 provenance／未來 OCR 樣本，不屬於影片 timeline，也沒有影片 ROI。

只有人工確認後，才可另行明確執行：

```bash
.venv/bin/pokemon-battle-vision approve-roi \
  --video samples/videos/win-01.mp4 \
  --roi-config configs/roi_2868x1320.json \
  --overlay-manifest outputs/checkpoint-1a/roi_overlay_manifest.json \
  --approved-by '人工核准者名稱' \
  --output outputs/checkpoint-1a/roi_approval.json
```

approval 會重新驗證 video、ROI config、manifest 及每張 overlay 的 SHA-256。任一內容改變都必須重產 overlays 並重新核准。

Checkpoint 1B 啟動前會 read-only 重驗 video、ROI config、manifest、全部 overlays 與 approval hashes；任一 Frozen Baseline 證據改變都會拒絕掃描。

## 執行 Checkpoint 1B

Checkpoint 1B 的 sampling rate 固定為 `10 Hz`，CLI 沒有 adaptive sampling 或 rate override。`BATTLE_TEXT` 採 template similarity 加文字結構的高召回 proposal：

```bash
.venv/bin/pokemon-battle-vision checkpoint-1b \
  --project-root . \
  --video samples/videos/win-01.mp4 \
  --roi-config configs/roi_2868x1320.json \
  --checkpoint-1a-dir outputs/checkpoint-1a \
  --roi-approval outputs/checkpoint-1a/roi_approval.json \
  --output outputs/checkpoint-1b \
  --debug-output outputs/checkpoint-1b-debug
```

Scanner 會順序讀完整影片，以 Checkpoint 1A 的 `ffprobe.best_effort_timestamp_time` index 為時間權威，為每個 0.1 秒 target 選最近的實際 frame。主要輸出：

```text
outputs/checkpoint-1b/
├── frames.jsonl
├── events.json
├── detector_report.json
└── checkpoint_1b_report.json

outputs/checkpoint-1b-debug/
├── battle_text_diagnostics.jsonl
├── battle_text_detector_report.json
├── trigger_notification_diagnostics.jsonl
└── trigger_notification_audit_report.json
```

`frames.jsonl` 每列另保存 BATTLE_TEXT template、水平文字列、connected components 與 layout fingerprint evidence。Diagnostics 對全部 10 Hz samples 記錄 strong／weak／negative、candidate active before／after、open／continue／bridge／close／split decision 與原因。完整設計與 round-1 根因稽核請見 [`docs/checkpoint1b_battle_text_recall.md`](docs/checkpoint1b_battle_text_recall.md)。

TRIGGER_NOTIFICATION 不再只依賴固定大小的道具通知 template。Detector 仍保留 frozen canonical ROI，但會由該 ROI 向上推導 side-specific analysis context，分開計算半透明 panel、兩行白字排列與 optional icon evidence；player／opponent 使用獨立 timeline state。`references/trigger_notification_human_review_round1.json` 只供測試與 audit，production detector 不會讀取 Pokémon 名稱、通知名稱或人工 timestamps。

Checkpoint 1B、debug 與 Review Pack 都採 transactional output replacement：新結果先在同層、非點號開頭的 `*.tmp-<UUID>` staging 完整建立與驗證，成功才原子替換舊目錄；失敗保留上一版。macOS 會在 commit 前後遞迴清除並驗證 BSD hidden flag，成功後不留下 tmp、backup 或空白的 ` 2` 衝突目錄。

## 建立 Checkpoint 1B Human Review Pack

Review Pack 忠實呈現既有 `events.json`，不會重跑 detector、重新分類、調整邊界或修改 Checkpoint 1B inputs：

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

一般 candidate 使用 start／middle／end；BATTLE_TEXT 額外使用 first strong、peak score＋structure、last strong，超過 3 秒時另顯示約每 0.5 秒一張的 evidence strip。TRIGGER_NOTIFICATION 使用 start、peak evidence、end，並同時顯示 canonical ROI 與實際 analysis context 的 panel／text／icon／combined scores。一般 coverage 固定為 0.5 秒，dense recall audit 使用既有 10 Hz diagnostics；`battle_text_round1_regression/` 與 `trigger_notification_round1_regression/` 分別提供人工案例的新舊 mapping。完整人工欄位、索引與建議流程請見 [`docs/checkpoint1b_review_guide.md`](docs/checkpoint1b_review_guide.md)。

## 執行 Checkpoint 1C 第一階段

Checkpoint 1C 不重跑 detector，也不修改 Checkpoint 1B candidates。它從 PTS／ordinal 對應選取每筆 candidate 的 `2–7` 張 evidence frame，產生四種有限 preprocessing variants，再以 macOS Apple Vision `VNRecognizeTextRequest`（`zh-Hant`）執行完全本機 OCR：

```bash
.venv/bin/pokemon-battle-vision checkpoint-1c \
  --project-root . \
  --video samples/videos/win-01.mp4 \
  --checkpoint-1b-dir outputs/checkpoint-1b \
  --checkpoint-1b-review-dir outputs/checkpoint-1b-review \
  --output outputs/checkpoint-1c \
  --review-output outputs/checkpoint-1c-review
```

執行環境另需 Xcode Command Line Tools 的 `xcrun clang`；adapter 直接連結系統 Vision framework，不下載模型或呼叫雲端。輸出會保留 `178` 個 candidates、全部 raw OCR evidence、multi-frame aggregate、`VALID_TEXT/NO_TEXT/UNCERTAIN`、`auto_accepted/needs_review/rejected`、possible duplicate hints 與人工欄位。完整欄位、Review Pack 結構與審查順序請見 [`docs/checkpoint1c_review_guide.md`](docs/checkpoint1c_review_guide.md)，架構與 engine 選型請見 [`docs/checkpoint1c_architecture.md`](docs/checkpoint1c_architecture.md)。

## 執行 Checkpoint 1D MVP

Checkpoint 1D 只讀取完成審查的 1C review JSON，不需要影片，也不會重新執行 OCR：

```bash
.venv/bin/pokemon-battle-vision checkpoint-1d \
  --project-root . \
  --review outputs/checkpoint-1c-review/checkpoint1c_review.json \
  --output outputs/checkpoint-1d
```

輸出為 `battle_events.json` 與 `checkpoint1d_manifest.json`。架構、schema、接受規則與 `UNKNOWN_EVENT` 策略請見 [`docs/checkpoint1d_architecture.md`](docs/checkpoint1d_architecture.md)；1D.1 taxonomy audit 與工程 ROI 決策見 [`docs/checkpoint1d1_quality_audit.md`](docs/checkpoint1d1_quality_audit.md)。

## 測試

快速單元與整合測試：

```bash
.venv/bin/python -m pytest -m 'not slow'
```

實際 `win-01.mp4` 全片順序解碼、10 Hz Checkpoint 1B 與當前新版 candidates Review Pack integration tests：

```bash
.venv/bin/python -m pytest -m slow -s
```

slow tests 會使用安全的暫存 project `outputs/`：1A 驗證既有 Frozen Gate；1B 驗證 25,873 個來源 frames、5,918 個 10 Hz records、逐 sample diagnostics、17 個 regression windows、round-1 人工案例及其他 event type 固定數量；1B Review Pack 驗證一一對應、peak evidence、0.5 秒 coverage、dense audit 與 transaction cleanup；1C 以真實影片驗證單次順序解碼、178 candidates、本機繁中 OCR、schemas、Review Pack、frozen hashes 與 macOS visibility。
