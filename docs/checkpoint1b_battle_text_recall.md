# Checkpoint 1B BATTLE_TEXT precision、boundary 與 segmentation

本輪只修改中央 `BATTLE_TEXT` 的 classical CV proposal、專屬 timeline、diagnostics 與 Review Pack 呈現。正式掃描仍固定 `10 Hz`，ROI 與其他五種 event detector 均未變；沒有 OCR、文字解析、BattleEvent parser 或 Checkpoint 1C。

## Round-1 人工樣本根因

前 35 個舊候選的完整 audit table 位於 `references/battle_text_human_review_round1.json` 的 `baseline_audit`。這份 fixture 只供 debug／回歸報告使用，production detector 不會匯入。

16 個空候選的舊觸發路徑如下：

- template-only：`0001`、`0008`、`0009`、`0010`、`0012`、`0013`、`0025`、`0032`
- structure-only：`0003`、`0004`、`0005`、`0011`、`0028`、`0029`
- template 與舊 structure 混合：`0002`、`0026`

主要干擾來自 team/status UI、人物與寶可夢輪廓、場地圓形燈光、招式亮塊及轉場。舊 structure feature 觀察整個 ROI 的亮度與 edge，沒有確認「多個小型低飽和元件沿水平文字列排列」，因此大型輪廓與單一亮塊也可能得到高分。

`0006`／`0007`／`0014` 的舊 timeline 把所有 raw positive 視為同等 continuation；其中 `0014` 在最後 structural positive 後又被 template-only weak evidence 延長約 1.3 秒。`0033` 的 layout reference 每個 positive 都更新，且只比較相鄰 frame；真正換訊息時的單步距離未達舊 split threshold。`0021`／`0022` 之間是 iOS 控制中心遮擋同一個底層畫面；遮擋前後 strong samples 的最小 layout distance 為 `0.000798`，新版以通用 same-layout reopen 規則合併，不讀取文字內容。`0035` 的固定 middle 位於淡出，peak structure frame 更清楚。

## Proposal features

`battle_text_features.py` 只在 Frozen `battle_text` ROI 的候選文字垂直區域與左側文字範圍計算：

- 低飽和、高亮且相對局部背景更亮的 text mask
- connected components 的寬、高、面積與大型亮塊比例
- 水平文字列的 aligned component count、橫向 span 與高度一致性
- text mask ratio、局部背景與 edge 密度
- 排除橫跨 ROI、但 mask 很稀疏的場地燈點／選單刻度排列
- row／column profile、bbox、component count 與 downsampled mask hash 組成的 layout fingerprint

Proposal 分為 `strong`、`weak`、`negative`。大型單一亮塊、過亮 mask、缺少水平文字列或元件高度不一致會留下明確 `negative_reasons`。低元件數的 template-supported evidence 只作 weak proposal，須由連續 weak 或後續 strong 支持；真正 strong 可立即保留 0.1 秒訊息。

## Temporal state machine

`battle_text_timeline.py` 明確區分：

- open：strong 立即開啟；連續三個 weak 才能 provisional open，先前 weak 也可由 strong confirmation 納入起點。
- continue：strong 正常延續；有文字結構的 weak 可有限延續，weak-only continuation 有固定上限。
- bridge：最多橋接一個 0.1 秒 negative gap。
- close：連續 negative、weak decay 或 end-of-stream 都記錄 `close_reason`；generic weak tail 與 structural weak tail採不同邊界上限。
- split：開啟後先經過 fade-in grace period建立穩定 reference；只有 layout distance 持續超過門檻，或淡出後恢復為明顯不同 layout，才切成新 candidate。
- merge prevention：一個 0.1 秒 gap 直接 bridge；較長但有界的遮擋只有在前後 fingerprint 幾乎相同時才 same-layout reopen；不依 candidate ID 或文字內容判斷。

Candidate duration 完全由上述 evidence state 決定，沒有固定 1 秒／2 秒裁切、minimum duration filter、cooldown 或 candidate suppression。

## Diagnostics 與 regression

`outputs/checkpoint-1b-debug/battle_text_diagnostics.jsonl` 對全部 5,918 個 samples 保存 PTS、ordinal、proposal score、evidence level、features、layout fingerprint、open／continue／bridge／close／split decision 與原因。

`battle_text_detector_report.json` 比較重掃前後的 candidate 統計與 17 個 known recall windows。Round-1 mapping 使用時間 overlap 與人工記錄的 visual spans，不依賴新版 candidate IDs，輸出到 `outputs/checkpoint-1b-review/battle_text_round1_regression/`。

## Transactional replacement 與 macOS visibility

Checkpoint 1B、debug 與 Review Pack 都先在同層的可見 staging directory 建立，例如 `checkpoint-1b-review.tmp-<UUID>`。Schema、hash、數量與路徑通過後才 rename 替換正式目錄；失敗會保留上一版。

正式 output tree 在 commit 前後都會遞迴清除並驗證 BSD `UF_HIDDEN`。空白的精確衝突目錄 `<output> 2` 可安全移除；若其中有內容則拒絕覆蓋。成功後不得留下 dot staging、tmp、backup 或空白衝突副本。

## 人工 gate

17/17 regression coverage 與 round-1 targets 通過，只代表已知案例沒有回歸。全片 false positive、漏檢與 boundary 仍必須由人先看 0.5 秒 coverage、round-1 regression、dense recall audit，再看 BATTLE_TEXT contact sheets。
