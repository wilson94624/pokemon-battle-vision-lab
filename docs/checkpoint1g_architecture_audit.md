# Checkpoint 1G 視覺證據與架構稽核

## 範圍與 frozen 邊界

Checkpoint 1G 以 Checkpoint 1F sparse state 加上新的視覺 observation 建立 enriched state；它不回寫 Checkpoint 1A 至 1F。正式輸入的 SHA-256 會在 `checkpoint1g_manifest.json` 記錄並於產生前後重驗。

| 視覺來源 | 目前階段 | 1G 處理 | 是否需順序解碼原始影片 |
|---|---|---|---|
| TEAM_PREVIEW | 已有 approved ROI、1 個 candidate、review frame；無 parser | 雙方各六格，文字可見時 OCR，否則保留 icon fingerprint | 是 |
| SELECTED_FOUR | 已有 approved ROI、1 個 candidate、review frame；無 parser | 保留四格 UI 順序，以 icon fingerprint 對齊我方 roster | 是 |
| MOVE_MENU | 已有 approved ROI、31 個 candidates；無 parser | 選擇中 slot、可見招式、狀態欄 identity 與 rejection reason | 是 |
| player_status | 已有 approved ROI；無 candidate／tracker | 精確 HP、percent、status、左右 slot temporal track | 是，正式戰鬥區間固定 2 Hz |
| opponent_status | 已有 approved ROI；無 candidate／tracker | OCR percent、bar estimate、status、左右 slot temporal track | 是，正式戰鬥區間固定 2 Hz |
| Active slot | 無正式 tracker | 融合 status UI、Move Menu、Selected Four 與 1E／1F evidence | 否；使用上述 observation |
| Decision cycle | 無正式 builder | 以 Move Menu clusters 建立非官方決策週期 | 否 |

1B candidate 保存 `start_frame`／`end_frame`、PTS、visible ROI 與 confidence；`frames.jsonl` 的 `frame_index` 已由 Checkpoint 1A ffprobe PTS index 驗證為 ordinal。因此 1G 不使用 `frame_index / fps` 或 `CAP_PROP_POS_MSEC`，也不需要修改 candidate detector。

## 影格、OCR 與視覺特徵

全片只做一次 OpenCV 順序解碼，關閉自動 orientation，依 1A metadata 做固定 rotation，逐張以 ffprobe PTS index 指派時間。解碼完成後必須同時通過 frame count、尺寸、rotation、ordinal position 與所有 request 已擷取等 gate。

Apple Vision OCR 僅用於本機可見文字，固定 `VNRecognizeTextRequestRevision3`、`zh-Hant`、accurate；保存 raw text、line bounding boxes、confidence 與 preprocessing provenance。availability probe 先檢查語言能力，再用 production 相同 `recognize()` batch path 實際辨識合成影像；任一 job 失敗即中止 transaction。icon identity 使用 dHash 與 HSV histogram；HP bar 使用 HSV 健康色彩的水平 run。visual bar 只標為 `visual_bar_estimate`，不升格為 exact HP。

## Temporal tracking 與融合

HP/status 每 0.5 秒取樣。短暫 UI 消失不清除 slot；同 slot 以 identity、穩定數值與視覺 fingerprint 聚合，identity 持續改變才切 track。精確數值、OCR percentage、bar estimate 分開保存，數值 jitter 以小型 rolling consensus 處理。HP change 只描述觀察到的 before/after；沒有直接 timeline evidence 時，cause 固定為 `unknown`。

Entity Resolution 不以名稱作唯一 key。canonical entity 由 side、team slot、selected order、UI track 與時間共同識別；每條 merge／link edge 都保存 rule ID、confidence、source IDs 與 evidence。不確定時保留 visual identity 或 unresolved alias，不使用遊戲合法性或模擬器補值。

Decision Cycle 以相鄰 Move Menu windows 聚合；開局事件進 opening cycle，最後一次選單後至結果進 final cycle。它明確標示 `is_official_turn_number=false`。

Enriched snapshot 對每個 1F snapshot 建立一對一 base mapping，並疊加該時間已觀察的 roster、selected four、active slots、HP、movesets 與 decision cycle。欄位知識狀態區分 `known`、`observed`、`inferred`、`unknown`、`conflicted`、`not_applicable`。

## 開源與官方方案研究

- Apple [RecognizeTextRequest](https://developer.apple.com/documentation/vision/recognizetextrequest)：採用 line location、candidate confidence、固定語言與 accurate 模式。
- Apple [Detecting Objects in Still Images](https://developer.apple.com/documentation/vision/detecting-objects-in-still-images)：採用 sequence handler／避免重複初始化的原則；固定 HUD 不引入較重的 object tracker。
- OpenCV [Template Matching](https://docs.opencv.org/master/de/da9/tutorial_template_matching.html)、[Video Tracking](https://docs.opencv.org/master/dc/d6b/group__video__track.html)、[AKAZE/ORB Tracking](https://docs.opencv.org/master/dc/d16/tutorial_akaze_tracking.html)：採用固定 ROI、fingerprint、connected components 與 temporal consensus；不採用 optical flow，因 HUD 固定且 flow 會加入不必要的 motion 誤差。
- [Pokémon Showdown](https://github.com/smogon/pokemon-showdown)、[pkmn engine](https://github.com/pkmn/engine)、[poke-env](https://poke-env.readthedocs.io/en/stable/modules/other_environment.html)：研究其 state／choice model，但不引入 simulator；目前沒有完整 protocol 或完整 choices，模擬器會製造影片未觀察到的假設。
- [PokeAPI](https://github.com/PokeAPI/pokeapi)：採用 pinned revision 的 species、forms 與繁體中文名稱 metadata。
- [PokeAPI sprites](https://github.com/PokeAPI/sprites)：只保存 pinned tree 的 sprite 路徑／blob provenance，不把未具 repository-level license file 的 binary assets vendoring 到本專案。
- [Pokémon Champions Regulation Set M-B](https://champions-news.pokemon-home.com/en/page/776.html)：採用官方 roster availability，明確與視覺 evidence 分離。
- Entity Resolution survey（[arXiv:1905.06397](https://arxiv.org/abs/1905.06397)）與 multi-source knowledge fusion（[arXiv:1503.00302](https://arxiv.org/abs/1503.00302)）：採用保留 provenance、confidence 與 conflict 的 evidence-edge 模型。

## 已知限制

對手 Team Preview 沒有可讀 species 文字時只保留 visual identity；後續實際出場可透過 status OCR 連回 icon，但未出場者不得猜 species。KB exact alias resolution 只能正規化已讀出的文字，不能補造畫面未顯示的 species。Move Menu 只能可靠保存可見 available moves；沒有確認動畫時 `chosen_move` 與 `target` 保持 unknown。HP bar 因漸層、動畫與遮擋只能作估算，不取代數字 OCR。Decision Cycle 是畫面決策區間，不是官方 turn。
