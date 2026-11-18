from collections import defaultdict
from decimal import Decimal
from app.coerce import money

def select_versions(settlements):
    grouped=defaultdict(list)
    for row in settlements: grouped[row['ticket']].append(row)
    final=[];conflicts=[];audit=[]
    for ticket,versions in sorted(grouped.items()):
        by_v={r['version']:r for r in versions};current=None;invalid=False
        # 同票所有版本必须共用同一计价依据，否则视为锁定漂移，整票阻断。
        pricing={r.get('pricing_key') for r in versions if r.get('pricing_key')}
        if len(pricing)>1:
            conflicts.append({'ticket':ticket,'code':'PRICING_BASIS_DRIFT','version':versions[-1]['version'],
                              'detail':'同票不同版本锁定了不同扣杂档期'})
            continue
        for row in sorted(versions,key=lambda r:r['version']):
            target=row['replaces'].strip()
            if current is None:
                if target: conflicts.append({'ticket':ticket,'code':'MISSING_BASE','version':row['version']});invalid=True;break
            elif target!=str(current['version']):
                conflicts.append({'ticket':ticket,'code':'STALE_CORRECTION','version':row['version'],'expected':current['version']});invalid=True;break
            audit.append({'ticket':ticket,'version':row['version'],'replaces':target,'amount':row['amount'],
                          'period_code':row.get('period_code',''),'period_label':row.get('period_label',''),
                          'pricing_basis':row.get('pricing_basis',''),'lock_at':row.get('lock_at',''),
                          'lock_basis_label':row.get('lock_basis_label',''),'lines':row['lines']})
            current=row
        if current and not invalid:
            paid=max(Decimal(r['paid']) for r in versions)
            final.append({**current,'paid':money(paid),'due':money(Decimal(current['amount'])-paid),
                          'code':'CORRECTED' if len(versions)>1 else 'ORIGINAL'})
    return final,conflicts,audit

def apply(state):
    final,conflicts,audit=select_versions(state['settlements'])
    state.update(final=final,version_conflicts=conflicts,version_audit=audit)
    state['views']['correction']={'title':'更正裁决','rows':final+conflicts,'metrics':{'生效票':len(final),'版本冲突':len(conflicts),'追溯版':len(audit)}}
