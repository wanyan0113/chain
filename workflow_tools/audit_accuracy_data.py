"""Independent label replay and analytic references for the declared controls."""
import csv
import gzip
import math


def reference_endpoint(case):
    p=[s['clean_pass_probability'] for s in case['stages']];mode=case['mechanism'];length=len(p)
    if mode=='absorbing':return math.prod(p)
    if mode=='shared_difficulty':
        h=case['population_evaluation']['hard_probability']
        return (1-h)*case['easy_pass']**length+h*case['hard_pass']**length
    if mode=='repeated':return p[0]
    if mode=='exclusive':return .3
    if mode=='repair':return p[0]+(1-p[0])*case['repair_probability']
    if mode=='flip':return (1+math.prod(2*x-1 for x in p))/2
    raise ValueError('unknown mechanism')


def replay(path,case):
    n=0;endpoint=0
    with gzip.open(path,'rt',encoding='utf-8',newline='') as handle:
        for row in csv.DictReader(handle):
            n+=1;gold=row['gold'];value=gold;shadow=[];serial=[]
            for i,fault in enumerate(row['fault_bits']):
                if case['mechanism']=='repair' and i==1:
                    shadow.append(gold)
                    if row['split']=='evaluation':
                        if value==gold or fault=='0':value=gold
                        serial.append(value)
                elif case['mechanism'] in ('flip','repair'):
                    shadow.append(str(1-int(gold)) if fault=='1' else gold)
                    if row['split']=='evaluation':
                        if fault=='1':value=str(1-int(value))
                        serial.append(value)
                else:
                    shadow.append('x' if fault=='1' else gold)
                    if row['split']=='evaluation':
                        if fault=='1' or value=='x':value='x'
                        serial.append(value)
            if ''.join(shadow)!=row['shadow_outputs'] or ''.join(serial)!=row['serial_outputs']:
                raise AssertionError('stored labels disagree with independent fault replay')
            endpoint+=row['split']=='evaluation' and value==gold
    return dict(rows_checked=n,endpoint_correct=endpoint,reference_probability=reference_endpoint(case),
        scope='replay declared synthetic operators, not independent real-domain labels')
