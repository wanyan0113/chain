"""Persist the complete FDNA regression result for reporting."""
import io
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import time
import unittest

root=Path(__file__).resolve().parents[1]
out=root/'generated/汇报用/23_统一入口与验收'
out.mkdir(parents=True,exist_ok=True)
suite=unittest.defaultTestLoader.discover(str(root/'tests'),pattern='test_fdna*.py')
stream=io.StringIO();start=time.monotonic()
result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
text=stream.getvalue()
(out/'完整回归测试.txt').write_text(text,encoding='utf-8')
summary=dict(tests_run=result.testsRun,passed=result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),
    failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),
    skipped_cases=[dict(test=str(t),reason=r) for t,r in result.skipped],seconds=time.monotonic()-start,successful=result.wasSuccessful())
(out/'测试摘要.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
raise SystemExit(0 if result.wasSuccessful() else 1)
