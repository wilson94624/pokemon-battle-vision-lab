# Milestone 1 — Video Inspection & Annotation Baseline

## 目標

建立一個本地 Python 工具，能可靠讀取 `win-01.mp4`、抽取代表影格、裁切固定 ROI、輸出 contact sheet，並以人工可修正的 JSON 標記畫面狀態。

這個里程碑只建立可觀察、可標記、可驗證的基礎，不做完整 OCR、寶可夢自動辨識或戰鬥事件推論。

## 必須完成

1. 讀取並輸出影片 metadata：
   - 寬度、高度
   - FPS
   - 總影格數
   - 影片長度
   - 是否符合 2868×1320
2. 支援依固定秒數抽幀，輸出檔名包含 timestamp。
3. 產生 contact sheet，且每格能對應回原始 timestamp。
4. 以 normalized coordinates 定義與輸出主要 ROI：
   - team preview
   - selected four
   - player status（左下）
   - opponent status（右上）
   - move menu（右側）
   - battle text（中央偏下）
   - result
5. 讀取 `known_frames.json`，在指定容許誤差內抽出代表影格。
6. 建立人工標記 JSON 格式，至少支援：
   - `TEAM_PREVIEW`
   - `PLAYER_FOUR_CONFIRMED`
   - `MOVE_SELECTION_PLAYER_LEFT`
   - `MOVE_SELECTION_PLAYER_RIGHT`
   - `BATTLE_TEXT`
   - `RESULT`
   - `UNKNOWN`
7. 所有輸出寫入 `outputs/`，不得覆寫原始影片與 reference。
8. 提供 CLI、README 使用方式與基本測試。
9. 核心模組使用清楚命名；關鍵限制與容易踩雷處加入精簡繁體中文註解。
10. 完成後說明修改檔案、模組責任、資料流、風險，以及最值得使用者親自閱讀的 3–5 個程式區塊。

## 驗收條件

- 對有效影片執行 CLI 時，不需修改程式碼即可完成抽幀與輸出。
- metadata 必須正確；解析度不符時要明確警告，不可靜默縮放後假裝符合。
- 六個人工錨點都能在容許時間範圍內輸出對應影格。
- contact sheet 不得缺少 timestamp 對照。
- ROI 座標必須為 0–1 normalized coordinates，且能重新映射到原始解析度。
- 錯誤輸入（檔案不存在、影片不可讀、JSON 格式錯誤）要有可理解的錯誤訊息。
- 測試至少涵蓋 metadata parsing、timestamp/frame conversion、ROI coordinate conversion、annotation schema validation。
- 不得透過硬編碼輸出圖片或測試答案來通過驗收。

## 明確不做

- 完整中文 OCR
- 自動辨識寶可夢物種
- 自動判斷 HP
- 自動辨識招式有效程度
- BattleEvent parser
- 戰術分析或 AI Review
- React、FastAPI、資料庫、登入、部署
- CNN、YOLO 或模型訓練

## 停止條件

完成上述驗收後停止，不得自行擴充到 OCR、模型訓練、網站或完整 Battle Parser。
