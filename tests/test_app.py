import os,tempfile,json,io
from pathlib import Path
os.environ['AGRISIGHT_DATA']=tempfile.mkdtemp(prefix='agrisight-test-')
from fastapi.testclient import TestClient
from backend.app import app
from backend import core
from PIL import Image
import pytest

@pytest.fixture
def client():
    with TestClient(app) as c:
        c.headers['X-App-Token']=c.get('/api/session').json()['token']
        yield c

def create(c):
    r=c.post('/api/farms',json={'name':'اختبار','lat':28,'lon':30,'area':1000});assert r.status_code==200;return r.json()['id']
def test_auth():
    with TestClient(app) as c:assert c.get('/api/farms').status_code==401

def test_cross_origin(client):assert client.get('/api/farms',headers={'Origin':'https://evil.example'}).status_code==403

def test_farm_round_trip(client):
    fid=create(client);assert any(f['id']==fid and f['name']=='اختبار' for f in client.get('/api/farms').json())

def test_invalid_coordinates(client):assert client.post('/api/farms',json={'name':'X','lat':100,'lon':30,'area':100}).status_code==422

def test_split_ratio():
    r=core.split_field({'type':'Polygon','coordinates':[[[30,28],[30.01,28],[30.01,28.01],[30,28.01],[30,28]]]})
    assert abs(r['ratio']-.2)<1e-8

def test_self_intersection():
    with pytest.raises(ValueError):core.split_field({'type':'Polygon','coordinates':[[[30,28],[30.01,28.01],[30.01,28],[30,28.01],[30,28]]]})

def test_water():assert core.water(4,1,0,80,1000)['gross_m3_day']==5

def test_water_bad_efficiency():
    with pytest.raises(ValueError):core.water(4,1,0,0,1000)

def test_normalized_trial():
    r=core.compare_trial({'area':200,'water':40,'yield':100},{'area':800,'water':200,'yield':320})
    assert r['yield_change_pct']==25;assert abs(r['water_reduction_pct']-20)<1e-8

def test_zero_reference():
    r=core.compare_trial({'area':10,'water':1,'yield':1},{'area':10,'water':0,'yield':0})
    assert r['yield_change_pct'] is None and r['water_reduction_pct'] is None

def test_trial_storage(client):
    fid=create(client);p={'crop':'test','date':'2026-11-15','test':{'area':200,'water':40,'yield':100},'control':{'area':800,'water':200,'yield':320}}
    assert client.post(f'/api/farms/{fid}/trials',json=p).status_code==200
    assert len(client.get(f'/api/farms/{fid}/trials').json())==1

def test_offline_pack(client):
    fid=create(client);r=client.get(f'/api/farms/{fid}/offline-pack');assert r.status_code==200;assert len(r.content)<2_000_000;assert r.json()['farm']['id']==fid

def test_no_fabricated_cache(client):
    fid=create(client);r=client.get(f'/api/farms/{fid}/weather').json();assert r['state']=='unavailable' and r['payload'] is None

def test_no_invented_recommendations(client):
    fid=create(client);r=client.get(f'/api/farms/{fid}/recommendations').json();assert r['status']=='missing_local_reviewed_rules' and r['candidates']==[]

def test_photo_dedup(client):
    fid=create(client);b=io.BytesIO();Image.new('RGB',(25,25),'green').save(b,format='PNG');raw=b.getvalue()
    r=client.post(f'/api/farms/{fid}/photos',files={'file':('x.png',raw,'image/png')});assert r.status_code==200
    assert client.post(f'/api/farms/{fid}/photos',files={'file':('x.png',raw,'image/png')}).status_code==409
    assert len(client.get(f'/api/farms/{fid}/photos').json())==1

def test_raster_host_rejection():
    from backend.satellite import safe_href
    with pytest.raises(ValueError):safe_href('http://127.0.0.1/private')
    with pytest.raises(ValueError):safe_href('https://blob.core.windows.net.attacker.example/a.tif')

def test_unreviewed_rule():
    r=core.rank_candidates({'soil':'رملية','season':'شتوي'},[{'soils':['رملية'],'seasons':['شتوي']}]);assert r[0]['eligibility']=='unknown'
