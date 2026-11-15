from pathlib import Path
import os,json,sqlite3,secrets,datetime,hashlib,io,uuid,concurrent.futures
from contextlib import asynccontextmanager
from fastapi import FastAPI,HTTPException,Request,UploadFile,File
from fastapi.responses import FileResponse,Response,JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field,ConfigDict
from shapely.geometry import shape,Point
from PIL import Image,ImageOps
from . import providers,core,satellite

ROOT=Path(__file__).resolve().parents[1]
DATA=Path(os.getenv('AGRISIGHT_DATA',str(ROOT/'data')));DATA.mkdir(exist_ok=True)
(DATA/'photos').mkdir(exist_ok=True);(DATA/'exports').mkdir(exist_ok=True)
TOKEN=secrets.token_urlsafe(32)
DB=DATA/'agrisight.sqlite3'
def connect():
    c=sqlite3.connect(DB,timeout=20);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');return c

def init():
    with connect() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS farms(id TEXT PRIMARY KEY, body TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, body TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS photos(id TEXT PRIMARY KEY,farm_id TEXT NOT NULL REFERENCES farms(id),hash TEXT UNIQUE NOT NULL,note TEXT NOT NULL,created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS equipment(id TEXT PRIMARY KEY,body TEXT NOT NULL,created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bookings(id TEXT PRIMARY KEY,equipment_id TEXT NOT NULL REFERENCES equipment(id),body TEXT NOT NULL,created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trials(id TEXT PRIMARY KEY,farm_id TEXT NOT NULL REFERENCES farms(id),body TEXT NOT NULL,created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plans(farm_id TEXT PRIMARY KEY REFERENCES farms(id),body TEXT NOT NULL);
''')
@asynccontextmanager
async def lifespan(app):init();yield
app=FastAPI(title='بصيرة الأرض — Local',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
@app.middleware('http')
async def guard(req:Request,call_next):
    # Local-only app. Never bind to public interfaces without adding real account authentication.
    host=req.headers.get('host','').split(':')[0]
    if host not in ('127.0.0.1','localhost','testserver'):return JSONResponse({'detail':'Localhost only'},status_code=403)
    origin=req.headers.get('origin')
    if origin and origin not in ('http://'+req.headers.get('host',''),'https://'+req.headers.get('host','')):return JSONResponse({'detail':'Cross-origin access rejected'},status_code=403)
    if req.url.path.startswith('/api/') and req.url.path!='/api/session' and req.headers.get('x-app-token')!=TOKEN:
        return JSONResponse({'detail':'Session required'},status_code=401)
    r=await call_next(req)
    r.headers['X-Content-Type-Options']='nosniff';r.headers['Referrer-Policy']='no-referrer'
    if req.url.path.startswith('/api/'):r.headers['Cache-Control']='no-store'
    return r
@app.exception_handler(ValueError)
async def invalid(req,exc):return JSONResponse({'detail':str(exc)},status_code=422)
@app.get('/api/session')
def session():return {'token':TOKEN,'mode':'local_single_operator','version':'2.0-local','cloud_sync':False}
@app.get('/api/status')
def status():
    return {'boundaries':(DATA/'governorates.geojson').exists(),'crop_rules':(DATA/'crop_rules.json').exists(),'salinity_atlas':False,'numeric_earthdata_products':False,'crop_classifier':False,'sms_calls':'replaced_with_in_app_alerts','voice':'browser dependent','storage':'SQLite on this computer'}
class Farm(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=100)
    lat:float=Field(ge=22,le=32)
    lon:float=Field(ge=24,le=37)
    area:float=Field(gt=0,le=100000000)
    soil:str='غير معروف'
    season:str='شتوي'
    previous:str=''
    previous_family:str='غير معروفة'
    ec_ds_m:float|None=Field(default=None,ge=0,le=100)
    budget:str='اقتصادية'
    geometry:dict|None=None

def farm(fid):
    with connect() as c:r=c.execute('SELECT * FROM farms WHERE id=?',(fid,)).fetchone()
    if not r:raise HTTPException(404,'الأرض غير موجودة')
    return {'id':fid,**json.loads(r['body'])}
@app.get('/api/farms')
def farms():
    with connect() as c:return [{'id':r['id'],**json.loads(r['body'])} for r in c.execute('SELECT * FROM farms ORDER BY updated DESC')]
@app.post('/api/farms')
def create_farm(f:Farm):
    if f.geometry:core.project_polygon(f.geometry)
    fid=str(uuid.uuid4())
    with connect() as c:c.execute('INSERT INTO farms VALUES(?,?,?)',(fid,f.model_dump_json(),providers.utc()))
    return {'id':fid}
@app.put('/api/farms/{fid}')
def update_farm(fid:str,f:Farm):
    farm(fid)
    if f.geometry:core.project_polygon(f.geometry)
    with connect() as c:c.execute('UPDATE farms SET body=?,updated=? WHERE id=?',(f.model_dump_json(),providers.utc(),fid))
    return {'id':fid}
@app.get('/api/governorates')
def governorates():
    p=DATA/'governorates.geojson'
    if not p.exists():return {'type':'FeatureCollection','features':[],'status':'unavailable'}
    return json.loads(p.read_text())
def atlas_lookup(f):
    features=governorates().get('features',[]);found=[]
    for g in features:
        if shape(g['geometry']).covers(Point(f['lon'],f['lat'])):found.append(g['properties'])
    return {'governorates':found,'source':'geoBoundaries gbOpen EGY ADM1','soil_status':'requires SoilGrids request or field records','salinity_status':'not available; enter laboratory result','note':'Administrative identity does not imply uniform soil across the governorate.'}
@app.get('/api/farms/{fid}/atlas')
def atlas(fid:str):return atlas_lookup(farm(fid))
def cache_get(key):
    with connect() as c:r=c.execute('SELECT * FROM cache WHERE key=?',(key,)).fetchone()
    return {'state':'cached','cached_at':r['updated'],'payload':json.loads(r['body'])} if r else {'state':'unavailable','payload':None}
def cache_put(key,data):
    with connect() as c:c.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?)',(key,json.dumps(data,ensure_ascii=False,allow_nan=False),providers.utc()))
def cached_provider(fid,name,fn,refresh):
    f=farm(fid);key=f"{fid}:{name}:{f['lat']}:{f['lon']}"
    if not refresh:return cache_get(key)
    try:
        data=fn(f['lat'],f['lon']);cache_put(key,data);return {'state':'fetched','payload':data}
    except Exception as e:
        old=cache_get(key);return {**old,'refresh_error':type(e).__name__,'message':'تعذّر تحديث المصدر؛ إن ظهرت بيانات فهي محفوظة بتاريخها وليست تحديثًا جديدًا.'}
@app.get('/api/farms/{fid}/weather')
def weather(fid:str,refresh:bool=False):return cached_provider(fid,'weather',providers.weather,refresh)
@app.get('/api/farms/{fid}/power')
def power(fid:str,refresh:bool=False):return cached_provider(fid,'power',providers.power,refresh)
@app.get('/api/farms/{fid}/soil')
def soil(fid:str,refresh:bool=False):return cached_provider(fid,'soil',providers.soil,refresh)
@app.get('/api/farms/{fid}/missions/{name}')
def mission(fid:str,name:str,refresh:bool=False):
    if name not in providers.MISSION_NAMES:raise HTTPException(404)
    return cached_provider(fid,name,lambda a,b:providers.catalog(name,a,b),refresh)
@app.get('/api/farms/{fid}/split')
def split(fid:str):
    f=farm(fid)
    if not f['geometry']:return {'area_m2':f['area'],'trial_m2':f['area']*.2,'control_m2':f['area']*.8,'geometry':None,'note':'No field boundary supplied; numerical area split only.'}
    return core.split_field(f['geometry'])
class Analysis(BaseModel):year:int=Field(ge=2013,le=2100)
@app.post('/api/farms/{fid}/ndvi')
def ndvi(fid:str,p:Analysis):
    f=farm(fid)
    if not f['geometry']:raise HTTPException(422,'ارسم حدود الأرض أولًا')
    try:r=satellite.field_ndvi(f['geometry'],p.year)
    except Exception as e:raise HTTPException(502,'تعذّر استخراج NDVI: '+str(e)[:200])
    cache_put(fid+':ndvi',r);return r
class Movie(BaseModel):start_year:int=Field(ge=2013,le=2100);end_year:int=Field(ge=2013,le=2100)
@app.post('/api/farms/{fid}/timelapse')
def movie(fid:str,p:Movie):
    f=farm(fid)
    if not f['geometry']:raise HTTPException(422,'ارسم حدود الأرض أولًا')
    try:raw,meta=satellite.timelapse(f['geometry'],p.start_year,p.end_year)
    except Exception as e:raise HTTPException(502,str(e)[:200])
    name=uuid.uuid4().hex+'.gif';(DATA/'exports'/name).write_bytes(raw);cache_put(fid+':movie',meta)
    return {'file':name,'metadata':meta}
@app.get('/api/exports/{name}')
def export_file(name:str):
    if '/' in name or '..' in name or not name.endswith('.gif'):raise HTTPException(400)
    p=DATA/'exports'/name
    if not p.exists():raise HTTPException(404)
    return FileResponse(p,media_type='image/gif')
@app.get('/api/farms/{fid}/recommendations')
def recommendations(fid:str):
    f=farm(fid);p=DATA/'crop_rules.json'
    if not p.exists():return {'status':'missing_local_reviewed_rules','candidates':[],'message':'لا توجد قواعد زراعية محلية معتمدة في الحزمة. لا يمكن توليد ترشيحات موثوقة من المناخ وحده.'}
    rules=json.loads(p.read_text());return {'status':'rule_based_not_trained_ai','candidates':core.rank_candidates(f,rules),'source':'Imported agronomist-reviewed rules'}
class Plan(BaseModel):
    seasons:list[dict]=Field(max_length=12)
@app.put('/api/farms/{fid}/plan')
def save_plan(fid:str,p:Plan):
    farm(fid)
    with connect() as c:c.execute('INSERT OR REPLACE INTO plans VALUES(?,?)',(fid,p.model_dump_json()))
    return {'saved':True}
@app.get('/api/farms/{fid}/plan')
def plan(fid:str):
    farm(fid)
    with connect() as c:r=c.execute('SELECT body FROM plans WHERE farm_id=?',(fid,)).fetchone()
    return json.loads(r[0]) if r else {'seasons':[]}
class Water(BaseModel):eto:float;kc:float;rain:float;efficiency:float;area:float
@app.post('/api/water')
def watercalc(p:Water):return core.water(**p.model_dump())
class Trial(BaseModel):test:dict;control:dict;crop:str=Field(max_length=80);date:str=Field(max_length=30)
@app.post('/api/farms/{fid}/trials')
def trial(fid:str,p:Trial):
    farm(fid);r=core.compare_trial(p.test,p.control);tid=uuid.uuid4().hex
    with connect() as c:c.execute('INSERT INTO trials VALUES(?,?,?,?)',(tid,fid,json.dumps({'input':p.model_dump(),'result':r},ensure_ascii=False),providers.utc()))
    return {'id':tid,**r}
@app.get('/api/farms/{fid}/trials')
def trials(fid:str):
    farm(fid)
    with connect() as c:return [json.loads(r[0]) for r in c.execute('SELECT body FROM trials WHERE farm_id=? ORDER BY created DESC',(fid,))]
@app.post('/api/farms/{fid}/photos')
async def photo(fid:str,file:UploadFile=File(...)):
    farm(fid);raw=await file.read(6_000_001)
    if len(raw)>6_000_000:raise HTTPException(413,'الصورة أكبر من ٦ ميجابايت')
    try:
        im=Image.open(io.BytesIO(raw));im=ImageOps.exif_transpose(im);im.thumbnail((1200,1200));b=io.BytesIO();im.convert('RGB').save(b,format='JPEG',quality=80);raw=b.getvalue()
    except Exception:raise HTTPException(422,'صورة غير صالحة')
    digest=hashlib.sha256(raw).hexdigest();pid=uuid.uuid4().hex
    try:
        with connect() as c:c.execute('INSERT INTO photos VALUES(?,?,?,?,?)',(pid,fid,digest,'مساهمة مصورة، غير معتمدة كقياس للتربة',providers.utc()))
    except sqlite3.IntegrityError:raise HTTPException(409,'الصورة مسجلة بالفعل')
    (DATA/'photos'/(pid+'.jpg')).write_bytes(raw)
    return {'id':pid,'points':1,'meaning':'نقطة مشاركة داخلية، بلا قيمة مالية؛ الصورة لا تؤكد قراءة قمر صناعي.'}
@app.get('/api/farms/{fid}/photos')
def photos(fid:str):
    farm(fid)
    with connect() as c:return [dict(r) for r in c.execute('SELECT id,note,created FROM photos WHERE farm_id=? ORDER BY created',(fid,))]
@app.get('/api/photos/{pid}')
def image(pid:str):
    if len(pid)!=32 or not all(x in '0123456789abcdef' for x in pid):raise HTTPException(400)
    p=DATA/'photos'/(pid+'.jpg')
    if not p.exists():raise HTTPException(404)
    return FileResponse(p,media_type='image/jpeg')
class Equipment(BaseModel):name:str=Field(min_length=1,max_length=80);region:str=Field(max_length=80);daily_cost:float=Field(ge=0);participants:int=Field(ge=1,le=30)
@app.post('/api/equipment')
def equipment(p:Equipment):
    eid=uuid.uuid4().hex
    with connect() as c:c.execute('INSERT INTO equipment VALUES(?,?,?)',(eid,p.model_dump_json(),providers.utc()))
    return {'id':eid}
@app.get('/api/equipment')
def equipments():
    with connect() as c:return [{'id':r['id'],**json.loads(r['body']),'per_person':json.loads(r['body'])['daily_cost']/json.loads(r['body'])['participants']} for r in c.execute('SELECT * FROM equipment')]
@app.get('/api/farms/{fid}/offline-pack')
def pack(fid:str):
    f=farm(fid);w=weather(fid);a=atlas(fid);p=plan(fid);raw=core.seasonal_pack(f,a,w,p)
    return Response(raw,media_type='application/json',headers={'Content-Disposition':'attachment; filename="season-pack.json"','X-Pack-Bytes':str(len(raw))})
@app.get('/api/farms/{fid}/neighbors')
def neighbors(fid:str):
    f=farm(fid);counts={};n=0
    for x in farms():
        d=6371*2*__import__('math').asin(min(1,(__import__('math').sin(__import__('math').radians(x['lat']-f['lat'])/2)**2+__import__('math').cos(__import__('math').radians(f['lat']))*__import__('math').cos(__import__('math').radians(x['lat']))*__import__('math').sin(__import__('math').radians(x['lon']-f['lon'])/2)**2)**.5))
        if x['id']!=fid and d<=5 and x.get('previous'):n+=1;counts[x['previous']]=counts.get(x['previous'],0)+1
    if n<5:return {'status':'insufficient_records','minimum_records':5,'distribution':{},'source':'locally entered records, NOT satellite classification'}
    return {'status':'local_records_only','distribution':{k:round(v/n*100) for k,v in counts.items() if v>=3},'source':'locally entered records; categories with fewer than three fields suppressed; percentages by field count, not area'}
@app.get('/api/backup')
def backup():
    tables={}
    with connect() as c:
        for name in ['farms','trials','plans','equipment','photos']:tables[name]=[dict(r) for r in c.execute('SELECT * FROM '+name)]
    return Response(json.dumps(tables,ensure_ascii=False),media_type='application/json',headers={'Content-Disposition':'attachment; filename="agrisight-records.json"'})
app.mount('/',StaticFiles(directory=ROOT/'web',html=True),name='web')
