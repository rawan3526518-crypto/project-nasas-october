"""Read-only free data providers. Errors remain unavailable; no simulated fallback."""
import json, datetime, urllib.request, urllib.parse, math, concurrent.futures
import numpy as np
from rasterio.io import MemoryFile
from rasterio.warp import transform

def request(url,data=None,limit=8_000_000):
    req=urllib.request.Request(url,data=json.dumps(data).encode() if data is not None else None,headers={'User-Agent':'AgriSight-Local/2.0','Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=35) as r:
        b=r.read(limit+1)
        if len(b)>limit:raise ValueError('Provider response exceeds size limit')
        return b

def get_json(url,data=None):return json.loads(request(url,data))
def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def weather(lat,lon):
    q=urllib.parse.urlencode(dict(latitude=lat,longitude=lon,daily='temperature_2m_max,temperature_2m_min,precipitation_sum,et0_fao_evapotranspiration',timezone='Africa/Cairo',forecast_days=7))
    return {'source':'Open-Meteo','fetched_at':utc(),'location':[lon,lat],'resolution':'model grid, not a field sensor','data':get_json('https://api.open-meteo.com/v1/forecast?'+q)}
def power(lat,lon):
    end=datetime.date.today()-datetime.timedelta(days=10);start=end-datetime.timedelta(days=29)
    q=urllib.parse.urlencode(dict(latitude=lat,longitude=lon,parameters='T2M_MAX,T2M_MIN,PRECTOTCORR',community='AG',start=start.strftime('%Y%m%d'),end=end.strftime('%Y%m%d'),format='JSON'))
    d=get_json('https://power.larc.nasa.gov/api/temporal/daily/point?'+q)
    p=d.get('properties',{}).get('parameter',{});fill=d.get('header',{}).get('fill_value',-999)
    v=[x for x in p.get('T2M_MAX',{}).values() if isinstance(x,(float,int)) and math.isfinite(x) and x not in (fill,-999)]
    return {'source':'NASA POWER','fetched_at':utc(),'location':[lon,lat],'start':str(start),'end':str(end),'valid_days':len(v),'mean_daily_max_c':sum(v)/len(v) if v else None,'highest_daily_max_c':max(v) if v else None,'resolution':'Native regional grid, not a farm observation','data':d}
def soil(lat,lon):
    # SoilGrids REST API is paused; use documented WCS instead.
    # Raw map sand/silt/clay g/kg -> divide by 10 for percentage.
    out={}
    x,y=transform('EPSG:4326','ESRI:54052',[lon],[lat]);x=x[0];y=y[0]
    for prop in ['sand','silt','clay','phh2o']:
        params=[('map','/map/'+prop+'.map'),('SERVICE','WCS'),('VERSION','2.0.1'),('REQUEST','GetCoverage'),('COVERAGEID',prop+'_0-5cm_mean'),('FORMAT','GEOTIFF_INT16'),('SUBSET',f'X({x-130},{x+130})'),('SUBSET',f'Y({y-130},{y+130})'),('SUBSETTINGCRS','http://www.opengis.net/def/crs/EPSG/0/152160'),('OUTPUTCRS','http://www.opengis.net/def/crs/EPSG/0/152160')]
        try:
            raw=request('https://maps.isric.org/mapserv?'+urllib.parse.urlencode(params),limit=1_000_000)
            with MemoryFile(raw) as mem:
                with mem.open() as ds:
                    a=ds.read(1,masked=True);v=a.compressed();v=v[np.isfinite(v)]
                    out[prop]={'value':float(v.mean()/10) if len(v) else None,'unit':'pH' if prop=='phh2o' else '%'}
        except Exception:out[prop]={'value':None,'status':'unavailable'}
    return {'source':'ISRIC SoilGrids WCS, CC BY 4.0','fetched_at':utc(),'depth_cm':[0,5],'resolution_m':250,'properties':out,'limitations':'Model predictions, not laboratory tests. No salinity inferred. A governorate has multiple soils.'}

MISSION_NAMES={'SMAP':'SPL3SMP_E','ECOSTRESS':'ECO_L2T_LSTE','GPM':'GPM_3IMERGDF','GRACE-FO':'TELLUS_GRAC-GRFO_MASCON_CRI_GRID_RL06.3_V4','SRTM':'SRTMGL1','MODIS':'MOD13Q1'}
def catalog(mission,lat,lon):
    if mission not in MISSION_NAMES:raise ValueError('Unknown mission')
    q=urllib.parse.urlencode({'short_name':MISSION_NAMES[mission],'point':f'{lon},{lat}','page_size':5,'sort_key':'-start_date'})
    j=get_json('https://cmr.earthdata.nasa.gov/search/granules.json?'+q)
    return {'source':'NASA Earthdata CMR','mission':mission,'fetched_at':utc(),'measurement':None,'status':'catalog_only','limitations':'Product discovery only. No numeric product decoding implemented in this delivery. Some downloads require Earthdata login.','items':[{'title':e.get('title'),'observed_at':e.get('time_start'),'links':[{'href':x['href']} for x in e.get('links',[]) if x.get('href','').startswith('https://')]} for e in j.get('feed',{}).get('entry',[])]}
