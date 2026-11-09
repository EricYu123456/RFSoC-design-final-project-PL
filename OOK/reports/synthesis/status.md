# 合成通過，授權已確認可用

日期：2026-11-03。工具：Vivado 2024.2。目標：xczu48dr-ffvg1517-2-e。

Vivado 已成功取得 Synthesis / xczu48dr 授權。DDS、Multiplier、Block Memory Generator，以及整個 image_link_synth 收發器都已完成合成。頂層合成為 0 errors、0 critical warnings；一般 warnings 仍存在。

## 資源使用量

| 資源 | 使用量 | 裝置占用率 |
| --- | ---: | ---: |
| LUT | 1,408 | 0.33% |
| FF | 1,367 | 0.16% |
| DSP | 2 | 0.05% |
| Block RAM Tile | 16 | 1.48% |

BRAM 的實體組成為 14 個 RAMB36E2 加上 4 個 RAMB18E2，相當於 16 個 36-Kb tiles；包括 DDS 的載波查表記憶體與封包暫存。

原始資料見 [資源報告](utilization.rpt)。合成 checkpoint 為 build/image_link_synth.dcp。

## 時序狀態

頂層時脈約束為 100 MHz，也就是 10 ns。合成階段的 setup WNS 為 +7.796 ns，hold WHS 為 -0.070 ns，所以目前不能宣稱完整時序已通過。

這次尚未進行 placement / routing；12 個輸入與 31 個輸出也尚未指定外部 I/O delay。Multiplier 與 BRAM 的獨立合成預設時脈和頂層不同，後續實作前應統一設定。這些項目與授權是否可用是不同的檢查。

原始資料見 [合成時序報告](timing_summary.rpt)。目前的純模擬驗證仍為 [11 個測試通過](../summary.md)，包含正常與混合錯誤兩組完整圖片傳送，兩組的 307,200 bytes 都與原圖完全相同。
