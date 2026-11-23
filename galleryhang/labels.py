from functools import lru_cache
from math import atan, degrees

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .artworks import linear_color, rgb_value
from .layout import label_footprint
from .photos import luminance
from .projection import polygon_mask, register_view, wall_to_uv


@lru_cache(maxsize=100)
def font(size, *, heading=False):
    size = max(8,int(size))
    filename = 'DejaVuSans-Bold.ttf' if heading else 'DejaVuSans.ttf'
    try:
        return ImageFont.truetype(filename, size)
    except OSError:
        return ImageFont.load_default(size=size)


def measure(font_face, text):
    return float(font_face.getlength(text))


def split_word(word, line_width, face):
    piece = ''
    for char in word:
        if piece and measure(face,piece+char)>line_width:
            yield piece
            piece = char
        else:
            piece += char
    if piece:
        yield piece


def wrap_text(value,line_width,face):
    lines = []
    for para in value.split('\n'):
        if not para.strip():
            lines.append('')
            continue
        line = ''
        for word in para.split():
            chunks = list(split_word(word,line_width,face))
            for part_index,piece in enumerate(chunks):
                combined = (line+' '+piece).strip()
                if line and (measure(face,combined)>line_width or part_index>0):
                    lines.append(line)
                    line = piece
                else:
                    line = combined
        if line:
            lines.append(line)
    return lines


def label_copy(scene,label):
    art = None
    if label['placement']:
        art_id = next((p['artwork'] for p in scene.layout['placements'] if p['id']==label['placement']), None)
        art = scene.artworks.get(art_id)
    heading = label['name'] or (art['title'] if art else '')
    details = []
    if art:
        if art['artist']:
            details.append(art['artist'])
        further = ' · '.join(filter(None,(art['year'],art['medium'])))
        if further:
            details.append(further)
    body = label['body'] or (art['description'] if art else '')
    return heading,details,body


def label_lines(scene, label, width_pixels, height_pixels):
    scale = min(width_pixels/label['width'],height_pixels/label['height'])
    body_size = max(8,round(label['type_size']*scale*1.32))
    heading_size = max(9,round(body_size*1.33))
    padding = max(4,round(5.0*scale))
    span = max(5,width_pixels-2*padding)
    heading,details,body = label_copy(scene,label)
    lines = []
    for copy,style,size in ((heading,'heading',heading_size),('\n'.join(details),'credit',body_size),
                            (body,'body',body_size)):
        if not copy:
            continue
        face = font(size,heading=style=='heading')
        for phrase in wrap_text(copy,span,face):
            lines.append({'text':phrase,'size':size,'style':style,'face':face,
                          'advance':round(size * (1.33 if style=='heading' else 1.24))})
        lines[-1]['advance'] += round(size*0.30)
    total = sum(line['advance'] for line in lines)
    available = max(0,height_pixels-2*padding)
    return lines,padding,total,available


def label_image(scene,label,size):
    width,height = size
    lines,padding,total,available = label_lines(scene,label,width,height)
    ink = rgb_value(label['ink'])
    background = (*rgb_value(label['paper']),255) if label['style']=='paper' else (0,0,0,0)
    canvas = Image.new('RGBA',size,background)
    draw = ImageDraw.Draw(canvas)
    y = padding
    written = 0
    for line in lines:
        if y + line['size'] > height-padding:
            break
        left = padding
        if label['align']=='center':
            left = max(left,(width-measure(line['face'],line['text']))/2)
        draw.text((left,y),line['text'],font=line['face'],fill=(*ink,255),anchor='lt')
        written += 1
        y += line['advance']
    return canvas,{'line_count':len(lines),'drawn_lines':written,'overflow':total>available,'render_scale':width/label['width']}


def contrast(luma_a,luma_b):
    brighter = np.maximum(luma_a,luma_b)+0.05
    dimmer = np.minimum(luma_a,luma_b)+0.05
    return brighter/dimmer


def sample_label_region(scene,label,registered):
    shape = label_footprint(label)
    region = polygon_mask(registered.size,[wall_to_uv(scene.wall,shape)])
    supported = region & registered.clear
    ratio = int(supported.sum())/max(1,int(region.sum()))
    return registered.rgb[supported],ratio


def readable_at(label, station):
    dx = label['x']-station['x']
    dy = label['y']-station['height']
    distance = float(np.linalg.norm([dx,dy,station['distance']]))
    angle = 2*degrees(atan(label['type_size'] / (2*distance)))
    return {'station':station['name'],'distance_mm':round(distance,1),
            'body_angle_arcmin':round(angle*60,2),'small_at_station':bool(angle*60<6)}


def labels_report(blobs,scene,shot):
    use_photo = shot is not None and any(label['style']=='vinyl' for label in scene.layout['labels'])
    registered = register_view(blobs,scene.wall,shot,850) if use_photo else None
    rows = []
    for label in scene.layout['labels']:
        ink = float(luminance(linear_color(label['ink'])))
        _,typing = label_image(scene,label,(max(1,round(label['width']*3)),max(1,round(label['height']*3))))
        backing = 'paper' if label['style']=='paper' else 'wall swatch'
        sample_ratio = 1.0
        if label['style']=='vinyl' and registered is not None:
            patches,sample_ratio = sample_label_region(scene,label,registered)
            shade = luminance(patches) if len(patches) else np.array([],dtype=float)
            backing = 'wall photograph'
        else:
            key = label['paper'] if label['style']=='paper' else scene.wall['paint']
            shade = np.atleast_1d(luminance(linear_color(key)))
        ratios = np.quantile(contrast(ink,shade),[0.1,0.5,0.9]) if len(shade) else None
        title,_,_ = label_copy(scene,label)
        rows.append({'id':label['id'],'title':title,'style':label['style'],'backing':backing,
                     'supported_fraction':round(sample_ratio,3), 'lines':typing,
                     'contrast_low':round(float(ratios[0]),2) if ratios is not None else None,
                     'contrast_median':round(float(ratios[1]),2) if ratios is not None else None,
                     'contrast_high':round(float(ratios[2]),2) if ratios is not None else None,
                     'needs_attention':bool(ratios is None or ratios[0]<4.5 or typing['overflow'] or sample_ratio<0.9),
                     'stations':[readable_at(label,station) for station in scene.layout['stations']]})
    return {'results':rows,'count':len(rows),'needs_attention':sum(row['needs_attention'] for row in rows),
            'photo':None if shot is None else shot['name']}
