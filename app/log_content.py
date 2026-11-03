"""严格日志解码、物理行定位与完整行分块，不推断报文含义。"""
from __future__ import annotations

import codecs
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

ENCODINGS = {'auto','utf-8','utf-16','gb18030'}
ACTUAL_ENCODINGS = {'utf-8','utf-8-sig','utf-16-le','utf-16-be','gb18030'}
TIMESTAMP = re.compile(r'^\s*\[?(?:\d{4}[-/]\d{2}[-/]\d{2}[ T])?\d{2}:\d{2}:\d{2}(?:[.,]\d+)?')


class LogError(ValueError):
    pass


def parse(raw, encoding='auto', *, max_chars=4_000_000, max_lines=50_000):
    if not isinstance(encoding,str) or encoding not in ENCODINGS | ACTUAL_ENCODINGS:
        raise LogError('不支持的日志编码；请选择自动/BOM、UTF-8、UTF-16或GB18030')
    selected = encoding
    if encoding in ('auto','utf-16'):
        if raw.startswith(codecs.BOM_UTF16_LE):
            selected = 'utf-16-le'
        elif raw.startswith(codecs.BOM_UTF16_BE):
            selected = 'utf-16-be'
        elif encoding == 'utf-16':
            raise LogError('UTF-16日志需要BOM，不能猜测字节顺序')
        else:
            selected = 'utf-8'
    if selected == 'utf-8' and raw.startswith(codecs.BOM_UTF8):
        selected = 'utf-8-sig'
    try:
        text = raw.decode(selected, errors='strict')
    except UnicodeError as exc:
        raise LogError('日志编码不匹配；未替换乱码。GB18030日志请手动选择相应编码') from exc
    if selected.startswith('utf-16') and text.startswith('\ufeff'):
        text = text[1:]
    if any((ord(c) < 32 and c not in '\t\r\n') or 127 <= ord(c) <= 159 for c in text) or '\ufffd' in text:
        raise LogError('日志含NUL、控制字符或乱码替换符，拒绝当作完整文本入库')
    if len(text)>max_chars:
        raise LogError(f'日志超过{max_chars}字符上限，请拆分原件后导入')
    # splitlines也会把VT、U+2028当换行；物理行只认CRLF/LF/CR。
    lines = re.findall(r'[^\r\n]*(?:\r\n|\r|\n|$)',text)[:-1]
    if not text.strip():
        raise LogError('日志没有可索引正文')
    if len(lines)>max_lines:
        raise LogError(f'日志超过{max_lines}行上限，请拆分原件后导入')
    endings = set(re.findall(r'\r\n|\r|\n',text))
    newline = 'mixed' if len(endings)>1 else {'\r\n':'crlf','\n':'lf','\r':'cr'}.get(next(iter(endings),''),'none')
    return {'format':1,'kind':'log','sha256':hashlib.sha256(raw).hexdigest(),
            'encoding':selected,'line_count':len(lines),'newline':newline}, lines


def metadata(value):
    if isinstance(value,str):
        try:
            value=json.loads(value)
        except ValueError:
            return None
    if not isinstance(value,dict) or type(value.get('format')) is not int or value['format']!=1 or value.get('kind')!='log':
        return None
    if not isinstance(value.get('sha256'),str) or not re.fullmatch(r'[0-9a-f]{64}',value['sha256']):
        return None
    if not isinstance(value.get('encoding'),str) or value['encoding'] not in ACTUAL_ENCODINGS:
        return None
    if type(value.get('line_count')) is not int or not 0<value['line_count']<=2**53-1 or value.get('newline') not in ('crlf','lf','cr','mixed','none'):
        return None
    return {key:value[key] for key in ('format','kind','sha256','encoding','line_count','newline')}


def is_log_document(document):
    """原件后缀或入库标签仍在时，缺失元数据不能冒充普通文本。"""
    if Path(document['stored_name']).suffix.lower()=='.log':
        return True
    try:
        tags=json.loads(document['tags'] or '[]')
    except (TypeError,ValueError):
        return False
    return isinstance(tags,list) and 'log_source' in tags


@dataclass
class LogPiece:
    text: str
    token_count: int
    line_start: int
    line_end: int


def build_pieces(lines, tokenizer, max_tokens, overlap, max_chunks):
    if max_tokens<=0 or not 0<=overlap<max_tokens:
        raise LogError('日志切块预算无效')
    # 字符预算同时约束大量空白：避免零token行导致长窗口和二次计数开销。
    weights=[max(1,tokenizer.count(line),(len(line)+7)//8) for line in lines]
    for number,weight in enumerate(weights,1):
        if weight>max_tokens:
            raise LogError(f'日志第{number}行超过单块预算，已拒绝在行内截断；请先拆分该原件')
    timestamps=[i for i,line in enumerate(lines) if TIMESTAMP.match(line)]
    starts=sorted(set([0]+timestamps)) if timestamps else [0]+[
        i for i in range(1,len(lines)) if not lines[i-1].strip() and lines[i].strip()]
    # 两种记录规则下，首部空白都并入首条正文记录。
    if len(starts)>1 and not ''.join(lines[:starts[1]]).strip():
        starts.pop(1)
    boundaries=starts+[len(lines)]
    pieces=[]
    for first,last in zip(boundaries,boundaries[1:]):
        start=first
        while start<last:
            end=start
            spent=0
            while end<last and spent+weights[end]<=max_tokens:
                spent+=weights[end]
                end+=1
            text=''.join(lines[start:end])
            while tokenizer.count(text)>max_tokens and end>start+1:
                end-=1
                text=''.join(lines[start:end])
            if not text.strip():
                raise LogError(f'日志第{start+1}–{end}行为超预算连续空白，已拒绝丢弃原行；请拆分原件')
            if len(pieces)>=max_chunks:
                raise LogError(f'日志切片超过{max_chunks}上限，请拆分原件')
            pieces.append(LogPiece(text,tokenizer.count(text),start+1,end))
            if end==last:
                break
            next_start=end
            spent=0
            while next_start>start+1 and spent+weights[next_start-1]<=overlap:
                next_start-=1
                spent+=weights[next_start]
            start=next_start
    return pieces
