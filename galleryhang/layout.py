from copy import deepcopy
from dataclasses import dataclass
from math import cos, radians, sin

import numpy as np

from .model import (
    InputError, MAX_LAYOUTS, MAX_PLACEMENTS, MAX_STATIONS, append_item, choice, find,
    label_fields, layout_fields, new_id, new_layout, number, placement_fields,
    remove_item, sequence, station_fields, text, timestamp,
)
from .store import document, sha256


MAX_LABELS = 50
ALIGNMENTS = ('center-x', 'center-y', 'left', 'right', 'top', 'bottom')
ACTIONS = (*ALIGNMENTS,'space-x','space-y','eye-line','raise','lower')


@dataclass(frozen=True)
class Scene:
    project: dict
    layout: dict
    wall: dict
    artworks: dict

    @staticmethod
    def build(project, layout_id):
        layout = find(project['layouts'],layout_id,'Layout')
        wall = find(project['walls'],layout['wall'],'Wall')
        return Scene(project,layout,wall,{row['id']: row for row in project['artworks']})

    def works(self, *, enabled=True):
        return [(placement, self.artworks[placement['artwork']])
                for placement in self.layout['placements'] if not enabled or placement['visible']]

    def shot(self, key=None):
        if key:
            return find(self.wall['shots'], key, 'Site photograph')
        return self.wall['shots'][0] if self.wall['shots'] else None

    def reference_identity(self):
        source = {field: self.wall[field] for field in (
            'id','width','height','paint','baseboard','datum','shots','obstacles','mount_marks','swatches',
        )}
        placed = {placement['artwork']:self.artworks[placement['artwork']]
                  for placement in self.layout['placements']}
        return sha256(document({'wall':source,'layout':self.layout,'artworks':placed}).encode())


def wall_for(project, layout):
    return find(project['walls'], layout['wall'], 'Wall')


def make_layout(project, data):
    wall = find(project['walls'],data.get('wall'),'Wall')
    existing = new_layout(data.get('name','Working layout'),wall['id'])
    result = layout_fields(data,existing)
    append_item(project['layouts'],result,MAX_LAYOUTS,'layouts')
    return result, f"Created layout {result['name']} on {wall['name']}"


def edit_layout(project, layout_id, data):
    current = find(project['layouts'],layout_id,'Layout')
    current.update(layout_fields(data,current))
    return current, f"Edited settings for {current['name']}"


def duplicate_layout(project, layout_id, data):
    original = find(project['layouts'],layout_id,'Layout')
    duplicate = deepcopy(original)
    duplicate['id'] = new_id()
    duplicate['name'] = text(data.get('name',original['name']+' – copy'),'Layout name')
    duplicate['created'] = timestamp()
    duplicate['compared_with'] = original['id']
    placement_ids = {}
    for item in duplicate['placements']:
        original_id = item['id']
        item['id'] = new_id()
        placement_ids[original_id] = item['id']
        item['copied_from'] = original_id
    for label in duplicate['labels']:
        label['id'] = new_id()
        if label['placement'] is not None:
            label['placement'] = placement_ids[label['placement']]
    station_ids = {}
    for station in duplicate['stations']:
        original_id = station['id']
        station['id'] = new_id()
        station_ids[original_id] = station['id']
    for route in duplicate.get('visit_sequences',[]):
        route['id'] = new_id()
        for stop in route['stops']:
            stop['station'] = station_ids[stop['station']]
    duplicate['observations'] = []
    duplicate.pop('installed_surveys',None)
    append_item(project['layouts'],duplicate,MAX_LAYOUTS,'layouts')
    return duplicate, f"Copied {original['name']} as {duplicate['name']}"


def delete_layout(project, layout_id):
    layout = remove_item(project['layouts'],layout_id,'Layout')
    return layout['id'], f"Removed working layout {layout['name']}"


def place_work(project, layout_id, data):
    scene = Scene.build(project,layout_id)
    art = find(project['artworks'],data.get('artwork'),'Artwork')
    entry = {**placement_fields(data,scene.wall,art),'id':new_id()}
    append_item(scene.layout['placements'],entry,MAX_PLACEMENTS,'placements per layout')
    return entry, f"Placed {art['title']} on {scene.wall['name']}"


def edit_placement(project, layout_id, placement_id, data):
    scene = Scene.build(project,layout_id)
    existing = find(scene.layout['placements'],placement_id,'Artwork placement')
    artwork = scene.artworks[existing['artwork']]
    existing.update(placement_fields(data,scene.wall,artwork,existing))
    return existing, f"Edited placement for {artwork['title']}"


def delete_placement(project, layout_id, placement_id):
    scene = Scene.build(project,layout_id)
    found = find(scene.layout['placements'],placement_id,'Artwork placement')
    if found['pinned']:
        raise InputError('Unpin this artwork before removing it.')
    scene.layout['placements'].remove(found)
    for label in scene.layout['labels']:
        if label['placement'] == placement_id:
            label['placement'] = None
    return placement_id, f"Removed {scene.artworks[found['artwork']]['title']} from the layout"


def place_label(project, layout_id, data, identifier=None):
    scene = Scene.build(project,layout_id)
    collection = scene.layout['labels']
    existing = find(collection,identifier,'Label') if identifier else None
    fields = label_fields(data,scene.wall,existing)
    if fields['placement'] is not None:
        find(scene.layout['placements'],fields['placement'],'Artwork placement')
    if fields['placement'] is None and not fields['name'] and not fields['body']:
        raise InputError('Provide label text or choose an artwork for the label.')
    if existing is None:
        entry = {'id':new_id(), **fields}
        append_item(collection,entry,MAX_LABELS,'labels per layout')
    else:
        existing.update(fields)
        entry = existing
    return entry, 'Updated label on wall elevation'


def delete_label(project, layout_id, identifier):
    scene = Scene.build(project,layout_id)
    remove_item(scene.layout['labels'],identifier,'Label')
    return identifier, 'Removed label from wall elevation'


def place_station(project, layout_id, data, identifier=None):
    scene = Scene.build(project,layout_id)
    collection = scene.layout['stations']
    existing = find(collection,identifier,'Viewing position') if identifier else None
    fields = station_fields(data,scene.wall,existing)
    if existing is None:
        entry = {'id':new_id(), **fields}
        append_item(collection,entry,MAX_STATIONS,'viewing positions per layout')
    else:
        existing.update(fields)
        entry = existing
    return entry, f"Saved viewing position {entry['name']}"


def delete_station(project, layout_id, identifier):
    scene = Scene.build(project,layout_id)
    if any(stop['station']==identifier for route in scene.layout.get('visit_sequences',[]) for stop in route['stops']):
        raise InputError('Remove this viewpoint from its visit sequences before deleting it.')
    found = remove_item(scene.layout['stations'],identifier,'Viewing position')
    return identifier, f"Removed viewing position {found['name']}"


def local_rectangle(width,height):
    return np.array([[-width/2,height/2], [width/2,height/2], [width/2,-height/2], [-width/2,-height/2]])


def rotation(angle):
    theta = radians(angle)
    return np.array([[cos(theta),-sin(theta)],[sin(theta),cos(theta)]])


def local_to_wall(placement, coordinates):
    center = np.array([placement['x'],placement['y']])
    return np.asarray(coordinates) @ rotation(placement['angle']).T + center


def footprint(placement, artwork):
    return local_to_wall(placement,local_rectangle(artwork['width'],artwork['height']))


def label_footprint(label):
    return local_rectangle(label['width'],label['height']) + np.array([label['x'],label['y']])


def rect_bounds(coordinates):
    points = np.asarray(coordinates)
    minimum = np.min(points,axis=0)
    maximum = np.max(points,axis=0)
    return [float(minimum[0]),float(minimum[1]),float(maximum[0]),float(maximum[1])]


def artwork_bounds(placement, artwork):
    return rect_bounds(footprint(placement,artwork))


def centroid(placements, axis):
    return sum(p[axis] for p in placements)/len(placements)


def check_selection(scene, data, action):
    identifiers = sequence(data.get('placements'), 'Selected placements', minimum=1, maximum=MAX_PLACEMENTS)
    selected = [find(scene.layout['placements'], ident, 'Selected placement') for ident in identifiers]
    if len(set(identifiers)) != len(identifiers):
        raise InputError('Choose each placement once.')
    if action not in ('eye-line','raise','lower') and len(selected) < 2:
        raise InputError('Select at least two artworks to align or space them.')
    if sum(placement['pinned'] for placement in selected) > 1 and action not in ('raise','lower'):
        raise InputError('Select at most one pinned artwork as a reference.')
    return selected


def shift_axis(placement, axis, value):
    if placement['pinned'] and abs(placement[axis]-value)>1e-6:
        raise InputError('This action would move a pinned artwork.')
    return {**placement, axis:round(float(value),4)}


def align_coordinates(selected, artworks, action):
    axis_by_action = {'center-x':'x','center-y':'y','left':'x','right':'x','top':'y','bottom':'y'}
    axis = axis_by_action[action]
    pinned = next((p for p in selected if p['pinned']), None)
    if action.startswith('center'):
        anchor = pinned[axis] if pinned else centroid(selected,axis)
        return [shift_axis(placement,axis,anchor) for placement in selected]
    index = {'left':0,'bottom':1,'right':2,'top':3}[action]
    coordinates = [artwork_bounds(p,artworks[p['artwork']])[index] for p in selected]
    if pinned:
        anchor = coordinates[selected.index(pinned)]
    else:
        anchor = min(coordinates) if action in ('left','bottom') else max(coordinates)
    return [shift_axis(p,axis,p[axis] + anchor - edge) for p,edge in zip(selected,coordinates)]


def equal_spacing(selected, artworks, axis):
    indexed = sorted(selected,key=lambda p:(p[axis],p['id']))
    min_index,max_index = (0,2) if axis=='x' else (1,3)
    bounds = [artwork_bounds(p,artworks[p['artwork']]) for p in indexed]
    lengths = [b[max_index]-b[min_index] for b in bounds]
    span = bounds[-1][max_index]-bounds[0][min_index]
    step = (span-sum(lengths))/(len(indexed)-1)
    cursor = bounds[0][min_index]
    destinations = []
    for placement,size,bound in zip(indexed,lengths,bounds):
        proposed = cursor + placement[axis] - bound[min_index]
        destinations.append((placement,proposed))
        cursor += size+step
    pinned = next(((p,value) for p,value in destinations if p['pinned']), None)
    shift = pinned[0][axis]-pinned[1] if pinned else 0
    return [shift_axis(p,axis,value+shift) for p,value in destinations]


def reorder(placements, selected, on_top):
    keys = {p['id'] for p in selected}
    moved = [p for p in placements if p['id'] in keys]
    remaining = [p for p in placements if p['id'] not in keys]
    return remaining + moved if on_top else moved + remaining


def apply_arrangement(project, layout_id, data):
    scene = Scene.build(project,layout_id)
    action = choice(data.get('action'),ACTIONS,'arrangement action')
    selected = check_selection(scene,data,action)
    if action in ('raise','lower'):
        scene.layout['placements'] = reorder(scene.layout['placements'],selected,action=='raise')
    else:
        if action in ALIGNMENTS:
            changed = align_coordinates(selected,scene.artworks,action)
        elif action in ('space-x','space-y'):
            changed = equal_spacing(selected,scene.artworks,action[-1])
        else:
            level = number(data.get('level',scene.layout['eye_level']),'Align at height in mm',lower=0,upper=scene.wall['height'])
            changed = [shift_axis(p,'y',level) for p in selected]
        for entry in changed:
            source = find(scene.layout['placements'],entry['id'],'Placement')
            source.update(placement_fields(entry,scene.wall,scene.artworks[entry['artwork']],source))
    return scene.layout, f"Applied {action} to {len(selected)} placement(s)"


def compare_layouts(project, first_id, other_id):
    first = Scene.build(project,first_id)
    second = Scene.build(project,other_id)
    if first.wall['id'] != second.wall['id']:
        raise InputError('Choose layouts on the same measured wall.')
    originals = {p['id']:(p,art) for p,art in first.works(enabled=False)}
    unmatched = set(originals)
    changes = []
    for other,art in second.works(enabled=False):
        key = other.get('copied_from') or other['id']
        if key not in unmatched:
            candidates = [ident for ident in unmatched if originals[ident][1]['id']==art['id']]
            key = candidates[0] if len(candidates)==1 else None
        if key is None or key not in unmatched:
            changes.append({'status':'added','title':art['title'],'after':other['id']})
            continue
        unmatched.remove(key)
        previous,_ = originals[key]
        dx,dy,dangle = (round(other[field]-previous[field],2) for field in ('x','y','angle'))
        visibility_changed = previous['visible'] != other['visible']
        changes.append({'status':'moved' if dx or dy or dangle or visibility_changed else 'same',
                        'title':art['title'],'previous':previous['id'],'after':other['id'],
                        'dx':dx,'dy':dy,'rotation_change':dangle,'visibility_changed':visibility_changed})
    changes.extend({'status':'removed','title':originals[key][1]['title'],'previous':key}
                   for key in originals if key in unmatched)
    return {'wall':first.wall['name'],'first':first.layout['name'],'second':second.layout['name'],'changes':changes}
