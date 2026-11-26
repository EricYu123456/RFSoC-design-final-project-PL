import argparse
import json
from ai.ai_inference_rfsoc import interpreter_class,detail_json
p=argparse.ArgumentParser()
p.add_argument('model')
a=p.parse_args()
i=interpreter_class()(model_path=a.model,num_threads=1)
i.allocate_tensors()
print(json.dumps({'inputs':[detail_json(d) for d in i.get_input_details()],
                  'outputs':[detail_json(d) for d in i.get_output_details()]},indent=2))
