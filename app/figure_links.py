"""识别网页正文中有明确结构证据的文字与图片关系。"""
from __future__ import annotations

import re
from . import web_content

_REF = re.compile(r'(?:如下图|见下图)')
_NUMBER = re.compile(r'图\s*(\d+(?:[.．-]\d+)*|[一二三四五六七八九十]+)')
_CHINESE = {'一':'1', '二':'2', '三':'3', '四':'4', '五':'5', '六':'6', '七':'7', '八':'8', '九':'9', '十':'10'}


def figure_numbers(text: str) -> set[str]:
    return {_CHINESE.get(match, match.replace('．', '.')) for match in _NUMBER.findall(text)}


def adjacent_images(raw: bytes, url: str) -> list[dict]:
    """只接受相邻段落中唯一的图片；图片顺序不能当作图号。"""
    root = web_content.Tree(raw).root
    bodies = [n for n in web_content.nodes(root) if n.attrs.get('id') == 'cnblogs_post_body']
    if not bodies:
        for tag in ('main', 'article', 'body'):
            bodies = [n for n in web_content.nodes(root) if n.tag == tag]
            if bodies:
                break
    if len(bodies) != 1:
        return []
    links = []
    for parent in web_content.nodes(bodies[0]):
        children = [x for x in parent.children if isinstance(x, web_content.Node)]
        for text_node, image_node in zip(children, children[1:]):
            if text_node.tag != 'p' or image_node.tag != 'p':
                continue
            text = re.sub(r'\s+', ' ', web_content.text_of(text_node)).strip()
            if not _REF.search(text) or any(n.tag == 'img' for n in web_content.nodes(text_node)):
                continue
            images = [n for n in web_content.nodes(image_node) if n.tag == 'img']
            if len(images) != 1 or web_content.text_of(image_node).strip():
                continue
            image = images[0]
            image_url = web_content.public_link(url, image.attrs.get('data-src') or image.attrs.get('src'))
            if image_url:
                links.append({'text': text, 'image_url': image_url})
    # 仅使用 figure/figcaption 中唯一图号；重复图号或多个图片均不推断。
    captions = {}
    body = bodies[0]
    for figure in (n for n in web_content.nodes(body) if n.tag == 'figure'):
        images = [n for n in web_content.nodes(figure) if n.tag == 'img']
        labels = [n for n in web_content.nodes(figure) if n.tag == 'figcaption']
        if len(images) != 1 or len(labels) != 1:
            continue
        numbers = figure_numbers(web_content.text_of(labels[0]))
        image_url = web_content.public_link(url, images[0].attrs.get('data-src') or images[0].attrs.get('src'))
        if len(numbers) == 1 and image_url:
            captions.setdefault(next(iter(numbers)), []).append(image_url)
    for paragraph in (n for n in web_content.nodes(body) if n.tag == 'p'):
        text = re.sub(r'\s+', ' ', web_content.text_of(paragraph)).strip()
        for number in figure_numbers(text):
            if len(captions.get(number, [])) == 1:
                item = {'text': text, 'image_url': captions[number][0]}
                if item not in links:
                    links.append(item)
    return links


def attach_adjacent_images(db, sources: list[dict], document_ids: set[int], upload_dir) -> list[dict]:
    """在允许的检索范围内补充真实图片切片，并记录可核对的相邻关系。"""
    import hashlib
    import json
    from pathlib import Path
    from .source_metadata import parse_metadata

    if not any((s.get('filename') or '').endswith('.html.snapshot.txt') for s in sources):
        return sources
    pairs = json.loads(Path(__file__).with_name('figure_snapshot_pairs.json').read_text(encoding='utf-8'))
    image_rows = db.execute("SELECT d.id,d.filename,d.tags,c.id AS chunk_id,c.seq,c.page,c.paragraph,c.content "
                            "FROM documents d JOIN chunks c ON c.document_id=d.id "
                            "WHERE d.status='ready' AND d.lifecycle='active' AND c.seq=0").fetchall()
    available = []
    for row in image_rows:
        if row['id'] not in document_ids:
            continue
        try:
            tags = json.loads(row['tags'] or '[]')
            if not isinstance(tags, list) or 'web_image_shared_source' not in tags:
                continue
            meta = dict(t.split('=', 1) for t in tags if isinstance(t, str) and '=' in t)
        except (TypeError, ValueError):
            continue
        if all(k in meta for k in ('web_image_url', 'web_image_article_url', 'web_image_article_sha256')):
            available.append((row, meta))
    if not available:
        return sources
    result = list(sources)
    seen = {s['document_id'] for s in result}
    cache = {}
    for source in sources:
        row = db.execute('SELECT stored_name,sha256,source_metadata FROM documents WHERE id=?',
                         (source['document_id'],)).fetchone()
        if not row:
            continue
        metadata = parse_metadata(row['source_metadata'])
        if not metadata or metadata['kind'] != 'web' or metadata['sha256'] != row['sha256']:
            continue
        if source['document_id'] not in cache:
            name = row['stored_name']
            try:
                if not name or Path(name).name != name:
                    continue
                raw = (Path(upload_dir) / name).read_bytes()
                if hashlib.sha256(raw).hexdigest() != row['sha256']:
                    continue
                cache[source['document_id']] = adjacent_images(raw, metadata['url'])
            except (OSError, ValueError):
                continue
        excerpt = re.sub(r'\s+', ' ', source.get('content') or '')
        for link in cache[source['document_id']]:
            if link['text'] not in excerpt:
                continue
            for image, image_meta in available:
                same = image_meta['web_image_article_sha256'] == row['sha256']
                same |= any(p['url'] == metadata['url'] and p['article_sha256'] == row['sha256']
                            and p['image_article_sha256'] == image_meta['web_image_article_sha256'] for p in pairs)
                if not same or image_meta['web_image_article_url'] != metadata['url'] or image_meta['web_image_url'] != link['image_url']:
                    continue
                if image['id'] not in seen and len(result) >= len(sources) + 3:
                    continue
                source.setdefault('related_images', []).append({'document_id': image['id'], 'filename': image['filename']})
                if image['id'] not in seen:
                    result.append({'chunk_id': image['chunk_id'], 'document_id': image['id'], 'seq': image['seq'],
                                   'filename': image['filename'], 'page': image['page'], 'paragraph': image['paragraph'],
                                   'score': 0.0, 'content': image['content']})
                    seen.add(image['id'])
    return result
