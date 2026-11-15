"""Actual Landsat reflectance/QA processing, bounded field NDVI and aligned GIF."""
import io, math, urllib.parse, concurrent.futures
import numpy as np
import rasterio
from rasterio.warp import transform_geom, reproject, Resampling
from rasterio.features import geometry_mask, bounds
from rasterio.transform import from_bounds
from rasterio.windows import from_bounds as window_from_bounds
from PIL import Image, ImageDraw
from .providers import get_json, utc
from .core import project_polygon

CATALOG='https://planetarycomputer.microsoft.com/api/stac/v1/search'
def search(geometry,year):
    project_polygon(geometry)
    j=get_json(CATALOG,{'collections':['landsat-c2-l2'],'intersects':geometry,'datetime':f'{year}-01-01T00:00:00Z/{year}-12-31T23:59:59Z','limit':12,'query':{'eo:cloud_cover':{'lt':25}},'sortby':[{'field':'eo:cloud_cover','direction':'asc'}]})
    return j.get('features',[])
def safe_href(href):
    p=urllib.parse.urlparse(href)
    if p.scheme!='https' or not p.hostname or not p.hostname.endswith('.blob.core.windows.net'):raise ValueError('Unsupported raster host')
    return href

def signed(asset):
    href=safe_href(asset['href'])
    return safe_href(get_json('https://planetarycomputer.microsoft.com/api/sas/v1/sign?'+urllib.parse.urlencode({'href':href}))['href'])
def read_clip(asset,geometry):
    href=signed(asset)
    with rasterio.Env(GDAL_HTTP_TIMEOUT=25,GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR',CPL_VSIL_CURL_ALLOWED_EXTENSIONS='.tif,.TIF',GDAL_HTTP_MAX_RETRY=1):
        with rasterio.open(href) as src:
            geo=transform_geom('EPSG:4326',src.crs,geometry)
            left,bottom,right,top=bounds(geo)
            win=window_from_bounds(left,bottom,right,top,src.transform).round_offsets().round_lengths()
            if win.width<1 or win.height<1:raise ValueError('Field smaller than raster pixel')
            if win.width*win.height>1_000_000:raise ValueError('Field raster exceeds processing limit')
            a=src.read(1,window=win,masked=True)
            tr=src.window_transform(win)
            inside=geometry_mask([geo],a.shape,tr,invert=True)
            return a,tr,src.crs,inside,abs(src.res[0])
def compute(item,geometry):
    assets=item['assets']
    red,tr,crs,inside,res=read_clip(assets['red'],geometry)
    nir,ntr,ncrs,_,_=read_clip(assets['nir08'],geometry)
    qa,qtr,qcrs,_,_=read_clip(assets['qa_pixel'],geometry)
    sat,strans,scrs,_,_=read_clip(assets['qa_radsat'],geometry)
    if any(a.shape!=red.shape for a in (nir,qa,sat)) or any(x!=tr for x in (ntr,qtr,strans)) or any(x!=crs for x in (ncrs,qcrs,scrs)):raise ValueError('Raster grids do not match')
    def scale(asset,a):
        b=asset.get('raster:bands',[{}])[0]
        if 'scale' not in b:raise ValueError('Missing reflectance scale metadata')
        return a.astype('float32')*b['scale']+b.get('offset',0)
    r,n=scale(assets['red'],red),scale(assets['nir08'],nir)
    valid=inside & ~np.ma.getmaskarray(r)&~np.ma.getmaskarray(n)&~np.ma.getmaskarray(qa)&~np.ma.getmaskarray(sat)
    valid&=((qa.filled(1).astype('uint16')&0b111111)==0)&(sat.filled(1)==0)
    den=(r+n).filled(np.nan);valid&=np.isfinite(den)&(np.abs(den)>1e-8)
    ndvi=np.full(red.shape,np.nan,dtype='float32');ndvi[valid]=((n-r).filled(np.nan)[valid]/den[valid]);valid&=(ndvi>=-1)&(ndvi<=1);ndvi[~valid]=np.nan
    count=int(valid.sum());total=int(inside.sum())
    if not count:raise ValueError('No clear valid field pixels')
    meta={'scene_id':item['id'],'observed_at':item['properties']['datetime'],'fetched_at':utc(),'source':'USGS Landsat Collection 2 Level 2 via Microsoft Planetary Computer','mean_ndvi':float(ndvi[valid].mean()),'valid_pixels':count,'field_pixels':total,'valid_fraction':count/max(total,1),'resolution_m':res,'method':'QA masked field-pixel mean of scaled (NIR-Red)/(NIR+Red)','limitations':'Clear-pixel mean only; not nutrient/soil diagnosis or crop identification.'}
    return meta,ndvi,tr,crs

def field_ndvi(geometry,year):
    errors=[]
    for item in search(geometry,year)[:3]:
        try:return compute(item,geometry)[0]
        except Exception as e:errors.append(type(e).__name__)
    raise ValueError('No usable Landsat scene: '+','.join(errors))

def timelapse(geometry,start_year,end_year):
    if end_year-start_year>9 or end_year<start_year:raise ValueError('Choose at most ten calendar years')
    project_polygon(geometry);extent=bounds(geometry);target=from_bounds(*extent,256,256);frames=[];records=[];missing=[]
    for year in range(start_year,end_year+1):
        success=False
        for item in search(geometry,year)[:3]:
            try:
                meta,arr,tr,crs=compute(item,geometry)
                dst=np.full((256,256),np.nan,dtype='float32')
                reproject(arr,dst,src_transform=tr,src_crs=crs,src_nodata=np.nan,dst_transform=target,dst_crs='EPSG:4326',dst_nodata=np.nan,resampling=Resampling.nearest)
                z=np.clip(np.nan_to_num(dst,nan=-1),0,1);rgb=np.zeros((256,256,3),dtype='uint8');rgb[:,:,0]=(180*(1-z));rgb[:,:,1]=(90+100*z);rgb[:,:,2]=60;rgb[~np.isfinite(dst)]=[225,225,225]
                im=Image.fromarray(rgb).convert('RGB');ImageDraw.Draw(im).text((6,6),meta['observed_at'][:10],fill='black',stroke_width=1,stroke_fill='white');frames.append(im);records.append(meta);success=True;break
            except Exception:continue
        if not success:missing.append(year)
    if len(frames)<2:raise ValueError('Fewer than two usable years; cannot create a time-lapse')
    b=io.BytesIO();frames[0].save(b,format='GIF',append_images=frames[1:],save_all=True,duration=1000,loop=0)
    return b.getvalue(),{'frames':records,'missing_years':missing,'limitations':'Aligned NDVI visualization, not natural-color footage. One clear scene per available year; season differences affect comparison. No simulated future.'}
