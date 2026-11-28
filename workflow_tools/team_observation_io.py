"""New, isolated six-table input. No legacy detection fallback or score updates."""
import csv
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from jsonschema import Draft202012Validator
from task_flow.contracts import read_json

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT/'docs/task-flow/team-observation-contract-v1'
VERSION = 'team-observation-contract-1.0'
FROZEN_CONTRACT_HASH = 'b524ffa2f76316cc51ed7aa43d148663d27c1d15839909413cb2f8c162cc4a26'
TABLES = ('samples','executions','stage_attempts','member_observations','group_outputs','task_endpoints')


class InputError(ValueError):
    def __init__(self, code, message, path=None):
        self.issue = dict(code=code, message=message, path=path or [])
        super().__init__(message)


def local(parent, name):
    if type(name) is not str or not name:
        raise InputError('INVALID_FILE_REFERENCE', 'expected a local relative file')
    p=(parent/name).resolve()
    if Path(name).is_absolute() or not p.is_relative_to(parent.resolve()):
        raise InputError('INVALID_FILE_REFERENCE', 'packet references must remain inside their containing directory')
    return p


def decode_cell(value, schema, path):
    if value is None:
        raise InputError('CSV_COLUMN_COUNT', 'missing CSV column', path)
    if value=='': return None
    if unicodedata.normalize('NFC',value)!=value:raise InputError('CSV_NON_NFC','CSV text must use NFC normalization',path)
    types=schema.get('type',[])
    if isinstance(types,str): types=[types]
    enum=schema.get('enum',[])
    if 'array' in types:
        def invalid_constant(v):raise ValueError('nonfinite array value')
        try: return json.loads(value,parse_constant=invalid_constant)
        except ValueError as exc: raise InputError('CSV_ARRAY', 'array cells require JSON syntax',path) from exc
    if 'boolean' in types or any(type(v) is bool for v in enum):
        if value not in ('true','false'): raise InputError('CSV_BOOLEAN','use true or false',path)
        return value=='true'
    if 'integer' in types:
        if not re.fullmatch(r'[1-9][0-9]*',value): raise InputError('CSV_INTEGER','expected a positive base-10 integer',path)
        return int(value)
    return value  # Labels such as "0" stay strings, never become false/missing.


def load_packet(dataset_path, protocol_path, lock_path, sources_path):
    manifest=read_json(CONTRACT/'freeze-manifest.json',allow_finite_floats=True)
    fingerprint=hashlib.sha256(json.dumps(manifest['files'],sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()
    if fingerprint!=FROZEN_CONTRACT_HASH or manifest['content_hash']!=fingerprint:raise InputError('CONTRACT_CHANGED','frozen contract fingerprint changed')
    for name,digest in manifest['files'].items():
        if hashlib.sha256(local(CONTRACT,name).read_bytes()).hexdigest()!=digest:raise InputError('CONTRACT_CHANGED','frozen contract bytes changed: '+name)
    files=[]
    def read(path,role):
        raw=path.read_bytes()
        files.append(dict(role=role,path=str(path.resolve()),sha256=hashlib.sha256(raw).hexdigest(),raw=raw))
        return read_json(path,allow_finite_floats=True)
    schema=read_json(CONTRACT/'schema.json',allow_finite_floats=True)
    first=read(dataset_path,'dataset')
    if type(first) is not dict:raise InputError('JSON_OBJECT','expected a dataset object')
    if first.get('contract_version')!=VERSION:
        raise InputError('UNSUPPORTED_VERSION','new team input requires '+VERSION,['contract_version'])
    if 'table_files' in first:
        expected={'contract_version','study_id','protocol_revision_hash','data_origin','table_files'}
        if set(first)!=expected or set(first['table_files'])!=set(TABLES):
            raise InputError('CSV_METADATA','all six tables and exact packet metadata are required')
        data={k:v for k,v in first.items() if k!='table_files'}
        for table in TABLES:
            p=local(dataset_path.parent,first['table_files'][table])
            raw=p.read_bytes();files.append(dict(role='table:'+table,path=str(p),sha256=hashlib.sha256(raw).hexdigest(),raw=raw))
            with p.open(encoding='utf-8-sig',newline='') as f:
                reader=csv.DictReader(f)
                props=schema['$defs'][table]['properties']
                if reader.fieldnames!=list(props):
                    raise InputError('CSV_HEADER','header/order differs from frozen '+table,[table])
                rows=[]
                for i,row in enumerate(reader):
                    if None in row: raise InputError('CSV_COLUMN_COUNT','extra CSV columns',[table,i])
                    rows.append({k:decode_cell(row[k],props[k],[table,i,k]) for k in props})
                    if len(rows)>100000: raise InputError('INPUT_LIMIT','table exceeds 100000 rows',[table])
                data[table]=rows
    else:
        data=first
    protocol=read(protocol_path,'protocol');lock=read(lock_path,'lock');manifest=read(sources_path,'sources_manifest')
    if set(manifest)!={'version','sources'} or manifest['version']!='team-observation-sources-1.0':
        raise InputError('SOURCE_MANIFEST','unsupported source manifest')
    sources={}
    for entry in manifest['sources']:
        if set(entry)!={'source_report_id','report_file'} or entry['source_report_id'] in sources:
            raise InputError('SOURCE_MANIFEST','duplicate or malformed source ID')
        sources[entry['source_report_id']]=read(local(sources_path.parent,entry['report_file']),'source:'+entry['source_report_id'])
    issues=[]
    for value,schema_name,label in [(data,'schema.json','dataset'),(protocol,'protocol.schema.json','protocol')]:
        s=schema if schema_name=='schema.json' else read_json(CONTRACT/schema_name,allow_finite_floats=True)
        for error in Draft202012Validator(s).iter_errors(value):
            issues.append(dict(code='SCHEMA',message=error.message,path=[label,*error.absolute_path]))
            if len(issues)>=500: return data,protocol,lock,sources,files,issues+[dict(code='ERROR_LIMIT',message='additional errors omitted',path=[])]
    return data,protocol,lock,sources,files,issues
