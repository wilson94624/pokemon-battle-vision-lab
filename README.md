# Pokémon Battle Vision Lab

本 repository 是本地端 Python Computer Vision 研究型 prototype。目前只實作 **Milestone 1 — Checkpoint 1A**：環境與媒體 preflight、ffprobe metadata／PTS、OpenCV 順序解碼 baseline、known anchors、固定間隔 contact sheets，以及 raw-video ROI calibration overlays。

本專案不是網站、即時助手、完整 OCR、Battle Parser 或戰術分析工具。Checkpoint 1B、10 Hz feature analysis、segmentation、annotation draft 與 OCR 都不在目前範圍。

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

`configs/roi_2868x1320.json` 是由 design-reference screenshots 推導的 normalized 初稿，不是 ground truth。主流程把 ROI 映射到 rotation 後 raw video anchors，產生六張 overlays，然後以 `pending_human_approval` 停止。

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

**本次交付不會執行上述 approval command，也不會開始 Checkpoint 1B。**

## 測試

快速單元與整合測試：

```bash
.venv/bin/python -m pytest -m 'not slow'
```

實際 `win-01.mp4` 全片順序解碼 smoke test：

```bash
.venv/bin/python -m pytest -m slow -s
```

slow test 會使用暫存輸出目錄，驗證完整 frame count 對齊、六個 anchors、contact sheets、raw-video overlays 及 ROI gate 仍為 pending；不產生任何 Checkpoint 1B 產物。
