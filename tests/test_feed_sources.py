import json
import socket
import httpx
import pytest
from urllib.parse import quote
from app.feed_sources import fetch_feed
from app.network import public_target,download
from test_app import client,REAL_REFRESH_ONE,mock_transport
from app import main as m

def test_rewrite_only_official_hub_preserves_route_and_query(monkeypatch):
    monkeypatch.setenv('RSSHUB_BASE_URL','http://rsshub:1200')
    monkeypatch.setenv('RSSHUB_INTERNAL_URL','http://rsshub:1200')
    calls=[]
    def fake(url,**kwargs):calls.append((url,kwargs));return b'feed',url,''
    fetch_feed('https://rsshub.app/eastmoney/report/industry?limit=10',fake)
    assert calls[0][0]=='http://rsshub:1200/eastmoney/report/industry?limit=10'
    assert calls[0][1]['trusted_origin']=='http://rsshub:1200'
    fetch_feed('https://example.org/feed',fake)
    assert calls[1][0]=='https://example.org/feed' and 'trusted_origin' not in calls[1][1]

def test_configured_private_origin_only_initial_request(monkeypatch):
    monkeypatch.setenv('RSSHUB_INTERNAL_URL','http://rsshub:1200')
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('172.19.0.3',1200))])
    with pytest.raises(ValueError):public_target('http://rsshub:1200/feed')
    assert public_target('http://rsshub:1200/feed',trusted_origin='http://rsshub:1200')[1]==['172.19.0.3']
    with pytest.raises(ValueError):public_target('http://other:1200/feed',trusted_origin='http://rsshub:1200')
    with pytest.raises(ValueError):public_target('http://rsshub:80/feed',trusted_origin='http://rsshub:1200')
    mock_transport(monkeypatch,lambda req:httpx.Response(302,headers={'Location':'http://other:1200/private'}))
    with pytest.raises(ValueError):download('http://rsshub:1200/feed',trusted_origin='http://rsshub:1200')

def test_private_trust_never_allows_loopback(monkeypatch):
    monkeypatch.setenv('RSSHUB_INTERNAL_URL','http://rsshub:1200')
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',1200))])
    with pytest.raises(ValueError):public_target('http://rsshub:1200/feed',trusted_origin='http://rsshub:1200')

def test_html_transform_fetches_origin_directly_and_validates_links():
    route='https://rsshub.app/rsshub/transform/html/'+quote('https://example.com/news',safe='')+'/'+quote('title=News&item=article&itemTitle=h2&itemLink=a&itemDesc=p',safe='')
    calls=[]
    def fake(url,**kwargs):
        calls.append(url)
        return b'<article><h2>News one</h2><a href="/post/1">Read</a><p>Summary</p></article><article><h2>Unsafe</h2><a href="javascript:bad()">Bad</a></article>',url,'text/html'
    raw,final,_=fetch_feed(route,fake);data=json.loads(raw)
    assert calls==['https://example.com/news'] and final=='https://example.com/news'
    assert len(data['items'])==1 and data['items'][0]['url']=='https://example.com/post/1'

def test_transform_does_not_exempt_private_targets(monkeypatch):
    route='https://rsshub.app/rsshub/transform/html/'+quote('http://localhost/private',safe='')+'/'+quote('item=a',safe='')
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',80))])
    with pytest.raises(ValueError):fetch_feed(route,download)

def test_empty_transform_is_error_not_false_success():
    route='https://rsshub.app/rsshub/transform/html/'+quote('https://example.com',safe='')+'/'+quote('item=article',safe='')
    with pytest.raises(ValueError,match='未匹配到文章'):fetch_feed(route,lambda *a,**kw:(b'<html>Changed site</html>','https://example.com',''))

def test_public_hub_403_and_self_host_error_are_distinguished(monkeypatch):
    def fail(url,**kwargs):
        response=httpx.Response(403,request=httpx.Request('GET',url))
        raise httpx.HTTPStatusError('forbidden',request=response.request,response=response)
    monkeypatch.delenv('RSSHUB_BASE_URL',raising=False)
    with pytest.raises(ValueError,match='公共演示实例'):fetch_feed('https://rsshub.app/bjp/apod',fail)
    monkeypatch.setenv('RSSHUB_BASE_URL','https://own-hub.example')
    with pytest.raises(ValueError,match='已使用自建 RSSHub'):fetch_feed('https://rsshub.app/bjp/apod',fail)

def test_read_state_and_original_url_survive_hub_switch(client,monkeypatch):
    client.post('/api/feeds',json={'url':'https://rsshub.app/bjp/apod'})
    rss=b'<rss version="2.0"><channel><title>APOD</title><item><guid>stable</guid><link>https://example.com/a</link><title>A</title><description>Body</description></item></channel></rss>'
    monkeypatch.setenv('RSSHUB_BASE_URL','http://rsshub:1200');monkeypatch.setenv('RSSHUB_INTERNAL_URL','http://rsshub:1200')
    monkeypatch.setattr(m,'download',lambda url,**kw:(rss,url,''))
    REAL_REFRESH_ONE(1);client.patch('/api/articles/1',json={'read':True,'starred':True});REAL_REFRESH_ONE(1)
    assert client.get('/api/feeds').json()[0]['url']=='https://rsshub.app/bjp/apod'
    assert client.get('/api/articles').json()['total']==1
    assert client.get('/api/articles/1').json()['starred'] and client.get('/api/articles/1').json()['read']
