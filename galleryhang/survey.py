from math import acos, atan2, degrees, hypot

import numpy as np
from PIL import Image,ImageDraw

from .capture_review import photograph_points
from .clearance import area, intersection_rect
from .labels import font
from .layout import Scene, footprint, local_rectangle
from .model import InputError, choice, find, new_id, number, optional_id, points, text, timestamp
from .projection import calibrated_quad, signed_area, wall_to_photo


MAX_SURVEYS = 60
MAX_READINGS = 6
CONDITIONS = ('as installed','after adjustment','reference check')
CORNER_NAMES = ('top-left','top-right','bottom-right','bottom-left')
EDGE_NAMES = ('top','right','bottom','left')
BLOCKED = {'none':None,'top-left':0,'top-right':1,'bottom-right':2,'bottom-left':3}


def measured_edges(points):
    vectors = np.roll(points,-1,axis=0)-points
    edges = np.linalg.norm(vectors,axis=1)
    return vectors,edges


def right_angle_error(vectors,edges):
    offsets = []
    for index,point in enumerate(vectors):
        next_index = (index+1)%len(vectors)
        denom = edges[index]*edges[next_index]
        if denom<1e-6:
            raise InputError('The marked sides must enclose a visible artwork.')
        similarity = np.dot(point,-vectors[next_index])/denom
        angle = degrees(acos(float(np.clip(similarity,-1,1))))
        offsets.append(angle-90)
    return offsets


def normalize_angle(angle):
    return (angle+180)%360-180


def measured_shape(points):
    vectors,edges = measured_edges(points)
    angle_errors = right_angle_error(vectors,edges)
    combined = vectors[0]-vectors[2]
    skew = normalize_angle(degrees(atan2(float(combined[1]),float(combined[0]))))
    center = (np.asarray(points)[0]+np.asarray(points)[2]+np.asarray(points)[1]+np.asarray(points)[3])/4
    return {
        'center':np.round(center,4).tolist(),
        'width':round((edges[0]+edges[2])*0.5,4),
        'height':round((edges[1]+edges[3])*0.5,4),
        'angle':round(skew,4),
        'edges':np.round(edges,4).tolist(),
        'non_rectangularity_deg':round(max(abs(value) for value in angle_errors),4),
        'non_rectangularity_mm':round(float(max(abs(edges[0]-edges[2]),abs(edges[1]-edges[3]))),4),
    }


def verify_frontal_support(wall,polygon):
    full = area(polygon)
    if full<80:
        raise InputError('Use a larger visible outline for the installed artwork.')
    overlap = area(intersection_rect(polygon,[0,0,wall['width'],wall['height']]))
    return min(1,overlap/full)


def planned_shape(placement,artwork):
    corners = footprint(placement,artwork)
    return {'artwork':artwork['id'],'title':artwork['title'],'center':[placement['x'],placement['y']],
            'angle':placement['angle'],'width':artwork['width'],'height':artwork['height'],
            'wall_corners':np.round(corners,4).tolist()}


def error_magnitudes(measured,expected):
    x_shift = measured['center'][0]-expected['center'][0]
    y_shift = measured['center'][1]-expected['center'][1]
    return {'delta_x':round(x_shift,2),'delta_y':round(y_shift,2),
            'center_error':round(hypot(x_shift,y_shift),2),
            'width_error':round(measured['width']-expected['width'],2),
            'height_error':round(measured['height']-expected['height'],2),
            'rotation_error':round(normalize_angle(measured['angle']-expected['angle']),2)}


def geometry_thresholds(payload):
    return {
        'center':number(payload.get('center_tolerance',20),'Center tolerance in mm',lower=1,upper=500),
        'rotation':number(payload.get('angle_tolerance',1),'Rotation tolerance in degrees',lower=0.1,upper=20),
        'size':number(payload.get('size_tolerance',20),'Outside dimensions tolerance in mm',lower=1,upper=350),
    }


def assess(measured, expected, tolerances, support):
    if expected is None:
        return {'status':'observed only','errors':None,'flags':[]}
    differences = error_magnitudes(measured,expected)
    issues = []
    if support<0.96:
        issues.append('partly outside calibrated wall')
    if measured['non_rectangularity_deg']>3 or measured['non_rectangularity_mm']>tolerances['size']:
        issues.append('opposing sides disagree')
    if differences['center_error']>tolerances['center']:
        issues.append('center differs')
    if abs(differences['rotation_error'])>tolerances['rotation']:
        issues.append('rotation differs')
    if max(abs(differences['width_error']),abs(differences['height_error']))>tolerances['size']:
        issues.append('outline size differs')
    return {'status':'review' if issues else 'within submitted tolerances','errors':differences,'flags':issues}


def unit_vector(value,label):
    length = float(np.linalg.norm(value))
    if length<1e-6:
        raise InputError(f'The {label} is too short to complete the hidden corner.')
    return value/length


def rotated(vector,quarter):
    return np.array([-vector[1],vector[0]]) if quarter>0 else np.array([vector[1],-vector[0]])


def edge_names(index_pair):
    return [EDGE_NAMES[index] for index in sorted(index_pair)]


def perpendicular(vector,reference):
    """The 90-degree turn of vector that points toward reference."""
    for candidate in (rotated(vector,1),rotated(vector,-1)):
        if np.dot(candidate,reference)>0:
            return candidate
    return rotated(vector,1)


def direction(vector,label):
    vector = unit_vector(vector,label)
    return degrees(atan2(float(vector[1]),float(vector[0])))


def completed_rectangle(known,blocked,width,height):
    """Complete one hidden corner with a right-angled frame of the registered width and height.

    The corner opposite the hidden one is shared by the two photographed sides. Both sides are
    anchored at that shared corner, and the right-angled frame is the compromise halfway between
    their measured directions, so the estimated top and left sides hug both photographed edges as
    closely as a square corner allows. If those edges are not exactly perpendicular the record
    gives each edge's departure from the frame and the spread of the hidden corner between a frame
    aligned exactly to one observed edge or the other. Edges joining two observed corners are
    anchored; the two edges meeting at the hidden corner are estimated.
    """
    model = local_rectangle(width,height)
    source = [index for index in range(4) if index!=blocked]
    shared = (blocked+2)%4
    horizontal = [index for index in range(4) if index!=shared and model[index][1]==model[shared][1]][0]
    vertical = [index for index in range(4) if index!=shared and model[index][0]==model[shared][0]][0]
    corner = np.asarray(known[shared],dtype=np.float64)
    seen_horizontal = np.asarray(known[horizontal],dtype=np.float64)-corner
    seen_vertical = np.asarray(known[vertical],dtype=np.float64)-corner
    interior = degrees(atan2(float(np.cross(seen_horizontal,seen_vertical)),
                             float(np.dot(seen_horizontal,seen_vertical))))
    length = float(np.linalg.norm(model[horizontal]-model[shared]))
    span = float(np.linalg.norm(model[vertical]-model[shared]))
    along_seen = unit_vector(seen_horizontal,'visible side')
    across_seen = unit_vector(seen_vertical,'visible side')
    along_frame = unit_vector(along_seen+perpendicular(across_seen,along_seen),'observed sides')
    across_frame = perpendicular(along_frame,across_seen)
    top_sign = 1.0 if (model[horizontal]-model[shared])[0]>0 else -1.0
    quad = [None]*4
    quad[shared] = corner
    quad[horizontal] = corner+length*along_frame
    quad[vertical] = corner+span*across_frame
    quad[blocked] = corner+length*along_frame+span*across_frame
    hug_horizontal = corner+length*along_seen+span*perpendicular(along_seen,across_seen)
    hug_vertical = corner+length*perpendicular(across_seen,along_seen)+span*across_seen
    geometry = {
        'method':'registered-size right-angle frame midway between the two observed sides',
        'hidden_corner':CORNER_NAMES[blocked],'anchor_corner':CORNER_NAMES[shared],
        'observed_corners':[CORNER_NAMES[index] for index in source],
        'anchored_edges':edge_names(((shared-1)%4,shared)),
        'inferred_edges':edge_names(((blocked-1)%4,blocked)),
        'registered_frame_mm':[width,height],
        'frame_angle_deg':round(normalize_angle(direction(top_sign*along_frame,'estimated frame')),4),
        'adjacent_sides_deg':round(abs(interior),4),
        'adjacent_sides_offset_from_right_deg':round(abs(interior)-90,4),
        'edge_offset_deg':{CORNER_NAMES[horizontal]:round(normalize_angle(direction(along_seen,'visible side')-direction(along_frame,'estimated frame')),4),
                           CORNER_NAMES[vertical]:round(normalize_angle(direction(across_seen,'visible side')-direction(across_frame,'estimated frame')),4)},
        'corner_residual_mm':{CORNER_NAMES[index]:round(float(np.linalg.norm(quad[index]-np.asarray(known[index],dtype=np.float64))),2)
                              for index in source},
        'hidden_corner_spread_mm':round(float(np.linalg.norm(hug_horizontal-hug_vertical)),2),
        'hidden_corner_side_limits_mm':np.round([hug_horizontal,hug_vertical],2).tolist(),
        'hidden_corner_estimate':np.round(quad[blocked],4).tolist(),
    }
    return np.asarray(quad,dtype=np.float64),geometry


def hidden_index(payload):
    return BLOCKED[choice(payload.get('blocked_corner','none'),tuple(BLOCKED),'hidden corner')]


def visible_quad(value,blocked):
    if blocked is None:
        quad = calibrated_quad(value,'Installed artwork outline')
        return quad,[0,1,2,3]
    corners = points(value,'Installed artwork outline',minimum=3,maximum=3)
    if signed_area(corners)<0.001:
        raise InputError('Mark the three visible corners clockwise from the upper left, keeping the hidden corner out.')
    ordered = [None]*4
    indices = [index for index in range(4) if index!=blocked]
    for position,index in enumerate(indices):
        ordered[index] = corners[position]
    return ordered,indices


def reading_measure(scene,payload,placement,artwork,tolerances):
    shot = find(scene.wall['shots'],payload.get('shot'),'Site photograph')
    blocked = hidden_index(payload)
    ordered,indices = visible_quad(payload.get('corners'),blocked)
    known_photo = [ordered[index] for index in indices]
    known_wall = photograph_points(scene.wall,shot,known_photo)
    geometry = None
    if blocked is None:
        kind = 'four corner observation'
        wall_corners = known_wall
        photo_corners = [ordered[index] for index in indices]
    else:
        if artwork is None:
            raise InputError('Choose the planned artwork so its registered width and height can complete the hidden corner.')
        kind = 'three corner estimate'
        known = [None]*4
        for position,index in enumerate(indices):
            known[index] = known_wall[position]
        completed,geometry = completed_rectangle(known,blocked,artwork['width'],artwork['height'])
        wall_corners = completed
        photo_corners = known_photo
    observed = measured_shape(wall_corners)
    support = verify_frontal_support(scene.wall,wall_corners)
    expected = planned_shape(placement,artwork) if placement and artwork else None
    return {
        'id':new_id(),'kind':kind,'shot':shot['id'],'source_digest':shot['photo']['source_digest'],
        'calibration':shot['corners'],'photo_corners':photo_corners,'corner_indices':indices,
        'blocked_corner':None if blocked is None else CORNER_NAMES[blocked],
        'wall_corners':np.round(wall_corners,6).tolist(),
        'wall_size':[scene.wall['width'],scene.wall['height']],
        'observed':observed,'expected':expected,'support':round(support,4),'geometry':geometry,
        'conclusion':assess(observed,expected,tolerances,support),
        'note':text(payload.get('note',''),'Reading notes',maximum=500,required=False),
        'recorded':timestamp(),
    }


def check_readings(record):
    rows = record.get('readings')
    if rows:
        return rows
    return [{
        'id':record['id'],'index':1,'kind':'four corner observation','shot':record['shot'],
        'source_digest':record['source_digest'],'calibration':record['calibration'],
        'photo_corners':record['photo_corners'],'corner_indices':[0,1,2,3],'blocked_corner':None,
        'wall_corners':record['wall_corners'],'wall_size':record['wall_size'],
        'observed':record['observed'],'expected':record.get('expected'),'support':record.get('wall_support'),
        'geometry':None,'conclusion':record['conclusion'],'note':record.get('note',''),
        'recorded':record.get('recorded'),
    }]


def sync_check(check,readings):
    latest = readings[-1]
    check.update({'readings':readings,'shot':latest['shot'],'source_digest':latest['source_digest'],
                  'calibration':latest['calibration'],'photo_corners':latest['photo_corners'],
                  'wall_corners':latest['wall_corners'],'wall_size':latest['wall_size'],
                  'observed':latest['observed'],'expected':latest['expected'],
                  'wall_support':latest['support'],'conclusion':latest['conclusion']})
    for index,reading in enumerate(readings,1):
        reading['index'] = index
    return check


def check_context(scene,check):
    placement = next((item for item in scene.layout['placements'] if item['id']==check.get('placement')),None)
    return placement, (scene.artworks.get(placement['artwork']) if placement else None)


def save_survey(project,layout_id,payload):
    scene = Scene.build(project,layout_id)
    checks = scene.layout.setdefault('installed_surveys',[])
    existing_id = optional_id(payload.get('check'),'Installed artwork check')
    if existing_id:
        check = find(checks,existing_id,'Installed artwork check')
        readings = check_readings(check)
        if len(readings)>=MAX_READINGS:
            raise InputError(f'This installed check already holds {MAX_READINGS} readings.')
        placement,artwork = check_context(scene,check)
        reading = reading_measure(scene,payload,placement,artwork,check['tolerances'])
        readings.append(reading)
        check['layout_revision'] = project['revision']
        return sync_check(check,readings), f"Added {reading['kind']} to {check['title']}"
    if len(checks)>=MAX_SURVEYS:
        raise InputError(f'This layout may hold at most {MAX_SURVEYS} installed artwork checks.')
    placement_id = optional_id(payload.get('placement'),'Planned artwork placement')
    placement = find(scene.layout['placements'],placement_id,'Artwork placement') if placement_id else None
    artwork = scene.artworks[placement['artwork']] if placement else None
    if artwork is None and hidden_index(payload) is not None:
        raise InputError('Choose the planned artwork so its registered width and height can complete the hidden corner.')
    tolerances = geometry_thresholds(payload)
    expected = planned_shape(placement,artwork) if placement else None
    title = text(payload.get('title') or (expected and expected['title']) or 'Surveyed outline','Check name')
    reading = reading_measure(scene,payload,placement,artwork,tolerances)
    check = {'id':new_id(),'recorded':timestamp(),'layout_revision':project['revision'],'title':title,
             'placement':placement_id,'tolerances':tolerances,
             'condition':choice(payload.get('condition','as installed'),CONDITIONS,'survey condition'),
             'note':text(payload.get('note',''),'Survey notes',maximum=650,required=False),
             'readings':[reading]}
    checks.append(sync_check(check,[reading]))
    return check, f"Measured {check['title']}"


def delete_survey(project,layout_id,identifier):
    scene = Scene.build(project,layout_id)
    checks = scene.layout.get('installed_surveys',[])
    item = find(checks,identifier,'Installed artwork check')
    checks.remove(item)
    return identifier,f"Removed saved outline {item['title']}"


def reading_comparison(first,second):
    prior,next_state = first['observed'],second['observed']
    return {'first':first['id'],'second':second['id'],
            'kinds':[first['kind'],second['kind']],
            'center_move_mm':round(hypot(next_state['center'][0]-prior['center'][0],
                                         next_state['center'][1]-prior['center'][1]),2),
            'size_delta':{axis:round(next_state[axis]-prior[axis],2) for axis in ('width','height')},
            'rotation_delta':round(normalize_angle(next_state['angle']-prior['angle']),2),
            'from_same_calibration':first['calibration']==second['calibration'] and first['shot']==second['shot']}


def differences_between_checks(first,second):
    return reading_comparison(check_readings(first)[-1],check_readings(second)[-1])


def reading_flag(scene,check,reading):
    shot = next((item for item in scene.wall['shots'] if item['id']==reading['shot']),None)
    if shot is None or shot['photo']['source_digest']!=reading['source_digest']:
        return 'photograph removed'
    if shot['corners']!=reading['calibration']:
        return 'camera marks updated'
    if reading['wall_size']!=[scene.wall['width'],scene.wall['height']]:
        return 'wall remeasured'
    linked = next((p for p in scene.layout['placements'] if p['id']==check.get('placement')),None)
    if linked is None:
        return 'unlinked' if check.get('placement') is None else 'placement removed'
    if reading['expected'] is not None and planned_shape(linked,scene.artworks[linked['artwork']])!=reading['expected']:
        return 'plan has moved'
    return 'current projection'


def survey_report(scene):
    items = []
    by_placement = {}
    for entry in scene.layout.get('installed_surveys',[]):
        difference = None
        if entry['placement']:
            previous = by_placement.get(entry['placement'])
            if previous:
                difference = differences_between_checks(previous,entry)
            by_placement[entry['placement']] = entry
        readings = check_readings(entry)
        enriched = [{**reading,'current_source':reading_flag(scene,entry,reading)} for reading in readings]
        comparison = reading_comparison(readings[0],readings[-1]) if len(readings)>1 else None
        items.append({**entry,'readings':enriched,'reading_count':len(readings),
                      'original_reading':readings[0]['id'],'latest_reading':readings[-1]['id'],
                      'estimate_vs_observed':comparison,'compared_with_previous':difference,
                      'current_source':enriched[-1]['current_source']})
    return {'checks':items,'count':len(items),
            'review_count':sum(item['conclusion']['status']=='review' for item in items)}


def scale_outline(points,size):
    coords = np.asarray(points)*np.array(size)
    return [tuple(entry) for entry in coords]


def paint_outline(draw,points,tint,*,width=3,dashed=False):
    segments = list(zip(points,[*points[1:],points[0]]))
    for start,end in segments:
        if dashed:
            dashed_line(draw,start,end,tint,width=width)
        else:
            draw.line((*start,*end),fill='#29251b',width=width+2)
            draw.line((*start,*end),fill=tint,width=width)
    if not dashed:
        for x,y in points:
            draw.ellipse((x-3,y-3,x+3,y+3),fill=tint)


def dashed_line(draw,start,end,tint,*,width=3,mark=9):
    start,end = np.asarray(start,dtype=float),np.asarray(end,dtype=float)
    length = float(np.linalg.norm(end-start))
    if length<1e-6:
        return
    direction = (end-start)/length
    position = 0.0
    while position<length:
        finish = min(position+mark,length)
        draw.line((*start+direction*position,*start+direction*finish),fill='#29251b',width=width+2)
        draw.line((*start+direction*position,*start+direction*finish),fill=tint,width=width)
        position += mark*2


def survey_picture(blobs,scene,row,reading_id=None):
    readings = check_readings(row)
    reading = next((item for item in readings if item['id']==reading_id),readings[-1])
    shot = find(scene.wall['shots'],reading['shot'],'Site photograph')
    with Image.open(blobs.require(shot['photo']['preview_digest'])) as source:
        image = source.convert('RGB')
    draw = ImageDraw.Draw(image)
    archived = {**shot,'corners':reading['calibration']}
    wall = {**scene.wall,'width':reading['wall_size'][0],'height':reading['wall_size'][1]}
    expected = reading.get('expected') or row.get('expected')
    if expected:
        predicted = wall_to_photo(wall,archived,expected['wall_corners'])
        paint_outline(draw,scale_outline(predicted,image.size),'#eccb81')
    if reading is not readings[0]:
        projected = scale_outline(wall_to_photo(wall,archived,readings[0]['wall_corners']),image.size)
        paint_outline(draw,projected,'#e2a1b4',dashed=True)
        first_x,first_y = projected[0]
        draw.text((first_x+13,first_y+9),f"reading {readings[0]['index']}",font=font(12,heading=True),
                  fill='#e2a1b4',stroke_width=2,stroke_fill='#26251e')
    outline = scale_outline(wall_to_photo(wall,archived,reading['wall_corners']),image.size)
    geometry = reading.get('geometry')
    if geometry:
        anchored = set(geometry['anchored_edges'])
        for index,(start,end) in enumerate(zip(outline,[*outline[1:],outline[0]])):
            if EDGE_NAMES[index] in anchored:
                draw.line((*start,*end),fill='#29251b',width=5)
                draw.line((*start,*end),fill='#a4f0d3',width=3)
            else:
                dashed_line(draw,start,end,'#7fd7e6')
    else:
        paint_outline(draw,outline,'#a4f0d3')
    for x,y in outline:
        draw.ellipse((x-4,y-4,x+4,y+4),fill='#f8f3e9',outline='#29251b',width=2)
    for order,(x,y) in enumerate(scale_outline(reading['photo_corners'],image.size),1):
        draw.ellipse((x-9,y-9,x+9,y+9),fill='#29251b')
        draw.text((x,y),str(order),font=font(11,heading=True),fill='#f4efe5',anchor='mm')
    if geometry:
        first,second = scale_outline(geometry['hidden_corner_side_limits_mm'],image.size)
        dashed_line(draw,first,second,'#f0a35e',width=2,mark=5)
        hidden = scale_outline([geometry['hidden_corner_estimate']],image.size)[0]
        draw.ellipse((hidden[0]-9,hidden[1]-9,hidden[0]+9,hidden[1]+9),outline='#7fd7e6',width=3)
        draw.text((hidden[0]+13,hidden[1]-7),
                  f"estimated · spread {geometry['hidden_corner_spread_mm']:g} mm",
                  font=font(12,heading=True),fill='#7fd7e6',stroke_width=2,stroke_fill='#26251e')
    draw.rounded_rectangle((14,14,min(760,image.width-14),100),radius=8,fill='#302c25')
    draw.text((27,24),row['title'],font=font(18,heading=True),fill='#f4efe5')
    if geometry:
        caption = (f"Reading {reading['index']} · {geometry['hidden_corner']} hidden · "
                   f"anchored {' and '.join(geometry['anchored_edges'])} · "
                   f"estimated {' and '.join(geometry['inferred_edges'])}")
        legend = 'observed corners  /  anchored edges  /  estimated edges  /  planned outline'
    else:
        caption = f"Reading {reading['index']} · four corners observed"
        legend = 'observed outline  /  planned outline'
    if reading is not readings[0]:
        legend += f"  /  reading {readings[0]['index']} projected"
    draw.text((27,50),caption,font=font(13),fill='#e9ddbe')
    draw.text((27,73),legend,font=font(12),fill='#c8bda4')
    return image
