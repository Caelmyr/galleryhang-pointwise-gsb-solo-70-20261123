from dataclasses import dataclass
from io import BytesIO
from math import ceil

from PIL import Image,ImageDraw

from .labels import font, label_copy, label_image
from .model import InputError


DOT = 6
PAPER_FORMATS = {'a4':(210,297),'letter':(215.9,279.4)}
PAGE_MARGIN = 12
TOP_TITLE = 17
PANEL_GAP = 10


@dataclass(frozen=True)
class LabelItem:
    identifier: str
    title: str
    width: float
    height: float
    x: float
    y: float
    page: int
    label: dict


def points(*figures):
    return tuple(round(value*DOT) for value in figures)


def available_area(paper):
    width,height = PAPER_FORMATS[paper]
    return width-2*PAGE_MARGIN,height-2*PAGE_MARGIN-TOP_TITLE


def page_layout(scene,paper):
    free_width,free_height = available_area(paper)
    left = 0.0
    top = 0.0
    line_height = 0.0
    page = 0
    physical = []
    separate = []
    oversized = []
    for label in scene.layout['labels']:
        title,_,_ = label_copy(scene,label)
        width,height = label['width'],label['height']
        if label['style']=='vinyl':
            separate.append({'id':label['id'],'title':title,'dimensions_mm':[width,height]})
            continue
        if width>free_width or height>free_height:
            oversized.append({'id':label['id'],'title':title,'dimensions_mm':[width,height]})
            continue
        if left+width>free_width:
            left=0.0
            top+=line_height+PANEL_GAP
            line_height=0.0
        if top+height>free_height:
            left=top=line_height=0.0
            page+=1
        physical.append(LabelItem(label['id'],title,width,height,PAGE_MARGIN+left,PAGE_MARGIN+TOP_TITLE+top,page,label))
        left+=width+PANEL_GAP
        line_height=max(line_height,height)
    return physical,separate,oversized


def print_plan(scene,paper):
    physical,separate,oversized = page_layout(scene,paper)
    entries = [{'id':row.identifier,'title':row.title,'sheet':row.page+1,'position_mm':[row.x,row.y],
                'size_mm':[row.width,row.height]} for row in physical]
    return {'paper':paper,'sheet_count':physical[-1].page+1 if physical else 0,'cards':entries,
            'vinyl':separate,'oversized':oversized}


def cut_marks(draw,x,y,w,h):
    outer,gap=22,5
    for left,top,dx,dy in ((x,y,-1,-1),(x+w,y,1,-1),(x,y+h,-1,1),(x+w,y+h,1,1)):
        draw.line((left+dx*gap,top,left+dx*outer,top),fill='#494943',width=2)
        draw.line((left,top+dy*gap,left,top+dy*outer),fill='#494943',width=2)


def place_card(scene, sheet, item):
    width,height = points(item.width,item.height)
    x,y = points(item.x,item.y)
    image,style = label_image(scene,item.label,(width,height))
    sheet.paste(image,(x,y),image.getchannel('A'))
    draw = ImageDraw.Draw(sheet)
    cut_marks(draw,x,y,width,height)
    if style['overflow']:
        warning = 'Caption copy overflows this card'
        draw.text((x+7,y+height-22),warning,font=font(15,heading=True),fill='#832b21')


def heading(sheet, scene, paper, index, count):
    draw = ImageDraw.Draw(sheet)
    right = sheet.width-PAGE_MARGIN*DOT
    left,y = points(PAGE_MARGIN,8)
    draw.text((left,y),'GalleryHang · printed wall labels',font=font(22,heading=True),fill='#343628')
    draw.text((right,y),f'{paper.upper()}  {index+1} / {count}',font=font(17),fill='#59574c',anchor='ra')
    draw.text((left,y+31),scene.project['name']+' · '+scene.layout['name'],font=font(17),fill='#5b5649')
    calibration_y = sheet.height-round(6.0*DOT)
    start = left
    end = start+100*DOT
    draw.line((start,calibration_y,end,calibration_y),fill='#3b3b35',width=2)
    draw.line((start,calibration_y-7,start,calibration_y+7),fill='#3b3b35',width=2)
    draw.line((end,calibration_y-7,end,calibration_y+7),fill='#3b3b35',width=2)
    draw.text((end+16,calibration_y),'100 mm · print at actual size',font=font(15),fill='#59574c',anchor='lm')


def print_wall_labels(scene,paper):
    entries,_,_ = page_layout(scene,paper)
    if not entries:
        raise InputError('There are no printed cards that fit the selected paper. Wall vinyl stays in the label review.')
    sheet_total = entries[-1].page+1
    if sheet_total>24:
        raise InputError('This selection needs more than 24 print sheets. Issue multiple layouts or reduce label size.')
    size = points(*PAPER_FORMATS[paper])
    sheets = [Image.new('RGB',size,'white') for _ in range(sheet_total)]
    for index,sheet in enumerate(sheets):
        heading(sheet,scene,paper,index,sheet_total)
    for item in entries:
        place_card(scene,sheets[item.page],item)
    data = BytesIO()
    sheets[0].save(data,'PDF',save_all=True,append_images=sheets[1:],resolution=DOT*25.4,
                   title=scene.project['name']+' wall labels')
    return data.getvalue()
