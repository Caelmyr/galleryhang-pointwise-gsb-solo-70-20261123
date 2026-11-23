from dataclasses import dataclass

import numpy as np
from PIL import Image,ImageDraw
from scipy.ndimage import gaussian_filter, label as label_components

from .artworks import linear_color
from .labels import font
from .layout import footprint
from .model import InputError, number
from .photos import linear_to_srgb, luminance, uint8_image
from .projection import polygon_mask, register_view, wall_dimensions, wall_to_uv
from .render import Layer, render_items


@dataclass(frozen=True)
class Comparison:
    left: object
    right: object
    left_view: object
    right_view: object
    common: np.ndarray
    luma_delta: np.ndarray
    gain: float


def pair_views(blobs,wall,left,right,edge):
    if left['id']==right['id']:
        raise InputError('Choose two different site photographs.')
    a = register_view(blobs,wall,left,edge)
    b = register_view(blobs,wall,right,edge)
    shared = a.clear & b.clear
    if np.count_nonzero(shared)<80:
        raise InputError('These two registered views do not share enough clear wall pixels.')
    return a,b,shared


def sample_gain(a,b,shared,neutral_mask=None):
    first,second = luminance(a),luminance(b)
    safe = shared & (first>0.04) & (first<0.86) & (second>0.04) & (second<0.86)
    if neutral_mask is not None:
        safe &= neutral_mask
    if np.count_nonzero(safe)<48:
        return 1.0
    return float(np.clip(np.median(first[safe]/second[safe]),0.5,2.0))


def compare_captures(blobs,wall,left,right,*,edge=1150,normalize=True):
    a,b,common = pair_views(blobs,wall,left,right,edge)
    gain = sample_gain(a.rgb,b.rgb,common) if normalize else 1.0
    delta = luminance(b.rgb)*gain-luminance(a.rgb)
    return Comparison(left,right,a,b,common,delta,gain)


def difference_components(delta,common,threshold):
    strong = (np.abs(delta)>=threshold)&common
    value = gaussian_filter(strong.astype(float),sigma=1.0)>0.48
    groups,count = label_components(value & common)
    frequencies = np.bincount(groups.ravel())
    found = []
    indices = np.argsort(frequencies[1:])[-14:][::-1]+1
    total_height,total_width = strong.shape
    for idx in indices:
        if frequencies[idx]<12:
            continue
        rows,columns = np.nonzero(groups==idx)
        values = delta[rows,columns]
        found.append({'pixels':int(len(rows)),
                      'box_uv':[round(float(columns.min()/total_width),5),round(float(rows.min()/total_height),5),
                                round(float((columns.max()+1)/total_width),5),round(float((rows.max()+1)/total_height),5)],
                      'median_luma_shift':round(float(np.median(values)),4),
                      'direction':'lighter' if np.median(values)>0 else 'darker'})
    return strong,found


def comparison_metrics(result,threshold):
    threshold = number(threshold,'Difference threshold',lower=0.01,upper=0.8)
    strong,regions = difference_components(result.luma_delta,result.common,threshold)
    differences = np.abs(result.luma_delta[result.common])
    return {'first':result.left['name'],'second':result.right['name'],'gain':round(result.gain,4),
            'supported_fraction':round(float(result.common.mean()),4),
            'different_fraction_in_supported':round(float(np.count_nonzero(strong)/len(differences)),4),
            'absolute_median':round(float(np.median(differences)),4),'absolute_p90':round(float(np.quantile(differences,0.9)),4),
            'threshold':threshold,'regions':regions}


def checker(shape):
    rows,cols = np.indices(shape)
    return (0.27 + 0.08*((rows//14+cols//14)%2)).astype(np.float32)


def difference_picture(delta,common,threshold):
    strength = np.clip(np.abs(delta)/max(threshold*3,0.09),0,1)
    upward = linear_color('#e16a3c')
    downward = linear_color('#508fa9')
    neutral = linear_color('#e7ded1')
    RGB = np.where((delta>=0)[...,None],upward,downward)
    preview = neutral*(1-strength[...,None]) + RGB*strength[...,None]
    preview = np.where(common[...,None],preview,checker(common.shape)[...,None])
    return uint8_image(linear_to_srgb(preview))


def split_view(a,b,fraction):
    edge = round(a.width*fraction)
    overview = a.copy()
    if edge<a.width:
        overview.paste(b.crop((edge,0,b.width,b.height)),(edge,0))
    if 0<edge<a.width:
        ImageDraw.Draw(overview).line((edge,0,edge,overview.height),fill='#fff8e3',width=3)
    return overview


def strip_sheet(first,second,caption_a,caption_b):
    gap = 12
    head = 40
    size = (2*first.width+3*gap, first.height+head+gap)
    target = Image.new('RGB',size,'#f3eee5')
    target.paste(first,(gap,head),first if first.mode=='RGBA' else None)
    target.paste(second,(first.width+2*gap,head),second if second.mode=='RGBA' else None)
    draw = ImageDraw.Draw(target)
    draw.text((gap,14),caption_a,font=font(15),fill='#38342f')
    draw.text((first.width+2*gap,14),caption_b,font=font(15),fill='#38342f')
    return target


def comparison_image(result,mode,threshold,split):
    first = result.left_view.image()
    second = uint8_image(linear_to_srgb(result.right_view.rgb*result.gain),result.right_view.alpha)
    if mode=='difference':
        return difference_picture(result.luma_delta,result.common,threshold)
    if mode=='split':
        return split_view(first,second,split)
    return strip_sheet(first,second,result.left['name'],result.right['name'])


def actual_preview(blobs,scene,shot,edge):
    registered = register_view(blobs,scene.wall,shot,edge)
    items = render_items(blobs,scene,registered.size,shadow=False)
    return registered, items


def planned_vs_actual(blobs,scene,shot,planned_shot,*,edge=1120):
    observed,items = actual_preview(blobs,scene,shot,edge)
    planned = register_view(blobs,scene.wall,planned_shot,edge)
    comparable = observed.clear & planned.clear
    tint = sample_gain(planned.rgb,observed.rgb,comparable,items.alpha<0.01)
    target = Layer(planned.rgb,planned.alpha).over(items)
    before = luminance(target.rgb)
    after = luminance(observed.rgb)*tint
    delta = after-before
    results = []
    for placement,artwork in scene.works():
        area = polygon_mask(observed.size,[wall_to_uv(scene.wall,footprint(placement,artwork))])
        sampled = comparable & area
        data = np.abs(delta[sampled])
        results.append({'placement':placement['id'],'title':artwork['title'],
                        'observed_fraction':round(np.count_nonzero(sampled)/max(1,np.count_nonzero(area)),4),
                        'median_absolute_delta':round(float(np.median(data)),4) if len(data) else None})
    return {'shot':shot['name'],'reference':planned_shot['name'],'gain':round(tint,4),'items':results},target,observed,delta,comparable
