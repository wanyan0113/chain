"""Held-out binary-correctness validation; never upgrades domain_validity."""
from collections import Counter
import csv
import gzip
import math
from statistics import NormalDist

from task_flow.joint_candidates import metrics_for_chain

VERSION='tfdo-controlled-accuracy-1.0'
FIELDS=['sample_id','split','stratum','gold','fault_bits','shadow_outputs','serial_outputs']


def wilson(successes,n,alpha=.05):
    if n==0:return None
    z=NormalDist().inv_cdf(1-alpha/2);p=successes/n;den=1+z*z/n
    center=(p+z*z/(2*n))/den
    radius=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return [max(0,center-radius),min(1,center+radius)]


def prediction(rates):
    # Invoke the existing raw policy without changing it or entering normalization.
    artifacts=[dict(id=f'operator-{i}',activity_metrics={'decide':dict(duration_s=1,
        accuracy=p,cost=dict(value=0,unit='controlled/use'))}) for i,p in enumerate(rates)]
    chain=dict(plan=dict(steps=[dict(artifact=a['id'],activity='decide') for a in artifacts]))
    return metrics_for_chain(chain,dict(artifacts=artifacts,policies=dict(cost_unit='controlled/use')))['accuracy']


class Evidence:
    def __init__(self,length):
        self.length=length
        self.n=Counter();self.shadow={'calibration':Counter(),'evaluation':Counter()}
        self.serial=Counter();self.population={'calibration':Counter(),'evaluation':Counter()}
        self.recovery=[0]*length;self.recovery_eligible=[0]*length
    def add(self,row):
        length=self.length;split=row['split'];gold=row['gold']
        if split not in ('calibration','evaluation') or gold not in ('0','1'):
            raise ValueError('unknown split or missing/invalid ground truth')
        shadow=row['shadow_outputs'];serial=row['serial_outputs']
        if len(shadow)!=length or any(s not in '01x' for s in shadow):raise ValueError('invalid shadow labels')
        if len(row['fault_bits'])!=length or any(s not in '01' for s in row['fault_bits']):raise ValueError('invalid fault log')
        if (split=='evaluation' and (len(serial)!=length or any(s not in '01x' for s in serial))) or (split=='calibration' and serial):
            raise ValueError('missing serial outcomes or calibration/evaluation leakage')
        self.n[split]+=1;self.population[split][(row['stratum'],gold)]+=1
        mask=sum((s==gold)<<i for i,s in enumerate(shadow));self.shadow[split][mask]+=1
        if split=='evaluation':
            output_mask=sum((s==gold)<<i for i,s in enumerate(serial));self.serial[output_mask]+=1
            for i,s in enumerate(serial):
                previous=True if i==0 else serial[i-1]==gold
                if not previous:
                    self.recovery_eligible[i]+=1;self.recovery[i]+=s==gold
    def merge(self,other):
        if self.length!=other.length:raise ValueError('cannot pool different sequences')
        self.n.update(other.n);self.serial.update(other.serial)
        for split in self.shadow:
            self.shadow[split].update(other.shadow[split]);self.population[split].update(other.population[split])
        self.recovery=[a+b for a,b in zip(self.recovery,other.recovery)]
        self.recovery_eligible=[a+b for a,b in zip(self.recovery_eligible,other.recovery_eligible)]


def load(path,length):
    data=Evidence(length);seen=set()
    with gzip.open(path,'rt',encoding='utf-8',newline='') as handle:
        reader=csv.DictReader(handle)
        if reader.fieldnames!=FIELDS:raise ValueError('unexpected observation columns')
        for row in reader:
            if not row['sample_id'] or row['sample_id'] in seen:raise ValueError('duplicate/empty subject identity')
            seen.add(row['sample_id']);data.add(row)
    if not all(data.n[s]>0 for s in ('calibration','evaluation')):raise ValueError('both held-out splits required')
    return data


def summarize(data,case,policy):
    length=data.length;cal_n=data.n['calibration'];eval_n=data.n['evaluation']
    if not cal_n or not eval_n:raise ValueError('empty evidence')
    alpha=policy['alpha'];tol=policy['absolute_error_tolerance']
    # Bonferroni envelope: simultaneous for L marginal rates plus endpoint.
    component_alpha=alpha/(length+1)
    stages=[];rates=[];bounds=[]
    for i,stage in enumerate(case['stages']):
        count=sum(n for mask,n in data.shadow['calibration'].items() if mask&(1<<i))
        eval_count=sum(n for mask,n in data.shadow['evaluation'].items() if mask&(1<<i))
        rate=count/cal_n;rates.append(rate);bounds.append(wilson(count,cal_n,component_alpha))
        stages.append(dict(stage=stage,n_calibration=cal_n,correct=count,rate=rate,ci95=wilson(count,cal_n,alpha),
            simultaneous_component_ci=bounds[-1],n_evaluation_shadow=eval_n,evaluation_shadow_rate=eval_count/eval_n))
    endpoint=sum(n for mask,n in data.serial.items() if mask&(1<<(length-1)))
    observed=endpoint/eval_n;predicted=prediction(rates)
    interval=[math.prod(b[0] for b in bounds),math.prod(b[1] for b in bounds)]
    observed_interval=wilson(endpoint,eval_n,component_alpha)
    discrepancy_ci=[observed_interval[0]-interval[1],observed_interval[1]-interval[0]]
    comparable=case['population_calibration']==case['population_evaluation']
    coverage=all(data.population[s][(stratum,gold)]>=policy['minimum_stratum_class_samples']
                 for s in ('calibration','evaluation') for stratum in case['expected_strata'] for gold in ('0','1'))
    if not comparable:decision='blocked_population_mismatch'
    elif not coverage:decision='blocked_sample_coverage'
    elif discrepancy_ci[0]>=-tol and discrepancy_ci[1]<=tol:decision='equivalent_within_tolerance'
    elif discrepancy_ci[0]>tol:decision='systematic_underprediction'
    elif discrepancy_ci[1]<-tol:decision='systematic_overprediction'
    else:decision='inconclusive'
    correlations=[]
    for i in range(length):
        for j in range(i+1,length):
            counts=Counter()
            for mask,n in data.shadow['evaluation'].items():counts[(bool(mask&(1<<i)),bool(mask&(1<<j)))]+=n
            a,b,c,d=[counts[key] for key in ((True,True),(True,False),(False,True),(False,False))]
            denominator=math.sqrt((a+b)*(c+d)*(a+c)*(b+d))
            correlations.append(dict(stage_i=i,stage_j=j,n=eval_n,phi=(a*d-b*c)/denominator if denominator else None,
                reason=None if denominator else 'zero_variance',contingency=[a,b,c,d]))
    conditional=[];conditional_rates=[]
    for i in range(length):
        prefix=(1<<i)-1
        eligible=sum(n for mask,n in data.serial.items() if mask&prefix==prefix)
        correct=sum(n for mask,n in data.serial.items() if mask&prefix==prefix and mask&(1<<i))
        conditional.append(dict(stage=i,eligible=eligible,correct=correct,rate=correct/eligible if eligible else None,
            ci95=wilson(correct,eligible,alpha)))
        conditional_rates.append(correct/eligible if eligible else None)
    all_correct=data.serial[(1<<length)-1]/eval_n
    chain_rule=math.prod(conditional_rates) if all(p is not None for p in conditional_rates) else None
    if chain_rule is not None and not math.isclose(chain_rule,all_correct,abs_tol=1e-12):raise AssertionError('conditional chain-rule identity failed')
    return dict(case_id=case['id'],group=case['group'],length=length,n_calibration=cal_n,n_evaluation=eval_n,
        stages=stages,prediction=predicted,prediction_envelope=interval,observed=observed,
        observed_correct=endpoint,observed_ci95=wilson(endpoint,eval_n,alpha),
        signed_error_observed_minus_predicted=observed-predicted,absolute_error=abs(observed-predicted),
        discrepancy_ci95_simultaneous=discrepancy_ci,decision=decision,population_comparable=comparable,
        minimum_coverage_passed=coverage,population_counts={s:[dict(stratum=k[0],gold=k[1],n=n)
            for k,n in sorted(v.items())] for s,v in data.population.items()},
        shadow_error_correlations=correlations,recovery_counts=data.recovery,
        recovery_eligible=data.recovery_eligible,all_intermediate_outputs_correct_rate=all_correct,
        conditional_prefix_rates=conditional,conditional_chain_rule_product=chain_rule,
        conditional_product_is_in_sample_identity=True,domain_validity='pending')
