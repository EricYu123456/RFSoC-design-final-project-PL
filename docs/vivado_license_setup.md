# Vivado 2024.2 合成授權怎麼設定？

本機已在 2026-11-03 確認 ZU48DR 合成授權可用，並完成整個收發器的合成。以下保留設定方法，方便其他組員或換電腦時使用。最新結果見 [合成狀態](../reports/synthesis/status.md)。

## 板卡定義與合成授權是兩件事

你提供的資料夾：

```text
C:\Xilinx\Vivado\2024.2\data\xhub\boards\XilinxBoardStore\boards\Xilinx\rfsoc4x2
```

裡面是 board.xml、preset.xml、接腳設定與板卡圖片，讓 Vivado 認識這塊開發板。資料夾裡的 LICENSE 是板卡檔案的開源授權，不是讓 Vivado 執行合成的授權。

已確認這份 board.xml 的裝置為 xczu48dr-ffvg1517-2-e，與本專案目標一致。

## 先開啟授權管理員

在 PowerShell 執行：

```powershell
& 'C:\Xilinx\Vivado\2024.2\bin\vlm.bat'
```

開啟後，在 View License Status 查看是否有有效、涵蓋 ZU48DR 且適用於 Vivado 2024.2 的 Vivado 授權。

### 如果有學校提供的 .lic 檔

1. 在授權管理員選 Load License。
2. 按 Copy License。
3. 選擇學校提供的 .lic 檔案。
4. 回到 View License Status 重新整理狀態。

詳見 [AMD 2024.2：安裝本機授權檔](https://docs.amd.com/r/2024.2-English/ug973-vivado-release-notes-install-license/Install-Certificate-Based-Node-Locked-License-Key-File)。

### 如果學校使用授權伺服器

向助教取得 port@host 格式的伺服器資訊，並詢問是否需要校園網路或 VPN。在授權管理員的 Manage License Search Paths 中，將 XILINXD_LICENSE_FILE 設成助教提供的值。

詳見 [AMD 2024.2：授權狀態與搜尋路徑](https://docs.amd.com/r/2024.2-English/ug973-vivado-release-notes-install-license/Managing-Licenses-with-the-Vivado-License-Manager)。

### 如果兩者都沒有

請向助教詢問：「這門課是否提供支援 ZU48DR、適用於 Vivado 2024.2 的合成授權，或可連線的授權伺服器？」

這週可以先完成既有 XSim 模擬，不必等合成授權才開始。

## 設定完成後再驗證

在專案目錄執行：

```powershell
python scripts/run.py --case baseline --packets 3 --synth
```

腳本會先跑短模擬，再重試合成。成功時，reports/synthesis/ 會有 utilization.rpt 與 timing_summary.rpt；失敗時，請看 status.md 與 build/synthesis.log。不要把「已安裝板卡檔」或「模擬 PASS」當作合成已通過。
