# Milestone 1 — Checkpoint 1A 實作報告

## 結論

Checkpoint 1A 已完成並停在 ROI 人工核准 gate。正式輸出位於 `outputs/checkpoint-1a/`，狀態為 `complete_pending_roi_approval`；`roi_approval.json` 不存在，`outputs/checkpoint-1b/` 不存在，也沒有 `analysis_samples.npz`、change analysis、segmentation、annotation draft 或 OCR 產物。

ROI overlays 已由程式產生並經實作端視覺 QA，仍必須由使用者人工核准；本次沒有代替使用者執行 `approve-roi`。

## 實際環境與媒體結果

| 項目 | 結果 |
|---|---|
| Python | `3.9.6` |
| FFmpeg／ffprobe | `8.1.2`／`8.1.2`，capability probe 通過 |
| NumPy | `2.0.2` |
| opencv-python-headless distribution | `4.13.0.92`（`cv2.__version__ == 4.13.0`） |
| jsonschema | `4.23.0` |
| codec | HEVC |
| encoded dimensions | `1320×2868` |
| ffprobe raw rotation | counter-clockwise `90°` |
| internal clockwise canonical rotation | `270°` |
| display dimensions | `2868×1320`，profile gate 通過 |
| OpenCV decoded dimensions | `1320×2868`，autorotation 已關閉 |
| OpenCV 手動 rotation 後 dimensions | `2868×1320` |
| OpenCV backend | `FFMPEG` |
| ffprobe reported／PTS／decoded frames | `25,873 / 25,873 / 25,873` |
| PTS validation | 0 missing、0 duplicate、0 non-monotonic |
| VFR | `true`；median delta 約 `0.016667s`、max `0.1s` |
| 圖片格式 | 7 張 `.jpeg` 全部由 magic bytes 判定為 PNG，均記錄 `INPUT_FORMAT_MISMATCH` |

ffprobe frame ordinal 與 OpenCV sequential decoded ordinal 一對一通過；`CAP_PROP_POS_FRAMES` 沒有順序 mismatch。時間查詢與固定間隔抽幀只使用 `best_effort_timestamp_time`。

## 六個 validation anchors

anchor selection 只在每個人工 tolerance window 內運作，使用 reference-image 灰階差異、相鄰影格 motion、Laplacian clarity 與小幅 PTS 距離 tie-break。reference 不參與 segmentation、threshold tuning、state detection 或完整分析範圍。

| Anchor | Target | 實際 PTS | 差值 | 結果 |
|---|---:|---:|---:|---|
| `kf_team_preview` | 15.000s | 15.168333s | +0.168333s | pass |
| `kf_selected_four` | 39.000s | 39.036667s | +0.036667s | pass |
| `kf_battle_text` | 79.000s | 78.940000s | −0.060000s | pass |
| `kf_move_selection_player_right` | 493.000s | 493.078333s | +0.078333s | pass |
| `kf_move_selection_player_left` | 533.000s | 533.031667s | +0.031667s | pass |
| `kf_result` | 588.000s | 589.971667s | +1.971667s | pass（含清楚 WIN／LOSE） |

## 產物與視覺驗收

- `frame_timestamps.npz`：25,873 筆 ordinal／PTS／duration／key-frame 與 authority metadata。
- `anchors/*.png`：6 張，皆為 rotation 後 raw video `2868×1320` PNG。
- `contact_frames/*.png`：從第一個 PTS 起每 30 秒取最近影格，共 20 張。
- `contact_sheets/*.jpg`：2 頁；每格顯示 PTS 與 ordinal，`contact_sheet_index.json` 可反查 frame path。
- `roi_overlays/*.png`：6 張 raw-video overlays；team preview 拆成 player/opponent 兩個不相連 ROI，其餘為 selected four、player/opponent status、move menu、battle text、result。
- `roi_overlay_manifest.json`：逐張記錄 source ordinal／PTS、ROI IDs、pixel coordinates 與 SHA-256，狀態 `pending_human_approval`。
- `input_image_report.json`：驗證 `player_team_details.jpeg` 可讀、實際 PNG、與 match reference provenance 相連；明確標記不屬於影片 timeline 且沒有影片 ROI。

實作過程的第一輪視覺 QA 發現 ffprobe Display Matrix 正 rotation 語意被錯當成 clockwise，雖然寬高仍能通過，但 raw frames 會上下顛倒。最終實作保留 raw counter-clockwise 值，轉成 clockwise canonical `270°` 後再呼叫 OpenCV；修正後六張 overlays 與 contact sheets 均為正向。這證明 rotation gate 不能只檢查尺寸。

第二輪視覺 QA 發現 result 的 target-nearest frame 尚未出現 `WIN/LOSE`。實作因此在 tolerance window 內加入 reference difference，最終挑到 `589.971667s` 的清楚結算幀；沒有擴張 window，也沒有把 anchor 用於後續分析。

## 測試摘要

- 快速 suite：`26 passed, 1 deselected`。
- 實際影片 slow smoke：`1 passed, 26 deselected`，完整執行 preflight、PTS、全片 sequential decode、anchors、contact sheets、overlays 與 pending approval gate。
- 單元測試涵蓋 dependency missing／版本 warning／timeout／malformed JSON、metadata／rotation、PTS missing／duplicate／non-monotonic／VFR／nearest tie、magic bytes、suffix mismatch、ROI conversion／overlay／approval hashes、contact tile traceability、annotation schema、manual rotation、autorotation disable 與 frame-count mismatch diagnosis。

## 修改檔案與模組責任

- `pyproject.toml`、`.gitignore`、`README.md`：固定環境、CLI、輸出與人工 gate 操作說明。
- `configs/roi_2868x1320.json`：normalized ROI 初稿與 design-reference derivation。
- `schemas/annotation.schema.json`、`annotations.py`：原 acceptance 所需的人工 schema contract；1A 不產生 draft。
- `media_probe.py`：外部依賴、ffprobe metadata、rotation、PTS、VFR 與 `.npz`。
- `video.py`：順序 reader、manual rotation、ordinal alignment、anchor candidate selection。
- `image_io.py`：magic-byte input 與寫後格式驗證。
- `sampling.py`、`contact_sheet.py`：PTS-based 固定間隔與分頁 contact sheets。
- `roi.py`：normalized conversion、raw overlays、hash-bound approval command。
- `pipeline.py`、`cli.py`：failure gates、產物 orchestration 與 CLI exit codes。
- `tests/unit/`、`tests/integration/`：快速與實際影片 slow smoke tests。

## 最值得親自閱讀的程式區塊

1. `media_probe.py` 的 `normalize_rotation`、`parse_metadata_payload`、`parse_frame_timestamp_payload`：時間與 rotation authority 的核心。
2. `video.py` 的 `decode_and_extract`：autorotation disable、single sequential decode、ordinal diagnostics 與只在需要時 rotation。
3. `video.py` 的 `_AnchorSelector`：known references 僅限 tolerance-window validation 的隔離邊界。
4. `roi.py` 的 `normalized_to_pixel`、`create_roi_approval`：座標 rounding 與 hashes 失效規則。
5. `pipeline.py` 的 `run_checkpoint_1a`：display、PTS、alignment、overlay 與 pending approval 的停止順序。

## 殘餘風險與停止點

- ROI 仍是單一 `2868×1320`、繁體中文、固定 UI scaling profile 的初稿，最終正確性需要人工核准。
- HEVC 全片 decode 與 ffprobe `-show_frames` 有固定成本；1A 會保存 PTS index，但為避免證據混用，正式 output 目錄目前要求為空，不自動重用舊產物。
- reference screenshots 含人工彩框；選幀前會裁掉 screenshot 黑邊，但彩框只作小比例灰階差異，仍不是 ground truth。
- VFR 的最大 delta 為 `0.1s`；所有時間查詢必須繼續使用 PTS index，不能回退到 fps 換算。

目前唯一下一步是人工檢查 `outputs/checkpoint-1a/roi_overlays/` 與 `roi_overlay_manifest.json`。未收到明確核准前不得執行 approval command；即使核准完成，本次任務也不得自行開始 Checkpoint 1B。

