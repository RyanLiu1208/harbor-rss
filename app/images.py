import hashlib
import os
import threading
import time
import uuid
from pathlib import Path
from bs4 import BeautifulSoup
from .content import web_url

slots=threading.BoundedSemaphore(4)

def image_type(raw):
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):return 'image/png'
    if raw.startswith(b'\xff\xd8\xff'):return 'image/jpeg'
    if raw.startswith((b'GIF87a',b'GIF89a')):return 'image/gif'
    if raw.startswith(b'RIFF') and raw[8:12]==b'WEBP':return 'image/webp'
    if raw[4:8]==b'ftyp' and any(x in raw[8:32] for x in (b'avif',b'avis')):return 'image/avif'
    raise ValueError('不是支持的图片类型（仅支持 JPEG、PNG、GIF、WebP、AVIF）')

def image_urls(html):
    return {i.get('src') for i in BeautifulSoup(html,'html.parser').find_all('img',src=True)}

def cached_image(data_dir,url,referrer,downloader):
    if not web_url(url):raise ValueError('无效图片地址')
    folder=Path(data_dir)/'images';folder.mkdir(exist_ok=True)
    digest=hashlib.sha256((url+'\n'+referrer).encode()).hexdigest();path=folder/(digest+'.img')
    if path.exists() and time.time()-path.stat().st_mtime<7*86400:
        with path.open('rb') as f:kind=image_type(f.read(40))
        return path,kind
    if not slots.acquire(timeout=5):raise ValueError('图片请求较多，请稍后重试')
    try:
        raw,_,_=downloader(url,headers={'Referer':referrer,'Accept':'image/avif,image/webp,image/*;q=0.9'},limit=12*1024*1024)
        kind=image_type(raw)
        temp=folder/(digest+'.'+uuid.uuid4().hex+'.tmp')
        try:temp.write_bytes(raw);os.replace(temp,path)
        finally:temp.unlink(missing_ok=True)
        files=sorted(folder.glob('*.img'),key=lambda p:p.stat().st_mtime,reverse=True);used=0
        for file in files:
            try:
                used+=file.stat().st_size
                if file!=path and (used>256*1024*1024 or time.time()-file.stat().st_mtime>7*86400):file.unlink(missing_ok=True)
            except FileNotFoundError:pass
        return path,kind
    finally:slots.release()
