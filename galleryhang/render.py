from dataclasses import dataclass
from math import ceil, floor

import numpy as np
from PIL import Image,ImageDraw
from scipy import ndimage

from .artworks import create_framed_image, linear_color
from .clearance import clearance_report, obstacle_outline
from .labels import font, label_image
from .layout import footprint, label_footprint
from .mounts import mounting_report
from .photos import linear_to_srgb, split_rgba, srgb_to_linear, uint8_image
from .projection import (
    composite, image_dimensions, polygon_mask, pull_rgba, register_view, shot_matrix,
    wall_dimensions, wall_to_pixels, wall_to_uv,
)


@dataclass
class Layer:
    rgb: np.ndarray
    alpha: np.ndarray

    @classmethod
    def transparent(cls,size):
        width,height = size
        return cls(np.zeros((height,width,3),dtype=np.float32),np.zeros((height,width),dtype=np.float32))

    @classmethod
    def image_layer(cls,image):
        rgb,alpha = split_rgba(image)
        return cls(srgb_to_linear(rgb),alpha)

    @property
    def size(self):
        return self.alpha.shape[::-1]

    def over(self, other):
        rgb,alpha = composite(self.rgb,self.alpha,other.rgb,other.alpha)
        return Layer(rgb,alpha)

    def paint(self, other, rows, columns):
        rgb,opacity = composite(self.rgb[rows,columns],self.alpha[rows,columns],other.rgb,other.alpha)
        self.rgb[rows,columns] = rgb
        self.alpha[rows,columns] = opacity

    def image(self):
        return uint8_image(linear_to_srgb(self.rgb),self.alpha)


def wall_frame(wall, size):
    width,height = size
    layer = Layer.transparent(size)
    base = linear_color(wall['paint'])
    yy,xx = np.ogrid[-1:1:complex(0,height),-1:1:complex(0,width)]
    bounce = 1.02 - 0.045*yy - 0.025*xx*xx
    layer.rgb[:] = np.clip(bounce[...,None] * base,0,1)
    layer.alpha[:] = 1
    if wall['baseboard'] > 0:
        top = round(height * (1 - wall['baseboard']/wall['height']))
        layer.rgb[top:] *= 0.88
        if 0 <= top < height:
            layer.rgb[top] *= 0.80
    return layer


def layout_geometry(wall,size,corners,*,padding=0):
    uv = wall_to_uv(wall,corners)
    ab = uv[1]-uv[0]
    ad = uv[3]-uv[0]
    transform = np.array([[ab[0],ad[0],uv[0,0]],[ab[1],ad[1],uv[0,1]],[0,0,1.]])
    pixel_corners = uv*np.asarray(size)
    minimum = np.floor(np.min(pixel_corners,axis=0)-padding).astype(int)
    maximum = np.ceil(np.max(pixel_corners,axis=0)+padding).astype(int)
    left,top = np.maximum(0,minimum)
    right,bottom = np.minimum(size,maximum)
    if right <= left or bottom <= top:
        return None
    return (slice(top,bottom),slice(left,right),(right-left,bottom-top),np.linalg.inv(transform),
            (left/size[0],top/size[1],right/size[0],bottom/size[1]))


def project_layer(layer,geometry):
    _,_,size,transform,rect = geometry
    return Layer(*pull_rgba(layer.rgb,layer.alpha,size,transform,rect=rect))


def render_items(blobs, scene, size, *, labels=True, shadow=True):
    work_layer = Layer.transparent(size)
    shadow_layer = Layer.transparent(size)
    for placement,art in scene.works():
        if placement['opacity'] <= 0:
            continue
        corners = footprint(placement,art)
        geometry = layout_geometry(scene.wall,size,corners)
        if geometry is None:
            continue
        px = max(size[0]*art['width']/scene.wall['width'],size[1]*art['height']/scene.wall['height'])
        source = create_framed_image(blobs,art,max(96,min(1050,ceil(px*1.25))),exposure=scene.layout['exposure'])
        patch = project_layer(Layer.image_layer(source),geometry)
        patch.alpha *= placement['opacity']
        rows,columns,*_ = geometry
        work_layer.paint(patch,rows,columns)
        if shadow and art['depth']>0 and scene.layout['shadow']>0:
            draw_shadow(shadow_layer,scene,art,corners,placement['opacity'])
    if labels:
        for label in scene.layout['labels']:
            geometry = layout_geometry(scene.wall,size,label_footprint(label))
            if not geometry:
                continue
            source_size = max(10,round(label['width']*2)),max(8,round(label['height']*2))
            source,_ = label_image(scene,label,source_size)
            patch = project_layer(Layer.image_layer(source),geometry)
            work_layer.paint(patch,geometry[0],geometry[1])
    return shadow_layer.over(work_layer)


def draw_shadow(layer,scene,art,corners,opacity):
    wall = scene.wall
    px_per_mm = (layer.size[0]/wall['width']+layer.size[1]/wall['height'])/2
    sigma = min(26, max(0.6, art['depth']*px_per_mm*0.22))
    shifted = corners + np.array([art['depth']*0.17, -art['depth']*0.25])
    geometry = layout_geometry(wall,layer.size,shifted,padding=ceil(sigma*3))
    if not geometry:
        return
    rows,columns,size,_,_ = geometry
    points = wall_to_pixels(wall,layer.size,shifted)-np.array([columns.start,rows.start])
    mask = polygon_mask(size,[points/np.asarray(size)])
    alpha = ndimage.gaussian_filter(mask.astype(np.float32),sigma=sigma,mode='constant')
    alpha *= scene.layout['shadow']*opacity*0.76
    target = Layer(np.full((*alpha.shape,3),0.016,dtype=np.float32),alpha)
    layer.paint(target,rows,columns)


def draw_obstacles(scene, canvas):
    draw = ImageDraw.Draw(canvas)
    colors = {'opening':'#ddd5c5','switch':'#faf8f2','fixture':'#bca995','plinth':'#b2aaa0',
              'door-swing':'#e0d6c2','prohibited':'#ddccb7'}
    for obstacle in scene.wall['obstacles']:
        points = wall_to_pixels(scene.wall,canvas.size,obstacle_outline(obstacle))
        coords = [tuple(p) for p in points]
        draw.polygon(coords,fill=colors[obstacle['kind']],outline='#827b72',width=1)
        if len(points) and abs(points[1,0]-points[0,0])>110 and abs(points[3,1]-points[0,1])>39:
            draw.text(tuple(points[0]+[6,6]),obstacle['name'],fill='#4d453c',font=font(13))
    return canvas


def elevation(blobs, scene, *, shot=None, annotated=None, edge=1450, labels=True):
    size = wall_dimensions(scene.wall,edge)
    plane = wall_frame(scene.wall,size)
    if shot is None:
        plane = Layer.image_layer(draw_obstacles(scene,plane.image().convert('RGB')))
        combined = plane.over(render_items(blobs,scene,size,labels=labels))
    else:
        observed = register_view(blobs,scene.wall,shot,edge)
        source = Layer(observed.rgb,observed.alpha)
        plane = plane.over(source)
        artwork = render_items(blobs,scene,size,labels=labels)
        artwork.alpha[observed.foreground] = 0
        combined = plane.over(artwork)
    result = combined.image().convert('RGB')
    if annotated:
        return annotate(scene,result,annotated)
    return result


def project_art_onto_shot(blobs,scene,shot,*,edge=1500,labels=True):
    photo = shot['photo']
    with Image.open(blobs.require(photo['preview_digest'])) as source:
        size = image_dimensions(*source.size,edge)
        base = Layer.image_layer(source.resize(size,Image.Resampling.LANCZOS))
    wall_size = wall_dimensions(scene.wall, min(1800, max(size)))
    film = render_items(blobs,scene,wall_size,labels=labels)
    original_transform = np.linalg.inv(shot_matrix(shot))
    shifted = Layer(*pull_rgba(film.rgb,film.alpha,size,original_transform))
    shifted.alpha[polygon_mask(size,shot['foreground'])] = 0
    if photo['has_alpha']:
        shifted.alpha *= base.alpha
    return base.over(shifted).image()


def ruler_step(limit, pixels):
    required = limit * 55/max(pixels,1)
    return next((step for step in (25,50,100,200,250,500,1000,2000,5000) if step>=required),10000)


def add_rulers(scene,canvas,margin):
    width,height = canvas.size
    wall_width,wall_height = scene.wall['width'],scene.wall['height']
    item = Image.new('RGB',(width+margin*2,height+margin*2),'#f5f0e7')
    item.paste(canvas,(margin,margin))
    draw = ImageDraw.Draw(item)
    face = font(max(11,margin//4))
    tone = '#5e554b'
    draw.rectangle((margin-1,margin-1,margin+width,margin+height),outline='#827b72',width=1)
    for col in range(0,floor(wall_width)+1,ruler_step(wall_width,width)):
        x = margin+col/wall_width*width
        draw.line((x,margin+height,x,margin+height+6),fill=tone)
        draw.text((x,margin+height+10),str(col),anchor='mt',fill=tone,font=face)
    for row in range(0,floor(wall_height)+1,ruler_step(wall_height,height)):
        y = margin+(1-row/wall_height)*height
        draw.line((margin-6,y,margin,y),fill=tone)
        draw.text((margin-9,y),str(row),anchor='rm',fill=tone,font=face)
    draw.text((margin,margin//2),scene.wall['name']+' / measured elevation · mm',font=face,fill=tone,anchor='lm')
    return item


def plot_cross(draw, position, size, color, width=2):
    x,y = position
    draw.line((x-size,y,x+size,y),fill=color,width=width)
    draw.line((x,y-size,x,y+size),fill=color,width=width)
    draw.ellipse((x-size*0.65,y-size*0.65,x+size*0.65,y+size*0.65),outline=color,width=width)


def annotate_installation(scene,canvas):
    canvas = canvas.copy()
    draw = ImageDraw.Draw(canvas)
    report = mounting_report(scene)
    face = font(max(10,canvas.width//100),heading=True)
    for item in report['works']:
        center = wall_to_pixels(scene.wall,canvas.size,item['artwork_center'])
        x,y = center
        box = 13
        draw.rounded_rectangle((x-box,y-box,x+box,y+box),radius=4,fill='#211f1c')
        draw.text((x,y),str(item['index']),font=face,fill='#fff8eb',anchor='mm')
        for point in item['points']:
            coordinates = wall_to_pixels(scene.wall,canvas.size,[point['wall_x'],point['wall_y']])
            tone = '#d74630' if not point['inside'] or point['restricted'] else '#ffe067'
            plot_cross(draw,coordinates,7,'#29231e',width=4)
            plot_cross(draw,coordinates,7,tone,width=2)
            adopted = point.get('reuse')
            if adopted:
                color = '#9fe0c1' if adopted['confirmed'] else '#f0b48c'
                draw.text((coordinates[0]+10,coordinates[1]-8),
                          f"#{adopted['number']}"+('' if adopted['confirmed'] else ' ?'),
                          font=font(12,heading=True),fill=color,stroke_width=2,stroke_fill='#211f1c')
    for mark in scene.wall['mount_marks']:
        x,y = wall_to_pixels(scene.wall,canvas.size,[mark['x'],mark['y']])
        if mark['usable']:
            draw.ellipse((x-3,y-3,x+3,y+3),fill='#488d70',outline='#f0ebe1')
    datum = wall_to_pixels(scene.wall,canvas.size,scene.wall['datum'])
    plot_cross(draw,datum,10,'#3d70a2',width=3)
    return add_rulers(scene,canvas,52)


def annotate_clearance(scene,canvas):
    result = canvas.convert('RGBA')
    overlay = Image.new('RGBA',canvas.size)
    draw = ImageDraw.Draw(overlay)
    checks = clearance_report(scene)
    for item in checks['items']:
        if not item['outside'] and not item['obstructions']:
            continue
        polygon = [tuple(p) for p in wall_to_pixels(scene.wall,canvas.size,item['corners'])]
        draw.polygon(polygon,fill=(187,67,35,69),outline=(159,49,26,240),width=3)
    for obstacle in scene.wall['obstacles']:
        polygon = wall_to_pixels(scene.wall,canvas.size,obstacle_outline(obstacle,margin=obstacle['clearance']))
        draw.line([*(tuple(p) for p in polygon),tuple(polygon[0])],fill=(190,120,31,230),width=2)
    for pair in checks['pairs']:
        if pair['at']:
            a,b = wall_to_pixels(scene.wall,canvas.size,pair['at'])
            draw.line((tuple(a),tuple(b)),fill='#ff4d1f',width=3)
            x,y = (a+b)*0.5
            draw.text((x,y-4),str(pair['distance']),font=font(14,heading=True),fill='#77311a',
                      anchor='mb',stroke_width=2,stroke_fill='#fff0d6')
    return add_rulers(scene,Image.alpha_composite(result,overlay).convert('RGB'),52)


def annotate(scene, canvas, mode):
    if mode=='mounts':
        return annotate_installation(scene,canvas)
    if mode=='clearances':
        return annotate_clearance(scene,canvas)
    return add_rulers(scene,canvas,52)
