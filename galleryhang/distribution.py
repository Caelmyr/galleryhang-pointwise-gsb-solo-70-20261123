from itertools import pairwise
from math import ceil

import numpy as np
from PIL import Image,ImageDraw

from .artworks import linear_color
from .clearance import intersection_rect, area
from .labels import font
from .layout import footprint, rect_bounds
from .photos import luminance
from .projection import polygon_mask, wall_dimensions, wall_to_pixels, wall_to_uv
from .render import elevation


GRID_TINT = (41,41,32,130)


def occupancy_grid(scene, edge=900):
    size = wall_dimensions(scene.wall,edge)
    visits = np.zeros(size[::-1],dtype=np.uint16)
    groups = []
    for placement,artwork in scene.works():
        outline = footprint(placement,artwork)
        mask = polygon_mask(size,[wall_to_uv(scene.wall,outline)])
        visits += mask
        groups.append({'id':placement['id'],'title':artwork['title'],'mask':mask,'polygon':outline})
    return visits,groups


def row_buckets(visits, bins):
    height = visits.shape[0]
    edges = np.rint(np.linspace(0,height,bins+1)).astype(int)
    output = []
    for index,(a,b) in enumerate(pairwise(edges)):
        band = visits[a:b]
        count = int((band>0).sum())
        excess = int((band>1).sum())
        output.append((index,count/max(1,band.size),excess/max(1,band.size)))
    return output


def row_profile(scene, visits, bins):
    wall_height = scene.wall['height']
    sampled = row_buckets(visits,bins)
    return [{'center_mm':round(wall_height*(1-(index+0.5)/bins),2),
             'coverage':round(covered,4),'overlap':round(overlap,4)} for index,covered,overlap in sampled]


def column_profile(scene, visits, bins):
    wall_width = scene.wall['width']
    sampled = row_buckets(visits.T,bins)
    return [{'center_mm':round(wall_width*(index+0.5)/bins,2),
             'coverage':round(covered,4),'overlap':round(overlap,4)} for index,covered,overlap in sampled]


def design_band(scene,low,high):
    total = 0.0
    contributions = []
    for placement,artwork in scene.works():
        outline = footprint(placement,artwork)
        covered = area(intersection_rect(outline,[0,low,scene.wall['width'],high]))
        share = covered / max(1,artwork['width']*artwork['height'])
        total += covered
        if covered>0:
            contributions.append({'name':artwork['title'],'portion_of_frame':round(share,4)})
    return {'bottom_mm':low,'top_mm':high,'additive_area_mm2':round(total,2),
            'band_area_mm2':round(scene.wall['width']*(high-low),2),'artworks':contributions}


def visual_center(scene,visits):
    unique = visits>0
    where = np.argwhere(unique)
    if not len(where):
        return None
    y,x = np.mean(where+0.5,axis=0)
    center = [x/max(1,visits.shape[1])*scene.wall['width'],(1-y/max(1,visits.shape[0]))*scene.wall['height']]
    dx = center[0]-scene.wall['width']/2
    dy = center[1]-scene.layout['eye_level']
    return {'center_mm':np.round(center,2).tolist(),'horizontal_offset_from_wall_center_mm':round(float(dx),2),
            'vertical_offset_from_eye_line_mm':round(float(dy),2)}


def overlap_exposure(groups, visits):
    target = []
    for group in groups:
        visible = group['mask']
        total = max(1,int(visible.sum()))
        target.append({'name':group['title'],'id':group['id'],'visible_pixel_area':int(visible.sum()),
                       'overlap_fraction':round(int((visible & (visits>1)).sum())/total,4)})
    return target


def distribution_report(scene):
    visits, groups = occupancy_grid(scene)
    width,height = scene.wall['width'],scene.wall['height']
    eye = scene.layout['eye_level']
    low,high = max(0,eye-160),min(height,eye+160)
    band = design_band(scene,low,high) if high>low else None
    edges = row_profile(scene,visits,8)
    stacks = column_profile(scene,visits,12)
    wall_area_each = width*height/max(1,visits.size)
    return {'artwork_coverage_fraction':round(float((visits>0).mean()),4),
            'overlap_fraction':round(float((visits>1).mean()),4),
            'unique_artwork_area_mm2':round(float((visits>0).sum()*wall_area_each),1),
            'eye_level_mm':eye,'eye_line_band':band,'visual_center':visual_center(scene,visits),
            'row_profile':edges,'column_profile':stacks,'works':overlap_exposure(groups,visits)}


def draw_axis_histogram(draw,profile, rectangle, *, columns, tint):
    left,top,right,bottom = rectangle
    width,height = right-left,bottom-top
    if columns:
        each = width/len(profile)
        for index,item in enumerate(profile):
            x = left+index*each
            base = bottom-height*item['coverage']
            draw.rectangle((x+2,base,x+each-2,bottom),fill=tint)
            overlap = bottom-height*item['overlap']
            draw.rectangle((x+2,overlap,x+each-2,bottom),fill='#a86c55')
    else:
        each = height/len(profile)
        for index,item in enumerate(profile):
            y = top+index*each
            bar = left+width*item['coverage']
            draw.rectangle((left,y+2,bar,y+each-2),fill=tint)
            overlap = left+width*item['overlap']
            draw.rectangle((left,y+2,overlap,y+each-2),fill='#a86c55')


def elevate_distribution(blobs,scene):
    image = elevation(blobs,scene,edge=1050)
    metrics = distribution_report(scene)
    alpha = Image.new('RGBA',image.size)
    overlay = ImageDraw.Draw(alpha)
    for x in np.linspace(0,image.width,13)[1:-1]:
        overlay.line((x,0,x,image.height),fill=GRID_TINT,width=1)
    for y in np.linspace(0,image.height,9)[1:-1]:
        overlay.line((0,y,image.width,y),fill=GRID_TINT,width=1)
    level = wall_to_pixels(scene.wall,image.size,[0,scene.layout['eye_level']])[1]
    overlay.line((0,level,image.width,level),fill='#e4ce8e',width=2)
    visual = metrics['visual_center']
    if visual:
        center = wall_to_pixels(scene.wall,image.size,visual['center_mm'])
        cx,cy = center
        overlay.ellipse((cx-9,cy-9,cx+9,cy+9),outline='#ebd3a1',width=3)
        overlay.line((cx-14,cy,cx+14,cy),fill='#211f1a',width=2)
        overlay.line((cx,cy-14,cx,cy+14),fill='#211f1a',width=2)
    visual_wall = Image.alpha_composite(image.convert('RGBA'),alpha).convert('RGB')
    margin,head,side,foot = 33,70,116,96
    canvas = Image.new('RGB',(image.width+2*margin+side,image.height+head+foot),'#f4eee2')
    canvas.paste(visual_wall,(margin,head))
    draw = ImageDraw.Draw(canvas)
    draw.text((margin,21),'Visual weight across '+scene.wall['name'],font=font(21,heading=True),fill='#34322c')
    draw_axis_histogram(draw,metrics['row_profile'],(image.width+margin+9,head,image.width+margin+side,head+image.height),
                        columns=False,tint='#788776')
    draw_axis_histogram(draw,metrics['column_profile'],(margin,head+image.height+8,margin+image.width,canvas.height-25),
                        columns=True,tint='#788776')
    return canvas
