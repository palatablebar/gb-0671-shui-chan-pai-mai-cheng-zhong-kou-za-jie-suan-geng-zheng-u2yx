from fastapi import Request
from fastapi.templating import Jinja2Templates
from app.config import ROOT, META
from app.engine import evaluate
from app import store, standards

templates=Jinja2Templates(directory=str(ROOT/'app/templates'))

def _recalc_display(state, events):
    """展示用重算来源：优先用批次自带的血缘；同批重跑只留事件，按事件补足。"""
    stored=(state.get('manifest') or {}).get('lineage') or state.get('recalc') or {}
    if stored.get('reason')=='standard-switch':
        return stored
    reruns=[row for row in events if row['kind']=='rerun' and row['run_id']==state['manifest']['run_id']]
    if reruns:
        return {'reason':'rerun','source_run_id':state['manifest']['run_id'],
                'standard_version':stored.get('standard_version',''),
                'count':len(reruns),'at':reruns[-1]['created_at']}
    return stored or {'reason':'initial','source_run_id':None,
                      'standard_version':(state.get('standards') or {}).get('version','')}


def render(request:Request,key):
    from app.main import DB_PATH
    run_id=request.query_params.get('run_id')
    if run_id:
        state=store.get(DB_PATH,run_id)
    else:
        state=store.save(DB_PATH,evaluate({'dataset':request.query_params.get('dataset','dirty')}))
    # 换档重算会生成新批号，历史仍按来源批一并展示，避免新页面看不到换档经过。
    lineage=(state.get('manifest') or {}).get('lineage') or {}
    related=[state['manifest']['run_id']]
    if lineage.get('source_run_id'): related.append(lineage['source_run_id'])
    events=store.events(DB_PATH,related)
    if not lineage:
        # 同一标准已存在批次时，换档结果会复用已有批号，血缘仅保存在事件里。
        match=[row for row in events if row['kind']=='standard-switch' and row['accepted']
               and row['detail'].get('new_run_id')==state['manifest']['run_id']]
        if match:
            state['manifest']['lineage']={'source_run_id':match[-1]['run_id'],
                                          'source_standard_version':'','standard_version':match[-1]['standard_version']}
    return templates.TemplateResponse(request=request,name=f'{key}.html',context={
        'meta':META,'key':key,'state':state,'view':state['views'][key],
        'presets':standards.preset_list(),'switches':events,
        'recalc':_recalc_display(state,events)})
