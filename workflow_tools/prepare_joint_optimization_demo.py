"""Create the explicit-policy joint fixture; preserve existing quick-mode demos."""
from copy import deepcopy
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'task_flow/examples/design_bridge'
TARGET=ROOT/'task_flow/examples/joint_optimization'


def main():
    TARGET.mkdir(parents=True,exist_ok=True)
    for path in SOURCE.glob('*.json'):
        data=json.loads(path.read_text(encoding='utf-8'))
        if path.name=='scene.json':
            data['scope']='跨流程联合优化合成验收：主解析可用但耗时高，备用解析更快、更准且成本更低。'
            primary=next(a for a in data['artifacts'] if a['id']=='R-Analysis')
            primary['available']=True
            primary['activity_metrics']['decide']=dict(duration_s=8,accuracy=.8,cost={'value':8,'unit':'demo-credit/use'})
            backup=next(a for a in data['artifacts'] if a['id']=='R-Analysis-Backup')
            backup['activity_metrics']['decide']['accuracy']=.99
        elif path.name=='mapping.json':
            data['evaluation']=dict(mode='sequential_product_v2',source_refs=['TFDO-DESIGN-1.0 explicit raw metric policy'])
        (TARGET/path.name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Prepared task_flow/examples/joint_optimization; no prewritten flow sequence.')


if __name__=='__main__':
    main()
