"""Recommend which existing wall holes a rehang can reuse.

The planner is deliberately narrow:

* only artworks whose measured attachment has two fixing points take part;
* the two fixing points are trial-fitted as one rigid pair against two
  *distinct* reusable holes, each point centre within ``tolerance`` of its hole;
* a work may only move horizontally, by at most ``max_shift`` millimetres, and
  keeps its height, rotation and left-to-right order;
* a work that fits no pair is counted as two new holes, and every position still
  has to clear obstacles, the baseboard and the layout's minimum gap.

Search is a small beam over the works in wall order.  Reusing a pair of holes is
worth far more than any movement; among equal reuse the score keeps movement
small and pulls neighbouring horizontal shifts toward each other, so a reused
row does not read as obviously uneven.
"""

from math import sqrt

from .adoption import plan_status
from .clearance import bounds_distance, item_checks, nearest_points, rect_bounds
from .layout import Scene, footprint, label_footprint, local_to_wall
from .model import InputError, find, new_id, number, timestamp
from .mounts import restricted_at, source_attachment
from .store import document, sha256


DEFAULT_TOLERANCE = 3.0
DEFAULT_MAX_SHIFT = 80.0
MOVE_WEIGHT = 0.02          # per mm² of horizontal movement
SMOOTH_WEIGHT = 1.0         # per mm² of change between adjoining shifts, which is the gap change
BAND_MM = 1.5               # a gap may change this far before the extra cost
BAND_WEIGHT = 60.0          # per mm² beyond the band
BEAM_WIDTH = 400
MAX_PAIRS = 8
EPS = 1e-6


def _rounded(value):
    return round(float(value), 2)


def _hole_sort_key(mark):
    return (mark['x'], mark['y'], mark['id'])


def hole_catalog(wall):
    """Number every recorded hole left to right and note whether it is reusable."""
    marks = sorted((mark for mark in wall['mount_marks'] if mark['kind'] == 'hole'), key=_hole_sort_key)
    rows = []
    for number, mark in enumerate(marks, 1):
        obstacles = restricted_at(wall, mark['x'], mark['y'])
        rows.append({
            'id': mark['id'],
            'number': number,
            'name': mark['name'],
            'x': _rounded(mark['x']),
            'y': _rounded(mark['y']),
            'usable': bool(mark['usable']),
            'restricted': obstacles,
            'available': bool(mark['usable']) and not obstacles,
        })
    return rows


def eligible_work(artwork):
    return artwork['attachment'] == 'pair' and len(source_attachment(artwork)) == 2


def fixing_points(placement, artwork):
    """Wall coordinates of the two fixing points for a placement."""
    designs = source_attachment(artwork)
    coordinates = local_to_wall(placement, [[x, y] for _, x, y in designs])
    return [{'name': name, 'x': float(x), 'y': float(y)}
            for (name, _, _), (x, y) in zip(designs, coordinates)]


def _shift_interval(point, hole, tolerance):
    """Horizontal shifts that keep a fixing point within tolerance of a hole."""
    delta_x = hole['x'] - point['x']
    delta_y = hole['y'] - point['y']
    if abs(delta_y) > tolerance + EPS:
        return None
    reach = sqrt(max(0.0, tolerance * tolerance - delta_y * delta_y))
    return delta_x - reach, delta_x + reach


def _merge_intervals(intervals):
    lower = max(item[0] for item in intervals)
    upper = min(item[1] for item in intervals)
    return (lower, upper) if lower <= upper + EPS else None


def candidate_pairs(holes, placement, artwork, *, tolerance, max_shift, pinned):
    """Trial-fit both fixing points against distinct reusable hole pairs."""
    available = [hole for hole in holes if hole['available']]
    if len(available) < 2:
        return []
    points = fixing_points(placement, artwork)
    points.sort(key=lambda point: point['x'])
    reach = max_shift + tolerance
    near = [hole for hole in available
            if any(abs(hole['x'] - point['x']) <= reach for point in points)
            and any(abs(hole['y'] - point['y']) <= tolerance + EPS for point in points)]
    if len(near) < 2:
        return []

    pairs = []
    ordered = sorted(near, key=lambda hole: (hole['x'], hole['y'], hole['id']))
    for first_index in range(len(ordered)):
        for second_index in range(first_index + 1, len(ordered)):
            left, right = ordered[first_index], ordered[second_index]
            if left['id'] == right['id']:
                continue
            interval = _merge_intervals([value for value in (
                _shift_interval(points[0], left, tolerance),
                _shift_interval(points[1], right, tolerance),
            ) if value is not None])
            if interval is None:
                continue
            lower = max(interval[0], -max_shift)
            upper = min(interval[1], max_shift)
            if lower > upper + EPS:
                continue
            if pinned:
                # A pinned work may be reused only if the recorded holes already
                # sit within tolerance of its current fixed position.
                if not lower - EPS <= 0.0 <= upper + EPS:
                    continue
                pairs.append({
                    'holes': [left, right],
                    'shift': 0.0,
                    'level_error': _rounded(max(abs(left['y'] - points[0]['y']),
                                                abs(right['y'] - points[1]['y']))),
                    'movement': 0.0,
                })
                continue
            idle = min(max(0.0, lower), upper)
            candidates = sorted({round(value, 4) for value in (lower, idle, (lower + upper) / 2, upper)})
            for shift in candidates:
                if not lower - EPS <= shift <= upper + EPS:
                    continue
                pairs.append({
                    'holes': [left, right],
                    'shift': shift,
                    'level_error': _rounded(max(abs(left['y'] - points[0]['y']),
                                                abs(right['y'] - points[1]['y']))),
                    'movement': _rounded(abs(shift)),
                })
    pairs.sort(key=lambda pair: (pair['level_error'], pair['movement'],
                                 pair['holes'][0]['number'], pair['holes'][1]['number']))
    trimmed = []
    seen = set()
    for pair in pairs:
        key = (pair['holes'][0]['id'], pair['holes'][1]['id'], pair['shift'])
        if key in seen:
            continue
        seen.add(key)
        trimmed.append(pair)
        if len(trimmed) >= MAX_PAIRS:
            break
    return trimmed


def _work_state(scene, placement, artwork, x):
    moved = {**placement, 'x': x}
    corners = footprint(moved, artwork)
    row = item_checks({'id': placement['id'], 'name': artwork['title'], 'kind': 'artwork',
                       'key': artwork['inventory'], 'corners': corners}, scene.wall)
    return {'position': moved, 'corners': corners, 'bounds': rect_bounds(corners),
            'outside': bool(row['outside']), 'obstructions': row['obstructions']}


def _gap_limit(scene, first_kind, second_kind):
    if first_kind == second_kind == 'artwork':
        return scene.layout['gap']
    return min(scene.layout['gap'], scene.layout['label_gap'])


def _too_close(scene, first_kind, first_state, second_kind, second_state):
    limit = _gap_limit(scene, first_kind, second_kind)
    if bounds_distance(first_state['bounds'], second_state['bounds']) > max(limit, EPS):
        return False
    distance, _, _ = nearest_points(first_state['corners'], second_state['corners'])
    return distance < limit - EPS


def _conflicts(scene, state, placed_states, labels):
    """Strict clearance test used for a *reused* position."""
    if state['outside'] or state['obstructions']:
        return True
    for other in placed_states:
        if _too_close(scene, 'artwork', state, 'artwork', other):
            return True
    for label in labels:
        if _too_close(scene, 'artwork', state, 'label', label):
            return True
    return False

def _label_states(scene):
    rows = []
    for label in scene.layout['labels']:
        corners = label_footprint(label)
        rows.append({'corners': corners, 'bounds': rect_bounds(corners)})
    return rows


def _step_cost(option, previous_shift, has_previous):
    """Return (new hole count, smoothness cost) for one step.

    The two parts stay separate so the search can treat ``fewer new holes`` as
    strictly more important than any amount of gap smoothing.
    """
    if not option['reuse']:
        return 2, 0.0
    shift = option['shift']
    cost = MOVE_WEIGHT * shift * shift
    if has_previous:
        deviation = shift - previous_shift
        cost += SMOOTH_WEIGHT * deviation * deviation
        cost += BAND_WEIGHT * max(0.0, abs(deviation) - BAND_MM) ** 2
    return 0, cost


def _state_conflicts(scene, state, labels):
    """Clearance conflicts for one work position in isolation."""
    hits = []
    if state['outside']:
        hits.append('outside wall')
    hits.extend(item['name'] for item in state['obstructions'])
    for label in labels:
        if _too_close(scene, 'artwork', state, 'label', label):
            hits.append('label clearance')
    return hits


def _all_pairs_clear(scene, entries, labels):
    """A reused position must be clean; a stationary pair's old gap is not ours to fix.

    Any pair that involves at least one reused (moved) work is checked, so the
    recommendation never creates a new clearance or gap failure.
    """
    for index, first in enumerate(entries):
        if first['option']['reuse'] and (first['state']['outside'] or first['state']['obstructions']):
            return False
        for second in entries[index + 1:]:
            if not (first['option']['reuse'] or second['option']['reuse']):
                continue
            if _too_close(scene, 'artwork', first['state'], 'artwork', second['state']):
                return False
        if first['option']['reuse']:
            for label in labels:
                if _too_close(scene, 'artwork', first['state'], 'label', label):
                    return False
    return True


def _gaps(scene, planned):
    rows = []
    for index in range(len(planned) - 1):
        first, second = planned[index], planned[index + 1]
        original, _, _ = nearest_points(first['original_state']['corners'], second['original_state']['corners'])
        planned_gap, _, _ = nearest_points(first['state']['corners'], second['state']['corners'])
        change = planned_gap - original
        if abs(change) < 0.005:
            direction = 'unchanged'
        elif change > 0:
            direction = 'wider'
        else:
            direction = 'narrower'
        rows.append({
            'order': index + 1,
            'first': first['placement'],
            'first_title': first['title'],
            'second': second['placement'],
            'second_title': second['title'],
            'original_mm': _rounded(original),
            'planned_mm': _rounded(planned_gap),
            'change_mm': _rounded(abs(change)),
            'direction': direction,
            'minimum_mm': _rounded(scene.layout['gap']),
            'within_minimum': planned_gap >= scene.layout['gap'] - EPS,
            'first_shift_mm': first['shift_mm'],
            'second_shift_mm': second['shift_mm'],
        })
    return rows


def _signature(scene, workspace, tolerance, max_shift):
    payload = {
        'wall': {'id': scene.wall['id'], 'width': scene.wall['width'], 'height': scene.wall['height'],
                 'baseboard': scene.wall['baseboard'], 'datum': scene.wall['datum'],
                 'holes': sorted([[mark['id'], mark['x'], mark['y'], mark['usable'], mark['kind']]
                                  for mark in scene.wall['mount_marks']]),
                 'obstacles': sorted([[item['id'], item['left'], item['bottom'], item['width'],
                                       item['height'], item['clearance']] for item in scene.wall['obstacles']])},
        'layout': {'id': scene.layout['id'], 'gap': scene.layout['gap'],
                   'label_gap': scene.layout['label_gap']},
        'works': workspace,
        'tolerance': tolerance,
        'max_shift': max_shift,
    }
    return sha256(document(payload).encode())


def current_signature(scene, *, tolerance=DEFAULT_TOLERANCE, max_shift=DEFAULT_MAX_SHIFT):
    ordered = sorted(scene.works(), key=lambda pair: (pair[0]['x'], -pair[0]['y'], pair[0]['id']))
    workspace = [{'placement': placement['id'], 'artwork': artwork['id'], 'x': placement['x'],
                  'y': placement['y'], 'angle': placement['angle']} for placement, artwork in ordered]
    return _signature(scene, workspace, float(tolerance), float(max_shift))


def plan_reuse(scene, *, tolerance=DEFAULT_TOLERANCE, max_shift=DEFAULT_MAX_SHIFT):
    """Return one recommended arrangement that reuses as many old holes as possible."""
    tolerance = float(tolerance)
    max_shift = float(max_shift)
    if not 0 <= tolerance <= 25:
        raise InputError('Hole tolerance must be between 0 and 25 mm.')
    if not 0 <= max_shift <= 500:
        raise InputError('Maximum horizontal movement must be between 0 and 500 mm.')

    holes = hole_catalog(scene.wall)
    labels = _label_states(scene)
    ordered = sorted(scene.works(), key=lambda pair: (pair[0]['x'], -pair[0]['y'], pair[0]['id']))
    if not ordered:
        raise InputError('Place artworks on this layout before planning existing holes.')
    if not any(eligible_work(artwork) for _, artwork in ordered):
        raise InputError('No two-point works are placed on this wall. Record a pair attachment on an '
                         'artwork before planning hole reuse.')
    if not any(hole['available'] for hole in holes):
        raise InputError('This wall has no reusable holes recorded. Mark the old holes as reusable fixing '
                         'points before planning hole reuse.')
    workspace = [{'placement': placement['id'], 'artwork': artwork['id'], 'x': placement['x'],
                  'y': placement['y'], 'angle': placement['angle']} for placement, artwork in ordered]
    signature = _signature(scene, workspace, tolerance, max_shift)

    options_by_work = []
    for placement, artwork in ordered:
        options = [{'reuse': False, 'shift': 0.0, 'holes': None}]
        if eligible_work(artwork):
            for pair in candidate_pairs(holes, placement, artwork, tolerance=tolerance,
                                        max_shift=max_shift, pinned=bool(placement['pinned'])):
                options.append({'reuse': True, 'shift': pair['shift'], 'holes': pair['holes'],
                                'level_error': pair['level_error']})
        options_by_work.append(options)

    state_cache = {}
    for index, (placement, artwork) in enumerate(ordered):
        for option_index, option in enumerate(options_by_work[index]):
            state_cache[(index, option_index)] = _work_state(scene, placement, artwork,
                                                             placement['x'] + option['shift'])

    beam = [{'entries': [], 'used': frozenset(), 'new_holes': 0, 'cost': 0.0,
             'shift': 0.0, 'has_shift': False}]
    for index, (placement, artwork) in enumerate(ordered):
        expanded = []
        for candidate in beam:
            for option_index, option in enumerate(options_by_work[index]):
                used = candidate['used']
                if option['reuse']:
                    ids = [hole['id'] for hole in option['holes']]
                    if any(hole_id in used for hole_id in ids):
                        continue
                    used = used | frozenset(ids)
                state = state_cache[(index, option_index)]
                placed_states = [entry['state'] for entry in candidate['entries']]
                if option['reuse'] and _conflicts(scene, state, placed_states, labels):
                    continue
                new_x = state['position']['x']
                if candidate['entries'] and new_x <= candidate['entries'][-1]['state']['position']['x'] + EPS:
                    continue
                shift = option['shift'] if option['reuse'] else 0.0
                added, cost = _step_cost(option, candidate['shift'], candidate['has_shift'])
                entry = {'placement': placement['id'], 'kind': 'artwork', 'title': artwork['title'],
                         'option': option, 'state': state, 'shift': shift}
                expanded.append({'entries': [*candidate['entries'], entry], 'used': used,
                                 'new_holes': candidate['new_holes'] + added,
                                 'cost': candidate['cost'] + cost,
                                 'shift': shift, 'has_shift': True})
        if not expanded:
            break
        expanded.sort(key=lambda item: (item['new_holes'], item['cost'], -len(item['used'])))
        beam = expanded[:BEAM_WIDTH]

    viable = [candidate for candidate in beam
              if len(candidate['entries']) == len(ordered)
              and _all_pairs_clear(scene, candidate['entries'], labels)]
    if not viable:
        raise InputError('No arrangement fits within the wall, clearance and gap settings. '
                         'Reduce the movement limit or check the recorded obstacles.')
    viable.sort(key=lambda item: (item['new_holes'], item['cost'], -len(item['used'])))
    best = viable[0]

    planned = []
    for index, (placement, artwork) in enumerate(ordered):
        entry = best['entries'][index]
        option = entry['option']
        shift = entry['shift']
        original_state = _work_state(scene, placement, artwork, placement['x'])
        holes_used = []
        if option['reuse']:
            points = fixing_points(entry['state']['position'], artwork)
            points.sort(key=lambda point: point['x'])
            for side, hole, point in zip(('left', 'right'), option['holes'], points):
                holes_used.append({'id': hole['id'], 'number': hole['number'], 'name': hole['name'],
                                   'x': hole['x'], 'y': hole['y'], 'side': side,
                                   'point': point['name'],
                                   'point_x': _rounded(point['x']), 'point_y': _rounded(point['y']),
                                   'delta_x': _rounded(point['x'] - hole['x']),
                                   'delta_y': _rounded(point['y'] - hole['y'])})
        new_holes = []
        if not option['reuse']:
            for point in fixing_points(entry['state']['position'], artwork):
                wall_hit = restricted_at(scene.wall, point['x'], point['y'])
                new_holes.append({'name': point['name'], 'x': _rounded(point['x']),
                                  'y': _rounded(point['y']), 'restricted': wall_hit,
                                  'needs_field_check': bool(wall_hit)})
        conflicts = _state_conflicts(scene, entry['state'], labels)
        planned.append({
            'placement': placement['id'],
            'artwork': artwork['id'],
            'title': artwork['title'],
            'inventory': artwork['inventory'],
            'attachment': artwork['attachment'],
            'eligible': eligible_work(artwork),
            'pinned': bool(placement['pinned']),
            'order': index + 1,
            'original': {'x': _rounded(placement['x']), 'y': _rounded(placement['y']),
                         'angle': placement['angle']},
            'final': {'x': _rounded(entry['state']['position']['x']),
                      'y': _rounded(entry['state']['position']['y']),
                      'angle': placement['angle']},
            'shift_mm': _rounded(shift),
            'reused': bool(option['reuse']),
            'holes': holes_used,
            'new_holes': new_holes,
            'status': 'reused pair' if option['reuse'] else 'new pair',
            'conflicts': conflicts,
            'original_state': original_state,
            'state': entry['state'],
        })

    gaps = _gaps(scene, planned)
    used_ids = {hole['id'] for row in planned for hole in row['holes']}
    for hole in holes:
        hole['used_by'] = [row['title'] for row in planned
                           if any(used['id'] == hole['id'] for used in row['holes'])]
        hole['reused'] = hole['id'] in used_ids
        hole['excluded_reason'] = None
        if not hole['reused']:
            if not hole['usable']:
                hole['excluded_reason'] = 'not marked reusable'
            elif hole['restricted']:
                hole['excluded_reason'] = 'inside restricted clearance'
            else:
                hole['excluded_reason'] = 'no work has a two-point fit within tolerance'

    reusable = [row for row in planned if row['reused']]
    new_points = [point for row in planned if not row['reused'] for point in row['new_holes']]
    moved = [row for row in planned if abs(row['shift_mm']) > 0.005]
    summary = {
        'works': len(planned),
        'eligible_works': sum(row['eligible'] for row in planned),
        'reused_works': len(reusable),
        'reused_holes': sum(len(row['holes']) for row in reusable),
        'new_hole_points': len(new_points),
        'works_moved': len(moved),
        'max_shift_used': _rounded(max((abs(row['shift_mm']) for row in planned), default=0.0)),
        'gaps_changed': sum(1 for gap in gaps if gap['direction'] != 'unchanged'),
        'max_gap_change': _rounded(max((gap['change_mm'] for gap in gaps), default=0.0)),
    }
    return {
        'id': None,
        'created': None,
        'signature': signature,
        'tolerance_mm': tolerance,
        'max_shift_mm': max_shift,
        'wall': scene.wall['name'],
        'wall_size': [_rounded(scene.wall['width']), _rounded(scene.wall['height'])],
        'minimum_gap_mm': _rounded(scene.layout['gap']),
        'holes': holes,
        'works': [{key: value for key, value in row.items() if key not in ('original_state', 'state')}
                  for row in planned],
        'assignments': [{'placement': row['placement'], 'title': row['title'],
                         'final': row['final'], 'holes': row['holes'],
                         'shift_mm': row['shift_mm']} for row in reusable],
        'gaps': [{key: value for key, value in gap.items()
                  if key not in ('first_shift_mm', 'second_shift_mm')} for gap in gaps],
        'summary': summary,
        'missing_attachment': [artwork['title'] for placement, artwork in ordered
                               if not eligible_work(artwork)],
        'checks': {'tolerance_mm': tolerance, 'max_shift_mm': max_shift,
                   'clearance_ok': not any(row['conflicts'] for row in planned),
                   'order_kept': True, 'height_kept': True, 'rotation_kept': True},
    }


def save_plan(project, layout_id, payload):
    scene = Scene.build(project, layout_id)
    tolerance = number(payload.get('tolerance', DEFAULT_TOLERANCE), 'Hole tolerance in mm',
                       lower=0, upper=25)
    max_shift = number(payload.get('max_shift', DEFAULT_MAX_SHIFT), 'Maximum horizontal movement in mm',
                       lower=0, upper=500)
    plan = plan_reuse(scene, tolerance=tolerance, max_shift=max_shift)
    plan['id'] = new_id()
    plan['created'] = timestamp()
    plan['adopted'] = False
    scene.layout['reuse_plan'] = plan
    return plan, (f"Planned reuse of {plan['summary']['reused_holes']} existing holes "
                  f"across {plan['summary']['reused_works']} works")


def adopt_plan(project, layout_id, payload):
    scene = Scene.build(project, layout_id)
    plan = scene.layout.get('reuse_plan')
    if not isinstance(plan, dict):
        raise InputError('Plan the reuse of existing holes before adopting it.')
    requested = payload.get('plan')
    if requested not in (None, '') and requested != plan.get('id'):
        raise InputError('A newer hole-reuse plan was saved. Recalculate before adopting.')
    if plan.get('adopted'):
        raise InputError('This hole-reuse plan was already adopted.')
    if current_signature(scene, tolerance=plan['tolerance_mm'],
                         max_shift=plan['max_shift_mm']) != plan['signature']:
        raise InputError('The wall or layout changed since this plan was made. Recalculate the plan.')
    recomputed = plan_reuse(scene, tolerance=plan['tolerance_mm'], max_shift=plan['max_shift_mm'])
    if recomputed['signature'] != plan['signature']:
        raise InputError('The wall or layout changed since this plan was made. Recalculate the plan.')
    if not recomputed['checks']['clearance_ok']:
        raise InputError('The plan still leaves a work against an obstacle. Adjust it before adopting.')

    from .layout import placement_fields

    moved = []
    for row in plan['works']:
        placement = find(scene.layout['placements'], row['placement'], 'Artwork placement')
        final = row['final']
        if any(abs(placement[key] - final[key]) > 0.005 for key in ('x', 'y', 'angle')):
            if placement['pinned']:
                raise InputError(f"Unpin {row['title']} before adopting a plan that moves it.")
            fields = placement_fields({'x': final['x'], 'y': final['y'], 'angle': final['angle']},
                                      scene.wall, scene.artworks[placement['artwork']], placement)
            placement.update(fields)
            moved.append(row['title'])

    plan['adopted'] = True
    plan['options'] = {'tolerance': plan['tolerance_mm'], 'max_shift': plan['max_shift_mm']}
    scene.layout['reuse_plan'] = plan
    message = (f"Adopted reuse of {plan['summary']['reused_holes']} existing holes; "
               f"{len(moved)} work(s) shifted")
    return plan, message


def reuse_report(scene):
    plan = scene.layout.get('reuse_plan')
    status = plan_status(scene)
    saved = None
    if plan:
        saved = {key: plan[key] for key in ('id', 'created', 'adopted', 'tolerance_mm', 'max_shift_mm',
                                            'summary', 'works', 'gaps', 'holes', 'checks')
                 if key in plan}
    if status['present']:
        # The works have already been moved to the adopted positions; recomputing
        # would chase a second shift, so the saved plan is the recommendation.
        return {'plan': saved, 'status': status, 'preview': saved}
    try:
        preview = plan_reuse(scene)
    except InputError as error:
        preview = {'error': str(error)}
    return {'plan': saved, 'status': status, 'preview': preview}
