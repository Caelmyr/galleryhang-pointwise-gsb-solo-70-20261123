from copy import deepcopy
from dataclasses import dataclass
from math import cos, sin

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy.ndimage import gaussian_filter

from .layout import Scene, new_layout
from .model import new_artwork, new_id, new_project, new_shot, new_wall
from .photos import linear_to_srgb, png_bytes, srgb_to_linear, store_capture, uint8_image
from .projection import UNIT_QUAD, homography, pull_rgba
from .render import Layer, project_art_onto_shot


SEED = 127
PHOTO_SIZE = (1280,800)
MAIN_CORNERS = [[0.095,0.175],[0.878,0.112],[0.886,0.789],[0.102,0.745]]
SIDE_CORNERS = [[0.152,0.127],[0.825,0.216],[0.827,0.763],[0.151,0.826]]


@dataclass(frozen=True)
class ArtworkSample:
    title: str
    artist: str
    year: str
    medium: str
    width: int
    height: int
    palette: tuple
    frame: str
    design: str
    caption: str


EXAMPLES = (
    ArtworkSample('Suspended Light','Elena Morita','2024','Pigment print',660,820,
                  ('#ddd4bd','#3a463f','#e2b679','#826b58','#f0e9d6'),'walnut','horizon',
                  'A late reflection breaks against a line of dark reeds.'),
    ArtworkSample('Between Weather','Rowan Hale','2022','Oil on panel',1240,990,
                  ('#d6c3a0','#4e5750','#bd7852','#726f58','#ede2c5'),'oak','hills',
                  'A study of the interval between one sky and the next.'),
    ArtworkSample('A Quiet Line','Elena Morita','2025','Monotype',580,740,
                  ('#ede0c5','#2e423c','#b88f61','#776d54','#f8ead0'),'ink','branch',
                  'Dry marks register the direction of a moving stem.'),
    ArtworkSample('Low Orchard','Samira Kim','2023','Pastel on paper',850,630,
                  ('#d2be95','#43554d','#9c6846','#757b67','#efe6cb'),'oak','hills',
                  'Gathered surfaces under a dark northern sky.'),
    ArtworkSample('Field Notes III','Samira Kim','2024','Paper and ink',520,640,
                  ('#e5d8ba','#485749','#ab725b','#63796c','#f3eee1'),'white','geometry',
                  'Studies of diagonal cuts in cream and ochre stock.'),
)


def shared_noise(rng,size,grain=2.0):
    return gaussian_filter(rng.normal(0,grain,(size[1],size[0])),sigma=0.45).astype(np.float32)


def painted_background(size,palette,seed):
    rng = np.random.default_rng(seed)
    image = Image.new('RGB',size,palette[0])
    tint = ImageDraw.Draw(image)
    for _ in range(85):
        x,y = rng.uniform(0,size[0]),rng.uniform(0,size[1])
        width,height = rng.uniform(5,size[0]/3),rng.uniform(2,size[1]/10)
        color = palette[rng.integers(0,len(palette))]
        tint.ellipse((x-width,y-height,x+width,y+height),fill=color)
    backdrop = image.filter(ImageFilter.GaussianBlur(max(1,size[0]//10)))
    return Image.blend(Image.new('RGB',size,palette[0]),backdrop,0.19)


def wave_shape(rng,width,height,mean,amplitude,phase):
    controls = []
    count = 24
    for idx in range(count+1):
        x = width*idx/count
        y = height*(mean + amplitude*(sin(idx*0.26+phase)+0.28*cos(idx*0.73+phase)))
        controls.append((x,y+rng.uniform(-2,2)))
    return controls


def landscape(draw,size,palette,rng,*,horizon=False):
    width,height = size
    layers = [(0.43,0.11,palette[2]),(0.51,0.10,palette[3]),(0.66,0.12,palette[1]),
              (0.78,0.09,palette[3]),(0.90,0.07,palette[1])]
    if horizon:
        layers = [(0.46,0.02,palette[2]),(0.54,0.015,palette[3]),(0.63,0.032,palette[1]),
                  (0.75,0.021,palette[0]),(0.89,0.026,palette[1])]
    for mean,wave,color in layers:
        boundary = wave_shape(rng,width,height,mean,wave,rng.uniform(0,4))
        draw.polygon([*boundary,(width,height),(0,height)],fill=color)
    for _ in range(58):
        x,y = rng.uniform(0,width),rng.uniform(height*0.4,height)
        span = rng.uniform(2,width*0.19)
        ink = palette[rng.choice((0,1,3,4))]
        draw.line((x,y,x+span,y+rng.uniform(-2,2)),fill=ink,width=rng.integers(1,3))
    if horizon:
        point = (int(width*0.70),int(height*0.26))
        radius = width*0.07
        draw.ellipse((point[0]-radius,point[1]-radius,point[0]+radius,point[1]+radius),fill=palette[4])


def branch_study(draw,size,palette,rng):
    width,height = size
    for index in range(7):
        points = []
        x = width*(0.4+index*0.045)
        for step in range(9):
            y = height*((8-step)/10+0.1)
            x += rng.uniform(-0.07*width,0.048*width)
            points.append((x,y))
        color = palette[1] if index<5 else palette[2]
        draw.line(points,fill=color,width=(10-index),joint='curve')
        for number,(xz,yz) in enumerate(points[2:],2):
            dx = rng.uniform(40,105) * (-1 if number%2 else 1)
            dy = rng.uniform(-110,-30)
            draw.line((xz,yz,xz+dx,yz+dy),fill=color,width=3)
            draw.line((xz+dx,yz+dy,xz+dx*1.18,yz+dy*1.22),fill=palette[3],width=2)
    for _ in range(47):
        x,y = rng.uniform(0.15,0.9)*width,rng.uniform(0.15,0.92)*height
        draw.ellipse((x,y,x+4,y+6),fill=palette[3])


def geometry_study(draw,size,palette,rng):
    width,height = size
    for i in range(8):
        x = width*(0.13+0.09*i)
        y = height*(0.05+0.10*i)
        draw.polygon(((x,y),(x+width*0.25,y+height*0.11),(x+width*0.15,y+height*0.22),
                      (x-width*0.09,y+height*0.13)),fill=palette[(i%3)+1])
    for _ in range(15):
        x,y = rng.uniform(0,width),rng.uniform(0,height)
        draw.line((x,y,x+width*0.30,y-height*0.07),fill=palette[4],width=3)


def artwork_source(sample,index):
    size = (820,max(400,round(820*sample.height/sample.width)))
    if size[1]>1200:
        size = (max(400,round(size[0]*1200/size[1])),1200)
    rng = np.random.default_rng(SEED+index)
    ground = painted_background(size,sample.palette,SEED+index)
    draw = ImageDraw.Draw(ground)
    if sample.design=='hills' or sample.design=='horizon':
        landscape(draw,size,sample.palette,rng,horizon=sample.design=='horizon')
    elif sample.design=='branch':
        branch_study(draw,size,sample.palette,rng)
    else:
        geometry_study(draw,size,sample.palette,rng)
    noise = shared_noise(rng,size,3.9)
    yy,xx = np.indices((size[1],size[0]),dtype=np.float32)
    weave = (0.7*np.sin(xx*2.35)+0.6*np.cos(yy*2.8)).astype(np.float32)
    rough = np.clip(np.asarray(ground,dtype=np.float32)+(noise+weave)[...,None],0,255)
    return Image.fromarray(np.uint8(rough))


def wall_plaster(size,variant):
    width,height = size
    yy,xx = np.mgrid[0:1:complex(0,height),0:1:complex(0,width)]
    color = np.array([0.85,0.816,0.75],dtype=np.float32)
    raw = np.broadcast_to(color,(height,width,3)).copy()
    gradients = 0.04*np.cos(xx*2.7)-0.09*yy + 0.076*np.exp(-((xx-0.25)**2+(yy-0.18)**2)*18)
    if variant==1:
        gradients += 0.07*np.sin(xx*2.2)+0.025*np.cos(yy*4)
    raw += gradients[...,None]
    random = np.random.default_rng(SEED+80)
    plume = shared_noise(random,size,1.75)/255
    raw += plume[...,None]
    return np.clip(raw,0,1)


def architectural_background(size,variant):
    width,height = size
    yy,xx = np.ogrid[0:1:complex(0,height),0:1:complex(0,width)]
    sky = yy<0.20
    floor = yy>0.70
    ceiling = np.array([0.77,0.73,0.67])
    side = np.array([0.70,0.63,0.54])
    concrete = np.array([0.53,0.47,0.40])
    surfaces = np.where(sky[...,None],ceiling,np.where(floor[...,None],concrete,side))
    light = 1.05+0.12*np.cos(xx*2.4)-0.10*yy
    fill = surfaces*light[...,None]
    fill += shared_noise(np.random.default_rng(SEED+variant+20),size,1.45)[...,None]/255
    base = uint8_image(fill).convert('RGB')
    draw = ImageDraw.Draw(base)
    draw.line(((0.02*width,0.16*height),(0.57*width,0.12*height),(width,0.11*height)),fill='#888176',width=4)
    draw.line(((0.01*width,0.16*height),(0.01*width,0.72*height)),fill='#71675e',width=4)
    for k in range(4):
        x = (0.12+k*0.23)*width
        draw.line(((0.40*width,0.73*height),(x,1.09*height)),fill='#746b60',width=1)
    for k in range(3):
        start = ((0.12+k*0.25)*width,(0.15-0.014*k)*height)
        draw.line((start,(start[0],start[1]+13)),fill='#52493d',width=3)
        draw.rounded_rectangle((start[0]-9,start[1]+10,start[0]+9,start[1]+19),radius=2,fill='#696156')
    return base


def blank_site_photo(corners,variant):
    base = Layer.image_layer(architectural_background(PHOTO_SIZE,variant))
    wall_size = (1200,720)
    plaster = srgb_to_linear(wall_plaster(wall_size,variant))
    plaster[-22:] *= 0.88
    plaster[-23] *= 0.68
    transform = np.linalg.inv(homography(UNIT_QUAD,corners))
    wall = Layer(*pull_rgba(plaster,np.ones(wall_size[::-1],dtype=np.float32),PHOTO_SIZE,transform))
    result = base.over(wall).image().convert('RGB')
    draw = ImageDraw.Draw(result)
    wall_edge = np.asarray(corners)*np.asarray(PHOTO_SIZE)
    for first,second in ((wall_edge[0],wall_edge[3]),(wall_edge[1],wall_edge[2])):
        draw.line((tuple(first),tuple(second)),fill='#847b6c',width=2)
    return result


def site_furnishings(image, variant):
    x = 1055 if variant==0 else 1015
    y = 548 if variant==0 else 575
    draw = ImageDraw.Draw(image,'RGBA')
    draw.ellipse((x-56,y+95,x+123,y+148),fill=(27,26,24,64))
    draw.polygon(((x,y),(x+75,y+8),(x+111,y-3),(x+38,y-13)),fill='#d2c5ac')
    draw.polygon(((x,y),(x+75,y+8),(x+75,y+108),(x,y+99)),fill='#b7ab95')
    draw.polygon(((x+75,y+8),(x+111,y-3),(x+111,y+96),(x+75,y+108)),fill='#877a68')
    for offset in range(4):
        draw.ellipse((x+20+offset*3,y-45+offset*3,x+64+offset*3,y-8),fill='#706e60')
    foreground = [[x/PHOTO_SIZE[0], (y-44)/PHOTO_SIZE[1]],[(x+111)/PHOTO_SIZE[0],(y-34)/PHOTO_SIZE[1]],
                  [(x+111)/PHOTO_SIZE[0],(y+108)/PHOTO_SIZE[1]],[x/PHOTO_SIZE[0],(y+101)/PHOTO_SIZE[1]]]
    return image,foreground


def save_photo(blobs,image,filename,role):
    return store_capture(blobs,png_bytes(image),role,filename,'Synthetic demonstration artwork or site view',
                         new_id(),synthetic=True)


def fill_artwork(blobs,index,sample):
    photo = save_photo(blobs,artwork_source(sample,index),f'exhibition-art-{index+1}.png','artwork')
    fields = {'title':sample.title,'artist':sample.artist,'year':sample.year,'medium':sample.medium,
              'width':sample.width,'height':sample.height,'frame':sample.frame,'inventory':f'NW-{70+index}',
              'mat':16 if index==1 else 21,'border':19 if index==1 else 14,
              'weight':round(sample.width*sample.height/126_000,1), 'description':sample.caption}
    if index==2:
        fields.update(attachment='center',fixing_top=78)
    return new_artwork(fields,photo)


def demo_wall(blobs):
    wall = new_wall({'name':'West wall', 'width':5400.0, 'height':3050.0,'paint':'#ddd3bd',
                     'baseboard':83.0,'datum':[450.0,410.0],
                     'note':'Measured from the plaster return and finished floor.'})
    for variant,(name,corners) in enumerate((('Entrance · reference',MAIN_CORNERS),('Window side · alternate',SIDE_CORNERS))):
        image,foreground = site_furnishings(blank_site_photo(corners,variant),variant)
        photo = save_photo(blobs,image,f'exhibition-site-{variant+1}.png','reference' if variant==0 else 'alternate')
        shot = new_shot(photo,name,photo['role'],corners)
        shot['foreground'] = [foreground]
        wall['shots'].append(shot)
    wall['obstacles'] = [
        {'id':new_id(),'name':'Door return','kind':'opening','left':0.0,'bottom':0.0,'width':390.0,
         'height':2170.0,'clearance':75.0,'depth':0.0,'note':'Keep the return clear.'},
        {'id':new_id(),'name':'Stone plinth','kind':'plinth','left':4620.0,'bottom':0.0,'width':610.0,
         'height':1030.0,'clearance':60.0,'depth':680.0,'note':'Freestanding pedestal.'},
        {'id':new_id(),'name':'Lighting plate','kind':'switch','left':4280.0,'bottom':970.0,'width':96.0,
         'height':108.0,'clearance':38.0,'depth':0.0,'note':''},
    ]
    wall['mount_marks'] = [
        {'id':new_id(),'name':'Existing fixing A','kind':'hole','x':1000.0,'y':2040.0,'usable':True,'note':''},
        {'id':new_id(),'name':'Existing fixing B','kind':'hole','x':2823.0,'y':2126.0,'usable':True,'note':''},
    ]
    for index,reference in enumerate(('#dad0ba','#c9bca4')):
        wall['swatches'].append({'id':new_id(),'name':f'Plaster sample {index+1}','appearance':reference,
                                 'region':[[0.12+index*0.54,0.14],[0.22+index*0.54,0.14],
                                           [0.22+index*0.54,0.23],[0.12+index*0.54,0.23]],
                                 'shot':wall['shots'][0]['id'],'note':''})
    return wall


def example_layout(wall,artworks):
    layout = new_layout('Opening hang',wall['id'])
    layout.update(note='Three works across the principal wall, with the largest work on the center sightline.',gap=92.0)
    for index,(x,y) in enumerate(((1270.0,1660.0),(2470.0,1625.0),(3640.0,1680.0))):
        placement = {'id':new_id(),'artwork':artworks[index]['id'],'x':x,'y':y,'angle':0.0,
                     'pinned':index==1,'visible':True,'opacity':1.0}
        layout['placements'].append(placement)
        layout['labels'].append({'id':new_id(),'placement':placement['id'],'name':'','body':'',
                                 'x':x+artworks[index]['width']/2-20,'y':y-artworks[index]['height']/2-99,
                                 'width':185.0,'height':105.0,'style':'paper' if index!=2 else 'vinyl',
                                 'align':'left','type_size':3.7,'ink':'#35312c','paper':'#f3ede1'})
    for name,x,y,distance,target in (('Entrance',2530.0,1480.0,4120.0,2500.0),
                                     ('Low viewpoint',4000.0,1160.0,2260.0,3180.0)):
        layout['stations'].append({'id':new_id(),'name':name,'x':x,'distance':distance,'height':y,
                                   'target_x':target,'target_y':1590.0,'fov':65.0,'note':''})
    return layout


def synthetic_installation(blobs,project,wall,layout):
    draft = deepcopy(project)
    layout_copy = draft['layouts'][0]
    layout_copy['placements'][0]['x'] += 35
    layout_copy['placements'][2]['y'] -= 22
    layout_copy['placements'][2]['angle'] = 1.5
    scene = Scene.build(draft,layout['id'])
    view = wall['shots'][0]
    rendered = project_art_onto_shot(blobs,scene,view,edge=1200)
    photo = save_photo(blobs,rendered,'exhibition-install-check.png','inspection')
    result = new_shot(photo,'Installed · check photo','inspection',view['corners'])
    result['foreground'] = deepcopy(view['foreground'])
    return result


def build_demo(blobs):
    project = new_project({'name':'North Window Study','venue':'Arbor Rooms · Small gallery',
                           'note':'Synthetic demonstration with one photographed and measured gallery wall.'})
    project['synthetic'] = True
    project['artworks'] = [fill_artwork(blobs,index,sample) for index,sample in enumerate(EXAMPLES)]
    wall = demo_wall(blobs)
    project['walls'] = [wall]
    layout = example_layout(wall,project['artworks'])
    project['layouts'] = [layout]
    wall['shots'].append(synthetic_installation(blobs,project,wall,layout))
    return project


def demo_cover():
    image,_ = site_furnishings(blank_site_photo(MAIN_CORNERS,0),0)
    return image
