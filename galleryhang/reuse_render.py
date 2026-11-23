"""Draw the recommended hole-reuse plan as an annotated elevation.

The elevation shows the recommended (moved) positions; the original frame
outline is kept as a faint ghost with an arrow so the movement is visible.
"""

from copy import deepcopy

from PIL import ImageDraw

from .labels import font
from .layout import Scene, footprint
from .model import InputError
from .projection import wall_to_pixels
from .render import add_rulers, elevation, plot_cross
from .reuse import plan_reuse


REUSED = '#2f7d5b'
NEW = '#b84a2c'
SYMBOL = '#2b2722'
MUTED = '#6d675b'
GHOST = '#8b7f6b'


def _shift_text(shift):
    if abs(shift) < 0.005:
        return 'stays put'
    direction = 'left' if shift < 0 else 'right'
    return f'{abs(shift):g} mm {direction}'


def _gap_text(gap):
    if gap['direction'] == 'unchanged':
        return f"gap {gap['original_mm']:g} mm unchanged"
    return (f"gap {gap['original_mm']:g} → {gap['planned_mm']:g} mm "
            f"({gap['change_mm']:g} mm {gap['direction']})")


def _callout(draw, position, lines, size):
    x, y = position
    face = font(13)
    heading = font(13, heading=True)
    box_w = max(int(draw.textlength(line, font=heading if index == 0 else face))
                for index, line in enumerate(lines)) + 16
    box_h = 17 * len(lines) + 8
    anchor_x = x + 14 if x + 14 + box_w < size[0] - 8 else x - 14 - box_w
    anchor_y = min(max(6, y - box_h / 2), size[1] - box_h - 6)
    draw.rounded_rectangle((anchor_x, anchor_y, anchor_x + box_w, anchor_y + box_h), radius=4,
                           fill='#fbf7ec', outline='#b7a98e')
    draw.line((x, y, anchor_x + (0 if anchor_x > x else box_w), anchor_y + box_h / 2),
              fill=SYMBOL, width=1)
    for index, line in enumerate(lines):
        draw.text((anchor_x + 8, anchor_y + 5 + index * 17), line,
                  font=heading if index == 0 else face,
                  fill=SYMBOL if index == 0 else MUTED)


def planned_scene(scene, plan):
    project = deepcopy(scene.project)
    layout = next(row for row in project['layouts'] if row['id'] == scene.layout['id'])
    by_id = {row['placement']: row for row in plan['works']}
    for placement in layout['placements']:
        row = by_id.get(placement['id'])
        if row:
            placement['x'] = row['final']['x']
    return Scene.build(project, layout['id'])


def reuse_diagram(blobs, scene, *, edge=1450, tolerance=3.0, max_shift=80.0):
    from .adoption import plan_status

    status = plan_status(scene)
    if status['present']:
        # After adoption the works already sit at their final positions; show the
        # adopted plan itself rather than planning a second shift.
        plan = {**scene.layout['reuse_plan'], 'holes': status['holes']}
        works = []
        for row in plan['works']:
            live = next((item for item in status['assignments'] if item['placement'] == row['placement']), None)
            works.append({**row, 'reuse_state': live['status'] if live else 'confirmed',
                          'reuse_reasons': live.get('reasons', []) if live else []})
        plan = {**plan, 'works': works}
    else:
        try:
            plan = plan_reuse(scene, tolerance=tolerance, max_shift=max_shift)
        except InputError as error:
            raise InputError(str(error)) from error
    target = planned_scene(scene, plan)
    canvas = elevation(blobs, target, edge=edge).convert('RGB')
    draw = ImageDraw.Draw(canvas)
    size = canvas.size

    ghost = ImageDraw.Draw(canvas)
    for row in plan['works']:
        if abs(row['shift_mm']) <= 0.005:
            continue
        artwork = scene.artworks[row['artwork']]
        corners = footprint(row['original'], artwork)
        points = [tuple(value) for value in wall_to_pixels(scene.wall, size, corners)]
        ghost.line([*points, points[0]], fill=GHOST, width=2)
        for start, end in zip(points, points[1:] + points[:1]):
            ghost.line((start, end), fill=GHOST, width=2)

    for hole in plan['holes']:
        x, y = wall_to_pixels(scene.wall, size, [hole['x'], hole['y']])
        if hole['reused']:
            plot_cross(draw, (x, y), 10, '#163f2f', width=5)
            plot_cross(draw, (x, y), 10, REUSED, width=3)
        else:
            tone = '#a29a8b' if not hole['available'] else '#7a7263'
            draw.ellipse((x - 6, y - 6, x + 6, y + 6), outline='#28231e', width=3)
            draw.ellipse((x - 6, y - 6, x + 6, y + 6), outline=tone, width=1)
        draw.text((x + 11, y - 9), f"#{hole['number']}", font=font(13, heading=True),
                  fill='#20201c', stroke_width=2, stroke_fill='#f4eee1')

    for row in plan['works']:
        center_final = wall_to_pixels(scene.wall, size, [row['final']['x'], row['final']['y']])
        center_original = wall_to_pixels(scene.wall, size, [row['original']['x'], row['original']['y']])
        if abs(row['shift_mm']) > 0.005:
            draw.line((tuple(center_original), tuple(center_final)), fill='#c47b3a', width=3)
            draw.ellipse((center_original[0] - 4, center_original[1] - 4,
                          center_original[0] + 4, center_original[1] + 4), outline='#c47b3a', width=2)
        for point in row['holes']:
            px, py = wall_to_pixels(scene.wall, size, [point['point_x'], point['point_y']])
            draw.line((px - 6, py, px + 6, py), fill=REUSED, width=3)
            draw.line((px, py - 6, px, py + 6), fill=REUSED, width=3)
        for point in row['new_holes']:
            px, py = wall_to_pixels(scene.wall, size, [point['x'], point['y']])
            draw.ellipse((px - 7, py - 7, px + 7, py + 7), outline=NEW, width=2)
        cx, cy = center_final
        draw.rounded_rectangle((cx - 15, cy - 15, cx + 15, cy + 15), radius=5, fill='#211f1c')
        draw.text((cx, cy), str(row['order']), font=font(15, heading=True), fill='#fff8eb', anchor='mm')
        lines = [f"{row['order']}. {row['title']}"]
        if row['reused']:
            numbers = ', '.join(f"#{hole['number']}" for hole in row['holes'])
            suffix = ''
            if row.get('reuse_state') == 'pending':
                suffix = ' · pending confirmation'
            lines.append(f"reuses {numbers} · {_shift_text(row['shift_mm'])}{suffix}")
        elif row['eligible']:
            lines.append('no pair fits · 2 new holes')
        else:
            lines.append(f"not a two-point work · {len(row['new_holes'])} new hole"
                         f"{'s' if len(row['new_holes']) != 1 else ''}")
        _callout(draw, (cx + 18, cy - 20), lines, size)

    for gap in plan['gaps']:
        first = next(row for row in plan['works'] if row['placement'] == gap['first'])
        second = next(row for row in plan['works'] if row['placement'] == gap['second'])
        artwork_a = scene.artworks[first['artwork']]
        artwork_b = scene.artworks[second['artwork']]
        mid_x = (first['final']['x'] + artwork_a['width'] / 2
                 + second['final']['x'] - artwork_b['width'] / 2) / 2
        lower = min(first['final']['y'] - artwork_a['height'] / 2,
                    second['final']['y'] - artwork_b['height'] / 2)
        x, y = wall_to_pixels(scene.wall, size, [mid_x, max(0, lower - 70)])
        changed = gap['direction'] != 'unchanged'
        label = _gap_text(gap)
        face = font(13, heading=changed)
        draw.rounded_rectangle((x - draw.textlength(label, font=face) / 2 - 6, y - 11,
                                x + draw.textlength(label, font=face) / 2 + 6, y + 11),
                               radius=4, fill='#f4eee1', outline='#d6c9ad')
        draw.text((x, y), label, font=face,
                  fill='#b8722f' if changed else MUTED, anchor='mm')

    summary = plan['summary']
    lines = [f"Reuses {summary['reused_holes']} old holes across {summary['reused_works']} works",
             f"{summary['new_hole_points']} new fixing point{'s' if summary['new_hole_points'] != 1 else ''} "
             f"· movement max {summary['max_shift_used']:g} mm",
             f"tolerance ±{plan['tolerance_mm']:g} mm · move limit {plan['max_shift_mm']:g} mm",
             f"minimum gap {plan['minimum_gap_mm']:g} mm"]
    if not plan['checks']['clearance_ok']:
        lines.append('CHECK: a work still touches an obstacle or the wall edge')
    draw.multiline_text((18, 14), '\n'.join(lines), font=font(15), fill='#2b2722',
                        spacing=4, stroke_width=3, stroke_fill='#f4eee1')
    return add_rulers(scene, canvas, 52)
