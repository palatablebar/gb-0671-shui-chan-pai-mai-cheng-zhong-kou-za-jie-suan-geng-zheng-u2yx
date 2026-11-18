import io, json, zipfile

import pytest
from fastapi.testclient import TestClient

from app import main
from app.engine import evaluate
from app.errors import BusinessError
from app.standards import normalize_standard, period_code


def row(sample, ticket, version=1):
    return next(r for r in sample['settlements'] if r['ticket'] == ticket and r['version'] == version)


def test_lock_uses_dealt_not_weigh(sample):
    # 00013：旧档称重、零点后成交 → 必须锁新档。
    settlement = row(sample, '00013')
    assert settlement['period_code'] == 'N1'
    assert '称重落在别档' in settlement['cross_reason']


def test_old_deal_late_reweigh_stays_old(sample):
    # 00014：旧档成交、新档晚到复秤 → 仍锁旧档，复秤只作证据。
    settlement = row(sample, '00014')
    assert settlement['period_code'] == 'D1'
    assert '复秤落在别档' in settlement['cross_reason']
    assert settlement['cross_period'] is True


def test_correction_version_inherits_base_period(sample):
    # 00001 v2 是零点后复称重与版本次成交，但基础版旧档成交，整票仍按旧档。
    base = row(sample, '00001', 1)
    corrected = row(sample, '00001', 2)
    assert base['period_code'] == corrected['period_code'] == 'D1'
    assert corrected['lock_at'] == base['lock_at']
    assert corrected['locked_by'] == '基础版本v1'
    assert corrected['pricing_key'] == base['pricing_key']


def test_pricing_drift_blocks_ticket():
    from app.modules.correction.rules import select_versions
    rows = [
        {'ticket': '9', 'version': 1, 'replaces': '', 'amount': '1.00', 'paid': '0', 'lines': [1],
         'pricing_key': 'D1|dealt_at|t1'},
        {'ticket': '9', 'version': 2, 'replaces': '1', 'amount': '2.00', 'paid': '0', 'lines': [3],
         'pricing_key': 'N1|dealt_at|t1'},
    ]
    final, conflicts, _ = select_versions(rows)
    assert final == []
    assert conflicts[0]['code'] == 'PRICING_BASIS_DRIFT'


def test_basis_must_be_dealt_time():
    with pytest.raises(BusinessError) as error:
        normalize_standard({'basis': 'weighed_at', 'periods': [
            {'code': 'D1', 'effective_from': '2026-11-17T00:00:00+08:00',
             'effective_to': '2026-11-18T00:00:00+08:00', 'rate': '0.05'}]})
    assert error.value.code == 'BAD_LOCK_BASIS'


def test_periods_cannot_overlap():
    with pytest.raises(BusinessError) as error:
        normalize_standard({'periods': [
            {'code': 'A', 'effective_from': '2026-11-17T00:00:00+08:00', 'effective_to': '2026-11-18T02:00:00+08:00', 'rate': '0.05'},
            {'code': 'B', 'effective_from': '2026-11-18T00:00:00+08:00', 'effective_to': '2026-11-18T12:00:00+08:00', 'rate': '0.04'}]})
    assert error.value.code == 'BAD_STANDARD'


def test_period_boundary_left_closed(clean):
    standard = normalize_standard(clean['standards'])
    assert period_code(standard, '2026-11-17T23:59:59+08:00') == 'D1'
    assert period_code(standard, '2026-11-18T00:00:00+08:00') == 'N1'


def test_missing_dealt_time_isolated():
    text = ('id,ticket,kind,weight,unit,fish,price,rate,version,replaces,paid\n'
            'g1,1,gross,100,kg,黄鱼,10,0.05,1,,0\n'
            't1,1,tare,10,kg,黄鱼,10,0.05,1,,0\n')
    result = evaluate({'csv_text': text})
    assert result['settlements'] == []
    assert any(r['code'] == 'BAD_TIME' for r in result['quarantine'])


def test_outside_period_isolated():
    standard = {'version': 'only-night', 'basis': 'dealt_at', 'periods': [
        {'code': 'N1', 'effective_from': '2026-11-18T00:00:00+08:00',
         'effective_to': '2026-11-18T12:00:00+08:00', 'rate': '0.04'}]}
    result = evaluate({'overrides': {'standards': standard}})
    # 只覆盖新档：旧档成交票整票不结算并留隔离原因，新档票仍正常计价。
    assert {r['period_code'] for r in result['settlements']} == {'N1'}
    assert any(r['code'] == 'NO_STANDARD_PERIOD' for r in result['quarantine'])
    assert result['balance']['money_balanced'] is True


def test_row_conservation_with_period_rejection():
    standard = {'version': 'only-night', 'basis': 'dealt_at', 'periods': [
        {'code': 'N1', 'effective_from': '2026-11-18T00:00:00+08:00',
         'effective_to': '2026-11-18T12:00:00+08:00', 'rate': '0.04'}]}
    result = evaluate({'overrides': {'standards': standard}})
    assert result['balance']['row_balanced'] is True


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DB_PATH', str(tmp_path / 'switch.sqlite'))
    return TestClient(main.app)


def test_rerun_adds_no_settlement(client):
    first = client.post('/api/runs', json={}).json()
    run_id = first['manifest']['run_id']
    for _ in range(3):
        again = client.post(f'/api/runs/{run_id}/rerun', json={}).json()
        assert again['manifest']['run_id'] == run_id
        assert len(again['settlements']) == len(first['settlements'])
    history = client.get('/api/runs').json()
    assert [item['id'] for item in history] == [run_id]


def test_failed_switch_keeps_old_version(client):
    first = client.post('/api/runs', json={}).json()
    run_id = first['manifest']['run_id']
    before = client.get(f'/api/runs/{run_id}').json()
    response = client.post(f'/api/runs/{run_id}/switch', json={'standard': 'night-only'})
    assert response.status_code == 409
    assert response.json()['code'] == 'SWITCH_REJECTED'
    after = client.get(f'/api/runs/{run_id}').json()
    assert after['manifest']['sha256'] == before['manifest']['sha256']
    assert after['balance'] == before['balance']
    # 没有新增批次，也没有新增结算。
    assert [item['id'] for item in client.get('/api/runs').json()] == [run_id]
    events = client.get(f'/api/runs/{run_id}/switches').json()
    assert events[-1]['accepted'] == 0


def test_successful_switch_keeps_old_run_and_evidence(client):
    first = client.post('/api/runs', json={}).json()
    run_id = first['manifest']['run_id']
    switched = client.post(f'/api/runs/{run_id}/switch', json={'standard': 'night-tight'}).json()
    assert switched['manifest']['run_id'] != run_id
    assert switched['manifest']['lineage']['source_run_id'] == run_id
    assert switched['manifest']['standard']['version'] == 'std-night-tight'
    # 旧版与原始证据原样保留。
    old = client.get(f'/api/runs/{run_id}').json()
    assert old['manifest']['standard']['version'] == 'std-site-default'
    assert old['input_snapshot']['bytes_b64'] == first['input_snapshot']['bytes_b64']
    assert old['quarantine'] == first['quarantine']
    assert client.get(f'/api/runs/{run_id}/switches').json()[-1]['accepted'] == 1


def test_switch_reuses_same_result_for_same_standard(client):
    first = client.post('/api/runs', json={}).json()
    run_id = first['manifest']['run_id']
    one = client.post(f'/api/runs/{run_id}/switch', json={'standard': 'night-tight'}).json()
    two = client.post(f'/api/runs/{run_id}/switch', json={'standard': 'night-tight'}).json()
    assert one['manifest']['run_id'] == two['manifest']['run_id']
    assert len(client.get('/api/runs').json()) == 2


def test_switch_changes_only_new_period_amounts(client):
    first = client.post('/api/runs', json={}).json()
    run_id = first['manifest']['run_id']
    switched = client.post(f'/api/runs/{run_id}/switch', json={'standard': 'night-tight'}).json()
    old_d1 = {r['ticket']: r['amount'] for r in first['settlements'] if r['period_code'] == 'D1'}
    new_d1 = {r['ticket']: r['amount'] for r in switched['settlements'] if r['period_code'] == 'D1'}
    assert old_d1 == new_d1
    old_n1 = {r['ticket']: r['amount'] for r in first['settlements'] if r['period_code'] == 'N1'}
    new_n1 = {r['ticket']: r['amount'] for r in switched['settlements'] if r['period_code'] == 'N1'}
    assert old_n1 != new_n1


def test_export_contains_standard_and_evidence(client):
    run = client.post('/api/runs', json={}).json()
    payload = client.get('/api/export/' + run['manifest']['run_id']).content
    bundle = zipfile.ZipFile(io.BytesIO(payload))
    names = set(bundle.namelist())
    assert {'manifest.json', 'standards.json', 'settlements.json', 'evidence.json'} <= names
    standards = json.loads(bundle.read('standards.json'))
    assert standards['applied']['basis'] == 'dealt_at'
    evidence = json.loads(bundle.read('evidence.json'))
    intake = next(view for view in evidence['views'].values() if view.get('rows') and 'weighed_at' in view['rows'][0])
    assert intake['rows'][0]['dealt_at']


def test_export_keeps_raw_source_bytes(client):
    import base64
    run = client.post('/api/runs', json={}).json()
    bundle = zipfile.ZipFile(io.BytesIO(client.get('/api/export/' + run['manifest']['run_id']).content))
    raw = bundle.read('source/source-dirty.csv')
    assert raw == base64.b64decode(run['input_snapshot']['bytes_b64'])
    # BOM 与原始换行原样保留，可直接作为复算输入。
    assert raw.startswith(b'\xef\xbb\xbf') and raw.count(b'\r\n') > 0


def test_exported_source_recomputes_same_batch(client):
    # 无 BOM 的样例可经 csv_text 原样提交，用凭证包里的原始字节复算得到同一批号。
    run = client.post('/api/runs', json={'dataset': 'clean'}).json()
    bundle = zipfile.ZipFile(io.BytesIO(client.get('/api/export/' + run['manifest']['run_id']).content))
    raw = bundle.read('source/source-clean.csv')
    again = client.post('/api/runs', json={'csv_text': raw.decode('utf-8')}).json()
    assert again['manifest']['sha256'] == run['manifest']['sha256']


def test_archive_seal_tracks_pricing_basis(client):
    run = client.post('/api/runs', json={}).json()
    switched = client.post(f"/api/runs/{run['manifest']['run_id']}/switch", json={'standard': 'night-tight'}).json()
    assert run['archive']['seal'] != switched['archive']['seal']
    assert run['archive']['lock_basis'] == 'dealt_at'


def test_page_shows_period_and_recalc_source(client):
    run = client.post('/api/runs', json={}).json()
    html = client.get('/auction?run_id=' + run['manifest']['run_id']).text
    for token in ('扣杂档期标准', '成交时间', '计价依据', '跨档', '换档尝试记录'):
        assert token in html
    switched = client.post(f"/api/runs/{run['manifest']['run_id']}/switch", json={'standard': 'night-tight'}).json()
    html = client.get('/auction?run_id=' + switched['manifest']['run_id']).text
    assert '换档重算' in html
    assert switched['manifest']['run_id'] in html
