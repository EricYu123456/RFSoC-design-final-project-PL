# XSim 驗證結果

工具：Vivado / XSim 2024.2。下表為實際模擬結果。

persistent_error 的預期結果是達重試上限、回報傳輸失敗；測試通過不代表圖片傳送成功。

| 測試 | 包數 | 測試 | 圖片傳送 | 資料 CRC 錯誤 | ACK CRC 錯誤 | 重傳 | 重複包 | 像素差異 | 時間 ms | 有效 Mbit/s |
| --- | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| [ack_corrupt_3](ack_corrupt_3/result.json) | 3 | PASS | 成功 | 0 | 1 | 1 | 1 | 0 | 5.46788 | 4.49461 |
| [ack_drop_3](ack_drop_3/result.json) | 3 | PASS | 成功 | 0 | 0 | 1 | 1 | 0 | 5.46788 | 4.49461 |
| [backpressure_3](backpressure_3/result.json) | 3 | PASS | 成功 | 0 | 0 | 0 | 0 | 0 | 4.12420 | 5.95897 |
| [baseline_3](baseline_3/result.json) | 3 | PASS | 成功 | 0 | 0 | 0 | 0 | 0 | 4.11796 | 5.96800 |
| [baseline_300](baseline_300/result.json) | 300 | PASS | 成功 | 0 | 0 | 0 | 0 | 0 | 412.88500 | 5.95226 |
| [data_error_3](data_error_3/result.json) | 3 | PASS | 成功 | 1 | 0 | 1 | 0 | 0 | 5.48836 | 4.47784 |
| [last_ack_drop_3](last_ack_drop_3/result.json) | 3 | PASS | 成功 | 0 | 0 | 1 | 1 | 0 | 5.46788 | 4.49461 |
| [mixed_300](mixed_300/result.json) | 300 | PASS | 成功 | 1 | 1 | 4 | 3 | 0 | 418.30516 | 5.87514 |
| [mixed_4](mixed_4/result.json) | 4 | PASS | 成功 | 1 | 1 | 4 | 3 | 0 | 10.91444 | 3.00226 |
| [persistent_error_1](persistent_error_1/result.json) | 1 | PASS | 失敗（預期） | 11 | 0 | 10 | 0 | — | 15.07362 | 0.00000 |
| [wrong_ack_3](wrong_ack_3/result.json) | 3 | PASS | 成功 | 0 | 0 | 1 | 1 | 0 | 5.46788 | 4.49461 |

有效吞吐率只計算成功傳送的圖片像素；包含 ACK、等待與重傳花費的時間。

每個測試目錄包含 result.json、事件紀錄 events.csv，以及實際取樣的波形圖 waveforms.png。
完整 300 包測試另有 source.png、received.png、difference.png、comparison.png；comparison.png 由左至右為原圖、接收圖、絕對差異圖。

通道採理想同步、固定增益、無雜訊。錯誤測試驗證 CRC 與 ARQ，不代表實際 RF 通道的 BER–SNR 表現。
