"""Publish a small verified performance snapshot without duplicating old arrays."""
import argparse
import hashlib
import json
from pathlib import Path


def publish(source,destination):
    source=Path(source);out=Path(destination)
    if out.exists() and any(out.iterdir()):raise ValueError('拒绝覆盖非空发布目录')
    read=lambda p:json.loads(p.read_text(encoding='utf-8'))
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    audit=read(source/'独立核验.json')
    if audit['status']!='passed' or audit['bitwise_numeric_values_compared']!=1296000 or audit['bitwise_mask_values_compared']!=1215000:
        raise ValueError('需要通过全部数组逐字节与独立标量核验')
    if sha(source/'manifest.json')!=audit['source_manifest_sha256'] or sha(source/'benchmark_report.json')!=audit['report_sha256']:
        raise ValueError('独立核验记录与当前文件不同')
    for name,value in read(source/'manifest.json').items():
        path=(source/name).resolve()
        if not path.is_relative_to(source.resolve()) or sha(path)!=value:raise ValueError('来源文件或摘要错误: '+name)
    out.mkdir(parents=True,exist_ok=True)
    names=['benchmark_report.json','固定方案.json','逐轮计时.csv','结果说明.md','独立核验.json','01_传播计算性能对照.png','01_传播计算性能对照.svg']
    for name in names+['全量复跑/独立标量复核.json']:
        data=(source/name).read_bytes()
        if Path(name).suffix in ('.md','.csv','.json','.svg'):
            value=data.decode('utf-8').replace('\r\n','\n').replace('\r','')
            data=('\n'.join(line.rstrip() for line in value.split('\n'))).encode('utf-8')
        destination_name='全量响应独立复核.json' if '/' in name else name
        (out/destination_name).write_bytes(data)
    report=read(source/'benchmark_report.json');speedup=report['median_speedup']
    (out/'README.md').write_text(f'''# 传播计算效率优化

[性能图与说明](结果说明.md) · [逐轮原始计时](逐轮计时.csv) · [核验记录](独立核验.json) · [方法与复跑](../prepared-prediction.md)

重复传播时，每个拓扑与参数集只准备一次，同一干预的多个目标共享一次DAG计算。保留完整校验，在显式准备时冻结拓扑与参数，每次调用仍检查输入；没有全局缓存，不保留观测或跨模型复用干预状态。

本机固定6模型×30基准×9关系×五档比例，预热后5轮交替计时：旧实现中位数{report['timing_summary']['reference']['median_seconds']:.6f}秒，优化后{report['timing_summary']['prepared']['median_seconds']:.6f}秒，比值{speedup:.2f}倍。计时包含每轮准备与完整内存回放，排除读文件、拟合、独立核验和绘图；不是端到端FDNA求解加速保证。

实际拓扑校验277→1次，完整DAG计算276→186次。参考为19d31b8的三个原函数原样副本，仅用于对照；当前正式传播路径使用新准备对象。旧源文件与参考副本指纹均已记录。

全50轮重估模型回放后，1296000个数值与1215000个布尔掩码和原09逐字节一致，含NaN位置、符号零；405000条响应及270行摘要再次通过独立标量复核。163项相关测试通过。预测精度和研究结论保持原样，这轮提升的是运行效率。

完整复跑包位于本地12目录的全量复跑子目录；数组与[原09公开数组](../refit-propagation-report/逐模型逐基准数组.npz)一致，无需在Git重复保存。此快照提供图、逐轮计时、环境、固定协议、代码指纹、审计和[全量响应标量复核](全量响应独立复核.json)。本地全量复核需原06/07/08/09来源包；计时算术核验不等于另一台机器的独立计时。

manifest.json记录发布快照的精确字节，与本地完整来源manifest分开。代码指纹先规范化CRLF为LF，避免跨平台行尾转换被误判为实现变化。
''',encoding='utf-8',newline='\n')
    inventory={p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file()}
    (out/'manifest.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    return len(inventory)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args();print(publish(args.source,args.destination))
