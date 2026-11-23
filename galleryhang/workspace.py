from .layout import Scene, new_layout
from .model import (
    InputError, MAX_ARTWORKS, MAX_LAYOUTS, MAX_OBSTACLES, MAX_SHOTS, MAX_SWATCHES, MAX_WALLS,
    append_item, artwork_fields, find, mount_mark_fields, new_artwork, new_id, new_shot,
    new_wall, obstacle_fields, parse_revision, project_fields, remove_item, shot_fields,
    swatch_fields, text, timestamp, wall_fields,
)
from .photos import diagnostic_flags, store_capture
from .projection import shot_matrix


WALL_COLLECTIONS = {
    'obstacle': ('obstacles', obstacle_fields, MAX_OBSTACLES, 'obstacles'),
    'sample': ('swatches', swatch_fields, MAX_SWATCHES, 'paint samples'),
    'fixing': ('mount_marks', mount_mark_fields, 100, 'fixing marks'),
}


def add_wall(project,payload):
    wall = new_wall(payload)
    append_item(project['walls'],wall,MAX_WALLS,'walls')
    append_item(project['layouts'],new_layout('Working layout · '+wall['name'],wall['id']),MAX_LAYOUTS,'layouts')
    return wall, f"Added measured wall {wall['name']}"


def wall_containment(wall, candidate):
    outside = []
    for obstacle in wall['obstacles']:
        if (obstacle['left']+obstacle['width']>candidate['width']
                or obstacle['bottom']+obstacle['height']>candidate['height']):
            outside.append(obstacle['name'])
    for mark in wall['mount_marks']:
        if mark['x']>candidate['width'] or mark['y']>candidate['height']:
            outside.append(mark['name'])
    return outside


def change_wall(project,wall_id,payload):
    wall = find(project['walls'],wall_id,'Wall')
    fields = wall_fields(payload,wall)
    invalid = wall_containment(wall,fields)
    if invalid:
        raise InputError('The new dimensions put marked features outside this wall: '+', '.join(invalid[:6])+'.')
    wall.update(fields)
    return wall, f"Updated measured wall {wall['name']}"


def change_project(project,payload):
    project.update(project_fields(payload,project))
    return None, 'Updated exhibition information'


def delete_wall(project,wall_id):
    wall = find(project['walls'],wall_id,'Wall')
    if any(layout['wall']==wall_id for layout in project['layouts']):
        raise InputError('Remove the working layouts on this wall before removing it.')
    project['walls'].remove(wall)
    return wall_id, f"Removed wall {wall['name']}"


def parse_shot_meta(payload,filename):
    base = new_shot(None, text(payload.get('name',filename) or 'Site photograph','View name'),payload.get('role','reference'))
    base.update(shot_fields(payload,base))
    return base


def add_shot(store, project_id, wall_id, payload, data, filename):
    revision = parse_revision(payload)
    readback = store.get(project_id)
    wall = find(readback['walls'],wall_id,'Wall')
    if len(wall['shots'])>=MAX_SHOTS:
        raise InputError(f'This wall already has {MAX_SHOTS} site photographs.')
    upload = parse_shot_meta(payload,filename)
    upload['photo'] = store_capture(store.blobs,data,upload['role'],filename,upload['note'],new_id())

    def apply(project):
        target = find(project['walls'],wall_id,'Wall')
        append_item(target['shots'],upload,MAX_SHOTS,'site photographs per wall')
        return upload, f"Added site view {upload['name']} to {target['name']}"

    return store.update(project_id,revision,apply,'photo')


def change_shot(project, wall_id, shot_id, payload):
    wall = find(project['walls'],wall_id,'Wall')
    target = find(wall['shots'],shot_id,'Site photograph')
    fields = shot_fields(payload,target)
    target.update(fields)
    return target, f"Updated correspondence for {target['name']}"


def delete_shot(project, wall_id, shot_id):
    wall = find(project['walls'],wall_id,'Wall')
    target = find(wall['shots'],shot_id,'Site photograph')
    if any(sample['shot']==shot_id for sample in wall['swatches']):
        raise InputError('Remove paint samples measured on this view before removing it.')
    if any(obs['shot']==shot_id for layout in project['layouts'] if layout['wall']==wall_id
           for obs in layout['observations']):
        raise InputError('Remove working observations tied to this view before removing it.')
    if any(survey_uses_shot(survey,shot_id) for layout in project['layouts'] if layout['wall']==wall_id
           for survey in layout.get('installed_surveys',[])):
        raise InputError('Remove the measured outlines tied to this view before removing it.')
    wall['shots'].remove(target)
    return shot_id, f"Removed site view {target['name']}"


def survey_uses_shot(survey,shot_id):
    readings = survey.get('readings') or [survey]
    return any(reading.get('shot')==shot_id for reading in readings)


def edit_wall_annotation(project, wall_id, kind, payload, identifier=None):
    wall = find(project['walls'],wall_id,'Wall')
    group,validate,limit,label = WALL_COLLECTIONS[kind]
    current = find(wall[group],identifier,kind.capitalize()) if identifier else None
    fields = validate(payload,wall,current)
    if current is None:
        row = {**fields,'id':new_id()}
        append_item(wall[group],row,limit,label)
    else:
        current.update(fields)
        row = current
    return row, f"Saved {row['name']} on {wall['name']}"


def delete_wall_annotation(project, wall_id, kind, identifier):
    wall = find(project['walls'],wall_id,'Wall')
    group,_,_,_ = WALL_COLLECTIONS[kind]
    old = remove_item(wall[group],identifier,kind.capitalize())
    return identifier, f"Removed {old['name']} from {wall['name']}"


def set_primary_view(project, wall_id, shot_id):
    wall = find(project['walls'],wall_id,'Wall')
    selected = find(wall['shots'],shot_id,'Site photograph')
    wall['shots'].remove(selected)
    wall['shots'].insert(0,selected)
    return selected, f"Primary site view is {selected['name']}"


def add_artwork(store, project_id, payload, data, filename):
    revision = parse_revision(payload)
    readback = store.get(project_id)
    if len(readback['artworks'])>=MAX_ARTWORKS:
        raise InputError(f'This exhibition already has {MAX_ARTWORKS} artworks.')
    fields = artwork_fields(payload)
    preview = store_capture(store.blobs,data,'artwork',filename,'',new_id())
    entry = new_artwork(fields,preview)

    def apply(project):
        append_item(project['artworks'],entry,MAX_ARTWORKS,'artworks')
        return entry, f"Registered artwork {entry['title']}"

    return store.update(project_id,revision,apply,'artwork')


def duplicate_artwork(project, artwork_id, payload):
    source = find(project['artworks'],artwork_id,'Artwork')
    updates = artwork_fields(payload,source)
    if 'title' not in payload:
        updates['title'] = text(source['title']+' · copy','Artwork title')
    updates.update(id=new_id(),created=timestamp(),inventory=payload.get('inventory',''))
    append_item(project['artworks'],updates,MAX_ARTWORKS,'artworks')
    return updates, f"Registered another version of {source['title']}"


def change_artwork(project, artwork_id, payload):
    source = find(project['artworks'],artwork_id,'Artwork')
    current = artwork_fields(payload,source)
    source.update(current)
    return source, f"Updated artwork details for {source['title']}"


def delete_artwork(project, artwork_id):
    row = find(project['artworks'],artwork_id,'Artwork')
    if any(p['artwork']==artwork_id for layout in project['layouts'] for p in layout['placements']):
        raise InputError('Remove placements of this artwork before removing it from the catalog.')
    project['artworks'].remove(row)
    return artwork_id, f"Removed artwork {row['title']}"


def shot_description(shot):
    photo = shot['photo']
    matrix = shot_matrix(shot)
    return {
        'id':shot['id'],'name':shot['name'],'role':shot['role'],'native_size':[photo['width'],photo['height']],
        'corners':shot['corners'],'foreground_count':len(shot['foreground']),
        'transform':matrix.round(8).tolist(),'photograph':photo['name'],
        'color_space':photo['working_color_space'],'warnings':diagnostic_flags(photo),
    }


def scene_from_shot(project, layout_id, shot_id=None):
    scene = Scene.build(project,layout_id)
    shot = scene.shot(shot_id)
    if not shot:
        raise InputError('Add a site photograph to this wall first.')
    return scene, shot
