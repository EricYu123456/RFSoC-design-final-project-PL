# 雙板 RFSoC 4×2 OFDM 上板指南

## 設備與接線

使用兩塊有 PYNQ SD 映像的 RFSoC 4×2、兩條 50 Ω SMA 同軸線及兩個 10 dB 衰減器。板 A 傳送圖片，板 B 接收圖片。每塊板的 `DAC_A` 在手冊標為 DAC0，`ADC_A` 標為 ADC0。先在兩塊板上辨認這些 SMA 接頭。 [RFSoC 4x2 Reference Manual](https://www.realdigital.org/downloads/4b98c421901794107cd1e25e208fe002.pdf)

| 路徑 | 起點 | 線材 | 終點 |
| --- | --- | --- | --- |
| 圖片資料 | 板 A `DAC_A` | 50 Ω 同軸線與 10 dB 衰減器 | 板 B `ADC_A` |
| 回覆 ACK | 板 B `DAC_A` | 50 Ω 同軸線與 10 dB 衰減器 | 板 A `ADC_A` |

分別開啟兩板的 JupyterLab 終端機，也可以使用兩個 SSH 終端機。若經同一個管理網路存取兩塊板，請確認 IP 各自可識別。圖片封包與 ACK 都走上表的 RF 路徑。管理連線用來執行命令與取回驗證檔案。

在兩塊板的 PYNQ 資料夾分別解壓同一份 `ofdm_two_board_rfsoc4x2.zip`，保留 `ofdm_two_board.bit`、`ofdm_two_board.hwh`、`manifest.json` 和 `run_on_board.py` 在同一層。將 XSA 留在 `hardware_description/` 供 Vivado 使用。關閉其他會操作 FPGA 的 Notebook 或程式，再開始以下步驟。

## 傳圖

1. **先在 B 啟動接收。** 在 B 的解壓資料夾執行下列指令。等到 `RX_WAITING_FOR_PILOT`。

   ```bash
   sudo -E python3 run_on_board.py receive
   ```

2. **再在 A 啟動傳送。** 在 A 的解壓資料夾執行下列指令。預設使用包內的 `image.raw`。

   ```bash
   sudo -E python3 run_on_board.py send
   ```

3. **等 B 顯示 `RX_ARMED`，再到 A 按 Enter。** 兩板會先自動交換兩次短暫的 500 MHz pilot。A 發給 B，B 量完等待 A 靜音，再回送給 A。每塊板用自己的靜音和 pilot 幅度計算封包偵測門檻。
4. **等 A 顯示 `TX_DONE`，再到 B 按 Enter。** A 逐包等待有效 ACK，每包最多嘗試 11 次（首次加 10 次重傳）。每次封包送完後最多等待 ACK 65,536 個 RF PL clock，約 427 µs。耗盡次數後 A 會記下該包並繼續，`TX_DONE` 會顯示 `exhausted` 與總重傳次數。B 持續接收並回 ACK，直到你在 B 按 Enter。
5. **查看 B 的 `RX_DONE`。** B 會停止接收並從 BRAM 匯出 640 × 480 圖片及各包品質，即使部分封包未收齊。程式會重設各板的 OFDM 資料路徑。

## 接收圖與驗證

B 以獨立的標頭 CRC-32 驗證封包編號與長度，並依封包編號寫入圖片固定位置。每包的像素範圍為 `packet_id × 1024` 到下一包之前。B 另用整包 CRC-32 驗證內容。

| 品質碼 | 結果 |
| --- | --- |
| 2（CRC 正確） | 保留通過整包 CRC 的資料並回 ACK。若 A 因 ACK 遺失而重傳，B 收到 CRC 正確的重複包會再回 ACK。 |
| 1（CRC 錯誤） | 如果尚未取得 CRC 正確的版本，保留最近一次完整且標頭通過 CRC、整包 CRC 錯誤的 payload，並等待後續重傳。後續無法完整解碼的嘗試會保留這份備用資料。只有 CRC 正確的封包會收到 ACK。 |
| 0（缺包） | 所有嘗試皆無完整、標頭通過 CRC 的資料。這一包的 1,024 個像素維持為 0（黑色）。 |

A 的 `tx_results/source.raw` 是寫入 A 的 BRAM 並通過讀回驗證的原圖。B 的 `rx_results/received.raw` 和 `received.png` 是從 B 的 BRAM 讀回的圖，`packet_quality.raw` 的 300 bytes 依序記錄第 0 至第 299 包的品質碼。兩板各自的 `result.json` 記錄 bit/HWH 雜湊、pilot 幅度及板端狀態。A 記錄重傳及用完次數的包數，B 記錄重複包、CRC 錯包和缺包。板端 `completed: true` 表示已完成傳送嘗試或已完成接收圖匯出。B 的 `run_on_board.ipynb` 可以顯示接收圖與報告。

將 A 的 `tx_results/`、B 的 `rx_results/` 和 `run_on_board.py` 複製到同一台電腦。使用 Python 3 比對兩板像素及驗證紀錄。

```bash
python run_on_board.py verify --tx-dir tx_results --rx-dir rx_results --output two_board_result.json
```

只有 300 包均通過 CRC 且逐 byte 比對完全一致時，`passed` 才是 `true`。報告同時列出 `mismatched_bytes`、收發 SHA-256、`crc_bad_packet_ids` 與 `missing_packet_ids`。有錯包或缺包時，驗證程式會回傳非零結束碼，仍保存報告和已匯出的圖片。若 B 已正確收到圖而回程 ACK 遺失，A 的 `exhausted_packets` 可大於 0，像素驗證仍可通過。

更換圖檔時，在 A 的指令加上 `--input photo.jpg`。一般圖檔會轉為 640 × 480、8-bit 灰階；`.raw` 必須恰好為 307,200 bytes。需要保留多次紀錄時，可在兩個指令各加 `--output <資料夾>`。

## 量測及除錯

- B 預設最多等 A 的 pilot 180 秒，可在 B 加上 `--wait 300`。校正後 B 會維持接收直到你按 Enter。A 從按 Enter 開始計算的整張圖等待上限預設為 30 秒，可在 A 用 `--timeout` 設定。
- A 等不到 B 的 pilot 時，先看 B 的終端機。如果 B 停在 `RX_WAITING_FOR_PILOT`，先檢查 **A DAC_A → B ADC_A**。若 B 已印出 `RX_PILOT`，先檢查 **B DAC_A → A ADC_A**。
- 檢查 `idle_magnitude` 和 `pilot_magnitude`。pilot 太弱時，先檢查 SMA 方向、接頭及衰減量。pilot 過強時，增加該線衰減或降低對端 `--amplitude`。振幅預設 4096，可用範圍為 64–16380，須為 4 的倍數。A 的振幅控制資料方向，B 的振幅控制 ACK 方向。
- 要中止或重跑，先在兩個終端機按 Ctrl+C 結束仍在執行的程式，再從 B 的指令開始。兩份 `bringup.log` 記錄每階段的 `BEGIN`、`OK` 或 `FAIL`。用 `result.json` 查當次狀態。
- 可在各板的 `send` 或 `receive` 後加 `--stop-after metadata` 檢查檔案與 HWH、加 `--stop-after ps_bus` 檢查下載後 AXI，或加 `--stop-after rfdc` 檢查時脈、PLL、FIFO 與 mixer。例如在 B 執行下列指令。

  ```bash
  sudo -E python3 run_on_board.py receive --stop-after rfdc
  ```

## 硬體與暫存器

| 項目 | 設定 |
| --- | --- |
| DAC | `DAC_A`，tile 230 block 0，`s20_axis`，NCO +500 MHz |
| ADC | `ADC_A`，tile 226 dual block 1，`m22_axis`／`m23_axis`，NCO −500 MHz |
| RFDC | ADC 與 DAC 4.9152 GS/s。插值／抽取 16×，每拍兩個複數樣本 |
| 時脈 | 兩板各設 LMK 245.76 MHz、LMX 491.52 MHz。RF PL 153.6 MHz，PS AXI 100 MHz |
| OFDM | 8 點 FFT/IFFT，4 樣本 CP，±1、±2 子載波 QPSK，基頻 19.2 MS/s |
| 同步 | 定幅標記、前導訓練，以及每組 8 個資料符號之間的通道更新 |

RF 參考時脈依板上 PYNQ 設定初始化。 [RFSoC 4x2 PYNQ clock initialization](https://github.com/Xilinx/RFSoC-PYNQ/blob/master/boards/RFSoC4x2/base/base.py)

| PS 位址 | 功能 |
| --- | --- |
| `0xA0000000` | A 使用的原圖 BRAM，512 KiB，使用前 307,200 bytes |
| `0xA0080000` | B 使用的接收 BRAM，512 KiB，前 307,200 bytes 為像素，接續 300 bytes 為封包品質 |
| `0xA0100000` | 控制與狀態 GPIO |
| `0xA0110000` | 接收幅度及封包計數。A 記重傳，B 記收到的重複包 |
| `0xA0120000` | PS 端可讀的 RF 時脈狀態與 RF 域控制字回讀 |
| `0xA0140000` | RFDC 暫存器，256 KiB |

控制字的 bit 0 為 reset，bit 1 為傳送開始，bit 2 為 pilot，bit 3 選擇角色（0 = A 傳送，1 = B 接收）。bits 15:4 是振幅除以 4，bits 31:16 是本板的接收門檻。B 在解除 reset 後自動進入接收。

狀態字的 bit 0 代表 A 已完成全部封包的傳送嘗試，或 B 的所有封包位置都有資料。bit 1 代表失敗，bit 2 代表 A 正在傳輸或 B 已啟用接收，bit 3 代表 B 的所有封包位置都有資料。B 的已收狀態會計入品質碼 1 及 2。bits 4 和 5 分別為串流與位址故障。bits 24:6 在 A 回報重試耗盡的包數，在 B 回報已接收的位置共涵蓋多少 bytes。bits 31:25 為版本識別 `0x3f`。板端程式會核對發布介面 `ofdm_two_board_v2` 和 bit/HWH 雜湊，並等待 RF 域回讀控制字。

## 驗證紀錄

ZIP 中的 `verification/` 提供數位 I/Q 模擬和 Vivado 實作報告。上板後使用兩板的 `result.json` 與 `verify` 產出的合併報告記錄實體傳輸。
