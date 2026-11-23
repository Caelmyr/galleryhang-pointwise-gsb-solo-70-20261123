import {state,e,photo,purl,lurl,wurl,layout,wall,shot,art,qty,percent,choices,btn,link,tag,table,metrics,facts,
        field,numeric,request,change,applyProject,openDialog,notify} from './ui.js';
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

export function surveyOutline(after) {
  const available=wall().shots;
  const initial=available.find(item=>item.id===state.shotId&&item.role==='inspection') || available.find(item=>item.role==='inspection') || available[0];
  const values=choice('shot','Choose the installed photograph',initial.id,available.map(item=>[item.id,item.name]));
  openDialog('Measure installed artwork',values,async fields=>{
    const selected=available.find(s=>s.id===fields.shot);
    installedCorners(selected,after);
    return false;
  },{confirm:'Open photograph'});
}

function installedCorners(selected,after) {
  const active=layout();
  const placements=[['','Observed outline only'],...active.placements.map(p=>[p.id,art(p.artwork).title])];
  const controls=choice('placement','Expected work',active.placements[0]?.id || '',placements)+required('title','Check name','Installed position')+
    choice('condition','Survey stage','as installed',['as installed','after adjustment','reference check'])+
    numeric('center_tolerance','Center tolerance in mm',20,{min:1,max:500})+numeric('angle_tolerance','Rotation tolerance in degrees',1,{min:.1,max:20})+
    numeric('size_tolerance','Outside size tolerance in mm',20,{min:1,max:350})+longText('note','Notes','',650);
  imagePicker('Mark installed artwork outline',photo(selected.photo.preview_digest),'Mark the visible frame in the installation photo. Start upper left and select the remaining corners clockwise.',[],4,controls,async (corners,fields)=>{
    await change(lurl('/surveys'),{...fields,corners,shot:selected.id});
    state.review='survey';
    await after();
  },{confirm:'Save measured outline'});
}

export function surveyPicture(id) {
  const found=layout().installed_surveys.find(row=>row.id===id);
  if (!found) throw new Error('The installed outline was not found.');
  const image=lurl(`/surveys/${id}/preview.png?revision=${state.project.revision}`);
  openDialog(found.title,`<img src="${e(image)}" alt="Observed and predicted outline on the installation photograph"><p>${e(found.conclusion.status)}</p>${table(['Quantity','Recorded value'],[['Observed center',found.observed.center.join(', ')+' mm'],['Observed dimensions',`${qty(found.observed.width)} × ${qty(found.observed.height)} mm`],['Rotation',qty(found.observed.angle,'°')]])}<div class="toolline">${btn('Remove measured outline','delete-survey',{id},'danger')}</div>`,null,{simple:true,wide:true});
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

function cardBlock(option) {
  const leg=option.legibility;
  const blocked=option.collision.blocked;
  const blockerRows=blocked
    ? option.collision.blockers.map(b=>[b.name,b.reason,`${qty(b.distance_mm,'mm')} / need ${qty(b.required_mm,'mm')}`])
    : [];
  return `<article class="proposal-card${option.preferred?' preferred':''}">
    <header><h4>${option.card==='narrow'?'Narrow card · easier to align':'Wide card · larger type'}</h4>
      ${option.preferred?tag('Preferred suggestion','good'):''}</header>
    ${facts([['Card size',`${qty(option.width)} × ${qty(option.height)} mm`],
      ['Sheet',option.paper.toUpperCase()+' '+option.paper_size_mm.join(' × ')+' mm'],
      ['Body type size',qty(option.type_size,'mm')],
      ['Heading size',qty(option.heading_size,'mm')],
      ['Gap to work',qty(option.gap_to_work_mm,'mm')],
      ['Position from left / floor',`${qty(option.position_mm[0])} / ${qty(option.position_mm[1])} mm`],
      ['Copy lines',String(option.fitting.lines)],
      ['Viewpoint legibility',leg?`${e(leg.name)} · ${qty(leg.body_angle_arcmin,'arcmin')} · ${leg.legible?'reads clearly':'below 6 arcmin'}`:'No viewpoint saved']])}
    ${blocked
      ? `<p class="note attention">Cannot place here — ${e(blockerRows.map(r=>r[0]).join(', '))}</p>
         ${table(['Blocks against','Reason','Gap / required'],blockerRows)}`
      : (option.meets_viewpoint
          ? '<p class="note good">Clear of neighbouring works and other cards, and legible at the selected viewpoint.</p>'
          : '<p class="note attention">Fits the gap, but the body type is below six arc minutes at the selected viewpoint.</p>')}
    <div class="toolline">${btn(option.preferred?'Adopt preferred card':'Adopt this card','adopt-label-card',{card:option.card},option.preferred?'primary':'')}</div>
  </article>`;
}

export function labelProposalDialog(id,after) {
  const active=layout();
  const current=active.labels.find(row=>row.id===id);
  if (!current?.placement) throw new Error('Link the label to an artwork first, then propose its card.');
  const stationOptions=active.stations.map(item=>[item.id,`${item.name} · ${qty(item.distance,'mm')} away`]);
  const defaultStation=stationOptions.find(([id])=>id===state.stationId)?.[0] || stationOptions[0]?.[0] || '';
  const controls=stationOptions.length
    ? choice('station','Readability viewpoint',defaultStation,stationOptions)
    : '';
  const load=async fields=>{
    const query=new URLSearchParams();
    if (fields.station) query.set('station',fields.station);
    const report=await request(lurl(`/labels/${id}/proposal?${query}`));
    state.labelProposal={id, report, station:fields.station || '', after};
    document.querySelector('#proposal-result').innerHTML=
      `<p>${e(report.summary)}</p><div class="proposal-grid">${report.options.map(cardBlock).join('')}</div>`;
    return false;
  };
  openDialog('Right-side label card',`<p class="small">Two card stocks trimmed from the paper sizes in hand. The card keeps the layout's label gap to the work and clears neighbouring art, other cards and marked wall features.</p>${controls}<div id="proposal-result" class="full"><p class="small">Preparing the two card options…</p></div>`,
    load,{wide:true,confirm:'Refresh suggestion'});
  const form=document.querySelector('#dialog form');
  form.querySelector('[name=station]')?.addEventListener('change',()=>form.requestSubmit());
  // Load the initial suggestion once the dialog is on screen.
  setTimeout(()=>form.requestSubmit(),0);
}

export async function adoptProposedCard(card) {
  const pending=state.labelProposal;
  if (!pending) throw new Error('Open a label card suggestion first.');
  const {id, station, after}=pending;
  const response=await request(lurl(`/labels/${id}/proposal`),{method:'POST',
    body:{revision:state.project.revision,card,station:station||undefined}});
  // Keep the local revision in step with the saved adoption, otherwise the
  // next placement edit would be rejected as a stale revision.
  applyProject(response.project);
  document.querySelector('#dialog').close();
  state.labelProposal=null;
  notify('Card adopted and anchored to the work');
  await after();
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
