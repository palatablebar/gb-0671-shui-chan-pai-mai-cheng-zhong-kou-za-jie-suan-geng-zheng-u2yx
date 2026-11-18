REASONS = {
    'COLUMN_COUNT':'列数错误', 'MISSING_ID':'编号缺失', 'DUPLICATE_CONFLICT':'重复编号冲突',
    'MISSING_VALUE':'数值缺失', 'BAD_NUMBER':'数值格式错误', 'BAD_TIME':'时间格式错误',
    'BAD_UNIT':'单位不受支持', 'UNKNOWN_REFERENCE':'引用对象不存在', 'INVALID_RANGE':'范围错误',
    'VERSION_CONFLICT':'版本冲突', 'EMPTY_FILE':'空文件', 'BLANK_FILE':'全空行',
    'HEADER_ONLY':'仅有表头', 'STALE_VERSION':'复核版本已更新', 'NOT_FOUND':'批次不存在',
    'NO_STANDARD_PERIOD':'成交时点不在任何扣杂档期', 'PAIR_TIME_MISMATCH':'毛皮成交时点不一致',
    'PRICING_BASIS_DRIFT':'同票计价档期不一致', 'BAD_STANDARD':'扣杂档期标准无效',
    'BAD_LOCK_BASIS':'锁定依据不受支持', 'BAD_PRESET':'档期预设不存在',
    'SWITCH_REJECTED':'换档重算未通过，保留旧版', 'INPUT_SNAPSHOT_MISSING':'旧批缺少源快照',
    'STANDARD_MIGRATION_REQUIRED':'旧批缺少档期标准，须走换档重算',
}
def reject(state, record, code, detail):
    state['quarantine'].append({'line':record.get('_line',0),'id':record.get('id',''),
       'code':code,'detail':detail,'raw':{k:v for k,v in record.items() if not k.startswith('_')}})
