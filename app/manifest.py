import hashlib, json
from datetime import datetime, timezone
from app.config import RULE_VERSION

def digest(raw, context):
    # 批号指纹覆盖源字节、全部生效参数与规则版本；换档标准不同即不同批号，无重复结算。
    return hashlib.sha256(raw + json.dumps(context,ensure_ascii=False,sort_keys=True).encode() + RULE_VERSION.encode()).hexdigest()

def make_manifest(source_digest, source, elapsed, standards=None, lineage=None):
    manifest={'run_id':source_digest[:16], 'sha256':source_digest, 'rule_version':RULE_VERSION,
            'source':source, 'generated_at':datetime.now(timezone.utc).isoformat(),
            'elapsed_ms':round(elapsed*1000,3), 'timezone':'Asia/Shanghai'}
    if standards:
        manifest['standard']={'version':standards['version'],'digest':standards['digest'],
                              'lock_basis':standards['basis'],'lock_basis_label':standards['basis_label'],
                              'periods':standards['periods']}
    if lineage:
        manifest['lineage']=lineage
    return manifest
