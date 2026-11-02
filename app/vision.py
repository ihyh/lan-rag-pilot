"""本地视觉检索：机器描述只做召回，原图才是视觉模型的输入。"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

import numpy as np

from .config import settings
from .index import technical_terms


def candidate(db, qvec, document_ids, question, vector_index):
    """在允许范围内融合语义和问题字面词，最多返回两条真实图片切片。"""
    data_dir = getattr(settings, 'data_dir', None)
    if not data_dir:
        return []
    try:
        path = Path(data_dir) / 'vision-hints.json'
        if path.stat().st_size > 2_000_000:
            return []
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('format') != 1 or data.get('embedding_model') != settings.embed_model:
            return []
        hints = data['items']
        if not isinstance(hints, list) or len(hints) > 200:
            return []
        q = np.asarray(qvec, dtype=np.float32).reshape(-1)
        scoped = vector_index._filename_scope(question, document_ids)
        if not scoped:
            return []
        terms = set(technical_terms(question))
        bigrams = {run[i:i+2] for run in re.findall(r'[\u4e00-\u9fff]+', question)
                   for i in range(len(run)-1)}
        matches = []
        for hint in hints:
            text = hint.get('search_text', '')
            if not isinstance(text, str) or not terms.issubset(set(technical_terms(text))):
                continue
            overlap = sum(term in text for term in bigrams)
            if not terms and overlap < 2:
                continue
            vector = np.frombuffer(base64.b64decode(hint['vector'], validate=True), dtype=np.float32)
            if vector.shape != q.shape or not np.isfinite(vector).all():
                continue
            row = db.execute("SELECT d.id AS document_id,d.sha256,d.filename,d.tags,c.id AS chunk_id,"
                "c.seq,c.page,c.paragraph,c.content FROM documents d JOIN chunks c ON c.document_id=d.id "
                "WHERE d.sha256=? AND d.status='ready' AND d.lifecycle='active' AND c.seq=0", (hint['sha256'],)).fetchone()
            if not row or row['document_id'] not in scoped:
                continue
            tags = json.loads(row['tags'] or '[]')
            if not isinstance(tags, list) or 'web_image_shared_source' not in tags:
                continue
            score = float(np.dot(vector, q))
            if np.isfinite(score) and score >= 0.4:
                # ponytail: 仅有界词面加权；描述是召回线索，不能作为事实证据。
                matches.append((score + min(overlap, 6) * 0.03, score, dict(row), '\n'.join(text.split('\n', 2)[:2])[:1200]))
        matches.sort(key=lambda item: (-item[0], item[2]['document_id']))
        if not matches:
            return []
        selected = [matches[0]]
        if len(matches) > 1 and matches[0][0] - matches[1][0] <= 0.04:
            selected.append(matches[1])
        return [{k: row[k] for k in ('chunk_id','document_id','seq','filename','page','paragraph','content')}
                | {'score':round(score, 4), '_vision_hint':hint} for _,score,row,hint in selected]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return []


def attach_image(db, sources, candidates, document_ids):
    """仅附加权限范围内、原件哈希匹配的图片；机器描述不送作证据。"""
    if not candidates:
        return sources
    try:
        path = Path(settings.data_dir) / 'vision-hints.json'
        if path.stat().st_size > 2_000_000:
            return sources
        hints = json.loads(path.read_text(encoding='utf-8'))
        if hints.get('format') != 1 or hints.get('embedding_model') != settings.embed_model or not isinstance(hints.get('items'), list):
            return sources
    except (OSError, ValueError, TypeError, AttributeError):
        return sources
    result = [dict(source) for source in sources]
    for source in result:
        source.pop('_visual_image', None)
    wanted = {item['document_id'] for item in candidates if item['document_id'] in document_ids}
    candidate_hints = {item['document_id']: item.get('_vision_hint', '') for item in candidates}
    attached = set()
    for item in candidates:
        if item['document_id'] in wanted and item['document_id'] not in {source['document_id'] for source in result}:
            result.append(dict(item))
    for source in result:
        if source['document_id'] not in wanted or source['document_id'] in attached:
            continue
        row = db.execute('SELECT stored_name,sha256,tags,size_bytes FROM documents WHERE id=?',
                         (source['document_id'],)).fetchone()
        if not row:
            continue
        try:
            tags = json.loads(row['tags'] or '[]')
            name = row['stored_name']
            if not isinstance(tags, list) or 'web_image_shared_source' not in tags or Path(name).name != name:
                continue
            root = Path(settings.upload_dir).resolve()
            path = (root/name).resolve()
            if path.parent != root or path.suffix.lower() not in {'.png','.jpg','.jpeg'} or not 0 < row['size_bytes'] <= 10*1024*1024:
                continue
            raw = path.read_bytes()
            if len(raw) == row['size_bytes'] and hashlib.sha256(raw).hexdigest() == row['sha256']:
                source['_visual_image'] = raw
                source['_vision_hint'] = candidate_hints.get(source['document_id'], '')
                attached.add(source['document_id'])
        except (OSError, ValueError, TypeError):
            continue
    return result
