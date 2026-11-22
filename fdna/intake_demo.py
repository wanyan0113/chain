"""Reproducible draft -> blocked check -> registered example -> compile -> fit demo."""
import argparse
import hashlib
from copy import deepcopy
import json
from pathlib import Path
import shutil
from .bridge_intake import draft_bundle, inspect_mapping, compile_bundle
from .bridge_demo import run as run_fit
from .workflow_common import dumps


def run(config_dir, output_dir):
    config=Path(config_dir);out=Path(output_dir)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):raise ValueError('输出目录须不存在或为空')
    read=lambda name:json.loads((config/name).read_text(encoding='utf-8'))
    source=read('source_report.json');ready=read('mapping.json')
    draft=draft_bundle(source,out/'01_自动待审草稿')
    draft_check=inspect_mapping(source,draft)
    if draft_check['status']!='blocked':raise ValueError('待审草稿不应通过审查')
    compiled=out/'02_已登记假设导出'
    ready_check=compile_bundle(source,ready,compiled)
    # Numeric generation assumptions cannot be inferred from an equipment report.
    # Use the separately frozen synthetic example, never auto-approve the draft.
    for name in ('generation_nodes.json','experiment.json','fit.json'):
        shutil.copyfile(config/name,compiled/name)
    changed=deepcopy(source);changed['scope']=changed.get('scope','')+' [变更校验样例]'
    stale=inspect_mapping(changed,ready)
    (out/'源报告变化校验.json').write_text(dumps(stale),encoding='utf-8')
    if stale['status']!='blocked':raise ValueError('变更后的报告不应通过旧映射摘要校验')
    counts=run_fit(compiled,out/'03_拟合与分析')
    trace=dict(draft=draft_check,registered_example=ready_check,changed_source=stale,fit=counts,
        note='已登记配置取自冻结合成示例，与草稿独立保存；工具没有自动执行专家审查。')
    (out/'接入流程核验.json').write_text(dumps(trace),encoding='utf-8')
    inventory={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()}
    (out/'本包全部产物校验.json').write_text(dumps(inventory),encoding='utf-8')
    return trace


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config-dir',type=Path,default=Path('data/fdna/bridge_intake'));parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();result=run(args.config_dir,args.output_dir);print(dumps(result));return 0 if result['fit']['status']=='complete' else 1

if __name__=='__main__':raise SystemExit(main())
