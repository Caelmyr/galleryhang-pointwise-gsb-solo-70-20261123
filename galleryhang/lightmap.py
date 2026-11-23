from dataclasses import dataclass
from math import log2

import numpy as np
from PIL import Image,ImageDraw
from scipy.ndimage import gaussian_filter, label as connected_components

from .artworks import linear_color
from .labels import font
from .model import InputError
from .photos import linear_to_srgb, luminance, uint8_image
from .projection import polygon_mask, register_view


@dataclass(frozen=True)
class Illumination:
    levels: np.ndarray
    support: np.ndarray
    detail: np.ndarray
    baseline: float
    scale: float


def smoothing_scale(wall,size,spread_mm):
    px_per_mm = min(size[0]/wall['width'],size[1]/wall['height'])
    return max(1.2,px_per_mm*spread_mm)


def normalized_blur(signal,valid,scale):
    weights = gaussian_filter(valid.astype(np.float32),sigma=scale,mode='nearest',truncate=3)
    total = gaussian_filter(np.where(valid,signal,0),sigma=scale,mode='nearest',truncate=3)
    result = np.zeros_like(total)
    available = weights>=0.22
    np.divide(total,weights,out=result,where=available)
    return result,available


def registered_illumination(blobs,wall,shot,spread=65,reference=None):
    image = register_view(blobs,wall,shot,1150)
    gray = luminance(image.rgb)
    valid = image.clear & (gray>0.006) & (gray<0.985)
    scale = smoothing_scale(wall,image.size,spread)
    field,supported = normalized_blur(gray,valid,scale)
    if np.count_nonzero(supported)<48:
        raise InputError('The photo has too few clear pixels for a wall lighting reading.')
    if reference:
        mask = polygon_mask(image.size,[reference['region']]) & supported & valid
        if np.count_nonzero(mask)<24:
            raise InputError('Choose a reference patch with at least 24 clear pixels.')
    else:
        mask = supported & valid
    base = float(np.median(field[mask]))
    if base<0.004:
        raise InputError('The reference area is too dark for a stable relative reading.')
    available = supported & image.clear
    levels = np.full_like(field,np.nan)
    levels[available] = np.log2(np.maximum(field[available],0.002)/base)
    surface_detail = np.clip(gray-field,-1,1)
    return Illumination(levels,available,surface_detail,base,scale)


def lighten(levels,available,goal):
    positive = np.where(available,np.maximum(levels,0),0)
    negative = np.where(available,np.maximum(-levels,0),0)
    neutral = linear_color('#e5dfca')
    light = linear_color('#d58c47')
    shade = linear_color('#526d92')
    bright_t = np.clip(positive/goal,0,1)[...,None]
    shadow_t = np.clip(negative/goal,0,1)[...,None]
    return neutral + bright_t*(light-neutral) + shadow_t*(shade-neutral)


def illumination_picture(result,heading,cutoff):
    rgb = lighten(result.levels,result.support,max(cutoff,0.12)*1.6)
    tint = np.clip(result.detail[...,None]*0.33,-0.13,0.13)
    rgb = np.clip(rgb+tint,0,1)
    y,x = np.indices(result.support.shape)
    blocked = 0.11+0.12*((x//10+y//10)%2)
    rgb = np.where(result.support[...,None],rgb,blocked[...,None])
    view = uint8_image(linear_to_srgb(rgb)).convert('RGB')
    draw = ImageDraw.Draw(view)
    draw.rounded_rectangle((10,10,min(510,view.width-10),63),6,fill='#2e3028')
    draw.text((22,20),heading,font=font(14,heading=True),fill='#f0e9d8')
    draw.text((22,41),'Cooler: lower relative light · warmer: higher relative light',font=font(11),fill='#e3d7bc')
    return view


def parts_between(values,mask,cutoff):
    warning = np.isfinite(values) & (np.abs(values)>=cutoff) & mask
    regions,count = connected_components(warning)
    sizes = np.bincount(regions.ravel())
    if len(sizes)<=1:
        return []
    active = np.argsort(sizes[1:])[-10:][::-1]+1
    height,width = values.shape
    candidates = []
    for group in active:
        if sizes[group]<30:
            continue
        hit = regions==group
        ys,xs = np.nonzero(hit)
        median = float(np.median(values[hit]))
        candidates.append({'pixels':int(len(ys)),'median_stops':round(median,3),
                           'direction':'brighter' if median>=0 else 'dimmer',
                           'wall_rect_uv':[round(xs.min()/width,5),round(ys.min()/height,5),
                                           round((xs.max()+1)/width,5),round((ys.max()+1)/height,5)]})
    return candidates


def wall_zones(result):
    height,width = result.support.shape
    y_parts = np.rint(np.linspace(0,height,5)).astype(int)
    x_parts = np.rint(np.linspace(0,width,7)).astype(int)
    zones = []
    for y in range(4):
        for x in range(6):
            block = result.levels[y_parts[y]:y_parts[y+1],x_parts[x]:x_parts[x+1]]
            values = block[np.isfinite(block)]
            zones.append({'row':y+1,'column':x+1, 'support':round(len(values)/max(1,block.size),4),
                          'median_stops':round(float(np.median(values)),3) if len(values) else None,
                          'variation_stops':round(float(np.quantile(values,0.90)-np.quantile(values,0.10)),3) if len(values)>6 else None})
    return zones


def lighting_metrics(result, cutoff):
    intensity = result.levels[result.support]
    lo,median,hi = np.quantile(intensity,[0.1,0.5,0.9])
    dark = np.count_nonzero(intensity<-cutoff)
    bright = np.count_nonzero(intensity>cutoff)
    return {'coverage':round(float(result.support.mean()),4),'low_stop':round(float(lo),3),
            'median_stops':round(float(median),3),'high_stop':round(float(hi),3),
            'contrast_per_80_percent':round(float(hi-lo),3),'lower_fraction':round(dark/len(intensity),4),
            'upper_fraction':round(bright/len(intensity),4),'reference_linear_luminance':round(result.baseline,6),
            'support_regions':parts_between(result.levels,result.support,cutoff),'zones':wall_zones(result)}


def line_probe(result,from_uv,to_uv,*,examples=64):
    a,b = np.asarray(from_uv),np.asarray(to_uv)
    uv = a + np.linspace(0,1,examples)[:,None]*(b-a)
    points = uv*np.array(result.support.shape[::-1])
    px = np.clip(np.rint(points[:,0]).astype(int),0,result.support.shape[1]-1)
    py = np.clip(np.rint(points[:,1]).astype(int),0,result.support.shape[0]-1)
    loaded = result.levels[py,px]
    valid = np.isfinite(loaded)
    return {'observed_count':int(valid.sum()),'levels_stops':[round(float(x),4) if present else None
                                                            for x,present in zip(loaded,valid)]}
