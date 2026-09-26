"""Conservative article media extraction; never execute source HTML/scripts."""
import html
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, parse_qs

MEDIA_FORMAT_VERSION = 2
VOID = set('area base br col embed hr img input link meta param source track wbr'.split())
BODY_CLASSES = {'entry-content', 'article-body', 'article-content', 'post-content', 'post__content', 'post-body'}
NOISE = re.compile(r'(?:^|[\s_-])(?:related|recommendations?|recommended|author|avatar|sidebar|social|share|newsletter|navigation|menu|post-card|tease-text)(?:$|[\s_-])', re.I)


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []


class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break


def safe_url(value, base=''):
    url = urljoin(base, html.unescape(value or '').strip())
    p = urlparse(url)
    return url if p.scheme in {'http', 'https'} and p.hostname and not p.username and not p.password else ''


def video_ref(value, base='', title='', poster=''):
    url = safe_url(value, base)
    p = urlparse(url)
    host = (p.hostname or '').lower()
    ident = ''
    if host in {'youtube.com', 'www.youtube.com', 'youtube-nocookie.com', 'www.youtube-nocookie.com', 'm.youtube.com'}:
        ident = p.path.split('/')[-1] if p.path.startswith(('/embed/', '/shorts/')) else parse_qs(p.query).get('v', [''])[0]
    elif host == 'youtu.be':
        ident = p.path.strip('/')
    if re.fullmatch(r'[\w-]{11}', ident):
        return {'kind': 'embed', 'src': f'https://www.youtube-nocookie.com/embed/{ident}', 'originalUrl': f'https://www.youtube.com/watch?v={ident}', 'title': title or 'YouTube 视频'}
    if host == 'player.vimeo.com' and re.fullmatch(r'/video/\d+', p.path):
        return {'kind': 'embed', 'src': f'https://player.vimeo.com{p.path}', 'originalUrl': f'https://vimeo.com/{p.path.split("/")[-1]}', 'title': title or 'Vimeo 视频'}
    if url and re.search(r'\.(mp4|webm|ogv|ogg)$', p.path, re.I):
        result = {'kind': 'video', 'src': url, 'originalUrl': url, 'title': title or '原文视频'}
        if safe_url(poster, base):
            result['poster'] = safe_url(poster, base)
        return result
    return None


def blocked(n):
    attrs = n.attrs
    if n.tag in {'', 'html', 'body', 'main'}:
        return False
    return (n.tag in {'nav', 'aside', 'footer', 'script', 'style', 'svg', 'noscript'}
            or bool(NOISE.search(' '.join(str(attrs.get(k) or '') for k in ('class', 'id', 'aria-labelledby')))))


def walk(n):
    if blocked(n):
        return
    yield n
    for child in n.children:
        yield from walk(child)


def extract_media_refs(value, base_url='', limit=12):
    parser = Document()
    parser.feed(value or '')
    nodes = list(walk(parser.root))
    explicit = [n for n in nodes if BODY_CLASSES.intersection((n.attrs.get('class') or '').split()) or n.attrs.get('itemprop') == 'articleBody']
    articles = [n for n in nodes if n.tag == 'article']
    mains = [n for n in nodes if n.tag == 'main' or n.attrs.get('role') == 'main']
    roots = explicit or articles[:1] or mains[:1]
    # GitHub Blog places the article's video facade in the main header, outside post__content.
    if urlparse(base_url).hostname == 'github.blog' and explicit:
        roots += [c for main in mains for c in main.children if c.tag == 'header']
    full_document = any(n.tag in {'html', 'head', 'body'} for n in nodes)
    if not roots:
        if full_document:
            raise ValueError('article media scope not found')
        roots = [parser.root]
    images, videos, seen_i, seen_v = [], [], set(), set()
    for root in roots:
        selected = list(walk(root))
        video_nodes = {}
        for n in selected:
            a = n.attrs
            if n.tag not in {'video', 'iframe'}:
                continue
            sources = [a.get('src') or a.get('data-src')]
            if n.tag == 'video':
                sources += [c.attrs.get('src') or c.attrs.get('data-src') for c in n.children if c.tag == 'source']
            for source in sources:
                ref = video_ref(source, base_url, a.get('title') or '', a.get('poster') or '') if source else None
                if ref:
                    video_nodes[id(n)] = ref
                    if ref['src'] not in seen_v:
                        videos.append(ref)
                        seen_v.add(ref['src'])
                    break
        # Facade thumbnails and native video posters are not independent article images.
        suppressed = set()
        posters = {safe_url(n.attrs.get('poster'), base_url) for n in selected if n.tag == 'video' and n.attrs.get('poster')}
        for n in selected:
            cls = n.attrs.get('class') or ''
            if n.tag == 'figure' or 'tease-thumbnail' in cls or 'video' in cls:
                descendants = list(walk(n))
                if any(id(c) in video_nodes for c in descendants):
                    suppressed.update(id(c) for c in descendants if c.tag in {'img', 'source'})
        for n in selected:
            a = n.attrs
            if id(n) in suppressed or n.tag != 'img':
                continue
            if any(k in (a.get('class') or '').lower() for k in ('logo', 'avatar', 'icon', 'tracking', 'pixel')):
                continue
            src = next((a.get(k) for k in ('data-src', 'data-original', 'data-lazy-src', 'data-original-src', 'data-url', 'src') if a.get(k) and not a[k].startswith(('data:', 'blob:'))), '')
            if not src:
                src = (a.get('srcset') or a.get('data-srcset') or '').split(',')[0].strip().split(' ')[0]
            url = safe_url(src, base_url) if src else ''
            if not url or url in posters or re.search(r'\.(mp4|webm|ogv|ogg)$', urlparse(url).path, re.I):
                continue
            # WordPress responsive variants represent the same asset.
            identity = url.split('?')[0] if '/wp-content/uploads/' in url else url
            if identity not in seen_i:
                images.append({'url': url, 'alt': re.sub(r'\s+', ' ', a.get('alt') or a.get('title') or '').strip()})
                seen_i.add(identity)
    # Metadata is a fragment-only fallback; never pull page-wide thumbnails/JSON contentUrl into body media.
    if not full_document and not videos and not explicit and not articles and not mains:
        for n in reversed(nodes):
            if n.tag == 'meta' and (n.attrs.get('property') or n.attrs.get('name')) in {'og:image', 'twitter:image'}:
                url = safe_url(n.attrs.get('content'), base_url)
                if url and url not in seen_i:
                    images.insert(0, {'url': url, 'alt': ''})
                    seen_i.add(url)
    return images[:limit], videos[:limit]
