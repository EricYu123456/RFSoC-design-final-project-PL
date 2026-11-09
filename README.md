# RFSoC 4x2 OOK 圖片收發

## 使用 Notebook 上板

1. 下載 [RFSoC 4x2 上板 ZIP](board/ook_rf_rfsoc4x2.zip)，將整包解壓到板上的同一個 Jupyter 資料夾。包內包含 Notebook、Python、bitstream、HWH、`manifest.json` 和測試圖。
2. 用同軸線將 **DAC_A（DAC0）接到 ADC_A（ADC0）**，初次測試串接 10 dB 衰減器。
3. 在板上的 PYNQ JupyterLab 開啟 `run_on_board.ipynb`，從第一格開始逐格執行。Notebook 會核對檔案、設定時脈、下載 Overlay 並檢查 PS 與 RFDC，再校正 RF 幅度、收發圖片和顯示對照結果。
4. 在 `received/result.json` 查看實體收發結果。`passed: true` 且 `mismatched_bytes: 0` 表示接收圖和原圖逐位元組相同。

目前上板包的介面版本為 `ps_clock_v2`。接線、執行階段與錯誤排查請見 [RFSoC 4x2 上板說明](docs/rfsoc4x2_rf_bringup.md)。

需要重建硬體時，在專案根目錄執行 `python scripts/build_rf.py`。建置後的 Vivado 專案位於 `build/rf/vivado/ook_rf.xpr`。

## 第一週理想通道模擬

這個專案使用 **Vivado / XSim 2024.2**，模擬一張 640×480、8-bit 灰階圖片的收發。圖片資料與 ACK 都會通過 OOK 調變、理想通道、OOK 解調，再進行 CRC 檢查。

一次只傳一包。收到有效 ACK 才傳下一包；沒有收到有效 ACK，就等待逾時後重傳。最後用 RTL testbench 與 Python 各比對一次圖片像素，確認 307,200 個 bytes 完全相同。

- [淺顯版規格](week1_ook_image_link_spec.md)
- [實際模擬結果與量測數據](reports/summary.md)
- [預設灰階測試圖](assets/source.png)

## 1. 如何開始

使用 Vivado / XSim 2024.2。在 PowerShell 進入專案根目錄並安裝 Python 相依套件。

```powershell
python -m pip install -r requirements.txt
```

### 快速檢查整套功能

```powershell
python scripts/run.py --suite smoke
```

這會執行九個短測試，包括資料錯誤、ACK 損壞或遺失、錯誤 ACK 編號、介面暫停與重試上限。每包仍是 1,024 bytes，只是先少傳幾包，方便快速確認功能。

第一次執行會自動建立 Vivado 專案、產生 IP、編譯及執行模擬。每次測試通過，畫面會顯示 `PASS`，並更新 `reports/summary.md`。

### 傳完整圖片

```powershell
python scripts/run.py --case baseline --packets 300
python scripts/run.py --case mixed --packets 300
```

第一個命令是正常通道；第二個命令會分別製造一次資料錯誤、ACK 損壞、ACK 遺失，以及最後一包 ACK 遺失。預期混合測試發生四次重傳，最後仍得到完全相同的圖片。

完整圖片約需四千萬個 sample clock，電腦執行模擬需要數分鐘以上。程式每收到 25 包會顯示一次進度。畫面中的模擬時間與電腦實際等待時間不同。

若要一次重跑短測試與兩個完整圖片測試：

```powershell
python scripts/run.py --suite full
```

## 2. 如何換成自己的照片

```powershell
python scripts/run.py --input 'C:\path\to\photo.jpg' --case baseline --packets 300
```

Python 會把照片轉成 640×480 灰階圖片。後續執行若不指定 `--input`，就會繼續使用這張已轉換的圖片。

若要恢復預設測試圖：

```powershell
python scripts/prepare_image.py
```

原圖比對的對象是轉換後的像素資料，不是 JPEG 或 PNG 檔案本身。預設圖片是程式產生的灰階圖，包含漸層、圓形及變化較大的圖樣，方便觀察資料排列是否正確。

## 3. 圖片怎麼送進去？

```text
Python：照片 → 灰階像素 → assets/image.mem
                              ↓
Testbench：用 $readmemh 載入，透過 data / valid / ready 逐 byte 餵給 TX
                              ↓
TX：先存滿一包 1,024 bytes → Header + CRC → byte 轉 bit → OOK
                              ↓
RX：能量判決 → 找 Sync word → 收封包 → CRC 檢查 → 保存像素
                              ↓
RX：產生 ACK + CRC → OOK → TX 解調 ACK → 決定下一包或重傳
```

完整圖片存在 testbench 中；發送端與接收端各使用一個 1,024-byte BRAM 暫存封包。重傳時直接重讀發送端 BRAM，不向 testbench 索取新的圖片資料。

接收端只有共用的時脈與 bit 時間基準，必須自行從收到的 bit stream 找封包；沒有直接從 TX 取得封包編號或「這一包成功」的捷徑。

## 4. 結果在哪裡？

每次測試有自己的資料夾，例如 `reports/baseline_300/`、`reports/mixed_300/`。

| 檔案 | 內容 |
| --- | --- |
| `result.json` | 接收包數、CRC 錯誤、重傳、重複包、時間、有效吞吐率、SHA-256 與 Python 像素比對結果 |
| `events.csv` | 每次資料發送、CRC 檢查、像素保存、ACK 發送與逾時的時脈紀錄 |
| `received.raw` | 實際收到並保存的像素 bytes |
| `waveforms.png` | 正向資料與反向 ACK 的 OOK bit、實際波形與接收能量 |
| `progress.png` | 已保存的不同封包數，以及累積重傳次數 |
| `source.png`、`received.png` | 完整圖片測試的原圖與接收圖 |
| `difference.png` | 完整圖片的絕對差異圖；完全相同時是全黑 |
| `comparison.png` | 左：原圖；中：接收圖；右：絕對差異圖 |
| `console.log`、`xsim.log` | 模擬紀錄；出錯時先看這兩個檔案 |

完整圖片的 PNG 只會在成功完成 300 包時產生。短測試只驗證圖片前幾包，不能當作整張圖片驗收。

`forward_demod_errors`、`reverse_demod_errors` 比較的是「實際送入通道的 bit」與解調結果。資料或 ACK 在 OOK 之前被刻意反轉時，會由封包 CRC 檢出；它不應被誤算成 OOK 解調器的錯誤。ACK 波形被消除時，接收端應正確解出零。

有效吞吐率從第一個資料封包開始發送，計算到最後一個有效 ACK 被確認，只計入不重複的圖片像素資料。

## 5. 如何看 Vivado 波形？

先執行一個短測試，並加上 `--waves`：

```powershell
python scripts/run.py --case mixed --packets 4 --waves
```

`reports/mixed_4/waves.wdb` 會記錄主要訊號。將下列範例路徑換成你本機的專案路徑，再到 Vivado 的 Tcl Console 執行。

```tcl
source {C:/path/to/OOK/scripts/open_waves.tcl}
```

這個測試包含四種錯誤，適合展示重傳流程。

| 訊號 | 觀察重點 |
| --- | --- |
| `data_wave`、`ack_wave` | 兩個方向的 OOK 波形 |
| `data_energy`、`ack_energy` | 每個 bit 的接收能量；門檻為 1,073,741,824 |
| `data_bit`、`ack_bit` | 能量判決後的 bit |
| `sender.packet_id` | 目前傳哪一包；重傳時維持同一編號 |
| `sender.retry_count` | 目前這一包重傳了幾次 |
| `sender.timer` | 等待 ACK 的逾時計時 |
| `receiver.unique_packets` | 已保存的不同封包數；重複包不增加 |

也可以開啟 `build/vivado/ook_image_link.xpr`。專案的模擬預設為正常傳送 3 包，已設定好圖片與輸出路徑；在 Flow Navigator 選 **Run Simulation → Run Behavioral Simulation** 即可。

## 6. 原始碼與 IP 分工

| 路徑 | 功能 |
| --- | --- |
| `rtl/ook_tx.v` | byte 轉 bit、固定 16 samples/bit、DDS 載波開關 |
| `rtl/ook_rx.v` | Multiplier 平方、36-bit 能量累加與門檻判決 |
| `rtl/packet_rx.v` | 搜尋 Sync word、固定長度解析、CRC 檢查 |
| `rtl/crc32_functions.vh` | CRC-32/ISO-HDLC 的 byte 更新函式 |
| `rtl/image_sender.v` | 一包 BRAM、資料封包組裝、等待 ACK、逾時及重傳 |
| `rtl/image_receiver.v` | 接收 BRAM、保存資料、辨認重複包、產生 OOK ACK |
| `rtl/image_link.v` | 串接 A、B 兩個邏輯角色；保留測試用錯誤注入點 |
| `rtl/image_link_synth.v` | 合成用的理想自收發頂層；關閉錯誤注入 |
| `sim/tb_image_link.sv` | 圖片來源、理想通道、錯誤測試、逐 byte 檢查與事件紀錄 |
| `scripts/create_project.tcl` | 建立 Vivado 專案與三種實際 IP |
| `scripts/run.py` | 建立、編譯、執行及整理測試結果 |

實際使用的 IP 為 DDS Compiler 6.0、Multiplier 12.0、Block Memory Generator 8.4。正向與反向各有 DDS 與 Multiplier；兩端各有一個封包 BRAM。專案使用 ZU48DR、FFVG1517、-2、E 作為合成目標。

## 7. 其他測試與合成

可指定的案例：

| `--case` | 製造的情況 | 預期 |
| --- | --- | --- |
| `baseline` | 沒有錯誤 | 不重傳，資料相同 |
| `data_error` | 某包第一次 Payload 反轉一個 bit | 重傳一次，資料相同 |
| `ack_corrupt` | 某個 ACK 第一次編號反轉一個 bit | ACK CRC 失敗，資料重傳一次 |
| `ack_drop` | 某個 ACK 第一次的波形設為零 | 逾時後重傳，重複包不重複保存 |
| `last_ack_drop` | 最後一包第一次 ACK 遺失 | RX 收齊後仍能再次回 ACK |
| `wrong_ack` | 第一個 ACK 編號錯誤，但 CRC 正確 | TX 忽略它，逾時後重傳 |
| `persistent_error` | 同一包每次都被破壞 | 最多嘗試 11 次，回報失敗 |
| `backpressure` | 圖片來源與儲存端間歇暫停 | byte 握手正常，不破壞 OOK 的 bit 長度 |
| `mixed` | 資料錯誤、ACK 損壞、ACK 遺失、最後 ACK 遺失各一次 | 重傳四次，資料相同；至少傳四包 |

`persistent_error` 顯示測試 PASS，代表「正確回報傳送失敗」，不是圖片傳送成功。請同時看 `result.json` 的 `test_passed` 與 `link_success`。

跑一次合成與資源報告：

本機授權已驗證可用，完整收發器已成功合成。LUT 使用 1,408、FF 使用 1,367、DSP 使用 2、Block RAM Tile 使用 16。詳細數據與尚未完成的時序項目見 [合成狀態](reports/synthesis/status.md)。

若遇到 ZU48DR 授權錯誤，請看 [授權設定說明](docs/vivado_license_setup.md) 與 [目前合成狀態](reports/synthesis/status.md)。安裝板卡定義檔不等於已取得合成授權。

```powershell
python scripts/run.py --case baseline --packets 3 --synth
```

`reports/synthesis/` 記錄第一週理想通道設計的合成時序，以及 LUT、FF、DSP 和 BRAM 使用量。RFSoC 4x2 上板版的布線後時序報告收在上板 ZIP 的 `verification/`。

修改 IP 設定或需要重建專案時，使用 `--rebuild`。一般執行會重新檢查編譯；`--skip-build` 僅供 RTL 與 testbench 都沒變更時使用。

這一版採共用時脈、已知 bit 邊界、理想通道。重傳測試證明的是目前模型下的功能正確性；實際 RFDC、通道雜訊與未知同步，後續再接入。
