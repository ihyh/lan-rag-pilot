"""Attach verified human image descriptions to selected image evidence."""
from pathlib import Path
import json

from . import ocr_upload, parsing
from .chunking import TokenizerAdapter
from .embeddings import embedding_service


class ImageCaptionEvidenceError(ValueError):
    pass


def attach_image_captions(db, sources, document_ids, *, max_extra=12):
    """Only append real, fully verified description chunks from selected images."""
    result = [dict(source) for source in sources]
    used = {source['chunk_id'] for source in result}
    extra = []
    document_order = list(dict.fromkeys(source['document_id'] for source in result))
    for document_id in document_order:
        if document_ids is not None and document_id not in document_ids:
            continue
        doc = db.execute('SELECT filename,stored_name,status,lifecycle,caption,tags,num_chunks FROM documents WHERE id=?',
                         (document_id,)).fetchone()
        if (doc is None or doc['status'] != 'ready' or doc['lifecycle'] != 'active'
                or Path(doc['stored_name']).suffix.lower() not in {'.png', '.jpg', '.jpeg'}
                or not (doc['caption'] or '').strip()):
            continue
        try:
            pieces = ocr_upload._caption_pieces(doc['caption'], TokenizerAdapter(embedding_service.tokenizer_or_none()))
            tags = json.loads(doc['tags'] or '[]')
        except (parsing.ParseError, ValueError, TypeError):
            raise ImageCaptionEvidenceError('资料的图片说明与索引不一致，请联系维护人员核对') from None
        if not pieces:
            continue
        job = db.execute('SELECT state FROM ocr_jobs WHERE document_id=?', (document_id,)).fetchone()
        rows = db.execute('SELECT id,seq,page,paragraph,content FROM chunks WHERE document_id=? ORDER BY seq',
                          (document_id,)).fetchall()
        count = len(pieces)
        caption_only = isinstance(tags, list) and 'image_caption_only' in tags
        ocr_image = isinstance(tags, list) and 'ocr_image_text_only' in tags
        direct_image = caption_only and 'image_text_direct' in tags and job is None
        note = '图片OCR文字（仅文字已核对，图表关系和阅读顺序未验证）：\n'
        if ((not direct_image and (job is None or job['state'] != 'ready')) or caption_only == ocr_image
                or doc['num_chunks'] != len(rows)
                or (len(rows) != count if caption_only else len(rows) <= count)
                or any(row['seq'] != i for i, row in enumerate(rows))
                or any(row['page'] is not None or row['paragraph'] is not None or row['content'] != piece.text
                       for row, piece in zip(rows[:count], pieces))
                or any(row['page'] is not None or row['paragraph'] != 1 or not row['content'].startswith(note)
                       for row in rows[count:])):
            raise ImageCaptionEvidenceError('资料的图片说明与索引不一致，请联系维护人员核对')
        caption_ids = {row['id'] for row in rows[:count]}
        for source in result:
            if source['document_id'] == document_id and source['chunk_id'] in caption_ids:
                source['_image_caption'] = True
        for row in rows[:count]:
            if row['id'] not in used:
                extra.append({'chunk_id': row['id'], 'document_id': document_id, 'seq': row['seq'],
                              'filename': doc['filename'], 'page': row['page'], 'paragraph': row['paragraph'],
                              'score': None, 'content': row['content'], '_image_caption': True})
                used.add(row['id'])
    if len(extra) > max_extra:
        raise ImageCaptionEvidenceError('关联图片说明过多，请缩小资料范围后重试')
    return result + extra
