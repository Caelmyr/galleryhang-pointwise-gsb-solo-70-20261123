from math import hypot

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_erosion
from scipy.signal import fftconvolve

from .labels import font
from .model import (
    InputError, MAX_IDENTIFICATIONS, MAX_UPLOAD, MissingError, digest_token, find, integer,
    mapping, new_id, sequence, text, timestamp, token,
)
from .photos import luminance, split_rgba, srgb_to_linear, uint8_image
from .projection import polygon_mask, signed_area, validate_polygon


SEARCH_EDGE = 1024
RENDER_EDGE = 1150
SCALE_STEPS = 7
MIN_MASK_PIXELS = 120
MIN_FRAGMENT_PIXELS = 300
COVERAGE_GRID = 384
CLOSE_MARGIN = 0.06
WEAK_LOCAL_FIT = 0.45


def load_scaled(blobs, photo, edge):
    with Image.open(blobs.require(photo['preview_digest'])) as source:
        image = source.convert('RGB')
    if max(image.size) > edge:
        image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    return image


def working_image(blobs, photo):
    image = load_scaled(blobs, photo, SEARCH_EDGE)
    rgb, alpha = split_rgba(image)
    return srgb_to_linear(rgb) * alpha[..., None], alpha, image.size


def fragment_patch(rgb, alpha, region, size):
    support = polygon_mask(size, [region]) & (alpha > 0.5)
    support = binary_erosion(support, iterations=2)
    if np.count_nonzero(support) < MIN_FRAGMENT_PIXELS:
        raise InputError('Circle a larger clearly visible area of the artwork.')
    rows, columns = np.nonzero(support)
    top, bottom = rows.min(), rows.max() + 1
    left, right = columns.min(), columns.max() + 1
    return {
        'luma': luminance(rgb)[top:bottom, left:right].astype(np.float64),
        'rgb': rgb[top:bottom, left:right],
        'mask': support[top:bottom, left:right].astype(np.float64),
    }


def resize_float(array, size):
    return np.asarray(
        Image.fromarray(array.astype(np.float32), mode='F').resize(size, Image.Resampling.BILINEAR),
        dtype=np.float64)


def masked_ncc(image, template, mask):
    flipped = mask[::-1, ::-1]
    count = fftconvolve(np.ones_like(image), flipped, mode='valid')
    sum_image = fftconvolve(image, flipped, mode='valid')
    sum_squared = fftconvolve(image * image, flipped, mode='valid')
    weighted = template * mask
    sum_pair = fftconvolve(image, weighted[::-1, ::-1], mode='valid')
    mask_count = max(float(mask.sum()), 1.0)
    total = float(weighted.sum())
    total_squared = float((weighted * template).sum())
    support = np.maximum(count, 1.0)
    covariance = sum_pair - sum_image * total / support
    variance_image = sum_squared - sum_image * sum_image / support
    variance_template = total_squared - total * total / mask_count
    denominator = np.sqrt(np.maximum(variance_image, 0.0) * max(variance_template, 1e-12))
    ncc = np.full_like(denominator, -1.0)
    np.divide(covariance, np.maximum(denominator, 1e-12), out=ncc, where=denominator > 1e-9)
    ncc[count < 0.995 * mask_count] = -1.0
    return ncc


def probe_scale(image, fragment, scale):
    fh, fw = fragment['luma'].shape
    ih, iw = image.shape
    size = (max(8, round(fw * scale)), max(8, round(fh * scale)))
    if size[0] > iw or size[1] > ih:
        return None
    template = resize_float(fragment['luma'], size)
    mask = resize_float(fragment['mask'], size) > 0.5
    if mask.sum() < MIN_MASK_PIXELS:
        return None
    ncc = masked_ncc(image, template, mask.astype(np.float64))
    index = int(np.argmax(ncc))
    y, x = np.unravel_index(index, ncc.shape)
    return {'score': float(ncc.flat[index]), 'x': int(x), 'y': int(y),
            'width': size[0], 'height': size[1], 'scale': scale}


def search_scales(image, fragment):
    ih, iw = image.shape
    fh, fw = fragment['luma'].shape
    best = None
    for target in np.geomspace(max(30.0, iw * 0.045), iw * 0.97, SCALE_STEPS):
        candidate = probe_scale(image, fragment, target / fw)
        if candidate and (best is None or candidate['score'] > best['score']):
            best = candidate
    if best is None:
        return None
    for factor in (0.88, 1.12):
        candidate = probe_scale(image, fragment, best['scale'] * factor)
        if candidate and candidate['score'] > best['score']:
            best = candidate
    return best


def color_delta(image_rgb, fragment, pose):
    size = (pose['width'], pose['height'])
    window = image_rgb[pose['y']:pose['y'] + pose['height'], pose['x']:pose['x'] + pose['width']]
    mask = resize_float(fragment['mask'], size) > 0.5
    if not mask.any():
        return None
    delta = np.zeros(mask.shape, dtype=np.float64)
    for channel in range(3):
        template = resize_float(fragment['rgb'][..., channel], size)
        delta += np.abs(window[..., channel] - template)
    return float((delta / 3)[mask].mean())


def crop_coverage(quad, crop, grid=COVERAGE_GRID):
    matched = polygon_mask((grid, grid), [quad])
    displayed = polygon_mask((grid, grid), [crop])
    area = int(matched.sum())
    return float((matched & displayed).sum() / area) if area else 0.0


def match_candidate(blobs, fragment, artwork):
    rgb, _, size = working_image(blobs, artwork['photo'])
    pose = search_scales(luminance(rgb).astype(np.float64), fragment)
    if pose is None:
        raise InputError(
            f'The circled area does not fit inside the original image of {artwork["title"]}.')
    width, height = size
    quad = [[pose['x'] / width, pose['y'] / height],
            [(pose['x'] + pose['width']) / width, pose['y'] / height],
            [(pose['x'] + pose['width']) / width, (pose['y'] + pose['height']) / height],
            [pose['x'] / width, (pose['y'] + pose['height']) / height]]
    coverage = crop_coverage(quad, artwork['crop'])
    local = min(1.0, max(0.0, pose['score']))
    return {
        'artwork': artwork['id'],
        'title': artwork['title'],
        'local_fit': round(local, 4),
        'coverage': round(coverage, 4),
        'combined': round(local * coverage, 4),
        'color_delta': None if (delta := color_delta(rgb, fragment, pose)) is None else round(delta, 4),
        'quad': [[round(value, 5) for value in point] for point in quad],
        'matched_pixels': int(pose['width'] * pose['height']),
    }


def candidate_note(result, best_local):
    local, coverage = result['local_fit'], result['coverage']
    if coverage < 0.55:
        return (f'Texture agrees locally ({local:.2f}), but only {coverage * 100:.0f}% of the matched '
                'area lies inside this version’s current display crop; the rest would be trimmed away '
                'even though it is visible in the photograph.')
    if local < best_local - 0.12:
        return (f'Covers {coverage * 100:.0f}% of the matched area, but the local texture agreement '
                f'({local:.2f}) is weaker than the best candidate.')
    return f'Covers {coverage * 100:.0f}% of the matched area with local agreement {local:.2f}.'


def build_verdict(ordered, region_percent):
    top = ordered[0]
    if top['local_fit'] < WEAK_LOCAL_FIT:
        summary = (f'No convincing match: the best local agreement is {top["local_fit"]:.2f}. The '
                   'photograph may be too oblique, or the displayed version is not among the candidates.')
    elif top['combined'] - ordered[1]['combined'] < CLOSE_MARGIN:
        runner = ordered[1]
        summary = (f'Close call between “{top["title"]}” and “{runner["title"]}”: the circled area is '
                   'consistent with both croppings. Circle a larger area that crosses a crop edge to '
                   'separate them.')
    else:
        summary = (f'Most likely “{top["title"]}”: local agreement {top["local_fit"]:.2f} over the '
                   f'circled area, and {top["coverage"] * 100:.0f}% of the matched area lies inside its '
                   'current display crop.')
    method = (f'Only the circled area ({region_percent:.0f}% of the photograph) was compared, at '
              f'{SCALE_STEPS + 2} scales per candidate. Parts of the artwork outside the circle were '
              'not photographed clearly and are not counted as matched.')
    return summary, method


def capture_reference(raw):
    data = mapping(raw, 'Identification photograph')
    photo = {
        'id': token(data.get('id'), 'Photograph id'),
        'role': 'identification',
        'name': text(data.get('name') or 'identification photograph', 'Photograph name', maximum=140),
        'note': '',
        'format': text(data.get('format') or 'PNG', 'Photograph format', maximum=8),
        'original_mime': text(data.get('original_mime') or 'image/png', 'Photograph type', maximum=40),
        'original_size': [0, 0],
        'width': integer(data.get('width'), 'Photograph width', lower=24, upper=12000),
        'height': integer(data.get('height'), 'Photograph height', lower=24, upper=12000),
        'has_alpha': bool(data.get('has_alpha')),
        'working_color_space': text(data.get('working_color_space') or 'assumed sRGB',
                                    'Color space', maximum=40),
        'warning': '',
        'exif': {},
        'bytes': integer(data.get('bytes', 1), 'Photograph size', lower=1, upper=MAX_UPLOAD),
        'synthetic': False,
    }
    photo['original_size'] = [photo['width'], photo['height']]
    for key in ('source_digest', 'working_digest', 'preview_digest', 'thumbnail_digest'):
        photo[key] = digest_token(data.get(key), 'Photograph digest')
    return photo


def identification_photo(project, payload):
    source = payload.get('source_shot')
    if source is not None:
        reference = mapping(source, 'Source site photograph')
        wall = find(project['walls'], token(reference.get('wall'), 'Wall'), 'Wall')
        shot = find(wall['shots'], token(reference.get('shot'), 'Site photograph'), 'Site photograph')
        return shot['photo'], {'wall': wall['id'], 'shot': shot['id'], 'name': shot['name']}
    if payload.get('photo') is not None:
        return capture_reference(payload['photo']), None
    raise InputError('Choose a site photograph or upload one.')


def add_identification(blobs, project, payload):
    records = project.setdefault('identifications', [])
    if len(records) >= MAX_IDENTIFICATIONS:
        raise InputError(f'This exhibition already has {MAX_IDENTIFICATIONS} site identifications.')
    region = validate_polygon(payload.get('region'), 'Visible area outline')
    candidate_ids = [token(value, 'Candidate artwork') for value in
                     sequence(payload.get('candidates'), 'Candidate artworks', minimum=2, maximum=4)]
    if len(set(candidate_ids)) != len(candidate_ids):
        raise InputError('Choose each candidate artwork once.')
    artworks = [find(project['artworks'], candidate, 'Artwork') for candidate in candidate_ids]
    photo, provenance = identification_photo(project, payload)
    rgb, alpha, size = working_image(blobs, photo)
    fragment = fragment_patch(rgb, alpha, region, size)
    results = [match_candidate(blobs, fragment, artwork) for artwork in artworks]
    results.sort(key=lambda result: result['combined'], reverse=True)
    best_local = max(result['local_fit'] for result in results)
    for result in results:
        result['note'] = candidate_note(result, best_local)
    area = abs(signed_area(region))
    summary, method = build_verdict(results, area * 100)
    record = {
        'id': new_id(),
        'created': timestamp(),
        'name': text(payload.get('name') or 'Site identification', 'Identification name'),
        'note': text(payload.get('note', ''), 'Identification notes', maximum=600, required=False),
        'status': 'open',
        'choice': None,
        'confirmed': None,
        'photo': photo,
        'source_shot': provenance,
        'region': region,
        'region_area': round(area, 5),
        'candidates': candidate_ids,
        'results': results,
        'verdict': summary,
        'method': method,
    }
    records.append(record)
    return record, f"Compared the circled area with {len(results)} candidate artworks"


def confirm_identification(project, identification_id, payload):
    record = find(project.get('identifications', []), identification_id, 'Site identification')
    choice = token(payload.get('choice'), 'Chosen artwork')
    if choice not in record['candidates']:
        raise InputError('Choose one of the compared candidate artworks.')
    artwork = find(project['artworks'], choice, 'Artwork')
    record['choice'] = choice
    record['status'] = 'confirmed'
    record['confirmed'] = timestamp()
    return record, f"Confirmed {artwork['title']} as the displayed version"


def delete_identification(project, identification_id):
    records = project.get('identifications', [])
    record = find(records, identification_id, 'Site identification')
    records.remove(record)
    return identification_id, f"Removed identification {record['name']}"


def identification_detail(project, record):
    current = []
    for result in record['results']:
        artwork = next((item for item in project['artworks'] if item['id'] == result['artwork']), None)
        inside = crop_coverage(result['quad'], artwork['crop']) if artwork else None
        current.append({
            'artwork': result['artwork'],
            'title': artwork['title'] if artwork else result['title'],
            'available': artwork is not None,
            'chosen': record['choice'] == result['artwork'],
            'inside_current_crop': None if inside is None else round(inside, 4),
        })
    return {'record': record, 'current': current}


def dash_line(draw, start, end, fill, width, dash=9, gap=7):
    length = hypot(end[0] - start[0], end[1] - start[1])
    if length < 1e-6:
        return
    ux, uy = (end[0] - start[0]) / length, (end[1] - start[1]) / length
    position = 0.0
    while position < length:
        stop = min(position + dash, length)
        draw.line([(start[0] + ux * position, start[1] + uy * position),
                   (start[0] + ux * stop, start[1] + uy * stop)], fill=fill, width=width)
        position = stop + gap


def paint_loop(draw, points, tint, *, dashed=False, width=3):
    closed = [*points, points[0]]
    if dashed:
        for start, end in zip(closed, closed[1:]):
            dash_line(draw, start, end, '#29251b', width + 2)
            dash_line(draw, start, end, tint, width)
    else:
        draw.line(closed, fill='#29251b', width=width + 2, joint='curve')
        draw.line(closed, fill=tint, width=width, joint='curve')
    for x, y in points:
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=tint)


def caption_bar(draw, image, lines):
    width = min(image.width - 28, 660)
    height = 24 + 21 * len(lines)
    draw.rounded_rectangle((14, 14, 14 + width, 14 + height), radius=8, fill='#302c25')
    for index, line in enumerate(lines):
        draw.text((27, 26 + 21 * index), line, font=font(16 if index == 0 else 14, heading=index == 0),
                  fill='#f4efe5' if index == 0 else '#e9ddbe')


def scaled_points(points, size):
    return [(x * size[0], y * size[1]) for x, y in points]


def identification_photo_image(blobs, record):
    image = load_scaled(blobs, record['photo'], RENDER_EDGE)
    draw = ImageDraw.Draw(image)
    paint_loop(draw, scaled_points(record['region'], image.size), '#a4f0d3')
    caption_bar(draw, image, [record['name'],
                              f"Compared area · {record['region_area'] * 100:.0f}% of the photograph"])
    return image


def identification_match_image(blobs, project, record, artwork_id):
    artwork = find(project['artworks'], artwork_id, 'Artwork')
    result = next((item for item in record['results'] if item['artwork'] == artwork_id), None)
    if result is None:
        raise MissingError('This artwork was not compared in the identification.')
    image = load_scaled(blobs, artwork['photo'], RENDER_EDGE)
    matched = polygon_mask(image.size, [result['quad']])
    current = polygon_mask(image.size, [artwork['crop']])
    outside = matched & ~current
    outside_fraction = float(outside.sum() / max(1, matched.sum()))
    if outside.any():
        rgb = np.asarray(image, dtype=np.float32) / 255
        tint = np.array([0.83, 0.27, 0.16], dtype=np.float32)
        rgb[outside] = rgb[outside] * 0.42 + tint * 0.58
        image = uint8_image(rgb).convert('RGB')
    draw = ImageDraw.Draw(image)
    paint_loop(draw, scaled_points(artwork['crop'], image.size), '#eccb81', dashed=True)
    paint_loop(draw, scaled_points(result['quad'], image.size), '#a4f0d3')
    lines = [artwork['title'],
             f"Matched area · local fit {result['local_fit']:.2f} · "
             f"{(1 - outside_fraction) * 100:.0f}% inside the current crop"]
    if outside_fraction > 0.005:
        lines.append(f'{outside_fraction * 100:.0f}% of the matched area is outside the current crop (red)')
    caption_bar(draw, image, lines)
    return image
