"""扣杂档期标准：按成交时刻锁定，档期边界左闭右开。

为什么锁定成交时间：
- 只按称重时间：成交后的让价/改价会被算进称重时所在旧档；
- 只按结算时间：旧场成交的货包会在零点后被新档重新结算；
- 成交时间是票面计价依据真正确定的时刻，晚到复秤只提供称重事实，不改变档期。
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import hashlib, json

from app.coerce import instant, number
from app.errors import BusinessError

LOCAL_TZ = timezone(timedelta(hours=8))
LOCK_BASIS = 'dealt_at'
LOCK_BASIS_LABEL = '成交时间'
SUPPORTED_BASES = {LOCK_BASIS}


def to_local(value):
    if isinstance(value, str):
        value = instant(value)
    return value.astimezone(LOCAL_TZ).isoformat()


def basis_label(basis):
    return {'dealt_at': '成交时间', 'weighed_at': '称重时间', 'settled_at': '结算时间'}.get(basis, basis)


def _normal_period(raw, index):
    if not isinstance(raw, dict):
        raise BusinessError('BAD_STANDARD', f'第{index + 1}个档期须为对象')
    code = str(raw.get('code', '')).strip()
    if not code:
        raise BusinessError('BAD_STANDARD', f'第{index + 1}个档期缺少编码')
    label = str(raw.get('label') or code).strip()
    start_raw = raw.get('effective_from', raw.get('from'))
    end_raw = raw.get('effective_to', raw.get('to'))
    if start_raw in (None, '') or end_raw in (None, ''):
        raise BusinessError('BAD_STANDARD', f'档期{code}缺少起止时间')
    start = instant(start_raw)
    end = instant(end_raw)
    if start >= end:
        raise BusinessError('BAD_STANDARD', f'档期{code}的起始时间须早于结束时间')
    try:
        rate = number(raw.get('rate'))
    except BusinessError:
        raise BusinessError('BAD_STANDARD', f'档期{code}的扣杂率无效')
    if not (Decimal('0') <= rate < Decimal('1')):
        raise BusinessError('BAD_STANDARD', f'档期{code}的扣杂率须在[0,1)内')
    return {'code': code, 'label': label, 'effective_from': to_local(start),
            'effective_to': to_local(end), 'rate': format(rate, 'f')}


def normalize_standard(raw):
    """校验并规范化档期标准；失败抛 BusinessError，调用方不得写入任何状态。"""
    if raw is None:
        raise BusinessError('BAD_STANDARD', '缺少扣杂档期标准')
    if not isinstance(raw, dict):
        raise BusinessError('BAD_STANDARD', '扣杂档期标准须为对象')
    basis = str(raw.get('basis') or raw.get('lock_basis') or LOCK_BASIS).strip()
    if basis not in SUPPORTED_BASES:
        raise BusinessError(
            'BAD_LOCK_BASIS',
            f'锁定依据必须是成交时间（dealt_at）；称重时间会把成交后的让价带入旧档，结算时间会把旧场货包按新档重结（收到 {basis}）')
    periods_raw = raw.get('periods')
    if not isinstance(periods_raw, list) or not periods_raw:
        raise BusinessError('BAD_STANDARD', '至少需要一个扣杂档期')
    periods = [_normal_period(item, index) for index, item in enumerate(periods_raw)]
    periods.sort(key=lambda item: instant(item['effective_from']))
    codes = [item['code'] for item in periods]
    if len(codes) != len(set(codes)):
        raise BusinessError('BAD_STANDARD', '档期编码重复')
    for previous, current in zip(periods, periods[1:]):
        if instant(previous['effective_to']) > instant(current['effective_from']):
            raise BusinessError('BAD_STANDARD', f"档期{previous['code']}与{current['code']}重叠")
    version = str(raw.get('version') or '').strip()
    normalized = {'version': version, 'basis': LOCK_BASIS, 'basis_label': LOCK_BASIS_LABEL,
                  'timezone': 'Asia/Shanghai', 'periods': periods}
    if not version:
        normalized['version'] = 'std-' + standard_digest(normalized)[:12]
    return normalized


def standard_digest(config):
    payload = {key: config[key] for key in ('basis', 'timezone', 'periods')}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def period_at(config, value):
    if value in (None, ''):
        return None
    point = instant(value) if not isinstance(value, datetime) else value
    for period in config['periods']:
        if instant(period['effective_from']) <= point < instant(period['effective_to']):
            return period
    return None


def period_code(config, value):
    period = period_at(config, value)
    return period['code'] if period else ''


def public_config(config):
    return {**config, 'digest': standard_digest(config)}


def _preset(version, label, periods):
    return {'version': version, 'label': label, 'basis': LOCK_BASIS, 'periods': periods}


_OLD = {'code': 'D1', 'label': '17日夜场旧档', 'effective_from': '2026-11-17T12:00:00+08:00',
        'effective_to': '2026-11-18T00:00:00+08:00'}
_NEW = {'code': 'N1', 'label': '18日零点新档', 'effective_from': '2026-11-18T00:00:00+08:00',
        'effective_to': '2026-11-18T12:00:00+08:00'}

PRESETS = {
    # 现场默认：交接班只换档期标识，扣杂率仍按 5%，用于核对锁档逻辑本身。
    'site-default': _preset('std-site-default', '现场默认（两档均 5%）', [
        {**_OLD, 'rate': '0.05'}, {**_NEW, 'rate': '0.05'}]),
    # 换档后新档扣杂率降到 4%，旧档货包仍按 5%。
    'night-tight': _preset('std-night-tight', '新档扣杂降至 4%', [
        {**_OLD, 'rate': '0.05'}, {**_NEW, 'rate': '0.04'}]),
    # 换档后新档扣杂率升到 6%。
    'night-loose': _preset('std-night-loose', '新档扣杂升至 6%', [
        {**_OLD, 'rate': '0.05'}, {**_NEW, 'rate': '0.06'}]),
    # 只覆盖夜场：17日旧档成交票会落在档期外，切换应整体失败并保留旧版。
    'night-only': _preset('std-night-only', '仅18日新档（会漏旧档）', [
        {**_NEW, 'rate': '0.04'}]),
}


def preset(name):
    if name not in PRESETS:
        raise BusinessError('BAD_PRESET', f'档期预设不存在：{name}')
    return PRESETS[name]


def preset_list():
    return [{'name': name, 'version': value['version'], 'label': value['label'],
             'periods': value['periods']} for name, value in PRESETS.items()]
