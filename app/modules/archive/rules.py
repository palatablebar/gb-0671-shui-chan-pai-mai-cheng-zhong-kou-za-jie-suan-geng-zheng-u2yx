from app.archive import build_seal

def archive_decision(state):
    ready=state['balance']['row_balanced'] and state['balance']['money_balanced'] and not state['balance']['blocked']
    seal=build_seal(state)
    return {'seal':seal,'status':'可归档' if ready else '待处理','tickets':len(state['final']),
            'reasons':len(state['quarantine']),'lock_basis':(state.get('standards') or {}).get('basis',''),
            'standard_version':(state.get('standards') or {}).get('version','')}

def apply(state):
    state['archive']=archive_decision(state)
    state['views']['archive']={'title':'结算封存','rows':state['version_audit'],
        'metrics':{'封存票':state['archive']['tickets'],'隔离证据':len(state['quarantine']),
                   '状态':state['archive']['status'],'锁档依据':state['archive']['lock_basis']}}
