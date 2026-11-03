"""指定博客园网页解析与有界读取；CLI和管理导入共用。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

CATEGORY_URL = 'https://www.cnblogs.com/wwwen/category/2130396.html'
CONFIRMED_ARTICLES = (
    {'url':'https://www.cnblogs.com/wwwen/p/16044077.html','title':'SECS/GEM 基本概念'},
    {'url':'https://www.cnblogs.com/wwwen/p/16048873.html','title':'HSMS'},
    {'url':'https://www.cnblogs.com/wwwen/p/16053090.html','title':'SECS-II'},
    {'url':'https://www.cnblogs.com/wwwen/p/16065818.html','title':'GEM'},
)
CONFIRMED_URLS = frozenset(item['url'] for item in CONFIRMED_ARTICLES)
MAX_BYTES = 2_000_000
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
SKIP = {'script', 'style', 'noscript', 'iframe', 'object', 'svg', 'canvas', 'video', 'audio'}
BLOCK = {'p', 'div', 'section', 'article', 'blockquote', 'li', 'ul', 'ol', 'pre', 'table', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)


class Tree(HTMLParser):
    def __init__(self, raw: bytes):
        super().__init__(convert_charrefs=True)
        self.root = Node('root')
        self.stack = [self.root]
        if len(raw) > MAX_BYTES:
            raise ValueError('网页超过大小上限')
        try:
            self.feed(raw.decode('utf-8-sig'))
            self.close()
        except UnicodeDecodeError as exc:
            raise ValueError('仅接受UTF-8网页，拒绝乱码替换') from exc

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)
            if len(self.stack) > 128:
                raise ValueError('HTML嵌套过深')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def nodes(node):
    yield node
    for child in node.children:
        if isinstance(child, Node):
            yield from nodes(child)


def text_of(node):
    if isinstance(node, str):
        return node
    if node.tag in SKIP:
        return ''
    if node.tag == 'br':
        return '\n'
    return ''.join(text_of(c) + ('\n' if isinstance(c, Node) and c.tag in BLOCK else '')
                   for c in node.children)


def public_link(base, href):
    if not href:
        return ''
    value = urljoin(base, href or '')
    parts = urlsplit(value)
    return value if parts.scheme in {'https', 'http'} and parts.hostname and not parts.username else ''


def article_url(url):
    return bool(re.fullmatch(r'https://www\.cnblogs\.com/wwwen/p/[0-9]+\.html', url))


def category_members(raw):
    members = []
    for node in nodes(Tree(raw).root):
        if node.tag == 'a' and 'entrylistItemTitle' in (node.attrs.get('class') or '').split():
            url = urljoin(CATEGORY_URL, node.attrs.get('href') or '')
            if not article_url(url):
                raise ValueError('栏目出现范围外文章，停止而非自动扩站')
            if url not in [m['url'] for m in members]:
                members.append({'url': url, 'title': text_of(node).strip()})
    if len(members) != 4:
        raise ValueError(f'栏目成员为{len(members)}篇，与确认的4篇不符')
    return members


def extract_article(raw, url, *, public=False):
    if not public and not article_url(url):
        raise ValueError('不在已限定作者的文章URL范围')
    tree = Tree(raw)
    if public:
        def prune(node):
            node.children = [c for c in node.children if isinstance(c, str) or (
                c.tag not in SKIP | {'nav', 'header', 'footer', 'aside', 'form'}
                and 'hidden' not in c.attrs and c.attrs.get('aria-hidden') != 'true')]
            for child in node.children:
                if isinstance(child, Node):
                    prune(child)
        prune(tree.root)
    bodies = [n for n in nodes(tree.root) if n.attrs.get('id') == 'cnblogs_post_body']
    titles = [n for n in nodes(tree.root) if n.attrs.get('id') == 'cb_post_title_url']
    if not titles:
        titles = [n for n in nodes(tree.root) if n.tag == 'title']
    if public and not bodies:
        for tag in ('main', 'article', 'body'):
            bodies = [n for n in nodes(tree.root) if n.tag == tag]
            if bodies:
                # A main region can contain several articles; retain the region.
                break
    if public and not titles:
        titles = [n for n in nodes(tree.root) if n.tag == 'h1']
    if len(bodies) != 1 or not titles or not text_of(titles[0]).strip():
        raise ValueError('未找到唯一文章正文/标题，拒绝把错误页或导航入库')
    body = bodies[0]
    images, links, warnings, blocks = [], [], set(), []
    for node in nodes(body):
        if node.tag == 'img':
            image_url = public_link(url, node.attrs.get('data-src') or node.attrs.get('src'))
            images.append({'url': image_url, 'alt': node.attrs.get('alt') or '', 'status': 'not_extracted'})
            warnings.add('image_text_not_extracted')
        elif node.tag == 'a':
            link = public_link(url, node.attrs.get('href'))
            if link:
                links.append({'url': link, 'label': text_of(node).strip()})
        elif node.tag in SKIP - {'script', 'style'}:
            warnings.add('embedded_content_not_extracted')
        if node.attrs.get('rowspan', '1') != '1' or node.attrs.get('colspan', '1') != '1':
            warnings.add('table_spans_not_expanded')

    def add(kind, text, **metadata):
        # pre代码的缩进/换行保留；普通段落仅折叠HTML排版空白。
        if kind != 'pre':
            text = text.strip()
        if text.strip():
            blocks.append({'number': len(blocks)+1, 'kind': kind, 'text': text, **metadata})

    def visit(node):
        if isinstance(node, str):
            add('text', node)
            return
        if node.tag in SKIP:
            return
        if node.tag == 'table':
            if any(n is not node and n.tag == 'table' for n in nodes(node)):
                raise ValueError('嵌套表格暂不支持，拒绝丢失单元格关系')
            rows = [[{'text': re.sub(r'\s+', ' ', text_of(c)).strip(),
                      'rowspan': c.attrs.get('rowspan', '1'), 'colspan': c.attrs.get('colspan', '1')}
                     for c in row.children if isinstance(c, Node) and c.tag in {'td', 'th'}]
                    for row in nodes(node) if row.tag == 'tr']
            add('table', '\n'.join('\t'.join(c['text'] for c in row) for row in rows), rows=rows)
        elif node.tag == 'pre':
            add('pre', text_of(node))
        elif node.tag in {'p', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            # 含复杂块的列表项递归保留pre/table；不把代码压成一句。
            if any(n is not node and n.tag in {'pre', 'table', 'ul', 'ol'} for n in nodes(node)):
                for c in node.children:
                    visit(c)
            else:
                add(node.tag, re.sub(r'\s+', ' ', text_of(node)))
        else:
            for c in node.children:
                visit(c)

    visit(body)
    if not blocks:
        raise ValueError('正文没有可索引文字；图像不能当成已提取正文')
    headings = []
    for block in blocks:
        if re.fullmatch(r'h[1-6]', block['kind']):
            level = int(block['kind'][1])
            headings = [h for h in headings if h['level'] < level]
            block['headings'] = [dict(h) for h in headings]
            headings.append({'level': level, 'number': block['number'], 'text': block['text']})
        else:
            block['headings'] = [dict(h) for h in headings]
    return {'source': {'url': url, 'title': text_of(titles[0]).strip(),
                       'fetched_at': datetime.now(timezone.utc).isoformat(),
                       'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw)},
            'status': 'partial' if warnings else 'text_extracted', 'warnings': sorted(warnings),
            'blocks': blocks, 'images': images, 'links': links,
            'location_kind': 'extracted_block_not_physical_page'}


def context_text(block):
    """仅复制同一正文内实际上级标题，块号和标题块号分别保存在提取JSON中。"""
    return '\n'.join([h['text'] for h in block['headings']] + [block['text']])


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('站点发生重定向，需核对新来源后再导入')


def fetch(url):
    if url != CATEGORY_URL and not article_url(url):
        raise ValueError('只读取指定栏目和该作者文章')
    with build_opener(NoRedirect).open(Request(url, headers={'User-Agent': 'PersonalKnowledgeImport/1.0'}), timeout=25) as response:
        if response.headers.get_content_type() != 'text/html':
            raise ValueError('响应不是HTML')
        raw = response.read(MAX_BYTES+1)
        if len(raw) > MAX_BYTES:
            raise ValueError('网页超过大小上限')
        return raw



def metadata(value):
    """只返回格式有效的网页来源，不用损坏元数据伪造网页身份。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, dict) or type(value.get('format')) is not int or value['format'] != 1 or value.get('kind') != 'web':
        return None
    from .public_web import normalize_url
    try:
        normalize_url(value.get('url'))
    except ValueError:
        return None
    if not isinstance(value.get('title'), str) or not value['title'].strip():
        return None
    if not isinstance(value.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', value['sha256']):
        return None
    try:
        stamp = datetime.fromisoformat(value['fetched_at'])
        if stamp.tzinfo is None:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    if value.get('status') not in ('partial', 'text_extracted') or not isinstance(value.get('warnings'), list):
        return None
    if not all(isinstance(item, str) for item in value['warnings']):
        return None
    if any(type(value.get(key)) is not int or value[key] < 0 for key in ('image_count', 'block_count')):
        return None
    return {key:value[key] for key in ('format','kind','url','title','fetched_at','sha256','status','warnings','image_count','block_count')}


def build_pieces(article, tokenizer, max_tokens, overlap, max_chunks):
    """提取块号及真实上级标题保留；代码/表格超过预算拒绝中间截断。"""
    from .chunking import Piece, chunk_units
    from .parsing import ParseError, Unit
    pieces = []
    for block in article['blocks']:
        text = context_text(block)
        if len(pieces) >= max_chunks:
            raise ParseError('网页切片超过配置上限', code='too_many_chunks')
        if block['kind'] in {'pre', 'table'}:
            count = tokenizer.count(text)
            if count > max_tokens:
                raise ParseError('代码/表格超过单块预算，拒绝硬截断结构', code='web_structure_too_large')
            pieces.append(Piece(text, count, paragraph=block['number']))
        else:
            pieces.extend(chunk_units([Unit(text=text, paragraph=block['number'])], tokenizer,
                                      max_tokens, overlap, max_pieces=max_chunks-len(pieces)))
    return pieces
