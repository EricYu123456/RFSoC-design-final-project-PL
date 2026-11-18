# 雙板 RFSoC 4×2 OFDM 圖片收發（32 子載波）

板 A 傳送 640 × 480、8-bit 灰階圖，板 B 接收並透過另一條同軸線回送 RF ACK。兩塊板使用同一套 Overlay，各自設定 RF 參考時脈。圖片分成 300 包，每包 1,024 bytes。資料與 ACK 都經過 QPSK-OFDM 和 CRC-32。每包先傳一次，未收到有效 ACK 時最多重傳 10 次，耗盡後仍繼續下一包。

此版本的 RF 波形與原本 4 子載波版本不相容；兩塊板都必須使用本版本的 Overlay。

## 快速上板

1. 下載並將 [上板 ZIP](board/ofdm_two_board_32subcarriers_rfsoc4x2.zip) 解壓到兩塊 RFSoC 4×2 的 PYNQ 資料夾。
2. 用兩條 50 Ω SMA 同軸線交叉接 **A 的 DAC_A → B 的 ADC_A** 和 **B 的 DAC_A → A 的 ADC_A**。每條線先串接 10 dB 衰減器。
3. 在 B 的 Jupyter terminal 執行 `sudo -E python3 run_on_board.py receive`。等 B 顯示 `RX_WAITING_FOR_PILOT`，再在 A 執行 `sudo -E python3 run_on_board.py send`。
4. 兩板會自動交換 pilot，分別量測接收門檻。看到 B 顯示 `RX_ARMED` 才在 A 按 Enter 傳圖。等 A 顯示 `TX_DONE`，再回 B 按 Enter 儲存接收圖與封包品質。
5. 將 A 的 `tx_results/` 和 B 的 `rx_results/` 複製到同一台電腦，執行 `python run_on_board.py verify --tx-dir tx_results --rx-dir rx_results`。完整無誤的圖會得到 `passed: true`。報告也會列出錯包及缺包的編號。

B 會保留 CRC 正確的資料，若該包只有完整且標頭驗證正確的錯包，就保留最近一次的內容。完全沒有可用資料的封包位置填 0（黑色）。收圖尺寸固定為 640 × 480。

完整接線、PYNQ 操作、圖檔位置、換圖和除錯方式見 [雙板上板說明](docs/rfsoc4x2_rf_bringup.md)。實體雙板驗收需在接線後執行上述流程。

## PHY 參數

| 項目 | 設定 |
| --- | --- |
| 基頻 | 複數 I/Q，signed 16-bit RFDC 介面 |
| IFFT／FFT | 64 點定點 radix-2 |
| 子載波 | ±1 至 ±16 共 32 個；DC 與其餘 bin 為 0。QPSK 每個資料符號載 64 bits（8 bytes） |
| 循環字首 | 16 個樣本，每個符號共 80 個樣本 |
| PL 時脈及基頻取樣 | 153.6 MHz。每個 OFDM 基頻樣本保持 8 拍，有效取樣率 19.2 MS/s |
| 子載波間距 | 300 kHz，最外側子載波為中心頻率 ±4.8 MHz |
| 符號率 | 240 ksymbol/s。包含 CP 的調變毛速率為 15.36 Mbit/s；計入每兩個資料符號的一個訓練符號後為 10.24 Mbit/s。另計標記、填補、封包與 ACK 開銷 |
| RF | DAC NCO +500 MHz，ADC NCO −500 MHz，RFDC 取樣率 4.9152 GS/s |

每個 OFDM 封包以定幅標記和已知訓練符號開頭。接收器依標記找封包起點，再用訓練符號估計各子載波的複數通道。每組最多 2 個資料符號之前插入已知訓練符號，以更新兩板獨立時基造成的相位變化。各封包重新同步。資料與 ACK 最後一個 OFDM 符號的空位補 0；接收器按原封包長度取出有效 bytes。雙向數位 I/Q 模擬包含正向 +10 kHz 頻偏搭配 +20 ppm 取樣時基誤差，以及 −10 kHz 搭配 −20 ppm 的情境。回程頻偏方向相反。

## 模擬與重建

在本目錄使用 Vivado 2024.2。先執行包含 300 包全圖傳送的 XSim，再建置硬體。建置會核對全圖模擬結果、接收 BRAM 資料及模擬所用原始檔的雜湊。

```powershell
python scripts/test_rf.py --full --vivado-home D:\Vivado\2024.2
python scripts/build_rf.py --vivado-home D:\Vivado\2024.2
```

[模擬報告](reports/rf_simulation/result.json) 記錄各案例及逐 byte 驗證結果。布線後時序、DRC、CDC 及匯流排偏移檢查通過後，建置輸出 `board/output/` 和上板 ZIP。ZIP 內含同一組 `ofdm_two_board_32subcarriers.bit` 與 `ofdm_two_board_32subcarriers.hwh`、PYNQ runner、Notebook、測試圖及 `verification/` 驗證紀錄。

也可以用 Icarus Verilog 只驗證 TX 頻譜、循環字首與封包尾端填補：

```powershell
New-Item -ItemType Directory -Force build/spectrum | Out-Null
iverilog -g2012 -I rtl -s tb_ofdm_spectrum -o build/spectrum/spectrum.vvp board/rtl/ofdm_fft64.v board/rtl/ofdm_tx.v sim/tb_ofdm_spectrum.sv
vvp build/spectrum/spectrum.vvp
```
