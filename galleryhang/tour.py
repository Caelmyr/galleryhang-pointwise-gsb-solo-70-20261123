from itertools import pairwise
from math import atan2, cos, degrees, hypot, radians, sin, tan

import numpy as np
from PIL import Image,ImageDraw

from .labels import font
from .layout import artwork_bounds
from .model import InputError, boolean, find, new_id, number, sequence, text
from .viewing import viewpoint_report


MAX_ROUTES = 12
PLATE_BG = '#f2ecdf'
INK = '#2f3028'
MUTED = '#80786b'
ACCENT = '#b65c37'


def normalized_route(layout, payload, existing=None):
    available = layout['stations']
    if len(available)<1:
        raise InputError('Save at least one viewpoint before building a visit sequence.')
    base = existing or {'name':'Gallery walkthrough','note':'','closed':False,'stops':[]}
    stops = sequence(payload.get('stops',base['stops']),'Viewpoint sequence',minimum=1,maximum=12)
    clean = []
    for stop in stops:
        if not isinstance(stop,dict):
            raise InputError('Each stop needs a viewpoint and a time.')
        station = find(available,stop.get('station'),'Viewing position')
        dwell = number(stop.get('dwell',30),'Stop duration in seconds',lower=0,upper=1200)
        clean.append({'station':station['id'],'dwell':dwell,
                      'focus':text(stop.get('focus',''),'Stop introduction',maximum=350,required=False)})
    return {'name':text(payload.get('name',base['name']),'Visit sequence name'),
            'note':text(payload.get('note',base['note']),'Routing notes',maximum=600,required=False),
            'closed':boolean(payload.get('closed',base['closed']),'Return to the first stop'),'stops':clean}


def edit_route(scene,payload,identifier=None):
    routes = scene.layout.setdefault('visit_sequences',[])
    existing = find(routes,identifier,'Visit sequence') if identifier else None
    fields = normalized_route(scene.layout,payload,existing)
    if existing:
        existing.update(fields)
        result = existing
    else:
        if len(routes)>=MAX_ROUTES:
            raise InputError(f'This layout may hold at most {MAX_ROUTES} visit sequences.')
        result = {'id':new_id(),**fields}
        routes.append(result)
    return result,f"Saved visit sequence {result['name']}"


def remove_route(scene,identifier):
    routes = scene.layout.get('visit_sequences',[])
    found = find(routes,identifier,'Visit sequence')
    routes.remove(found)
    return identifier,f"Removed visit sequence {found['name']}"


def resolve_stops(scene, route):
    stops = []
    for index,entry in enumerate(route['stops'],1):
        station = find(scene.layout['stations'],entry['station'],'Viewing position')
        stops.append({'index':index,'station':station,'dwell':entry['dwell'],'focus':entry['focus']})
    return stops


def station_floor_point(station):
    return np.asarray([station['x'],station['distance']], dtype=float)


def footprint_lines(station):
    x,z = station_floor_point(station)
    yaw = atan2(station['target_x']-x,z)
    half_fov = radians(station['fov']/2)
    rays = []
    for direction in (yaw-half_fov,yaw,yaw+half_fov):
        if abs(direction) >= radians(89):
            distance = max(z*1.65,750.0)
        else:
            distance = min(z/cos(direction),max(z*1.65,750.0))
        endpoint = [x+sin(direction)*distance,z-cos(direction)*distance]
        rays.append(endpoint)
    return rays


def route_legs(stops,closed):
    targets = stops+[stops[0]] if closed and len(stops)>1 else stops
    legs = []
    for a,b in pairwise(targets):
        first,second = station_floor_point(a['station']),station_floor_point(b['station'])
        span = second-first
        distance = hypot(*span)
        bearing = degrees(atan2(float(span[0]),float(-span[1]))) if distance else 0.0
        legs.append({'from':a['station']['name'],'to':b['station']['name'],
                     'a':first.tolist(),'b':second.tolist(),'distance_mm':round(distance,2),
                     'wall_relative_bearing':round(bearing,1)})
    return legs


def tour_coverage(scene,stops):
    sightings = {}
    individual = []
    cached = {}
    for stop in stops:
        station = stop['station']
        station_id = station['id']
        if station_id not in cached:
            cached[station_id] = viewpoint_report(scene,station)
        report = cached[station_id]
        visible = []
        for item in report['works']:
            per_work = sightings.setdefault(item['placement'],{'name':item['name'],'strongest':0.0,'found_at':[]})
            per_work['strongest'] = max(per_work['strongest'],item['clear_in_view'])
            if item['clear_in_view']>=0.995:
                per_work['found_at'].append(stop['index'])
                visible.append(item['name'])
        individual.append({'stop':stop['index'],'name':station['name'],'focus':stop['focus'],'duration':stop['dwell'],
                           'fully_visible':report['works_fully_visible'],'partially_visible':report['works_partially_visible'],
                           'titles':visible})
    return individual,list(sightings.values())


def tour_report(scene,route, pace=1100):
    stops = resolve_stops(scene,route)
    legs = route_legs(stops,route.get('closed',False))
    sum_distance = sum(leg['distance_mm'] for leg in legs)
    visit_time = sum(stop['dwell'] for stop in stops)
    individuals,works = tour_coverage(scene,stops)
    inside = [entry['name'] for entry in works if entry['found_at']]
    partial = [entry['name'] for entry in works if not entry['found_at'] and entry['strongest']>0]
    missing = [entry['name'] for entry in works if entry['strongest']==0]
    return {'name':route['name'],'stops':individuals,'legs':legs,'distance_mm':round(sum_distance,2),
            'walking_seconds':round(sum_distance/pace,1),'viewing_seconds':round(visit_time,1),
            'estimated_seconds':round(visit_time+sum_distance/pace,1),'whole_works':inside,
            'partial_only_works':partial,'out_of_view_works':missing,'works':works}


def chart_limits(scene,stops):
    station_values = [station_floor_point(entry['station']) for entry in stops]
    rays = [p for entry in stops for p in footprint_lines(entry['station'])]
    shape = np.array([[0,0],[scene.wall['width'],0],*station_values,*rays])
    minimum,maximum = np.min(shape,axis=0),np.max(shape,axis=0)
    minimum[1] = 0
    span = np.maximum(maximum-minimum,[500,500])
    return minimum,span


def draw_tour(scene,route):
    stops = resolve_stops(scene,route)
    minimum,span = chart_limits(scene,stops)
    width,height = 1120,760
    margin_x,margin_top,margin_bottom = 54,96,72
    scale = min((width-2*margin_x)/span[0],(height-margin_top-margin_bottom)/span[1])
    x_shift = (width-span[0]*scale)/2
    background = Image.new('RGB',(width,height),PLATE_BG)
    draw = ImageDraw.Draw(background)

    def project(xz):
        x,z = (np.asarray(xz)-minimum)*scale
        return x+x_shift,z+margin_top

    charts_caption(draw,scene,route,width,height)
    draw_floor_grid(draw,project,span,minimum)
    draw.line((project([0,0]),project([scene.wall['width'],0])),fill=INK,width=7)
    for placement,artwork in scene.works():
        left,_,right,_ = artwork_bounds(placement,artwork)
        first,second = project([left,0]),project([right,0])
        draw.line((first,second),fill=ACCENT,width=10)
        x = (first[0]+second[0])/2
        draw.text((x,first[1]-16),artwork['title'][:28],font=font(12),fill=INK,anchor='mb')
    for fixture in scene.wall['obstacles']:
        if fixture['depth']<=0:
            continue
        a = project([fixture['left'],0])
        b = project([fixture['left']+fixture['width'],fixture['depth']])
        draw.rectangle((*a,*b),outline=MUTED,fill='#d8cbbc',width=2)
    rays = Image.new('RGBA',background.size)
    draw_rays = ImageDraw.Draw(rays)
    for stop in stops:
        station = stop['station']
        origin = project(station_floor_point(station))
        left,center,right = [project(point) for point in footprint_lines(station)]
        draw_rays.polygon((origin,left,right),fill=(109,114,91,25))
        draw_rays.line((origin,center),fill=(93,88,62,100),width=1)
    background = Image.alpha_composite(background.convert('RGBA'),rays).convert('RGB')
    draw = ImageDraw.Draw(background)
    legs = route_legs(stops,route.get('closed',False))
    for leg in legs:
        a,b = project(leg['a']),project(leg['b'])
        draw.line((a,b),fill=ACCENT,width=4)
        mx,my = (a[0]+b[0])*0.5,(a[1]+b[1])*0.5
        draw.text((mx,my-10),f"{leg['distance_mm']/1000:.1f} m",font=font(14,heading=True),anchor='mb',
                  fill=INK,stroke_width=2,stroke_fill=PLATE_BG)
    for stop in stops:
        x,y = project(station_floor_point(stop['station']))
        draw.ellipse((x-13,y-13,x+13,y+13),fill=INK,outline=PLATE_BG,width=2)
        draw.text((x,y),str(stop['index']),font=font(15,heading=True),fill=PLATE_BG,anchor='mm')
        draw.text((x,y+20),stop['station']['name'],font=font(13),fill=INK,anchor='mt',stroke_width=2,stroke_fill=PLATE_BG)
    return background


def charts_caption(draw,scene,route,width,height):
    draw.text((35,28),route['name'],font=font(26,heading=True),fill=INK)
    draw.text((width-35,35),scene.wall['name']+' · floor view',font=font(17),fill=MUTED,anchor='ra')
    draw.line((35,height-48,width-35,height-48),fill='#c9c0b0',width=1)
    draw.text((35,height-34),'Measured wall and saved viewing positions · physical obstacles shown at their recorded depth',
              font=font(13),fill=MUTED)


def draw_floor_grid(draw,project,span,minimum):
    step = 500 if max(span)<7000 else 1000 if max(span)<15_000 else 2500
    y_max = int(span[1])+step//2
    x_min = int(minimum[0]//step)*step
    x_max = int(minimum[0]+span[0])
    for x in range(x_min,x_max+1,step):
        draw.line((project([x,0]),project([x,y_max])),fill='#d8d2c7',width=1)
    for y in range(step,y_max+1,step):
        draw.line((project([x_min,y]),project([x_max,y])),fill='#d8d2c7',width=1)
        x_px,y_px = project([x_min,y])
        draw.text((x_px+3,y_px-2),f'{y/1000:g} m',font=font(12),fill=MUTED,anchor='lb')
