# Checkpoint 1A 架構與驗收邊界

## 權威與資料流

```text
FFmpeg/ffprobe preflight
→ ffprobe container + video stream metadata
→ rotation 後 display-resolution gate
→ input image magic-byte + provenance validation
→ ffprobe best_effort_timestamp_time index
→ PTS 完整性／嚴格單調性／VFR 診斷
→ OpenCV 關閉 autorotation、從頭到尾順序解碼
→ ffprobe ordinal 與 decoded ordinal 一對一驗證
→ anchors + 固定間隔 frames + paginated contact sheets
→ raw-video ROI overlays + hash manifest
→ pending human ROI approval（停止）
```

ffprobe 是 container、codec、encoded dimensions、rotation、time base 與 frame PTS 的權威來源。OpenCV 只提供 pixels 與 backend 診斷。流程沒有 `frame_index / fps` 精確時間 API，也不使用 `CAP_PROP_POS_MSEC` 或 random seek。

ffprobe Display Matrix 的正 rotation 值採 counter-clockwise 語意；metadata 同時保留 raw／counter-clockwise 值，內部轉成明確的 clockwise canonical 值後交給 OpenCV。這個方向會由 raw-frame overlay 的正向視覺 QA 驗證，不能只靠寬高交換判定。

## 模組責任

- `media_probe.py`：dependency preflight、ffprobe JSON、rotation normalization、metadata、PTS 與 VFR。
- `video.py`：關閉 OpenCV autorotation、明確 rotation、全片順序 decode、ordinal 對齊與 anchor 穩定幀選擇。
- `image_io.py`：magic-byte detection、`imdecode`、一致的 PNG／JPEG 輸出與寫後驗證。
- `sampling.py`：以 PTS 最近查詢建立固定秒數 targets；等距時選較早 frame。
- `contact_sheet.py`：分頁排版與 tile → PTS／ordinal／frame path 反查。
- `roi.py`：normalized → pixel、raw-frame overlays、人工 approval hashes。
- `pipeline.py`：唯一 1A orchestration 與所有 failure gates。
- `annotations.py`：只保留原 acceptance 要求的 schema 驗證契約，不產生 annotation draft。

## 文件解讀與範圍

`docs/milestone1_acceptance.md` 描述整個 Milestone 1；核准修訂版 Plan 將交付拆成 1A／1B。本次採最保守解讀：

- 1A 完成 acceptance 所需的 foundation、metadata、固定抽幀、contact sheets、normalized ROI、known anchors、輸入錯誤與 schema validation 基礎。
- annotation schema 可存在並測試，但不產生 annotation draft。
- `sampling.py` 只包含 1A 固定秒數 schedule；沒有完整影片 10 Hz feature schedule 或 `analysis_samples.npz`。
- 不建立 `similarity.py`、`segmentation.py`、`battle_text.py`，避免把 1B 空殼誤認為已實作。

這個解讀不會擴張到 Checkpoint 1B，也沒有發現必須中止 1A 的 Plan／acceptance 實質衝突。

## Failure gates

- executable 缺失、timeout、non-zero、malformed JSON、capability 欄位缺失：可理解的非零錯誤，禁止 fallback 到 OpenCV metadata。
- display dimensions 不符：仍寫 metadata/mismatch，接著在 PTS、ROI、anchors 前停止。
- PTS 缺失、重複、非單調：寫 PTS 診斷與 metadata 後停止。
- OpenCV decoded dimensions、autorotation、ordinal position 或 frame count 無法對齊：寫詳細 alignment report 後停止，不猜測修正。
- ROI approval：只接受 video/config/manifest/overlay hashes 全部一致的人工 command。

## 已知風險

- 外部 ffprobe `-show_frames` 與 1 GB HEVC 全片 decode 有固定時間成本；1A 以 `.npz` 保存 PTS index，但目前輸出目錄採不可混用策略，不自動重用舊快取。
- anchor 代表幀只在各自 tolerance window 內，以 PTS 距離、reference-image 灰階差異、相鄰影格 motion 與 Laplacian clarity 綜合選擇；reference 不參與分析範圍、segmentation 或 threshold，它只做 validation，不是 state detection ground truth。
- ROI 是單一 profile 的初稿；人工核准前不能作為後續分析契約。
- OpenCV `CAP_PROP_POS_FRAMES` 只作 ordinal 順序診斷，絕不作時間權威。
