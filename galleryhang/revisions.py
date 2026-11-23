from collections import Counter, defaultdict
from math import hypot

from .model import InputError


def frozen_geometry(scene):
    return {
        'wall':{'id':scene.wall['id'],'name':scene.wall['name'],'width':scene.wall['width'],
                'height':scene.wall['height'],'datum':scene.wall['datum']},
        'layout':{'id':scene.layout['id'],'name':scene.layout['name'],'eye_level':scene.layout['eye_level'],
                  'gap':scene.layout['gap']},
        'artworks':[{
            'placement':p['id'],'artwork':a['id'],'label':a['title'],'inventory':a['inventory'],
            'width':a['width'],'height':a['height'],'x':p['x'],'y':p['y'],'angle':p['angle'],
            'source':a['photo']['source_digest'],'crop':a['crop'],'frame':a['frame'],
            'copied_from':p.get('copied_from'),
        } for p,a in scene.works()],
        'labels':[{
            'id':row['id'],'name':row['name'],'x':row['x'],'y':row['y'],'linked':row['placement'],
            'body':row['body'],'type_size':row['type_size'],'style':row['style'],
        } for row in scene.layout['labels']],
    }


def saved_snapshot_geometry(snapshot):
    geometry = snapshot.get('geometry_record')
    if not isinstance(geometry,dict):
        raise InputError('This earlier issue has no retained geometry. Save another issue to compare working changes.')
    return geometry


def unique_identity_match(left, right, kind):
    bins = defaultdict(list)
    for idx,item in enumerate(left):
        if item.get(kind):
            bins[item[kind]].append(idx)
    usage = Counter(item[kind] for item in right if item.get(kind))
    return {index:bins[item[kind]][0] for index,item in enumerate(right)
            if item.get(kind) and len(bins[item[kind]])==1 and usage[item[kind]]==1}


def link_artwork_positions(before,after):
    source = {work['placement']:i for i,work in enumerate(before)}
    links = {}
    used = set()
    for idx,item in enumerate(after):
        if item['placement'] in source:
            key = source[item['placement']]
            links[idx] = key
            used.add(key)
    replace = {item.get('copied_from'):idx for idx,item in enumerate(after)
               if idx not in links and item.get('copied_from') in source}
    for original,idx in replace.items():
        if source[original] not in used:
            links[idx] = source[original]
            used.add(source[original])
    for category in ('artwork','inventory'):
        for idx,key in unique_identity_match(before,after,category).items():
            if idx not in links and key not in used:
                links[idx] = key
                used.add(key)
    return links,used


def artwork_diff(before,after):
    dx = round(after['x']-before['x'],2)
    dy = round(after['y']-before['y'],2)
    angle = round((after['angle']-before['angle']+180)%360-180,2)
    changes = []
    if dx or dy:
        changes.append('position')
    if angle:
        changes.append('orientation')
    if before['source']!=after['source'] or before['crop']!=after['crop']:
        changes.append('artwork image')
    if any(before[f]!=after[f] for f in ('frame','width','height')):
        changes.append('outside frame')
    if before['label']!=after['label']:
        changes.append('display title')
    return {'title':after['label'],'prior':before['placement'],'current':after['placement'],
            'dx':dx,'dy':dy,'angle_delta':angle,'distance_mm':round(hypot(dx,dy),2),'changes':changes,
            'status':'changed' if changes else 'unchanged'}


def label_diff(before,after):
    old = {item['id']:item for item in before}
    delta = []
    consumed = set()
    for item in after:
        previous = old.get(item['id'])
        consumed.add(item['id'])
        if previous is None:
            delta.append({'status':'added','id':item['id'],'title':item['name'] or 'Artwork label'})
        elif item!=previous:
            delta.append({'status':'changed','id':item['id'],'title':item['name'] or 'Artwork label',
                          'fields':[field for field in item if item[field]!=previous[field]]})
    for item in before:
        if item['id'] not in consumed:
            delta.append({'status':'removed','id':item['id'],'title':item['name'] or 'Artwork label'})
    return delta


def compare_issues(first, second):
    before = saved_snapshot_geometry(first)
    after = saved_snapshot_geometry(second)
    if before['wall']['id']!=after['wall']['id']:
        raise InputError('Choose two issues saved from the same measured wall.')
    earlier,later = before['artworks'],after['artworks']
    matches,used = link_artwork_positions(earlier,later)
    differences = []
    for index,item in enumerate(later):
        if index in matches:
            differences.append(artwork_diff(earlier[matches[index]],item))
        else:
            differences.append({'title':item['label'],'current':item['placement'],'status':'added'})
    for index,item in enumerate(earlier):
        if index not in used:
            differences.append({'title':item['label'],'prior':item['placement'],'status':'removed'})
    wall_changes = [f for f in ('width','height','datum') if before['wall'][f]!=after['wall'][f]]
    return {'first':{'name':first['name'],'revision':first['revision']},
            'second':{'name':second['name'],'revision':second['revision']},
            'wall':after['wall']['name'],'wall_changes':wall_changes,
            'coordinates_share_measurement':not wall_changes,
            'counts':dict(Counter(row['status'] for row in differences)),
            'artworks':differences,'labels':label_diff(before['labels'],after['labels'])}
