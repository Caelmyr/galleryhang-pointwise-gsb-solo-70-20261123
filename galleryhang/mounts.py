from math import hypot

from .clearance import obstacle_outline
from .layout import artwork_bounds, local_to_wall


ATTACHMENT_NAMES = {'center':'Center point', 'pair':'Two attachment points', 'rail':'Rail endpoints', 'none':'No measured attachment'}


def source_attachment(artwork):
    if artwork['attachment']=='none':
        return []
    vertical = artwork['height']/2-artwork['fixing_top']
    if artwork['attachment']=='center':
        return [('center',0.0,vertical)]
    side = artwork['width']*artwork['fixing_span']/2
    if artwork['attachment']=='rail':
        return [('rail left',-side,vertical),('rail right',side,vertical)]
    return [('left',-side,vertical),('right',side,vertical)]


def nearby_marks(wall, x, y, tolerance):
    candidates = []
    for mark in wall['mount_marks']:
        if not mark['usable'] or mark['kind'] == 'reference':
            continue
        distance = hypot(x-mark['x'], y-mark['y'])
        if distance <= tolerance:
            candidates.append({'id':mark['id'],'name':mark['name'],'type':mark['kind'],
                               'x':mark['x'],'y':mark['y'],'distance':round(distance,2)})
    candidates.sort(key=lambda m:(m['distance'],m['name'],m['id']))
    return candidates


def restricted_at(wall,x,y):
    hits = []
    for obstacle in wall['obstacles']:
        polygon = obstacle_outline(obstacle, margin=obstacle['clearance'])
        if polygon[0,0] <= x <= polygon[1,0] and polygon[2,1] <= y <= polygon[0,1]:
            hits.append({'name':obstacle['name'],'kind':obstacle['kind'],'id':obstacle['id']})
    if y <= wall['baseboard']:
        hits.append({'name':'Baseboard','kind':'fixture'})
    return hits


def attachment_points(wall, placement, artwork, tolerance):
    designs = source_attachment(artwork)
    if not designs:
        return []
    coordinates = local_to_wall(placement, [[x,y] for _,x,y in designs])
    points = []
    for (name,local_x,local_y), (x,y) in zip(designs,coordinates):
        restricted = restricted_at(wall,x,y)
        inside = bool(0 <= x <= wall['width'] and 0 <= y <= wall['height'])
        marks = nearby_marks(wall,x,y,tolerance)
        points.append({
            'name':name,
            'wall_x':round(float(x),2),
            'wall_y':round(float(y),2),
            'datum_x':round(float(x-wall['datum'][0]),2),
            'datum_y':round(float(y-wall['datum'][1]),2),
            'back_x':round(float(local_x+artwork['width']/2),2),
            'back_y':round(float(artwork['height']/2-local_y),2),
            'inside':inside,
            'restricted':restricted,
            'nearby_mark':marks[0] if marks else None,
            'mark_options':marks,
        })
    return points


def work_order(scene):
    ordered = sorted(scene.works(), key=lambda pair: (pair[0]['x'], -pair[0]['y'], pair[0]['id']))
    return [(index+1,placement,artwork) for index,(placement,artwork) in enumerate(ordered)]


def mounting_report(scene,tolerance=18):
    records = []
    for index,placement,artwork in work_order(scene):
        points = attachment_points(scene.wall,placement,artwork,tolerance)
        bounds = artwork_bounds(placement,artwork)
        records.append({
            'index':index,
            'placement':placement['id'],
            'title':artwork['title'],
            'artist':artwork['artist'],
            'catalog_number':artwork['inventory'],
            'outside_size':[artwork['width'],artwork['height']],
            'bounds':[round(value,2) for value in bounds],
            'artwork_center':[round(placement['x'],2),round(placement['y'],2)],
            'rotation':placement['angle'],
            'weight':artwork['weight'],
            'mount':ATTACHMENT_NAMES[artwork['attachment']],
            'pinned':placement['pinned'],
            'points':points,
            'needs_field_check':not points or any(not p['inside'] or p['restricted'] for p in points),
        })
    return {'wall':scene.wall['name'],'origin':{'left':scene.wall['datum'][0],'from_floor':scene.wall['datum'][1]},
            'wall_size':[scene.wall['width'],scene.wall['height']],'unit':'mm','tolerance':tolerance,
            'work_count':len(records),'point_count':sum(len(row['points']) for row in records),
            'work_without_verified_clearance':sum(row['needs_field_check'] for row in records),'works':records}


def mounting_csv(report):
    headers = [
        'order','artwork','catalog_number','weight_kg','attachment_type','point',
        'wall_x_mm','wall_y_mm','datum_x_mm','datum_y_mm','object_back_x_mm','object_back_y_mm',
        'field_check','nearest_existing_mark','nearest_existing_mark_gap_mm',
    ]
    rows = []
    for work in report['works']:
        base = [work['index'],work['title'],work['catalog_number'],work['weight'],work['mount']]
        if not work['points']:
            rows.append([*base,None,None,None,None,None,None,None,'attachment not measured',None,None])
        for point in work['points']:
            nearby = point['nearby_mark']
            rows.append([
                *base,point['name'],point['wall_x'],point['wall_y'],point['datum_x'],point['datum_y'],
                point['back_x'],point['back_y'],'review at site' if point['restricted'] or not point['inside'] else '',
                nearby['name'] if nearby else None,nearby['distance'] if nearby else None,
            ])
    return headers,rows
