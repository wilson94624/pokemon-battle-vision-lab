# Apple Vision production runtime 修復紀錄

## 結論

Checkpoint 1G 原本的 `probe()` 只讀取 `supportedRecognitionLanguages`，沒有真的執行 `VNImageRequestHandler.performRequests`，因此會在 production OCR 無法建立 CoreVideo buffer 時誤報可用。新版 probe 先做 capability check，再透過與正式流程完全相同的 Python `recognize()` batch path 辨識一張 deterministic 合成 PNG。probe 或任一正式 job 失敗時，1G 會中止 paired transaction 並保留上一版 output，不再把 runtime failure 隱藏成 `unknown`。

## 已驗證根因

在 Codex managed sandbox 中，同一個最小 Objective-C 程式、同一張 evidence crop、同一個 `VNRecognizeTextRequestRevision3`：

- `recognitionLevel=fast` 回傳 `NSOSStatusErrorDomain -6662`，訊息為無法建立 `CVPixelBuffer`。
- Apple CoreVideo SDK 的 `CVReturn.h` 將 `-6662` 定義為 `kCVReturnAllocationFailed`。
- `recognitionLevel=accurate` 可能回傳 `perform_success=false`、`results=nil` 且沒有 `NSError`；helper 現在會把這種狀態明確視為失敗。

相同 binary 與 image 在 sandbox 外執行時，`fast`、`accurate` 及正式 `AppleVisionOcrEngine.recognize()` 都成功。因此排除 ROI、影像格式、`zh-Hant`、Vision revision 與 helper source 差異；根因是 managed sandbox 對 Vision／CoreVideo runtime 資源的限制。正式 1G 必須在允許 Apple Vision runtime 的本機 process 執行。

## 最小重現

檔案：`tools/apple_vision_runtime_mre.m`

```bash
xcrun clang -fobjc-arc \
  -framework Foundation -framework Vision -framework ImageIO -framework CoreGraphics \
  tools/apple_vision_runtime_mre.m -o /tmp/apple_vision_runtime_mre

/tmp/apple_vision_runtime_mre \
  outputs/checkpoint-1g-review/evidence/team-player-slot1.jpg \
  accurate zh-Hant
```

這個 MRE 不依賴 Python、OpenCV、OCR preprocessing 或 Checkpoint 1G orchestration，只建立 image source、執行一次 Vision request 並輸出結構化結果，可直接比較 sandbox 內外行為。

## 修復界線

- 固定使用本機 Apple Vision；沒有 cloud fallback。
- probe 與 production 共用相同 `recognize()` path。
- helper 同時檢查 synchronous `performRequests` error、completion-handler error、nil results 與結果型別。
- production 任一 OCR job error 都使 generation 失敗，paired transactional output 不提交。
- 未修改 Checkpoint 1A–1F，也未重新產生它們。
