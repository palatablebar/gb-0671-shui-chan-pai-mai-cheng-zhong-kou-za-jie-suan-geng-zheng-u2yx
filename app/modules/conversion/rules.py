from app.coerce import number
from app.errors import BusinessError
from app.reasons import reject
from app.standards import to_local

def _time(row, key):
    text=(row.get(key) or '').strip()
    return to_local(text) if text else ''

def normalize(row):
    out=dict(row)
    factor={'kg':1,'t':1000,'公斤':1}.get(row['unit'])
    if factor is None: raise BusinessError('BAD_UNIT','称重单位不受支持')
    if row['kind'] not in {'gross','tare'}: raise BusinessError('BAD_KIND','称重类型无效')
    weight=number(row['weight'])*factor;price=number(row['price']);rate=number(row['rate'])
    paid=number(row['paid']);version=number(row['version'])
    if weight<0 or price<0 or paid<0 or not 0<=rate<1 or version<1 or version%1:
        raise BusinessError('INVALID_RANGE','重量、单价、扣杂率或版本越界')
    out.update(weight_kg=str(weight),price=str(price),rate=str(rate),paid=str(paid),version=int(version))
    if not row['ticket'].strip() or not row['fish'].strip(): raise BusinessError('UNKNOWN_REFERENCE','票号或品类缺失')
    for key in ('weighed_at','dealt_at','reweighed_at'):
        out[key]=_time(row,key)
    # 成交时间是锁档依据，缺失即无法计价；称重与复秤可以后补，故只对成交时点强制。
    if not out['dealt_at']: raise BusinessError('BAD_TIME','缺少成交时间，无法锁定扣杂档期')
    return out

def apply(state):
    good=[]
    for row in state['records']:
        try: good.append(normalize(row))
        except BusinessError as error: reject(state,row,error.code,error.message)
    state['records']=good
    state['views']['conversion']={'title':'重量换算','rows':good,'metrics':{'规范行':len(good),'隔离行':len(state['quarantine'])}}
