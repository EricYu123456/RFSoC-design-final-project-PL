"""Hardware-free worker smoke test; all records are explicitly mock=true."""
import argparse
import time
from pathlib import Path
import numpy as np
from ai.ai_worker import AIWorker

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);a=p.parse_args()
    w=AIWorker(shape=(480,640),fps=2,out=a.out,mock=True).start()
    try:
        for i in range(30):
            w.submit(np.full((480,640),i,np.uint8),i,None)
            time.sleep(1/30)
        time.sleep(.6)
    finally:w.close()
    print(Path(a.out).resolve())
if __name__=='__main__':main()
