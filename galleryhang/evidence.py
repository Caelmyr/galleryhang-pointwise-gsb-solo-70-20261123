from collections import Counter
import csv
from io import BytesIO, StringIO
import json
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

from .clearance import clearance_report
from .handover import handover_html
from .labels import labels_report
from .layout import Scene
from .model import InputError, choice, text
from .mounts import mounting_csv, mounting_report
from .pages import installation_pdf, labels_sheet
from .photos import png_bytes
from .render import elevation, project_art_onto_shot
from .revisions import frozen_geometry
from .store import document, sha256
from .viewing import viewpoint_report


ARCHIVE_SCHEMA = 'galleryhang-issue-v1'
ISSUE_TYPES = ('installer','proof')
MAX_ARCHIVE_BYTES = 45 * 1024**2
MAX_UNCOMPRESSED_BYTES = 115 * 1024**2
MAX_ARCHIVE_ENTRIES = 40
ARTIFACT_TYPES = {'pdf':'application/pdf','png':'image/png','csv':'text/csv; charset=utf-8',
                  'json':'application/json','zip':'application/zip','html':'text/html; charset=utf-8'}


def json_bytes(value):
    return (document(value)+'\n').encode('utf-8')


def number_cell(value):
    if value is None:
        return ''
    if isinstance(value,str) and value[:1] in ('=','+','-','@','\t','\r'):
        return "'"+value
    return value


def csv_bytes(columns, rows):
    buffer = StringIO(newline='')
    output = csv.writer(buffer,lineterminator='\n')
    output.writerow(columns)
    for row in rows:
        output.writerow(number_cell(cell) for cell in row)
    return buffer.getvalue().encode('utf-8-sig')


def copy_counts(scene):
    return {'works':len(scene.works()),'labels':len(scene.layout['labels']),
            'observations':len(scene.layout['observations']),'stations':len(scene.layout['stations'])}


def clearance_csv(report):
    columns = ['name','type','edge','object','actual_mm','desired_mm','covered_wall_fraction']
    rows = []
    for item in report['items']:
        for edge in item['outside']:
            rows.append([item['name'],item['kind'],edge,'wall',item['wall_gaps'][edge],0,item['visible_wall_fraction']])
        for block in item['obstructions']:
            rows.append([item['name'],item['kind'],'near obstacle',block['name'],
                         block['distance'],block['clearance'],item['visible_wall_fraction']])
    for pair in report['pairs']:
        rows.append([pair['first_name'],pair['kind'],'',pair['second_name'],pair['distance'],pair['target'],None])
    return csv_bytes(columns,rows)


def sources_for(scene, shot):
    work = []
    for placement,art in scene.works():
        work.append({'title':art['title'],'catalog':art['inventory'],'placement':placement['id'],
                     'artwork':art['id'],'photo':art['photo']['source_digest'],'crop':art['crop'],
                     'width_mm':art['width'],'height_mm':art['height'],'angle':placement['angle'],
                     'center_mm':[placement['x'],placement['y']]})
    return {'wall_id':scene.wall['id'],'wall_size_mm':[scene.wall['width'],scene.wall['height']],
            'date':scene.project['date'],'shot':None if shot is None else {
                'id':shot['id'],'name':shot['name'],'source_sha256':shot['photo']['source_digest'],
                'corners':shot['corners'],'foreground':shot['foreground'],
            }, 'artworks':work}


def create_snapshot(blobs, project, layout_id, payload):
    scene = Scene.build(project,layout_id)
    name = text(payload.get('name',''), 'Issue name', maximum=100)
    mode = choice(payload.get('kind','installer'),ISSUE_TYPES,'issue type')
    shot = scene.shot(payload.get('shot'))
    images = snapshot_images(blobs,scene,shot,mode)
    reports = snapshot_reports(blobs,scene,shot,mode)
    files = snapshot_files(scene,name,images,reports,mode)
    manifest = {'schema':ARCHIVE_SCHEMA,'name':name,'kind':mode,'project':project['name'],
                'wall':scene.wall['name'],'layout':scene.layout['name'],'revision':project['revision'],
                'scene_sha256':scene.reference_identity(),'counts':copy_counts(scene),
                'files':{key:{'sha256':sha256(value),'bytes':len(value)} for key,value in files.items()}}
    files['manifest.json'] = json_bytes(manifest)
    artifacts = {}
    for filename, data in files.items():
        extension = filename.rsplit('.',1)[-1]
        artifacts[filename] = {'sha256':blobs.put(data),'size':len(data),'mime':ARTIFACT_TYPES[extension]}
    package = archive_bytes(files)
    artifacts['issue.zip'] = {'sha256':blobs.put(package),'size':len(package),'mime':ARTIFACT_TYPES['zip']}
    return {'name':name,'revision':project['revision'],'scene_sha256':scene.reference_identity(),
            'wall_name':scene.wall['name'],'layout_name':scene.layout['name'],'kind':mode,
            'counts':manifest['counts'],'manifest':manifest,'artifacts':artifacts,'geometry_record':frozen_geometry(scene)}


def snapshot_images(blobs,scene,shot,mode):
    images = {'elevation':elevation(blobs,scene,edge=1350)}
    if shot:
        images['montage'] = project_art_onto_shot(blobs,scene,shot,edge=1300)
    if mode=='installer':
        images['mounts'] = elevation(blobs,scene,annotated='mounts',edge=1350)
        images['clearance'] = elevation(blobs,scene,annotated='clearances',edge=1350)
        if scene.layout['labels']:
            images['labels'] = labels_sheet(scene)
    return images


def snapshot_reports(blobs,scene,shot,mode):
    if mode=='proof':
        return {}
    return {'sources':sources_for(scene,shot),'mounts':mounting_report(scene),'clearance':clearance_report(scene),
            'labels':labels_report(blobs,scene,shot),
            'viewing':[viewpoint_report(scene,station) for station in scene.layout['stations']],
            'observations':scene.layout['observations']}


def snapshot_files(scene,name,images,reports,mode):
    files = {key+'.png':png_bytes(image) for key,image in images.items()}
    proof = mode=='proof'
    files['proof.pdf' if proof else 'installation.pdf'] = installation_pdf(
        scene,name,scene.project['revision'],images,reports,proof_only=proof,
    )
    files['handover.html'] = handover_html(scene,name,images,reports,proof)
    if not proof:
        files['fixings.csv'] = csv_bytes(*mounting_csv(reports['mounts']))
        files['clearances.csv'] = clearance_csv(reports['clearance'])
        files['geometry.json'] = json_bytes(reports)
    return files


def archive_bytes(files):
    output = BytesIO()
    with ZipFile(output,'w',compression=ZIP_DEFLATED,compresslevel=5) as archive:
        for filename in sorted(files):
            archive.writestr(filename,files[filename])
    package = output.getvalue()
    if len(package)>MAX_ARCHIVE_BYTES:
        raise InputError('The issue ZIP exceeds the 45 MiB download limit. Reduce the image count.')
    return package


def check_archive_paths(archive):
    entries = archive.infolist()
    if not 1<=len(entries)<=MAX_ARCHIVE_ENTRIES:
        raise InputError('The number of entries is outside the normal GalleryHang issue range.')
    names = [entry.filename for entry in entries]
    if len(set(names))!=len(names):
        raise InputError('The ZIP contains duplicate entry names.')
    if sum(entry.file_size for entry in entries)>MAX_UNCOMPRESSED_BYTES:
        raise InputError('The expanded contents exceed the inspection limit.')
    for entry in entries:
        path = PurePosixPath(entry.filename)
        if (entry.is_dir() or path.name!=entry.filename or '\\' in entry.filename
                or entry.filename.startswith('.') or len(entry.filename)>120 or entry.flag_bits&1):
            raise InputError('The ZIP contains unsupported names or password-protected data.')
    if 'manifest.json' not in names:
        raise InputError('The issue ZIP must contain manifest.json.')
    if archive.getinfo('manifest.json').file_size>150_000:
        raise InputError('The issue manifest is too large to inspect.')
    return set(names)


def read_issue_manifest(archive):
    try:
        info = json.loads(archive.read('manifest.json'))
    except (UnicodeDecodeError,json.JSONDecodeError) as error:
        raise InputError('The issue manifest is not valid UTF-8 JSON.') from error
    if not isinstance(info,dict) or info.get('schema')!=ARCHIVE_SCHEMA or not isinstance(info.get('files'),dict):
        raise InputError('The manifest is not a supported GalleryHang issue.')
    if info.get('kind') not in ISSUE_TYPES or not isinstance(info.get('revision'),int):
        raise InputError('The manifest does not identify a valid issue type and project revision.')
    return info


def inspected_files(archive,manifest,names):
    declared = manifest['files']
    required = {'elevation.png','proof.pdf'} if manifest['kind']=='proof' else {
        'elevation.png','installation.pdf','fixings.csv','geometry.json','mounts.png','clearance.png',
    }
    optional = {'montage.png','handover.html'} if manifest['kind']=='proof' else {'montage.png','labels.png','clearances.csv','handover.html'}
    if not required.issubset(declared) or not set(declared).issubset(required|optional):
        raise InputError('The manifest has a missing or unsupported issue artifact.')
    data = []
    for name,expected in declared.items():
        if not isinstance(expected,dict):
            raise InputError('An artifact declaration is malformed.')
        if name not in names:
            data.append({'name':name,'status':'missing'})
            continue
        raw = archive.read(name)
        digest = sha256(raw)
        match = len(raw)==expected.get('bytes') and digest==expected.get('sha256')
        data.append({'name':name,'status':'match' if match else 'different','sha256':digest,'bytes':len(raw)})
    for name in sorted(names-set(declared)-{'manifest.json'}):
        data.append({'name':name,'status':'undeclared'})
    return data


def inspect_issue(payload):
    if not payload or len(payload)>MAX_ARCHIVE_BYTES:
        raise InputError('Select a nonempty GalleryHang ZIP under 45 MiB.')
    try:
        with ZipFile(BytesIO(payload)) as archive:
            actual_names = check_archive_paths(archive)
            manifest = read_issue_manifest(archive)
            evidence = inspected_files(archive,manifest,actual_names)
    except (BadZipFile,NotImplementedError,RuntimeError,OSError) as error:
        raise InputError('The file could not be read as a GalleryHang issue ZIP.') from error
    result = Counter(row['status'] for row in evidence)
    details = {key:manifest.get(key) for key in ('name','project','wall','layout','revision','kind','scene_sha256')}
    return {**details,'valid':len(evidence)>0 and result.get('match')==len(evidence),
            'counts':dict(result),'files':evidence}
