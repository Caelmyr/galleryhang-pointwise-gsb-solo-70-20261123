import {state,e,photo,purl,lurl,wurl,layout,wall,shot,art,qty,percent,opts,choices,btn,link,tag,table,metrics,
        field,numeric,request,change,openDialog,notify} from './ui.js';
import {imagePicker} from './editing.js';

const choice = (name,label,value,options) => field(name,label,value,{type:'select',options});
const required = (name,label,value,max=120) => field(name,label,value,{required:true,attrs:{maxlength:max}});
const longText = (name,label,value,max=500) => field(name,label,value,{type:'textarea',wide:true,attrs:{maxlength:max}});
const check = (name,label,value) => field(name,label,value,{type:'checkbox'});

export function comparePhotos() {
  const captures=wall().shots;
  const options=captures.map(item=>[item.id,item.name]);
  const controls=choice('first','Reference view',captures[0].id,options)+choice('second','Compare with',captures[1].id,options)+
    choice('mode','Preview mode','split',[['split','Split view'],['side','Side by side'],['difference','Difference map']])+
    numeric('threshold','Difference threshold',0.075,{min:0.01,max:0.8,step:0.005})+
    numeric('split','Split location, fraction across',0.5,{min:0,max:1,step:0.05})+check('normalize','Normalize exposure for comparison',true)+
    `<div class="full" id="compare-result"></div>`;
  openDialog('Compare registered photographs',controls,async fields=>{
    if (fields.first===fields.second) throw new Error('Choose two different views.');
    const query=new URLSearchParams({...fields,normalize:fields.normalize?'1':'0'});
    const result=await request(wurl(`/compare?${query}`));
    const image=wurl(`/compare.png?${query}`);
    document.querySelector('#compare-result').innerHTML=`<img alt="Registered photo comparison" src="${e(image)}">${metrics([['Shared wall',percent(result.supported_fraction)],['Different area',percent(result.different_fraction_in_supported)],['Exposure gain',qty(result.gain)]])}`;
    return false;
  },{wide:true,confirm:'Compare photographs'});
}

export function installationCompare() {
  const captures=wall().shots;
  const options=captures.map(item=>[item.id,item.name]);
  const initial=captures.find(item=>item.role==='inspection') || captures[1];
  const controls=choice('base','Survey reference',captures[0].id,options)+choice('shot','Installation photograph',initial.id,options)+
    choice('mode','Preview mode','side',[['side','Side by side'],['difference','Difference map']])+
    numeric('threshold','Difference threshold',0.075,{min:0.01,max:0.8,step:0.005})+`<div class="full" id="install-result"></div>`;
  openDialog('Compare the installation with plan',controls,async fields=>{
    const query=new URLSearchParams(fields);
    const result=await request(lurl(`/check?${query}`));
    document.querySelector('#install-result').innerHTML=`<img alt="Planned and installed wall comparison" src="${e(lurl(`/check.png?${query}`))}">${table(['Artwork','Observed wall area','Median luminance difference'],result.items.map(row=>[row.title,percent(row.observed_fraction),qty(row.median_absolute_delta)]))}`;
    return false;
  },{wide:true,confirm:'Compare installation'});
}

const hiddenOptions=[['none','All four corners visible'],['top-left','Top left hidden'],['top-right','Top right hidden'],
                     ['bottom-right','Bottom right hidden'],['bottom-left','Bottom left hidden']];

export function surveyOutline(after,target=null) {
  const available=wall().shots;
  if (!available.length) throw new Error('Import the installation photograph first.');
  const initial=available.find(item=>item.id===state.shotId&&item.role==='inspection') || available.find(item=>item.role==='inspection') || available[0];
  const values=choice('shot',target?'Choose the re-shoot photograph':'Choose the installed photograph',initial.id,available.map(item=>[item.id,item.name]))+
    (target?`<p class="small">Reading for <strong>${e(target.title)}</strong>. Mark all four corners on the re-shoot so the original estimate and this observation can be compared.</p>`:
      choice('blocked','Which corner is hidden','none',hiddenOptions));
  openDialog('Measure installed artwork',values,async fields=>{
    const selected=available.find(s=>s.id===fields.shot);
    installedCorners(selected,after,{check:target,blocked:target?'none':fields.blocked});
    return false;
  },{confirm:'Open photograph'});
}

function installedCorners(selected,after,{check=null,blocked='none'}={}) {
  const active=layout();
  const placements=[['','Observed outline only'],...active.placements.map(p=>[p.id,art(p.artwork).title])];
  const controls=check?
    `<p class="small">Adding a reading to <strong>${e(check.title)}</strong>. The original estimated corners and measurements stay in the check.</p>`+longText('note','Reading notes','',500):
    choice('placement','Expected work',active.placements[0]?.id || '',placements)+required('title','Check name','Installed position')+
    choice('condition','Survey stage','as installed',['as installed','after adjustment','reference check'])+
    numeric('center_tolerance','Center tolerance in mm',20,{min:1,max:500})+numeric('angle_tolerance','Rotation tolerance in degrees',1,{min:.1,max:20})+
    numeric('size_tolerance','Outside size tolerance in mm',20,{min:1,max:350})+longText('note','Notes','',650);
  const limit=blocked==='none'?4:3;
  const description=blocked==='none'
    ? 'Mark the four artwork corners clockwise from the upper left.'
    : `Mark the three visible corners clockwise, skipping the hidden ${blocked.replace('-',' ')}. The registered frame width and height supply a right-angled estimate for the missing corner; the two observed sides are matched as closely as a square corner allows.`;
  imagePicker('Mark installed artwork outline',photo(selected.photo.preview_digest),description,[],limit,controls,async (corners,fields)=>{
    if (blocked!=='none' && !fields.placement) throw new Error('Choose the planned artwork so its registered width and height can complete the hidden corner.');
    const body={...fields,corners,shot:selected.id,blocked_corner:blocked};
    if (check) body.check=check.id;
    await change(lurl('/surveys'),body);
    state.review='survey';
    await after();
  },{confirm:'Save measured outline'});
}

function savedReadings(check) {
  if (check.readings?.length) return check.readings;
  return [{id:check.id,index:1,kind:'four corner observation',shot:check.shot,photo_corners:check.photo_corners,
           corner_indices:[0,1,2,3],blocked_corner:null,observed:check.observed,expected:check.expected,geometry:null}];
}

export function surveyPicture(id,readingId=null) {
  const found=layout().installed_surveys.find(row=>row.id===id);
  if (!found) throw new Error('The installed outline was not found.');
  const readings=savedReadings(found);
  const image=lurl(`/surveys/${id}/preview.png?revision=${state.project.revision}${readingId?`&reading=${encodeURIComponent(readingId)}`:''}`);
  const rows=readings.map(row=>[{html:btn(`Reading ${row.index}${row.id===readingId?' · shown':''}`,'survey-reading',{id,reading:row.id})},
    row.kind,row.blocked_corner?`${row.blocked_corner} estimated`:'four corners',row.observed.center.map(v=>qty(v)).join(', ')+' mm',
    `${qty(row.observed.width)} × ${qty(row.observed.height)} mm`,row.shot===shot()?.id?'This view':'Other view']);
  const shown=readings.find(row=>row.id===readingId) || readings.at(-1);
  const geometry=shown?.geometry;
  const inferred=geometry?`<p class="small">${e(geometry.hidden_corner)} hidden: anchored ${e(geometry.anchored_edges.join(' and '))}, estimated ${e(geometry.inferred_edges.join(' and '))}. The two photographed sides meet at ${qty(geometry.adjacent_sides_deg,'°')}; the estimated hidden corner moves ${qty(geometry.hidden_corner_spread_mm,'mm')} between hugging the first or second observed side.</p>`:'';
  const first=readings[0],last=readings.at(-1);
  const comparison=readings.length>1?table(['First reading → latest','Value'],[
    ['Basis',`${first.kind} → ${last.kind}`],
    ['Center move',qty(Math.hypot(last.observed.center[0]-first.observed.center[0],last.observed.center[1]-first.observed.center[1]),'mm')],
    ['Width change',qty(last.observed.width-first.observed.width,'mm')],
    ['Height change',qty(last.observed.height-first.observed.height,'mm')],
    ['Rotation change',qty(last.observed.angle-first.observed.angle,'°')]]):'';
  openDialog(found.title,`<img src="${e(image)}" alt="Observed and estimated outline on the installation photograph"><p>${e(found.conclusion.status)}</p>${table(['Reading','Basis','Hidden corner','Observed center','Observed size','View'],rows,'No readings saved yet.')}${inferred}${comparison}<div class="toolline">${btn('Add re-shoot reading','survey-reshoot',{id},'primary')}${btn('Remove measured outline','delete-survey',{id},'danger')}</div>`,null,{simple:true,wide:true});
}

export function visitRoute(id,after) {
  const current=layout().visit_sequences?.find(row=>row.id===id);
  const available=layout().stations;
  const choicesForStops=[['','No stop'],...available.map(item=>[item.id,item.name])];
  const fields=[required('name','Sequence name',current?.name || 'Gallery walkthrough'),check('closed','Return to first viewpoint',current?.closed || false),longText('note','Route notes',current?.note,600)];
  const stopCount=Math.max(3,available.length,current?.stops.length || 0);
  for (let index=0;index<Math.min(stopCount,12);index++) {
    const stop=current?.stops[index];
    fields.push(choice(`stop-${index}`,`Stop ${index+1}`,stop?.station || (!current&&available[index]?.id) || '',choicesForStops),
                numeric(`dwell-${index}`,'Time at stop in seconds',stop?.dwell ?? 30,{min:0,max:1200}));
  }
  openDialog(current?'Edit visit sequence':'New visit sequence',fields.join(''),async input=>{
    const stops=[];
    for (let index=0;index<Math.min(stopCount,12);index++) {
      if (input[`stop-${index}`]) stops.push({station:input[`stop-${index}`],dwell:input[`dwell-${index}`]});
    }
    const data={name:input.name,note:input.note,closed:input.closed,stops};
    const item=await change(lurl(current?`/routes/${id}`:'/routes'),data,{method:current?'PATCH':'POST'});
    state.routeId=item.id;
    await after();
  },{footer:current?`<hr class="dialog-separator"><div class="remove-row">${btn('Remove sequence','delete-route',{id},'danger')}</div>`:''});
}

export function issueDialog(after) {
  const active=layout();
  const controls=required('name','Issue name',active.name+' · issue')+
    choice('kind','Issue contents','installer',[['installer','Installation PDF, fixing table and images'],['proof','Client proof and preview images']])+
    choice('shot','Photograph for the montage',shot()?.id || '',wall().shots.length?wall().shots.map(item=>[item.id,item.name]):[['','No site photograph']]);
  openDialog('Save an exhibition issue',controls,async fields=>{
    await request(lurl('/issue'),{method:'POST',body:{...fields,revision:state.project.revision}});
    state.page='issues';
    notify('Issue saved');
    await after();
  },{confirm:'Save issue'});
}

export function verifyIssue() {
  const controls=field('file','Received GalleryHang ZIP','',{type:'file',wide:true,required:true,attrs:{accept:'application/zip,.zip'}})+`<div class="full" id="received-verification"></div>`;
  openDialog('Verify received issue',controls,async fields=>{
    const result=await request('/api/issues/verify',{method:'POST',file:fields.file});
    document.querySelector('#received-verification').innerHTML=`<h3>${e(result.name)}</h3>${tag(result.valid?'All declared files match':'Issue requires review',result.valid?'good':'attention')}<p>${e(result.project)}, revision ${e(result.revision)}</p>${table(['Artifact','Verification'],result.files.map(row=>[row.name,row.status]))}`;
    return false;
  },{confirm:'Check archive'});
}

function artworkSelectors() {
  return state.project.artworks.map(item=>`<label class="field"><span>${e(item.title)}</span><input name="art-${e(item.id)}" type="checkbox" checked></label>`).join('');
}

function selectedWorks(fields) {
  return Object.keys(fields).filter(key=>key.startsWith('art-') && fields[key]).map(key=>key.slice(4));
}

export function colorsDialog() {
  const values=artworkSelectors()+`<div class="full" id="palette-result"></div>`;
  openDialog('Compare artwork colors',values,async fields=>{
    const artworks=selectedWorks(fields);
    if (!artworks.length) throw new Error('Select at least one artwork.');
    const cards=await request(purl('/catalog/palette'),{method:'POST',body:{artworks}});
    document.querySelector('#palette-result').innerHTML=cards.artworks.map(item=>`<div class="panel"><h3>${e(item.title)}</h3><div class="toolline">${item.swatches.map(s=>`<span class="tag" style="border-left:12px solid ${e(s.color)}">${e(s.color)} · ${qty(s.percent,'%')}</span>`).join('')}</div></div>`).join('')+table(['First','Second','Palette difference'],cards.pairs.map(row=>[row.first,row.second,qty(row.difference)]));
    return false;
  },{wide:true,confirm:'Compare selected palettes'});
}

export function contactSheetDialog() {
  const values=choice('order','Artwork order','title',[['title','Title'],['artist','Artist'],['inventory','Inventory number'],['wall order','Current wall order']])+artworkSelectors()+`<div class="full" id="contact-result"></div>`;
  openDialog('Artwork contact sheet',values,async fields=>{
    const artworks=selectedWorks(fields);
    if (!artworks.length) throw new Error('Select at least one artwork.');
    const query=new URLSearchParams({artworks:artworks.join(','),order:fields.order,layout:layout()?.id || ''});
    const preview=purl(`/catalog/contact.png?${query}`);
    document.querySelector('#contact-result').innerHTML=`<img src="${e(preview)}" alt="Rendered artwork contact sheet">${link('Download contact sheet',preview+'&download=1')}`;
    return false;
  },{wide:true,confirm:'Make contact sheet'});
}

export function compareIssuedGeometry() {
  const choices=state.issues.map(row=>[row.id,row.name]);
  const fields=choice('first','Earlier issue',choices[1][0],choices)+choice('second','Compare with',choices[0][0],choices)+`<div id="issue-difference" class="full"></div>`;
  openDialog('Compare saved issues',fields,async data=>{
    const result=await request(purl(`/issues/${data.first}/compare/${data.second}`));
    const cells=result.artworks.map(item=>[item.title,item.status,item.changes?.join(', ') || '—',qty(item.distance_mm,'mm')]);
    document.querySelector('#issue-difference').innerHTML=metrics([['Changed',result.counts.changed || 0],['Added',result.counts.added || 0],['Removed',result.counts.removed || 0]])+table(['Artwork','State','Changes','Movement'],cells);
    return false;
  },{confirm:'Compare issues',wide:true});
}

export function printLabels() {
  const fields=choice('paper','Paper size','a4',[['a4','A4'],['letter','US Letter']])+`<div id="label-print-plan" class="full"></div>`;
  openDialog('Print wall label cards',fields,async data=>{
    const report=await request(lurl(`/label-print?paper=${data.paper}`));
    const cards=report.cards.map(card=>[card.title,card.sheet,card.size_mm.join(' × ')+' mm']);
    const warnings=report.oversized.length?`<p>${e(report.oversized.map(card=>card.title).join(', '))} exceeds this paper format.</p>`:'';
    const vinyl=report.vinyl.length?`<p>Wall vinyl is omitted: ${e(report.vinyl.map(card=>card.title).join(', '))}.</p>`:'';
    document.querySelector('#label-print-plan').innerHTML=metrics([['Pages',report.sheet_count],['Cards',cards.length]])+table(['Card','Page','Physical size'],cards)+warnings+vinyl+(cards.length?link('Download actual-size PDF',lurl(`/label-print.pdf?paper=${data.paper}`),'primary'):'');
    return false;
  },{confirm:'Check print layout'});
}

export async function wallObservations() {
  const photoView=shot();
  const path=`?shot=${photoView.id}&r=${state.project.revision}`;
  const result=await request(lurl('/observations'+path));
  const image=lurl(`/render.png?view=observations&shot=${photoView.id}&r=${state.project.revision}`);
  const rows=result.observations.map(item=>[item.title,item.note,item.original_view,item.status,{html:btn('Remove','delete-observation',{id:item.id},'danger')}]);
  openDialog('Wall observations',`<img src="${e(image)}" alt="Site photograph with wall observations">${table(['Observation','Notes','Source view','Location',''],rows,'No observations on this working layout.')}${btn('Pin an observation','add-observation')}`,null,{simple:true,wide:true});
}

export function addObservation(after) {
  const active=shot();
  const fields=required('title','Observation title','')+longText('note','Observation notes','',1000);
  imagePicker('Pin an observation',photo(active.photo.preview_digest),'Pick one location on the measured wall.',[],1,fields,async (points,values)=>{
    if (!points.length) throw new Error('Mark one point in the photograph.');
    await change(lurl('/observations'),{...values,point:points[0],shot:active.id});
    await after();
  },{confirm:'Save observation'});
}
