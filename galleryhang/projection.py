from dataclasses import dataclass
from math import hypot

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from .model import InputError, points
from .photos import linear_to_srgb, open_working, srgb_to_linear, uint8_image


UNIT_QUAD = np.array([[0.,0.], [1.,0.], [1.,1.], [0.,1.]])
WARP_ROW_BLOCK = 96


@dataclass(frozen=True)
class RegisteredView:
    rgb: np.ndarray
    alpha: np.ndarray
    transform: np.ndarray
    foreground: np.ndarray

    @property
    def size(self):
        return self.alpha.shape[::-1]

    @property
    def valid(self):
        return self.alpha > 0.99

    @property
    def clear(self):
        return self.valid & ~self.foreground

    def image(self):
        return uint8_image(linear_to_srgb(self.rgb), self.alpha)


def cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def signed_area(vertices):
    return 0.5 * sum(x1*y2-y1*x2 for (x1,y1),(x2,y2) in zip(vertices, [*vertices[1:], vertices[0]]))


def on_segment(a, b, p, *, tol=1e-8):
    return (abs(cross(a,b,p)) <= tol
            and min(a[0],b[0])-tol <= p[0] <= max(a[0],b[0])+tol
            and min(a[1],b[1])-tol <= p[1] <= max(a[1],b[1])+tol)


def edges_intersect(a, b, c, d):
    first = cross(a,b,c)
    second = cross(a,b,d)
    third = cross(c,d,a)
    fourth = cross(c,d,b)
    if first * second < 0 and third * fourth < 0:
        return True
    return on_segment(a,b,c) or on_segment(a,b,d) or on_segment(c,d,a) or on_segment(c,d,b)


def validate_polygon(value, label):
    vertices = points(value, label)
    if abs(signed_area(vertices)) < 1e-5:
        raise InputError(f'{label} encloses too little area.')
    total = len(vertices)
    for index, start in enumerate(vertices):
        end = vertices[(index+1) % total]
        if hypot(end[0]-start[0], end[1]-start[1]) < 1e-5:
            raise InputError(f'{label} has repeated points.')
        for other in range(index + 2, total):
            if index == 0 and other == total - 1:
                continue
            if edges_intersect(start, end, vertices[other], vertices[(other+1)%total]):
                raise InputError(f'{label} crosses itself.')
    return vertices


def calibrated_quad(value, label):
    corners = points(value, label, minimum=4, maximum=4)
    if any(cross(corners[i], corners[(i+1)%4], corners[(i+2)%4]) < 0.0003 for i in range(4)):
        raise InputError(f'Mark {label.lower()} in clockwise order, starting at the upper-left corner.')
    if signed_area(corners) < 0.003:
        raise InputError(f'{label} covers too little of the image to calibrate.')
    homography(UNIT_QUAD, corners)
    return corners


def homography(target, source):
    x = np.asarray(target, dtype=np.float64)
    y = np.asarray(source, dtype=np.float64)
    equations = np.zeros((8, 8), dtype=np.float64)
    for index, ((tx,ty),(sx,sy)) in enumerate(zip(x,y)):
        equations[2*index] = [tx, ty, 1, 0, 0, 0, -sx*tx, -sx*ty]
        equations[2*index+1] = [0, 0, 0, tx, ty, 1, -sy*tx, -sy*ty]
    try:
        matrix = np.append(np.linalg.solve(equations, y.reshape(-1)), 1).reshape(3, 3)
    except np.linalg.LinAlgError as error:
        raise InputError('The corners cannot define a usable image plane.') from error
    if (not np.isfinite(matrix).all() or np.linalg.cond(matrix)>2_000
            or np.min(np.c_[x, np.ones(4)] @ matrix[2]) <= 0.012):
        raise InputError('The perspective is too extreme. Mark a wider image of the surface.')
    return matrix


def map_points(transform, points):
    values = np.asarray(points, dtype=np.float64)
    homogeneous = np.concatenate((values, np.ones((*values.shape[:-1],1))), axis=-1)
    output = homogeneous @ np.asarray(transform).T
    with np.errstate(divide='ignore', invalid='ignore'):
        return output[..., :2] / output[..., 2:3]


def wall_dimensions(wall, max_edge):
    ratio = max(wall['width'],wall['height'])
    return (max(12, round(wall['width'] / ratio * max_edge)),
            max(12, round(wall['height'] / ratio * max_edge)))


def image_dimensions(width, height, max_edge):
    scale = min(1.0, max_edge / max(width, height))
    return max(1, round(width*scale)), max(1, round(height*scale))


def wall_to_uv(wall, coordinates):
    xy = np.asarray(coordinates, dtype=np.float64)
    return np.stack((xy[...,0] / wall['width'], 1 - xy[...,1] / wall['height']), axis=-1)


def uv_to_wall(wall, coordinates):
    uv = np.asarray(coordinates, dtype=np.float64)
    return np.stack((uv[...,0] * wall['width'], (1 - uv[...,1]) * wall['height']), axis=-1)


def wall_to_pixels(wall, size, coordinates):
    return wall_to_uv(wall, coordinates) * np.asarray(size)


def shot_matrix(shot):
    return homography(UNIT_QUAD, shot['corners'])


def wall_to_photo(wall, shot, coordinates):
    return map_points(shot_matrix(shot), wall_to_uv(wall, coordinates))


def photo_to_wall(wall, shot, coordinates):
    return uv_to_wall(wall, map_points(np.linalg.inv(shot_matrix(shot)), coordinates))


def pixel_grid(size, start, stop, rect=None):
    width,height = size
    left,top,right,bottom = rect or (0.,0.,1.,1.)
    x = left + (np.arange(width,dtype=np.float64)[None,:]+0.5) * ((right-left)/width)
    y = top + (np.arange(start,stop,dtype=np.float64)[:,None]+0.5) * ((bottom-top)/height)
    return x,y


def sampling_coordinates(transform, x, y, source_size):
    denominator = transform[2,0]*x + transform[2,1]*y + transform[2,2]
    stable = np.abs(denominator) > 1e-8
    denom = np.where(stable, denominator, 1)
    mapped_x = (transform[0,0]*x + transform[0,1]*y + transform[0,2]) / denom
    mapped_y = (transform[1,0]*x + transform[1,1]*y + transform[1,2]) / denom
    width,height = source_size
    within = stable & (mapped_x>=0) & (mapped_x<=1) & (mapped_y>=0) & (mapped_y<=1)
    src_x = np.where(within, mapped_x*width - 0.5, 0)
    src_y = np.where(within, mapped_y*height - 0.5, 0)
    return (src_y, src_x), within


def pull_rgba(linear_rgb, alpha, size, transform, *, rect=None):
    width,height = size
    rgb_result = np.zeros((height,width,3), dtype=np.float32)
    alpha_result = np.zeros((height,width), dtype=np.float32)
    shape = alpha.shape[::-1]
    weighted = linear_rgb * alpha[...,None]
    for start in range(0,height,WARP_ROW_BLOCK):
        stop = min(start + WARP_ROW_BLOCK, height)
        x,y = pixel_grid(size,start,stop,rect)
        coordinates,within = sampling_coordinates(transform,x,y,shape)
        opacity = ndimage.map_coordinates(alpha, coordinates, order=1, mode='nearest') * within
        alpha_result[start:stop] = opacity
        rgb = rgb_result[start:stop]
        for channel in range(3):
            values = ndimage.map_coordinates(weighted[...,channel],coordinates,order=1,mode='nearest')
            np.divide(values, opacity, out=rgb[...,channel], where=opacity > 1e-6)
    return rgb_result, alpha_result


def pull_mask(mask, size, transform):
    width,height = size
    result = np.zeros((height,width), dtype=bool)
    for start in range(0,height,WARP_ROW_BLOCK):
        stop = min(start + WARP_ROW_BLOCK,height)
        coords,within = sampling_coordinates(transform,*pixel_grid(size,start,stop),mask.shape[::-1])
        sampled = ndimage.map_coordinates(mask, coords, order=0, mode='nearest')
        result[start:stop] = within & sampled
    return result


def polygon_mask(size, polygons):
    width,height = size
    mask = Image.new('1',size)
    draw = ImageDraw.Draw(mask)
    for polygon in polygons:
        draw.polygon([(float(x)*width-0.5,float(y)*height-0.5) for x,y in polygon],fill=1)
    return np.asarray(mask, dtype=bool)


def register_view(blobs, wall, shot, max_edge=1500):
    rgb,alpha = open_working(blobs, shot['photo'])
    matrix = shot_matrix(shot)
    size = wall_dimensions(wall, max_edge)
    linear, coverage = pull_rgba(srgb_to_linear(rgb),alpha,size,matrix)
    foreground = np.zeros(coverage.shape,dtype=bool)
    if shot['foreground']:
        mask = polygon_mask(alpha.shape[::-1], shot['foreground'])
        foreground = pull_mask(mask,size,matrix)
    return RegisteredView(linear, coverage, matrix, foreground)


def rectify_art(blobs, artwork, size):
    rgb,alpha = open_working(blobs, artwork['photo'])
    matrix = homography(UNIT_QUAD, artwork['crop'])
    return pull_rgba(srgb_to_linear(rgb),alpha,size,matrix)


def composite(background_rgb, background_alpha, overlay_rgb, overlay_alpha):
    overlay_alpha = np.clip(overlay_alpha,0,1)
    held = background_alpha * (1 - overlay_alpha)
    opacity = held + overlay_alpha
    weighted = overlay_rgb * overlay_alpha[...,None] + background_rgb*held[...,None]
    result = np.zeros_like(weighted)
    np.divide(weighted, opacity[...,None], where=opacity[...,None]>1e-7, out=result)
    return result, opacity
