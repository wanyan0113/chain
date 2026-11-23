"""Audit timing arithmetic, source fingerprints and all full-replay array bytes."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import statistics

import numpy as np


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify(directory):
    out=Path(directory);report=read(out/'benchmark_report.json');protocol=read(out/'固定方案.json')
    for name,value in read(out/'manifest.json').items():assert sha(out/name)==value,name
    assert report['protocol']==protocol and protocol['trials']==5
    source=Path(report['source_directory']);assert sha(source/'manifest.json')==report['source_manifest_sha256']
    for name,value in read(source/'manifest.json').items():assert sha(source/name)==value,name
    timings=report['timings'];assert len(timings)==10
    for trial in range(1,6):
        rows=[r for r in timings if r['trial']==trial]
        assert [r['position'] for r in rows]==[1,2]
        assert [r['implementation'] for r in rows]==(['reference','prepared'] if trial%2 else ['prepared','reference'])
    for name in ('reference','prepared'):
        values=[r['seconds'] for r in timings if r['implementation']==name]
        assert len(values)==5 and all(np.isfinite(v) and v>0 for v in values)
        summary=report['timing_summary'][name]
        assert summary==dict(minimum_seconds=min(values),median_seconds=statistics.median(values),maximum_seconds=max(values),trials=5)
    assert report['median_speedup']==report['timing_summary']['reference']['median_seconds']/report['timing_summary']['prepared']['median_seconds']
    assert report['call_counts']=={'reference':{'topology_validations':277,'dag_evaluations':276},'prepared':{'topology_validations':1,'dag_evaluations':186}}
    with (out/'逐轮计时.csv').open(encoding='utf-8-sig',newline='') as stream:stored=list(csv.DictReader(stream))
    assert len(stored)==len(timings)
    for a,b in zip(stored,timings):
        for key,value in b.items():assert a[key]==str(value)
    numeric=0;masks=0
    with np.load(source/'逐模型逐基准数组.npz') as a,np.load(out/'全量复跑/逐模型逐基准数组.npz') as b:
        assert set(a.files)==set(b.files)
        for name in a.files:
            left=a[name];right=b[name]
            assert left.dtype==right.dtype and left.shape==right.shape
            assert left.tobytes(order='C')==right.tobytes(order='C'),name
            assert np.array_equal(left,right,equal_nan=True),name
            if left.dtype.kind=='b':masks+=left.size
            else:numeric+=left.size
    assert numeric==1296000 and masks==1215000
    assert report['all_50_models_comparison']==dict(numeric_values_compared=numeric,mask_values_compared=masks,maximum_difference=0.,bitwise_equal_including_missing=True)
    from workflow_tools.verify_refit_propagation import verify as independent_verify
    scalar=independent_verify(out/'全量复跑')
    assert scalar==report['independent_verification']
    # Code hashes describe the optimized implementation used for these measured runs.
    root=Path(__file__).resolve().parents[1]
    assert report['source_code_hash_policy']=='normalize_CRLF_to_LF'
    code_sha=lambda p:hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
    for name,value in report['optimized_files_sha256'].items():assert code_sha(root/name)==value,name
    assert code_sha(root/'workflow_tools/reference_refit_replay.py')==report['reference_file_sha256']
    return dict(status='passed',timing_rows_checked=10,bitwise_numeric_values_compared=numeric,bitwise_mask_values_compared=masks,
        scalar_responses_checked=scalar['responses_checked'],maximum_difference=0.,median_speedup=report['median_speedup'],
        source_manifest_sha256=sha(out/'manifest.json'),report_sha256=sha(out/'benchmark_report.json'),
        limitation='verifies_recorded_timing_arithmetic_not_independent_remeasurement')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--report-dir',type=Path,required=True);parser.add_argument('--output',type=Path)
    args=parser.parse_args();result=verify(args.report_dir);text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.output:
        if args.output.exists():raise ValueError('独立核验记录已存在')
        args.output.write_text(text,encoding='utf-8',newline='\n')
    print(text)
