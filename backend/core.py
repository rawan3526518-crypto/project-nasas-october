"""Pure calculations. No fabricated agronomic observations."""
import json, math
from shapely.geometry import shape, box, mapping
from shapely.ops import transform

def finite(value):
    value=float(value)
    if not math.isfinite(value): raise ValueError('Non-finite number')
    return value

def project_polygon(geometry):
    g=shape(geometry)
    if g.geom_type!='Polygon' or not g.is_valid or g.is_empty: raise ValueError('حدود الأرض يجب أن تكون مضلعًا صحيحًا')
    if len(g.exterior.coords)>2000: raise ValueError('عدد نقاط الحدود كبير جدًا')
    x,y=g.centroid.x,g.centroid.y
    if not (-180<=x<=180 and -80<y<80): raise ValueError('إحداثيات غير صحيحة')
    kx=111320*math.cos(math.radians(y)); ky=110574
    p=transform(lambda a,b:((a-x)*kx,(b-y)*ky),g)
    if p.area<=1 or p.area>100_000_000: raise ValueError('المساحة خارج نطاق النموذج المحلي (١ م² إلى ١٠٠ كم²)')
    return p,lambda a,b:(a/kx+x,b/ky+y)

def split_field(geometry,ratio=.2):
    p,back=project_polygon(geometry)
    x0,y0,x1,y1=p.bounds;lo=x0;hi=x1
    for _ in range(50):
        mid=(lo+hi)/2
        if p.intersection(box(x0-1,y0-1,mid,y1+1)).area<p.area*ratio:lo=mid
        else:hi=mid
    trial=p.intersection(box(x0-1,y0-1,(lo+hi)/2,y1+1))
    return {'trial':mapping(transform(back,trial)),'control':mapping(transform(back,p.difference(trial))), 'area_m2':p.area,'trial_m2':trial.area,'ratio':trial.area/p.area,'method':'Local equirectangular approximation; not a cadastral survey'}

def water(eto,kc,rain,efficiency,area):
    eto,kc,rain,efficiency,area=map(finite,(eto,kc,rain,efficiency,area))
    if not(0<=eto<=30 and 0<kc<=2 and rain>=0 and 0<efficiency<=100 and area>0):raise ValueError('قيم حساب المياه غير صحيحة')
    return {'etc_mm_day':eto*kc,'gross_m3_day':max(0,eto*kc-rain)*area/1000/(efficiency/100),'scope':'Educational standard-condition estimate, excludes soil storage and salt leaching'}

def compare_trial(test,control):
    def calc(r):
        area,w,y=map(finite,(r['area'],r['water'],r['yield']))
        if area<=0 or w<0 or y<0:raise ValueError('قيم التجربة غير صحيحة')
        return {'yield_per_m2':y/area,'water_per_m2':w/area}
    a,b=calc(test),calc(control)
    return {'test':a,'control':b,'yield_change_pct':100*(a['yield_per_m2']/b['yield_per_m2']-1) if b['yield_per_m2'] else None,'water_reduction_pct':100*(1-a['water_per_m2']/b['water_per_m2']) if b['water_per_m2'] else None}

def seasonal_pack(farm,atlas,weather,plan):
    data=json.dumps({'version':1,'farm':farm,'atlas':atlas,'weather':weather,'plan':plan},ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
    if len(data)>=2_000_000:raise ValueError('حزمة الموسم تجاوزت ٢ ميجابايت')
    return data

def rank_candidates(farm,candidates,priority='water'):
    checked=[]
    for c in candidates:
        # Eligibility is only established by an explicitly reviewed local rule.
        errors=[]
        for key,field in [('soil','soils'),('season','seasons')]:
            if farm.get(key) not in c.get(field,[]):errors.append(key)
        if not c.get('source_url') or not c.get('reviewed_by'):errors.append('review')
        salinity=farm.get('ec_ds_m')
        if salinity is None or c.get('max_ec_ds_m') is None:errors.append('salinity_unknown')
        elif finite(salinity)>finite(c['max_ec_ds_m']):errors.append('salinity')
        repeated=farm.get('previous_family')==c.get('family')
        row={**c,'eligibility':'unknown' if any(e.endswith('unknown') or e=='review' for e in errors) else ('unsuitable' if errors else 'suitable'),'reasons':errors,'repeat_family':repeated}
        checked.append(row)
    return checked
