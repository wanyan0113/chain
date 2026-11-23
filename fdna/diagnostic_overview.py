"""Condition-specific reading views of existing cards; no fitting or model selection."""
from .model import ModelError


FLAG_LABELS = {
    'LOCAL_RANK_DEFICIENT': '局部秩不足',
    'INACTIVE_PARAMETERS': '存在不活跃参数',
    'MULTISTART_PARAMETER_SPREAD': '多初值参数跨度',
    'DELTA_AT_BOUNDARY': 'δ触界',
    'EDGE_PARAMETER_AT_BOUNDARY': '边参数触界',
    'PREDICTOR_COVERAGE_EXTRAPOLATION': '输入覆盖外推',
    'PREDICTION_OUTSIDE_SE_RANGE': '预测越界',
    'NO_HOLDOUT_PREDICTION': '缺少留出预测',
}


def cell(value):
    if value is None:
        return '—'
    if isinstance(value, float):
        return f'{value:.6g}'
    if isinstance(value, (list, tuple)):
        value = '；'.join(str(v) for v in value)
    return str(value).replace('|', '/').replace('\n', ' ')


def evidence_rows(cards, index):
    """Keep each fit separate and distinguish evaluation coverage from usability."""
    paths = {r['fit_id']: r['card'] for r in index}
    if len(paths) != len(index):
        raise ModelError('总览索引存在重复fit_id')
    result = []
    seen = set()
    for card in sorted(cards, key=lambda c: (c['fit_group'], c['node'], c['model'])):
        identity = card['fit_id']
        if identity in seen or identity not in paths:
            raise ModelError('总览卡片重复或缺少索引')
        seen.add(identity)
        metrics = {}
        for m in card['metrics']:
            key = m['prediction'] + '_' + m['split']
            if key in metrics:
                raise ModelError('同次拟合存在重复预测指标')
            metrics[key] = m
        row = {k: card[k] for k in ('experiment_id', 'fit_id', 'fit_group', 'node', 'model',
                                    'status', 'data_sha256', 'split_version', 'source_record')}
        row.update(card=paths[identity], prediction_usability='not_assessed_no_application_threshold',
                   flags=card['flags'], flag_labels=[FLAG_LABELS.get(f, f) for f in card['flags']])
        for kind in ('local', 'recursive'):
            for split in ('train', 'holdout'):
                key = kind + '_' + split
                metric = metrics.get(key, {})
                for field in ('rmse', 'mae', 'evaluated', 'available', 'outside', 'rmse_lower_bound'):
                    row[key + '_' + field] = metric.get(field)
            metric = metrics.get(kind + '_holdout', {})
            count = metric.get('evaluated', 0)
            available = metric.get('available', 0)
            expected = card['split_counts'].get('holdout', 0)
            row[kind + '_holdout_evidence'] = (
                'not_evaluated' if not count or metric.get('rmse') is None else
                'evaluated_all_records' if count == available == expected else 'evaluated_partial_records')
        sensitivity = card['sensitivity']
        row.update(rank=sensitivity.get('local_jacobian_rank'), parameter_count=sensitivity.get('parameter_count'),
                   inactive_parameters=sensitivity.get('inactive_parameters', []))
        if card['status'] != 'fitted':
            explanation = '拟合未完成，参数解释受限'
        elif row['rank'] is None or row['parameter_count'] is None:
            explanation = '缺少局部约束诊断，不能判断参数约束'
        elif row['rank'] < row['parameter_count'] or row['inactive_parameters']:
            explanation = '局部参数约束不足，不能认定唯一'
        else:
            explanation = '观察到局部约束证据，不证明全局唯一'
        if 'MULTISTART_PARAMETER_SPREAD' in card['flags']:
            explanation += '；多初值存在参数跨度'
        row['parameter_interpretation'] = explanation
        delta = next((p for p in card['parameters'] if p['name'] == 'delta'), {})
        row.update(delta_policy=card['delta_policy'], delta=delta.get('value'),
                   delta_lower=delta.get('lower'), delta_upper=delta.get('upper'),
                   delta_boundary='DELTA_AT_BOUNDARY' in card['flags'])
        for split in ('train', 'holdout'):
            coverage = next((p for p in card['prediction_coverage'] if p['split'] == split), {})
            for kind in ('local', 'recursive'):
                row[kind + '_' + split + '_outside_training'] = coverage.get(kind + '_outside_training')
        result.append(row)
    if seen != set(paths):
        raise ModelError('总览索引与卡片不一一对应')
    return result


def overview_markdown(rows, contrasts, stats, source_kind):
    lines = ['# 优化第一步：已有结果诊断总览', '',
        '本页只整理06冻结结果；不重新拟合、不生成SE、不选择最优模型。', '',
        f"来源性质：{source_kind}；实验标识：`{stats['experiment_id']}`。", '',
        f"本次覆盖{stats['cards']}次局部拟合、{stats['delta_pairs']}组δ对照、"
        f"{stats['node_audit_rows']}个能力变量和{stats['candidate_audit_rows']}条候选关系"
        f"（{stats['exported_edges']}条导出边）。", '',
        '**预测表现已经评估，不等于已经判定可用。** 当前未登记应用容许误差，不生成可信等级或置信概率；'
        '以下保留环境、节点、模型和局部/递推条件，不将不同拟合合并成节点总分。', '',
        '## 查看入口', '',
        '- [变量与依赖依据阅读表](审计阅读表.md)：逐项复核指标、产物消费、登记状态和来源。',
        '- [诊断卡片完整索引](诊断卡片总表.md)：查看参数、分支、边界、起点和原始记录位置。',
        '- [结构化总览](诊断结果总览.json)与[CSV总览](诊断结果总览.csv)：保留fit_id及未舍入数值。', '',
        '## 如何读取', '',
        '局部预测使用同次记录的供给SE；整网递推从外部根输入逐级计算。两种误差分别阅读。'
        '“已评估全部留出”只说明本组记录覆盖；部分或缺少留出应保留证据不足状态。', '',
        '外推列是留出输入超出训练范围的记录数（局部/递推），不等于预测值越界；'
        '预测越界单独列出。局部满秩不证明全局唯一，秩不足也不直接证明预测无效。', '',
        '## 诊断标记计数', '', '| 标记 | 拟合次数 |', '| --- | ---: |']
    lines += [f'| {FLAG_LABELS.get(flag, flag)} | {count} |'
              for flag, count in sorted(stats['flag_counts'].items())]
    lines += ['', '标记可能重叠，不能相加作为拟合总数。']
    labels = {'evaluated_all_records': '全部留出已评估', 'evaluated_partial_records': '部分留出已评估',
              'not_evaluated': '缺少留出评估'}
    for group in sorted({r['fit_group'] for r in rows}):
        lines += ['', '## ' + cell(group), '',
            '| 节点/模型（卡片） | 留出证据（局部/递推） | 留出RMSE（局部/递推） | 局部秩/参数数 | δ及边界 | 留出外推（局部/递推） | 留出预测越界（局部/递推） | 参数解释 |',
            '| --- | --- | --- | --- | --- | --- | --- | --- |']
        for r in rows:
            if r['fit_group'] != group:
                continue
            pair = lambda field: '/'.join(cell(r[k + '_holdout_' + field]) for k in ('local', 'recursive'))
            delta = '未启用' if r['delta_policy'] == 'excluded_for_base_check' else (
                cell(r['delta']) + ' [' + cell(r['delta_lower']) + ',' + cell(r['delta_upper']) + ']'
                + ('（触界）' if r['delta_boundary'] else ''))
            values = [f"[{cell(r['node'])} / {cell(r['model'])}]({r['card']})",
                '/'.join(labels[r[k + '_holdout_evidence']] for k in ('local', 'recursive')),
                pair('rmse'), cell(r['rank']) + '/' + cell(r['parameter_count']), delta,
                pair('outside_training'), pair('outside'), cell(r['parameter_interpretation'])]
            lines.append('| ' + ' | '.join(values) + ' |')
    lines += ['', '## 同数据δ对照', '',
        '改善值为“基础式RMSE−比较模型RMSE”，正值只表示本批留出误差下降；'
        '不按留出结果选择模型，不据此认定δ具有唯一物理意义。06未运行固定δ组。', '',
        '| 环境 | 节点 | 比较模型 | 局部留出改善 | 递推留出改善 | δ | δ触界 |',
        '| --- | --- | --- | ---: | ---: | ---: | --- |']
    for r in contrasts:
        lines.append('| ' + ' | '.join(cell(v) for v in (r['fit_group'], r['node'], r['comparison_model'],
            r['local_holdout_improvement'], r['recursive_holdout_improvement'], r['delta'],
            '是' if r['delta_boundary'] else '否')) + ' |')
    lines += ['', '## 本步完成后的边界', '',
        '参数解释、预测表现、登记依赖和真实因果分别判断。本页不引入容许误差、固定δ实验、'
        '简单预测基线、拓扑替代或重采样。后续是否开展这些实验，按研究主张决定。', '']
    return '\n'.join(lines)


def audit_markdown(nodes, edges):
    lines = ['# 能力指标与依赖依据阅读表', '',
        '来源性质与形成方式分别保存。合成输入不是实测事实；approved_assumption表示登记的工作假设，'
        '不是已确认的真实依赖。完整字段见[映射审计JSON](映射审计.json)。', '',
        '## 能力变量', '',
        '| 节点 | 主体/功能 | 任务上下文 | 输入产物 | 输出产物 | SE指标 | 归一化 | 来源性质 | 定义形成方式 | 登记状态与说明 |',
        '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for n in nodes:
        values = [n['node'], n['subject'] + '/' + n['function'], n['task_context'], n['inputs'], n['outputs'],
                  n['metric'], n['normalization'], n['source_kind'], n['definition_evidence_form'],
                  cell(n['review_status']) + '；' + cell(n['review_note'])]
        lines.append('| ' + ' | '.join(cell(v) for v in values) + ' |')
    lines += ['', '## 候选依赖', '',
        '通道可用不自动证明效能依赖；共享主体、心跳和间接可达也不自动导出直接边。'
        '消费依据形成方式与通道证据分开列出，产品匹配是程序一致性检查。', '',
        '| 候选 | 供给→接受 | 产物 | 供给/消费匹配 | 导出 | 登记状态 | 登记依据 | 来源性质 | 消费依据形成方式 |',
        '| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for e in edges:
        values = [e['candidate_id'], e['feeder'] + '→' + e['receiver'], e['product'],
                  f"{e['produced']}/{e['consumed']}", '是' if e['exported'] else '否', e['review_status'],
                  e['rationale'], e['source_kind'], e['consumption_evidence_form']]
        lines.append('| ' + ' | '.join(cell(v) for v in values) + ' |')
    lines += ['', '共享状态及通道证据详见[映射审计JSON](映射审计.json)；'
              '[能力变量CSV](能力变量审计.csv)和[候选关系CSV](依赖候选审计.csv)可用于逐项复核。', '']
    return '\n'.join(lines)
