from math import hypot

import numpy as np
from PIL import Image,ImageDraw
from scipy.ndimage import gaussian_filter, sobel

from .artworks import linear_color
from .labels import font
from .model import InputError, find, new_id, number, pair, text, timestamp
from .photos import linear_to_srgb, luminance, srgb_to_linear, uint8_image
from .projection import (
    photo_to_wall, polygon_mask, register_view, shot_matrix, signed_area,
    uv_to_wall, wall_to_photo, wall_to_uv,
)
from .render import plot_cross
from .store import document, sha256
from .workspace import shot_description


GRID_COLUMNS = 6
GRID_ROWS = 4


def coverage_cells(support,foreground,clipping,signal):
    height,width = support.shape
    rows = []
    for row in range(GRID_ROWS):
        edges_y = round(height*row/GRID_ROWS), round(height*(row+1)/GRID_ROWS)
        for column in range(GRID_COLUMNS):
            edges_x = round(width*column/GRID_COLUMNS), round(width*(column+1)/GRID_COLUMNS)
            slices = (slice(*edges_y),slice(*edges_x))
            counts = support[slices]
            clear = counts & ~foreground[slices]
            values = signal[slices][clear]
            clipped = clipping[slices]&clear
            rows.append({'row':row+1,'column':column+1,
                         'coverage':round(float(counts.mean()),4),
                         'clear':round(float(clear.mean()),4),
                         'clipped':round(float(clipped.sum()/max(1,clear.sum())),4),
                         'edge_response':round(float(np.quantile(values,0.85)),5) if values.size else None})
    return rows


def capture_diagnostics(blobs, wall, shot):
    result = register_view(blobs,wall,shot,1100)
    luma = luminance(result.rgb)
    highlights = np.max(result.rgb,axis=2)>=float(srgb_to_linear(0.985))
    dark = luma<=float(srgb_to_linear(0.022))
    dx = sobel(luma,axis=1)*0.125
    dy = sobel(luma,axis=0)*0.125
    edge = np.hypot(dx,dy)
    edge = np.maximum(edge-0.001,0)
    coverage = coverage_cells(result.valid,result.foreground,highlights|dark,edge)
    linear = luma[result.clear]
    source_size = np.array([shot['photo']['width'],shot['photo']['height']])
    corners_source = np.asarray(shot['corners']) * source_size
    area = abs(signed_area(corners_source.tolist()))
    physical_area = wall['width']*wall['height']
    if linear.size:
        levels = [round(float(x),5) for x in np.quantile(linear,[0.05,0.5,0.95])]
    else:
        levels = []
    return {
        **shot_description(shot),
        'registered_size':list(result.size),
        'clear_fraction':round(float(result.clear.mean()),5),
        'supported_fraction':round(float(result.valid.mean()),5),
        'foreground_fraction':round(float((result.foreground & result.valid).mean()),5),
        'source_pixels_per_square_mm':round(area/physical_area,5),
        'clear_lightness_p05_p50_p95':levels,
        'cells':coverage,
    }


def capture_map(blobs,wall,shot,mode):
    registered = register_view(blobs,wall,shot,1040)
    rgb = registered.rgb.copy()
    support = registered.valid
    if mode=='detail':
        luma = luminance(rgb)
        local = np.abs(luma-gaussian_filter(luma,sigma=2))
        intensity = np.clip(local*10,0,1)
        shade = linear_color('#3d646c')
        highlight = linear_color('#e9d1a1')
        rgb = shade + intensity[...,None]*(highlight-shade)
    elif mode=='exposure':
        bright = np.max(rgb,axis=2)>=float(srgb_to_linear(0.985))
        dim = luminance(rgb)<=float(srgb_to_linear(0.022))
        rgb[bright] = linear_color('#e9a347')
        rgb[dim] = linear_color('#4a8bcb')
    rgb[registered.foreground] = rgb[registered.foreground]*0.3 + linear_color('#c49568')*0.7
    rgb[~support] = linear_color('#3e454b')
    out = uint8_image(linear_to_srgb(rgb)).convert('RGB')
    draw = ImageDraw.Draw(out)
    for column in range(1,GRID_COLUMNS):
        x = round(out.width*column/GRID_COLUMNS)
        draw.line((x,0,x,out.height),fill='#ddd0ad',width=1)
    for row in range(1,GRID_ROWS):
        y = round(out.height*row/GRID_ROWS)
        draw.line((0,y,out.width,y),fill='#ddd0ad',width=1)
    return out


def photograph_points(wall,shot,points):
    coordinates = photo_to_wall(wall,shot,points)
    if not np.isfinite(coordinates).all() or np.max(np.abs(coordinates))>500_000:
        raise InputError('A selected point is too far from the measured wall plane.')
    return coordinates


def ruler_measurement(wall,shot,start,end):
    a,b = photograph_points(wall,shot,[start,end])
    inside = lambda q: bool(0<=q[0]<=wall['width'] and 0<=q[1]<=wall['height'])
    distance = hypot(*(b-a))
    return {'photo_points':[start,end],'wall_points':np.round([a,b],2).tolist(),
            'inside':[inside(a),inside(b)],'length_mm':round(float(distance),2),
            'horizontal_delta':round(float(b[0]-a[0]),2),'vertical_delta':round(float(b[1]-a[1]),2),
            'shot':shot['name']}


def pixels_to_hex(pixels):
    channels = np.rint(linear_to_srgb(pixels)*255).astype(int)
    return '#'+''.join(f'{channel:02x}' for channel in channels)


def color_distance(first,second):
    first,second = np.asarray(first),np.asarray(second)
    return float(np.linalg.norm(first-second))


def paint_samples(blobs,wall,shot):
    captures = [swatch for swatch in wall['swatches'] if swatch['shot']==shot['id']]
    if not captures:
        return {'photo':shot['name'],'samples':[]}
    observed = register_view(blobs,wall,shot,1000)
    results = []
    for swatch in captures:
        area = polygon_mask(observed.size,[swatch['region']])
        clear = area & observed.clear
        colors = observed.rgb[clear]
        ratio = int(clear.sum())/max(1,int(area.sum()))
        if len(colors)<8:
            results.append({'id':swatch['id'],'name':swatch['name'],'measurable':False,
                            'clear_fraction':round(ratio,3),'reference':swatch['appearance']})
            continue
        med = np.median(colors,axis=0)
        base = linear_color(swatch['appearance'])
        sample_luma = luminance(colors)
        spread = np.quantile(sample_luma,[0.05,0.5,0.95])
        results.append({'id':swatch['id'],'name':swatch['name'],'measurable':True,
                        'clear_fraction':round(ratio,3),'reference':swatch['appearance'],
                        'observed':pixels_to_hex(med),'linear_rgb_gap':round(color_distance(med,base),4),
                        'luminance_range':np.round(spread,4).tolist(),
                        'median_sample_linear':np.round(med,5).tolist(),'pixels':len(colors)})
    return {'photo':shot['name'],'samples':results}


def paint_sample_map(blobs,wall,shot):
    output = register_view(blobs,wall,shot,1060).image().convert('RGB')
    results = {r['id']:r for r in paint_samples(blobs,wall,shot)['samples']}
    draw = ImageDraw.Draw(output)
    for source in wall['swatches']:
        if source['id'] not in results:
            continue
        polygon = [tuple(p) for p in np.asarray(source['region'])*np.asarray(output.size)]
        draw.line([*polygon,polygon[0]],fill='#fff3b6',width=3)
        x,y = polygon[0]
        draw.text((x+4,y+4),source['name'],font=font(14,heading=True),fill='#1e1d1a',stroke_width=2,stroke_fill='#fff3b6')
    return output


def add_observation(project, layout_id, payload):
    from .layout import Scene

    scene = Scene.build(project,layout_id)
    shot = find(scene.wall['shots'],payload.get('shot'),'Site photograph')
    norm = pair(payload.get('point'),'Observation dot',lower=0,upper=1)
    xy = photograph_points(scene.wall,shot,[norm])[0]
    if not 0<=xy[0]<=scene.wall['width'] or not 0<=xy[1]<=scene.wall['height']:
        raise InputError('Pin the observation on the measured area of the wall.')
    record = {'id':new_id(),'shot':shot['id'],'source_digest':shot['photo']['source_digest'],
              'source_corners':shot['corners'],'photo_point':norm,'wall_point':np.round(xy,4).tolist(),
              'title':text(payload.get('title',''), 'Observation title'),
              'note':text(payload.get('note',''),'Observation notes',maximum=1000,required=False),
              'created':timestamp(),'scene_signature':scene.reference_identity()}
    if len(scene.layout['observations'])>=75:
        raise InputError('This layout already has 75 site observations.')
    scene.layout['observations'].append(record)
    return record, f"Pinned observation {record['title']}"


def remove_observation(project, layout_id, observation_id):
    from .layout import Scene
    scene = Scene.build(project,layout_id)
    target = find(scene.layout['observations'],observation_id,'Site observation')
    scene.layout['observations'].remove(target)
    return observation_id, f"Removed observation {target['title']}"


def observation_overlay(blobs,scene,shot,*,edge=1200):
    with Image.open(blobs.require(shot['photo']['preview_digest'])) as source:
        photo = source.convert('RGB')
    photo.thumbnail((edge,edge),Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(photo)
    for observation in scene.layout['observations']:
        fixed_x,fixed_y = wall_to_photo(scene.wall,shot,[observation['wall_point']])[0]
        if not 0<=fixed_x<=1 or not 0<=fixed_y<=1:
            continue
        x,y = fixed_x*photo.width,fixed_y*photo.height
        same = observation['shot']==shot['id'] and observation['source_corners']==shot['corners']
        tone = '#b5efd7' if same else '#ffcb92'
        plot_cross(draw,(x,y),8,'#28231c',width=5)
        plot_cross(draw,(x,y),8,tone,width=2)
        draw.text((x+13,y-8),observation['title'],font=font(14,heading=True),fill=tone,stroke_width=2,stroke_fill='#26251e')
    return photo


def observation_report(scene,shot):
    left = scene.wall['width']
    height = scene.wall['height']
    original_sources = {view['id']:view for view in scene.wall['shots']}
    output = []
    for record in scene.layout['observations']:
        origin = original_sources.get(record['shot'])
        original = origin is not None and origin['photo']['source_digest']==record['source_digest']
        aligned = original and origin['corners']==record['source_corners']
        x,y = record['wall_point']
        inside = 0<=x<=left and 0<=y<=height
        projected = wall_to_photo(scene.wall,shot,[[x,y]])[0] if inside else None
        projected_inside = projected is not None and np.isfinite(projected).all() and np.all((projected>=0)&(projected<=1))
        state = 'same photograph' if shot['id']==record['shot'] and aligned else 'projected from wall coordinates'
        output.append({**record,'original_view':None if origin is None else origin['name'],
                       'source_unchanged':bool(aligned),'inside_measured_wall':inside,
                       'in_active_view':bool(projected_inside),'status':state,
                       'projection':np.round(projected,6).tolist() if projected_inside else None})
    return {'active_view':shot['name'],'observations':output,
            'visible_count':sum(row['in_active_view'] for row in output),'count':len(output)}
