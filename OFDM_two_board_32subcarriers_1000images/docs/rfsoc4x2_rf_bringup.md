# 四天線 500 MHz 雙板上板操作

兩塊 RFSoC 4×2 各解壓同一份發布包。A 的 DAC_A、B 的 ADC_A 各接一根天線作為資料路徑。B 的 DAC_A、A 的 ADC_A 各接一根作為 ACK 路徑。四根天線使用先前成功收發的近距離與方向，每一對收發天線保持相同極化。此配置使用板上 DAC、既有天線和同頻分時，請依實驗室適用的場域用頻條件操作。板間的資料和所有控制回覆都走天線。

## 啟動兩塊板

在 B 的解壓資料夾先執行：

```bash
sudo -E python3 run_on_board.py receive
```

等 B 顯示 `RX_WAITING_FOR_PILOT`，在 A 的解壓資料夾執行：

```bash
sudo -E python3 run_on_board.py send --input-dir data --count 1000
```

兩板分開量靜音和對端 pilot，設定各自的偵測門檻。每次批次各載入 overlay、交換 pilot 一次。接著自行逐張換圖。請等 A 顯示 `TX_DONE`、B 顯示 `RX_DONE`。在已能穩定傳一張的距離，先用 `--count 1`，再用 10 和 1000。也可用 `send --input photo.png` 傳單張圖或 307,200 bytes 的 raw 檔。`--amplitude` 可在每板獨立調整，預設 4096。資料振幅由 A 設定，ACK 振幅由 B 設定。

每板將像素、逐張記錄寫到 `tx_results` 或 `rx_results`，內層依批次 ID 及圖號分目錄。B 的每張圖都有 received.raw、received.png 及 packet_quality.raw。缺包位置為黑色。識別為 CRC 錯誤的像素保留最近一次完整 payload。A 的 source.raw 來自上傳並讀回核對的原始圖。保留舊的批次摘要時可另外指定 `--output`。

將兩份結果目錄與 runner 複製到同一台電腦：

```bash
python run_on_board.py verify --tx-dir tx_results --rx-dir rx_results --output two_board_result.json
```

驗證會檢查兩板的 overlay、批次 ID、所有圖號、各檔雜湊、像素及封包品質。只有所有圖逐 byte 相同且全包 CRC 正確才會得到 passed=true。查看個別圖的 missing_packet_ids 和 crc_bad_packet_ids 定位錯誤。A 的重試及未取得確認數與 B 實際收圖品質分開記錄。

## 空中鏈路定位

先看兩端 bringup.log 的 pilot 與靜音幅度，逐一確認兩對天線，從先前成功的位置開始調整距離。未收到 B 的 pilot 要檢查回程天線與 B 的振幅。A 持續重問而 B 收圖完整時，優先量回程訊號。每張的 rx_peak 是本板解碼路徑的數位峰值，adc_overrange_latched 讀取 RFDC 轉換器黏滯過載旗標，包含本板發射時的洩漏。若讀值過強或出現 ADC 過載，降低該方向的發射振幅。每批的 result.json 記錄本機實際 mixer 設定、PLL/FIFO 狀態與校正結果。保留相同距離及極化作兩次測試，才可比較 PER 與實際耗時。數位 I/Q 模擬結果隨附於 verification，空中鏈路性能以兩板實測為準。

