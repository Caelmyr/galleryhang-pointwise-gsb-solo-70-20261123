from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
import re
from uuid import uuid4


class InputError(ValueError):
    pass


class MissingError(LookupError):
    pass


class ConflictError(RuntimeError):
    pass


MAX_PIXELS = 12_000_000
MAX_UPLOAD = 24 * 1024**2
MAX_WALLS = 12
MAX_ARTWORKS = 80
MAX_LAYOUTS = 18
MAX_PLACEMENTS = 50
MAX_SHOTS = 8
MAX_OBSTACLES = 30
MAX_STATIONS = 12
MAX_SWATCHES = 12
MAX_IDENTIFICATIONS = 40

ID_PATTERN = re.compile(r'[0-9a-f]{16}\Z')
DIGEST_PATTERN = re.compile(r'[0-9a-f]{64}\Z')
COLOR_PATTERN = re.compile(r'#[0-9a-fA-F]{6}\Z')
FRAME_FINISHES = ('none', 'ink', 'oak', 'white', 'silver', 'walnut')
ATTACHMENTS = ('center', 'pair', 'rail', 'none')
OBSTACLE_KINDS = ('opening', 'switch', 'fixture', 'plinth', 'door-swing', 'prohibited')
LIGHTINGS = ('reference', 'evening', 'inspection', 'alternate')
DEFAULT_CORNERS = [[0.08, 0.11], [0.92, 0.11], [0.92, 0.89], [0.08, 0.89]]
DEFAULT_CROP = [[0, 0], [1, 0], [1, 1], [0, 1]]


def new_id():
    return uuid4().hex[:16]


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def token(value, label='ID'):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise InputError(f'{label} is invalid.')
    return value


def digest_token(value, label='Image reference'):
    if not isinstance(value, str) or not DIGEST_PATTERN.fullmatch(value):
        raise InputError(f'{label} is invalid.')
    return value


def mapping(value, label='Object'):
    if not isinstance(value, dict):
        raise InputError(f'{label} must be an object.')
    return value


def sequence(value, label, *, minimum=0, maximum=100):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        if minimum == maximum:
            raise InputError(f'{label} needs exactly {minimum} entries.')
        raise InputError(f'{label} needs between {minimum} and {maximum} entries.')
    return value


def text(value, label, *, maximum=120, required=True):
    if not isinstance(value, str):
        raise InputError(f'{label} must be text.')
    clean = value.strip()
    if any(ord(char) < 32 and char not in '\n\t' for char in clean):
        raise InputError(f'{label} contains an unsupported character.')
    if len(clean) > maximum:
        raise InputError(f'{label} must be at most {maximum} characters.')
    if required and not clean:
        raise InputError(f'{label} is required.')
    return clean


def number(value, label, *, lower=None, upper=None):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise InputError(f'{label} must be a number.')
    if (isinstance(value, float) and not isfinite(value)
            or lower is not None and value < lower
            or upper is not None and value > upper):
        limits = f' ({lower} to {upper})' if lower is not None and upper is not None else ''
        raise InputError(f'{label} is outside its supported range{limits}.')
    try:
        return float(value)
    except OverflowError as error:
        raise InputError(f'{label} is too large.') from error


def integer(value, label, *, lower=0, upper=1000):
    if type(value) is not int or not lower <= value <= upper:
        raise InputError(f'{label} must be a whole number from {lower} to {upper}.')
    return value


def boolean(value, label):
    if type(value) is not bool:
        raise InputError(f'{label} must be true or false.')
    return value


def choice(value, choices, label):
    if not isinstance(value, str) or value not in choices:
        raise InputError(f'Select a supported {label}.')
    return value


def color(value, label):
    if not isinstance(value, str) or not COLOR_PATTERN.fullmatch(value):
        raise InputError(f'{label} must be a six-digit RGB color.')
    return value.lower()


def pair(value, label, *, lower=None, upper=None):
    return [number(v, label, lower=lower, upper=upper)
            for v in sequence(value, label, minimum=2, maximum=2)]


def points(value, label, *, minimum=3, maximum=20):
    return [pair(point, label, lower=0, upper=1)
            for point in sequence(value, label, minimum=minimum, maximum=maximum)]


def parse_revision(data):
    return integer(mapping(data).get('revision'), 'Project revision', lower=1, upper=2**31-1)


def update_fields(payload, existing, rules):
    fields = dict(existing)
    for key, validator in rules.items():
        if key in payload:
            fields[key] = validator(payload[key])
    return fields


def required(entity, key, label):
    if key not in entity:
        raise InputError(f'{label} is required.')
    return entity[key]


def find(items, identifier, label='Item'):
    token(identifier, label)
    for entry in items:
        if entry['id'] == identifier:
            return entry
    raise MissingError(f'{label} was not found.')


def optional_id(value, label):
    return None if value is None or value == '' else token(value, label)


def append_item(items, item, limit, label):
    if len(items) >= limit:
        raise InputError(f'This project can hold up to {limit} {label}.')
    items.append(item)
    return item


def remove_item(items, identifier, label):
    found = find(items, identifier, label)
    items.remove(found)
    return found


def project_fields(payload, existing=None):
    data = mapping(payload, 'Exhibition')
    project = update_fields(data, existing or {'venue':'', 'date':'', 'note':''}, {
        'name': lambda x: text(x, 'Exhibition name'),
        'venue': lambda x: text(x, 'Venue', required=False),
        'date': lambda x: text(x, 'Installation date', maximum=32, required=False),
        'note': lambda x: text(x, 'Exhibition notes', maximum=1400, required=False),
    })
    required(project, 'name', 'Exhibition name')
    return project


def new_project(payload):
    return {
        **project_fields(payload),
        'id': new_id(),
        'created': timestamp(),
        'updated': timestamp(),
        'revision': 1,
        'walls': [],
        'artworks': [],
        'layouts': [],
        'identifications': [],
        'synthetic': False,
    }


def wall_fields(payload, existing=None):
    data = mapping(payload, 'Wall')
    wall = update_fields(data, existing or {
        'paint':'#e8dfd0', 'baseboard':80, 'datum':[0.0, 0.0], 'note':'',
    }, {
        'name': lambda x: text(x, 'Wall name'),
        'width': lambda x: number(x, 'Wall width in mm', lower=250, upper=30_000),
        'height': lambda x: number(x, 'Wall height in mm', lower=250, upper=12_000),
        'paint': lambda x: color(x, 'Wall color'),
        'baseboard': lambda x: number(x, 'Baseboard height in mm', lower=0, upper=500),
        'datum': lambda x: pair(x, 'Datum in mm', lower=0, upper=30_000),
        'note': lambda x: text(x, 'Wall notes', maximum=600, required=False),
    })
    required(wall, 'name', 'Wall name')
    width = required(wall, 'width', 'Wall width')
    height = required(wall, 'height', 'Wall height')
    if wall['baseboard'] >= height or wall['datum'][0] > width or wall['datum'][1] > height:
        raise InputError('Baseboard and reference point must fit the wall dimensions.')
    return wall


def new_wall(data):
    return {**wall_fields(data), 'id':new_id(), 'shots':[], 'obstacles':[], 'swatches':[], 'mount_marks':[]}


def shot_fields(payload, existing):
    from .projection import calibrated_quad, validate_polygon

    return update_fields(mapping(payload, 'Site photograph'), existing, {
        'name': lambda x: text(x, 'View name'),
        'role': lambda x: choice(x, LIGHTINGS, 'view type'),
        'note': lambda x: text(x, 'Photograph notes', maximum=700, required=False),
        'corners': lambda x: calibrated_quad(x, 'Wall corners'),
        'foreground': lambda x: [validate_polygon(poly, 'Foreground outline') for poly in
                                  sequence(x, 'Foreground outlines', maximum=12)],
    })


def new_shot(photo, name, role='reference', corners=None):
    defaults = {'name':name, 'role':role, 'note':'', 'corners':deepcopy(DEFAULT_CORNERS), 'foreground':[]}
    payload = {'name':name, 'role':role}
    if corners is not None:
        payload['corners'] = corners
    return {**shot_fields(payload, defaults), 'id':new_id(), 'photo':photo, 'captured':timestamp()}


def obstacle_fields(payload, wall, existing=None):
    result = update_fields(mapping(payload, 'Obstacle'), existing or {
        'name':'Nearby fixture', 'kind':'fixture', 'depth':0.0, 'clearance':25.0, 'note':'',
    }, {
        'name': lambda x: text(x, 'Obstacle name'),
        'kind': lambda x: choice(x, OBSTACLE_KINDS, 'obstacle type'),
        'left': lambda x: number(x, 'Left edge in mm', lower=0, upper=wall['width']),
        'bottom': lambda x: number(x, 'Bottom edge in mm', lower=0, upper=wall['height']),
        'width': lambda x: number(x, 'Obstacle width in mm', lower=1, upper=wall['width']),
        'height': lambda x: number(x, 'Obstacle height in mm', lower=1, upper=wall['height']),
        'depth': lambda x: number(x, 'Distance out from wall, mm', lower=0, upper=10_000),
        'clearance': lambda x: number(x, 'Clear space in mm', lower=0, upper=1000),
        'note': lambda x: text(x, 'Obstacle notes', maximum=400, required=False),
    })
    for key in ('left', 'bottom', 'width', 'height'):
        required(result, key, key.capitalize())
    if result['left'] + result['width'] > wall['width'] or result['bottom'] + result['height'] > wall['height']:
        raise InputError('Obstacle must fit inside the measured wall.')
    return result


def swatch_fields(payload, wall, existing=None):
    result = update_fields(mapping(payload, 'Paint sample'), existing or {
        'name':'Wall paint', 'appearance':'#ede8df', 'note':'',
    }, {
        'name': lambda x: text(x, 'Paint sample name'),
        'appearance': lambda x: color(x, 'Intended sample color'),
        'region': lambda x: points(x, 'Paint sample outline'),
        'shot': lambda x: token(x, 'Site photograph'),
        'note': lambda x: text(x, 'Paint sample notes', maximum=400, required=False),
    })
    from .projection import validate_polygon
    result['region'] = validate_polygon(required(result, 'region', 'Paint sample outline'), 'Paint sample outline')
    find(wall['shots'], required(result, 'shot', 'Site photograph'), 'Site photograph')
    return result


def mount_mark_fields(payload, wall, existing=None):
    result = update_fields(mapping(payload, 'Existing fixing'), existing or {
        'name':'Existing hole', 'kind':'hole', 'usable':True, 'note':'',
    }, {
        'name': lambda x: text(x, 'Fixing name'),
        'kind': lambda x: choice(x, ('hole', 'stud', 'rail-end', 'reference'), 'fixing type'),
        'x': lambda x: number(x, 'Horizontal position in mm', lower=0, upper=wall['width']),
        'y': lambda x: number(x, 'Height from floor in mm', lower=0, upper=wall['height']),
        'usable': lambda x: boolean(x, 'Can reuse'),
        'note': lambda x: text(x, 'Fixing notes', maximum=400, required=False),
    })
    required(result, 'x', 'Horizontal position')
    required(result, 'y', 'Height from floor')
    return result


def artwork_fields(payload, existing=None):
    data = mapping(payload, 'Artwork')
    result = update_fields(data, existing or {
        'artist':'', 'inventory':'', 'medium':'', 'description':'', 'year':'', 'crop':deepcopy(DEFAULT_CROP),
        'frame':'oak', 'border':18.0, 'mat':22.0, 'mat_color':'#f6f1e8', 'depth':28.0,
        'attachment':'pair', 'fixing_top':55.0, 'fixing_span':0.55, 'weight':1.0,
    }, {
        'title': lambda x: text(x, 'Artwork title'),
        'artist': lambda x: text(x, 'Artist', required=False),
        'inventory': lambda x: text(x, 'Inventory number', maximum=48, required=False),
        'medium': lambda x: text(x, 'Medium', required=False),
        'year': lambda x: text(x, 'Date of work', maximum=48, required=False),
        'description': lambda x: text(x, 'Label description', maximum=600, required=False),
        'width': lambda x: number(x, 'Outside width in mm', lower=30, upper=9000),
        'height': lambda x: number(x, 'Outside height in mm', lower=30, upper=9000),
        'crop': lambda x: validate_crop(x),
        'frame': lambda x: choice(x, FRAME_FINISHES, 'frame finish'),
        'border': lambda x: number(x, 'Frame border in mm', lower=0, upper=120),
        'mat': lambda x: number(x, 'Mat border in mm', lower=0, upper=220),
        'mat_color': lambda x: color(x, 'Mat color'),
        'depth': lambda x: number(x, 'Frame depth in mm', lower=0, upper=500),
        'attachment': lambda x: choice(x, ATTACHMENTS, 'attachment type'),
        'fixing_top': lambda x: number(x, 'Attachment distance from top in mm', lower=0, upper=4000),
        'fixing_span': lambda x: number(x, 'Attachment spacing as a proportion of width', lower=0.04, upper=0.98),
        'weight': lambda x: number(x, 'Object weight in kg', lower=0, upper=250),
    })
    required(result, 'title', 'Artwork title')
    width = required(result, 'width', 'Outside width')
    height = required(result, 'height', 'Outside height')
    if 2 * (frame_border(result) + result['mat']) >= min(width, height) - 10:
        raise InputError('Reduce the frame or mat width, or enter a larger measured artwork size.')
    if result['attachment'] != 'none' and result['fixing_top'] > height:
        raise InputError('Attachment distance from the top must fit behind the artwork.')
    return result


def validate_crop(value):
    from .projection import calibrated_quad
    return calibrated_quad(value, 'Artwork crop')


def frame_border(artwork):
    return 0 if artwork['frame'] == 'none' else artwork['border']


def new_artwork(payload, photo):
    return {**artwork_fields(payload), 'id':new_id(), 'photo':photo, 'created':timestamp()}


def layout_fields(payload, existing=None):
    return update_fields(mapping(payload, 'Layout'), existing or {
        'name':'Working layout', 'note':'', 'gap':70.0, 'eye_level':1500.0, 'label_gap':45.0,
        'shadow':0.35, 'exposure':0.0,
    }, {
        'name': lambda x: text(x, 'Layout name'),
        'note': lambda x: text(x, 'Layout notes', maximum=700, required=False),
        'gap': lambda x: number(x, 'Desired gap in mm', lower=0, upper=1500),
        'eye_level': lambda x: number(x, 'Eye level in mm', lower=500, upper=2500),
        'label_gap': lambda x: number(x, 'Label gap in mm', lower=0, upper=300),
        'shadow': lambda x: number(x, 'Preview shadow', lower=0, upper=1),
        'exposure': lambda x: number(x, 'Preview art exposure in stops', lower=-2, upper=2),
    })


def new_layout(name, wall_id):
    return {**layout_fields({'name':name}), 'id':new_id(), 'wall':wall_id, 'placements':[],
            'labels':[], 'stations':[], 'observations':[], 'created':timestamp()}


def placement_fields(payload, wall, artwork, existing=None):
    result = update_fields(mapping(payload, 'Placement'), existing or {
        'x':wall['width'] / 2, 'y':min(wall['height'] / 2, 1500),
        'angle':0.0, 'pinned':False, 'visible':True, 'opacity':1.0,
    }, {
        'x': lambda x: number(x, 'Horizontal center in mm', lower=-wall['width'], upper=2*wall['width']),
        'y': lambda x: number(x, 'Height of center in mm', lower=-wall['height'], upper=2*wall['height']),
        'angle': lambda x: number(x, 'Placement angle in degrees', lower=-180, upper=180),
        'pinned': lambda x: boolean(x, 'Pinned placement'),
        'visible': lambda x: boolean(x, 'Include in preview'),
        'opacity': lambda x: number(x, 'Preview opacity', lower=0.0, upper=1.0),
    })
    result['artwork'] = artwork['id']
    if existing and existing['pinned'] and result['pinned']:
        if any(result[key] != existing[key] for key in ('x', 'y', 'angle')):
            raise InputError('Unpin this artwork before moving it.')
    return result


def station_fields(payload, wall, existing=None):
    return update_fields(mapping(payload, 'Viewing position'), existing or {
        'name':'Main viewpoint', 'x':wall['width']/2, 'distance':2200.0, 'height':1500.0,
        'target_x':wall['width']/2, 'target_y':min(wall['height']/2, 1500.0),
        'fov':65.0, 'note':'',
    }, {
        'name': lambda x: text(x, 'Viewing position name'),
        'x': lambda x: number(x, 'Viewer horizontal position, mm', lower=-wall['width'], upper=2*wall['width']),
        'distance': lambda x: number(x, 'Viewer distance from wall in mm', lower=100, upper=40_000),
        'height': lambda x: number(x, 'Viewer eye height in mm', lower=200, upper=2700),
        'target_x': lambda x: number(x, 'Look-at point from left in mm', lower=0, upper=wall['width']),
        'target_y': lambda x: number(x, 'Look-at point from floor in mm', lower=0, upper=wall['height']),
        'fov': lambda x: number(x, 'Horizontal field in degrees', lower=12, upper=120),
        'note': lambda x: text(x, 'Viewing position notes', maximum=350, required=False),
    })


def label_fields(payload, wall, existing=None):
    return update_fields(mapping(payload, 'Label'), existing or {
        'width':160.0, 'height':95.0, 'x':wall['width']/2, 'y':1200.0, 'name':'',
        'style':'paper', 'body':'', 'align':'left', 'type_size':3.5, 'placement':None,
        'ink':'#222020', 'paper':'#faf7ef',
    }, {
        'name': lambda x: text(x, 'Label heading', required=False),
        'width': lambda x: number(x, 'Label width in mm', lower=30, upper=700),
        'height': lambda x: number(x, 'Label height in mm', lower=20, upper=500),
        'x': lambda x: number(x, 'Label center from left in mm', lower=0, upper=wall['width']),
        'y': lambda x: number(x, 'Label center from floor in mm', lower=0, upper=wall['height']),
        'style': lambda x: choice(x, ('paper', 'vinyl'), 'label style'),
        'body': lambda x: text(x, 'Label copy', maximum=600, required=False),
        'align': lambda x: choice(x, ('left', 'center'), 'label alignment'),
        'type_size': lambda x: number(x, 'Body letter height in mm', lower=2, upper=30),
        'placement': lambda x: optional_id(x, 'Artwork placement'),
        'ink': lambda x: color(x, 'Text color'),
        'paper': lambda x: color(x, 'Paper color'),
    })
