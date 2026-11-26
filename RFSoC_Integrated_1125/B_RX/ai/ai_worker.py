"""Spawned process, one shared-memory slot, overwrite pending frames.
submit() never waits for inference, I/O or a held slot lock. It does copy H*W bytes.
One producer; fixed resolution; stop RX producer before close().
"""
import ctypes
import json
import multiprocessing as mp
import time
import traceback
from pathlib import Path
import numpy as np


def _clock_ns():
    # Native POSIX clock has a shared epoch, including macOS Python <3.10
    # where Python's monotonic/perf_counter may use per-process origins.
    if hasattr(time,'clock_gettime_ns') and hasattr(time,'CLOCK_MONOTONIC'):
        return time.clock_gettime_ns(time.CLOCK_MONOTONIC)
    return time.monotonic_ns()


def _atomic_json(path,data):
    path=Path(path);tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False)+'\n')
    tmp.replace(path)


def _pause(stop,seconds):
    deadline=time.monotonic()+seconds
    while not stop.value and time.monotonic()<deadline:
        time.sleep(min(0.01,max(0,deadline-time.monotonic())))


def _run(raw,shape,lock,seq,meta,stop,ready,config,out,fps,mock,delay):
    try:
        if mock:
            detector=None
        else:
            from .ai_inference_rfsoc import RFSoCDetector
            detector=RFSoCDetector(config)
        ready.value=1
        seen=0;due=0.0
        with (Path(out)/'detections.jsonl').open('a',buffering=1) as log:
            while not stop.value:
                now=time.monotonic()
                if now<due:
                    _pause(stop,min(due-now,0.05));continue
                packet=None
                if lock.acquire(False):
                    try:
                        if seq.value!=seen:
                            frame=np.frombuffer(raw,dtype=np.uint8).reshape(shape).copy()
                            seen=seq.value;packet=(frame,list(meta[:]))
                    finally:
                        lock.release()
                if packet is None:
                    _pause(stop,0.005);continue
                frame,m=packet
                start=_clock_ns();cpu=time.process_time_ns()
                if mock:
                    if delay:_pause(stop,delay)
                    result={'schema_version':1,'frame_id':m[0],'source_timestamp_ns':None if m[1]<0 else m[1],
                            'width':shape[1],'height':shape[0],'detections':[],'mock':True,'mock_pixel_0':int(frame[0,0])}
                else:
                    result=detector.predict_gray(frame,m[0],None if m[1]<0 else m[1])
                end=_clock_ns()
                result.update(rx_timestamp_ns=m[2],ai_start_monotonic_ns=start,ai_end_monotonic_ns=end,
                              pipeline_ms=(end-start)/1e6,cpu_ms=(time.process_time_ns()-cpu)/1e6,
                              rx_to_ai_end_ms=(end-m[3])/1e6)
                try:
                    import resource
                    result['max_rss_raw']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    result['max_rss_unit']='bytes on macOS; KiB on Linux'
                except ImportError:
                    pass
                log.write(json.dumps(result,ensure_ascii=False,allow_nan=False)+'\n')
                _atomic_json(Path(out)/'latest.json',result)
                due=time.monotonic()+max(0.0,1/fps-(end-start)/1e9)
    except BaseException:
        _atomic_json(Path(out)/'worker_error.json',{'traceback':traceback.format_exc()})
        raise


class AIWorker:
    def __init__(self,config=None,shape=(480,640),fps=2.0,out='results/live',mock=False,mock_delay=0):
        if len(shape)!=2 or any(type(x) is not int or x<=0 for x in shape):
            raise ValueError('shape must be positive integer (H,W)')
        if not np.isfinite(fps) or fps<=0:
            raise ValueError('fps must be positive')
        self.shape=tuple(shape);self.config=str(Path(config).resolve()) if config else None
        self.out=Path(out).resolve();self.out.mkdir(parents=True,exist_ok=True)
        # Refuse accidental mixing/overwriting previous run artifacts.
        if any(self.out.iterdir()):raise ValueError('Use a new empty output directory for each run')
        self.ctx=mp.get_context('spawn')
        self.raw=self.ctx.RawArray(ctypes.c_ubyte,shape[0]*shape[1])
        self.lock=self.ctx.Lock();self.seq=self.ctx.RawValue(ctypes.c_ulonglong,0)
        self.meta=self.ctx.RawArray(ctypes.c_longlong,4)
        self.stop=self.ctx.RawValue(ctypes.c_ubyte,0);self.ready=self.ctx.RawValue(ctypes.c_ubyte,0)
        self.process=self.ctx.Process(target=_run,args=(self.raw,self.shape,self.lock,self.seq,self.meta,
            self.stop,self.ready,self.config,str(self.out),fps,mock,mock_delay),daemon=True)
        self.counts={'submitted':0,'accepted':0,'busy':0,'invalid':0,'unavailable':0,'submit_max_ms':0.0}
        self.started=False;self.closed=False

    def start(self,timeout=30):
        if self.started or self.closed:raise RuntimeError('Worker cannot be restarted')
        self.process.start();self.started=True
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            if self.ready.value:return self
            time.sleep(0.05)
            if not self.process.is_alive():break
        self.close()
        error=self.out/'worker_error.json'
        raise RuntimeError(error.read_text() if error.exists() else 'AI startup timed out')

    def submit(self,gray,frame_id,source_timestamp_ns=None):
        """Returns accepted bool; invalid input/failure disables only this frame.
        Source timestamp must be confirmed nanoseconds or None (never guessed).
        """
        start=_clock_ns();self.counts['submitted']+=1
        try:
            if self.closed or not self.started or not self.process.is_alive():
                self.counts['unavailable']+=1;return False
            if not isinstance(gray,np.ndarray) or gray.dtype!=np.uint8 or gray.shape!=self.shape:
                self.counts['invalid']+=1;return False
            values=[frame_id,-1 if source_timestamp_ns is None else source_timestamp_ns,time.time_ns(),start]
            if any(not isinstance(x,(int,np.integer)) or not -(2**63)<=x<2**63 for x in values):
                self.counts['invalid']+=1;return False
            if source_timestamp_ns is not None and source_timestamp_ns<0:
                self.counts['invalid']+=1;return False
            if not self.lock.acquire(False):
                self.counts['busy']+=1;return False
            try:
                np.copyto(np.frombuffer(self.raw,dtype=np.uint8).reshape(self.shape),gray)
                self.meta[:]=values;self.seq.value+=1
            finally:
                self.lock.release()
            self.counts['accepted']+=1;return True
        finally:
            self.counts['submit_max_ms']=max(self.counts['submit_max_ms'],(_clock_ns()-start)/1e6)

    def status(self):
        return dict(self.counts,alive=self.started and not self.closed and self.process.is_alive(),
                    error_file=str(self.out/'worker_error.json'))

    def close(self,timeout=5):
        if self.closed:return
        self.closed=True;self.stop.value=1
        forced=False
        if self.started:
            self.process.join(timeout)
            if self.process.is_alive():
                forced=True;self.process.terminate();self.process.join(2)
        _atomic_json(self.out/'worker_summary.json',dict(self.counts,forced_termination=forced,
            exitcode=self.process.exitcode if self.started else None,
            pending_frame_may_be_discarded=True))
