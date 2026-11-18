# RFSoC 4×2 灰階圖片收發

在 RFSoC 4×2 上透過 DAC_A 和 ADC_A 傳送 640×480、8-bit 灰階圖。各版本使用每包 1,024 bytes、CRC-32、ACK 與逾時重傳，並各自提供 RTL、模擬、Vivado 建置腳本和 PYNQ Notebook。

| 版本 | 調變 | 使用說明 | 上板包 |
| --- | --- | --- | --- |
| [OOK/](OOK/) | OOK，毛速率 2.4 Mbit/s | [OOK README](OOK/README.md) | [OOK ZIP](OOK/board/ook_rf_rfsoc4x2.zip) |
| [OFDM/](OFDM/) | 8 點 OFDM、4 個 QPSK 子載波、4 樣本 CP，毛速率 12.8 Mbit/s | [OFDM README](OFDM/README.md) | [OFDM ZIP](OFDM/board/ofdm_rf_rfsoc4x2.zip) |

上表兩個版本使用單板同軸迴路。雙板版本使用兩條同軸線分別傳送圖片和回覆 ACK，兩塊板須使用同一版本的上板包。

| 雙板版本 | 調變 | 使用說明 | 上板包 |
| --- | --- | --- | --- |
| [OFDM_two_board/](OFDM_two_board/) | 8 點 OFDM、4 個 QPSK 子載波、4 樣本 CP | [操作指南](OFDM_two_board/docs/rfsoc4x2_rf_bringup.md) | [上板 ZIP](OFDM_two_board/board/ofdm_two_board_rfsoc4x2.zip) |
| [OFDM_two_board_32subcarriers/](OFDM_two_board_32subcarriers/) | 64 點 OFDM、32 個 QPSK 子載波、16 樣本 CP | [操作指南](OFDM_two_board_32subcarriers/docs/rfsoc4x2_rf_bringup.md) | [上板 ZIP](OFDM_two_board_32subcarriers/board/ofdm_two_board_32subcarriers_rfsoc4x2.zip) |

## 單板上板實測

同一塊 RFSoC 4×2、同一張 307,200-byte 灰階圖、相同 DAC 振幅 4096，以 500 MHz 載波各傳三次。計時涵蓋封包傳輸及 ACK，Overlay 載入、校正、BRAM 上傳與結果比對分開計時。

| 指標 | OOK | OFDM |
| --- | ---: | ---: |
| 傳輸時間中位數 | 1.081 秒 | 0.215 秒 |
| 有效圖片吞吐量 | 2.27 Mbit/s | 11.43 Mbit/s |
| 每次錯誤 bytes／重傳次數 | 0／0 | 0／0 |

OFDM 有效吞吐量是 OOK 的 **5.03 倍**（約增加 **403%**），傳輸時間縮短 **80.1%**。[實測紀錄](OFDM/reports/board_comparison.json) · [OFDM 全圖模擬](OFDM/reports/rf_simulation/result.json)

## 單板執行步驟

1. 將對應的上板 ZIP 解壓到板上獨立的 Jupyter 資料夾。
2. 用同軸線將 **DAC_A 接到 ADC_A**，第一次測試串接 10 dB 衰減器。
3. 在板上 PYNQ JupyterLab 開啟該資料夾的 `run_on_board.ipynb`，由上到下執行。
4. 查看 `received/result.json` 的 `passed`、`mismatched_bytes` 與 `retransmissions`。

模擬與重建請從各版本目錄執行，依照各自 README 的 Vivado 2024.2 步驟操作。OFDM 建置腳本會先驗證全圖 XSim 報告與原始碼雜湊。
