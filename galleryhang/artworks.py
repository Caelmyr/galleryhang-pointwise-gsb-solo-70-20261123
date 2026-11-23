from math import hypot

import numpy as np
from PIL import Image, ImageDraw

from .model import InputError, frame_border
from .photos import linear_to_srgb, open_working, srgb_to_linear, uint8_image
from .projection import UNIT_QUAD, homography, image_dimensions, pull_rgba


FINISH_COLORS = {
    'ink':((46,43,40),(25,24,23),(82,76,70)),
    'oak':((163,124,80),(112,80,46),(201,163,110)),
    'white':((231,227,219),(184,179,171),(255,254,249)),
    'silver':((170,173,175),(121,126,130),(215,218,221)),
    'walnut':((103,71,58),(65,43,40),(142,108,84)),
}


def rgb_value(text):
    return tuple(int(text[i:i+2],16) for i in (1,3,5))


def linear_color(text):
    return srgb_to_linear(np.array(rgb_value(text),dtype=np.float32)/255)


def artwork_geometry(artwork):
    full_width,full_height = artwork['width'],artwork['height']
    inset = frame_border(artwork)+artwork['mat']
    return {'outer':[full_width,full_height], 'frame':frame_border(artwork),'mat':artwork['mat'],
            'opening':[full_width - 2*inset,full_height - 2*inset]}


def preview_geometry(artwork,max_edge):
    shape = artwork_geometry(artwork)
    full_width,full_height = shape['outer']
    size = image_dimensions(full_width,full_height,max_edge)
    if max(size)<max_edge:
        factor = max_edge/max(size)
        size = tuple(max(2,round(v*factor)) for v in size)
    width,height = size
    scale_x,scale_y = width/full_width,height/full_height
    frame_x = min(round(shape['frame']*scale_x),(width-2)//2)
    frame_y = min(round(shape['frame']*scale_y),(height-2)//2)
    inset_x = min(round((shape['frame']+shape['mat'])*scale_x),(width-2)//2)
    inset_y = min(round((shape['frame']+shape['mat'])*scale_y),(height-2)//2)
    return size, (frame_x,frame_y), (inset_x,inset_y)


def working_photo(blobs, photo, *, detail=False):
    source = photo if detail else {**photo,'working_digest':photo['preview_digest']}
    return open_working(blobs,source)


def crop_dimensions(artwork, source_size):
    corners = np.asarray(artwork['crop']) * np.asarray(source_size)
    width = (np.linalg.norm(corners[1]-corners[0])+np.linalg.norm(corners[2]-corners[3]))/2
    height = (np.linalg.norm(corners[3]-corners[0])+np.linalg.norm(corners[2]-corners[1]))/2
    return max(1,round(float(width))),max(1,round(float(height)))


def cropped_preview(blobs, artwork, edge=1200, *, detail=False):
    rgb,alpha = working_photo(blobs,artwork['photo'],detail=detail)
    dimensions = crop_dimensions(artwork,alpha.shape[::-1])
    size = image_dimensions(*dimensions,edge)
    from .projection import rectify_art
    if detail:
        sampled,coverage = rectify_art(blobs,artwork,size)
    else:
        sampled,coverage = pull_rgba(srgb_to_linear(rgb),alpha,size,homography(UNIT_QUAD,artwork['crop']))
    return uint8_image(linear_to_srgb(sampled),coverage)


def mimicked_frame(canvas, finish, frame):
    if finish=='none' or not any(frame):
        return
    high,low,edge = FINISH_COLORS[finish]
    draw = ImageDraw.Draw(canvas)
    width,height = canvas.size
    fx,fy = frame
    draw.rectangle((0,0,width-1,height-1),fill=high)
    if min(fx,fy)>0:
        draw.polygon(((0,0),(width-1,0),(width-1-fx,fy),(fx,fy)),fill=edge)
        draw.polygon(((width-1,0),(width-1,height-1),(width-1-fx,height-1-fy),(width-1-fx,fy)),fill=low)
        draw.polygon(((width-1,height-1),(0,height-1),(fx,height-1-fy),(width-1-fx,height-1-fy)),fill=low)
    bevel = max(1,round(min(fx,fy)/4))
    if min(fx,fy)>bevel:
        draw.rectangle((fx-bevel,fy-bevel,width-1-fx+bevel,height-1-fy+bevel),outline=low,width=bevel)


def create_framed_image(blobs, artwork, max_edge=960, *, exposure=0):
    size,frame,inset = preview_geometry(artwork,max_edge)
    width,height = size
    canvas = Image.new('RGB',size,rgb_value(artwork['mat_color']))
    mimicked_frame(canvas,artwork['frame'],frame)
    fx,fy = frame
    if fx or fy:
        ImageDraw.Draw(canvas).rectangle((fx,fy,width-1-fx,height-1-fy),fill=rgb_value(artwork['mat_color']))
    x,y = inset
    inner = width-2*x,height-2*y
    rgb,alpha = working_photo(blobs,artwork['photo'])
    matrix = homography(UNIT_QUAD,artwork['crop'])
    sampled,coverage = pull_rgba(srgb_to_linear(rgb),alpha,inner,matrix)
    if exposure:
        sampled = np.minimum(sampled * (2.0 ** exposure), 1)
    opening = uint8_image(linear_to_srgb(sampled),coverage)
    canvas.paste(opening,(x,y),opening.getchannel('A'))
    return canvas


def trim_warning(artwork):
    photo = artwork['photo']
    width,height = crop_dimensions(artwork,(photo['width'],photo['height']))
    opening = artwork_geometry(artwork)['opening']
    source_aspect = width/height
    display_aspect = opening[0]/opening[1]
    scale = min(source_aspect,display_aspect)/max(source_aspect,display_aspect)
    corners = np.asarray(artwork['crop'])
    delta = np.abs(corners[:,0]-0.5)
    return {
        'cropped_size_pixels':[width,height],
        'opening_size_mm':[round(value,2) for value in opening],
        'source_aspect':round(source_aspect,4),
        'opening_aspect':round(display_aspect,4),
        'aspect_fit_percent':round(scale*100,1),
        'stretched':scale < 0.92,
        'source_width_mm_pixel_density':round(width/opening[0],3),
        'crop_uses_edges':bool(np.any(delta>0.49) or np.any(np.abs(corners[:,1]-0.5)>0.49)),
    }


def artwork_information(artwork):
    return {
        'id':artwork['id'],
        'title':artwork['title'],
        'artist':artwork['artist'],
        'geometry':artwork_geometry(artwork),
        'crop':trim_warning(artwork),
        'fixing':{'type':artwork['attachment'],'distance_from_top':artwork['fixing_top'],
                  'span_mm':round(artwork['width']*artwork['fixing_span'],2),'mass_kg':artwork['weight']},
        'source':{key:artwork['photo'][key] for key in ('name','width','height','source_digest','original_mime')},
    }


def available_crop_ratio(artwork):
    unit = artwork['crop']
    near_width = hypot(unit[1][0]-unit[0][0],unit[1][1]-unit[0][1])
    far_width = hypot(unit[2][0]-unit[3][0],unit[2][1]-unit[3][1])
    near_height = hypot(unit[3][0]-unit[0][0],unit[3][1]-unit[0][1])
    far_height = hypot(unit[2][0]-unit[1][0],unit[2][1]-unit[1][1])
    return min(near_width,far_width)*min(near_height,far_height)
