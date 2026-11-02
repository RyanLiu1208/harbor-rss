import json
from bs4 import BeautifulSoup
import pytest
from app import main as m
from app.content import build_translation,parse_translation,plaintext
from test_app import client,insert_article,fake_translation

def test_old_excerpt_translation_is_not_presented_as_current_full_text(client,monkeypatch):
    insert_article('<p>Old excerpt only.</p>');monkeypatch.setenv('DEEPSEEK_API_KEY','fake-key')
    monkeypatch.setattr(m,'download',lambda url,**kw:(fake_translation(json.loads(kw['json']['messages'][-1]['content'])['segments']),url,''))
    jid=client.post('/api/articles/1/translate',json={'use_feed_content':True}).json()['job_id'];m.translate_job(jid)
    with m.db() as c:c.execute('UPDATE articles SET full_content=? WHERE id=1',('<p>'+('Complete source article. '*100)+'</p>',))
    job=client.get('/api/jobs/'+jid).json()
    assert job['status']=='done' and job['stale'] and job['source_kind']=='feed'
    assert plaintext(job['source_html'])=='Old excerpt only.'
    assert 'Complete source' not in job['source_html']
    full_id=client.post('/api/articles/1/translate',json={}).json()['job_id']
    assert full_id!=jid
    m.translate_job(full_id);complete=client.get('/api/jobs/'+full_id).json()
    assert not complete['stale'] and complete['source_kind']=='full'
    assert 'Complete source' in complete['translation'] and complete['segments_completed']==complete['segments_total']

def test_incomplete_checkpoint_cannot_be_reported_as_done(client,monkeypatch):
    insert_article('<p>First paragraph.</p><p>Second paragraph.</p>');monkeypatch.setenv('DEEPSEEK_API_KEY','fake-key')
    jid=client.post('/api/articles/1/translate',json={'use_feed_content':True}).json()['job_id']
    with m.db() as c:c.execute("UPDATE jobs SET status='done',results=? WHERE id=?",(json.dumps([{'s0':'Only first translated'}]),jid))
    job=client.get('/api/jobs/'+jid).json()
    assert job['status']=='incomplete' and job['segments_completed']==0
    assert client.post('/api/jobs/'+jid+'/retry').status_code==200
    monkeypatch.setattr(m,'download',lambda url,**kw:(fake_translation(json.loads(kw['json']['messages'][-1]['content'])['segments']),url,''))
    m.translate_job(jid)
    repaired=client.get('/api/jobs/'+jid).json()
    assert repaired['status']=='done' and repaired['segments_completed']==2

def test_obvious_summary_of_long_paragraph_is_rejected():
    with pytest.raises(ValueError):parse_translation('{"translations":[{"id":"s0","text":"Just a summary"}]}',[{'id':'s0','text':'Original full paragraph. '*100}])

def test_folder_rename_updates_descendants_and_preserves_states(client):
    insert_article();client.patch('/api/feeds/1',json={'url':'https://example.com/rss','folder':'Articles/AI','title':'A'})
    client.patch('/api/articles/1',json={'read':True,'starred':True})
    client.post('/api/feeds',json={'url':'https://example.org/rss','folder':'Articles/Finance','title':'B'})
    client.post('/api/feeds',json={'url':'https://example.net/rss','folder':'ArticlesExtra','title':'C'})
    result=client.post('/api/folders/rename',json={'old_folder':'Articles','new_folder':'阅读'}).json()
    assert result['updated']==2
    folders={f['id']:f['folder'] for f in client.get('/api/feeds').json()}
    assert folders=={1:'阅读/AI',2:'阅读/Finance',3:'ArticlesExtra'}
    assert client.get('/api/articles?folder=阅读').json()['total']==1
    article=client.get('/api/articles/1').json();assert article['starred'] and article['read']
    assert '阅读' in client.get('/api/export').text

def test_folder_rename_collision_and_invalid_move_are_atomic(client):
    insert_article();client.patch('/api/feeds/1',json={'url':'https://example.com/rss','folder':'A'})
    client.post('/api/feeds',json={'url':'https://example.org/rss','folder':'B'})
    assert client.post('/api/folders/rename',json={'old_folder':'A','new_folder':'B'}).status_code==409
    assert client.post('/api/folders/move',json={'feed_ids':[1,9999],'folder':'C'}).status_code==404
    assert next(f for f in client.get('/api/feeds').json() if f['id']==1)['folder']=='A'

def test_group_unfiled_and_move_selected_feeds(client):
    insert_article();client.post('/api/feeds',json={'url':'https://example.org/rss'})
    assert client.post('/api/folders/rename',json={'old_folder':'','new_folder':'Inbox'}).json()['updated']==2
    assert client.post('/api/folders/move',json={'feed_ids':[1,2],'folder':'Work/Research'}).json()['updated']==2
    assert all(f['folder']=='Work/Research' for f in client.get('/api/feeds').json())
    assert client.post('/api/folders/move',json={'feed_ids':[1],'folder':''}).status_code==200
    assert client.get('/api/articles?folder=').json()['total']==1
