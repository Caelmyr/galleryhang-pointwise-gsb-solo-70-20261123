from itertools import combinations
from math import atan2, degrees, hypot

import numpy as np

from .layout import footprint, label_footprint, rect_bounds


EPS = 1e-6


def rectangle(left,bottom,right,top):
    return np.array([[left,top],[right,top],[right,bottom],[left,bottom]],dtype=np.float64)


def obstacle_outline(obstacle, *, margin=0):
    left,bottom = obstacle['left'],obstacle['bottom']
    return rectangle(left-margin,bottom-margin,left+obstacle['width']+margin,bottom+obstacle['height']+margin)


def area(polygon):
    points = np.asarray(polygon)
    if len(points)<3:
        return 0.0
    shifted = np.roll(points,-1,axis=0)
    return abs(float(np.sum(points[:,0]*shifted[:,1]-points[:,1]*shifted[:,0])))/2


def polygon_center(polygon):
    points = np.asarray(polygon)
    return np.mean(points,axis=0)


def boundary_axis(point,axis,value,keep_positive):
    delta = point[axis]-value
    return delta >= -EPS if keep_positive else delta <= EPS


def intersection_on_axis(first,second,axis,value):
    span = second[axis]-first[axis]
    if abs(span) < EPS:
        return first.copy()
    ratio = (value-first[axis]) / span
    crossing = first + ratio*(second-first)
    crossing[axis] = value
    return crossing


def crop_polygon(polygon, axis, value, keep_positive):
    if not len(polygon):
        return []
    result = []
    prior = polygon[-1]
    prior_inside = boundary_axis(prior,axis,value,keep_positive)
    for point in polygon:
        inside = boundary_axis(point,axis,value,keep_positive)
        if inside != prior_inside:
            result.append(intersection_on_axis(prior,point,axis,value))
        if inside:
            result.append(point)
        prior = point
        prior_inside = inside
    return result


def intersection_rect(polygon,bounds):
    left,bottom,right,top = bounds
    result = list(np.asarray(polygon,dtype=np.float64))
    for axis,value,positive in ((0,left,True),(0,right,False),(1,bottom,True),(1,top,False)):
        result = crop_polygon(result,axis,value,positive)
    return result


def edge_pairs(polygon):
    for idx in range(len(polygon)):
        yield polygon[idx],polygon[(idx+1) % len(polygon)]


def candidate_axes(polygon):
    for start,end in edge_pairs(polygon):
        diff = end-start
        yield np.array([diff[1],-diff[0]])


def polygons_intersect(a,b):
    for polygon in (a,b):
        for direction in candidate_axes(polygon):
            a_project = a@direction
            b_project = b@direction
            if a_project.max() + EPS < b_project.min() or b_project.max() + EPS < a_project.min():
                return False
    return True


def point_segment(point, start, end):
    difference = end-start
    norm = float(difference@difference)
    if norm < EPS:
        return start
    position = float((point-start)@difference) / norm
    return start + difference * min(1,max(0,position))


def nearest_points(first,second):
    if polygons_intersect(first,second):
        return 0.0, None, None
    best = float('inf')
    pair = (None,None)
    for points, edges, reverse in ((first,second,False),(second,first,True)):
        for point in points:
            for start,end in edge_pairs(edges):
                closest = point_segment(point,start,end)
                distance = float(np.linalg.norm(point-closest))
                if distance < best:
                    best = distance
                    pair = (closest,point) if reverse else (point,closest)
    return best, pair[0], pair[1]


def wall_edge_distances(points,wall):
    left,bottom,right,top = rect_bounds(points)
    return {
        'left':left,
        'floor':bottom,
        'right':wall['width']-right,
        'ceiling':wall['height']-top,
        'above_baseboard':bottom-wall['baseboard'],
    }


def snap_for_output(vector):
    return [round(float(number),2) for number in vector]


def coordinate_lookup(scene):
    work = [{'id':placement['id'],'name':artwork['title'],'kind':'artwork',
             'key':artwork['inventory'], 'corners':footprint(placement,artwork)}
            for placement,artwork in scene.works()]
    labels = [{'id':label['id'],'name':label['name'] or 'Artwork label','kind':'label',
               'key':'', 'corners':label_footprint(label)} for label in scene.layout['labels']]
    return work + labels


def overlap_kind(first,second,distance,desired):
    if distance <= EPS:
        return 'touching or overlapping'
    if distance < desired:
        return 'short gap'
    return None


def item_checks(item,wall):
    outline = item['corners']
    distances = wall_edge_distances(outline,wall)
    walls = [side for side,space in distances.items() if side != 'above_baseboard' and space < -EPS]
    obstructed = []
    if distances['above_baseboard'] < -EPS:
        obstructed.append({'name':'Baseboard','kind':'fixture','clearance':0.0,
                           'distance':round(max(0.0,distances['above_baseboard']),2),
                           'area_mm2':round(area(intersection_rect(outline,[0,0,wall['width'],wall['baseboard']])),2)})
    for obstacle in wall['obstacles']:
        obstacle_poly = obstacle_outline(obstacle)
        measured,_,_ = nearest_points(outline,obstacle_poly)
        padding = obstacle['clearance']
        if measured <= max(padding,EPS):
            clipped_area = area(intersection_rect(outline,rect_bounds(obstacle_poly)))
            obstructed.append({'id':obstacle['id'],'name':obstacle['name'],'kind':obstacle['kind'],
                               'clearance':round(padding,2),'distance':round(measured,2),
                               'area_mm2':round(clipped_area,2)})
    cropped = area(intersection_rect(outline,[0,0,wall['width'],wall['height']]))
    fraction = min(1,cropped/max(area(outline),EPS))
    return {'id':item['id'],'name':item['name'],'kind':item['kind'],'key':item['key'],
            'corners':[snap_for_output(xy) for xy in outline], 'outside':walls,
            'wall_gaps':{side:round(value,2) for side,value in distances.items()},
            'visible_wall_fraction':round(fraction,4), 'obstructions':obstructed,
            'center':snap_for_output(polygon_center(outline))}


def bounds_distance(a,b):
    left_a,bottom_a,right_a,top_a = a
    left_b,bottom_b,right_b,top_b = b
    return hypot(max(0,left_a-right_b,left_b-right_a),max(0,bottom_a-top_b,bottom_b-top_a))


def clearance_report(scene):
    parts = coordinate_lookup(scene)
    rows = [item_checks(item,scene.wall) for item in parts]
    for item in parts:
        item['bounds'] = rect_bounds(item['corners'])
    pairs = []
    desired = scene.layout['gap']
    for left,right in combinations(parts,2):
        if left['kind'] != right['kind']:
            limit = min(desired,scene.layout['label_gap'])
        else:
            limit = desired
        if bounds_distance(left['bounds'],right['bounds']) > max(limit,EPS):
            continue
        distance,first,second = nearest_points(left['corners'],right['corners'])
        kind = overlap_kind(left,right,distance,limit)
        if kind:
            pairs.append({'kind':kind,'first':left['id'],'second':right['id'],
                          'first_name':left['name'],'second_name':right['name'],'distance':round(distance,2),
                          'target':round(limit,2),'at':None if first is None else
                          [snap_for_output(first),snap_for_output(second)]})
    held_labels = []
    for label in scene.layout['labels']:
        blocked = label.get('follow_blocked')
        if blocked:
            held_labels.append({
                'label': label['id'],
                'name': label['name'] or 'Artwork label',
                'placement': blocked['placement'],
                'attempted_mm': blocked['attempted_mm'],
                'blockers': blocked['blockers'],
            })
    return {'wall':scene.wall['name'],'unit':'mm','items':rows,'pairs':pairs,
            'held_labels': held_labels,
            'summary':{'off_wall':sum(bool(row['outside']) for row in rows),
                       'obstacle_conflicts':sum(bool(row['obstructions']) for row in rows),
                       'overlaps':sum(p['distance']==0 for p in pairs),
                       'short_gaps':sum(p['distance']>0 for p in pairs),
                       'labels_held':len(held_labels)}}


def wall_hint(scene, from_point, to_point):
    a,b = np.asarray(from_point),np.asarray(to_point)
    dx,dy = b-a
    return {'length':round(hypot(dx,dy),2),'horizontal':round(float(dx),2),'vertical':round(float(dy),2),
            'inclination':round(degrees(atan2(dy,dx)),2),
            'from_datum':snap_for_output(a-np.asarray(scene.wall['datum'])),
            'to_datum':snap_for_output(b-np.asarray(scene.wall['datum']))}
