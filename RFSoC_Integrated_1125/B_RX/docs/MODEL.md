# 模型契約與供應方式
1. 從舊板取「實際驗證成功」的 SSD-MobileNet V1 INT8 TFLite、labels 與原推論程式，保留來源、版本、SHA-256、授權。此包沒有模型/labels，也沒有你原本的 RFSoCDetector；本包是新實作的同名介面，需 golden test 對照。
2. 放 models/ssd_mobilenet_v1_int8.tflite 與 labels/labels.txt。不要依檔名假設架構、量化或資料集。INT8 內部運算不代表輸入一定是 int8；uint8 也可能。
3. `python -m tools.inspect_model models/ssd_mobilenet_v1_int8.tflite > results/model_details.json`。這會真正載入與 allocate 模型，列出 tensor 名稱、index、shape、dtype、量化。需要真實 interpreter。
4. 複製 `config/detector.example.json` 為 `config/detector.json`。所有檔案路徑相對於 config 的上一層（1125 root）；不相對於 notebook CWD。
5. outputs 使用真正 tensor **index**，不是 get_output_details 的序號。人工依模型輸出契約填 boxes/classes/scores/count；不可只按 shape 猜兩個 [1,N] 的角色。只支援 SSD DetectionPostProcess 四個已後處理輸出；raw heads、YOLO、未含 NMS 的模型須另寫 decoder，不能套此程式。
6. 本包採 stretch resize（Pillow bilinear）、灰階複製成 RGB，不做 letterbox。依原模型訓練前處理設定 `real = pixel * input_real_scale + input_real_bias`。例如實值範圍 0..255 為 1,0；0..1 為 1/255,0；-1..1 為 1/127.5,-1。這些是例子，**不是模型預設**。後續 int8/uint8 依 tensor scale/zero_point 計算 q=round(real/scale)+zero_point 並裁切。前處理和量化是兩個步驟，不可混用。
7. labels 每行一個名稱，保留 ID 對齊；`label_offset` 表示 label 行號 = model class ID + offset（0 起算）。確認背景類與 COCO 缺號，不提供臆測 COCO labels。
8. `contract_confirmed=true` 只在上述契約核對後設定。這是人工確認旗標，無法自動證明準確率。執行 `python -m tools.infer_image /path/to/known_gray.png --config config/detector.json`；以舊板相同圖片/前處理比對 class、score 與 box。無偵測不代表模型正確。

輸入：2D uint8 GRAY8；空影像或不同格式拒絕。輸出 box = ymin,xmin,ymax,xmax 正規化，還原到原始任意 W×H（含 497×335、640×480），JSON 提供 xyxy pixels。輸出有限值、count、score、label 範圍會檢查。此驗證只檢查格式，不證明模型身分或語義。

官方參考：
- [Interpreter tensor details、量化資訊與 allocation](https://www.tensorflow.org/api_docs/python/tf/lite/Interpreter)
- [SSD 四個 DetectionPostProcess 輸出契約](https://developers.google.com/edge/litert/libraries/task_library/object_detector)
此包直接使用 Interpreter，未使用 Task Library，故不強制 metadata；輸出語義仍需人工確認。
