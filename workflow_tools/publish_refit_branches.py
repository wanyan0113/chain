"""Publish only a verified branch-audit snapshot with exact-byte manifests."""
import argparse
import hashlib
import json
from pathlib import Path


def publish(source,destination):
    source=Path(source);out=Path(destination)
    if out.exists() and any(out.iterdir()):raise ValueError('拒绝覆盖非空发布目录')
    read=lambda p:json.loads(p.read_text(encoding='utf-8'))
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    audit=read(source/'独立标量复核.json')
    if audit['status']!='passed' or audit['scalar_responses_checked']!=405000:raise ValueError('须通过完整独立核验')
    if sha(source/'manifest.json')!=audit['source_manifest_sha256'] or sha(source/'branch_report.json')!=audit['report_sha256']:
        raise ValueError('复核记录与当前文件不一致')
    manifest=read(source/'manifest.json')
    for name,value in manifest.items():
        if Path(name).name!=name or sha(source/name)!=value:raise ValueError('来源文件或摘要错误: '+name)
    out.mkdir(parents=True,exist_ok=True)
    for name in list(manifest)+['独立标量复核.json']:
        data=(source/name).read_bytes()
        if Path(name).suffix in ('.json','.csv','.md','.svg'):
            content=data.decode('utf-8').replace('\r\n','\n').replace('\r','')
            data=('\n'.join(line.rstrip() for line in content.split('\n'))).encode('utf-8')
        (out/name).write_bytes(data)
    (out/'README.md').write_text('''# 训练不活跃参数与退化时的分支启用

[两张图与解释](结果说明.md) · [条件摘要](传播分支条件摘要.csv) · [独立复核](独立标量复核.json) · [方法与复跑](../refit-branches.md)

复用08的50轮重估模型与09全部405000条传播响应，没有新拟合或修改原模型。本步扩展已有冻结模型分支解释到训练重估模型，直接核对“训练时不活跃的β是否在递推/强退化时启用”。

1800次拟合中，1588次的β在全部重采样训练输入上严格不活跃；其中146次拟合在本批递推/干预情景中至少出现一次唯一COD限制。符合“训练β不活跃、响应唯一COD”的9009条记录中，8789条从同模型递推基准SOD转为干预COD，220条基准已是COD，不能全部归因于退化新触发。9009条均存在目标直接供给外推。

独立标量核验1800行训练拟合、162000条训练输入、405000条响应、13500行模型条件和270行条件摘要，数值差0；147项相关测试通过。近并列66条保留双候选，不指定唯一导数。

图1统计训练β严格不活跃且基准SOD→干预COD的比例，分母是该组全部有效可达响应。图2展示固定示例的全部50轮模型，不能把图2的全部COD比例当成图1启用率。两图均不是置信区间或参数物理正确性的证明。

本快照保留PNG/SVG、协议、完整三张CSV、分支代码NPZ与报告。response_branch轴为[轮次,模型任务,可达关系,比例,基准]，baseline_branch为[轮次,模型任务,可达关系,基准]；轴标签见branch_report.json。代码0=不可用，1=唯一SOD，2=唯一COD，3=近并列，不把不可用按SOD或零响应解释。

源码复跑和独立标量验证需要本地06/08/09来源包；报告中的绝对路径为本次来源定位。manifest.json用于检查此发布快照的精确字节，与本地来源包manifest分开。旧包未被覆盖，优化优先级第5项仍只部分完成；数值拓扑试验及真实依赖验证仍待开展。
''',encoding='utf-8',newline='\n')
    inventory={p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file()}
    (out/'manifest.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    return len(inventory)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args();print(publish(args.source,args.destination))
