from importlib import import_module
from copy import deepcopy
import json, time, base64
from app.config import ROOT, MODULES, MAX_BYTES
from app.errors import BusinessError
from app.manifest import digest, make_manifest
from app.standards import normalize_standard, public_config

def evaluate(payload=None, until=7, snapshot=None, overrides=None, recalc=None):
    payload=payload or {}
    if not isinstance(payload,dict): raise BusinessError('BAD_CONTEXT','参数须为对象')
    dataset=payload.get('dataset','dirty')
    if dataset not in {'dirty','clean','legacy'}: raise BusinessError('BAD_DATASET','样例名称无效')
    raw_overrides=payload.get('overrides',{})
    if not isinstance(raw_overrides,dict): raise BusinessError('BAD_CONTEXT','参数须为对象')
    applied=deepcopy(raw_overrides)
    applied.update(overrides or {})
    raw=(base64.b64decode(snapshot['bytes_b64'],validate=True) if snapshot is not None else (str(payload['csv_text']).encode() if 'csv_text' in payload else (ROOT/'sample_data'/f'{dataset}.csv').read_bytes()))
    if len(raw)>MAX_BYTES: raise BusinessError('TOO_LARGE','文件超过2MB',413)
    context=deepcopy(snapshot['context']) if snapshot is not None else json.loads((ROOT/'sample_data/context.json').read_text())
    context.update(deepcopy(applied))
    if 'standards' not in context:
        # 兼容升级前落地的旧快照：缺档期标准时按当前发布标准计算，绝不静默按票面率跳过锁档。
        context['standards']=json.loads((ROOT/'sample_data/context.json').read_text())['standards']
    # 先校验档期标准再动任何数据：标准不合法时整次计算失败，绝不写入半成品批次。
    standards=normalize_standard(context.get('standards'))
    context['standards']=standards
    # 数据血缘只由服务端重跑/换档入口写入，客户端参数不能伪造来源。
    lineage=deepcopy(recalc) if isinstance(recalc,dict) else None
    start=time.perf_counter()
    state={'raw_bytes':raw,'context':context,'records':[],'quarantine':[],'counts':{},'views':{},
           'source':dataset,'standards':public_config(standards),
           'recalc':lineage or {'reason':'initial','source_run_id':None,
                                'standard_version':standards['version']}}
    for index,key in enumerate(MODULES,1):
        if index>until: break
        import_module(f'app.modules.{key}.rules').apply(state)
    state['input_snapshot']={'bytes_b64':base64.b64encode(raw).decode(),'context':deepcopy(context)}
    state.pop('raw_bytes',None)
    state['manifest']=make_manifest(digest(raw,context),dataset,time.perf_counter()-start,
                                    standards=state['standards'],lineage=lineage)
    return state
