from contextlib import asynccontextmanager
from importlib import import_module
from fastapi import FastAPI, Request, Body
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from app.config import ROOT, META, MODULES, DB_PATH
from app.errors import BusinessError
from app.engine import evaluate
from app import store, standards
from app.archive import serialize


def _standard_from_payload(payload):
    """换档请求既接受预设名，也接受完整档期对象；一律先规范化再计算。"""
    raw = payload.get('standard', payload.get('standards'))
    if isinstance(raw, str):
        raw = standards.preset(raw)
    if raw is None:
        raise BusinessError('BAD_STANDARD', '请提供档期标准或预设名')
    return standards.public_config(standards.normalize_standard(raw))


def _switch_problems(previous, candidate):
    problems = []
    rejections = [row for row in candidate['quarantine'] if row['code'] in {'NO_STANDARD_PERIOD', 'NO_DEALT_TIME'}]
    if rejections:
        problems.append(f"{len(rejections)} 行因成交时点不在任何档期内无法锁档")
    if not candidate['balance']['row_balanced']:
        problems.append('行守恒未通过')
    if not candidate['balance']['money_balanced']:
        problems.append('金额守恒未通过')
    if candidate['balance']['blocked']:
        problems.append(f"{candidate['balance']['blocked']} 张票存在版本冲突")
    if len(candidate['final']) != len(previous['final']):
        problems.append(f"生效票由 {len(previous['final'])} 变为 {len(candidate['final'])}")
    return problems


@asynccontextmanager
async def lifespan(app):
    store.save(DB_PATH, evaluate())
    yield
app=FastAPI(title=META['title'],lifespan=lifespan)
app.mount('/static',StaticFiles(directory=str(ROOT/'app/static')),name='static')
@app.exception_handler(BusinessError)
async def business_error(request:Request,error:BusinessError):
    return JSONResponse({'code':error.code,'detail':error.message},status_code=error.status)
@app.get('/')
def home():
    return RedirectResponse('/'+MODULES[0],status_code=303)
@app.get('/healthz')
def health():
    return {'status':'ok','project':META['short']}
@app.post('/api/runs')
def create(payload:dict=Body(default={})):
    return store.save(DB_PATH,evaluate(payload))
@app.post('/api/runs/{run_id}/rerun')
def rerun(run_id:str):
    previous=store.snapshot(DB_PATH,run_id)
    if 'input_snapshot' not in previous:
        raise BusinessError('INPUT_SNAPSHOT_MISSING','旧批缺少源快照，请重新导入')
    if 'standards' not in (previous['input_snapshot'].get('context') or {}):
        # 旧批没记录锁档标准，直接重跑会生成另一套结算；改走可审计的换档入口。
        raise BusinessError('STANDARD_MIGRATION_REQUIRED',
                            '该批为升级前旧版，未记录扣杂档期标准；请用“按新档重算”或重新导入，重跑本身不会改写旧账')
    # 重跑只复用该批不可变输入与生效标准；批号相同则 INSERT OR IGNORE，不新增批次或结算记录。
    result=evaluate({'dataset':previous['source']},snapshot=previous['input_snapshot'],
                    recalc={'reason':'rerun','source_run_id':run_id,
                            'standard_version':previous['manifest'].get('standard',{}).get('version','')})
    saved=store.save(DB_PATH,result)
    store.log_event(DB_PATH,run_id,'rerun',True,
                    standard=previous['manifest'].get('standard',{}),
                    detail={'run_id':saved['manifest']['run_id'],'sha256':saved['manifest']['sha256']})
    return saved
@app.get('/api/standards')
def standard_presets():
    return {'lock_basis':standards.LOCK_BASIS,'lock_basis_label':standards.LOCK_BASIS_LABEL,
            'presets':standards.preset_list()}
@app.get('/api/runs/{run_id}/switches')
def run_switches(run_id:str):
    store.get(DB_PATH,run_id)
    return store.events(DB_PATH,[run_id])
@app.post('/api/runs/{run_id}/switch')
def switch_standard(run_id:str,payload:dict=Body(default={})):
    previous=store.get(DB_PATH,run_id)
    snapshot=store.snapshot(DB_PATH,run_id)
    if 'input_snapshot' not in snapshot:
        raise BusinessError('INPUT_SNAPSHOT_MISSING','旧批缺少源快照，请重新导入')
    standard=_standard_from_payload(payload)
    raw_overrides=payload.get('overrides',{})
    if not isinstance(raw_overrides,dict):
        raise BusinessError('BAD_CONTEXT','参数须为对象')
    overrides={**raw_overrides,'standards':{key:standard[key] for key in ('version','basis','periods')}}
    try:
        candidate=evaluate({'dataset':previous['source']},snapshot=snapshot['input_snapshot'],
                           recalc={'reason':'standard-switch','source_run_id':run_id,
                                   'source_standard_version':previous['manifest'].get('standard',{}).get('version',''),
                                   'standard_version':standard['version']},
                           overrides=overrides)
    except BusinessError as error:
        store.log_event(DB_PATH,run_id,'standard-switch',False,standard,{'detail':error.message})
        raise
    problems=_switch_problems(previous,candidate)
    if problems:
        # 换档失败：只留拒绝记录，不写 runs/run_snapshots，旧版与其原始证据完全不动。
        store.log_event(DB_PATH,run_id,'standard-switch',False,standard,{'detail':'；'.join(problems)})
        raise BusinessError('SWITCH_REJECTED','换档重算未通过：'+'；'.join(problems)+'。已保留旧版，未新增结算。',409)
    saved=store.save(DB_PATH,candidate)
    store.log_event(DB_PATH,run_id,'standard-switch',True,standard,
                    {'new_run_id':saved['manifest']['run_id'],'detail':'旧版保留，可回看'})
    return saved
@app.get('/api/runs')
def runs():
    return store.history(DB_PATH)
@app.get('/api/runs/{run_id}')
def run(run_id:str):
    return store.get(DB_PATH,run_id)
@app.post('/api/runs/{run_id}/review')
def review(run_id:str,payload:dict=Body(...)):
    revision=payload.get('revision')
    if not isinstance(revision,int) or revision<1: raise BusinessError('BAD_REVISION','版本号须为正整数')
    return store.review(DB_PATH,run_id,revision)
@app.get('/api/export/{run_id}')
def export(run_id:str):
    result=store.get(DB_PATH,run_id)
    return Response(serialize(result),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="{run_id}.zip"'})
for key in MODULES:
    app.include_router(import_module(f'app.modules.{key}.handler').router)
