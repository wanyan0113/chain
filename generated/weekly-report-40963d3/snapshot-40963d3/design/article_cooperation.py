"""Check table10 cooperation for ONE explicitly declared parallel chain group.

This verifies supplied chains; it neither selects a network nor verifies task
capabilities, artifact screening, schedules or simulation outcomes.
"""
from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from .engine import DesignError, condition_result, require_finite

SOURCES = {'link': '联想截图_20261027151000.png：表10法器协同规则',
           'network': '联想截图_20261027151000.png、联想截图_20261027151006.png：表10法术链协同规则'}


def cooperation_schema():
    nullable = lambda schema: {'anyOf': [schema, {'type':'null'}]}
    text = {'type':'string','minLength':1,'pattern':r'\S'}
    refs = {'type':'array','items':text,'uniqueItems':True}
    quantity = nullable({'type':'object','additionalProperties':False,'required':['value','unit'],
                         'properties':{'value':{'type':'number','minimum':0},'unit':text}})
    count = nullable({'type':'integer','minimum':1})
    def obj(properties, required):
        return {'type':'object','additionalProperties':False,'properties':properties,'required':required}
    artifact = obj({'id':text,'role':{'enum':['insight','coordination','manifestation']},
                    'detection_range':quantity,'max_tracks':count,'max_processing_targets':count,
                    'source_refs':refs}, ['id','role','source_refs'])
    chain = obj({'id':text,'task':text,'target_count':count,'source_refs':refs,
                 'insight':text,'coordination':text,'manifestation':text},
                ['id','task','source_refs','insight','coordination','manifestation'])
    link = obj({'from':text,'to':text,'communicates':nullable({'type':'boolean'}),
                'controls':nullable({'type':'boolean'}),'distance':quantity,'max_distance':quantity,
                'source_refs':refs},['from','to','source_refs'])
    context = obj({'task':text,'artifact':text,'target_distance':quantity,'source_refs':refs},
                  ['task','artifact','source_refs'])
    return obj({'scope':text,'mode':{'const':'declared_parallel_group'},'artifacts':{'type':'array','items':artifact},
                'chains':{'type':'array','minItems':1,'items':chain},'links':{'type':'array','items':link},
                'contexts':{'type':'array','items':context},'blocking_issue_ids':refs},
               ['scope','mode','artifacts','chains','links','contexts','blocking_issue_ids'])


def check_cooperation(document):
    require_finite(document)
    errors = list(Draft202012Validator(cooperation_schema()).iter_errors(document))
    if errors:
        raise DesignError('; '.join(error.message for error in errors[:5]))
    def unique(items, key, name):
        result = {}
        for item in items:
            identifier = key(item)
            if identifier in result:
                raise DesignError(f'{name}重复：{identifier}')
            result[identifier] = item
        return result
    artifacts = unique(document['artifacts'],lambda a:a['id'],'法器')
    unique(document['chains'],lambda c:c['id'],'链编号')
    tasks = unique(document['chains'],lambda c:c['task'],'任务（一个任务只接受一条待核验链）')
    links = unique(document['links'],lambda e:(e['from'],e['to']),'有向协同边')
    contexts = unique(document['contexts'],lambda c:(c['task'],c['artifact']),'情境')
    for chain in document['chains']:
        for role in ('insight','coordination','manifestation'):
            if chain[role] not in artifacts or artifacts[chain[role]]['role'] != role:
                raise DesignError(f"{chain['id']}的{role}引用不存在或角色不符")
    if any(a not in artifacts or b not in artifacts or a==b for a,b in links):
        raise DesignError('协同边悬空或自指')
    if any(t not in tasks or a not in artifacts for t,a in contexts):
        raise DesignError('情境悬空')
    checks = []
    def add(identifier, quote, status, evidence, group='network'):
        checks.append(dict(id=identifier,quote=quote,status=status,source=SOURCES[group],evidence=evidence))
    def compare(identifier, quote, left, right, evidence, group='network'):
        status, reason = condition_result({'field':'artifact.left','op':'gt','ref':'task.right'},
                                           {'artifact':{'left':left},'task':{'right':right}})
        add(identifier,quote,status,dict(left=left,right=right,reason=reason,**evidence),group)
    for chain in document['chains']:
        for first, second in [('insight','coordination'),('coordination','manifestation')]:
            pair = chain[first],chain[second]
            edge = links.get(pair,{})
            evidence = {'chain':chain['id'],'from':pair[0],'to':pair[1],
                        'source_refs':edge.get('source_refs',[])}
            flag = edge.get('communicates')
            add('communicates','法器之间需要具有魔法共鸣传讯关系',
                'unknown' if flag is None else 'pass' if flag else 'fail',evidence,'link')
            compare('communication-distance','法器之间空间距离小于最大传讯距离',
                    edge.get('max_distance'),edge.get('distance'),evidence,'link')
            if first=='coordination':
                flag = edge.get('controls')
                add('controls','统筹法器和具现法器之间需要具有统筹关系',
                    'unknown' if flag is None else 'pass' if flag else 'fail',evidence,'link')
    uses = defaultdict(list)
    for chain in document['chains']:
        for role in ('insight','coordination','manifestation'):
            uses[chain[role]].append(chain)
    for identifier, chains in uses.items():
        artifact = artifacts[identifier]
        role = artifact['role']
        evidence = {'artifact':identifier,'chains':[c['id'] for c in chains],
                    'source_refs':artifact['source_refs']}
        if role=='manifestation':
            add('single-manifestation-use','具现法器只能在一条法术链中',
                'pass' if len(chains)==1 else 'fail',evidence)
        elif len(chains)>1:
            # The source conditions apply to the same resource shared by
            # multiple parallel chains. Distinct resources have distinct sums.
            counts = [chain.get('target_count') for chain in chains]
            total = None if any(count is None for count in counts) else {'value':sum(counts),'unit':'1'}
            if role=='insight':
                for chain in chains:
                    context = contexts.get((chain['task'],identifier),{})
                    compare('shared-detection-range','共享探知距离需要同时大于所有任务目标距离',
                            artifact.get('detection_range'),context.get('target_distance'),
                            dict(evidence,task=chain['task'],context_source_refs=context.get('source_refs',[])))
                maximum = artifact.get('max_tracks')
                compare('shared-max-tracks','最大标记数需要大于所有任务目标数量总和',
                        None if maximum is None else {'value':maximum,'unit':'1'}, total,evidence)
            else:
                maximum = artifact.get('max_processing_targets')
                compare('shared-max-processing','最大解析目标数需要大于所有任务目标数量总和',
                        None if maximum is None else {'value':maximum,'unit':'1'}, total,evidence)
    if document['blocking_issue_ids']:
        add('input-issues','输入已登记疑点尚未确认（项目阻塞政策）','unknown',
            {'blocking_issue_ids':document['blocking_issue_ids']})
    statuses = {item['status'] for item in checks}
    return deepcopy({'scope':document['scope'],'mode':document['mode'],
                     'status':'fail' if 'fail' in statuses else 'unknown' if 'unknown' in statuses else 'pass',
                     'checks':checks,'input':document,
                     'validation_scope':'仅核验给定并行链组的表10协同条件；不验证筛选、能力、可用性、评分、时序、串行重用或仿真，不自动选网'})


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path)
    args=parser.parse_args()
    try:
        report=check_cooperation(json.loads(args.input.read_text(encoding='utf-8')))
        print(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
        return 0 if report['status']=='pass' else 1
    except (OSError,ValueError) as exc:
        print(json.dumps({'status':'invalid','error':str(exc)},ensure_ascii=False))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
