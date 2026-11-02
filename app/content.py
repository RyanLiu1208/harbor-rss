"""Article normalization and translation with server-owned markup."""
import json
import hashlib
import re
from urllib.parse import urljoin, urlsplit, quote
import bleach
from bs4 import BeautifulSoup, Comment, NavigableString, UnicodeDammit
from readability import Document

TAGS = ['article','main','section','p','br','h1','h2','h3','h4','h5','h6','ul','ol','li','blockquote','pre','code','kbd','samp','strong','b','em','i','a','img','figure','figcaption','table','thead','tbody','tr','td','th','hr','div','span','sup','sub','del','details','summary']

def web_url(value, base=''):
    if not value:return ''
    try:
        url = urljoin(base, str(value or '').strip())
        p = urlsplit(url)
    except ValueError:return ''
    return url if p.scheme in ('http','https') and p.hostname and not p.username and not p.password else ''

def normalize_media(soup, base):
    for tag in list(soup.find_all('noscript')):
        inner = BeautifulSoup(tag.decode_contents(), 'html.parser')
        if inner.find('img'): tag.replace_with(inner)
    for img in soup.find_all('img'):
        lazy = next((img.get(k) for k in ['data-src','data-original','data-lazy-src','data-actualsrc','data-url'] if web_url(img.get(k),base) and img.get(k)), None)
        candidates = img.get('data-srcset') or img.get('srcset')
        if not candidates and img.parent and img.parent.name=='picture':
            source=img.parent.find('source',srcset=True)
            if source: candidates=source.get('srcset')
        if not lazy and candidates:
            parts=[part.strip().split() for part in candidates.split(',') if part.strip()]
            def rank(part):
                try:return float(part[1].rstrip('wx')) if len(part)>1 else 0
                except ValueError:return 0
            parts.sort(key=rank,reverse=True)
            lazy=next((p[0] for p in parts if web_url(p[0],base)),None)
        src=web_url(lazy or img.get('src'),base)
        if src: img['src']=src
        else: img.attrs.pop('src',None)
    for link in soup.find_all('a',href=True):
        link['href']=urljoin(base,link['href'])
        link['target']='_blank'; link['rel']='noopener noreferrer'
    return soup

def safe_html(html, base=''):
    soup=normalize_media(BeautifulSoup(html,'html.parser'),base)
    for tag in soup(['script','style','iframe','object','form','input','button','svg']): tag.decompose()
    return bleach.clean(str(soup),tags=TAGS,attributes={'a':['href','target','rel'],'img':['src','alt','title'],'td':['colspan','rowspan'],'th':['colspan','rowspan']},protocols=['http','https','mailto'],strip=True)

def plaintext(html): return BeautifulSoup(html,'html.parser').get_text('\n',strip=True)

def reader_html(html, aid):
    soup=BeautifulSoup(safe_html(html),'html.parser')
    for img in soup.find_all('img',src=True):
        if web_url(img['src']):
            img['src']=f'/api/articles/{aid}/image?url='+quote(img['src'],safe='')
            img['loading']='lazy'; img['decoding']='async'
    return str(soup)

def entry_link(entry, base, site=''):
    links=entry.get('links',[])
    choices=[x.get('href') for x in links if x.get('rel','alternate')=='alternate' and x.get('type','text/html').split(';')[0] in ('text/html','application/xhtml+xml','')]
    choices += [entry.get('link')]+[entry.get(k) for k in ('id','guid') if str(entry.get(k,'')).startswith(('https://','http://'))]
    for value in choices:
        if value and (url:=web_url(value,site or base)) and url!=base:
            return url
    return ''

def feed_body(entry, base):
    contents=entry.get('content') or []
    body=next((c.get('value') for c in contents if c.get('value')),None) or entry.get('summary') or ''
    html=safe_html(body,base)
    soup=BeautifulSoup(html,'html.parser'); existing={x.get('src') for x in soup.find_all('img')}
    media=list(entry.get('media_content',[]))+list(entry.get('media_thumbnail',[]))+list(entry.get('enclosures',[]))
    for item in media:
        kind=item.get('type','')
        url=web_url(item.get('url') or item.get('href'),base)
        if url and url not in existing and (kind.startswith('image/') or item.get('medium')=='image' or item in entry.get('media_thumbnail',[])):
            tag=soup.new_tag('img',src=url); tag['alt']=entry.get('title',''); soup.append(tag); existing.add(url)
    return str(soup)

def discover_feed(raw, url):
    soup=BeautifulSoup(raw,'html.parser')
    for link in soup.find_all('link',href=True):
        if 'alternate' in link.get('rel',[]) and link.get('type','').split(';')[0] in ('application/rss+xml','application/atom+xml','application/feed+json'):
            return web_url(link['href'],url)
    return ''

def extract_body(raw, url):
    decoded=UnicodeDammit(raw,is_html=True).unicode_markup or raw.decode('utf-8',errors='replace')
    soup=normalize_media(BeautifulSoup(decoded,'html.parser'),url)
    candidates=[]
    try: candidates.append(safe_html(Document(str(soup),url=url).summary(keep_all_images=True),url))
    except Exception: pass
    # Prefer an explicit body over the whole <article>, which often also holds
    # author cards, duplicate titles, recommendations and unrelated images.
    for selector in ['[itemprop="articleBody"]','.post-content','.entry-content','.article-content','.article-body','.article__content','.markdown-body']:
        found=[]
        for node in soup.select(selector)[:5]:
            for extra in node.select('nav,aside,footer,form,script,style'): extra.decompose()
            candidate=safe_html(str(node),url)
            if len(plaintext(candidate))>=80: found.append(candidate)
        if found:return max(found,key=lambda c:len(plaintext(c)))
    if candidates and len(plaintext(candidates[0]))>=80:return candidates[0]
    for node in soup.select('article,main')[:5]:
        for extra in node.select('nav,aside,header,footer,form,script,style'):extra.decompose()
        candidates.append(safe_html(str(node),url))
    if not candidates or max(len(plaintext(c)) for c in candidates)<80:
        raise ValueError('未能提取正文；可能是摘要、动态页面、登录页或失效链接')
    return max(candidates,key=lambda c:len(plaintext(c)))

def substantial(html):
    return len(plaintext(html))>=600

def build_translation(html, size=4500):
    soup=BeautifulSoup(html,'html.parser'); segments=[]
    for node in list(soup.find_all(string=True)):
        if isinstance(node,Comment) or any(p.name in ('pre','code','kbd','samp') for p in node.parents):continue
        text=str(node)
        if not re.search(r'[^\W\d_]',text,re.UNICODE): continue
        replacement=[]
        for offset in range(0,len(text),size):
            part=text[offset:offset+size]; sid='s'+str(len(segments))
            span=soup.new_tag('span'); span['data-harbor-text']=sid; span.string=part
            replacement.append(span); segments.append({'id':sid,'text':part})
        node.replace_with(*replacement)
    chunks=[]; current=[]; count=0
    for seg in segments:
        if current and (count+len(seg['text'])>size or len(current)>=60):chunks.append(current);current=[];count=0
        current.append(seg);count+=len(seg['text'])
    if current:chunks.append(current)
    return str(soup),chunks

def parse_translation(output, segments):
    text=output.strip()
    if text.startswith('```'):text=re.sub(r'^```(?:json)?\s*|\s*```$','',text)
    try:
        data=json.loads(text); rows=data['translations'] if isinstance(data,dict) else data
        expected={s['id'] for s in segments}
        if not isinstance(rows,list) or len(rows)!=len(expected):raise ValueError()
        result={r['id']:r['text'] for r in rows}
        if set(result)!=expected or any(not isinstance(t,str) or not t.strip() for t in result.values()):raise ValueError()
        for segment in segments:
            # Catch obvious one-line summaries of long paragraphs. Normal
            # translation ratios vary by language, so keep this conservative.
            if len(segment['text'])>=800 and len(result[segment['id']])<len(segment['text'])*0.12:
                raise ValueError()
        return result
    except (ValueError,TypeError,KeyError):
        raise ValueError('模型未返回完整的段落译文，已保留进度，可继续重试')

def translation_source(template):
    soup=BeautifulSoup(template,'html.parser')
    for span in list(soup.select('[data-harbor-text]')):span.unwrap()
    return safe_html(str(soup))

def content_signature(html):
    soup=BeautifulSoup(html,'html.parser')
    data=[soup.get_text(),[i.get('src','') for i in soup.find_all('img')],[a.get('href','') for a in soup.find_all('a')]]
    return hashlib.sha256(json.dumps(data,ensure_ascii=False).encode()).hexdigest()

def translation_coverage(chunks, results):
    total=sum(len(group) for group in chunks)
    completed=0;first_bad=None
    for index,group in enumerate(chunks):
        result=results[index] if index<len(results) else None
        try:
            parse_translation(json.dumps({'translations':[{'id':k,'text':v} for k,v in result.items()]}),group)
            completed+=len(group)
        except (ValueError,AttributeError,TypeError):
            if first_bad is None:first_bad=index
    return {'total':total,'completed':completed,'first_bad':first_bad}

def render_translation(template, results):
    soup=BeautifulSoup(template,'html.parser'); translated={k:v for group in results if isinstance(group,dict) for k,v in group.items() if isinstance(v,str) and v.strip()}
    for span in list(soup.select('[data-harbor-text]')):
        sid=span['data-harbor-text']
        if sid in translated:
            original=span.get_text(); leading=re.match(r'^\s*',original).group(); trailing=re.search(r'\s*$',original).group()
            span.replace_with(NavigableString(leading+translated[sid].strip()+trailing))
        else:span.unwrap()
    # AI output was inserted as text, never trusted as HTML.
    return safe_html(str(soup))
