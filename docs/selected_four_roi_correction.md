# SELECTED_FOUR 六列 ROI 修正

## 根因與正確語意

舊 calibration revision 3 由 win-01 的單一確認畫面量測；該場選中的四隻恰好位於 roster 第 3–6 列，因此 `selected_four` 被錯誤縮成下方四列。Checkpoint 1G 隨後又把四個 crop 的排列順序直接視為 selection order。這是 replay-specific coincidence，不是遊戲 UI invariant。

Team Selection 始終顯示六隻 player roster；其中任意四列可被選取，selected rows 可不連續，順序 marker 1–4 可出現在六列中的任何位置。Team Preview 用於觀察六隻可用 roster；Team Selection 用於觀察被選的四列及其順序。兩者幾何可以相同，但 evidence responsibility、ROI ID 與 parser semantics 不可合併。

## 幾何修正

| config | normalized `selected_four` | pixel（2868×1320） | 用途 |
|---|---|---|---|
| revision 3 frozen | `x=0.195, y=0.365, width=0.145, height=0.475` | `x=559, y=481, width=417, height=628` | 只涵蓋 win-01 當時的下方四列；僅供 frozen artifact hash 重現 |
| revision 4 corrected | `x=0.118, y=0.049, width=0.242, height=0.873` | `x=338, y=64, width=695, height=1154` | 完整六列 player roster 與所有 marker 位置 |

修正版位於 `configs/roi_2868x1320_v2.json`，仍使用 `pokemon-champions-doubles-zh-tw-2868x1320-v1` supported profile，狀態為 `pending_human_approval`。原 `configs/roi_2868x1320.json` 不能改動，因為 win-01 的 1A approval、1B 與 1G manifests 都鎖定其 SHA-256。

## Checkpoint 影響稽核

| Checkpoint | 實際使用／影響 |
|---|---|
| 1A | 產生 `kf_selected_four` overlay、pixel conversion、manifest 與 approval hash；修正版需新 overlay 與人工核准。 |
| 1B | `SELECTED_FOUR` template detector 與 Review Pack crop 使用此 ROI；換 config 必須在新 analysis revision 重新掃描，不能沿用舊 approval。 |
| 1C | 不對 SELECTED_FOUR 做 OCR，但 frozen input gate 追蹤 ROI／1B hashes；語意資料不直接受影響。 |
| 1D–1F | BattleEvent、Timeline 與 sparse Battle State 不接收 TEAM_PREVIEW／SELECTED_FOUR，因此沒有由此缺陷產生的 event/fact。 |
| 1G | 直接使用 ROI。修正版切成六個 roster rows，保存全部六列 evidence，只以可見 marker 1–4 建立 `player_selected`；marker 不足時保留 unknown。 |
| 1H | 只載入 1G 的 HP、entities、decision cycles 與 move menus；`selected_four.json` 不在必要輸入集合。entities 的 `selected_order` 也不參與 Battle Fact reconstruction。 |
| 1I–1J | 只消費 1H facts／relations 與 Rule Interpretation；沒有 SELECTED_FOUR 直接依賴。 |

## Canonical artifact 政策

win-01 舊畫面確實是第 3–6 列被選，舊 ROI 沒有漏掉該 replay 的四個 marker；正式 1G 的四筆 selected-four resolution 也全部是 `unknown`，沒有為 entity 設定 `selected_order`。因此既有 Battle Facts 與 Rule Interpretations 不因本缺陷失效，不需要覆寫或重跑 canonical outputs。

若未來需要修正 win-01 的 selected-team observation，應建立新的 1A→1G corrective analysis revision；不得改寫既有 `outputs/checkpoint-*`。official-02 尚停在未核准 1A，應以 revision 4 重做自己的 1A overlay 並停在人工 approval gate，無須回溯任何 win-01 artifact。
