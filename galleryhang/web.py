from functools import wraps
from io import BytesIO
import json
from pathlib import Path
from threading import BoundedSemaphore

from flask import Blueprint, Flask, Response, jsonify, request, send_file
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from .artworks import artwork_information, create_framed_image, cropped_preview
from .capture_review import (
    add_observation, capture_diagnostics, capture_map, observation_overlay, paint_sample_map,
    paint_samples, remove_observation, ruler_measurement,
)
from .clearance import clearance_report, wall_hint
from .compare import (
    compare_captures, comparison_image, comparison_metrics, difference_picture, planned_vs_actual,
    strip_sheet,
)
from .demo import build_demo, demo_cover
from .evidence import MAX_ARCHIVE_BYTES, create_snapshot, inspect_issue
from .labels import label_image, labels_report
from .layout import (
    Scene, apply_arrangement, compare_layouts, delete_label, delete_layout, delete_placement,
    delete_station, duplicate_layout, edit_layout, edit_placement, make_layout, place_label,
    place_station, place_work,
)
from .model import (
    ConflictError, InputError, MAX_UPLOAD, MissingError, choice, find, integer, mapping,
    new_project, number, pair, parse_revision,
)
from .mounts import mounting_report
from .photos import png_bytes
from .render import elevation, project_art_onto_shot
from .store import Store
from .viewing import rendered_view, viewpoint_report
from .workspace import (
    WALL_COLLECTIONS, add_artwork, add_shot, add_wall, change_artwork, change_project,
    change_shot, change_wall, delete_artwork, delete_shot, delete_wall, delete_wall_annotation,
    duplicate_artwork, edit_wall_annotation, scene_from_shot, set_primary_view,
)


STATIC = Path(__file__).resolve().parent.parent / 'static'


def json_payload():
    try:
        return mapping(request.get_json(), 'Request')
    except BadRequest as error:
        raise InputError('Upload a valid JSON request.') from error


def upload_form(max_bytes, *, metadata=True):
    file = request.files.get('file')
    if file is None or not file.filename:
        raise InputError('Choose a file to upload.')
    raw = file.stream.read(max_bytes+1)
    if len(raw)>max_bytes:
        raise InputError(f'Maximum file size is {max_bytes//1024**2} MiB.')
    if not raw:
        raise InputError('The selected file is empty.')
    if not metadata:
        return raw
    try:
        payload = mapping(json.loads(request.form.get('metadata','{}')), 'Upload details')
    except json.JSONDecodeError as error:
        raise InputError('Enter valid upload details.') from error
    return payload, raw, file.filename


def handles_errors(app):
    @app.errorhandler(InputError)
    @app.errorhandler(BadRequest)
    def bad_input(error):
        return jsonify(error=str(error)),400

    @app.errorhandler(MissingError)
    def missing(error):
        return jsonify(error=str(error)),404

    @app.errorhandler(ConflictError)
    def conflict(error):
        return jsonify(error=str(error)),409

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_):
        return jsonify(error='The request is above the supported upload size.'),413

    @app.after_request
    def response_headers(response):
        response.headers.setdefault('X-Content-Type-Options','nosniff')
        if request.path.startswith('/api/'):
            response.headers.setdefault('Cache-Control','no-store')
        return response


def limit_image_work(function):
    @wraps(function)
    def limited(*args,**kwargs):
        gate = request.environ['galleryhang.image_gate']
        if not gate.acquire(timeout=9.0):
            return jsonify(error='Other image previews are still rendering. Try again in a moment.'),429
        try:
            return function(*args,**kwargs)
        finally:
            gate.release()
    return limited


def image_response(image, *, filename=None):
    data = png_bytes(image)
    return send_file(BytesIO(data),mimetype='image/png',as_attachment=filename is not None,download_name=filename)


def query_choice(key,options,default):
    return choice(request.args.get(key,default),options,key)


def query_number(key,default,lower,upper):
    value = request.args.get(key,type=float)
    if value is None:
        if key in request.args:
            raise InputError(f'{key.capitalize()} must be a number.')
        value = default
    return number(value,key.capitalize(),lower=lower,upper=upper)


def edge_size(default=1350):
    return round(query_number('edge',default,360,1800))


def respond_change(result,status=200):
    project,item = result[0],result[1]
    extra = result[2] if len(result) > 2 else None
    payload = {'project':project,'item':item}
    if extra is not None:
        payload['follow'] = extra
    return jsonify(payload),status


def mutate(store,project_id,operation,label,*args,created=False):
    payload = json_payload()
    result = store.update(project_id,parse_revision(payload),lambda project:operation(project,*args,payload),label)
    return respond_change(result,201 if created else 200)


def erase(store,project_id,operation,label,*args):
    payload = json_payload()
    result = store.update(project_id,parse_revision(payload),lambda project:operation(project,*args),label)
    return respond_change(result)


def register_projects(api, store):
    @api.get('/health')
    def health():
        return jsonify(ok=True,service='GalleryHang',version=1)

    @api.get('/projects')
    def projects():
        return jsonify(projects=store.all())

    @api.post('/projects')
    def create_project():
        project = store.create(new_project(json_payload()))
        return jsonify(project=project),201

    @api.get('/projects/<project_id>')
    def project(project_id):
        return jsonify(project=store.get(project_id))

    @api.patch('/projects/<project_id>')
    def update_project(project_id):
        return mutate(store,project_id,change_project,'settings')

    @api.get('/projects/<project_id>/activity')
    def history(project_id):
        return jsonify(events=store.activity(project_id))

    @api.post('/demo')
    @limit_image_work
    def demo():
        project,added = store.import_demo('north-window-study-v1',lambda:build_demo(store.blobs))
        return jsonify(project=project),201 if added else 200

    @api.get('/demo/cover.png')
    @limit_image_work
    def cover():
        image = demo_cover()
        image.thumbnail((720,720))
        return image_response(image)

    @api.get('/photo/<digest>.png')
    def photo_blob(digest):
        return send_file(store.blobs.require(digest),mimetype='image/png',conditional=True,etag=digest,max_age=86400)


def register_walls(api, store):
    @api.post('/projects/<project_id>/walls')
    def create_wall(project_id):
        return mutate(store,project_id,add_wall,'wall',created=True)

    @api.route('/projects/<project_id>/walls/<wall_id>',methods=['PATCH','DELETE'])
    def wall_details(project_id,wall_id):
        if request.method=='DELETE':
            return erase(store,project_id,delete_wall,'wall',wall_id)
        return mutate(store,project_id,change_wall,'wall',wall_id)

    @api.post('/projects/<project_id>/walls/<wall_id>/shots')
    @limit_image_work
    def upload_shot(project_id,wall_id):
        payload,data,filename = upload_form(MAX_UPLOAD)
        return respond_change(add_shot(store,project_id,wall_id,payload,data,filename),201)

    @api.route('/projects/<project_id>/walls/<wall_id>/shots/<shot_id>',methods=['PATCH','DELETE'])
    def shot_details(project_id,wall_id,shot_id):
        if request.method=='DELETE':
            return erase(store,project_id,delete_shot,'photo',wall_id,shot_id)
        return mutate(store,project_id,change_shot,'photo',wall_id,shot_id)

    @api.post('/projects/<project_id>/walls/<wall_id>/shots/<shot_id>/primary')
    def primary_shot(project_id,wall_id,shot_id):
        payload = json_payload()
        result = store.update(project_id,parse_revision(payload),lambda project:set_primary_view(project,wall_id,shot_id),'photo')
        return respond_change(result)

    @api.post('/projects/<project_id>/walls/<wall_id>/<kind>')
    @api.route('/projects/<project_id>/walls/<wall_id>/<kind>/<annotation_id>',methods=['PATCH','DELETE'])
    def wall_annotations(project_id,wall_id,kind,annotation_id=None):
        if kind not in WALL_COLLECTIONS:
            raise MissingError('Wall annotation type was not found.')
        if request.method=='DELETE':
            return erase(store,project_id,delete_wall_annotation,kind,wall_id,kind,annotation_id)
        payload = json_payload()
        result = store.update(project_id,parse_revision(payload),
                              lambda project:edit_wall_annotation(project,wall_id,kind,payload,annotation_id),kind)
        return respond_change(result,201 if request.method=='POST' else 200)

    @api.get('/projects/<project_id>/walls/<wall_id>/shots/<shot_id>/original')
    def original_shot(project_id,wall_id,shot_id):
        _,shot = wall_shot(store,project_id,wall_id,shot_id)
        return original_file(store,shot['photo'])

    @api.get('/projects/<project_id>/walls/<wall_id>/shots/<shot_id>/review')
    @limit_image_work
    def capture_review(project_id,wall_id,shot_id):
        wall,shot = wall_shot(store,project_id,wall_id,shot_id)
        return jsonify(capture_diagnostics(store.blobs,wall,shot))

    @api.get('/projects/<project_id>/walls/<wall_id>/shots/<shot_id>/preview.png')
    @limit_image_work
    def capture_preview(project_id,wall_id,shot_id):
        wall,shot = wall_shot(store,project_id,wall_id,shot_id)
        mode = query_choice('kind',('coverage','detail','exposure','samples'),'coverage')
        if mode=='samples':
            return image_response(paint_sample_map(store.blobs,wall,shot))
        return image_response(capture_map(store.blobs,wall,shot,mode))

    @api.post('/projects/<project_id>/walls/<wall_id>/shots/<shot_id>/ruler')
    def measure_shot(project_id,wall_id,shot_id):
        wall,shot = wall_shot(store,project_id,wall_id,shot_id)
        payload = json_payload()
        start = pair(payload.get('start'),'Starting point',lower=0,upper=1)
        end = pair(payload.get('end'),'End point',lower=0,upper=1)
        return jsonify(ruler_measurement(wall,shot,start,end))

    @api.get('/projects/<project_id>/walls/<wall_id>/shots/<shot_id>/samples')
    @limit_image_work
    def sample_analysis(project_id,wall_id,shot_id):
        wall,shot = wall_shot(store,project_id,wall_id,shot_id)
        return jsonify(paint_samples(store.blobs,wall,shot))


def register_artworks(api, store):
    @api.post('/projects/<project_id>/artworks')
    @limit_image_work
    def upload_artwork(project_id):
        payload,data,filename = upload_form(MAX_UPLOAD)
        return respond_change(add_artwork(store,project_id,payload,data,filename),201)

    @api.route('/projects/<project_id>/artworks/<artwork_id>',methods=['PATCH','DELETE'])
    def artwork_details(project_id,artwork_id):
        if request.method=='DELETE':
            return erase(store,project_id,delete_artwork,'artwork',artwork_id)
        return mutate(store,project_id,change_artwork,'artwork',artwork_id)

    @api.post('/projects/<project_id>/artworks/<artwork_id>/duplicate')
    def artwork_copy(project_id,artwork_id):
        return mutate(store,project_id,duplicate_artwork,'artwork',artwork_id,created=True)

    @api.get('/projects/<project_id>/artworks/<artwork_id>/info')
    def artwork_info(project_id,artwork_id):
        artwork = find(store.get(project_id)['artworks'],artwork_id,'Artwork')
        return jsonify(artwork_information(artwork))

    @api.get('/projects/<project_id>/artworks/<artwork_id>/original')
    def artwork_source(project_id,artwork_id):
        artwork = find(store.get(project_id)['artworks'],artwork_id,'Artwork')
        return original_file(store,artwork['photo'])

    @api.get('/projects/<project_id>/artworks/<artwork_id>/print.png')
    @limit_image_work
    def artwork_thumbnail(project_id,artwork_id):
        artwork = find(store.get(project_id)['artworks'],artwork_id,'Artwork')
        kind = query_choice('kind',('crop','frame'),'frame')
        edge = edge_size(960)
        image = (cropped_preview(store.blobs,artwork,edge) if kind=='crop'
                 else create_framed_image(store.blobs,artwork,edge))
        filename = 'artwork-'+kind+'.png' if request.args.get('download')=='1' else None
        return image_response(image,filename=filename)


def register_layout_state(api,store):
    @api.post('/projects/<project_id>/layouts')
    def create_layout(project_id):
        return mutate(store,project_id,make_layout,'layout',created=True)

    @api.route('/projects/<project_id>/layouts/<layout_id>',methods=['PATCH','DELETE'])
    def edit_layout_route(project_id,layout_id):
        if request.method=='DELETE':
            return erase(store,project_id,delete_layout,'layout',layout_id)
        return mutate(store,project_id,edit_layout,'layout',layout_id)

    @api.post('/projects/<project_id>/layouts/<layout_id>/duplicate')
    def copy_layout_route(project_id,layout_id):
        return mutate(store,project_id,duplicate_layout,'layout',layout_id,created=True)

    @api.post('/projects/<project_id>/layouts/<layout_id>/placements')
    def add_placement_route(project_id,layout_id):
        return mutate(store,project_id,place_work,'placement',layout_id,created=True)

    @api.route('/projects/<project_id>/layouts/<layout_id>/placements/<placement_id>',methods=['DELETE','PATCH'])
    def edit_placement_route(project_id,layout_id,placement_id):
        if request.method=='DELETE':
            return erase(store,project_id,delete_placement,'placement',layout_id,placement_id)
        payload = json_payload()
        captured = {}
        def apply(project):
            item, message, follow = edit_placement(project,layout_id,placement_id,payload)
            captured['follow'] = follow
            return item, message
        result = store.update(project_id,parse_revision(payload),apply,'placement')
        return jsonify(project=result[0],item=result[1],follow=captured['follow']),200

    @api.post('/projects/<project_id>/layouts/<layout_id>/arrange')
    def arrange_route(project_id,layout_id):
        payload = json_payload()
        captured = {}
        def apply(project):
            layout_row, message, follow = apply_arrangement(project,layout_id,payload)
            captured['follow'] = follow
            return layout_row, message
        result = store.update(project_id,parse_revision(payload),apply,'arrangement')
        return jsonify(project=result[0],item=result[1],follow=captured['follow']),200

    @api.post('/projects/<project_id>/layouts/<layout_id>/labels')
    @api.route('/projects/<project_id>/layouts/<layout_id>/labels/<label_id>',methods=['DELETE','PATCH'])
    def layout_labels(project_id,layout_id,label_id=None):
        if request.method=='DELETE':
            return erase(store,project_id,delete_label,'label',layout_id,label_id)
        payload = json_payload()
        result = store.update(project_id,parse_revision(payload),
                              lambda project:place_label(project,layout_id,payload,label_id),'label')
        return respond_change(result,201 if request.method=='POST' else 200)

    @api.post('/projects/<project_id>/layouts/<layout_id>/stations')
    @api.route('/projects/<project_id>/layouts/<layout_id>/stations/<station_id>',methods=['DELETE','PATCH'])
    def layout_stations(project_id,layout_id,station_id=None):
        if request.method=='DELETE':
            return erase(store,project_id,delete_station,'viewpoint',layout_id,station_id)
        payload = json_payload()
        result = store.update(project_id,parse_revision(payload),
                              lambda project:place_station(project,layout_id,payload,station_id),'viewpoint')
        return respond_change(result,201 if request.method=='POST' else 200)

    @api.post('/projects/<project_id>/layouts/<layout_id>/observations')
    def observations(project_id,layout_id):
        return mutate(store,project_id,add_observation,'observation',layout_id,created=True)

    @api.delete('/projects/<project_id>/layouts/<layout_id>/observations/<observation_id>')
    def delete_observation_route(project_id,layout_id,observation_id):
        return erase(store,project_id,remove_observation,'observation',layout_id,observation_id)


def register_layout_preview(api,store):
    @api.get('/projects/<project_id>/layouts/<layout_id>/render.png')
    @limit_image_work
    def scene_render(project_id,layout_id):
        scene = Scene.build(store.get(project_id),layout_id)
        mode = query_choice('view',('elevation','photo','registered','mounts','clearances','rulers','observations'),'elevation')
        edge = edge_size()
        shot = scene.shot(request.args.get('shot'))
        if mode in ('photo','registered','observations') and not shot:
            raise InputError('Add a site photograph to render this view.')
        if mode=='photo':
            result = project_art_onto_shot(store.blobs,scene,shot,edge=edge)
        elif mode=='observations':
            result = observation_overlay(store.blobs,scene,shot,edge=edge)
        else:
            annotation = mode if mode in ('mounts','clearances','rulers') else None
            result = elevation(store.blobs,scene,shot=shot if mode=='registered' else None,annotated=annotation,edge=edge)
        filename = 'galleryhang-'+mode+'.png' if request.args.get('download')=='1' else None
        return image_response(result,filename=filename)

    @api.get('/projects/<project_id>/layouts/<layout_id>/audit')
    @limit_image_work
    def audit(project_id,layout_id):
        scene = Scene.build(store.get(project_id),layout_id)
        kind = query_choice('kind',('clearances','mounts','labels'),'clearances')
        if kind=='clearances':
            return jsonify(clearance_report(scene))
        if kind=='mounts':
            return jsonify(mounting_report(scene,query_number('tolerance',18,0,200)))
        return jsonify(labels_report(store.blobs,scene,scene.shot(request.args.get('shot'))))

    @api.post('/projects/<project_id>/layouts/<layout_id>/measure')
    def wall_measure_route(project_id,layout_id):
        scene = Scene.build(store.get(project_id),layout_id)
        data = json_payload()
        size = max(scene.wall['width'],scene.wall['height'])
        a = pair(data.get('start'),'Starting point',lower=0,upper=size)
        b = pair(data.get('end'),'End point',lower=0,upper=size)
        return jsonify(wall_hint(scene,a,b))

    @api.get('/projects/<project_id>/layouts/<layout_id>/compare/<other_id>')
    def diff_layout_route(project_id,layout_id,other_id):
        return jsonify(compare_layouts(store.get(project_id),layout_id,other_id))

    @api.get('/projects/<project_id>/layouts/<layout_id>/stations/<station_id>/render.png')
    @limit_image_work
    def station_render(project_id,layout_id,station_id):
        scene = Scene.build(store.get(project_id),layout_id)
        station = find(scene.layout['stations'],station_id,'Viewing position')
        return image_response(rendered_view(store.blobs,scene,station,edge=edge_size(1056),annotated=True))

    @api.get('/projects/<project_id>/layouts/<layout_id>/stations/<station_id>/readout')
    def station_readout(project_id,layout_id,station_id):
        scene = Scene.build(store.get(project_id),layout_id)
        station = find(scene.layout['stations'],station_id,'Viewing position')
        return jsonify(viewpoint_report(scene,station))

    @api.get('/projects/<project_id>/layouts/<layout_id>/labels/<label_id>/preview.png')
    def label_render(project_id,layout_id,label_id):
        scene = Scene.build(store.get(project_id),layout_id)
        label = find(scene.layout['labels'],label_id,'Wall label')
        image,_ = label_image(scene,label,(max(12,round(label['width']*3)),max(12,round(label['height']*3))))
        return image_response(image)


def register_compare(api,store):
    @api.get('/projects/<project_id>/walls/<wall_id>/compare')
    @api.get('/projects/<project_id>/walls/<wall_id>/compare.png')
    @limit_image_work
    def compare_photos(project_id,wall_id):
        project = store.get(project_id)
        wall = find(project['walls'],wall_id,'Wall')
        a = find(wall['shots'],request.args.get('first'),'First site photograph')
        b = find(wall['shots'],request.args.get('second'),'Second site photograph')
        result = compare_captures(store.blobs,wall,a,b,edge=edge_size(1020),normalize=request.args.get('normalize')!='0')
        threshold = query_number('threshold',0.075,0.01,0.8)
        if request.path.endswith('.png'):
            mode = query_choice('mode',('split','difference','side'),'split')
            fraction = query_number('split',0.5,0,1)
            return image_response(comparison_image(result,mode,threshold,fraction))
        return jsonify(comparison_metrics(result,threshold))

    @api.get('/projects/<project_id>/layouts/<layout_id>/check')
    @api.get('/projects/<project_id>/layouts/<layout_id>/check.png')
    @limit_image_work
    def compare_installation(project_id,layout_id):
        project = store.get(project_id)
        scene,shot = scene_from_shot(project,layout_id,request.args.get('shot'))
        reference = scene.shot(request.args.get('base'))
        if reference['id']==shot['id']:
            raise InputError('Choose separate photographs for the plan reference and installation check.')
        facts,expected,actual,delta,common = planned_vs_actual(store.blobs,scene,shot,reference,
                                                             edge=edge_size(1150))
        if request.path.endswith('.png'):
            kind = query_choice('mode',('difference','side'),'side')
            if kind=='difference':
                return image_response(difference_picture(delta,common,query_number('threshold',0.075,0.01,0.8)))
            return image_response(strip_sheet(expected.image(),actual.image(),'Planned arrangement',shot['name']))
        return jsonify(facts)


def register_issues(api,store):
    @api.get('/projects/<project_id>/issues')
    def issues(project_id):
        return jsonify(issues=store.snapshots(project_id))

    @api.post('/projects/<project_id>/layouts/<layout_id>/issue')
    @limit_image_work
    def post_issue(project_id,layout_id):
        payload = json_payload()
        item = store.save_snapshot(project_id,layout_id,parse_revision(payload),
                                   lambda project,layout:create_snapshot(store.blobs,project,layout,payload))
        return jsonify(issue=item),201

    @api.get('/projects/<project_id>/issues/<snapshot_id>/<artifact>')
    def download_issue(project_id,snapshot_id,artifact):
        item = store.snapshot(project_id,snapshot_id)
        source = item['artifacts'].get(artifact)
        if source is None:
            raise MissingError('Issue artifact was not found.')
        return send_file(store.blobs.require(source['sha256']),mimetype=source['mime'],as_attachment=True,
                         download_name=artifact,etag=source['sha256'])

    @api.post('/issues/verify')
    def verify_issue():
        return jsonify(inspect_issue(upload_form(MAX_ARCHIVE_BYTES,metadata=False)))


def wall_shot(store,project_id,wall_id,shot_id):
    wall = find(store.get(project_id)['walls'],wall_id,'Wall')
    return wall,find(wall['shots'],shot_id,'Site photograph')


def original_file(store,photo):
    return send_file(store.blobs.require(photo['source_digest']),mimetype=photo['original_mime'],
                     as_attachment=True,download_name=photo['name'],etag=photo['source_digest'])


def create_app(data_dir):
    store = Store(data_dir)
    app = Flask(__name__, static_folder=str(STATIC),static_url_path='/static')
    app.config.update(MAX_CONTENT_LENGTH=64*1024**2,MAX_FORM_MEMORY_SIZE=180_000,MAX_FORM_PARTS=8)
    handles_errors(app)
    image_gate = BoundedSemaphore(2)

    @app.before_request
    def supply_gate():
        request.environ['galleryhang.image_gate'] = image_gate

    @app.get('/')
    def home():
        return app.send_static_file('index.html')

    api = Blueprint('galleryhang_api',__name__,url_prefix='/api')
    register_projects(api,store)
    register_walls(api,store)
    register_artworks(api,store)
    register_layout_state(api,store)
    register_layout_preview(api,store)
    register_compare(api,store)
    register_issues(api,store)
    from .extra_web import register_additional
    register_additional(api,store)
    app.register_blueprint(api)
    app.extensions['galleryhang_store'] = store
    return app
