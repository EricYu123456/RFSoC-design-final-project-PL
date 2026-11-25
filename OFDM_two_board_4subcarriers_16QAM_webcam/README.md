# RFSoC 4×2 雙板攝影機 · 4 子載波 16-QAM

將 `RFSoC4x2_OFDM_4SC_16QAM_Webcam.zip` 分別解壓到 A、B 板的 Jupyter 工作目錄。在各板開啟同名的 `RFSoC4x2_OFDM_Live_Webcam_MethodB.ipynb`。部署包已包含兩板共用的 bitstream、HWH、manifest 與 Python 模組。

1. A 板接 USB 攝影機。建立兩條 500 MHz RF 路徑，A 的 DAC_A 到 B 的 ADC_A 傳影片，B 的 DAC_A 到 A 的 ADC_A 回覆。若使用同軸線，兩條路徑都要依板卡規格加入衰減。
2. B 板設定 `ROLE = "receive"` 並執行所有儲存格。看到 `RX_WAITING_FOR_PILOT` 後，在 A 板設定 `ROLE = "send"` 並執行。兩端均使用 `ofdm_qam16.bit`。
3. 要停止錄影，在 A 板選 **Kernel → Interrupt**。A 會停止取像、送完已取出的區塊並傳送結束標記。B 會自動寫完並關閉 `received_live_webcam/<時間>_receive/live_received.mp4`。若 A 斷線，仍可在 B 板按 Interrupt 完成當下的 MP4。

## 波形與串流

沿用雙板專案的 RFDC、GPIO、BRAM、CRC 與視窗重傳協定。4 個有效子載波位於 8 點 IFFT 的 +1、+2、−2、−1。基頻取樣率為 19.2 MS/s，循環字首為 4 點。資料與雙向控制封包的資料符號均使用 Gray 16-QAM，每符號承載 2 bytes。每 4 個資料符號前插入一個已知訓練符號，由接收端逐子載波估計相位和振幅。

A 的編碼佇列最多暫存 22 個 JPEG，每個 307,200-byte 區塊最多傳 11 幀。B 先檢查 300 個封包的品質和區塊的 SHA-256，再解碼並逐幀寫入 MP4。檔案依發送端的取像時間播放。若跳過影格，接收端在該時段維持上一張已收畫面。每次錄影的摘要會列出成功幀數與丟棄計數。16-bit 硬體區塊編號用盡時會自動換批，影片接續寫入同一檔案。

若要和既有雙板版本比較，兩板的 `BITFILE` 可改指向原 32 子載波部署包內的 `ofdm_air8.bit`，同目錄須有對應的 HWH 與 manifest。Notebook 會顯示該版本的 QPSK 資料與 BPSK 回覆設定。單板回送的 `ofdm_rf.bit` 僅用於原始 Method B。

## 即時預覽與錄影延遲

兩板設 `MODE = "live"`。B 以對齊的 32-bit 讀取取回有效 JPEG 與 SHA-256，釋放 BRAM 後驗證區塊並更新預覽。獨立的錄影執行緒有兩個待寫區塊的緩衝，結束時會排空再關檔。A 只改寫新增資料與上一區塊殘留的尾端，送出的完整 RF 區塊維持零填充。

B 的 `RECORDING_CODEC = "auto"` 在板上用系統 C 編譯器和 FFmpeg 開發函式庫載入加速模組，將原 JPEG 直接封裝為 MP4。無法載入時會使用逐字對齊的 Python 存取與 OpenCV mp4v。若播放器需要 MPEG-4 Part 2，設定 `RECORDING_CODEC = "mp4v"` 即可。摘要列出實際使用的編碼、接收幀數與錄影幀數。

單板以同一批 USB 攝影機 JPEG、每區塊 6 幀重播 240 幀，DONE 到可預覽的平均時間從 359.5 降至 18.3 ms。8 秒的 MJPEG MP4 為 5.79 MB，mp4v 為 1.99 MB。完整比較在 `reports/webcam_latency_single_board.json`。雙板請比較 B 的 `done_to_release`、`rx_service`、`record_queue_wait` 和摘要中的 `delivered_fps`，並檢查 A 的 `rf` 和重傳次數。此次使用原有 bit，每區塊空中傳輸固定 300 個封包。

## 重建與驗證

在本資料夾執行下列命令。硬體建置使用 Vivado 2024.2 與 RFSoC4x2 board part。Windows 可先用 `subst` 將本資料夾映射到一個空的磁碟代號，從該代號的根目錄執行 Vivado，以縮短產生檔的路徑。

```text
vivado -mode batch -source scripts/build_rf.tcl
python scripts/package_webcam.py
```

`python scripts/test_radio.py` 以 Icarus Verilog 執行雙板短測。`python scripts/test_radio.py --full` 以 Verilator 執行兩個完整 BRAM 區塊的 I/Q 測試，包含多徑、頻偏、時脈偏差、丟包和 ACK 重傳。Windows 的完整版可指定 `--verilator-root` 指向既有工具的 `share/verilator`。獨立星座測試 `sim/tb_constellation.sv` 直接對 TX 時域波形做 DFT，逐一檢查每個有效子載波的 16 種星座點。

`python sim/test_webcam.py` 以本機影片模擬相機，覆蓋 notebook 收發入口、中斷與 MP4 解碼。此測試需要 OpenCV 與 NumPy。實際板端另使用既有 PYNQ、xrfdc、xrfclk 與 Jupyter 環境。驗證輸出位於 `reports/`。
