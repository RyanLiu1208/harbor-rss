import json
from urllib.parse import quote
from bs4 import BeautifulSoup
import pytest
from app import main as m
from app.content import safe_html,entry_link,extract_body,build_translation,parse_translation,render_translation
from test_app import client,insert_article,fake_translation,REAL_REFRESH_ONE

def test_lazy_images_srcset_picture_and_relative_links():
    html='''<p>Hello</p><img src="data:image/gif;base64,eA==" data-original="/real.jpg"><picture><source srcset="/small.webp 400w, /large.webp 1200w"><img src=""></picture><a href="../article">Read</a>'''
    clean=safe_html(html,'https://example.com/posts/a')
    soup=BeautifulSoup(clean,'html.parser')
    assert [i['src'] for i in soup.find_all('img')]==['https://example.com/real.jpg','https://example.com/large.webp']
    assert soup.a['href']=='https://example.com/article'
    assert 'data-original' not in clean and 'data:image' not in clean

def test_article_link_prefers_html_and_never_guesses_from_arbitrary_guid():
    entry={'link':'https://example.com/feed','id':'urn:entry:123','links':[{'rel':'self','type':'application/atom+xml','href':'/feed'},{'rel':'alternate','type':'text/html','href':'/posts/123'}]}
    assert entry_link(entry,'https://example.com/feed')=='https://example.com/posts/123'
    assert entry_link({'id':'random-guid'},'https://example.com/feed')==''
    assert entry_link({'id':'https://example.com/post'},'https://example.com/feed')=='https://example.com/post'

def test_readability_preserves_lazy_images():
    html='<html><body><article><p>'+('An article with useful content. '*30)+'</p><figure><img data-src="/photo.png"><figcaption>Photo caption</figcaption></figure><pre><code>print("hello")</code></pre></article></body></html>'
    body=extract_body(html.encode(),'https://example.com/post')
    assert 'https://example.com/photo.png' in body
    assert 'print("hello")' in BeautifulSoup(body,'html.parser').pre.get_text()

def test_explicit_body_excludes_author_cards_and_duplicate_titles():
    html='<article><header>'+('Author profile '*100)+'<img src="/avatar.jpg"></header><div class="article-body"><p>'+('Real content. '*80)+'</p><img src="/body.jpg"></div></article>'
    result=extract_body(html.encode(),'https://example.com/a')
    assert 'Real content' in result and '/body.jpg' in result
    assert 'Author profile' not in result and '/avatar.jpg' not in result

def test_structured_translation_omits_code_and_preserves_media_and_links():
    source='<h2>Title</h2><p>Hello <code>user_id</code> world <a href="https://example.org">link</a>.</p><figure><img src="https://example.com/a.png"><figcaption>Caption</figcaption></figure><pre><code>if a &lt; 2:\n    print("keep me")</code></pre>'
    template,chunks=build_translation(source)
    sent=''.join(s['text'] for chunk in chunks for s in chunk)
    assert 'user_id' not in sent and 'print(' not in sent and 'Caption' in sent
    results=[{s['id']:'译'+s['text'] for s in chunk} for chunk in chunks]
    html=render_translation(template,results);soup=BeautifulSoup(html,'html.parser')
    assert soup.img['src']=='https://example.com/a.png'
    assert soup.a['href']=='https://example.org'
    assert soup.pre.get_text()=='if a < 2:\n    print("keep me")'
    assert soup.find('code').get_text()=='user_id'
    assert soup.figcaption.get_text().startswith('译')

def test_ai_cannot_inject_markup_or_omit_segments():
    template,chunks=build_translation('<p>Hello</p><p>World</p>')
    with pytest.raises(ValueError):parse_translation('{"translations":[{"id":"s0","text":"only one"}]}',chunks[0])
    result=parse_translation(json.dumps({'translations':[{'id':s['id'],'text':'<script>alert(1)</script>'} for s in chunks[0]]}),chunks[0])
    html=render_translation(template,[result])
    assert not BeautifulSoup(html,'html.parser').find('script') and '&lt;script&gt;' in html

def test_full_rich_translation_api_and_legacy_compatibility(client,monkeypatch):
    source='<p>Hello, world.</p><img src="https://example.com/p.png"><pre><code>const x = 1;</code></pre>'
    insert_article(source);monkeypatch.setenv('DEEPSEEK_API_KEY','fake-key')
    sent=[]
    def fake(url,**kwargs):
        segments=json.loads(kwargs['json']['messages'][-1]['content'])['segments'];sent.extend(segments)
        return fake_translation(segments),url,''
    monkeypatch.setattr(m,'download',fake)
    jid=client.post('/api/articles/1/translate',json={'use_feed_content':True}).json()['job_id'];m.translate_job(jid)
    result=client.get('/api/jobs/'+jid).json()
    assert result['status']=='done' and result['format']=='html'
    assert '/api/articles/1/image?' in result['translation_html'] and '<pre>' in result['translation_html']
    assert 'const x' not in str(sent)
    # Existing jobs keep their old source/result protocol after an upgrade.
    with m.db() as db:
        db.execute("INSERT INTO jobs(id,article_id,cache_key,language,model,base_url,chunks,results,status,created_at) VALUES('legacy',1,'old','zh','test','https://example.com','[\"old source\"]','[\"old result\"]','done',1)")
    assert client.get('/api/jobs/legacy').json()['translation']=='old result'
    assert client.get('/api/jobs/legacy').json()['format']=='text'

def test_only_code_requires_no_translation_calls(client,monkeypatch):
    insert_article('<pre><code>print("unchanged")</code></pre>');monkeypatch.setenv('DEEPSEEK_API_KEY','fake-key')
    monkeypatch.setattr(m,'download',lambda *a,**kw:pytest.fail('Code should never be sent'))
    jid=client.post('/api/articles/1/translate',json={'use_feed_content':True}).json()['job_id'];m.translate_job(jid)
    result=client.get('/api/jobs/'+jid).json()
    assert result['status']=='done' and result['total']==0
    assert 'print("unchanged")' in BeautifulSoup(result['translation_html'],'html.parser').get_text()

def test_image_proxy_auth_membership_cache_and_svg_rejection(client,monkeypatch):
    insert_article('<p>Text</p><img src="https://cdn.example.com/p.png">')
    calls=[]
    def image(url,**kwargs):
        calls.append(url);assert kwargs['headers']['Referer']=='https://example.com/a'
        return b'\x89PNG\r\n\x1a\n'+b'fake-test-image',url,'image/png'
    monkeypatch.setattr(m,'download',image)
    route='/api/articles/1/image?url='+quote('https://cdn.example.com/p.png',safe='')
    assert client.get(route).headers['content-type']=='image/png'
    assert client.get(route).status_code==200 and len(calls)==1
    assert client.get('/api/articles/1/image?url=https://evil.example/other.png').status_code==403
    client.cookies.clear();assert client.get(route).status_code==401

def test_image_proxy_rejects_non_raster(client,monkeypatch):
    insert_article('<img src="https://example.com/a.svg">')
    monkeypatch.setattr(m,'download',lambda url,**kw:(b'<svg onload="alert(1)"></svg>',url,'image/svg+xml'))
    assert client.get('/api/articles/1/image?url=https://example.com/a.svg').status_code==400

def test_extraction_falls_back_to_existing_substantial_feed(client,monkeypatch):
    insert_article('<p>'+'Full feed article. '*80+'</p><img src="https://example.com/p.jpg">')
    monkeypatch.setattr(m,'download',lambda *a,**kw:(_ for _ in ()).throw(ValueError('unavailable')))
    response=client.post('/api/articles/1/extract').json()
    assert response['source']=='feed' and response['warning'] and '/image?' in response['content']
    assert client.get('/api/articles/1').json()['full_content']==''

def test_discover_webpage_feed_and_media_enclosure(client,monkeypatch):
    client.post('/api/feeds',json={'url':'https://example.com/'})
    page=b'<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml"></head></html>'
    rss=b'<rss version="2.0"><channel><title>RSS</title><item><guid>x</guid><title>Entry</title><link>https://example.com/a</link><description>Text</description><enclosure url="https://example.com/p.jpg" type="image/jpeg"/></item></channel></rss>'
    monkeypatch.setattr(m,'download',lambda url,**kw:(rss if url.endswith('/feed.xml') else page,url,''))
    REAL_REFRESH_ONE(1)
    assert '/api/articles/1/image?' in client.get('/api/articles/1').json()['content']

def test_edit_feed_preserves_read_state_and_rejects_duplicates(client):
    insert_article();client.patch('/api/articles/1',json={'read':True,'starred':True})
    assert client.patch('/api/feeds/1',json={'url':'https://example.org/new.xml','title':'New','folder':'Folder'}).status_code==200
    a=client.get('/api/articles/1').json();assert a['starred'] and a['read']
    client.post('/api/feeds',json={'url':'https://example.org/duplicate'})
    assert client.patch('/api/feeds/1',json={'url':'https://example.org/duplicate'}).status_code==409
