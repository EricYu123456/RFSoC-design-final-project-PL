"""Offline recording replay; never accesses RF/PL. Run from package root."""
import argparse
import json
import time
from pathlib import Path
from .ai_worker import AIWorker


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--video',required=True)
    p.add_argument('--config',default='config/detector.json')
    p.add_argument('--out',required=True)
    p.add_argument('--ai-fps',type=float,default=2)
    p.add_argument('--mock',action='store_true')
    args=p.parse_args()
    import cv2
    cap=cv2.VideoCapture(args.video)
    if not cap.isOpened():raise RuntimeError('Cannot open video; check board OpenCV codec support')
    worker=None
    try:
        fps=cap.get(cv2.CAP_PROP_FPS)
        if not 0<fps<1000:raise ValueError('Video FPS unavailable; inspect recording first')
        frame_id=0;start=time.monotonic()
        while True:
            ok,bgr=cap.read()
            if not ok:break
            gray=cv2.cvtColor(bgr,cv2.COLOR_BGR2GRAY)
            if worker is None:
                worker=AIWorker(config=args.config,shape=gray.shape,fps=args.ai_fps,out=args.out,mock=args.mock).start()
                start=time.monotonic()
            remaining=start+frame_id/fps-time.monotonic()
            if remaining>0:time.sleep(remaining)
            worker.submit(gray,frame_id,None)
            frame_id+=1
        if worker is None:raise RuntimeError('No decoded frames')
        # Wait outside RX, solely to allow final pending replay inference.
        deadline=time.monotonic()+10
        while time.monotonic()<deadline and worker.process.is_alive():
            latest=Path(args.out)/'latest.json'
            if latest.exists() and json.loads(latest.read_text())['frame_id']==frame_id-1:break
            time.sleep(0.05)
        if (Path(args.out)/'worker_error.json').exists() or not worker.process.is_alive():
            raise RuntimeError('AI worker failed; inspect worker_error.json')
        print(json.dumps(worker.status(),indent=2))
    finally:
        cap.release()
        if worker is not None:worker.close()


if __name__=='__main__':main()
