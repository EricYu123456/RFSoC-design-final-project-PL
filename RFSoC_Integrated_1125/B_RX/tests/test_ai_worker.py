import json
import tempfile
import time
import unittest
from pathlib import Path
import numpy as np
from ai.ai_inference_rfsoc import RFSoCDetector, quantize
from ai.ai_worker import AIWorker

def detail(index,shape,dtype=np.float32):
    return {'index':index,'name':'tensor_'+str(index),'shape':np.array(shape),'dtype':dtype,
            'quantization_parameters':{'scales':np.array([1/128]) if dtype==np.int8 else np.array([]),
                                       'zero_points':np.array([0]) if dtype==np.int8 else np.array([])}}

class FakeInterpreter:
    def __init__(self,**kwargs):pass
    def allocate_tensors(self):pass
    def get_input_details(self):return [detail(0,[1,300,300,3],np.int8)]
    def get_output_details(self):return [detail(9,[1,1]),detail(7,[1,1,4]),detail(11,[1]),detail(8,[1,1])]
    def set_tensor(self,index,data):
        self.data=data
        assert data.shape==(1,300,300,3) and data.dtype==np.int8
    def invoke(self):pass
    def get_tensor(self,index):
        return {7:np.array([[[.1,.2,.8,.9]]],np.float32),8:np.array([[0]],np.float32),
                9:np.array([[.9]],np.float32),11:np.array([1],np.float32)}[index]

class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'config').mkdir();(self.root/'model.tflite').write_bytes(b'MOCK, NOT A TFLITE MODEL')
        (self.root/'labels.txt').write_text('person\n')
        self.config={'model':'model.tflite','labels':'labels.txt','contract_confirmed':True,
                     'input_real_scale':1/127.5,'input_real_bias':-1,
                     'outputs':{'boxes':7,'classes':8,'scores':9,'count':11},
                     'num_threads':2,'threshold':.5,'label_offset':0}
        self.path=self.root/'config/detector.json'
    def tearDown(self):self.temp.cleanup()
    def detector(self):
        self.path.write_text(json.dumps(self.config))
        return RFSoCDetector(self.path,FakeInterpreter)
    def test_quantization_and_shape(self):
        d=self.detector();r=d.predict_gray(np.full((335,497),255,np.uint8),105,123)
        self.assertEqual(d.interpreter.data.min(),127)
        self.assertEqual(r['frame_id'],105);self.assertEqual(r['source_timestamp_ns'],123)
        np.testing.assert_allclose(r['detections'][0]['bbox_xyxy_pixels'],[99.4,33.5,447.3,268],atol=.001)
    def test_uint8_quantization(self):
        dt=detail(0,[1],np.uint8)
        dt['quantization_parameters']={'scales':np.array([.5]),'zero_points':np.array([128])}
        np.testing.assert_array_equal(quantize(np.array([-100,0,1,100]),dt),[0,128,130,255])
    def test_contract_unconfirmed(self):
        self.config['contract_confirmed']=False
        with self.assertRaises(ValueError):self.detector()
    def test_bad_mapping(self):
        self.config['outputs']['scores']=2
        with self.assertRaises(ValueError):self.detector()
    def test_invalid_input(self):
        d=self.detector()
        with self.assertRaises(ValueError):d.predict_gray(np.zeros((3,3,3),np.uint8),1)
        with self.assertRaises(ValueError):d.predict_gray(np.zeros((3,3),np.float32),1)
    def test_label_alignment(self):
        self.config['label_offset']=1;d=self.detector()
        with self.assertRaises(ValueError):d.predict_gray(np.zeros((3,3),np.uint8),1)

class WorkerTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.out=Path(self.temp.name)/'run'
    def tearDown(self):self.temp.cleanup()
    def wait_latest(self,w,expected):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            p=self.out/'latest.json'
            if p.exists():
                data=json.loads(p.read_text())
                if data['frame_id']==expected:return data
            if not w.process.is_alive():self.fail('worker unexpectedly died')
            time.sleep(.02)
        self.fail('timeout waiting for result')
    def test_latest_frame_and_fail_open(self):
        w=AIWorker(shape=(335,497),fps=100,out=self.out,mock=True,mock_delay=.2).start()
        try:
            gray=np.zeros((335,497),np.uint8)
            self.assertTrue(w.submit(gray,1,123));time.sleep(.08)
            for i in range(2,50):w.submit(gray,i,None)
            deadline=time.monotonic()+2
            while not w.submit(gray,50,None):self.assertLess(time.monotonic(),deadline)
            gray[:]=255
            r=self.wait_latest(w,50)
            self.assertTrue(r['mock']);self.assertIsNone(r['source_timestamp_ns'])
            self.assertEqual(r['mock_pixel_0'],0)
            self.assertGreaterEqual(r['rx_to_ai_end_ms'],0)
            self.assertFalse(w.submit(np.zeros((1,1),np.uint8),51))
            w.lock.acquire()
            try:
                start=time.monotonic();self.assertFalse(w.submit(gray,52))
                self.assertLess(time.monotonic()-start,.1)
            finally:w.lock.release()
        finally:w.close()
        self.assertFalse(w.process.is_alive());self.assertFalse(w.submit(gray,53))
        rows=[json.loads(s) for s in (self.out/'detections.jsonl').read_text().splitlines()]
        self.assertLess(len(rows),50);self.assertTrue((self.out/'worker_summary.json').exists())
        w.close()
    def test_cross_process_clock_and_fast_result(self):
        w=AIWorker(shape=(10,10),out=self.out,mock=True).start()
        try:
            self.assertTrue(w.submit(np.zeros((10,10),np.uint8),1))
            row=self.wait_latest(w,1)
            self.assertGreaterEqual(row['rx_to_ai_end_ms'],0)
            self.assertLess(row['rx_to_ai_end_ms'],5000)
        finally:w.close()

    def test_child_failure_does_not_block_submit(self):
        w=AIWorker(shape=(10,10),out=self.out,mock=True).start()
        w.process.terminate();w.process.join(3)
        try:self.assertFalse(w.submit(np.zeros((10,10),np.uint8),1))
        finally:w.close()
    def test_startup_missing_model(self):
        w=AIWorker(config=Path(self.temp.name)/'missing.json',out=self.out)
        with self.assertRaises(RuntimeError):w.start(timeout=5)
        self.assertTrue((self.out/'worker_error.json').exists());self.assertFalse(w.process.is_alive())
    def test_no_reuse_output(self):
        self.out.mkdir();(self.out/'old').touch()
        with self.assertRaises(ValueError):AIWorker(out=self.out,mock=True)


if __name__=='__main__':unittest.main()
