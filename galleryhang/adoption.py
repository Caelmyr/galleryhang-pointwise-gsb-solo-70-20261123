"""Track adopted hole-reuse plans and flag reuses that no longer hold.

An adopted plan records, for every reused fixing point, the *identity* of the
hole that was confirmed. Later readings never look for a replacement: if the
artwork moved or the recorded hole changed, the reuse is reported as pending
confirmation and the originally adopted hole stays named in the report.
"""


EPS = 0.01


def stored_plan(scene):
    plan = scene.layout.get('reuse_plan')
    return plan if isinstance(plan, dict) else None


def _dedupe(values):
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def plan_status(scene):
    """Return the adopted plan enriched with a live confirmation state."""
    plan = stored_plan(scene)
    if plan is None:
        return {'present': False, 'assignments': [], 'gaps': [], 'holes': [], 'works': [],
                'summary': {'reused_works': 0, 'reused_holes': 0, 'confirmed': 0, 'pending': 0,
                            'new_hole_points': 0}}
    if not plan.get('adopted'):
        return {'present': False, 'assignments': [], 'gaps': [], 'holes': [], 'works': [],
                'summary': {'reused_works': 0, 'reused_holes': 0, 'confirmed': 0, 'pending': 0,
                            'new_hole_points': 0}}

    marks = {mark['id']: mark for mark in scene.wall['mount_marks']}
    placements = {placement['id']: placement for placement in scene.layout['placements']}
    assignments = []
    for row in plan.get('assignments', []):
        reasons = []
        placement = placements.get(row['placement'])
        current = None
        if placement is None:
            reasons.append('artwork placement removed')
        else:
            current = {key: round(float(placement[key]), 2) for key in ('x', 'y', 'angle')}
            if any(abs(placement[key] - row['final'][key]) > EPS for key in ('x', 'y', 'angle')):
                reasons.append('artwork moved after adoption')

        holes = []
        for hole in row.get('holes', []):
            mark = marks.get(hole['id'])
            if mark is None:
                reasons.append(f"hole {hole['number']} was removed")
                holes.append({**hole, 'current': None, 'state': 'missing'})
                continue
            moved = abs(mark['x'] - hole['x']) > EPS or abs(mark['y'] - hole['y']) > EPS
            reusable = mark['kind'] == 'hole' and mark['usable']
            if moved:
                reasons.append(f"hole {hole['number']} position changed")
                state = 'changed'
            elif not reusable:
                reasons.append(f"hole {hole['number']} is no longer marked reusable")
                state = 'unavailable'
            else:
                state = 'confirmed'
            holes.append({**hole,
                          'current': {'x': round(float(mark['x']), 2), 'y': round(float(mark['y']), 2),
                                      'usable': bool(mark['usable']), 'kind': mark['kind']},
                          'state': state})
        reasons = _dedupe(reasons)
        assignments.append({**row, 'status': 'pending' if reasons else 'confirmed',
                            'reasons': reasons, 'current': current, 'holes': holes,
                            'note': 'No other hole was substituted automatically.' if reasons else ''})

    states = {row['placement']: row['status'] for row in assignments}
    gaps = []
    for gap in plan.get('gaps', []):
        touched = [states.get(gap.get('first')), states.get(gap.get('second'))]
        state = 'pending' if 'pending' in touched else 'confirmed'
        gaps.append({**gap, 'status': state})

    base = plan.get('summary', {})
    summary = {
        'works': int(base.get('works', 0)),
        'eligible_works': int(base.get('eligible_works', 0)),
        'reused_works': len(assignments),
        'reused_holes': sum(len(row.get('holes', [])) for row in assignments),
        'new_hole_points': int(base.get('new_hole_points', 0)),
        'works_moved': int(base.get('works_moved', 0)),
        'max_shift_used': base.get('max_shift_used', 0),
        'gaps_changed': int(base.get('gaps_changed', 0)),
        'max_gap_change': base.get('max_gap_change', 0),
        'confirmed': sum(row['status'] == 'confirmed' for row in assignments),
        'pending': sum(row['status'] == 'pending' for row in assignments),
    }
    return {'present': True, 'id': plan.get('id'), 'created': plan.get('created'),
            'adopted': True, 'tolerance_mm': plan.get('tolerance_mm'),
            'max_shift_mm': plan.get('max_shift_mm'), 'wall_size': plan.get('wall_size'),
            'assignments': assignments, 'gaps': gaps,
            'holes': plan.get('holes', []), 'works': plan.get('works', []),
            'summary': summary}

