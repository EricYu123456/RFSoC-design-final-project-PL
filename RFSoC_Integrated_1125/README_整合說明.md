# RFSoC 4×2 雙板 Webcam + TCP + AI 整合包（2026-11-25）

## 單板執行（無 Rubik Pi、無相機）

從 `B_RX/` 開啟 `RFSoC4x2_OFDM_MethodB_RX_TCP_AI.ipynb`。更新程式後請重啟該 notebook 的 kernel，再執行全部儲存格。預設 `MODE = "single_board"`、`BENCH_CAMERA = None`、`TCP_ENABLED = False`、`AI_ENABLED = False`。單板只需開 B 端 notebook。

每次執行會載入本套件的 overlay。modem 維持 reset，PS 將 JPEG 區塊寫入 TX BRAM、讀回比對，再把資料、descriptor 與模擬的封包品質寫入 RX BRAM。正式 RX 程式接著驗 SHA-256、解碼、呼叫 `on_decoded` 並寫 MP4。預設跑 3 個區塊，每區塊 3 幀。`B_RX/received_live_webcam/<時間>_single_board/` 內有 `bringup.log`、`result.json`、`summary.json` 和 `benchmark.mp4`。空中資料、封包品質判定與 ACK 要用雙板驗證。

### 時鐘與 BRAM 存取

- 板上 PYNQ 3.1.1 建立 `Overlay(..., download=False)` 就會更新目前行程的 `PL.bitfile_name`。2026-11-26 在 FPGA manager 最後載入 `base.bin` 的狀態下，只解析 OFDM metadata 就觀察到這個檔名變成 `ofdm_qam16.bit`。舊單板流程可能因此跳過下載，對錯誤的設計發出 AXI 存取。原先死機當下未取得匯流排追蹤。
- 單板與雙板現在都在同一把板鎖內完成 metadata 解析、設定 245.76 / 491.52 MHz 參考時鐘、下載已核對雜湊的 bit/HWH，然後讀 PS host signature。兩個 tile 都到達 state 15、PLL 鎖定、RF 時鐘 ready 且 modem ID 相符後，才操作 BRAM。PG269 要求啟動前外部時鐘穩定，AXI4-Stream 時鐘穩定後才能解除對應 reset。時鐘值與鎖定訊號接法取自本專案的板級設計。[AMD PG269 啟動順序](https://docs.amd.com/r/en-US/pg269-rf-data-converter/Power-up-Sequence) · [AMD PG269 tile 狀態](https://docs.amd.com/r/en-US/pg269-rf-data-converter/Power-on-Sequence-Steps)
- 本 HWH 的 `source_ctrl`、`sink_ctrl` 均為 32-bit AXI4 BRAM Controller，各 512 KiB。程式核對 PYNQ 具名 bank 的基址及容量，沿用參考專案的對齊 32-bit 逐字讀寫。PYNQ 在底層仍使用 MMIO。這份 bitstream 的主機介面是 GPIO 與 BRAM。[AMD PG078 AXI BRAM Controller](https://docs.amd.com/v/u/en-US/pg078-axi-bram-ctrl)

## 三份來源與取捨

| 來源 | 採用 | 未採用 |
|---|---|---|
| GitHub `EricYu123456/RFSoC-design-final-project-PL` → `OFDM_two_board_4subcarriers_16QAM_webcam` | `ofdm_qam16.bit`、`ofdm_qam16.hwh`、`manifest.json`（SHA-256 已核對與 manifest 一致） | 舊版 `webcam_live.py`／`webcam_stream.py`（沒有 callback）與原 notebook；RTL、sim、scripts 留在 repo 重建用 |
| `RFSoC_MethodB_TX_RX_split.zip` | TX／RX notebook 與 7 個 `webcam_*` 模組（帶 `on_encoded`、`on_decoded`、`jpeg_quality`、`duration_s`、每區塊幀數統計） | 無 |
| `1125_RFSoC_AI.zip` | `ai/`（detector、spawn worker、replay）、`config/detector.example.json`、模型檢查／單圖推論／mock／統計工具、`docs/MODEL.md`、單元測試 | 封存的原 notebook、replay 版 notebook、mock 驗證結果、preflight、離線安裝腳本、package manifest 工具 |

原始分板版採用 GitHub 版的板端介面，並在 `webcam_live.py`／`webcam_stream.py` 加上 callback 與參數。本次單板修正同步套用到兩個端點的 `webcam_board.py`、`webcam_live.py` 與 `webcam_benchmark.py`。各端的 `manifest.json` 保存原 FPGA release 資訊，載入器核對其中的 bit/HWH 雜湊；整合版程式的雜湊以 `manifest_integrated.json` 為準。

1125 AI 包當時找不到可用的即時 RX hook，只能做錄影 replay。分板版的 `Recording.append()` 已在 JPEG 解碼成功後呼叫 `on_decoded(frame_id, gray, jpeg)`，這正是 AI 文件要求的位置：SHA 驗證完成、BRAM 已釋放，`gray` 是獨立的 640×480 uint8 陣列。整合版因此直接在這個 callback 裡呼叫 `AIWorker.submit()`。

## 部署

```
A_TX/  → 整個資料夾複製到 A 板（接 USB 攝影機）
B_RX/  → 整個資料夾複製到 B 板
```

雙板時各自從該資料夾啟動 Jupyter，順序如下。

1. 需要 TCP 轉送時先啟動 PC／Rubik Pi 上的 server，並在 B 的 notebook 設定 `TCP_ENABLED=True` 與 `TCP_HOST`。
2. B 板 `RFSoC4x2_OFDM_MethodB_RX_TCP_AI.ipynb` 設 `MODE = "live"`，等到 `RX_WAITING_FOR_PILOT`。
3. A 板 `RFSoC4x2_OFDM_MethodB_TX.ipynb`

RF 接線與原專案相同：A DAC_A → B ADC_A（影像），B DAC_A → A ADC_A（ACK），兩條路徑都要加衰減。

## B 板仍需補上的檔案

| 檔案 | 什麼時候需要 |
|---|---|
| `transport/protocol.py`（及其相依） | `TCP_ENABLED=True`；必須用原 TCP 專案的版本，不能自訂封包格式 |
| `models/ssd_mobilenet_v1_int8.tflite`、`labels/labels.txt` | `AI_ENABLED=True` 且 `AI_MOCK=False` |
| `config/detector.json`（由 example 複製，填 tensor index、前處理，設 `contract_confirmed=true`） | 同上，步驟見 `docs/MODEL.md` |
| 板上相容的 `tflite_runtime`（或既有 TensorFlow） | 同上 |

## AI 的兩條路

- **Rubik Pi（系統圖的主路徑）**：B 板經 TCP 送出 Gray8 原始像素（307,200 bytes/幀），Rubik Pi 端做推論。B 板不跑模型。
- **B 板 PS（可選，用來對照）**：`AI_ENABLED=True`。worker 在 RF 開始前載入模型；`submit()` 不等待，忙碌時直接丟幀，不會反壓 RF 或錄影。結果在 `B_RX/results/live_<時間>/`。

建議驗證順序：
1. `AI_ENABLED=False`，量一次基準。
2. `AI_ENABLED=True, AI_MOCK=True`，確認資料流。
3. 有模型之後跑 `python -m tools.inspect_model models/ssd_mobilenet_v1_int8.tflite`，再用 `python -m tools.infer_image <已知圖片>` 對照 golden。
4. 開真模型，與基準比較 `delivered_fps`、`done_to_release`、`rx_service`、`record_queue_wait`、`rx_analysis` 與 `submit_max_ms`。

即時 AI 影響 RF 時，關掉 AI，改用 notebook 第 8 節對錄好的 MP4 做 replay。

## 2026-11-26 RFSoC4x2 實板驗證

| 測試 | 觀察結果 |
|---|---|
| B 端 notebook 預設單板配置 | 完整執行全部儲存格。3 個資料區塊、9 幀、零丟棄，逐幀讀回 MJPEG MP4。 |
| B 端單板延長測試 | 44 個資料區塊、132 幀、零丟棄、收到 EOS，MP4 解碼 132 幀。RAM 按設定保留最後 120 幀，callback 錯誤為零。 |
| B 端雙板入口、單板等待 | 到達 `RX_WAITING_FOR_PILOT`，送出 Interrupt 後完成 reset 並釋放板鎖。 |
| A 端檔案影像來源 | 以 12 幀測試影片執行 A notebook 的編碼 callback。12 個資料區塊及 EOS 的 TX BRAM 完整讀回值與 descriptor 均吻合，測試時 modem 維持 reset。 |

## 原始整合版的本機驗證（無 RFSoC 硬體）

- 所有 `.py` 與 notebook code cell 語法通過；AI 單元測試 11 項通過（`cd B_RX && python -m unittest discover -s tests -t .`）。
- 端對端模擬：直接執行 RX notebook 的 cell，`run_live` 換成以分板版 `Recording` 餵 60 張 JPEG。TCP（測試用 stub 協定）收到 60 幀共 18,433,440 bytes，與預期一致。AI mock 在 10 FPS 取樣下 submit 60、accepted 60、完成 23 筆，最後一筆是 frame 59；每筆像素值都對得上 frame_id，正常關閉。
- 沒有模型時開 `AI_ENABLED`：在 TCP 與 RF 啟動前就報錯。AI 和 TCP 都關掉時，summary 與原版相同。
- 尚未驗證：真實 RF 雙板傳輸、真模型推論速度與準確率、ARM 上的 tflite ABI、與 TCP／錄影同時運作時的 CPU 競爭。
