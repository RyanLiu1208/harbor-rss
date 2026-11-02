import json
import socket
import threading
from pathlib import Path
import pytest
import httpx
from fastapi.testclient import TestClient
from app import main as m
from app.importers import parse_import
from app.network import public_target, download
REAL_REFRESH_ONE = m.refresh_one

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, 'DATA', tmp_path)
    monkeypatch.setattr(m, 'TOKEN', 'test-only-token-with-at-least-32-characters')
    monkeypatch.setattr(m, 'refresh_worker', lambda: m.stop.wait())
    monkeypatch.setattr(m, 'translation_worker', lambda: m.stop.wait())
    monkeypatch.setattr(m, 'refresh_all', lambda: None)
    monkeypatch.setattr(m, 'refresh_one', lambda fid: None)
    m.login_attempts.clear()
    with TestClient(m.app) as c:
        assert c.get('/api/feeds').status_code == 401
        assert c.post('/api/login', json={'token':m.TOKEN}).status_code == 200
        yield c

OPML = '''<opml version="2.0"><body><outline text="科技"><outline text="AI"><outline text="Example" xmlUrl="https://example.com/rss"/></outline></outline></body></opml>'''

@pytest.mark.parametrize('content', [OPML, '[{"url":"https://example.com/rss","title":"Example","folder":"科技/AI"}]', 'url,title,folder\nhttps://example.com/rss,Example,科技/AI', 'https://example.com/rss'])
def test_formats(content):
    result = parse_import(content)
    assert result[0]['url'] == 'https://example.com/rss'
    if content != 'https://example.com/rss': assert result[0]['folder'] == '科技/AI'

def test_import_roundtrip_and_invalid_atomic(client):
    assert client.post('/api/import', json={'content':OPML}).json()['imported'] == 1
    assert client.post('/api/import', json={'content':OPML}).json()['duplicates'] == 1
    exported = client.get('/api/export').text
    assert parse_import(exported)[0]['folder'] == '科技/AI'
    assert client.post('/api/import', json={'content':'https://example.org/feed\nfile:///etc/passwd'}).status_code == 400
    assert len(client.get('/api/feeds').json()) == 1

def test_unsafe_xml():
    with pytest.raises(Exception): parse_import('<!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><opml><body>&xxe;</body></opml>')

@pytest.mark.parametrize('ip', ['127.0.0.1','192.168.1.1','169.254.169.254','::1','::ffff:127.0.0.1','100.64.0.1'])
def test_private_network_blocked(monkeypatch, ip):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(2,1,6,'',(ip,80))])
    with pytest.raises(ValueError): public_target('https://example.com')

def fake_translation(segments,prefix='译文'):
    output=json.dumps({'translations':[{'id':s['id'],'text':prefix+s['text']} for s in segments]},ensure_ascii=False)
    return json.dumps({'choices':[{'finish_reason':'stop','message':{'content':output}}]}).encode()

def insert_article(text='<p>Hello, world.</p>'):
    with m.db() as db:
        db.execute("INSERT INTO feeds(url,title) VALUES('https://example.com/rss','Example')")
        db.execute("INSERT INTO articles(feed_id,guid,title,url,content,published) VALUES(1,'a','Hello','https://example.com/a',?,1700000000)",(text,))

def test_read_star_search_delete(client):
    insert_article()
    assert client.get('/api/articles?q=Hello').json()['total'] == 1
    assert client.patch('/api/articles/1',json={'read':True,'starred':True}).status_code == 200
    assert client.get('/api/articles?view=unread').json()['total'] == 0
    assert client.get('/api/articles?view=starred').json()['total'] == 1
    assert client.delete('/api/feeds/1').status_code == 200
    assert client.get('/api/articles/1').status_code == 404

def test_translation_all_chunks_cached_and_encrypted(client, monkeypatch):
    source = ('This is a long article.\n' * 700) + 'THE END'
    insert_article('<p>' + source + '</p>')
    assert client.put('/api/settings',json={'base_url':'https://api.deepseek.com','model':'deepseek-flash','api_key':'test-key-not-real','language':'简体中文'}).status_code == 200
    assert 'test-key-not-real' not in client.get('/api/settings').text
    with m.db() as db: assert 'test-key-not-real' not in db.execute("SELECT value FROM settings WHERE key='api_key'").fetchone()[0]
    sent = []
    def mock_api(url, **kw):
        assert url == 'https://api.deepseek.com/chat/completions'
        assert kw['headers']['Authorization'] == 'Bearer test-key-not-real'
        segments=json.loads(kw['json']['messages'][-1]['content'])['segments']; sent.extend(s['text'] for s in segments)
        return fake_translation(segments, '译文'), url, 'application/json'
    monkeypatch.setattr(m, 'download', mock_api)
    jid = client.post('/api/articles/1/translate',json={'use_feed_content':True}).json()['job_id']
    m.translate_job(jid)
    result = client.get('/api/jobs/' + jid).json()
    assert result['status'] == 'done' and result['completed'] > 1
    assert ''.join(sent) == m.plaintext('<p>' + source + '</p>')
    assert 'THE END' in result['translation']
    assert client.post('/api/articles/1/translate',json={'use_feed_content':True}).json()['job_id'] == jid

def test_retry_continues_from_checkpoint(client, monkeypatch):
    insert_article('<p>' + 'x' * 11000 + '</p>')
    monkeypatch.setenv('DEEPSEEK_API_KEY','fake-key')
    jid = client.post('/api/articles/1/translate',json={'use_feed_content':True}).json()['job_id']
    calls = []
    def mocked(url, **kwargs):
        calls.append(kwargs['json']['messages'][-1]['content'])
        if len(calls) > 1: raise ValueError('simulated failure')
        return fake_translation(json.loads(kwargs['json']['messages'][-1]['content'])['segments'],'first'),url,''
    monkeypatch.setattr(m,'download',mocked)
    monkeypatch.setattr(m.stop,'wait',lambda *a: False)
    m.translate_job(jid)
    assert client.get('/api/jobs/'+jid).json()['completed'] == 1
    assert client.get('/api/jobs/'+jid).json()['status'] == 'failed'
    monkeypatch.setattr(m,'download',lambda url,**kw: (fake_translation(json.loads(kw['json']['messages'][-1]['content'])['segments'],'next'),url,''))
    assert client.post('/api/jobs/'+jid+'/retry').status_code == 200
    m.translate_job(jid)
    result = client.get('/api/jobs/'+jid).json()
    assert result['status'] == 'done' and result['translation'].startswith('first')

def test_auth_csrf_xss(client):
    assert client.post('/api/refresh',headers={'Origin':'https://evil.example'}).status_code == 403
    clean = m.safe_html('<script>alert(1)</script><a href="javascript:alert(1)" onclick="bad()">click</a><img src="x" onerror="bad()">')
    assert 'script' not in clean and 'onclick' not in clean and 'onerror' not in clean
    client.cookies.clear()
    assert client.get('/api/settings').status_code == 401
    assert client.get('/api/settings',headers={'Authorization':'Bearer '+m.TOKEN}).status_code == 200

def test_split_lossless():
    text = '开头\n' + 'a' * 16000 + '\n结尾'
    chunks = m.split_text(text)
    assert ''.join(chunks) == text and max(map(len,chunks)) <= 5000

@pytest.mark.parametrize('payload,expected', [
    (b'<rss version="2.0"><channel><title>RSS</title><item><guid>x</guid><title>Entry</title><link>https://example.com/a</link><description>&lt;p&gt;Body&lt;/p&gt;</description></item></channel></rss>', 'Body'),
    (b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom</title><entry><id>x</id><title>Entry</title><link href="https://example.com/a"/><content type="html">&lt;p&gt;Atom body&lt;/p&gt;</content></entry></feed>', 'Atom body'),
    (b'{"version":"https://jsonfeed.org/version/1.1","title":"JSON","items":[{"id":"x","title":"Entry","url":"https://example.com/a","content_html":"<p>JSON body</p>"}]}', 'JSON body')
])
def test_real_feed_pipeline(client, monkeypatch, payload, expected):
    client.post('/api/feeds',json={'url':'https://example.com/rss'})
    monkeypatch.setattr(m,'download',lambda url,**kw: (payload,url,''))
    REAL_REFRESH_ONE(1)
    a=client.get('/api/articles/1').json()
    assert expected in a['content']
    client.patch('/api/articles/1',json={'read':True,'starred':True})
    REAL_REFRESH_ONE(1)
    a=client.get('/api/articles/1').json()
    assert a['read'] and a['starred']
    assert client.get('/api/articles').json()['total']==1

def test_full_translation_requires_extract(client, monkeypatch):
    insert_article('<p>Only excerpt.</p>')
    monkeypatch.setenv('DEEPSEEK_API_KEY','fake-key')
    monkeypatch.setattr(m,'download',lambda url,**kw: (b'<html><p>short</p></html>',url,''))
    response=client.post('/api/articles/1/translate',json={})
    assert response.status_code==400 and '无法确认网页全文' in response.json()['detail']
    assert client.post('/api/articles/1/translate',json={'use_feed_content':True}).status_code==202

def test_truncated_ai_output_not_cached_as_success(client, monkeypatch):
    insert_article()
    monkeypatch.setenv('DEEPSEEK_API_KEY','fake-key')
    monkeypatch.setattr(m.stop,'wait',lambda *a: False)
    monkeypatch.setattr(m,'download',lambda url,**kw: (b'{"choices":[{"finish_reason":"length","message":{"content":"incomplete"}}]}',url,''))
    jid=client.post('/api/articles/1/translate',json={'use_feed_content':True}).json()['job_id']
    m.translate_job(jid)
    result=client.get('/api/jobs/'+jid).json()
    assert result['status']=='failed' and result['completed']==0

def test_restart_recovers_running_job(client, monkeypatch):
    insert_article()
    monkeypatch.setenv('DEEPSEEK_API_KEY','fake-key')
    jid=client.post('/api/articles/1/translate',json={'use_feed_content':True}).json()['job_id']
    with m.db() as db: db.execute("UPDATE jobs SET status='running' WHERE id=?",(jid,))
    m.init_db()
    assert client.get('/api/jobs/'+jid).json()['status']=='queued'

def mock_transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(httpx,'Client',lambda **kwargs: original(transport=httpx.MockTransport(handler),**kwargs))

def test_download_pins_dns_and_retries_unreachable_ip(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw: [(2,1,6,'',('93.184.216.34',443)),(2,1,6,'',('8.8.8.8',443))])
    seen=[]
    def handler(request):
        seen.append(request.url.host)
        assert request.headers['host']=='example.com'
        assert request.extensions['sni_hostname']=='example.com'
        if len(seen)==1: raise httpx.ConnectError('unreachable',request=request)
        return httpx.Response(200,content=b'feed')
    mock_transport(monkeypatch,handler)
    assert download('https://example.com/rss')[0]==b'feed'
    assert seen==['93.184.216.34','8.8.8.8']

def test_download_redirect_to_private_blocked(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda host,*a,**kw: [(2,1,6,'',('127.0.0.1' if host=='localhost' else '93.184.216.34',443))])
    mock_transport(monkeypatch,lambda request: httpx.Response(302,headers={'Location':'https://localhost/secret'}))
    with pytest.raises(ValueError,match='禁止访问'): download('https://example.com/rss')

def test_download_size_bound(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw: [(2,1,6,'',('93.184.216.34',443))])
    mock_transport(monkeypatch,lambda request: httpx.Response(200,content=b'x'*100))
    with pytest.raises(ValueError,match='大小限制'): download('https://example.com/rss',limit=10)

def test_api_redirect_never_forwards_key(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**kw: [(2,1,6,'',('93.184.216.34',443))])
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(307,headers={'Location':'https://other.example/key'})
    mock_transport(monkeypatch,handler)
    with pytest.raises(ValueError,match='不允许重定向'): download('https://example.com/chat/completions',method='POST',headers={'Authorization':'Bearer fake-key'},json={})
    assert len(calls)==1

def test_changed_provider_never_receives_new_key(client, monkeypatch):
    insert_article()
    monkeypatch.setenv('DEEPSEEK_API_KEY','fake-key')
    jid=client.post('/api/articles/1/translate',json={'use_feed_content':True}).json()['job_id']
    client.put('/api/settings',json={'base_url':'https://new-provider.example/v1','model':'other','api_key':'new-fake-key'})
    def forbidden(*args,**kw): raise AssertionError('No API request should be made')
    monkeypatch.setattr(m,'download',forbidden)
    m.translate_job(jid)
    result=client.get('/api/jobs/'+jid).json()
    assert result['status']=='failed' and 'API 地址已变更' in result['error']

@pytest.mark.parametrize('encoding',['utf-8','gb18030'])
def test_extract_decodes_real_html_bytes(client, monkeypatch, encoding):
    insert_article('<p>Only excerpt.</p>')
    paragraph='这是一篇完整的中文文章，用来验证网页正文的编码转换与提取。' * 20
    source=f'<html><head><meta charset="{encoding}"><title>中文测试</title></head><body><article><h1>中文测试</h1><p>{paragraph}</p><p>{paragraph}正文结尾。</p></article><script>alert(1)</script></body></html>'
    monkeypatch.setattr(m,'download',lambda url,**kw:(source.encode(encoding),url,'text/html'))
    result=client.post('/api/articles/1/extract')
    assert result.status_code==200, result.text
    assert '正文结尾' in result.json()['content']
    assert '<script' not in result.json()['content']
    assert '正文结尾' in client.get('/api/articles/1').json()['full_content']
    monkeypatch.setenv('DEEPSEEK_API_KEY','fake-key')
    def no_fetch(*a,**kw): raise AssertionError('Cached full text should be used')
    monkeypatch.setattr(m,'download',no_fetch)
    assert client.post('/api/articles/1/translate',json={}).status_code==202
