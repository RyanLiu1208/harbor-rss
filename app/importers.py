import csv
import io
import json
from urllib.parse import urlsplit
from defusedxml import ElementTree as ET

def feed_url(value):
    if not isinstance(value, str):
        raise ValueError('订阅地址必须是字符串')
    value = value.strip()
    p = urlsplit(value)
    if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
        raise ValueError('订阅地址必须是 HTTP / HTTPS URL')
    return value

def parse_import(content):
    """OPML, JSON arrays/objects, CSV, and line-separated URLs. No guessing RSSHub routes."""
    content = content.lstrip('\ufeff').strip()
    rows = []
    def add(url, title='', folder=''):
        rows.append({'url': feed_url(url), 'title': str(title or '')[:300], 'folder': str(folder or '')[:300]})
    if content.startswith('<'):
        root = ET.fromstring(content)
        if root.tag.split('}')[-1].lower() != 'opml':
            raise ValueError('请导入 OPML 订阅列表；RSS / Atom 地址请通过添加订阅使用')
        def walk(node, path=''):
            for child in node:
                title = child.get('title') or child.get('text') or ''
                url = child.get('xmlUrl') or child.get('xmlurl')
                if url:
                    add(url, title, path)
                walk(child, '/'.join(filter(None, [path, title])) if child.tag.endswith('outline') and not url else path)
        walk(root)
    elif content.startswith(('[', '{')):
        data = json.loads(content)
        if isinstance(data, dict):
            data = data.get('feeds', data.get('subscriptions'))
        if not isinstance(data, list):
            raise ValueError('JSON 应为数组，或包含 feeds / subscriptions 数组')
        for row in data:
            if isinstance(row, str):
                add(row)
            elif isinstance(row, dict):
                feed = row.get('feed') if isinstance(row.get('feed'), dict) else row
                add(feed.get('url') or feed.get('xmlUrl') or feed.get('feedUrl'), row.get('title') or feed.get('title'), row.get('folder') or row.get('category'))
            else:
                raise ValueError('无法识别的 JSON 订阅')
    elif ',' in content.splitlines()[0] and not content.startswith('http'):
        for row in csv.DictReader(io.StringIO(content)):
            add(row.get('url') or row.get('xmlUrl') or row.get('feedUrl'), row.get('title'), row.get('folder'))
    else:
        for line in content.splitlines():
            if line.strip() and not line.lstrip().startswith('#'):
                add(line)
    if not rows:
        raise ValueError('没有找到可导入的订阅')
    if len(rows) > 5000:
        raise ValueError('单次最多导入 5000 个订阅')
    return list({row['url']: row for row in rows}.values())
