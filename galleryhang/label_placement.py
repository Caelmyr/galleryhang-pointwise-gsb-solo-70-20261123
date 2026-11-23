"""Right-side label proposals and anchored follow behavior.

A label anchored to an artwork keeps a fixed position relative to the work:
the offset is stored in the artwork's local frame, so a rotated hang carries
the card around with the frame. When the artwork is nudged inside its existing
single-piece hang, the label moves with it unless the new position would cover
a neighbouring artwork, another label, a wall feature or leave the measured
wall. In that case the label stays put and the blocking item is reported back
through the checks.
"""
from math import atan, cos, degrees, radians, sin

import numpy as np

from .clearance import EPS, nearest_points, obstacle_outline, rect_bounds
from .label_print import PAPER_FORMATS, PAGE_MARGIN, TOP_TITLE
from .labels import label_lines
from .layout import footprint, local_to_wall
from .model import InputError, choice, find


# Two card stocks held in the hand. Each card is trimmed inside the printable
# area of its sheet, so every proposed card is print-ready on that paper.
CARD_FORMATS = {
    'narrow': {'paper': 'a4', 'width': 130.0, 'height': 95.0},
    'wide': {'paper': 'letter', 'width': 185.0, 'height': 110.0},
}
# Body letter heights tried when fitting the complete copy onto a card.
TYPE_GRID = (2.4, 2.6, 2.8, 3.0, 3.2, 3.5, 3.8, 4.0, 4.3, 4.6, 5.0, 5.5, 6.0, 6.5, 7.0, 8.0)
# A letter is comfortably read when it subtends at least six arc minutes.
LEGIBLE_ARCMIN = 6.0


def printable_area(paper):
    width, height = PAPER_FORMATS[paper]
    return width - 2 * PAGE_MARGIN, height - 2 * PAGE_MARGIN - TOP_TITLE


def card_specs():
    rows = []
    for key, spec in CARD_FORMATS.items():
        area = printable_area(spec['paper'])
        rows.append({
            'card': key, 'paper': spec['paper'],
            'paper_size_mm': list(PAPER_FORMATS[spec['paper']]),
            'width': spec['width'], 'height': spec['height'],
            'fits_paper': spec['width'] <= area[0] and spec['height'] <= area[1],
        })
    return rows


def label_polygon(label):
    """Wall polygon of a label, honouring the anchored rotation (degrees)."""
    half_w, half_h = label['width'] / 2, label['height'] / 2
    corners = np.array([[-half_w, half_h], [half_w, half_h],
                        [half_w, -half_h], [-half_w, -half_h]])
    theta = radians(label.get('angle', 0.0))
    turn = np.array([[cos(theta), -sin(theta)], [sin(theta), cos(theta)]])
    return corners @ turn.T + np.array([label['x'], label['y']])


def local_offset(label, placement):
    """Label center offset in the artwork's own (rotated) coordinate frame."""
    delta = np.array([label['x'], label['y']]) - np.array([placement['x'], placement['y']])
    theta = radians(placement['angle'])
    inverse = np.array([[cos(theta), sin(theta)], [-sin(theta), cos(theta)]])
    return inverse @ delta


def follow_anchor(label):
    block = label.get('follow_blocked')
    return {
        'anchored': bool(label.get('follow')),
        'paper': label.get('paper_format'),
        'local_offset_mm': [round(label['follow']['dx'], 2), round(label['follow']['dy'], 2)]
        if label.get('follow') else None,
        'angle': label.get('angle', 0.0),
        'follows': bool(label.get('follow')) and not block,
        'blocked': block or None,
    }


def fit_copy(scene, label, width, height):
    """Largest body letter height that keeps the complete copy on the card."""
    best = None
    for size in TYPE_GRID:
        trial = {**label, 'width': width, 'height': height, 'type_size': size}
        lines, _padding, total, available = label_lines(
            scene, trial, max(1, round(width * 3)), max(1, round(height * 3)))
        if total <= available + EPS:
            best = {'type_size': size, 'lines': len(lines),
                    'used_mm': round(total / 3, 2), 'usable_mm': round(available / 3, 2)}
    return best


def station_legibility(label, station):
    dx = label['x'] - station['x']
    dy = label['y'] - station['height']
    distance = float(np.linalg.norm([dx, dy, station['distance']]))
    angle = 2 * degrees(atan(label['type_size'] / (2 * distance))) * 60
    return {'station': station['id'], 'name': station['name'],
            'distance_mm': round(distance, 1), 'body_angle_arcmin': round(angle, 2),
            'legible': angle >= LEGIBLE_ARCMIN}


def required_type_size(station):
    """Smallest letter height (mm) reaching six arc minutes at a station."""
    return station['distance'] * np.tan(np.radians(LEGIBLE_ARCMIN / 60))


def artwork_polygons(scene, *, ignore_placement=None):
    rows = []
    for placement, artwork in scene.works():
        if placement['id'] == ignore_placement:
            continue
        rows.append({'id': placement['id'], 'kind': 'artwork',
                     'name': artwork['title'], 'corners': footprint(placement, artwork)})
    return rows


def label_polygons(scene, *, ignore_label=None):
    rows = []
    for other in scene.layout['labels']:
        if other['id'] == ignore_label:
            continue
        rows.append({'id': other['id'], 'kind': 'label',
                     'name': other['name'] or 'Artwork label', 'corners': label_polygon(other)})
    return rows


def collision_report(scene, candidate, *, ignore_label=None, ignore_placement=None):
    """Everything that the candidate label polygon would cover or crowd.

    ``ignore_placement`` is the artwork the card belongs to: the design gap to
    that frame is intentional, so it is never reported as a blocker.
    """
    outline = label_polygon(candidate)
    bounds = rect_bounds(outline)
    blockers = []
    label_gap = scene.layout['label_gap']
    for item in [*artwork_polygons(scene, ignore_placement=ignore_placement),
                 *label_polygons(scene, ignore_label=ignore_label)]:
        distance, first, second = nearest_points(outline, item['corners'])
        if distance + EPS < label_gap:
            blockers.append({'id': item['id'], 'kind': item['kind'], 'name': item['name'],
                             'reason': 'touching or overlapping' if distance <= EPS else 'short gap',
                             'distance_mm': round(distance, 2), 'required_mm': round(label_gap, 2),
                             'at': None if first is None else
                             [[round(float(v), 2) for v in first],
                              [round(float(v), 2) for v in second]]})
    for obstacle in scene.wall['obstacles']:
        poly = obstacle_outline(obstacle)
        distance, first, second = nearest_points(outline, poly)
        if distance + EPS < obstacle['clearance']:
            blockers.append({'id': obstacle['id'], 'kind': 'obstacle', 'name': obstacle['name'],
                             'reason': 'inside required feature clearance' if distance > EPS
                             else 'touching or overlapping',
                             'distance_mm': round(distance, 2),
                             'required_mm': round(obstacle['clearance'], 2),
                             'at': None if first is None else
                             [[round(float(v), 2) for v in first],
                              [round(float(v), 2) for v in second]]})
    wall = scene.wall
    if bounds[0] < -EPS:
        blockers.append(wall_block('left edge', bounds[0], 0))
    if bounds[1] < wall['baseboard'] - EPS:
        blockers.append(wall_block('baseboard', bounds[1], wall['baseboard']))
    if bounds[2] > wall['width'] + EPS:
        blockers.append(wall_block('right edge', bounds[2], wall['width']))
    if bounds[3] > wall['height'] + EPS:
        blockers.append(wall_block('top edge', bounds[3], wall['height']))
    return {'blocked': bool(blockers), 'blockers': blockers,
            'bounds': [round(value, 2) for value in bounds]}


def wall_block(name, found, limit):
    beyond = round(abs(limit - found), 2)
    return {'id': None, 'kind': 'wall', 'name': 'Wall ' + name,
            'reason': 'runs past the measured wall edge',
            'distance_mm': 0.0, 'required_mm': 0.0, 'at': None,
            'detail': {'edge': name, 'beyond_mm': beyond}}


def right_side_candidate(scene, label, placement, artwork, spec, *, gap=None):
    """Card centered on the artwork, immediately to its right at label_gap."""
    gap = scene.layout['label_gap'] if gap is None else gap
    local_right = np.array([artwork['width'] / 2, 0.0])
    right_point = local_to_wall(placement, [local_right])[0]
    theta = radians(placement['angle'])
    direction = np.array([cos(theta), sin(theta)])
    center = right_point + direction * (gap + spec['width'] / 2)
    return {**label, 'width': spec['width'], 'height': spec['height'],
            'x': round(float(center[0]), 4), 'y': round(float(center[1]), 4),
            'angle': placement['angle']}


def build_proposal(scene, label, placement, card_key, station_id=None):
    spec = CARD_FORMATS[card_key]
    artwork = scene.artworks[placement['artwork']]
    candidate = right_side_candidate(scene, label, placement, artwork, spec)
    fitting = fit_copy(scene, candidate, spec['width'], spec['height'])
    if fitting is None:
        raise InputError('The complete label copy cannot fit this card. Shorten the copy or choose another card.')
    candidate['type_size'] = fitting['type_size']
    checks = collision_report(scene, candidate, ignore_label=label['id'],
                              ignore_placement=placement['id'])
    stations = scene.layout['stations']
    if station_id:
        station = find(stations, station_id, 'Viewing position')
    else:
        station = stations[0] if stations else None
    legibility = station_legibility(candidate, station) if station else None
    needed = round(required_type_size(station), 2) if station else None
    return {
        'card': card_key, 'paper': spec['paper'],
        'paper_size_mm': list(PAPER_FORMATS[spec['paper']]),
        'width': spec['width'], 'height': spec['height'],
        'type_size': fitting['type_size'], 'heading_size': round(fitting['type_size'] * 1.33, 2),
        'fitting': fitting,
        'position_mm': [candidate['x'], candidate['y']],
        'gap_to_work_mm': round(scene.layout['label_gap'], 2),
        'angle': candidate['angle'],
        'collision': checks,
        'legibility': legibility,
        'required_type_size_mm': needed,
        'meets_viewpoint': bool(legibility and legibility['legible']),
    }


def label_proposal(scene, label_id, station_id=None):
    label = find(scene.layout['labels'], label_id, 'Label')
    if not label.get('placement'):
        raise InputError('Link this label to an artwork before proposing a right-side card.')
    placement = find(scene.layout['placements'], label['placement'], 'Artwork placement')
    options = [build_proposal(scene, label, placement, key, station_id) for key in CARD_FORMATS]
    clear = [row for row in options if not row['collision']['blocked']]
    preferred = next((row for row in clear if row['card'] == 'wide'), None)
    if preferred is None:
        preferred = next((row for row in clear if row['card'] == 'narrow'), None)
    if preferred is None:
        preferred = min(options, key=lambda row: len(row['collision']['blockers']))
    for row in options:
        row['preferred'] = row is preferred
    return {
        'label': label_id,
        'placement': placement['id'],
        'cards': card_specs(),
        'options': options,
        'preferred': preferred['card'],
        'viewpoint': preferred['legibility']['name'] if preferred['legibility'] else None,
        'summary': proposal_summary(preferred),
    }


def proposal_summary(option):
    if option['collision']['blocked']:
        names = ', '.join(sorted({row['name'] for row in option['collision']['blockers']}))
        fit_note = f'blocked by {names}'
    elif option['meets_viewpoint']:
        fit_note = 'clear of neighbours and legible at the selected viewpoint'
    else:
        fit_note = 'clear of neighbours but the type is small at the selected viewpoint'
    return (f"{option['card']} card {option['width']:.0f} × {option['height']:.0f} mm on "
            f"{option['paper'].upper()}, body {option['type_size']:.1f} mm, "
            f"{option['gap_to_work_mm']:.0f} mm to the right of the work — {fit_note}.")


def anchor_label(label, placement):
    dx, dy = local_offset(label, placement)
    label['follow'] = {
        'placement': placement['id'],
        'dx': round(float(dx), 4), 'dy': round(float(dy), 4),
    }
    label['angle'] = placement['angle']
    label['paper_format'] = label.get('paper_format') or CARD_FORMATS['narrow']['paper']


def detach_label(label):
    label.pop('follow', None)
    label.pop('follow_blocked', None)


def adopt_proposal(scene, label_id, card_key, station_id=None):
    card_key = choice(card_key, tuple(CARD_FORMATS), 'card format')
    label = find(scene.layout['labels'], label_id, 'Label')
    if not label.get('placement'):
        raise InputError('Link this label to an artwork before adopting a card.')
    placement = find(scene.layout['placements'], label['placement'], 'Artwork placement')
    proposal = build_proposal(scene, label, placement, card_key, station_id)
    if proposal['collision']['blocked']:
        names = ', '.join(row['name'] for row in proposal['collision']['blockers'])
        raise InputError(f'The {card_key} card cannot be adopted here: {names}.')
    label.update({
        'width': proposal['width'], 'height': proposal['height'],
        'x': proposal['position_mm'][0], 'y': proposal['position_mm'][1],
        'angle': proposal['angle'], 'type_size': proposal['type_size'],
        'style': 'paper', 'paper_format': proposal['paper'],
    })
    anchor_label(label, placement)
    label.pop('follow_blocked', None)
    return label, proposal


def anchored_position(label, placement):
    follow = label['follow']
    point = local_to_wall(placement, [[follow['dx'], follow['dy']]])[0]
    return {**label, 'x': round(float(point[0]), 4),
            'y': round(float(point[1]), 4), 'angle': placement['angle']}


def follow_moved_placement(scene, placement):
    """Move every anchored label that still has clear space; pin the rest."""
    moved, held = [], []
    for label in scene.layout['labels']:
        follow = label.get('follow')
        if not follow or follow.get('placement') != placement['id']:
            continue
        candidate = anchored_position(label, placement)
        checks = collision_report(scene, candidate, ignore_label=label['id'],
                                  ignore_placement=label.get('placement'))
        if checks['blocked']:
            label['follow_blocked'] = {
                'placement': placement['id'],
                'attempted_mm': [candidate['x'], candidate['y']],
                'blockers': checks['blockers'],
            }
            held.append({'id': label['id'], 'name': label['name'] or 'Artwork label',
                         'blockers': checks['blockers']})
            continue
        label['x'], label['y'], label['angle'] = candidate['x'], candidate['y'], candidate['angle']
        label.pop('follow_blocked', None)
        moved.append(label['id'])
    return {'moved': moved, 'held': held}


def follow_report(scene):
    """Snapshot of anchored labels for the checks panel."""
    rows = []
    for label in scene.layout['labels']:
        if not label.get('follow'):
            continue
        placement = find(scene.layout['placements'], label['follow']['placement'], 'Artwork placement')
        rows.append({
            'id': label['id'],
            'name': label['name'] or scene.artworks.get(placement['artwork'], {}).get('title', 'Artwork label'),
            **follow_anchor(label),
            'placement': placement['id'],
            'position_mm': [label['x'], label['y']],
        })
    return {'anchored': len(rows),
            'blocked': sum(1 for row in rows if row['blocked']),
            'labels': rows}
