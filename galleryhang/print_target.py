from io import BytesIO
from math import ceil

from PIL import Image,ImageDraw

from .labels import font
from .model import InputError
from .mounts import source_attachment


DPI = 152.4
PX_PER_MM = 6
A4 = (210*PX_PER_MM,297*PX_PER_MM)
PANEL_MM = 174
TOP_MM = 82
LEFT_MM = 18


def format_mm(value):
    return f'{value:.1f} mm'


def spot_line(draw, x, y, size=20):
    draw.ellipse((x-size,y-size,x+size,y+size),outline='#292a24',width=2)
    draw.line((x-size-9,y,x+size+9,y),fill='#292a24',width=2)
    draw.line((x,y-size-9,x,y+size+9),fill='#292a24',width=2)


def title_only(draw,work,page_number,pages):
    draw.text((95,81),'GalleryHang · Frame-back measuring card',font=font(31,heading=True),fill='#282a24')
    draw.text((95,133),work['title'][:88],font=font(32),fill='#3f3f32')
    draw.text((95,183),f"{format_mm(work['width'])} × {format_mm(work['height'])} outside · {work['inventory'] or 'No inventory number'}",
              font=font(21),fill='#525148')
    draw.text((95,226),f'Card {page_number+1} of {pages} · reference offsets from outside frame edges',
              font=font(19),fill='#605a50')
    draw.line((95,267,A4[0]-95,267),fill='#a39b89',width=1)


def ruler(draw,x,y,span,cross_axis=False):
    end = int(round(span*PX_PER_MM))
    thick = 3
    if cross_axis:
        draw.line((x,y,x,y+end),fill='#35362e',width=thick)
    else:
        draw.line((x,y,x+end,y),fill='#35362e',width=thick)
    for index in range(0,int(span)+1,1):
        side = 16 if index%10==0 else 12 if index%5==0 else 6
        step = index*PX_PER_MM
        if cross_axis:
            draw.line((x,y+step,x-side,y+step),fill='#383930',width=2)
        else:
            draw.line((x+step,y,x+step,y-side),fill='#383930',width=2)
        if index%10==0:
            if cross_axis:
                draw.text((x-side-5,y+step),str(index),font=font(15),fill='#4d4d43',anchor='rm')
            else:
                draw.text((x+step,y-side-5),str(index),font=font(15),fill='#4d4d43',anchor='mb')


def note(draw,y,lines):
    for line in lines:
        draw.text((110,y),line,font=font(21),fill='#393a30')
        y+=31


def print_mount_card(work):
    points = source_attachment(work)
    if not points:
        raise InputError('Record a back attachment before making a measuring card.')
    pages = []
    for index,(name,dx,dy) in enumerate(points):
        image = Image.new('RGB',A4,'white')
        draw = ImageDraw.Draw(image)
        title_only(draw,work,index,len(points))
        x0,y0 = LEFT_MM*PX_PER_MM,TOP_MM*PX_PER_MM
        from_left = dx + work['width']/2
        from_right = work['width']-from_left
        from_top = work['height']/2-dy
        offset = min(from_left,from_right)
        edge = 'LEFT' if from_left<=from_right else 'RIGHT'
        note(draw,309,[f'Attachment: {name}',f'From left: {format_mm(from_left)}  ·  From top: {format_mm(from_top)}'])
        ruler(draw,x0,y0,PANEL_MM)
        ruler(draw,x0,y0,148,True)
        fixed = offset<=PANEL_MM and from_top<=148
        if fixed:
            marker_x = x0+offset*PX_PER_MM
            marker_y = y0+from_top*PX_PER_MM
            spot_line(draw,marker_x,marker_y)
            draw.text((marker_x+36,marker_y),f'{name} attachment',font=font(22),fill='#43463b',anchor='lm')
            note(draw,1430,[f'Line up the zero reference using the {edge.lower()} and upper edges of the frame.',
                            'Print at actual size. Confirm the 100 mm check line with a physical ruler.'])
        else:
            note(draw,665,['This attachment lies outside a single full-size card.',
                           'Use the numeric offsets above with a physical rule.'])
        draw.text((x0,y0-47),f'{edge} edge zero',font=font(19,heading=True),fill='#48463a')
        y_check = A4[1]-160
        draw.line((x0,y_check,x0+100*PX_PER_MM,y_check),fill='#252821',width=3)
        for x_check in (x0,x0+100*PX_PER_MM):
            draw.line((x_check,y_check-10,x_check,y_check+10),fill='#252821',width=3)
        draw.text((x0,y_check+18),'100 mm print calibration',font=font(18),fill='#4b4e43')
        pages.append(image)
    output = BytesIO()
    pages[0].save(output,'PDF',save_all=True,append_images=pages[1:],resolution=DPI,title=work['title']+' measuring card')
    return output.getvalue()
