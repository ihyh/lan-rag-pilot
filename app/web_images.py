"""Prepared web images: administrator preview, explicit OCR review, no auto index."""
import hashlib
import json
from pathlib import Path
import re
import uuid
from urllib.parse import urlsplit

from . import audit, ingest, ocr_upload, web_content
from .config import settings
from .db import now_iso

_SHA = re.compile(r'[0-9a-f]{64}')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _error(message='网页图片暂存资料损坏，请重新准备后再核对'):
    return ingest.IngestError(message, 409, 'web_image_invalid')


def _root():
    data = settings.data_dir.resolve()
    root = (data / 'web-images').resolve()
    if root.parent != data:
        raise _error()
    return root


def _file(root, folder, name):
    directory = (root / folder).resolve() if folder else root
    path = (directory / name).resolve()
    if not directory.is_relative_to(root) or path.parent != directory:
        raise _error()
    return path


def _read(path, limit):
    try:
        with path.open('rb') as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise _error()
        return data
    except OSError as exc:
        raise _error() from exc


def _json(raw):
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise _error() from exc


def validate_item(item):
    if not isinstance(item, dict) or type(item.get('id')) is not int or not 1 <= item['id'] <= 200:
        raise _error()
    sha = item.get('image_sha256')
    if not isinstance(sha, str) or not _SHA.fullmatch(sha):
        raise _error()
    origin = item.get('origin')
    info = item.get('image_info')
    if (not isinstance(origin, dict) or origin.get('origin_kind') != 'web_image'
            or not isinstance(origin.get('article_url'), str)
            or origin['article_url'] not in web_content.CONFIRMED_URLS
            or not isinstance(origin.get('article_title'), str)
            or not isinstance(origin.get('article_sha256'), str)
            or not _SHA.fullmatch(origin['article_sha256'])
            or not isinstance(origin.get('article_fetched_at'), str)
            or not isinstance(origin.get('image_url'), str)
            or type(origin.get('image_number')) is not int or not 1 <= origin['image_number'] <= 200
            or not isinstance(origin.get('headings'), list)
            or not all(isinstance(h, dict) and isinstance(h.get('text'), str) for h in origin['headings'])
            or not isinstance(info, dict) or info.get('format') not in ('PNG', 'JPEG')):
        raise _error()
    try:
        image_url = urlsplit(origin['image_url'])
        if (image_url.scheme != 'https' or image_url.hostname != 'img2022.cnblogs.com'
                or image_url.username or image_url.password or image_url.port not in (None, 443)):
            raise _error()
    except ValueError as exc:
        raise _error() from exc
    suffix, mime = ('.png', 'image/png') if info['format'] == 'PNG' else ('.jpg', 'image/jpeg')
    if (info.get('suffix') not in (('.png',) if suffix == '.png' else ('.jpg', '.jpeg'))
            or info.get('mime') != mime
            or any(type(info.get(key)) is not int or info[key] <= 0 for key in ('width', 'height'))
            or info['width'] * info['height'] > 20_000_000
            or type(item.get('size_bytes')) is not int or not 1 <= item['size_bytes'] <= 10 * 1024 * 1024
            or item.get('original_name') != sha + info['suffix']
            or item.get('ocr_name') != sha + '.json'
            or not isinstance(item.get('ocr_sha256'), str) or not _SHA.fullmatch(item['ocr_sha256'])
            or item.get('ocr_status') not in ('ok', 'suspect', 'failed')):
        raise _error()
    return item


def items():
    root = _root()
    path = _file(root, '', 'catalog.json')
    if not path.exists():
        return []
    catalog = _json(_read(path, 1024 * 1024))
    if (not isinstance(catalog, dict) or type(catalog.get('format')) is not int or catalog['format'] != 1
            or not isinstance(catalog.get('items'), list) or not 1 <= len(catalog['items']) <= 200):
        raise _error()
    rows = [validate_item(item) for item in catalog['items']]
    if len({item['id'] for item in rows}) != len(rows):
        raise _error()
    return rows


def asset(item, *, with_ocr=False):
    root = _root()
    raw = _read(_file(root, 'originals', item['original_name']), 10 * 1024 * 1024)
    magic = b'\x89PNG\r\n\x1a\n' if item['image_info']['format'] == 'PNG' else b'\xff\xd8\xff'
    if len(raw) != item['size_bytes'] or digest(raw) != item['image_sha256'] or not raw.startswith(magic):
        raise _error('网页图片原件与暂存清单不一致，拒绝核对或入库')
    if not with_ocr:
        return raw
    ocr = _read(_file(root, 'ocr', item['ocr_name']), 40 * 1024 * 1024)
    if digest(ocr) != item['ocr_sha256']:
        raise _error('网页图片识别结果与暂存清单不一致')
    pages = ocr_upload.validate(_json(ocr), item['image_sha256'], image=True)
    if pages[0]['status'] != item['ocr_status']:
        raise _error()
    return raw, ocr


def item_by_id(reference_id):
    item = next((item for item in items() if item['id'] == reference_id), None)
    if item is None:
        raise ingest.IngestError('网页图片不存在或尚未准备', 404, 'web_image_not_found')
    return item


def catalog(db):
    rows = items()
    result = []
    for item in rows:
        origin = item['origin']
        doc = db.execute('SELECT d.id,d.filename,d.status,d.tags,j.state FROM documents d LEFT JOIN ocr_jobs j '
                         'ON j.document_id=d.id WHERE d.sha256=?', (item['image_sha256'],)).fetchone()
        try:
            tags = json.loads(doc['tags'] or '[]') if doc else []
        except (TypeError, ValueError):
            tags = []
        result.append({'id': item['id'], 'title': origin['article_title'], 'article_url': origin['article_url'],
            'image_number': origin['image_number'], 'section': ' / '.join(h['text'] for h in origin['headings']),
            'source_captured_at': origin['article_fetched_at'], 'source_snapshot_sha256': origin['article_sha256'],
            'image_url': origin['image_url'],
            'ocr_status': item['ocr_status'], 'document_id': doc['id'] if doc else None,
            'filename': doc['filename'] if doc else None,
            'indexed': bool(doc and doc['status'] == 'ready'),
            'human_review_pending': bool(doc and isinstance(tags, list) and 'ocr_human_review_pending' in tags),
            'state': doc['state'] or 'duplicate' if doc else 'pending',
            'original_url': f'/api/admin/web-images/{item["id"]}/original'})
    return {'available': bool(rows), 'items': result,
            'message': '' if rows else '尚未准备网页图片，请先完成本机图片暂存。'}


def prepare(db, reference_id, version, user, ip=''):
    if not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,31}', version):
        raise ingest.IngestError('文档版本格式无效', 422, 'version_invalid')
    item = item_by_id(reference_id)
    raw, result = asset(item, with_ocr=True)
    with ingest.ingest_lock:
        existing = db.execute('SELECT d.id,d.filename,j.state FROM documents d LEFT JOIN ocr_jobs j '
                              'ON j.document_id=d.id WHERE d.sha256=?', (item['image_sha256'],)).fetchone()
        if existing:
            if not existing['state']:
                raise ingest.IngestError('相同图片已存在，但没有OCR核对任务，请从文档列表检查', 409, 'duplicate')
            return {'document_id': existing['id'], 'filename': existing['filename'], 'state': existing['state']}
        origin = item['origin']
        title = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', origin['article_title']).strip()[:100] or '网页图片'
        filename = f'{title}_图{origin["image_number"]:02d}_{item["image_sha256"][:8]}{item["image_info"]["suffix"]}'
        doc = None
        artifact = None
        db.execute('BEGIN IMMEDIATE')
        try:
            doc, _ = ingest.register_bytes(db, filename=filename, content_type=item['image_info']['mime'],
                data=raw, user_id=user.id, version=version, visibility='restricted', ocr_image=True,
                tags=['web_image_article_url=' + origin['article_url'],
                      'web_image_number=' + str(origin['image_number']),
                      'web_image_article_sha256=' + origin['article_sha256'],
                      'web_image_url=' + origin['image_url'],
                      'web_image_captured_at=' + origin['article_fetched_at']])
            name = uuid.uuid4().hex + '.json'
            artifact = ocr_upload.artifact(doc['id'], name)
            artifact.parent.mkdir(parents=True, exist_ok=True)
            with artifact.open('xb') as stream:
                stream.write(result)
            now = now_iso()
            db.execute("INSERT INTO ocr_jobs(document_id,state,result_name,created_at,updated_at) VALUES (?,'review',?,?,?)",
                       (doc['id'], name, now, now))
            audit.log_audit(db, action='web_image_prepare', user_id=user.id, username=user.username,
                            detail=f'doc:{doc["id"]} reference:{reference_id} waiting_for_review', ip=ip)
            db.commit()
        except BaseException:
            db.rollback()
            if doc:
                (settings.upload_dir / doc['stored_name']).unlink(missing_ok=True)
            if artifact:
                artifact.unlink(missing_ok=True)
            raise
        return {'document_id': doc['id'], 'filename': filename, 'state': 'review'}
