"""Explicit SSD DetectionPostProcess contract. Never guesses output ordering."""
import json
import time
from pathlib import Path
import numpy as np
from PIL import Image


def interpreter_class():
    try:
        from tflite_runtime.interpreter import Interpreter
    except ImportError:
        try:
            import tensorflow as tf
            Interpreter=tf.lite.Interpreter
        except ImportError as exc:
            raise RuntimeError('Install a board-compatible tflite-runtime wheel (or existing TensorFlow).') from exc
    return Interpreter


def detail_json(d):
    return {'name':d['name'], 'index':int(d['index']),
            'shape':[int(x) for x in d['shape']], 'dtype':str(np.dtype(d['dtype'])),
            'quantization_parameters':{k:np.asarray(v).tolist() for k,v in d['quantization_parameters'].items()}}


def quantize(real, detail):
    dtype=np.dtype(detail['dtype'])
    if dtype.kind == 'f':
        return real.astype(dtype)
    if dtype not in (np.dtype('int8'),np.dtype('uint8')):
        raise ValueError('Only float or int8/uint8 input supported')
    q=detail['quantization_parameters']
    scales=np.asarray(q['scales']); zeros=np.asarray(q['zero_points'])
    if scales.size != 1 or zeros.size != 1 or scales[0] <= 0:
        raise ValueError('Expected positive per-tensor quantization scale')
    limits=np.iinfo(dtype)
    return np.clip(np.rint(real/scales[0]+zeros[0]),limits.min,limits.max).astype(dtype)


def dequantize(value, detail):
    if np.asarray(value).dtype.kind == 'f':
        return np.asarray(value,dtype=np.float32)
    q=detail['quantization_parameters']
    s=np.asarray(q['scales']); z=np.asarray(q['zero_points'])
    if s.size!=1 or z.size!=1 or s[0]<=0:
        raise ValueError('Only positive per-tensor quantized outputs supported')
    return (np.asarray(value,dtype=np.float32)-z[0])*s[0]


class RFSoCDetector:
    def __init__(self, config, interpreter_factory=None):
        path=Path(config).resolve()
        self.cfg=json.loads(path.read_text())
        c=self.cfg
        if c.get('contract_confirmed') is not True:
            raise ValueError('Confirm model preprocessing, SSD outputs and labels in config first; see docs/MODEL.md')
        if not 0<=float(c['threshold'])<=1 or int(c['num_threads'])<1:
            raise ValueError('Invalid threshold/num_threads')
        if not all(isinstance(c.get(k),(int,float)) and np.isfinite(c[k]) for k in ('input_real_scale','input_real_bias')):
            raise ValueError('Explicit finite input_real_scale and input_real_bias required')
        base=path.parent.parent
        self.model=(base/c['model']).resolve()
        self.labels=(base/c['labels']).read_text().splitlines()
        if not self.model.is_file() or not self.labels:
            raise ValueError('Model and nonempty label file required')
        factory=interpreter_factory or interpreter_class()
        self.interpreter=factory(model_path=str(self.model),num_threads=int(c['num_threads']))
        self.interpreter.allocate_tensors()
        inputs=self.interpreter.get_input_details()
        if len(inputs)!=1:
            raise ValueError('Exactly one NHWC image input required')
        self.input=inputs[0]
        shape=list(self.input['shape'])
        if len(shape)!=4 or shape[0]!=1 or shape[3]!=3 or min(shape[1:3])<=0:
            raise ValueError('Expected fixed [1,H,W,3] input; gray replicated to RGB')
        self.h,self.w=int(shape[1]),int(shape[2])
        outputs={int(d['index']):d for d in self.interpreter.get_output_details()}
        mapping=c['outputs']
        if set(mapping)!={'boxes','classes','scores','count'} or any(type(i) is not int for i in mapping.values()):
            raise ValueError('Map all four roles to actual tensor INDEX values, not list positions')
        if len(set(mapping.values()))!=4 or not set(mapping.values())<=set(outputs):
            raise ValueError('Output mapping does not match model')
        self.outputs={role:outputs[index] for role,index in mapping.items()}
        shapes={k:list(d['shape']) for k,d in self.outputs.items()}
        b=shapes['boxes']
        if len(b)!=3 or b[0]!=1 or b[2]!=4 or shapes['classes']!=[1,b[1]] or shapes['scores']!=[1,b[1]] or shapes['count']!=[1]:
            raise ValueError('Only SSD postprocessed [1,N,4], [1,N], [1,N], [1] outputs supported')

    def predict_gray(self, gray, frame_id, timestamp_ns=None):
        start=time.perf_counter_ns()
        gray=np.asarray(gray)
        if gray.ndim!=2 or gray.dtype!=np.uint8 or min(gray.shape)<=0:
            raise ValueError('Expected nonempty HxW uint8 GRAY8 array')
        h,w=gray.shape
        resampling=getattr(Image,'Resampling',Image)
        resized=np.asarray(Image.fromarray(gray).resize((self.w,self.h),resampling.BILINEAR),dtype=np.float32)
        rgb=np.repeat(resized[:,:,None],3,axis=2)[None]
        real=rgb*self.cfg['input_real_scale']+self.cfg['input_real_bias']
        self.interpreter.set_tensor(self.input['index'],quantize(real,self.input))
        self.interpreter.invoke()
        out={k:dequantize(self.interpreter.get_tensor(d['index']),d) for k,d in self.outputs.items()}
        if not all(np.isfinite(v).all() for v in out.values()):
            raise ValueError('Nonfinite model outputs')
        count_value=float(out['count'][0])
        if abs(count_value-round(count_value))>0.01 or not 0<=count_value<=out['boxes'].shape[1]:
            raise ValueError('Invalid detection count; check output mapping')
        detections=[]
        for i in range(int(round(count_value))):
            score=float(out['scores'][0,i]); raw_class=float(out['classes'][0,i])
            if not 0<=score<=1 or abs(raw_class-round(raw_class))>0.01:
                raise ValueError('Invalid scores/classes; check output mapping')
            if score<self.cfg['threshold']:
                continue
            cls=int(round(raw_class)); label_index=cls+int(self.cfg['label_offset'])
            if not 0<=label_index<len(self.labels):
                raise ValueError('Class outside label file; check label_offset')
            ymin,xmin,ymax,xmax=np.clip(out['boxes'][0,i],0,1).tolist()
            if ymax<ymin or xmax<xmin:
                raise ValueError('Invalid ymin,xmin,ymax,xmax box')
            detections.append({'class_id':cls,'label':self.labels[label_index],'score':score,
                'bbox_xyxy_pixels':[xmin*w,ymin*h,xmax*w,ymax*h],
                'bbox_yxyx_normalized':[ymin,xmin,ymax,xmax]})
        return {'schema_version':1,'frame_id':int(frame_id),'source_timestamp_ns':timestamp_ns,
                'width':w,'height':h,'detections':detections,
                'pipeline_ms':(time.perf_counter_ns()-start)/1e6}
