"""Publish a byte-auditable snapshot of verified propagation figures and results."""
import argparse
import hashlib
import json
from pathlib import Path


def publish(source,destination):
    source=Path(source);out=Path(destination)
    if out.exists() and any(out.iterdir()):raise ValueError('拒绝覆盖非空发布目录')
    read=lambda p:json.loads(p.read_text(encoding='utf-8'))
    audit=read(source/'独立标量复核.json')
    if audit['status']!='passed' or audit['responses_checked']!=405000:raise ValueError('需要通过完整独立复核')
    for name,key in [('manifest.json','source_manifest_sha256'),('propagation_report.json','report_sha256'),('逐模型逐基准数组.npz','arrays_sha256')]:
        if hashlib.sha256((source/name).read_bytes()).hexdigest()!=audit[key]:raise ValueError('复核记录与当前文件不同: '+name)
    for name,value in read(source/'manifest.json').items():
        if hashlib.sha256((source/name).read_bytes()).hexdigest()!=value:raise ValueError('来源摘要错误: '+name)
    out.mkdir(parents=True,exist_ok=True)
    for path in sorted(source.iterdir()):
        if path.name=='manifest.json' or not path.is_file():continue
        data=path.read_bytes()
        if path.suffix in ('.json','.md','.csv','.svg'):
            text=data.decode('utf-8').replace('\r\n','\n').replace('\r','')
            data=('\n'.join(line.rstrip() for line in text.split('\n'))).encode('utf-8')
        (out/path.name).write_bytes(data)
    (out/'README.md').write_text('''# 重估模型的相对退化传播敏感性

[两张图与解读](结果说明.md) · [完整条件摘要](传播响应摘要.csv) · [独立复核](独立标量复核.json) · [逐模型数组](逐模型逐基准数组.npz)

复用08的50轮实验参数；固定原30个留出基准和五档比例，完成405000条下游响应。此处没有额外拟合，也没有覆盖原正式模型。原完整训练控制回放8100条旧响应，最大差4.26e-14；独立标量重算全部405000条响应与270行摘要、50行示例，最大差0。129项相关测试通过。

预先指定的env_0/自由δ/B1置零→F1：50轮平均减少量范围44.345—59.132，中位数52.552；原模型为57.090 SE点。这个范围描述有限训练重估模型的相对退化情景敏感性，不是置信区间或真实因果效应。

405000条响应可用，目标预测越界0条；199146条（49.17%）目标直接供给超当轮训练范围。普通预测误差不能替代传播稳定性的检查；不能从相关散点单独归因于β。

本快照包含绘图PNG/SVG、固定协议、全部270行摘要、50行固定示例、完整NPZ与审计。NPZ的decrease/changed/forced/valid/outside/extrapolated依次为[轮次,模型任务,可达关系,比例,基准]；baseline为[轮次,模型任务,基准,节点]，轴标签在propagation_report.json的axes中。不可用值为NaN，不能按0解释。

复跑与核验命令见[方法说明](../refit-propagation.md)。重新独立核验需本地原06、07、08来源包；报告内Windows绝对路径用于本次来源定位，迁移时需重新绑定并保留原文件摘要。GitHub快照可直接阅读和检查manifest.json，但单靠此快照不能重新估计全部训练模型。

目前推进了第5项中的训练重估传播敏感性，仍未覆盖有效参数区间、其他求解器/种子、数值拓扑实验和真实依赖验证。
''',encoding='utf-8',newline='\n')
    manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file()}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    return len(manifest)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args();print(publish(args.source,args.destination))
