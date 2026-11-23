from io import BytesIO
from math import ceil

from PIL import Image, ImageDraw

from .labels import font, wrap_text


PAPER = '#f8f3e9'
INK = '#252620'
MUTED = '#625e51'
LINE = '#c6bdad'
ACCENT = '#c87b4e'
PAGE_W,PAGE_H = 1240,1754
MARGIN = 85


class Book:
    def __init__(self, title, subtitle, details):
        self.title = title
        self.subtitle = subtitle
        self.details = details
        self.pages = []
        self.page = None
        self.draw = None
        self.cursor = 0
        self.bottom = PAGE_H-120

    def add_page(self, section):
        self.page = Image.new('RGB',(PAGE_W,PAGE_H),PAPER)
        self.draw = ImageDraw.Draw(self.page)
        self.pages.append(self.page)
        self.cursor = 88
        self.draw.rectangle((MARGIN,self.cursor,MARGIN+16,self.cursor+16),fill=ACCENT)
        self.draw.text((MARGIN+29,self.cursor-3),'GALLERYHANG  /  STUDIO ISSUE',font=font(21,heading=True),fill=INK)
        self.cursor += 58
        self.heading(self.title, size=48)
        self.paragraph(self.subtitle, size=24)
        self.annotate(self.details)
        self.rule()
        self.heading(section,size=29)

    def heading(self, value, *, size=34):
        face = font(size,heading=True)
        for text in wrap_text(value,PAGE_W-2*MARGIN,face):
            self.draw.text((MARGIN,self.cursor),text,font=face,fill=INK,anchor='lt')
            self.cursor += round(size*1.26)
        self.cursor += 17

    def paragraph(self, value, *, size=24):
        face = font(size)
        for text in wrap_text(value,PAGE_W-2*MARGIN,face):
            self.draw.text((MARGIN,self.cursor),text,font=face,fill=MUTED,anchor='lt')
            self.cursor += round(size*1.3)
        self.cursor += 12

    def annotate(self,value):
        self.paragraph(value,size=17)

    def rule(self):
        self.draw.line((MARGIN,self.cursor,PAGE_W-MARGIN,self.cursor),fill=LINE,width=2)
        self.cursor += 31

    def artwork(self, picture, max_height):
        frame = picture.convert('RGB')
        frame.thumbnail((PAGE_W-2*MARGIN,max_height), Image.Resampling.LANCZOS)
        self.page.paste(frame,(MARGIN,self.cursor))
        self.cursor += frame.height+24

    def row_lines(self, values, widths, face):
        return [wrap_text(str(text if text is not None else ''),max(8,width-25),face)
                for text,width in zip(values,widths)]

    def table_row(self, values, widths, *, header=False):
        face = font(17,heading=header)
        groups = self.row_lines(values,widths,face)
        line_height = 22
        row_height = max(45,max(map(len,groups),default=1)*line_height+18)
        tone = '#e8e0d3' if header else PAPER
        self.draw.rectangle((MARGIN,self.cursor,PAGE_W-MARGIN,self.cursor+row_height),fill=tone)
        x = MARGIN
        for width,lines in zip(widths,groups):
            for number,entry in enumerate(lines):
                self.draw.text((x+10,self.cursor+10+number*line_height),entry,font=face,fill=INK,anchor='lt')
            x += width
        self.cursor += row_height
        self.draw.line((MARGIN,self.cursor,PAGE_W-MARGIN,self.cursor),fill=LINE,width=1)

    def table(self, title, headers, values, widths):
        if sum(widths) != PAGE_W-2*MARGIN:
            raise ValueError('Table columns do not fit the paper margins.')
        self.add_page(title)
        self.table_row(headers,widths,header=True)
        for item in values:
            face = font(17)
            groups = self.row_lines(item,widths,face)
            height = max(45,max(map(len,groups),default=1)*22+18)
            if self.cursor+height > self.bottom:
                self.add_page(title+' · continued')
                self.table_row(headers,widths,header=True)
            self.table_row(item,widths)

    def finish(self):
        for index,page in enumerate(self.pages,1):
            draw = ImageDraw.Draw(page)
            draw.line((MARGIN,PAGE_H-92,PAGE_W-MARGIN,PAGE_H-92),fill=LINE,width=1)
            draw.text((MARGIN,PAGE_H-72),self.title,font=font(15),fill=MUTED)
            draw.text((PAGE_W-MARGIN,PAGE_H-72),f'{index} / {len(self.pages)}',font=font(15),fill=MUTED,anchor='ra')
        output = BytesIO()
        self.pages[0].save(output,'PDF',save_all=True,append_images=self.pages[1:],resolution=124.0,
                           author='GalleryHang',title=self.title,subject=self.subtitle)
        return output.getvalue()


def commissioning_rows(mounts):
    rows = []
    for work in mounts['works']:
        display_name = f"{work['index']}. {work['title']}"
        label = work['catalog_number'] or '—'
        weight = f"{work['weight']:g} kg"
        if not work['points']:
            rows.append([display_name,label,'—','—',weight,'Field measurement required'])
        for point in work['points']:
            datum = f"{point['datum_x']:g}, {point['datum_y']:g}"
            flags = []
            if not point['inside']:
                flags.append('off wall')
            if point['restricted']:
                flags.extend(item['name'] for item in point['restricted'])
            adopted = point.get('reuse')
            if adopted:
                state = 'confirmed' if adopted['confirmed'] else 'pending confirmation'
                flags.append(f"reuse hole #{adopted['number']} ({state})")
            elif not flags and point['nearby_mark']:
                mark = point['nearby_mark']
                flags.append(f"{mark['name']} within {mark['distance']:g} mm")
            rows.append([display_name,label,point['name'],datum,weight,', '.join(flags) or '—'])
    return rows


def adopted_hole_rows(mounts):
    reuse = mounts.get('reuse')
    if not reuse:
        return []
    rows = []
    for row in reuse['assignments']:
        for hole in row['holes']:
            state = 'confirmed' if row['status'] == 'confirmed' else 'pending confirmation'
            rows.append([row['title'], hole.get('name') or '—', f"#{hole['number']}",
                         f"{hole['x']:g}, {hole['y']:g}", f"{row.get('shift_mm') or 0:g} mm", state])
    return rows


def clearance_rows(clearance):
    rows = []
    for item in clearance['items']:
        if item['outside']:
            rows.append([item['name'],'Edge of wall',', '.join(item['outside']),str(item['visible_wall_fraction']*100)+'%'])
        for obstruction in item['obstructions']:
            rows.append([item['name'],obstruction['name'],f"Needs {obstruction['clearance']:g} mm",
                         f"{obstruction['distance']:g} mm"])
    for pair in clearance['pairs']:
        rows.append([pair['first_name'],pair['second_name'],pair['kind'], f"{pair['distance']:g} mm"])
    return rows


def label_rows(result):
    rows = []
    for entry in result['results']:
        score = entry['contrast_low']
        state = 'Check wording or contrast' if entry['needs_attention'] else 'Within review thresholds'
        rows.append([entry['title'],entry['backing'],entry['lines']['line_count'],
                     'Unobserved' if score is None else f'{score:g}:1',state])
    return rows


def installation_pdf(scene, snapshot_name, revision, images, reports, *, proof_only=False):
    names = f"{scene.wall['name']} · {scene.layout['name']}"
    info = f"{scene.project['venue'] or 'Local exhibition'}  |  Project revision {revision}  |  {snapshot_name}"
    book = Book(scene.project['name'],names,info)
    book.add_page('Proposed artwork on the wall')
    book.artwork(images.get('montage',images['elevation']),890)
    date = scene.project['date']
    if date:
        book.paragraph(f'Installation date: {date}')
    if scene.layout['note']:
        book.paragraph(scene.layout['note'],size=20)
    if proof_only:
        return book.finish()
    book.add_page('Measured elevation and mark reference')
    book.artwork(images['mounts'],940)
    origin = reports['mounts']['origin']
    book.paragraph(f"Datum at {origin['left']:g} mm from left and {origin['from_floor']:g} mm above floor.")
    book.annotate('Mark positions use measured back details and a planar wall estimate. Confirm surfaces, attachments and loads with the installer before drilling.')
    reuse = reports['mounts'].get('reuse')
    if reuse:
        summary = reuse['summary']
        book.annotate(f"Existing holes: {summary['reused_holes']} reused across {summary['reused_works']} works, "
                      f"{summary['new_hole_points']} new fixing points. Planned reuse confirms a pair only when both "
                      f"fixing points sit within {reuse['tolerance_mm']:g} mm of their own recorded hole.")
    rows = commissioning_rows(reports['mounts'])
    if rows:
        book.table('Fixing points from datum', ['Artwork','Catalog','Fixing','X, Y mm','Weight','Field notes'],
                   rows,[262,116,130,154,100,308])
    reuse_rows = adopted_hole_rows(reports['mounts'])
    if reuse_rows:
        book.table('Adopted existing holes', ['Work','Hole','Number','Hole X, Y mm','Shift','State'],
                   reuse_rows,[260,150,86,240,110,224])
    clearance = clearance_rows(reports['clearance'])
    if clearance:
        book.table('Clearance checks', ['Item','Against','Condition','Distance / area'],clearance,[292,294,294,190])
    labels = label_rows(reports['labels'])
    if labels:
        book.table('Wall labels', ['Caption','Backing','Lines','Contrast','Review'],labels,[317,195,83,110,365])
    return book.finish()


def labels_sheet(scene):
    from .labels import label_image

    if not scene.layout['labels']:
        return Image.new('RGB',(480,250),PAPER)
    columns = min(3,len(scene.layout['labels']))
    thumb_w,thumb_h = 300,185
    gap = 26
    rows = ceil(len(scene.layout['labels'])/columns)
    target = Image.new('RGB',(columns*thumb_w+(columns+1)*gap,rows*thumb_h+(rows+1)*gap),PAPER)
    for index,spec in enumerate(scene.layout['labels']):
        image,_ = label_image(scene,spec,(round(spec['width']*3),round(spec['height']*3)))
        image.thumbnail((thumb_w,thumb_h),Image.Resampling.LANCZOS)
        x = gap + (index%columns)*(thumb_w+gap) + (thumb_w-image.width)//2
        y = gap + (index//columns)*(thumb_h+gap) + (thumb_h-image.height)//2
        target.paste(image,(x,y),image.getchannel('A'))
    return target
