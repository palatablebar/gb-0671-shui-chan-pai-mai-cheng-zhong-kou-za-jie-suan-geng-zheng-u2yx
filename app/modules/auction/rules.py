"""扣杂结算：按成交时点锁定档期，逐票留下采用的时段、跨档标记与重算来源。

锁定规则（这是本项目对“零点换档”的裁决）：
1. 唯一锁档依据是成交时间 dealt_at；称重时间与晚到复秤只作为证据展示。
2. 同一票的所有版本（原始版、更正版、复秤版）共用基础版本的成交时点所锁定的档期，
   更正版本不会把旧场货包按新档重结，保证“同一票的计价依据”前后一致。
3. 票面 rate 作为原始证据保留；实际计价采用所锁档期的标准扣杂率，差异显式标注。
4. 称重/复秤/版本次成交落在别的档期时标记跨档，但不改变已锁档期。
"""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from app.coerce import money
from app.reasons import reject
from app.standards import period_at, basis_label


def settle(pair, period=None):
    """不传 period 时沿用票面扣杂率，保持单票计算的独立可用性。"""
    rate = Decimal(period['rate']) if period else Decimal(pair['rate'])
    billable=(Decimal(pair['net_kg'])*(1-rate)).quantize(Decimal('.001'),rounding=ROUND_HALF_UP)
    out={**pair,'billable_kg':str(billable),'amount':money(billable*Decimal(pair['price'])),
         'ticket_rate':pair.get('rate','')}
    if period:
        out.update(period_code=period['code'],period_label=period['label'],
                   period_rate=period['rate'],period_from=period['effective_from'],
                   period_to=period['effective_to'])
    return out


def _cross_reasons(config, pair, period, base):
    reasons=[]
    if pair.get('weighed_at') and (period_at(config,pair['weighed_at']) or {}).get('code')!=period['code']:
        reasons.append('称重落在别档')
    if pair.get('reweighed_at') and (period_at(config,pair['reweighed_at']) or {}).get('code')!=period['code']:
        reasons.append('复秤落在别档')
    if pair is not base and pair.get('dealt_at') and (period_at(config,pair['dealt_at']) or {}).get('code')!=period['code']:
        reasons.append('版本次成交落在别档，按基础版档期计价')
    return reasons


def apply(state):
    config=state['standards']
    by_ticket=defaultdict(list)
    for pair in state['pairs']: by_ticket[pair['ticket']].append(pair)
    settlements=[];locked=[];rejected_lines=set()
    for ticket in sorted(by_ticket):
        versions=sorted(by_ticket[ticket],key=lambda r:(r['version'],(r.get('lines') or [0])[0]))
        base=versions[0]
        period=period_at(config,base.get('dealt_at'))
        if period is None:
            detail=f"成交时点 {base.get('dealt_at') or '空'} 不在任何扣杂档期内，整票不结算"
            for row in versions:
                for line in row.get('lines') or [0]:
                    rejected_lines.add(line)
                    reject(state,{'id':row['ticket'],'_line':line,'ticket':row['ticket']},
                           'NO_STANDARD_PERIOD',detail)
            continue
        for pair in versions:
            row=settle(pair,period)
            note=[]
            if Decimal(pair.get('rate') or '0')!=Decimal(period['rate']):
                note.append(f"票面{pair.get('rate')}与档期{period['rate']}不一致，按档期标准")
            reasons=_cross_reasons(config,pair,period,base)
            row.update(lock_basis='dealt_at',lock_basis_label=basis_label('dealt_at'),
                       lock_at=base.get('dealt_at',''),
                       locked_by='基础版本' if pair is base else f"基础版本v{base['version']}",
                       cross_period=bool(reasons),cross_reason='；'.join(reasons),
                       pricing_basis=f"{period['code']} {period['label']} / 扣杂{period['rate']}",
                       rate_note='；'.join(note))
            row['pricing_key']='|'.join((row['pricing_basis'],row['lock_basis'],row['lock_at']))
            settlements.append(row);locked.append(pair)
    state['records']=[r for r in state['records'] if r['_line'] not in rejected_lines]
    state['pairs']=locked
    state['settlements']=settlements
    cross=[p for p in settlements if p.get('cross_period')]
    total=sum((Decimal(p['amount']) for p in settlements),Decimal(0))
    state['views']['auction']={'title':'扣杂结算','rows':settlements,
        'metrics':{'版本票':len(settlements),'金额_CNY':money(total),'跨档票':len(cross)}}
