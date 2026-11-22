"""Freeze portable acceptance cases from the existing independent demo, not new truth."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fdna.workflow_common import csv_text,dumps,write_bundle
from fdna.network_workflow import META


def build_cases(demo):
    read=lambda name:json.loads((demo/name).read_text(encoding='utf-8'))
    network=read('输入_拓扑.json');config=read('输入_拟合配置.json');config['input_role']='observed'
    original=read('输入_SE记录.json')
    rows=[{k:deepcopy(v) for k,v in r.items() if k!='predictor_values'} for r in original if r['fit_group']=='rep1_sigma2']
    if len(rows)!=250:raise ValueError('预期冻结rep1_sigma2的250条已有观测')
    outputs={'network.json':dumps(network),'config.json':dumps(config),'observed.json':dumps(rows)}
    outputs['observed_wide.csv']=csv_text([{**{k:r[k] for k in META},**r['node_values']} for r in rows])
    outputs['observed_long.csv']=csv_text([{**{k:r[k] for k in META},'node_id':n,'se':r['node_values'][n]} for r in rows for n in sorted(network['nodes'])])
    cases=[]
    def case(name,expected,code,*,n=None,r=None,layout='wide',sample=None):
        nf=name+'_network.json' if n is not None else 'network.json';sf=name+'_samples.json' if r is not None else sample or 'observed.json'
        if n is not None:outputs[nf]=dumps(n)
        if r is not None:outputs[sf]=dumps(r)
        cases.append(dict(id=name,network=nf,samples=sf,config='config.json',layout=layout,expected_status=expected,expected_exit=code))
    case('valid_json','ready_with_warnings',0)
    case('valid_wide','ready_with_warnings',0,sample='observed_wide.csv')
    case('valid_long','ready_with_warnings',0,layout='long',sample='observed_long.csv')
    r=deepcopy(rows);r[0]['node_values'].pop('D2');case('missing_one_root','ready_with_warnings',0,r=r)
    r=deepcopy(rows)
    for row in r:row['node_values'].pop('F11')
    case('missing_receiver_all','needs_data',1,r=r)
    n=deepcopy(network);n['edges'].append(deepcopy(n['edges'][0]));case('duplicate_edge','invalid',2,n=n)
    n=deepcopy(network);n['edges'][0]['feeder']='unknown';case('unknown_edge_node','invalid',2,n=n)
    n=deepcopy(network);n['edges'].append(dict(feeder='F11',receiver='D2'));case('cycle','invalid',2,n=n)
    n=deepcopy(network);n['edges'].append(dict(feeder='S5',receiver='S5'));case('self_loop','invalid',2,n=n)
    r=deepcopy(rows);r.append(deepcopy(r[0]));case('duplicate_sample','invalid',2,r=r)
    r=deepcopy(rows);r[0]['node_values']['D2']=101.;case('invalid_se','invalid',2,r=r)
    r=deepcopy(rows);r[0]['node_values']['unknown']=50.;case('unknown_se_node','invalid',2,r=r)
    r=deepcopy(rows);train=next(r for r in r if r['split']=='train');next(row for row in r if row['split']=='holdout')['base_case_id']=train['base_case_id'];case('split_leakage','invalid',2,r=r)
    r=[deepcopy(r) for r in rows if r['split']=='train'][:2]+[deepcopy(r) for r in rows if r['split']=='holdout'][:2]
    case('insufficient_samples','needs_data',1,r=r)
    r=deepcopy(rows)
    for row in r:row['node_values']={n:50. for n in network['nodes']}
    case('constant_inputs','ready_with_warnings',0,r=r)
    outputs['ragged.csv']=outputs['observed_wide.csv'].rstrip()+'\n'+','.join(['x']*(len(META)+len(network['nodes'])+1))+'\n'
    case('ragged_csv','invalid',2,sample='ragged.csv')
    provenance=dict(source='既有第22包独立机制模拟数据',source_file_sha256=hashlib.sha256((demo/'输入_SE记录.json').read_bytes()).hexdigest(),
        selected_group='rep1_sigma2',samples=250,train=200,holdout=50,source_noise_sigma=2,input_role='observed',
        simulated=True,predictor_values_removed=True,real_data_calibration=False,
        mutation_policy='异常案例从冻结观测副本构造，单独命名；不用于估计真实参数。')
    outputs.update({'cases.json':dumps(cases),'provenance.json':dumps(provenance)})
    outputs['README.md']='''# V1.0 可复跑输入验收案例

有效数据取自已有独立机制11节点18边模拟的rep1_sigma2，250条观测、200训练和50留出；保留原划分，移除全部干净供给字段，按observed角色读取。仍是模拟数据，不是实测校准，也未建模供给测量误差。

cases.json列出每例输入文件、布局、预期状态与退出码。invalid表示拒绝结构或非法记录；needs_data表示有效训练数据不足；ready_with_warnings保留缺失、常数输入或覆盖警告，不表示参数可辨识。缺失值不补0。

从仓库根目录运行：

```powershell
python -B -m fdna.release_acceptance --output-dir generated/汇报用/V1验收_新运行
```

该命令逐例运行预检查、保存日志，核对宽/长表与JSON映射，执行一组observed完整拟合及样本不足的失败传播验收，输出图表和结果说明。输出目录不可非空。单例可用network_preflight入口和cases.json中的路径复跑。
'''
    return outputs


def main():
    p=argparse.ArgumentParser();p.add_argument('--demo-dir',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args()
    write_bundle(a.output_dir,build_cases(a.demo_dir))


if __name__=='__main__':main()
