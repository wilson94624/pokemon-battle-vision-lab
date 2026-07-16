# Checkpoint 1C 第一階段架構與 OCR 可行性稽核

本階段只讀取 Checkpoint 1B 的既有 `BATTLE_TEXT` 與 `TRIGGER_NOTIFICATION` candidates，執行多影格本機 OCR（光學字元辨識）、文字驗證關卡與人工審查輸出。它不重跑 detector、不調整 candidate 邊界，也不建立 BattleEvent 或語意 parser。

## Frozen baseline

正式輸入固定為 212 candidates：`BATTLE_TEXT=176`、`TRIGGER_NOTIFICATION=2`、`MOVE_MENU=31`、`TEAM_PREVIEW=1`、`SELECTED_FOUR=1`、`RESULT=1`。Checkpoint 1C 啟動與結束都會核對 `events.json`、`frames.jsonl`、ROI config、ROI approval 以及兩種文字 detector／timeline 的 SHA-256。

## 架構稽核的十項答案

1. 每個 candidate 具有 `start_frame`、`end_frame`、PTS 與 10 Hz frame records；Checkpoint 1B Review Pack 另保存 `first_strong_positive`、`peak_score_structure`、`last_strong_positive`、Trigger `peak_evidence`、start/end 與長 candidate evidence strip。所有 ordinal 都能回查 Checkpoint 1A `frame_timestamps.npz`。
2. BATTLE_TEXT 優先使用 first strong、structure peak、peak 前後相鄰 sampled frame、last strong與 evidence strip 中最高品質影格；Trigger 優先使用 side-specific peak、前後相鄰 sampled frame及可用邊界。每筆最多 7 張並去除重複 ordinal。
3. BATTLE_TEXT 使用中央文字 ROI，需保留白字與暗色底板並抑制動態背景；Trigger 使用由 frozen canonical ROI 推導的實際 side analysis context，額外強調半透明暗板與兩行短文字。兩者共用有限、可解釋的 variants，但參數與 ROI policy 分開。
4. Apple Vision adapter 回傳逐行文字、bounding box 與 0–1 confidence；Python 層保留 `raw_text`，另做 Unicode NFKC、空白與換行的輕量 normalization。
5. 聚合先按 normalized exact match 與相似度建立文字群組，再以不同 frame 的支持數、OCR confidence、frame quality 與 variant quality 加權；不會只取單一最高 confidence。
6. Frame selection 先以 ordinal 去重；同 candidate 的 variants 只作為 evidence，不讓相同 frame 重複灌票。相鄰 candidates 另以文字相似度、CJK overlap 與時間間隔標記 duplicate，但不自動合併或刪除。
7. 多影格、多 variant 都為空或只有低信心非 CJK 雜訊，而且 frozen detector 沒有文字底板 template support，才可高信心判純特效型 `NO_TEXT`。若有明確 template、engine error 或雜訊結果互相衝突，一律保守判 `UNCERTAIN`；Mega 周邊 UI 另以數字狀態列加低 CJK 比例判斷。
8. Text validation record 同時保存 `validation_label`、`workflow_status`、review reasons、OCR evidence references、duplicate 欄位與預設為 `null` 的 human action/edit/merge/split/reviewer 欄位，可直接供未來 Human Review GUI 使用。
9. Pipeline 在建立 staging 前記錄 frozen hashes，驗證 candidate counts 與 ROI approval；完成 OCR、schema、path 與輸出驗證後再次計算 hashes。任何差異都使 transaction 失敗並保留上一版。
10. 新增 raw OCR、aggregate、text validation、Checkpoint 1C manifest 與 review schema；正式輸出分為 `outputs/checkpoint-1c/` 與 `outputs/checkpoint-1c-review/`，不改寫 `outputs/checkpoint-1b-review/`。

## OCR engine feasibility

| Engine | 繁中／confidence | macOS Apple Silicon | 安裝與重現成本 | 決策 |
|---|---|---|---|---|
| Apple Vision `VNRecognizeTextRequest` | 本機實測 `zh-Hant`，逐候選 confidence 0–1 | 系統原生、CPU/ANE 由框架管理 | 不下載模型；固定 language、accurate level 與 revision | 採用 |
| PaddleOCR | 有中文模型與 confidence | 官方宣稱支援 macOS | 需 Paddle inference runtime、額外模型與較大依賴；首次下載不利完全離線重現 | 本輪不採用 |
| EasyOCR | `ch_tra` 與 confidence | CPU 可執行 | 依賴 PyTorch，模型預設需下載；繁中仍使用較舊 generation | 本輪不採用 |
| Tesseract | `chi_tra.traineddata` 可用 | 可透過 Homebrew/MacPorts | 本機目前沒有 binary 或語言 data；遊戲動態背景通常需要更多 segmentation tuning | 保留為 fallback |

Apple 官方文件：<https://developer.apple.com/documentation/vision/vnrecognizetextrequest>；confidence 定義：<https://developer.apple.com/documentation/vision/vnrecognizedtext/confidence>。本機 Swift toolchain 與 SDK revision 不一致，因此以系統 `clang` 編譯 repository 內的小型 Objective-C adapter，直接連結 `Vision`、`Foundation`、`ImageIO` 與 `CoreGraphics`；這不會呼叫網路服務。

## Pipeline boundary

```text
frozen Checkpoint 1B events + frame evidence
  -> deterministic multi-frame selection
  -> one verified sequential video decode
  -> bounded preprocessing variants
  -> local Apple Vision raw OCR
  -> multi-frame consensus aggregation
  -> VALID_TEXT / NO_TEXT / UNCERTAIN gate
  -> duplicate hints only
  -> auto_accepted / needs_review / rejected Review Pack
```

`rejected` 只是不預設進最終時間線；原 candidate、frame、variant、raw OCR 與 aggregate 全部保留。本階段禁止字典式校正、SVO、BattleEvent、turn reconstruction、GUI 與任何雲端 OCR／LLM Vision。

## Initial evaluation fixture 的隔離邊界

`references/checkpoint1c_initial_evaluation.json` 記錄清楚文字、淡入淡出、短訊息、possible duplicate、Mega 周邊 UI、水花特效及兩個 trigger 的少量人工診斷案例。Pipeline 必須先完成全部 raw OCR、aggregation、Text Validation 與 duplicate marking，才由獨立的 `checkpoint1c_evaluation.py` 比較預期／實際並寫出 report；report 的 `inference_feedback_used` 固定為 `false`。正式 detector、frame selection、preprocessing、OCR engine、aggregation 與 validation 都不 import 或讀取這份 fixture。
