from math import acos, atan2, degrees, hypot, sqrt

import numpy as np
from PIL import Image,ImageDraw

from .capture_review import photograph_points
from .clearance import area, intersection_rect
from .labels import font
from .layout import Scene, footprint
from .model import InputError, choice, find, new_id, number, optional_id, text, timestamp
from .projection import calibrated_quad, wall_to_photo


MAX_SURVEYS = 60
CONDITIONS = ('as installed','after adjustment','reference check')


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


def survey_input(scene,payload):
    shot = find(scene.wall['shots'],payload.get('shot'),'Site photograph')
    quad = calibrated_quad(payload.get('corners'),'Installed artwork outline')
    points = photograph_points(scene.wall,shot,quad)
    observed = measured_shape(points)
    support = verify_frontal_support(scene.wall,points)
    placement_id = optional_id(payload.get('placement'),'Planned artwork placement')
    expected = None
    if placement_id:
        placement = find(scene.layout['placements'],placement_id,'Artwork placement')
        expected = planned_shape(placement,scene.artworks[placement['artwork']])
    tolerances = geometry_thresholds(payload)
    title = text(payload.get('title') or (expected and expected['title']) or 'Surveyed outline','Check name')
    return {
        'title':title,'shot':shot['id'],'source_digest':shot['photo']['source_digest'],
        'photo_corners':quad,'calibration':shot['corners'],'wall_corners':np.round(points,6).tolist(),
        'wall_size':[scene.wall['width'],scene.wall['height']],'condition':choice(payload.get('condition','as installed'),CONDITIONS,'survey condition'),
        'placement':placement_id,'observed':observed,'expected':expected,'tolerances':tolerances,
        'wall_support':round(support,4),'conclusion':assess(observed,expected,tolerances,support),
        'note':text(payload.get('note',''),'Survey notes',maximum=650,required=False),
    }


def save_survey(project,layout_id,payload):
    scene = Scene.build(project,layout_id)
    checks = scene.layout.setdefault('installed_surveys',[])
    if len(checks)>=MAX_SURVEYS:
        raise InputError(f'This layout may hold at most {MAX_SURVEYS} installed artwork checks.')
    measure = survey_input(scene,payload)
    measure.update(id=new_id(),recorded=timestamp(),layout_revision=project['revision'])
    checks.append(measure)
    return measure,f"Measured artwork outline {measure['title']}"


def delete_survey(project,layout_id,identifier):
    scene = Scene.build(project,layout_id)
    checks = scene.layout.get('installed_surveys',[])
    item = find(checks,identifier,'Installed artwork check')
    checks.remove(item)
    return identifier,f"Removed saved outline {item['title']}"


def differences_between_checks(first,second):
    prior,next_state = first['observed'],second['observed']
    return {'first':first['id'],'second':second['id'],
            'center_move_mm':round(hypot(next_state['center'][0]-prior['center'][0],next_state['center'][1]-prior['center'][1]),2),
            'size_delta':{axis:round(next_state[axis]-prior[axis],2) for axis in ('width','height')},
            'rotation_delta':round(normalize_angle(next_state['angle']-prior['angle']),2),
            'from_same_calibration':first['calibration']==second['calibration'] and first['shot']==second['shot'],
            'from_same_wall_measurement':first['wall_size']==second['wall_size']}


def current_flag(scene,row):
    shot = next((item for item in scene.wall['shots'] if item['id']==row['shot']),None)
    if shot is None or shot['photo']['source_digest']!=row['source_digest']:
        return 'photograph removed'
    if shot['corners']!=row['calibration']:
        return 'camera marks updated'
    if row['wall_size']!=[scene.wall['width'],scene.wall['height']]:
        return 'wall remeasured'
    linked = next((p for p in scene.layout['placements'] if p['id']==row['placement']),None)
    if linked is None:
        return 'unlinked' if row['placement'] is None else 'placement removed'
    if planned_shape(linked,scene.artworks[linked['artwork']])!=row['expected']:
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
        items.append({**entry,'compared_with_previous':difference,'current_source':current_flag(scene,entry)})
    return {'checks':items,'count':len(items),
            'review_count':sum(item['conclusion']['status']=='review' for item in items)}


def scale_outline(points,size):
    coords = np.asarray(points)*np.array(size)
    return [tuple(entry) for entry in coords]


def paint_outline(draw,points,tint,*,width=3):
    draw.line([*points,points[0]],fill='#29251b',width=width+2)
    draw.line([*points,points[0]],fill=tint,width=width)
    for x,y in points:
        draw.ellipse((x-3,y-3,x+3,y+3),fill=tint)


def survey_picture(blobs,scene,row):
    shot = find(scene.wall['shots'],row['shot'],'Site photograph')
    with Image.open(blobs.require(shot['photo']['preview_digest'])) as source:
        image = source.convert('RGB')
    draw = ImageDraw.Draw(image)
    if row['expected']:
        archived = {**shot,'corners':row['calibration']}
        wall = {**scene.wall,'width':row['wall_size'][0],'height':row['wall_size'][1]}
        predicted = wall_to_photo(wall,archived,row['expected']['wall_corners'])
        paint_outline(draw,scale_outline(predicted,image.size),'#eccb81')
    paint_outline(draw,scale_outline(row['photo_corners'],image.size),'#a4f0d3')
    draw.rounded_rectangle((14,14,min(610,image.width-14),78),radius=8,fill='#302c25')
    draw.text((27,25),row['title'],font=font(18,heading=True),fill='#f4efe5')
    draw.text((27,51),'Observed outline  /  Planned outline' if row['expected'] else 'Observed outline',
              font=font(13),fill='#e9ddbe')
    return image
