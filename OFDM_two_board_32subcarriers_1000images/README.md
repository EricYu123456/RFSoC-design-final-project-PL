# RFSoC 4×2 雙板天線 OFDM 圖片傳輸

本專案以四根天線在 500 MHz 同頻分時傳送 1000 張 640×480、8-bit 灰階圖。A 使用 DAC_A 傳圖片、ADC_A 收回覆，B 使用 ADC_A 收圖片、DAC_A 回覆。兩板使用同一份 overlay。

## GitHub 上板包與模擬資料

[下載上板包（約 183 MB）](https://github.com/EricYu123456/RFSoC-design-final-project-PL/raw/refs/heads/main/OFDM_two_board_32subcarriers_1000images/board/ofdm_air8_rfsoc4x2.zip)。透過 Git 取得時，請安裝 Git LFS，並在儲存庫根目錄執行 `git lfs pull --include="OFDM_two_board_32subcarriers_1000images/board/ofdm_air8_rfsoc4x2.zip"`。

上板包含 bitstream、HWH、板端程式、1,000 張原始輸入影像與建置報告。要從 GitHub 重新模擬，請將 ZIP 內的 `data/` 解壓到儲存庫根目錄（與本專案資料夾同一層），再依下節執行。批次模擬會重建 `build/batch/received.raw`，供建置檢查使用。

## 模擬與建置

在專案根目錄執行。Python 使用 Pillow。波形模擬與硬體建置使用 Vivado 2024.2。全批模擬使用 Verilator 5、C++20 編譯器與同步 BRAM 模型。Windows 本機的 Verilator 及標頭在 `build/tools/mingw64`，編譯器為 MinGW g++。Linux 可從 PATH 取用 Verilator 與 make。

```powershell
python scripts/test_batch.py
python scripts/test_iq.py
python scripts/build_rf.py --check-simulation
python scripts/build_rf.py
```

批次測試從上一層 `data` 解碼 `coco_gray_0000.png` 至 `coco_gray_0999.png`。兩個實際板端 RTL 實例經加速的 byte PHY 交換載荷及控制包。測試從 B 的 BRAM 位址與寫入通道重建輸出，檢查全部像素、品質碼、批次描述與 38,000 個正常 block ACK。完整收圖在 `build/batch/received.raw`，每 307,200 bytes 一張。執行 `python scripts/export_received.py` 可從此 dump 匯出 1000 張接收 PNG 到 `reports/batch/received_png/`。逐圖 SHA-256 在 `reports/batch/result.json`。

XSim 使用不同時脈的兩端、實際整數 FFT/IFFT、訓練與解調。短測比較視窗 1、4、8 和回程 BPSK/QPSK。長測連續傳來源的前兩張全圖。模型含反射、頻偏、取樣偏差、近端發射洩漏、接收恢復尾波、雜訊及間歇干擾。波形測試逐 byte 檢查兩張圖，並監看同時發射。I/Q 模型從 RFDC 複數基頻介面起算。場地距離、天線頻率響應、空中輻射功率與可用鏈路餘裕須用實體兩板量測。

建置核對本版模擬原始碼、測試結果、接收 BRAM dump 與每一張 PNG 雜湊。實作通過 timing、DRC、CDC、bus-skew 與 HWH 檢查後輸出 `board/ofdm_air8_rfsoc4x2.zip`。操作方式見 [上板說明](docs/rfsoc4x2_rf_bringup.md)。

## 通訊協定

19.2 MS/s 基頻，64 點 FFT、16 樣本 CP、32 個有效子載波。每個 burst 用定幅標記和兩個相同訓練符號起始，之後每兩個資料符號插入一次訓練。接收器檢查兩次訓練頻域相位的一致性，依每個子載波的通道變化補償後續符號。A 的資料與控制使用 QPSK，B 的短回覆使用對角 BPSK。同板發射期間遮蔽解碼器，發射結束後再保留 255 個 RF clocks 的接收恢復時間。

A 以 OPEN 和 READY 確認 B 已釋放該圖的空間。每包 1024 bytes、每張 300 包。A 連發最多 8 包，之後送 POLL 並靜音，B 只在收到有效請求後回 SACK。每次 burst 前間隔 2304 個 RF clocks（15 μs），接收端檢查批次 ID、圖號、包號與 CRC。bitmap 中只標記全包 CRC 正確的位置。ACK 遺失時 A 先重送 POLL，有缺包則按 bitmap 補傳。每個視窗最多 10 次重試，包號 296 至 299 共用最後一個回覆。FIN/CLOSED 結束每張，重複 FIN 可取得先前結果。正常狀況每圖有 38 個 SACK，另有 READY 與 CLOSED。

source/sink BRAM、PYNQ GPIO 與 RFDC 使用原位址。PS 在 source BRAM 的 0x7fff0 寫入 little-endian 32-bit 批次 ID，下一字高 16 bits 是總張數、低 16 bits 是圖號。B 在相同 sink 偏移回報描述。sink 前 307,200 bytes 為像素，後面 300 bytes 是逐包品質（2 正確、1 整包到達但 payload CRC 錯誤、0 缺包）。B 按 START 釋放已讀出的圖。數位振幅沿用原控制字的單位。模擬中斷以及實體空中測試的結果各自記錄。


## 接續已通過的模擬

在可為 xczu48dr 合成與實作的 Vivado 2024.2 環境中，從本專案執行 `python scripts/build_rf.py --vivado-home D:\Vivado\2024.2` 即可重新建置。工具會核對已完成的模擬證據。若使用學校的浮動授權，在啟動建置的同一個 shell 設定學校提供的 XILINXD_LICENSE_FILE。移到其他電腦時須一併保留 reports/batch/result.json、reports/iq/result.json、build/batch/received.raw、全部專案原始碼及上一層的 data 圖片資料夾。也可用 AIR8_SOURCE_DIR 指定已驗證 PNG 的實際位置。
