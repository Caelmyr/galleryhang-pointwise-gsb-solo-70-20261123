import {state,e,photo,purl,lurl,wurl,surl,layout,wall,shot,art,placed,qty,opts,btn,link,tag,table,
        field,numeric,change,request,applyProject,openDialog,collectImagePoints,notify} from './ui.js';

const filetypes = 'image/png,image/jpeg,image/webp,image/tiff,.tiff,.tif';
const sourceField = label => field('file',label,'',{type:'file',required:true,wide:true,attrs:{accept:filetypes}});
const colors = [['none','No frame'],['ink','Ink black'],['oak','Natural oak'],['white','Chalk white'],['silver','Silver'],['walnut','Walnut']];
const attachmentOptions = [['center','One center point'],['pair','Two frame fixings'],['rail','Rail endpoints'],['none','No measured fixing']];
const shotTypes = [['reference','Reference'],['alternate','Alternate viewpoint'],['evening','Different light'],['inspection','Installation check']];
const plain = (name,label,value,max=120) => field(name,label,value,{attrs:{maxlength:max}});
const required = (name,label,value,max=120) => field(name,label,value,{required:true,attrs:{maxlength:max}});
const bigText = (name,label,value,max=700) => field(name,label,value,{type:'textarea',wide:true,attrs:{maxlength:max}});
const choose = (name,label,value,options) => field(name,label,value,{type:'select',options});
const colorField = (name,label,value) => field(name,label,value,{type:'color'});
const check = (name,label,value) => field(name,label,value,{type:'checkbox'});

function deleteFooter(action,id,label='Remove') {
  return `<hr class="dialog-separator"><div class="remove-row">${btn(label,action,{id},'danger')}</div>`;
}

export function exhibitionDialog(existing,after) {
  const content=[required('name','Exhibition name',existing?.name),plain('venue','Venue',existing?.venue),
                 plain('date','Installation date',existing?.date,32),bigText('note','Notes',existing?.note,1400)];
  openDialog(existing?'Exhibition details':'New exhibition',content.join(''),async fields=>{
    if (existing) await change(purl(''),fields,{method:'PATCH'});
    else {
      const {project}=await request('/api/projects',{method:'POST',body:fields});
      state.layoutId=state.wallId=state.shotId=null;
      applyProject(project);
      state.page='plan';
    }
    await after();
  },{confirm:existing?'Save details':'Create exhibition'});
}

export function wallDialog(existing,after) {
  const fields=[required('name','Wall name',existing?.name || ''),numeric('width','Width in mm',existing?.width || 5400,{min:250,max:30000}),
                numeric('height','Height in mm',existing?.height || 3050,{min:250,max:12000}),colorField('paint','Wall paint',existing?.paint || '#ddd3bd'),
                numeric('baseboard','Baseboard height in mm',existing?.baseboard || 80,{min:0,max:500}),
                numeric('datum-x','Reference from left, mm',existing?.datum[0] || 0,{min:0}),
                numeric('datum-y','Reference above floor, mm',existing?.datum[1] || 0,{min:0}),bigText('note','Measurement notes',existing?.note,600)];
  openDialog(existing?'Measured wall details':'Add measured wall',fields.join(''),async values=>{
    values.datum=[values['datum-x'],values['datum-y']];
    await change(existing?wurl(''):purl('/walls'),values,{method:existing?'PATCH':'POST'});
    if (!existing) {
      const latest=state.project.walls.at(-1);
      const active=state.project.layouts.find(row=>row.wall===latest.id);
      applyProject(state.project,{wallId:latest.id,layoutId:active.id});
    }
    state.page='plan';
    await after();
  },{confirm:existing?'Save wall':'Add wall',footer:existing?deleteFooter('delete-wall',existing.id,'Remove wall'):''});
}

export function layoutDialog(existing,after) {
  const currentWall=wall();
  const values=[required('name','Layout name',existing?.name||''),
                choose('wall','Measured wall',existing?.wall || currentWall?.id,state.project.walls.map(w=>[w.id,w.name])),
                numeric('gap','Minimum artwork gap, mm',existing?.gap ?? 80,{min:0,max:1500}),
                numeric('eye_level','Eye line, mm above floor',existing?.eye_level ?? 1500,{min:500,max:2500}),
                numeric('label_gap','Label clearance, mm',existing?.label_gap ?? 45,{min:0,max:300}),
                numeric('shadow','Projected frame shadow (0 to 1)',existing?.shadow ?? 0.35,{min:0,max:1,step:0.05}),
                numeric('exposure','Artwork preview exposure, stops',existing?.exposure ?? 0,{min:-2,max:2,step:0.1}),
                bigText('note','Layout notes',existing?.note,700)];
  openDialog(existing?'Edit layout':'New layout',values.join(''),async payload=>{
    await change(existing?lurl(''):purl('/layouts'),payload,{method:existing?'PATCH':'POST',select:item=>({layoutId:item.id})});
    await after();
  },{confirm:existing?'Save layout':'Create layout'});
}

export function layoutOptions(after) {
  const active=layout();
  const duplicates=state.project.layouts.filter(candidate=>candidate.id!==active.id && candidate.wall===active.wall);
  const compare=duplicates.length?`<p>Compare with an alternate arrangement</p><div class="toolline">${duplicates.map(candidate=>btn(candidate.name,'compare-layout',{id:candidate.id})).join('')}</div>`:'';
  openDialog('Layout options',`<div class="toolline">${btn('Duplicate layout','duplicate-layout')}${btn('New layout','new-layout')}${btn('Remove layout','delete-layout',{id:active.id},'danger')}</div>${compare}`,null,{simple:true});
}

export function duplicateLayout(after) {
  openDialog('Duplicate layout',required('name','New layout name',layout().name+' · alternate'),async fields=>{
    await change(lurl('/duplicate'),fields,{select:item=>({layoutId:item.id})});
    await after();
  },{confirm:'Duplicate'});
}

export async function layoutComparison(id) {
  const report=await request(lurl(`/compare/${id}`));
  const cells=report.changes.map(row=>[row.title,row.status,row.dx==null?'—':`${qty(row.dx)}, ${qty(row.dy)} mm`,row.rotation_change==null?'—':qty(row.rotation_change,'°')]);
  openDialog('Layout comparison',`<p>${e(report.first)} → ${e(report.second)}</p>${table(['Artwork','Change','Center shift','Rotation'],cells,'No artworks to compare.')}`,null,{simple:true,wide:true});
}

export function artDialog(existing,after) {
  const width=existing?.width ?? 600,height=existing?.height ?? 800;
  const body=[required('title','Artwork title',existing?.title),plain('artist','Artist',existing?.artist),
              plain('inventory','Inventory number',existing?.inventory,48),plain('year','Date of work',existing?.year,48),
              plain('medium','Medium',existing?.medium),numeric('weight','Object weight, kg',existing?.weight ?? 1,{min:0,max:250}),
              numeric('width','Outside width, mm',width,{min:30,max:9000}),numeric('height','Outside height, mm',height,{min:30,max:9000}),
              choose('frame','Frame finish',existing?.frame || 'oak',colors),numeric('border','Frame border, mm',existing?.border ?? 18,{min:0,max:120}),
              numeric('mat','Mat border, mm',existing?.mat ?? 22,{min:0,max:220}),colorField('mat_color','Mat color',existing?.mat_color || '#f6f1e8'),
              choose('attachment','Back attachment',existing?.attachment || 'pair',attachmentOptions),
              numeric('depth','Frame depth, mm',existing?.depth ?? 28,{min:0,max:500}),
              numeric('fixing_top','Fixing distance from top, mm',existing?.fixing_top ?? 55,{min:0,max:4000}),
              numeric('fixing_span','Fixing separation, fraction of width',existing?.fixing_span ?? 0.55,{min:0.04,max:0.98,step:0.01}),
              bigText('description','Caption',existing?.description,600)];
  if (!existing) body.unshift(sourceField('Artwork photograph'));
  const footer=existing?`<hr class="dialog-separator"><div class="toolline">${btn('Edit image crop','crop',{id:existing.id})}${btn('Copy artwork','duplicate-art',{id:existing.id})}${link('Original photograph',purl(`/artworks/${existing.id}/original`))}${btn('Remove artwork','delete-art',{id:existing.id},'danger')}</div>`:'';
  openDialog(existing?'Edit artwork':'Add artwork',body.join(''),async values=>{
    const file=values.file;
    delete values.file;
    await change(purl(existing?`/artworks/${existing.id}`:'/artworks'),values,{method:existing?'PATCH':'POST',file});
    await after();
  },{confirm:existing?'Save artwork':'Register artwork',footer});
}

export function duplicateArtwork(id,after) {
  const artwork=art(id);
  openDialog('Copy artwork',required('title','Title for copy',artwork.title+' · copy')+plain('inventory','Inventory number','',48),async fields=>{
    await change(purl(`/artworks/${id}/duplicate`),fields);
    await after();
  },{confirm:'Copy artwork'});
}

export function pickArtwork() {
  const rows=state.project.artworks;
  openDialog('Place artwork',rows.length?`<div class="place-list">${rows.map(item=>`<button class="place" type="button" data-do="place-work" data-id="${e(item.id)}"><b>${e(item.title)}</b><span>${item.width} × ${item.height} mm</span></button>`).join('')}</div>`:btn('Add artwork','add-art'),null,{simple:true});
}

export function placementDialog(id,existing,after) {
  const work=art(id),active=layout(),surface=wall();
  const values=[numeric('x','Artwork center from left, mm',existing?.x ?? surface.width/2),
    numeric('y','Artwork center above floor, mm',existing?.y ?? Math.min(active.eye_level,surface.height/2)),
    numeric('angle','Rotation in degrees',existing?.angle ?? 0,{min:-180,max:180}),
    numeric('opacity','Site preview opacity (0 to 1)',existing?.opacity ?? 1,{min:0,max:1,step:0.05}),
    check('pinned','Pin artwork in place',existing?.pinned ?? false),check('visible','Show in plan and issue',existing?.visible ?? true)];
  const footer=existing&&!existing.pinned?deleteFooter('remove-placement',existing.id,'Remove placement'):'';
  openDialog(existing?`Position · ${work.title}`:`Place ${work.title}`,values.join(''),async fields=>{
    const item=await change(lurl(existing?`/placements/${existing.id}`:'/placements'),{...fields,artwork:id},{method:existing?'PATCH':'POST'});
    state.selections=[item.id];
    await after();
  },{confirm:existing?'Save position':'Place artwork',footer});
}

export function arrangement(after) {
  const current=placed();
  const options=[['center-y','Align horizontal centerline'],['center-x','Align vertical centerline'],['left','Align left edges'],['right','Align right edges'],['top','Align top edges'],['bottom','Align bottom edges'],['space-x','Equal horizontal gaps'],['space-y','Equal vertical gaps'],['eye-line','Align to eye line'],['raise','Bring to front'],['lower','Send to back']];
  openDialog('Arrange selected artworks',choose('action','Arrangement',options[0][0],options)+numeric('level','Eye line, mm',layout().eye_level,{min:0,max:wall().height}),async fields=>{
    await change(lurl('/arrange'),{...fields,placements:current.map(p=>p.id)});
    await after();
  },{confirm:'Apply arrangement'});
}

export function shotDialog(existing,after) {
  const values=[required('name','View name',existing?.name),choose('role','View type',existing?.role || 'reference',shotTypes),bigText('note','Notes',existing?.note,700)];
  if (!existing) values.unshift(sourceField('Room photograph'));
  const footer=existing?`<hr class="dialog-separator"><div class="toolline">${btn('Mark measured corners','calibrate',{id:existing.id})}${btn('Mark foreground','foreground',{id:existing.id})}${btn('Make primary','primary-shot',{id:existing.id})}${link('Original photograph',wurl(`/shots/${existing.id}/original`))}${btn('Remove photograph','delete-shot',{id:existing.id},'danger')}</div>`:'';
  openDialog(existing?'Site photograph options':'Add site photograph',values.join(''),async fields=>{
    const file=fields.file;
    delete fields.file;
    const item=await change(wurl(existing?`/shots/${existing.id}`:'/shots'),fields,{method:existing?'PATCH':'POST',file});
    state.shotId=item.id;
    await after();
  },{footer,confirm:existing?'Save view':'Import photograph'});
}

export function imagePicker(title,src,description,initial,limit,extra,submit,opts = {}) {
  const markup=`<div class="click-pair full"><p class="small">${e(description)}</p><div class="toolline"><button type="button" class="button" data-point-back>Undo point</button><button type="button" class="button" data-point-reset>Clear points</button></div><div class="editable-image is-picking"><img src="${e(src)}" alt="Mark points in this photograph"><div class="edit-marks" aria-hidden="true"></div></div></div>${extra || ''}`;
  let getPoints;
  const form=openDialog(title,markup,(fields,element)=>submit(getPoints(),fields,element),{wide:true,confirm:opts.confirm || 'Save marks',footer:opts.footer || ''});
  getPoints=collectImagePoints(form.querySelector('.editable-image'),initial,limit);
}

export function cropArtwork(id,after) {
  const work=art(id);
  imagePicker('Mark the artwork crop',photo(work.photo.preview_digest),'Click the four corners of the artwork to keep, clockwise from top left.',work.crop,4,'',async points=>{
    await change(purl(`/artworks/${id}`),{crop:points},{method:'PATCH'});
    await after();
  },{confirm:'Save crop'});
}

export async function artPreview(id) {
  const work=art(id);
  const path=purl(`/artworks/${id}`);
  const info=await request(path+'/info');
  const report=info.crop;
  openDialog(work.title,`<div class="review-layout"><div><img src="${e(path+'/print.png?kind=frame')}" alt="Framed artwork from the active crop"></div><div><p>${e(work.artist)}</p>${table(['Measurement','Value'],[['Cropped image',report.cropped_size_pixels.join(' × ')+' px'],['Mat opening',report.opening_size_mm.join(' × ')+' mm'],['Aspect fit',qty(report.aspect_fit_percent,'%')],['Source density',qty(report.source_width_mm_pixel_density,'px/mm')]])}<div class="toolline">${btn('Edit crop','crop',{id})}${link('Download crop',path+'/print.png?kind=crop&download=1')}${link('Frame-back measuring card',path+'/measuring-card.pdf')}</div></div></div>`,null,{simple:true,wide:true});
}

export function calibrateView(id,after) {
  const view=wall().shots.find(row=>row.id===id);
  imagePicker('Mark measured wall corners',photo(view.photo.preview_digest),'Mark the measured wall corners clockwise from top left. A corner on the finished floor defines the lower reference.',view.corners,4,'',async points=>{
    await change(wurl(`/shots/${id}`),{corners:points},{method:'PATCH'});
    state.shotId=id;
    await after();
  },{confirm:'Save calibration'});
}

export function foregroundView(id,after) {
  const view=wall().shots.find(row=>row.id===id);
  const controls=check('replace','Replace previous foreground marks',false);
  const footer=view.foreground.length?`<div class="toolline">${btn('Clear all foreground','clear-foreground',{id},'danger')} ${tag(view.foreground.length+' existing outlines')}</div>`:'';
  imagePicker('Mark a foreground outline',photo(view.photo.preview_digest),'Click around a foreground object that should remain in front of the simulated artworks. Mark at least three corners.',[],20,controls,async(points,fields)=>{
    const foreground=fields.replace?[points]:[...view.foreground,points];
    await change(wurl(`/shots/${id}`),{foreground},{method:'PATCH'});
    state.shotId=id;
    await after();
  },{confirm:'Save foreground outline',footer});
}

export function photoRuler() {
  const active=shot();
  imagePicker('Measure a line on the wall',photo(active.photo.preview_digest),'Choose two points on the photographed wall.',[],2,'<div class="full" id="ruler-result"></div>',async points=>{
    const result=await request(surl('/ruler'),{method:'POST',body:{start:points[0],end:points[1]}});
    document.querySelector('#ruler-result').innerHTML=`<p role="status"><strong>${qty(result.length_mm,'mm')}</strong> · ${qty(result.horizontal_delta,'mm')} across, ${qty(result.vertical_delta,'mm')} vertically${result.inside.every(Boolean)?'':' · One point is outside the calibrated wall.'}</p>`;
    return false;
  },{confirm:'Read wall distance'});
}

export function paintSample(after) {
  const active=shot();
  const source=surl('/preview.png')+'?kind=coverage&revision='+state.project.revision;
  const controls=required('name','Paint sample name','')+colorField('appearance','Reference color','#ddd3bd')+bigText('note','Notes','',400);
  imagePicker('Mark paint sample',source,'Mark a polygon over the paint sample on the rectified wall. Use at least three corners.',[],20,controls,async(points,fields)=>{
    await change(wurl('/sample'),{...fields,shot:active.id,region:points});
    state.siteReview='samples';
    await after();
  },{confirm:'Save sample'});
}

export function obstacleDialog(id,after) {
  const current=wall().obstacles.find(row=>row.id===id);
  const values=[required('name','Feature name',current?.name),choose('kind','Feature type',current?.kind || 'fixture',[['opening','Opening'],['switch','Switch plate'],['fixture','Fixture'],['plinth','Plinth'],['door-swing','Door swing'],['prohibited','No-hang area']]),
                numeric('left','Left edge, mm',current?.left ?? 0,{min:0}),numeric('bottom','Bottom edge, mm',current?.bottom ?? 0,{min:0}),
                numeric('width','Width, mm',current?.width ?? 450,{min:1}),numeric('height','Height, mm',current?.height ?? 700,{min:1}),
                numeric('depth','Distance out from wall, mm',current?.depth ?? 0,{min:0}),numeric('clearance','Required clearance, mm',current?.clearance ?? 25,{min:0}),bigText('note','Notes',current?.note,400)];
  openDialog(current?'Edit wall feature':'Add wall feature',values.join(''),async fields=>{
    await change(wurl(current?`/obstacle/${id}`:'/obstacle'),fields,{method:current?'PATCH':'POST'});
    await after();
  },{footer:current?deleteFooter('delete-obstacle',id,'Remove feature'):''});
}

export function markDialog(id,after) {
  const current=wall().mount_marks.find(row=>row.id===id);
  const values=[required('name','Fixing name',current?.name),choose('kind','Recorded point type',current?.kind || 'hole',[['hole','Existing hole'],['stud','Stud mark'],['rail-end','Rail endpoint'],['reference','Reference point']]),
                numeric('x','From left, mm',current?.x ?? wall().datum[0],{min:0,max:wall().width}),numeric('y','Above floor, mm',current?.y ?? wall().datum[1],{min:0,max:wall().height}),
                check('usable','Existing point can be reused',current?.usable ?? true),bigText('note','Notes',current?.note,400)];
  openDialog(current?'Edit recorded fixing':'Add recorded fixing',values.join(''),async fields=>{
    await change(wurl(current?`/fixing/${id}`:'/fixing'),fields,{method:current?'PATCH':'POST'});
    await after();
  },{footer:current?deleteFooter('delete-mark',id,'Remove mark'):''});
}

export function labelDialog(id,after) {
  const current=layout().labels.find(row=>row.id===id);
  const candidates=layout().placements.map(p=>[p.id,art(p.artwork).title]);
  const anchorNote=current?.follow
    ? `<p class="note ${current.follow_blocked?'attention':'good'}">${current.follow_blocked
        ? `Anchored to its work, but held in place: ${e(current.follow_blocked.blockers.map(b=>b.name).join(', '))}`
        : 'Anchored: this card follows its work at the adopted relative position.'}</p>` : '';
  const anchorField=current?.placement?check('anchor','Keep relative position when the work moves',Boolean(current?.follow)):'';
  const suggestButton=current?.placement?btn('Suggest right-side card','propose-label-card',{id:current.id},'primary'):'';
  const values=[choose('placement','Linked work',current?.placement || '',[['','Standalone label'],...candidates]),plain('name','Heading',current?.name),
                numeric('x','Center from left, mm',current?.x ?? wall().width/2,{min:0,max:wall().width}),
                numeric('y','Center above floor, mm',current?.y ?? 1200,{min:0,max:wall().height}),
                numeric('width','Label width, mm',current?.width ?? 160,{min:30,max:700}),numeric('height','Label height, mm',current?.height ?? 95,{min:20,max:500}),
                choose('style','Backing',current?.style || 'paper',[['paper','Printed card'],['vinyl','Lettering on wall']]),choose('align','Text alignment',current?.align || 'left',[['left','Left'],['center','Centered']]),
                colorField('ink','Ink color',current?.ink || '#222020'),colorField('paper','Paper color',current?.paper || '#faf7ef'),
                numeric('type_size','Body letter height, mm',current?.type_size ?? 3.5,{min:2,max:30}),bigText('body','Caption copy',current?.body,600),anchorField];
  const preview=current?`<div class="full"><img style="max-width:380px" src="${e(lurl(`/labels/${id}/preview.png?r=${state.project.revision}`))}" alt="Printed label proof"></div>${anchorNote}${suggestButton?'<div class="toolline full">'+suggestButton+'</div>':''}`:'';
  openDialog(current?'Edit wall label':'Add wall label',preview+values.join(''),async fields=>{
    await change(lurl(current?`/labels/${id}`:'/labels'),fields,{method:current?'PATCH':'POST'});
    await after();
  },{footer:current?deleteFooter('delete-label',id,'Remove label'):''});
}

export function stationDialog(id,after) {
  const current=layout().stations.find(row=>row.id===id);
  const values=[required('name','Viewpoint name',current?.name),numeric('x','Viewer from wall left, mm',current?.x ?? wall().width/2),
                numeric('distance','Viewer distance from wall, mm',current?.distance ?? 3000,{min:100,max:40000}),
                numeric('height','Viewer eye height, mm',current?.height ?? 1500,{min:200,max:2700}),
                numeric('target_x','Look-at point from left, mm',current?.target_x ?? wall().width/2,{min:0,max:wall().width}),
                numeric('target_y','Look-at point above floor, mm',current?.target_y ?? Math.min(1500,wall().height),{min:0,max:wall().height}),
                numeric('fov','Horizontal field of view, degrees',current?.fov ?? 65,{min:12,max:120}),bigText('note','Notes',current?.note,350)];
  openDialog(current?'Edit viewing position':'Add viewing position',values.join(''),async fields=>{
    const item=await change(lurl(current?`/stations/${id}`:'/stations'),fields,{method:current?'PATCH':'POST'});
    state.stationId=item.id;
    await after();
  },{footer:current?deleteFooter('delete-station',id,'Remove viewpoint'):''});
}

export function confirmDelete(label,url,after) {
  openDialog(`Remove ${label}?`,`<p>The item will be removed from this working exhibition.</p>`,async()=>{
    await change(url,{},{method:'DELETE'});
    await after();
  },{confirm:'Remove',simple:true});
}
