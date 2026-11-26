import argparse
import json
from PIL import Image
import numpy as np
from ai.ai_inference_rfsoc import RFSoCDetector
p=argparse.ArgumentParser()
p.add_argument('image');p.add_argument('--config',default='config/detector.json')
a=p.parse_args()
g=np.asarray(Image.open(a.image).convert('L'))
print(json.dumps(RFSoCDetector(a.config).predict_gray(g,0),ensure_ascii=False,indent=2))
