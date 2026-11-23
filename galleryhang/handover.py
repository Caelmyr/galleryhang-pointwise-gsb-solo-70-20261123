from html import escape


CSS = '''
:root{color-scheme:light;--ink:#282922;--paper:#f4eee1;--line:#d6ccba;--accent:#4d624a;--weak:#655f50}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 system-ui,sans-serif}
header{background:#2c3028;color:#f1e8d6;padding:28px clamp(24px,6vw,86px)}
header p{margin:5px 0;color:#d5ccbc}h1,h2,h3{font-family:Georgia,serif;font-weight:400;line-height:1.1}
h1{font-size:clamp(2.2rem,5vw,4rem);margin:0 0 8px}h2{font-size:2rem;margin:8px 0 16px}
main{max-width:1400px;margin:auto;padding:34px clamp(20px,5vw,70px) 70px}section{margin-bottom:30px}
.layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(260px,1fr);gap:22px;align-items:start}
.panel{padding:20px;background:#fbf8ef;border:1px solid var(--line);border-radius:8px}
figure{margin:0 0 20px}figure img{width:100%;height:auto;border-radius:5px}figcaption{font-size:13px;color:var(--weak)}
a{color:var(--accent);text-underline-offset:4px}a:focus{outline:2px solid #b37352}
.nav{display:flex;flex-wrap:wrap;gap:9px}.nav a{border:1px solid var(--line);border-radius:5px;padding:7px 10px;text-decoration:none}
.wrap{overflow:auto}table{border-collapse:collapse;width:100%;min-width:510px;font-size:13px}
th,td{padding:9px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}th{background:#eee6d5}
small{color:var(--weak)}.legend{padding:4px 7px;border-radius:4px;background:#eadfca;font-size:13px}.warn{background:#f5dfd2;color:#8a482b}
@media(max-width:820px){.layout{display:block}.panel{margin-bottom:18px}}
'''


def cell(value):
    return escape(str('—' if value is None or value=='' else value),quote=True)


def number_text(value,unit=None):
    if value is None:
        return '—'
    return f'{value:g} {unit}'.strip() if unit else f'{value:g}'


def table(headers,rows,empty='No entries for this issue.'):
    if not rows:
        return f'<p>{cell(empty)}</p>'
    heading = ''.join(f'<th scope="col">{cell(key)}</th>' for key in headers)
    contents = ''.join('<tr>'+''.join(f'<td>{cell(value)}</td>' for value in row)+'</tr>' for row in rows)
    return f'<div class="wrap"><table><thead><tr>{heading}</tr></thead><tbody>{contents}</tbody></table></div>'


def menu(link_map):
    return '<div class="nav">'+''.join(f'<a href="{cell(path)}">{cell(label)}</a>' for label,path in link_map)+'</div>'


def picture(filename, caption):
    return f'<figure><a href="{cell(filename)}"><img src="{cell(filename)}" alt="{cell(caption)}"></a><figcaption>{cell(caption)}</figcaption></figure>'


def card(title,contents):
    return f'<section class="panel"><h2>{cell(title)}</h2>{contents}</section>'


def artworks(scene):
    current = [row for row in scene.works()]
    rows = []
    for position,artwork in current:
        description = [artwork['medium'],artwork['year']]
        dimensions = f"{number_text(artwork['width'])} × {number_text(artwork['height'])} mm"
        rows.append((artwork['title'],artwork['artist'],artwork['inventory'],' · '.join(filter(None,description)),dimensions))
    return table(('Work','Artist','Inventory','Medium and date','Outside dimensions'),rows,'No artworks in this issue.')


def point_rows(mounts):
    for record in mounts['works']:
        if not record['points']:
            yield record['index'],record['title'],record['catalog_number'],'—','—','No attachment recorded'
        for point in record['points']:
            x,y = point['datum_x'],point['datum_y']
            if point['inside'] and not point['restricted']:
                state = point['nearby_mark']['name'] if point['nearby_mark'] else 'Check on wall'
            else:
                state = 'Inspect clearance before marking'
            yield record['index'],record['title'],record['catalog_number'],point['name'],f'{x:g}, {y:g} mm',state


def fixing_section(mounts):
    datum = mounts['origin']
    entry = f"<p>Datum reference {number_text(datum['left'],'mm')} from the left and {number_text(datum['from_floor'],'mm')} above the floor.</p>"
    links = menu([('Fixing schedule','fixings.csv'),('Measured fixing diagram','mounts.png')])
    marks = table(['Order','Work','Catalog','Fixing mark','From datum','Site check'],list(point_rows(mounts)))
    return card('Fixing schedule',entry+links+marks)


def clearance_section(data):
    summary = data['summary']
    rows = []
    for entry in data['items']:
        if entry['outside']:
            rows.append([entry['name'],'Wall edge',', '.join(entry['outside']),'—'])
        rows.extend([entry['name'],row['name'],'Feature clearance',number_text(row['distance'],'mm')]
                    for row in entry['obstructions'])
    rows.extend([pair['first_name'],pair['second_name'],pair['kind'],number_text(pair['distance'],'mm')]
                for pair in data['pairs'])
    label = ', '.join(f"{count} {key.replace('_',' ')}" for key,count in summary.items() if count)
    return card('Wall clearance',f'<p>{cell(label or "No clearance alerts in this issue")}</p>'+
                table(('Item','Against','Check','Distance'),rows,'No conflicts found at saved review settings.')+
                menu([('Clearance diagram','clearance.png'),('Clearance table','clearances.csv')]))


def label_section(data):
    rows = []
    for item in data['results']:
        contrast = item['contrast_low']
        text = f'{contrast:g}:1' if contrast is not None else 'Unobserved'
        rows.append([item['title'],item['style'],item['lines']['line_count'],text,
                     'Review' if item['needs_attention'] else 'Clear'])
    links = menu([('Label sheet','labels.png')]) if rows else ''
    return card('Labels',table(['Caption','Material','Text lines','Low contrast','Review'],rows)+links)


def source_section(sources):
    shot = sources.get('shot')
    wall = ' × '.join(number_text(size) for size in sources['wall_size_mm'])+' mm'
    caption = f'<p>Measured wall size: {cell(wall)}.</p>'
    if shot:
        caption += f'<p>Site montage reference: {cell(shot["name"])}.</p>'
    if sources['date']:
        caption += f'<p>Installation date: {cell(sources["date"])}.</p>'
    return card('Recorded sources',caption+menu([('Geometry records','geometry.json')]))


def handover_html(scene,name,images,reports,proof_only):
    generated = [picture('montage.png','Proposed hanging in the site photograph')] if 'montage' in images else []
    generated.append(picture('elevation.png','Measured wall showing the artwork arrangement'))
    pdf = ('Download client proof','proof.pdf') if proof_only else ('Download installation PDF','installation.pdf')
    navigation = menu([pdf,('Elevation image','elevation.png')])
    source = card('Artwork list',artworks(scene))
    first_panel = ''.join(generated)
    second_panel = source
    sections = ''
    if not proof_only:
        second_panel += source_section(reports['sources'])
        sections = fixing_section(reports['mounts']) + clearance_section(reports['clearance']) + label_section(reports['labels'])
    revision = scene.project['revision']
    heading = f'{scene.wall["name"]} · {scene.layout["name"]} · Revision {revision}'
    page = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{cell(name)} | GalleryHang</title><style>{CSS}</style></head><body>'
            f'<header><small>GalleryHang issue</small><h1>{cell(scene.project["name"])}</h1>'
            f'<p>{cell(name)}</p><p>{cell(heading)}</p></header>'
            f'<main><section>{navigation}</section><div class="layout"><div>{first_panel}</div>'
            f'<div>{second_panel}</div></div>{sections}</main></body></html>')
    return page.encode('utf-8')
