"""Map source media to stable archived blocks without rewriting archived text."""
import hashlib
import html
import re
from urllib.parse import urljoin, urlparse
from article_media import media_roots, blocked, VOID, video_ref

LAYOUT_VERSION = 1
MARKER = re.compile(r'LLMMEDIAPOSITIONTOKEN(\d{8})ENDTOKEN')


def body_digest(body):
    return hashlib.sha256((body or '').encode('utf-8')).hexdigest()


def canonical(url):
    value = html.unescape(url or '')
    return value.split('?')[0] if '/wp-content/uploads/' in value else value


def media_id(kind, resource):
    url = resource.get('originalUrl') or resource.get('url') or resource.get('src') or ''
    return kind + '_' + hashlib.sha256(canonical(url).encode()).hexdigest()[:16]


def layout_blocks(body):
    # Text ids are owned by the translation parser. Codes have a separate id space.
    from translate_articles import article_blocks
    from article_store import normalize_fenced_body
    text_count = code_count = 0
    result = []
    parts = re.split(r'(```[^\n]*\n[\s\S]*?(?:\n```|$))', normalize_fenced_body(body or ''))
    for part in parts:
        if part.startswith('```'):
            code_count += 1
            result.append({'id': f'c{code_count:04d}', 'source': part, 'kind': 'code'})
        else:
            for block in article_blocks(part):
                text_count += 1
                result.append({**block, 'id': f'b{text_count:04d}'})
    return result


def normalized(value):
    return re.sub(r'\s+', ' ', html.unescape(value or '')).strip()


def source_events(source, source_format, base_url, resources):
    from article_store import text_from_html, text_from_reader
    lookup = {}
    for kind, resource in resources:
        for key in ('url', 'src', 'originalUrl'):
            if resource.get(key):
                lookup[canonical(resource[key])] = (kind, media_id(kind, resource))
    occurrences = []
    cover = False
    def marker(url):
        found = lookup.get(canonical(url))
        if not found:
            return None
        index = len(occurrences)
        occurrences.append((*found, cover))
        return f'LLMMEDIAPOSITIONTOKEN{index:08d}ENDTOKEN'
    if source_format == 'markdown':
        def replace(match):
            token = marker(urljoin(base_url, html.unescape(match.group(2))))
            return '\n\n' + token + '\n\n' if token else match.group(0)
        # Never interpret image-like strings inside fenced code as actual media.
        parts = re.split(r'(```[\s\S]*?(?:```|$))', source or '')
        marked = ''.join(p if p.startswith('```') else re.sub(r'!\[([^]]*)\]\((\S+?)(?:\s+[\'"][^)]*[\'"])?\)', replace, p) for p in parts)
        text = text_from_reader(marked)
    else:
        roots, _ = media_roots(source, base_url)
        def render(node):
            if blocked(node):
                return ''
            if node.tag == '#text':
                return html.escape(node.attrs.get('text', ''))
            a = node.attrs
            if node.tag == 'img':
                src = next((a.get(k) for k in ('data-src', 'data-original', 'data-lazy-src', 'data-original-src', 'data-url', 'src') if a.get(k) and not a[k].startswith(('data:', 'blob:'))), '')
                if not src:
                    src = (a.get('srcset') or a.get('data-srcset') or '').split(',')[0].strip().split(' ')[0]
                token = marker(urljoin(base_url, src)) if src else None
                return f'<p>{token}</p>' if token else ''
            if node.tag in {'iframe', 'video'}:
                sources = [a.get('src') or a.get('data-src')]
                sources += [c.attrs.get('src') for c in node.children if c.tag == 'source']
                for url in sources:
                    ref = video_ref(url, base_url) if url else None
                    token = marker(ref['src']) if ref else None
                    if token:
                        return f'<p>{token}</p>'
                return ''
            children = ''.join(render(c) for c in node.children)
            if node.tag in {'header', 'template'}:
                return children  # Only within the already scoped article/cover roots.
            if node.tag == 'pre':
                return '<pre>' + children + '</pre>'
            if node.tag in VOID:
                return '<' + node.tag + '>'
            return '<' + (node.tag or 'div') + '>' + children + '</' + (node.tag or 'div') + '>'
        fragments = []
        for root in roots:
            cover = root.tag == 'header' and 'github.blog' == urlparse(base_url).hostname
            fragments.append(render(root))
        text = text_from_html(''.join(fragments))
    events = []
    for block in layout_blocks(text):
        match = MARKER.fullmatch(block['source'])
        if match:
            index = int(match.group(1))
            if index < len(occurrences):
                kind, ident, is_cover = occurrences[index]
                events.append({'mediaId': ident, 'type': kind, **({'cover': True} if is_cover else {})})
        else:
            events.append({'text': block['source']})
    return events


def build_layout(body, images, videos, source, source_format='html', base_url=''):
    resources = [('image', x) for x in images or []] + [('video', x) for x in videos or []]
    for kind, resource in resources:
        resource['id'] = media_id(kind, resource)
    events = source_events(source, source_format, base_url, resources)
    blocks = layout_blocks(body)
    positions = {}
    for i, block in enumerate(blocks):
        positions.setdefault(normalized(block['source']), []).append(i)
    def candidates(event):
        return positions.get(normalized(event.get('text', '')), [])
    layout = []
    unplaced = []
    for index, event in enumerate(events):
        if 'mediaId' not in event:
            continue
        # Nearest surviving text on either side; no fuzzy matching or even spacing.
        before = next((e for e in reversed(events[:index]) if 'text' in e), None)
        after = next((e for e in events[index+1:] if 'text' in e), None)
        left = candidates(before) if before else []
        right = candidates(after) if after else []
        anchor = 'unmatched'
        if event.get('cover'):
            anchor = None
        elif before is None and len(right) == 1 and right[0] == 0:
            anchor = None
        elif after is None and len(left) == 1 and left[0] == len(blocks) - 1:
            anchor = blocks[left[0]]['id']
        elif left and right:
            pairs = [(l, r) for l in left for r in right if r == l + 1]
            if len(pairs) == 1:
                anchor = blocks[pairs[0][0]]['id']
        if anchor == 'unmatched':
            unplaced.append({**event, 'reason': 'ambiguous_or_changed_context', 'order': index})
        else:
            layout.append({**event, 'afterBlockId': anchor, 'order': index})
    mentioned = {e['mediaId'] for e in events if 'mediaId' in e}
    for kind, resource in resources:
        if resource['id'] not in mentioned:
            unplaced.append({'mediaId': resource['id'], 'type': kind, 'reason': 'source_position_unavailable'})
    return {'mediaLayoutVersion': LAYOUT_VERSION, 'mediaLayoutBodyHash': body_digest(body),
            'mediaLayout': layout, 'mediaLayoutUnplaced': unplaced,
            'mediaLayoutMatched': len(layout),
            'mediaLayoutStatus': 'complete' if not unplaced else 'partial' if layout else 'unmatched'}


def layout_current(snapshot):
    return (snapshot.get('mediaLayoutVersion') == LAYOUT_VERSION
            and snapshot.get('mediaLayoutBodyHash') == body_digest(snapshot.get('body', ''))
            and snapshot.get('mediaLayoutStatus') == 'complete')
