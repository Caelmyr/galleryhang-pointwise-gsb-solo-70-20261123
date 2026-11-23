from flask import jsonify, request, send_file
from io import BytesIO

from .capture_review import observation_report
from .catalog import catalog_index, contact_sheet, palette_card, palette_sheet
from .distribution import distribution_report, elevate_distribution
from .layout import Scene
from .label_print import PAPER_FORMATS, print_plan, print_wall_labels
from .lightmap import illumination_picture, lighting_metrics, line_probe, registered_illumination
from .model import InputError, find, pair, parse_revision
from .revisions import compare_issues
from .print_target import print_mount_card
from .survey import delete_survey, save_survey, survey_picture, survey_report
from .tour import draw_tour, edit_route, remove_route, tour_report


def register_additional(api,store):
    from .web import (erase, image_response, json_payload, limit_image_work, mutate,
                      query_choice, query_number, respond_change, wall_shot)

    def scene(project_id,layout_id):
        return Scene.build(store.get(project_id),layout_id)

    @api.get('/projects/<project_id>/layouts/<layout_id>/observations')
    def layout_observations(project_id,layout_id):
        current = scene(project_id,layout_id)
        reference = current.shot(request.args.get('shot'))
        if not reference:
            raise InputError('Import a site photograph to view observations.')
        return jsonify(observation_report(current,reference))

    @api.get('/projects/<project_id>/layouts/<layout_id>/balance')
    def balance(project_id,layout_id):
        return jsonify(distribution_report(scene(project_id,layout_id)))

    @api.get('/projects/<project_id>/layouts/<layout_id>/balance.png')
    @limit_image_work
    def balance_chart(project_id,layout_id):
        return image_response(elevate_distribution(store.blobs,scene(project_id,layout_id)))

    @api.get('/projects/<project_id>/layouts/<layout_id>/surveys')
    def saved_surveys(project_id,layout_id):
        return jsonify(survey_report(scene(project_id,layout_id)))

    @api.post('/projects/<project_id>/layouts/<layout_id>/surveys')
    def post_survey(project_id,layout_id):
        return mutate(store,project_id,save_survey,'survey',layout_id,created=True)

    @api.delete('/projects/<project_id>/layouts/<layout_id>/surveys/<survey_id>')
    def remove_survey(project_id,layout_id,survey_id):
        return erase(store,project_id,delete_survey,'survey',layout_id,survey_id)

    @api.get('/projects/<project_id>/layouts/<layout_id>/surveys/<survey_id>/preview.png')
    @limit_image_work
    def preview_survey(project_id,layout_id,survey_id):
        current = scene(project_id,layout_id)
        record = find(current.layout.get('installed_surveys',[]),survey_id,'Installed outline')
        return image_response(survey_picture(store.blobs,current,record,request.args.get('reading')))

    @api.post('/projects/<project_id>/layouts/<layout_id>/routes')
    @api.route('/projects/<project_id>/layouts/<layout_id>/routes/<route_id>',methods=['PATCH','DELETE'])
    def route_edit(project_id,layout_id,route_id=None):
        payload = json_payload()
        revision = parse_revision(payload)
        if request.method=='DELETE':
            def apply(project):
                return remove_route(Scene.build(project,layout_id),route_id)
        else:
            def apply(project):
                return edit_route(Scene.build(project,layout_id),payload,route_id)
        result = store.update(project_id,revision,apply,'visit sequence')
        return respond_change(result,201 if request.method=='POST' else 200)

    @api.get('/projects/<project_id>/layouts/<layout_id>/routes/<route_id>/plan.png')
    def route_map(project_id,layout_id,route_id):
        current = scene(project_id,layout_id)
        selected = find(current.layout.get('visit_sequences',[]),route_id,'Visit sequence')
        return image_response(draw_tour(current,selected))

    @api.get('/projects/<project_id>/layouts/<layout_id>/routes/<route_id>/report')
    def route_report(project_id,layout_id,route_id):
        current = scene(project_id,layout_id)
        selected = find(current.layout.get('visit_sequences',[]),route_id,'Visit sequence')
        pace = query_number('pace',1100,300,3000)
        return jsonify(tour_report(current,selected,pace))

    @api.get('/projects/<project_id>/artworks/<artwork_id>/measuring-card.pdf')
    @limit_image_work
    def measuring_card(project_id,artwork_id):
        artwork = find(store.get(project_id)['artworks'],artwork_id,'Artwork')
        return send_file(BytesIO(print_mount_card(artwork)),mimetype='application/pdf',as_attachment=True,
                         download_name='frame-measuring-card.pdf')

    @api.get('/projects/<project_id>/layouts/<layout_id>/label-print')
    @api.get('/projects/<project_id>/layouts/<layout_id>/label-print.pdf')
    @limit_image_work
    def label_print_export(project_id,layout_id):
        paper = query_choice('paper',PAPER_FORMATS,'a4')
        current = scene(project_id,layout_id)
        if request.path.endswith('.pdf'):
            payload = print_wall_labels(current,paper)
            return send_file(BytesIO(payload),mimetype='application/pdf',as_attachment=True,
                             download_name=f'wall-labels-{paper}.pdf')
        return jsonify(print_plan(current,paper))

    @api.get('/projects/<project_id>/artworks/<artwork_id>/palette')
    @limit_image_work
    def artwork_palette(project_id,artwork_id):
        artworks = store.get(project_id)['artworks']
        return jsonify(palette_card(store.blobs,find(artworks,artwork_id,'Artwork')))

    @api.post('/projects/<project_id>/catalog/palette')
    @limit_image_work
    def read_palette(project_id):
        data = json_payload()
        return jsonify(palette_sheet(store.blobs,store.get(project_id),data.get('artworks',[])))

    @api.get('/projects/<project_id>/catalog/contact.png')
    @api.get('/projects/<project_id>/catalog/contact')
    @limit_image_work
    def artwork_contact_sheet(project_id):
        selected = list(filter(None,request.args.get('artworks','').split(',')))
        order = query_choice('order',('title','artist','inventory','wall order'),'title')
        layout_id = request.args.get('layout')
        project = store.get(project_id)
        if request.path.endswith('.png'):
            result = contact_sheet(store.blobs,project,selected,order,layout_id)
            return image_response(result,filename='artwork-contact-sheet.png' if request.args.get('download')=='1' else None)
        return jsonify(artworks=catalog_index(project,selected,order,layout_id))

    @api.get('/projects/<project_id>/issues/<first_id>/compare/<second_id>')
    def issue_difference(project_id,first_id,second_id):
        first = store.snapshot(project_id,first_id)
        second = store.snapshot(project_id,second_id)
        return jsonify(compare_issues(first,second))

    @api.get('/projects/<project_id>/walls/<wall_id>/shots/<shot_id>/lighting')
    @api.get('/projects/<project_id>/walls/<wall_id>/shots/<shot_id>/lighting.png')
    @api.post('/projects/<project_id>/walls/<wall_id>/shots/<shot_id>/lighting/profile')
    @limit_image_work
    def change_map(project_id,wall_id,shot_id):
        wall,photo = wall_shot(store,project_id,wall_id,shot_id)
        threshold = query_number('threshold',0.17,0.04,2)
        radius = query_number('spread',65,8,400)
        ref_id = request.args.get('reference')
        reference = find(wall['swatches'],ref_id,'Paint sample') if ref_id else None
        if reference and reference['shot']!=photo['id']:
            raise InputError('Select a paint sample on the current photograph.')
        result = registered_illumination(store.blobs,wall,photo,radius,reference)
        if request.method=='POST':
            points = json_payload()
            a = pair(points.get('start'),'First light probe point',lower=0,upper=1)
            b = pair(points.get('end'),'Last light probe point',lower=0,upper=1)
            return jsonify(line_probe(result,a,b))
        if request.path.endswith('.png'):
            return image_response(illumination_picture(result,photo['name'],threshold))
        return jsonify(lighting_metrics(result,threshold))
