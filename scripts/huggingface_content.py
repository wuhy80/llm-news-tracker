"""Safe, scoped HF blog extraction with stable prose IDs and explicit media accounting."""
import re
from collections import defaultdict
from urllib.parse import urlparse
from article_media import Document, Node, safe_url, video_ref

CONTENT_VERSION = 1
SKIP = {'script', 'style', 'svg', 'noscript', 'button', 'nav', 'aside', 'footer', 'form', 'template'}
BLOCK = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'blockquote', 'pre', 'table', 'figure', 'figcaption', 'div', 'section', 'ul', 'ol', 'details'}


def is_huggingface_blog(url):
    p = urlparse(url or '')
    return p.hostname in {'huggingface.co', 'www.huggingface.co'} and p.path.startswith('/blog/')


def nodes(root):
    yield root
    for child in root.children:
        yield from nodes(child)


def content_root(document):
    blogs = [n for n in nodes(document) if 'blog-content' in (n.attrs.get('class') or '').split()]
    roots = [n for b in blogs for n in nodes(b)
             if {'relative', 'overflow-clip'} <= set((n.attrs.get('class') or '').split())]
    if len(roots) != 1:
        raise ValueError('Hugging Face body scope not found uniquely')
    return roots[0]


def image_url(n, base):
    a = n.attrs
    src = next((a.get(k) for k in ('data-src', 'data-original', 'data-lazy-src', 'src')
                if a.get(k) and not a[k].startswith(('data:', 'blob:'))), '')
    if not src:
        src = (a.get('srcset') or a.get('data-srcset') or '').split(',')[0].strip().split(' ')[0]
    url = safe_url(src, base) if src else ''
    p = urlparse(url)
    # Body logos can be meaningful. Only known profile-image locations are excluded.
    if p.hostname == 'cdn-avatars.huggingface.co' or (p.hostname == 'huggingface.co' and p.path.startswith('/avatars/')):
        return ''
    return url


def extract(document, url):
    from media_layout import body_digest, media_id
    from translate_articles import article_blocks
    from article_store import redact_secrets
    p = Document(); p.feed(redact_secrets(document))
    root = content_root(p.root)
    pieces, annotations, images, videos, layout, codes = [], [], [], [], [], []
    image_map, video_map = {}, {}
    last_id = None
    image_occurrences = 0
    unsupported = []
    table_number = 0

    def inline(n):
        parts, spans = [], []
        size = 0
        def visit(x):
            nonlocal size
            if x.tag in SKIP or x.tag in {'img', 'video', 'iframe'}:
                return
            if x.tag == '#text':
                text = x.attrs.get('text', ''); parts.append(text); size += len(text); return
            start = size
            if x.tag == 'br':
                parts.append(' '); size += 1
            for child in x.children:
                visit(child)
            if size > start and x.tag in {'a', 'code', 'strong', 'b', 'em', 'i'}:
                span = {'start': start, 'end': size, 'kind': {'b': 'strong', 'i': 'em'}.get(x.tag, x.tag)}
                if x.tag == 'a':
                    href = safe_url(x.attrs.get('href'), url) if x.attrs.get('href') else ''
                    if not href:
                        return
                    span['href'] = href
                spans.append(span)
        visit(n)
        raw = ''.join(parts); text = re.sub(r'\s+', ' ', raw).strip()
        mapped = []
        for span in spans:
            label = re.sub(r'\s+', ' ', raw[span['start']:span['end']]).strip()
            start = len(re.sub(r'\s+', ' ', raw[:span['start']]).lstrip())
            while start < len(text) and text[start].isspace():
                start += 1
            if label and text[start:start+len(label)] == label:
                mapped.append({**span, 'start': start, 'end': start+len(label)})
        return text, mapped

    def add(n, prefix='', table=None):
        nonlocal last_id
        text, spans = inline(n)
        if not text and table is None:
            return
        # Empty table cells need a stable anchor too, but are not translation work.
        if not text:
            text = '\u00a0'
        last_id = f'b{len(annotations)+1:04d}'
        serialized = re.sub(r'^(?=[#*+>\-]|\d+[.)]\s)', r'\\', text) if not prefix else text
        pieces.append(prefix + serialized)
        annotations.append({'id': last_id, 'source': text, 'spans': spans,
                            **({'table': table} if table is not None else {})})

    def media(n):
        nonlocal image_occurrences
        if n.tag == 'img':
            src = image_url(n, url)
            if not src:
                # No silent successful accounting for an unsupported inline data image.
                if (n.attrs.get('src') or '').startswith(('data:', 'blob:')):
                    unsupported.append({'type': 'image', 'url': url, 'reason': 'inline_image'})
                return
            image_occurrences += 1
            if src not in image_map:
                value = {'src': src, 'originalUrl': src, 'alt': n.attrs.get('alt') or ''}
                value['id'] = media_id('image', value); images.append(value); image_map[src] = value
            resource, kind = image_map[src], 'image'
        else:
            sources = [n.attrs.get('src') or n.attrs.get('data-src')]
            sources += [c.attrs.get('src') for c in n.children if c.tag == 'source']
            resource = next((ref for src in sources if src and (ref := video_ref(src, url, n.attrs.get('title', ''), n.attrs.get('poster', '')))), None)
            if not resource:
                src = safe_url(n.attrs.get('src'), url) if n.attrs.get('src') else ''
                if src:
                    unsupported.append({'type': n.tag, 'url': src, 'reason': 'unsupported_embed'})
                return
            kind = 'video'; resource['id'] = media_id(kind, resource)
            if resource['id'] not in video_map:
                videos.append(resource); video_map[resource['id']] = resource
        layout.append({'mediaId': resource['id'], 'type': kind, 'afterBlockId': last_id, 'order': len(layout)})

    def nested_media(n):
        if n.tag in SKIP:
            return
        if n.tag in {'img', 'video', 'iframe'}:
            media(n); return
        for c in n.children:
            nested_media(c)

    def visit(n):
        nonlocal last_id, table_number
        if n.tag in SKIP:
            return
        if n.tag in {'img', 'video', 'iframe'}:
            media(n); return
        if n.tag == 'pre':
            def raw(x):
                if x.tag in SKIP:
                    return ''
                if x.tag == '#text':
                    return x.attrs.get('text', '')
                return ('\n' if x.tag == 'br' else '') + ''.join(raw(c) for c in x.children)
            code = raw(n).strip('\n')
            if code.strip():
                last_id = f'c{len(codes)+1:04d}'; codes.append({'id': last_id, 'source': code})
                pieces.append('```\n' + code.replace('```', '``\u200b`') + '\n```')
            return
        if n.tag == 'table':
            table_number += 1; ident = f't{table_number}'
            for row_id, row in enumerate(x for x in nodes(n) if x.tag == 'tr'):
                for col, cell in enumerate(c for c in row.children if c.tag in {'th', 'td'}):
                    text, _ = inline(cell)
                    # Preserve blank/image-only cells without making up prose for translation.
                    if not text:
                        text_node = Node('#text', [('text', '—')]); cell.children.insert(0, text_node)
                    def span(name):
                        try: return min(50, max(1, int(cell.attrs.get(name) or 1)))
                        except ValueError: return 1
                    add(cell, table={'id': ident, 'row': row_id, 'column': col, 'header': cell.tag == 'th',
                                     'colspan': span('colspan'), 'rowspan': span('rowspan')})
                    nested_media(cell)
            return
        if n.tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            add(n, '#' * int(n.tag[1]) + ' '); nested_media(n); return
        if n.tag in {'p', 'li', 'blockquote', 'figcaption', 'summary'} and not any(c.tag in BLOCK for c in n.children):
            add(n, '- ' if n.tag == 'li' else '> ' if n.tag == 'blockquote' else '')
            nested_media(n); return
        if n.tag == '#text':
            if n.attrs.get('text', '').strip(): add(n)
            return
        if not any(c.tag in BLOCK for c in n.children) and n.tag not in {'ul', 'ol'}:
            add(n); nested_media(n); return
        pending = []
        def flush():
            if pending:
                para = Node('p'); para.children = list(pending); visit(para); pending.clear()
        for child in n.children:
            if child.tag in BLOCK or child.tag in {'img', 'video', 'iframe'}:
                flush(); visit(child)
            else:
                pending.append(child)
        flush()

    visit(root)
    body = '\n\n'.join(pieces)
    if not body.strip() or len(body) > 300_000:
        raise ValueError('Hugging Face body empty or exceeds archive bound')
    actual = article_blocks(body)
    if [(x['id'], x['source']) for x in actual] != [(x['id'], x['source']) for x in annotations]:
        raise ValueError('Hugging Face prose boundaries do not match translation parser')
    digest = body_digest(body)
    return {'body': body, 'images': images, 'videos': videos, 'hfContentVersion': CONTENT_VERSION,
            'inlineContent': annotations, 'inlineContentBodyHash': digest, 'codeContent': codes,
            'mediaLayoutVersion': 1, 'mediaLayoutBodyHash': digest, 'mediaLayout': layout,
            'mediaLayoutUnplaced': [], 'mediaLayoutMatched': len(layout), 'mediaLayoutStatus': 'complete',
            'contentIntegrity': {'sourceImages': len(images), 'savedImages': len(images),
                'sourceImageOccurrences': image_occurrences, 'placedImageOccurrences': sum(e['type']=='image' for e in layout),
                'sourceVideos': len(videos), 'savedVideos': len(videos), 'bodyTruncated': False,
                'unsupportedEmbeds': unsupported, 'status': 'partial' if unsupported else 'complete'}}


def remap_translation(record, old_body, new_body):
    from translate_articles import article_blocks, body_hash, translatable_blocks, TRANSLATION_VERSION
    if record.get('sourceBodyHash') != body_hash(old_body) or record.get('translationVersion') != TRANSLATION_VERSION:
        raise ValueError('translation does not match previous archived source')
    old = {b['id']: b for b in article_blocks(old_body)}; candidates = defaultdict(list)
    def key(text): return re.sub(r'\s+', '', text)
    for translated in record.get('blocks', []):
        source = old.get(translated.get('id'))
        if source and translated.get('sourceHash') == source['sourceHash'] and translated.get('translationZh'):
            candidates[key(source['source'])].append(translated)
    blocks = translatable_blocks(article_blocks(new_body)); kept = []
    for block in blocks:
        matches = candidates.get(key(block['source']), [])
        if len(matches) == 1:
            kept.append({**matches[0], 'id': block['id'], 'kind': block['kind'], 'sourceHash': block['sourceHash']})
    result = {**record, 'sourceBodyHash': body_hash(new_body), 'blocks': kept,
              'translatedBlocks': len(kept), 'totalBlocks': len(blocks),
              'status': 'complete' if len(kept) == len(blocks) else 'partial'}
    for field in ('blockFailures', 'nextAttemptAt', 'lastError'):
        result.pop(field, None)
    if result['status'] != 'complete': result.pop('completedAt', None)
    result['wordWise'] = [w for w in record.get('wordWise', []) if w.get('term', '').lower() in new_body.lower()]
    return result
