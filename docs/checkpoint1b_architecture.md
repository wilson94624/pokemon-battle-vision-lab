# Checkpoint 1B Architecture

Checkpoint 1B 在 Checkpoint 1A Frozen Baseline 之上建立固定 10 Hz 的全片候選事件掃描器。它不修改 ROI、不做 OCR，也不解讀文字內容。

## Gate 與資料流

```text
roi_approval.json + roi_overlay_manifest.json + roi_2868x1320.json
  → read-only SHA-256 Frozen Baseline gate
  → load ffprobe best_effort_timestamp_time index from Checkpoint 1A
  → build exact 0.1-second target schedule
  → nearest authoritative PTS frame selection
  → OpenCV sequential full-video decode + explicit rotation
  → approved ROI appearance signatures
  → per-frame candidate scores / ui_state / visible_rois
  → temporal gap bridging + minimum-duration filtering
  → frames.jsonl + events.json
```

`scanner.py` 必定順序讀完整影片，不能 random seek、不能用 `frame_index / nominal fps` 推導時間，也沒有 adaptive sampling 選項。原始 frame ordinal 只和 Checkpoint 1A 的 ffprobe PTS index 一對一對應。

## 模組責任

- `checkpoint1b_models.py`：10 Hz sample、frame metadata 與 event candidate 的序列化模型。
- `candidate_detection.py`：核准 ROI crop 的 classical appearance signatures、template similarity 與 frame-level UI candidates。
- `timeline.py`：短 gap bridging、最短 sample 數與候選事件 start/end/duration 聚合。
- `scanner.py`：Frozen Baseline gate、PTS sample plan、完整順序解碼、JSONL／JSON outputs。
- `cli.py`：`checkpoint-1b` command；sampling rate 固定在程式契約中，沒有可調整 CLI flag。

## 非目標

Checkpoint 1B 不包含 OCR、文字分類、招式辨識、特性／道具名稱解析、LLM Vision、Battle Parser 或戰術推論。`events.json` 只包含 candidates；後續 consumer 必須保留 confidence 與原始時間邊界。

## 後續擴充邊界

Checkpoint 1C 可讀取 `frames.jsonl` 與 `events.json`，針對候選區段加入 OCR、Event Parser 及 Battle Parser。1C 不應改寫 1B 的 10 Hz sample schedule、frame identity 或 Frozen ROI hash。
