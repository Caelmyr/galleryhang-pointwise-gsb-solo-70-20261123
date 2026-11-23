from dataclasses import dataclass
from math import acos, atan2, degrees, radians, tan

import numpy as np
from PIL import Image,ImageDraw

from .artworks import linear_color
from .clearance import obstacle_outline
from .layout import footprint, label_footprint, local_to_wall
from .photos import linear_to_srgb, uint8_image
from .projection import pull_rgba, wall_dimensions
from .render import Layer, render_items, wall_frame
from .labels import font


VIEW_SIZE = (1056,680)
VIEW_GRID = 32


def normalized(vector):
    return vector/np.linalg.norm(vector)


@dataclass(frozen=True)
class Camera:
    origin: np.ndarray
    forward: np.ndarray
    right: np.ndarray
    up: np.ndarray
    half_width: float
    half_height: float

    @staticmethod
    def from_station(station,size=VIEW_SIZE):
        origin = np.array([station['x'],station['height'],station['distance']])
        target = np.array([station['target_x'],station['target_y'],0])
        forward = normalized(target-origin)
        right = normalized(np.cross(forward,np.array([0,1,0])))
        up = normalized(np.cross(right,forward))
        half_width = tan(radians(station['fov']/2))
        return Camera(origin,forward,right,up,half_width,half_width*size[1]/size[0])

    def image_points(self, coordinates):
        offset = np.asarray(coordinates)-self.origin
        depth = offset@self.forward
        stable = np.abs(depth)>1e-8
        z = np.where(stable,depth,1.0)
        u = 0.5+0.5*((offset@self.right)/z)/self.half_width
        v = 0.5-0.5*((offset@self.up)/z)/self.half_height
        points = np.stack((u,v),axis=-1)
        return points, stable & (depth>0)

    def ray_coefficients(self):
        # Columns encode u, v, constant for a normalized image coordinate.
        horizontal = 2*self.half_width*self.right
        vertical = -2*self.half_height*self.up
        starting = self.forward-self.half_width*self.right+self.half_height*self.up
        return np.column_stack((horizontal,vertical,starting))

    def texture_coordinates(self, left, bottom, width, height, depth):
        coefficients = self.ray_coefficients()
        dz = depth-self.origin[2]
        denominator = coefficients[2]
        tx = ((self.origin[0]-left)*denominator + dz*coefficients[0])/width
        ty = ((bottom+height-self.origin[1])*denominator - dz*coefficients[1])/height
        return np.stack((tx,ty,denominator))

    def rays_towards_wall(self,size):
        coefficients = self.ray_coefficients()
        u = (np.arange(size[0])[None,:]+0.5)/size[0]
        v = (np.arange(size[1])[:,None]+0.5)/size[1]
        return u*coefficients[2,0]+v*coefficients[2,1]+coefficients[2,2] < -1e-8


def with_depth(points, depth=0):
    points = np.asarray(points)
    return np.concatenate((points, np.full((*points.shape[:-1],1),depth)),axis=-1)


def visible_ground(camera,size):
    x = np.linspace(0,1,size[0])[None,:]
    y = np.linspace(0,1,size[1])[:,None]
    layer = Layer.transparent(size)
    horizon = 0.50
    ground = y>horizon
    top = linear_color('#283036')
    bottom = linear_color('#696053')
    shade = 0.88+0.20*np.maximum(y-horizon,0) - 0.25*(x-0.5)**2
    layer.rgb[:] = np.where(ground[...,None],bottom,top)*shade[...,None]
    layer.alpha[:] = 1
    return layer


def available_obstacles(wall,station):
    return sorted([o for o in wall['obstacles'] if 0 <= o['depth'] < station['distance'] and o['kind']!='prohibited'],
                  key=lambda entry:entry['depth'])


def overlay_fixture(layer,camera,obstacle,rays):
    tx = camera.texture_coordinates(obstacle['left'],obstacle['bottom'],obstacle['width'],obstacle['height'],obstacle['depth'])
    shades = {'plinth':'#ad9f8a','fixture':'#a9856b','opening':'#4b4138','switch':'#f1e7df','door-swing':'#68615a'}
    fill = linear_color(shades.get(obstacle['kind'],'#98918b'))
    rub = np.linspace(0.82,1.12,12)[:,None,None]
    rgb = (np.broadcast_to(fill,(12,12,3))*rub).astype(np.float32)
    alpha = np.ones((12,12),dtype=np.float32)
    data = Layer(*pull_rgba(rgb,alpha,layer.size,tx))
    data.alpha *= rays
    return layer.over(data)


def rendered_view(blobs,scene,station,*,edge=1056,annotated=False):
    size = (edge, round(edge*VIEW_SIZE[1]/VIEW_SIZE[0]))
    camera = Camera.from_station(station,size)
    world_size = wall_dimensions(scene.wall,edge)
    world = wall_frame(scene.wall,world_size)
    items = render_items(blobs,scene,world_size)
    wall = world.over(items)
    base = visible_ground(camera,size)
    tx = camera.texture_coordinates(0,0,scene.wall['width'],scene.wall['height'],0)
    adjusted = Layer(*pull_rgba(wall.rgb,wall.alpha,size,tx))
    rays = camera.rays_towards_wall(size)
    adjusted.alpha *= rays
    view = base.over(adjusted)
    for object_on_wall in available_obstacles(scene.wall,station):
        view = overlay_fixture(view,camera,object_on_wall,rays)
    result = view.image().convert('RGB')
    if annotated:
        annotate_view(scene,station,camera,result)
    return result


def interpolate_grid(corners,count):
    sample = (np.arange(count)+0.5)/count
    u,v = np.meshgrid(sample,sample)
    corners = np.asarray(corners,dtype=float)
    return corners[0] + u[...,None]*(corners[1]-corners[0]) + v[...,None]*(corners[3]-corners[0])


def clear_sight(camera, wall_samples, objects):
    camera_distance = camera.origin[2]
    still_clear = np.ones(wall_samples.shape[:-1],dtype=bool)
    cause = {}
    for item in reversed(objects):
        ratio = (camera_distance-item['depth'])/camera_distance
        intercept = camera.origin[:2] + ratio*(wall_samples-camera.origin[:2])
        x,y = intercept[...,0],intercept[...,1]
        shadow = ((item['left']<=x)&(x<=item['left']+item['width'])
                  &(item['bottom']<=y)&(y<=item['bottom']+item['height']))
        count = np.count_nonzero(still_clear & shadow)
        if count:
            cause[item['id']] = {'name':item['name'],'count':int(count)}
        still_clear &= ~shadow
    return still_clear,cause


def view_amount(camera,corners,objects):
    grid = interpolate_grid(corners,VIEW_GRID)
    spots,front = camera.image_points(with_depth(grid))
    inside = front & np.all((spots>=0)&(spots<=1),axis=-1)
    clear,causes = clear_sight(camera,grid,objects)
    world_center = np.mean(corners,axis=0)
    distance = np.linalg.norm(with_depth(world_center)-camera.origin)
    incidence = degrees(acos(min(1,camera.origin[2]/distance))) if distance else 90
    outline,_ = camera.image_points(with_depth(corners))
    angular = angular_width(camera,corners)
    return {
        'in_view':round(float(inside.mean()),4),
        'clear_in_view':round(float((inside&clear).mean()),4),
        'obscured_in_view':round(float((inside&~clear).mean()),4),
        'obstacles':[{'name':v['name'],'fraction':round(v['count']/inside.size,4)} for v in causes.values()],
        'angle_off_wall_deg':round(incidence,2),
        'angular_width_deg':round(angular,2),
        'distance_mm':round(float(distance),1),
        'outline_uv':np.round(np.clip(outline,-20,20),5).tolist(),
    }


def angular_width(camera,corners):
    first = with_depth((corners[0]+corners[3])*0.5)-camera.origin
    second = with_depth((corners[1]+corners[2])*0.5)-camera.origin
    cosine = float(np.clip(normalized(first)@normalized(second),-1,1))
    return degrees(acos(cosine))


def viewpoint_report(scene,station):
    camera = Camera.from_station(station)
    objects = available_obstacles(scene.wall,station)
    works = []
    for placement,artwork in scene.works():
        stats = view_amount(camera,footprint(placement,artwork),objects)
        works.append({'placement':placement['id'],'name':artwork['title'],**stats})
    labels = []
    for label in scene.layout['labels']:
        stats = view_amount(camera,label_footprint(label),objects)
        letter_size = 2*degrees(atan2(label['type_size'],2*max(stats['distance_mm'],1)))*60
        labels.append({'label':label['id'],'name':label['name'] or 'Artwork label',
                       'body_angle_arcmin':round(letter_size,2),**stats})
    return {'station':station['name'], 'distance_mm':station['distance'], 'eye_height_mm':station['height'],
            'horizontal_fov':station['fov'],'works':works,'labels':labels,
            'works_fully_visible':sum(row['clear_in_view']>=0.995 for row in works),
            'works_partially_visible':sum(0<row['clear_in_view']<0.995 for row in works),
            'works_out_of_view':sum(row['clear_in_view']==0 for row in works)}


def annotate_view(scene,station,camera,image):
    draw = ImageDraw.Draw(image)
    boxes = viewpoint_report(scene,station)
    for item in boxes['works']:
        if not item['in_view']:
            continue
        projected = np.asarray(item['outline_uv']) * np.asarray(image.size)
        polygon = [tuple(point) for point in projected]
        color = '#d5ecb8' if item['clear_in_view']>=0.995 else '#f9bb81'
        draw.line([*polygon,polygon[0]],fill=color,width=2)
    y = image.height-34
    draw.rounded_rectangle((12,y-8,image.width-12,y+25),radius=6,fill='#252624')
    draw.text((22,y),f"{station['name']}  ·  {boxes['works_fully_visible']} fully visible  ·  {boxes['works_partially_visible']} partial",
              font=font(15),fill='#efece2')
