"""Read-only browser access to the versioned evidence catalog and verified artifacts."""
from datetime import datetime,timezone
import hashlib
from html import escape
import json
from pathlib import Path
import re
from urllib.parse import quote,unquote,urlsplit

from fdna.evidence_review import read,safe_path,validate_catalog,check_package,check_document,STATES


PREFIX='/fdna-results'
REVIEW=dict(id='review_record',title='最近发布的完整复核',directory='docs/evidence-review-report',
    entrypoint='本次完整复核.md',required_files=['本次完整复核.json','本次完整复核.md','目录版本.json'])
MIMES={'.png':'image/png','.svg':'image/svg+xml','.pdf':'application/pdf','.csv':'text/csv; charset=utf-8',
       '.json':'application/json; charset=utf-8','.md':'text/plain; charset=utf-8'}


class EvidenceError(ValueError):
    def __init__(self,status,message):super().__init__(message);self.status=status


def catalog(repository):return validate_catalog(read(Path(repository)/'data/fdna/evidence_catalog.json'))


def package(repository,ident):
    entries=catalog(repository)['packages']+[REVIEW]
    try:return next(p for p in entries if p['id']==ident)
    except StopIteration:raise EvidenceError(404,'未知成果编号') from None


def asset_url(ident,name,reading=False):
    return PREFIX+('/read/' if reading else '/files/')+quote(ident,safe='')+'/'+quote(name,safe='/')


def artifact(repository,ident,name):
    entry=package(repository,ident);directory=safe_path(repository,entry['directory'])
    path=safe_path(directory,name)
    if path.suffix.lower() not in MIMES:raise EvidenceError(404,'该文件类型不提供网页访问')
    manifest=read(directory/'manifest.json')
    if name not in manifest or not path.is_file():raise EvidenceError(404,'文件未登记或不存在')
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=manifest[name]:raise EvidenceError(409,'文件内容已变化，请重新复核来源')
    return path,data,MIMES[path.suffix.lower()]


def document(repository,ident):
    try:entry=next(p for p in catalog(repository)['documents'] if p['id']==ident)
    except StopIteration:raise EvidenceError(404,'未知说明编号') from None
    if check_document(repository,entry)['status']!='passed':raise EvidenceError(409,'说明文档版本检查未通过')
    path=safe_path(repository,entry['path']);return path,path.read_bytes(),entry['title']


def status_data(repository):
    config=catalog(repository);packages=[]
    for entry in config['packages']:
        checked=check_package(repository,entry)
        figures=[]
        if checked['status']=='passed':
            directory=safe_path(repository,entry['directory'])
            figures=[n for n in read(directory/'manifest.json') if n.lower().endswith('.png')]
        packages.append(dict(id=entry['id'],title=entry['title'],status=checked['status'],files_checked=checked['files_checked'],
            manifest_sha256=checked['manifest_sha256'],errors=checked['errors'],
            reading_url=asset_url(entry['id'],entry['entrypoint'],True),
            thumbnail_url=asset_url(entry['id'],figures[0]) if figures else None,figure_count=len(figures)))
    documents=[dict(id=p['id'],title=p['title'],status=check_document(repository,p)['status'],url=PREFIX+'/documents/'+quote(p['id'],safe='')) for p in config['documents']]
    historical=dict(status='unavailable',reason='未找到通过字节检查的已发布完整复核记录')
    record_check=check_package(repository,REVIEW)
    if record_check['status']=='passed':
        directory=safe_path(repository,REVIEW['directory']);record=read(directory/'本次完整复核.json')
        recorded_catalog=read(directory/'目录版本.json')
        recorded={p['id']:p for p in record.get('packages',[])}
        same=(recorded_catalog==config and all(p['status']=='passed' for p in documents)
            and all(p['id'] in recorded and p['manifest_sha256']==recorded[p['id']]['manifest_sha256'] and p['status']=='passed' for p in packages))
        historical=dict(status='available',checked_at_utc=record['checked_at_utc'],recorded_status=record['status'],
            matches_current_materials=same,full_verifiers_invoked=record.get('full_verifiers_invoked',0),
            numerical_rechecks_completed=record.get('numerical_rechecks_completed',False),
            tasks=[dict(title=r['title'],status=r['status'],verifier_invoked=r.get('verifier_invoked',False)) for r in record.get('full_checks',[])],
            reading_url=asset_url(REVIEW['id'],REVIEW['entrypoint'],True))
    return dict(status='passed' if all(p['status']=='passed' for p in packages+documents) else 'failed',
        checked_at_utc=datetime.now(timezone.utc).isoformat(),check_kind='published_materials_only',
        numerical_rechecks_executed=False,performance_remeasured=False,packages=packages,documents=documents,historical_full=historical,
        priorities=[dict(p,state_label=STATES[p['state']]) for p in config['priorities']],
        declared_progress_counts={s:sum(p['state']==s for p in config['priorities']) for s in STATES},
        package_files_checked=sum(p['files_checked'] for p in packages))


def target_url(repository,source,target,image=False):
    """Resolve report links only to catalog entries and manifest-listed local artifacts."""
    parsed=urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return target if not image and parsed.scheme in ('http','https') and parsed.netloc else None
    if parsed.query:return None
    candidate=(source.parent/unquote(parsed.path)).resolve()
    root=Path(repository).resolve()
    if not candidate.is_relative_to(root):return None
    config=catalog(repository)
    for entry in config['packages']+[REVIEW]:
        directory=safe_path(repository,entry['directory'])
        if candidate.is_relative_to(directory):
            name=candidate.relative_to(directory).as_posix()
            manifest=read(directory/'manifest.json')
            if name not in manifest or candidate.suffix.lower() not in MIMES:return None
            if image and candidate.suffix.lower() not in ('.png','.svg'):return None
            return asset_url(entry['id'],name,candidate.suffix.lower()=='.md')
    if not image:
        for entry in config['documents']:
            if candidate==safe_path(repository,entry['path']):return PREFIX+'/documents/'+quote(entry['id'],safe='')
    return None


def inline(text,repository,source):
    # Escape all source HTML. Only selected inline Markdown constructs create markup.
    output=[];position=0
    pattern=r'(!?\[([^\]]*)\]\(([^)]+)\)|`([^`]+)`|\*\*([^*]+)\*\*)'
    for match in re.finditer(pattern,text):
        output.append(escape(text[position:match.start()]));token=match.group()
        if token.startswith('![') or token.startswith('['):
            image=token.startswith('![');label=match.group(2);url=target_url(repository,source,match.group(3),image)
            if url:
                output.append('<img loading="lazy" src="'+escape(url,quote=True)+'" alt="'+escape(label,quote=True)+'">' if image else
                              '<a href="'+escape(url,quote=True)+'" rel="noreferrer">'+escape(label)+'</a>')
            else:output.append(escape(label)+'（链接不在网页阅读范围）')
        elif token.startswith('`'):output.append('<code>'+escape(match.group(4))+'</code>')
        else:output.append('<strong>'+escape(match.group(5))+'</strong>')
        position=match.end()
    output.append(escape(text[position:]));return ''.join(output)


def markdown_body(text,repository,source):
    """Small escaped report reader, not a general HTML/Markdown execution engine."""
    result=[];paragraph=[];table=[];code=None
    def flush():
        if paragraph:result.append('<p>'+inline(' '.join(paragraph),repository,source)+'</p>');paragraph.clear()
        if table:
            result.append('<div class="table-scroll"><table>')
            for i,row in enumerate(table):
                if re.fullmatch(r'[\s|:\-]+',row):continue
                tag='th' if i==0 else 'td'
                result.append('<tr>'+''.join('<'+tag+'>'+inline(c.strip(),repository,source)+'</'+tag+'>' for c in row.strip('|').split('|'))+'</tr>')
            result.append('</table></div>');table.clear()
    for line in text.splitlines():
        if line.startswith('```'):
            flush()
            if code is None:code=[]
            else:result.append('<pre><code>'+escape('\n'.join(code))+'</code></pre>');code=None
            continue
        if code is not None:code.append(line);continue
        if line.startswith('|'):
            if paragraph:flush()
            table.append(line);continue
        if table:flush()
        heading=re.match(r'^(#{1,6})\s+(.*)',line)
        if heading:
            flush();level=min(len(heading[1])+1,6);result.append(f'<h{level}>'+inline(heading[2],repository,source)+f'</h{level}>')
        elif not line.strip():flush()
        elif line.startswith('- '):flush();result.append('<p class="bullet">• '+inline(line[2:],repository,source)+'</p>')
        elif line.startswith('!['):flush();result.append('<figure>'+inline(line,repository,source)+'</figure>')
        else:paragraph.append(line)
    flush()
    if code is not None:result.append('<pre><code>'+escape('\n'.join(code))+'</code></pre>')
    return ''.join(result)


def reading_page(repository,path):
    if path.startswith(PREFIX+'/documents/'):
        source,data,title=document(repository,unquote(path[len(PREFIX+'/documents/'):]))
    else:
        rest=path[len(PREFIX+'/read/'):].split('/',1)
        if len(rest)!=2:raise EvidenceError(404,'缺少已登记的图文文件')
        ident,name=unquote(rest[0]),unquote(rest[1]);entry=package(repository,ident)
        if check_package(repository,entry)['status']!='passed':raise EvidenceError(409,'成果包材料检查未通过，请先处理文件差异')
        source,data,_=artifact(repository,ident,name);title=entry['title']
        if source.suffix.lower()!='.md':raise EvidenceError(404,'图文阅读只支持已登记Markdown')
    body=markdown_body(data.decode('utf-8-sig'),repository,source)
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+escape(title)+'</title><style>'+READER_STYLE+'</style><main><nav><a href="'+PREFIX+'">← 返回优化成果</a></nav><p class="notice">原成果的图文阅读；此页面不重新执行数值复核。请保留报告中的适用范围和限制。</p>'+body+'</main></html>'


READER_STYLE='''body{margin:0;background:#f4f6f8;color:#203345;font:16px/1.85 "Microsoft YaHei",sans-serif}main{max-width:1100px;margin:24px auto;padding:24px 32px;background:white;border-radius:12px}a{color:#146f75}h2{font-size:26px}h3{font-size:21px}figure{margin:24px 0}img{display:block;max-width:100%;height:auto;border:1px solid #e0e6eb}pre{overflow:auto;padding:16px;background:#edf2f5;white-space:pre-wrap;word-break:break-word}code{font-size:.9em}table{border-collapse:collapse;min-width:550px;width:100%}td,th{padding:10px;border:1px solid #dce4ea;text-align:left}.table-scroll{overflow:auto}.notice{padding:12px 16px;background:#eef6f5;border-left:4px solid #267d79;color:#38565b}.bullet{margin:6px 0}@media(max-width:700px){main{margin:0;padding:18px}h2{font-size:22px}}'''
