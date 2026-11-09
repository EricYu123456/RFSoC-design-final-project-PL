# RFSoC 4×2 OFDM 圖片收發

RFSoC 4×2 透過 **DAC_A → ADC_A** 的同軸線迴路傳送 640×480、8-bit 灰階圖。圖片分成 300 包，每包 1,024 bytes。資料和 ACK 都會通過 OFDM 實體層。CRC-32 用於驗證封包，逾時會重傳，重複資料包會再回 ACK。

## 上板

1. 將 [上板 ZIP](board/ofdm_rf_rfsoc4x2.zip) 解壓到板上同一個 Jupyter 資料夾。
2. 用同軸線接 **DAC_A 到 ADC_A**，第一次測試串接 10 dB 衰減器。
3. 在 RFSoC 4×2 的 PYNQ JupyterLab 開啟 `run_on_board.ipynb`，由上到下執行。Notebook 會設定 RF 時脈、下載 Overlay、校正 pilot、傳圖及顯示前後對照。
4. 查看 `received/result.json`。整張圖正確時，`passed` 為 `true` 且 `mismatched_bytes` 為 0。

更換圖片、使用 CLI 和檢查各階段的方法見 [上板說明](docs/rfsoc4x2_rf_bringup.md)。

RFSoC 4×2 的 DAC_A → ADC_A 實測收回 307,200 bytes，0 bytes 不同、0 次重傳。三輪對照測試的傳輸時間中位數為 0.215 秒，有效圖片吞吐量 11.43 Mbit/s，為 OOK 的 5.03 倍。[對照測試](reports/board_comparison.json) · [板端結果](reports/physical_board/result.json) · [接收圖片](reports/physical_board/received.png)

## PHY 參數

| 項目 | 設定 |
| --- | --- |
| 基頻 | 複數 I/Q，signed 16-bit RFDC 介面 |
| IFFT／DFT | 8 點定點，僅計算有效子載波 |
| 子載波 | k = +1、+2、−2、−1，各承載 2 bits QPSK，合計每個 symbol 1 byte |
| 循環字首 | 4 個樣本，每個 symbol 共 12 個樣本 |
| PL 時脈及取樣 | 153.6 MHz，每拍 2 組 RFDC 複數樣本。每個 OFDM 基頻樣本保持 8 拍，有效取樣率 19.2 MS/s |
| 子載波間距 | 2.4 MHz |
| 資料 symbol 速率 | 1.6 M symbol/s，調變毛速率 12.8 Mbit/s，包含 CP，未扣除訓練、封包及 ACK 開銷 |
| RF | DAC 上變頻 +500 MHz，ADC 下變頻 −500 MHz，RFDC 取樣率 4.9152 GS/s |

TX 先送 24 個基頻樣本長度的等幅起始訊號，再送一個帶 CP 的訓練 symbol，最後連續送封包。RX 依 ADC 幅度找起始點，在 CP 內選定擷取相位。訓練 symbol 的四個有效頻點皆為已知 BPSK +1，RX 以各子載波的通道估測值共軛乘回資料，再用 I/Q 正負號判定 QPSK 位元。封包收完後重新搜尋下一個起始訊號。這個設計以同板共時基的 DAC／ADC 迴路為目標，封包期間使用固定取樣節奏。

## 模擬與重建

在本目錄使用 Vivado 2024.2 執行下列命令。第一個命令包含全圖驗證，第二個命令檢查目前原始碼的雜湊是否符合全圖通過的結果，才啟動 Vivado 硬體建置。

```powershell
python scripts/test_rf.py --full --vivado-home D:\Vivado\2024.2
python scripts/build_rf.py --vivado-home D:\Vivado\2024.2
```

XSim 從來源 BRAM 讀圖，在 RFDC 數位串流介面模擬同軸通道，驗證相位旋轉、衰減、延遲、回波、ACK 損壞／遺失、斷線，以及整張圖傳送。RTL testbench 和 Python 分別比對接收 BRAM 的資料。結果及每個案例的 log 位於 [模擬報告](reports/rf_simulation/result.json)。

全圖測試通過 300 包、307,200 bytes，逐 byte 比對和 SHA-256 皆一致。最後一包 ACK 遺失後重傳一次，接收端仍恰好寫入 307,200 bytes。實體板驗證請在板上執行 Notebook，並檢查 `received/result.json`。

建置會先驗證布線後時序、DRC 與 CDC，再輸出 `board/output/` 和上板 ZIP。ZIP 內含 `ofdm_rf.bit`、`ofdm_rf.hwh`、Notebook、測試圖和驗證紀錄。
