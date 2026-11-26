"""Summarize real completed rows; CPU % is active inference interval only."""
import argparse
import json
import math
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('run');a=p.parse_args()
rows=[json.loads(s) for s in (Path(a.run)/'detections.jsonl').read_text().splitlines() if s.strip()]
def stats(values):
    values=sorted(values)
    return {'mean':sum(values)/len(values),'p95':values[max(0,math.ceil(len(values)*.95)-1)],'max':values[-1]} if values else None
summary={'completed':len(rows),'mock':any(r.get('mock',False) for r in rows)}
for key in ['pipeline_ms','cpu_ms','rx_to_ai_end_ms']:
    summary[key]=stats([r[key] for r in rows if key in r])
if len(rows)>1:
    duration=(rows[-1]['ai_end_monotonic_ns']-rows[0]['ai_end_monotonic_ns'])/1e9
    summary['completed_fps_between_first_last']=(len(rows)-1)/duration if duration>0 else None
summary['active_inference_cpu_percent']=stats([100*r['cpu_ms']/r['pipeline_ms'] for r in rows if r.get('pipeline_ms',0)>=1 and 'cpu_ms' in r])
summary['cpu_note']='CPU ratio omitted for pipeline <1ms due to clock granularity; mock is not an inference benchmark'
summary['rss_peak_raw']=max((r.get('max_rss_raw',0) for r in rows),default=0)
summary['rss_unit']='bytes on macOS; KiB on Linux; whole worker process peak'
print(json.dumps(summary,ensure_ascii=False,indent=2))
