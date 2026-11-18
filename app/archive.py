import io, json, zipfile, hashlib
from app.errors import BusinessError

def build_seal(state):
    """封存摘要覆盖计价依据本身：换档重算会让封存指纹变化，证据与结论同源。"""
    payload={'final':state.get('final',[]),'standards':state.get('standards',{}),
             'pricing':[{k:row.get(k) for k in ('ticket','version','pricing_basis','lock_basis','lock_at','period_code')}
                        for row in state.get('settlements',[])]}
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def _raw_source(result):
    """原始源字节是称重/成交证据的根，凭证包必须带上而不是只带解析结果。"""
    import base64
    snapshot=result.get('input_snapshot') or {}
    encoded=snapshot.get('bytes_b64')
    if not encoded:
        return None
    try:
        return base64.b64decode(encoded,validate=True)
    except (ValueError,TypeError):
        return None


def _source_name(result):
    source=result.get('source','batch')
    return f'source-{source}.csv' if source else 'source.csv'


def serialize(result):
    if not result.get('manifest') or not result.get('views'):
        raise BusinessError('INCOMPLETE_RUN','批次尚未完成')
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json',json.dumps({**result['manifest'],'revision':result.get('revision',1),'status':result.get('status','待复核'),'recalc':result.get('recalc',{})},ensure_ascii=False,indent=2))
        archive.writestr('standards.json',json.dumps({'applied':result.get('standards',{}),
            'lock_basis':(result.get('standards') or {}).get('basis',''),
            'lock_basis_note':'零点换档以成交时间锁定；称重与复秤仅作证据'},ensure_ascii=False,indent=2))
        archive.writestr('settlements.json',json.dumps({'final':result.get('final',[]),
            'settlements':result.get('settlements',[]),'version_audit':result.get('version_audit',[]),
            'version_conflicts':result.get('version_conflicts',[])},ensure_ascii=False,indent=2))
        archive.writestr('evidence.json',json.dumps({'counts':result['counts'],'views':result['views'],
            'quarantine':result['quarantine'],'archive':result.get('archive',{})},ensure_ascii=False,indent=2))
        raw=_raw_source(result)
        if raw is not None:
            # 原始字节按原样封存，文件名为来源样例；不改编码、不重排，可独立复算。
            archive.writestr('source/'+_source_name(result),raw)
    return buffer.getvalue()
