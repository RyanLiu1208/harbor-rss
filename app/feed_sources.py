"""Use the owner's RSSHub; perform public HTML transforms without unsafe RSSHub routes."""
import json
import os
from urllib.parse import urlsplit, urlunsplit, unquote, parse_qs
from bs4 import BeautifulSoup, UnicodeDammit
from .content import safe_html, web_url

PUBLIC_HUBS={'rsshub.app','www.rsshub.app'}
TRANSFORM='/rsshub/transform/html/'

def transform_html(url, downloader):
    path=urlsplit(url).path
    parts=path[len(TRANSFORM):].split('/',1)
    if len(parts)!=2:raise ValueError('网页转换订阅缺少地址或 CSS 规则')
    source=web_url(unquote(parts[0]))
    if not source:raise ValueError('网页转换源地址无效')
    rules={k:v[0] for k,v in parse_qs(unquote(parts[1])).items()}
    raw,final,_=downloader(source)
    encoding=rules.get('encoding')
    decoded=raw.decode(encoding,errors='replace') if encoding else UnicodeDammit(raw,is_html=True).unicode_markup or raw.decode('utf-8',errors='replace')
    soup=BeautifulSoup(decoded,'html.parser')
    def selected(node,key):return node.select_one(rules[key]) if rules.get(key) else node
    items=[];seen=set()
    for node in soup.select(rules.get('item','a[href]'))[:100]:
        target=selected(node,'itemLink')
        if not target:continue
        attr=rules.get('itemLinkAttr','href')
        if not target.get(attr):target=target.find('a',href=True)
        link=web_url(target.get(attr) if target else None,final)
        if not link or link in seen:continue
        title_node=selected(node,'itemTitle')
        title=(title_node.get(rules['itemTitleAttr'],'') if rules.get('itemTitleAttr') else title_node.get_text(' ',strip=True)) if title_node else ''
        if not title:continue
        desc=selected(node,'itemDesc')
        description=desc.get(rules['itemDescAttr'],'') if desc and rules.get('itemDescAttr') else str(desc or '')
        items.append({'id':link,'url':link,'title':title,'content_html':safe_html(description,final)})
        seen.add(link)
        if len(items)>=30:break
    if not items:raise ValueError('原站页面可访问，但网页转换 CSS 未匹配到文章；请检查转换规则或网站是否已改版')
    result={'version':'https://jsonfeed.org/version/1.1','home_page_url':final,'title':rules.get('title') or (soup.title.get_text() if soup.title else source),'items':items}
    return json.dumps(result,ensure_ascii=False).encode(),final,'application/feed+json'

def fetch_feed(url, downloader):
    p=urlsplit(url)
    is_hub=p.hostname in PUBLIC_HUBS
    if is_hub and p.path.startswith(TRANSFORM):
        return transform_html(url,downloader)
    base=os.getenv('RSSHUB_BASE_URL','').rstrip('/')
    if is_hub and base:
        t=urlsplit(base)
        if t.scheme not in ('http','https') or not t.hostname or t.username or t.password or t.query or t.fragment:
            raise ValueError('RSSHUB_BASE_URL 必须是不含认证信息、查询和片段的 HTTP/HTTPS 基础地址')
        destination=urlunsplit((t.scheme,t.netloc,t.path.rstrip('/')+p.path,p.query,''))
        trusted=base if base==os.getenv('RSSHUB_INTERNAL_URL','').rstrip('/') else None
        try:return downloader(destination, trusted_origin=trusted, headers={'Accept':'application/rss+xml, application/atom+xml, application/feed+json'})
        except Exception as e:
            response=getattr(e,'response',None)
            if response is not None and response.status_code in (403,404,500,502,503):
                if p.path.startswith('/twitter/') and response.status_code==503:
                    raise ValueError('自建 RSSHub 已连接，但 X/Twitter 路由需要额外配置 API 或账户授权；当前未配置，不会自动读取浏览器登录信息') from e
                raise ValueError(f'已使用自建 RSSHub，但此路由返回 HTTP {response.status_code}；可能是原站拒绝、路由已变化或需要额外配置。已有文章已保留') from e
            raise
    try:return downloader(url,headers={'Accept':'application/atom+xml, application/rss+xml, application/feed+json, application/xml, text/xml, text/html;q=0.5'})
    except Exception as e:
        response=getattr(e,'response',None)
        if is_hub and response is not None and response.status_code==403:
            raise ValueError('RSSHub 公共演示实例限制了阅读器访问。可启用本项目 compose.rsshub.yaml 使用自己的 RSSHub；已有文章已保留') from e
        raise
