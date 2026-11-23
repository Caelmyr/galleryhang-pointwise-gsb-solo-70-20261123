from math import hypot

from .adoption import plan_status
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


def attachment_points(wall, placement, artwork, tolerance, reuse=None):
    designs = source_attachment(artwork)
    if not designs:
        return []
    coordinates = local_to_wall(placement, [[x,y] for _,x,y in designs])
    points = []
    for index, ((name,local_x,local_y), (x,y)) in enumerate(zip(designs,coordinates)):
        restricted = restricted_at(wall,x,y)
        inside = bool(0 <= x <= wall['width'] and 0 <= y <= wall['height'])
        marks = nearby_marks(wall,x,y,tolerance)
        adoption = None
        if reuse is not None:
            adoption = reuse_for_point(reuse, name)
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
            'reuse':adoption,
        })
    return points


def reuse_for_point(reuse, point_name):
    """Match an adopted hole to a fixing point strictly by stored identity.

    Only the hole recorded for this exact point is returned.  If the point is
    not in the adopted assignment, ``None`` is returned rather than falling
    back to whatever old hole happens to lie nearest now.
    """
    if reuse is None:
        return None
    for hole in reuse.get('holes', []):
        if hole.get('point') == point_name:
            return {'number': hole['number'], 'name': hole.get('name'), 'x': hole['x'], 'y': hole['y'],
                    'state': hole.get('state'), 'confirmed': reuse['status'] == 'confirmed',
                    'status': reuse['status'], 'reasons': reuse.get('reasons', [])}
    return None


def work_order(scene):
    ordered = sorted(scene.works(), key=lambda pair: (pair[0]['x'], -pair[0]['y'], pair[0]['id']))
    return [(index+1,placement,artwork) for index,(placement,artwork) in enumerate(ordered)]


def mounting_report(scene,tolerance=18):
    status = plan_status(scene)
    reuse_by_placement = {}
    if status['present']:
        for row in status['assignments']:
            reuse_by_placement[row['placement']] = {
                'title': row['title'], 'status': row['status'], 'reasons': row.get('reasons', []),
                'shift_mm': row.get('shift_mm'), 'holes': row.get('holes', []),
            }
    records = []
    for index,placement,artwork in work_order(scene):
        points = attachment_points(scene.wall,placement,artwork,tolerance,
                                   reuse_by_placement.get(placement['id']))
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
            'reuse_status':reuse_by_placement.get(placement['id'], {}).get('status'),
            'needs_field_check':not points or any(not p['inside'] or p['restricted'] for p in points),
        })
    return {'wall':scene.wall['name'],'origin':{'left':scene.wall['datum'][0],'from_floor':scene.wall['datum'][1]},
            'wall_size':[scene.wall['width'],scene.wall['height']],'unit':'mm','tolerance':tolerance,
            'work_count':len(records),'point_count':sum(len(row['points']) for row in records),
            'work_without_verified_clearance':sum(row['needs_field_check'] for row in records),
            'reuse':reuse_summary(status),'works':records}


def reuse_summary(status):
    if not status['present']:
        return None
    return {'plan':status['id'],'created':status['created'],
            'tolerance_mm':status['tolerance_mm'],'max_shift_mm':status['max_shift_mm'],
            'summary':status['summary'],
            'assignments':[{'placement':row['placement'],'title':row['title'],
                            'status':row['status'],'reasons':row.get('reasons', []),
                            'shift_mm':row.get('shift_mm'),
                            'holes':[{'number':hole['number'],'name':hole.get('name'),
                                      'x':hole['x'],'y':hole['y'],'point':hole.get('point'),
                                      'side':hole.get('side'),'state':hole.get('state')}
                                     for hole in row.get('holes', [])]}
                           for row in status['assignments']]}


def mounting_csv(report):
    headers = [
        'order','artwork','catalog_number','weight_kg','attachment_type','point',
        'wall_x_mm','wall_y_mm','datum_x_mm','datum_y_mm','object_back_x_mm','object_back_y_mm',
        'field_check','nearest_existing_mark','nearest_existing_mark_gap_mm',
        'adopted_hole_number','adopted_hole_name','adopted_hole_x_mm','adopted_hole_y_mm','reuse_status',
    ]
    rows = []
    for work in report['works']:
        base = [work['index'],work['title'],work['catalog_number'],work['weight'],work['mount']]
        if not work['points']:
            rows.append([*base,None,None,None,None,None,None,None,'attachment not measured',None,None,
                         None,None,None,None,None])
        for point in work['points']:
            nearby = point['nearby_mark']
            adopted = point.get('reuse')
            rows.append([
                *base,point['name'],point['wall_x'],point['wall_y'],point['datum_x'],point['datum_y'],
                point['back_x'],point['back_y'],'review at site' if point['restricted'] or not point['inside'] else '',
                nearby['name'] if nearby else None,nearby['distance'] if nearby else None,
                adopted['number'] if adopted else None,
                adopted['name'] if adopted else None,
                adopted['x'] if adopted else None,
                adopted['y'] if adopted else None,
                adopted['status'] if adopted else None,
            ])
    return headers,rows
