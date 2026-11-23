from collections import defaultdict
from math import ceil

import numpy as np
from PIL import Image,ImageDraw

from .artworks import artwork_information, create_framed_image, cropped_preview
from .capture_review import pixels_to_hex
from .labels import font, wrap_text
from .model import InputError, choice, find, sequence
from .photos import luminance, split_rgba, srgb_to_linear


CATALOG_ORDER = ('title','artist','inventory','wall order')
PALETTE_BINS = 9


def normalize_range(rgb):
    return np.where(rgb<0.003,0,rgb)


def lab_values(srgb):
    # D65 reference. Work in a perceptual space for color sorting and grouping.
    linear = srgb_to_linear(srgb)
    xyz = linear @ np.array([[0.4124564,0.2126729,0.0193339],
                             [0.3575761,0.7151522,0.1191920],
                             [0.1804375,0.0721750,0.9503041]])
    xyz = xyz/np.array([0.95047,1.0,1.08883])
    transformed = np.where(xyz > 216/24389,np.cbrt(xyz),(24389/27*xyz+16)/116)
    return np.stack((116*transformed[...,1]-16,
                     500*(transformed[...,0]-transformed[...,1]),
                     200*(transformed[...,1]-transformed[...,2])),axis=-1)


def color_groups(rgb, count):
    values = np.asarray(rgb)
    lab = lab_values(values)
    if len(values)<16:
        return []
    keys = np.stack((np.clip(np.floor(lab[:,0]/(100/PALETTE_BINS)),0,PALETTE_BINS-1),
                     np.floor((lab[:,1]+110)/16),np.floor((lab[:,2]+110)/16)),axis=-1).astype(int)
    unique, inverse, sizes = np.unique(keys, axis=0,return_inverse=True,return_counts=True)
    representatives = []
    for index in np.argsort(sizes)[::-1]:
        subset = inverse==index
        center = np.median(lab[subset],axis=0)
        previous = next((p for p in representatives if np.linalg.norm(p['lab']-center)<11),None)
        if previous is not None:
            previous['pixels'] += int(sizes[index])
            continue
        if len(representatives)<count:
            picked = np.median(values[subset],axis=0)
            representatives.append({'lab':center,'tone':pixels_to_hex(srgb_to_linear(picked)),
                                    'pixels':int(sizes[index])})
    return [{'color':row['tone'],'percent':round(100*row['pixels']/len(values),2),
             'lab':np.round(row['lab'],2).tolist()} for row in representatives]


def palette_card(blobs,artwork,number=5):
    preview = cropped_preview(blobs,artwork,350)
    rgb,alpha = split_rgba(preview)
    take = alpha>=0.96
    if np.count_nonzero(take)<16:
        return {'artwork':artwork['id'],'title':artwork['title'],'swatches':[],'sample_pixels':int(take.sum())}
    selected = rgb[take]
    step = max(1,int(len(selected)/18_000))
    sample = selected[::step]
    swatches = color_groups(sample,number)
    luma = luminance(srgb_to_linear(selected))
    percentiles = np.quantile(luma,[0.05,0.50,0.95])
    return {'artwork':artwork['id'],'title':artwork['title'],'swatches':swatches,
            'sample_pixels':int(take.sum()),'luminance_p05_p50_p95':np.round(percentiles,4).tolist(),
            'transparent_fraction':round(float((~take).mean()),4)}


def reference_difference(first,second):
    if not first.get('swatches') or not second.get('swatches'):
        return None
    first_colors = np.array([p['lab'] for p in first['swatches']])
    second_colors = np.array([p['lab'] for p in second['swatches']])
    distances = np.linalg.norm(first_colors[:,None,:]-second_colors[None,:,:],axis=-1)
    weights1 = np.array([p['percent'] for p in first['swatches']])
    weights2 = np.array([p['percent'] for p in second['swatches']])
    symmetric = (np.average(distances.min(axis=1),weights=weights1) +
                 np.average(distances.min(axis=0),weights=weights2))*0.5
    return round(float(symmetric),2)


def catalog_members(project, selection, order, layout_id=None):
    if selection:
        ids = sequence(selection,'Selected artwork IDs',minimum=1,maximum=32)
        items = [find(project['artworks'],identifier,'Artwork') for identifier in dict.fromkeys(ids)]
    else:
        items = list(project['artworks'])
    order = choice(order,CATALOG_ORDER,'catalog order')
    if order=='wall order':
        layout = find(project['layouts'],layout_id,'Layout')
        visited = {}
        for row in sorted(layout['placements'],key=lambda p:(p['x'], -p['y'])):
            if row['visible']:
                visited.setdefault(row['artwork'],len(visited))
        return sorted(items,key=lambda a:(a['id'] not in visited,visited.get(a['id'],0),a['title'].casefold()))
    return sorted(items,key=lambda a:(not a[order],a[order].casefold(),a['title'].casefold(),a['id']))


def catalog_index(project, selection, order, layout_id=None):
    return [artwork_information(art) for art in catalog_members(project,selection,order,layout_id)]


def contact_card(blobs,artwork,size,*,with_padding=True):
    width,height = size
    picture = create_framed_image(blobs,artwork,390)
    inner = (width-32,height-93)
    picture.thumbnail(inner,Image.Resampling.LANCZOS)
    card = Image.new('RGB',size,'#ede6da')
    draw = ImageDraw.Draw(card)
    if with_padding:
        draw.rectangle((11,9,width-12,height-11),outline='#d5cab9',width=1)
    x = (width-picture.width)//2
    y = 15+(inner[1]-picture.height)//2
    card.paste(picture,(x,y))
    cursor = height-74
    title = wrap_text(artwork['title'],width-32,font(15,heading=True))
    for line in title[:2]:
        draw.text((16,cursor),line,font=font(15,heading=True),fill='#292922')
        cursor += 18
    artist = artwork['artist'] or 'Artist not specified'
    artist = wrap_text(artist,width-32,font(13))[0]
    draw.text((16,cursor),artist,font=font(13),fill='#525146')
    cursor += 17
    meta = f"{artwork['width']:g} × {artwork['height']:g} mm"
    if artwork['inventory']:
        meta += ' · '+artwork['inventory']
    draw.text((16,cursor),wrap_text(meta,width-32,font(12))[0],font=font(12),fill='#696459')
    return card


def contact_sheet(blobs,project,selection,order,layout_id=None):
    works = catalog_members(project,selection,order,layout_id)
    if not works:
        raise InputError('Register an artwork before creating a contact sheet.')
    if len(works)>24:
        raise InputError('Choose at most 24 artworks for a single contact sheet.')
    cols = min(len(works),4)
    box_w,box_h,gap = 284,300,15
    page_w = cols*box_w+(cols+1)*gap
    page_h = ceil(len(works)/cols)*(box_h+gap)+92
    page = Image.new('RGB',(page_w,page_h),'#f8f3e9')
    draw = ImageDraw.Draw(page)
    draw.text((gap,16),'Artwork contact sheet',font=font(21,heading=True),fill='#28271e')
    draw.text((gap,46),project['name']+f' · {len(works)} artworks',font=font(14),fill='#665e51')
    for index,art in enumerate(works):
        position = (gap+(index%cols)*(box_w+gap),70+(index//cols)*(box_h+gap))
        page.paste(contact_card(blobs,art,(box_w,box_h)),position)
    return page


def palette_sheet(blobs,project,selection):
    items = catalog_members(project,selection,'title')
    if not items or len(items)>16:
        raise InputError('Select between one and sixteen artworks for a palette comparison.')
    cards = [palette_card(blobs,art) for art in items]
    pairs = []
    for index,first in enumerate(cards):
        for other in cards[index+1:]:
            pairs.append({'first':first['title'],'second':other['title'],'difference':reference_difference(first,other)})
    pairs.sort(key=lambda row:(row['difference'] is None,row['difference'] or 0,row['first']))
    return {'artworks':cards,'pairs':pairs}
