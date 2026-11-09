# RFSoC 4x2 OFDM 圖片收發

這個版本使用 **DAC_A（板上 DAC0）→ 同軸線 → ADC_A（板上 ADC0）**。
資料封包與反向 ACK 以分時方式共用這條實體 RF 路徑；兩個邏輯端點都在同一塊板上。
接收器從實體 ADC 的 I/Q 串流解調 OFDM 封包。

## 上板

1. 使用 RFSoC 4x2 的 PYNQ SD 映像開機，連上板子的 JupyterLab。
2. 將 **DAC_A 接到 ADC_A**。使用 50 Ω SMA 同軸線，串接 10 dB 衰減器作為初始設定。
3. 把新版發布包上傳並解壓到同一個資料夾。保留 `ofdm_rf.bit`、`ofdm_rf.hwh`、`manifest.json`、Python 和 Notebook。
   XSA 保留在 `hardware_description/`，避免 PYNQ 將同名 `.bit` 誤送到 XSA 解析器。
4. 在 PYNQ Jupyter 開啟 `run_on_board.ipynb`。第一次逐格執行。Notebook 解說檔案檢查、時脈、
   Overlay 下載、PS 與 RFDC 檢查、圖片上傳、pilot 校正、收發和結果顯示。

   也可以在該資料夾的 Jupyter terminal 執行。

   ```bash
   sudo -E python3 run_on_board.py --bit ofdm_rf.bit --input image.raw
   ```

程式會設定板上 LMK／LMX，下載 bitstream，檢查 ADC/DAC PLL、上傳圖片，
用短暫的 500 MHz 連續載波量測迴路接收幅度。靜音與載波的幅度中點用作封包起始標記的偵測門檻，接著傳送 300 包。
結束時逐 byte 比對 307,200 bytes，保存 `received.raw`、`received.png`、`result.json`。
只有 `passed: true`、`mismatched_bytes: 0`、收發 SHA-256 相同才代表本次實體上板通過。
程式結束或發生錯誤會關閉 RF 輸出。

`manifest.json` 記錄新版 `ps_clock_v2` 介面與 bit/HWH 的 SHA-256。程式會在下載前核對 bit/HWH、OFDM PHY 和主機介面版本。如果新檔案旁仍有同名 XSA，程式會改從 `.pynq_runtime/`
內的 bit/HWH 配對載入。BRAM 位址同時支援 PYNQ 的 `ip_dict` 與 `mem_dict`。

### PYNQ 驅動介面

控制使用 `overlay.control.channel1.write(value, 0xffffffff)`，狀態從
`overlay.control.channel2.read()` 取得。`telemetry` 的兩個 channel 提供幅度與重傳次數。
`host_state` 提供時脈狀態及 RF 域已接收的命令值，程式等到命令回讀一致才發出下一個命令。
每個 channel 依硬體方向設定為 32-bit `out` 或 `in`。
圖片使用 `overlay.source_ctrl`／`overlay.sink_ctrl` 的公開 `write(offset, bytes)` 與
`read(offset)` 介面，明確以 little endian 組回圖片。程式中的實體位址只用於核對 HWH。

PYNQ 驅動透過 AXI 存取暫存器。所有 PS 可存取的 AXI 從屬介面接到 PS 100 MHz，兩個低有效的 `aux_reset_in` 接至非作用電位。
RF 資料路徑使用獨立的 153.6 MHz 時脈。命令與狀態透過握手跨時脈，BRAM 使用獨立的 A、B 埠時脈。
若 RF 狀態超過 1 ms 沒更新，PS 端仍可讀取 `host_state`，其 ready 位元會清除。
[AMD 重設模組手冊](https://docs.amd.com/v/u/en-US/pg164-proc-sys-reset)
[AMD 跨時脈握手文件](https://docs.amd.com/r/2024.2-English/ug974-vivado-ultrascale-libraries/XPM_CDC_HANDSHAKE)

### 如果執行時 USB／Jupyter 斷線

Notebook 每一格對應一個階段。若使用 terminal，可先確認檔案和 metadata。

```bash
sudo -E python3 run_on_board.py --stop-after metadata
```

看到 `DIAGNOSTIC_COMPLETE stage=metadata` 後，可以測到下載後的 PS 介面檢查。

```bash
sudo -E python3 run_on_board.py --stop-after ps_bus
```

PS 介面有回應後，可再確認 RFDC 的 PLL、mixer、FIFO 和 RF 時脈。

```bash
sudo -E python3 run_on_board.py --stop-after rfdc
```

每個階段都會在動作前後印出 `BEGIN`／`OK`，並立即保存至 `received/bringup.log`。
如果連線中斷，重新連線後讀取最後幾行，辨認是 `clocks`、`download`、`ps_bus`、`rfdc`
或後續傳輸階段。完整實體驗證以 `received/result.json` 的 `passed: true` 為準。

更換照片：

```bash
sudo -E python3 run_on_board.py --bit ofdm_rf.bit --input photo.jpg
```

照片會轉成 640×480、8-bit 灰階。預設振幅 4096，約為數位滿刻度的 1/8。
如需調整可使用 `--amplitude`，必須是 64..16380 之間的 4 的倍數。
如果 pilot 無法檢出，程式會停止並回報量測值；先檢查連接的 SMA 是否為 A 通道及衰減量。

## 硬體設定

| 項目 | 設定 |
| --- | --- |
| 晶片 | xczu48dr-ffvg1517-2-e |
| DAC SMA / RFDC | DAC_A，tile 230，block 0；IP `s20_axis` |
| ADC SMA / RFDC | ADC_A，tile 226，dual ADC block 1；IP `vin2_23`、`m22_axis` / `m23_axis` |
| RF 載波 | 500 MHz，DAC NCO +500 MHz，ADC NCO −500 MHz |
| ADC 與 DAC 取樣率 | 4915.2 MSPS |
| 外部 RFDC 參考 | 491.52 MHz，內部 PLL 啟用 |
| LMK 設定檔頻率 | 245.76 MHz |
| 插值／抽取 | 16× |
| 數位資料 | 每拍兩個複數樣本；DAC 64 bits，ADC I/Q 各 32 bits |
| RF PL 時脈 | 153.6 MHz，由 RFDC ADC 的 307.2 MHz 輸出除以 2 |
| AXI 時脈 | PS 100 MHz，包含控制、狀態、BRAM 埠 A 與 RFDC 配置介面 |
| 跨時脈 | BRAM 埠 B 使用 RF 153.6 MHz。XPM 握手傳遞控制與狀態 |
| OFDM | 8 點 FFT/IFFT，4 樣本循環字首。±1、±2 子載波用 QPSK |
| OFDM 樣本與符號 | 每個複數樣本維持 8 個 RF PL clock，19.2 MS/s，子載波間隔 2.4 MHz，每個符號 96 個 clock |
| OFDM 原始位元率 | 每個資料符號 8 bits，12.8 Mbit/s。圖片吞吐量另計前導、訓練、封包欄位、ACK 與重傳 |
| 同步與等化 | 全振幅 DC 標記定位封包，已知訓練符號估計各子載波的複數通道，依固定時序取樣 |
| 可靠傳輸 | CRC-32、ACK、逾時重傳，最多重傳 10 次 |

LED0 為 RF PL heartbeat；LED1 為傳輸進行中；LED2 為完成；LED3 為失敗或介面錯誤。
Python 在結束時重設資料路徑，LED2 可能只短暫亮起；最終驗收請看 `result.json`。

## 記憶體與控制

| PS 位址 | 功能 |
| --- | --- |
| `0xA0000000`，512 KiB | 原圖 BRAM；使用前 307,200 bytes |
| `0xA0080000`，512 KiB | 接收圖 BRAM |
| `0xA0100000` | AXI GPIO control；`+0` 控制、`+8` 狀態 |
| `0xA0110000` | Telemetry；`+0` 接收峰值／即時幅度，`+8` 重傳次數 |
| `0xA0120000` | Host state，channel 1 回報時脈狀態，channel 2 回報 RF 域收到的控制字 |
| `0xA0140000`，256 KiB | RFDC 寄存器 |

控制：bit0 reset、bit1 start 上升緣、bit2 pilot、bits15:4 為振幅 / 4、bits31:16 為封包起始標記偵測門檻。
狀態：bit0 done、bit1 failed、bit2 busy、bit3 收齊、bit4 串流故障、bit5 位址故障、
bits24:6 為已收 bytes、bits31:25 為版本識別 `0x3d`。

不要在傳輸期間修改原圖 BRAM。每次重跑都先 reset；Python 已包含這個流程。

## 重建與驗證

Windows 專案根目錄：

```powershell
python scripts/test_rf.py --full
python scripts/build_rf.py --vivado-home D:\Vivado\2024.2
```

建置前會核對 `reports/rf_simulation/result.json` 的完整測試結果、300 包逐 byte 比對、BRAM 輸出檔和模擬所用的原始檔 SHA-256。
Windows 建置期間會暫時把專案映射到可用的短磁碟機代號，以縮短 Vivado 產生的 IP 路徑。
Vivado 專案在 `build/rf/vivado/ofdm_rf.xpr`。要開啟該專案，可從建置 log 確認原磁碟機代號，使用 `subst` 重新映射專案根目錄。
上板檔案在 `board/output/`，發布包為 `board/ofdm_rf_rfsoc4x2.zip`。
已完成發布檢查的實體 checkpoint 為 `build/rf/ofdm_rf_routed.dcp`；在 Vivado 使用
`open_checkpoint` 開啟這個檔案即可檢查發布版本。XSA 是硬體描述，位於 `hardware_description/`；bitstream 以獨立 `.bit` 一起提供。
`reports/implementation_rf/` 保存 routed timing、hold paths、DRC、CDC、clock interaction 與 utilization。
建置腳本會在 setup、hold、pulse width、未約束內部路徑或阻擋發布的 DRC 問題出現時停止。DSP 管線建議 DPIP-2、DPOP-3 和 DPOP-4 完整保留在 DRC 報告，數量另列在發布 manifest。
LED 是非同步人眼指示，只有 LED 輸出被排除外部同步時序分析；沒有降低 DRC 等級。
`no_output_delay` 的 4 個項目均為這四個 LED；實際未約束輸出為 0。
另有兩條 RFDC 內部 ADC／DAC clock-valid 狀態同步路徑，因同一 reset 扇出而被 CDC-11 標記；
兩個狀態各有四級同步器，彼此不需要同拍一致，已逐條審查並以精確端點記錄在
`scripts/rf_cdc_waivers.tcl`，不影響 setup／hold 約束。其餘 critical CDC 會阻止發布。

完整模擬涵蓋數位 I/Q 迴路、ACK 遺失、ACK 毀損、斷線、回波及整張圖的逐 byte 驗證。
Host 測試另檢查 RF 時脈未啟動、停止與恢復時的命令握手。
模擬和 Vivado 建置的驗證紀錄收錄在發布包的 `verification/`。
發布包的 `physical_board_tested` 記為 false。上板後請以 `received/result.json` 驗收實體 RF 傳輸。

## 參考

- [Real Digital RFSoC 4x2 Reference Manual](https://www.realdigital.org/downloads/4b98c421901794107cd1e25e208fe002.pdf)：SMA 對應、參考時脈及類比介面。
- [AMD PG269 RFDC](https://docs.amd.com/r/en-US/pg269-rf-data-converter)：插值、抽取、資料介面及 RF mixer。
- [AMD RFSoC 4x2 PYNQ clock initialization](https://github.com/Xilinx/RFSoC-PYNQ/blob/master/boards/RFSoC4x2/base/base.py)：LMK／LMX 設定。
- [PYNQ v3.1 AxiGPIO 官方文件](https://pynq.readthedocs.io/en/v3.1/pynq_libraries/axigpio.html)：channel、方向與 `write(value, mask)`。
- [PYNQ v3.1 Overlay 官方文件](https://pynq.readthedocs.io/en/v3.1/pynq_package/pynq.overlay.html)：具名驅動、`download=False` 及 IP 的 `read/write`。
- [PYNQ v3.1.2 Overlay 原始碼](https://github.com/Xilinx/PYNQ/blob/v3.1.2/pynq/overlay.py)：依 `mem_dict` 將記憶體暴露為 Overlay 屬性。
- [PYNQ v3.1.2 EmbeddedXrtMemory 原始碼](https://github.com/Xilinx/PYNQ/blob/v3.1.2/pynq/pl_server/embedded_device.py)：記憶體物件的 `read(address)`／`write(address, value)` 與底層存取。
