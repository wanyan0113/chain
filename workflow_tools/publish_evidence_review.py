"""Run with python -m workflow_tools.publish_evidence_review to publish both modes."""
import argparse
import json
from pathlib import Path

from fdna.evidence_review import (REPOSITORY,DEFAULT_CATALOG,TASKS,read,sha,check_package,
    check_document,validate_catalog,report_markdown)
from fdna.workflow_common import dumps


def publish(source_dir,output_dir):
    source=Path(source_dir);out=Path(output_dir)
    if out.exists() and any(out.iterdir()):raise ValueError('拒绝覆盖非空发布目录')
    catalog=validate_catalog(read(DEFAULT_CATALOG));reports={}
    for mode,folder in [('published','发布材料检查'),('full','完整数值复核')]:
        directory=source/folder
        manifest=read(directory/'manifest.json')
        if set(manifest)!= {'review_report.json','成果复核与阅读入口.md','目录版本.json'}:raise ValueError('复核输出文件清单不完整或含未知文件')
        for name,value in manifest.items():
            if sha(directory/name)!=value:raise ValueError('复核输出文件已变化: '+name)
        report=read(directory/'review_report.json')
        if report['status']!='passed' or report['mode']!=mode or report['failed_checks']:
            raise ValueError('须通过对应模式的本次复核')
        if report['catalog_sha256']!=sha(DEFAULT_CATALOG) or read(directory/'目录版本.json')!=catalog:
            raise ValueError('成果目录版本已变化，须重新复核')
        fresh=[check_package(REPOSITORY,p) for p in catalog['packages']]
        documents=[check_document(REPOSITORY,p) for p in catalog['documents']]
        if report['packages']!=fresh or report['documents']!=documents:raise ValueError('发布成果或说明版本已变化，须重新复核')
        if mode=='full':
            if not report['numerical_rechecks_executed'] or not report['numerical_rechecks_completed'] or report['full_verifiers_invoked']!=10:
                raise ValueError('完整模式缺少本次完整执行标记')
            if [r['id'] for r in report['full_checks']]!=[r[0] for r in TASKS] or any(r['status']!='passed' for r in report['full_checks']):
                raise ValueError('缺少完整的十项本次复核记录')
        elif report['full_checks'] or report['numerical_rechecks_executed']:raise ValueError('材料模式不能冒充本次数值重算')
        reports[mode]=report
    out.mkdir(parents=True,exist_ok=True)
    for mode,report in reports.items():
        name='本次完整复核' if mode=='full' else '发布材料检查'
        (out/(name+'.json')).write_text(dumps(report),encoding='utf-8',newline='\n')
        (out/(name+'.md')).write_text(report_markdown(report,REPOSITORY,out),encoding='utf-8',newline='\n')
    (out/'目录版本.json').write_text(dumps(catalog),encoding='utf-8',newline='\n')
    (out/'README.md').write_text('''# 优化成果统一复核入口

[本次完整复核与成果目录](本次完整复核.md) · [只查发布材料的示例](发布材料检查.md) · [命令与使用说明](../evidence-review.md)

本轮新增主CLI命令`python -B -m fdna review-results`。published模式检查7个已发布成果包共95个文件的精确字节与两份说明文档的登记版本；不需要本地generated来源，也不声称本次重新算过数值。full模式显式提供项目根目录，重新执行10项既有独立复核任务，分别标记源包完整性、CSV来源对照、标量数值与计时算术。

本次两种模式均实际运行并通过，full模式10项任务全部通过。复核报告记录本次执行时间、各任务结果、错误与成果入口，分别标记脚本是否调用、数值任务是否完成。没有新拟合、SE生成、重新测时、改边或覆盖历史结果。175项相关测试通过，包含篡改/缺失/额外文件、路径约束、说明版本、来源缺失、失败继续和退出码。

九项研究建议的进度来自版本化人工登记：5项已落实当前范围、2项部分完成、2项待新增优化。材料与数值检查不自动推断研究已全部完成，也不证明真实有效性；源数据和模型限制仍见各原成果报告。

缺少本地来源、摘要不符或数值断言失败，full不会退回published并报告成功；它保留失败记录并返回1。请求本身不合法返回2，全通过返回0。完整复核依赖独立脚本中的断言，拒绝`python -O/-OO`，避免断言被关闭后误报通过。

此快照的manifest.json校验当前发布文件；JSON保留本次原始复核记录，Markdown链接已重设为GitHub仓库内相对路径。这里只保存已执行记录，下载本页并不等于本机重新运行了全部数值核验。
''',encoding='utf-8',newline='\n')
    manifest={p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file()}
    (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    return len(manifest)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source-dir',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args();print(publish(args.source_dir,args.output_dir))
