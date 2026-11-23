import {state,e,photo,purl,art,qty,percent,shortDate,btn,tag,metrics,field,request,change,openDialog} from './ui.js';
import {imagePicker} from './editing.js';

const filetypes = 'image/png,image/jpeg,image/webp,image/tiff,.tiff,.tif';

function shotOptions() {
  return state.project.walls.flatMap(item => item.shots.map(view => ({wall:item,view})));
}

function candidateChecks() {
  return state.project.artworks.map(item =>
    `<label class="field check-line"><input name="cand-${e(item.id)}" type="checkbox"><span>${e(item.title)}</span></label>`).join('');
}

export function identifyDialog() {
  const shots = shotOptions();
  const fields = [
    shots.length ? field('shot','Use a site photograph','',{type:'select',
      options:[['','— or upload a new photograph —'],...shots.map(row=>[`${row.wall.id}/${row.view.id}`,`${row.wall.name} · ${row.view.name}`])]}) : '',
    field('file','Upload the on-site photograph','',{type:'file',wide:true,attrs:{accept:filetypes}}),
    field('name','Identification name','Site identification',{attrs:{maxlength:120}}),
    field('note','Notes','',{type:'textarea',wide:true,attrs:{maxlength:600}}),
    `<div class="full"><p class="small">Candidate versions to compare (choose 2 to 4). Only the circled area is compared; the rest of the surface does not count as a match.</p><div class="check-grid">${candidateChecks()}</div></div>`,
  ];
  openDialog('Identify the displayed version',fields.join(''),async values=>{
    const candidates = state.project.artworks.filter(item=>values[`cand-${item.id}`]).map(item=>item.id);
    if (candidates.length<2 || candidates.length>4) throw new Error('Choose two to four candidate artworks.');
    const prepared = {name:values.name || 'Site identification',note:values.note || '',candidates};
    if (values.file) {
      const result = await request(purl('/identify/photo'),{method:'POST',file:values.file});
      prepared.photo = result.photo;
    } else if (values.shot) {
      const found = shots.find(row=>`${row.wall.id}/${row.view.id}`===values.shot);
      if (!found) throw new Error('Choose a site photograph or upload one.');
      prepared.source_shot = {wall:found.wall.id,shot:found.view.id};
      prepared.photo = found.view.photo;
    } else {
      throw new Error('Choose a site photograph or upload one.');
    }
    markVisibleRegion(prepared);
  },{confirm:'Mark the visible area',wide:true});
}

function markVisibleRegion(prepared) {
  imagePicker('Circle the still-visible area',photo(prepared.photo.preview_digest),
    'Mark a polygon around the part of the artwork that remains clearly visible. Only this area is compared — anything outside the circle is not counted as a match.',
    [],16,'',async points=>{
      const payload = {name:prepared.name,note:prepared.note,candidates:prepared.candidates,region:points};
      if (prepared.source_shot) payload.source_shot = prepared.source_shot;
      else payload.photo = prepared.photo;
      const record = await change(purl('/identifications'),payload);
      showIdentificationResult(record);
    },{confirm:'Compare candidates'});
}

function resultCard(record,result) {
  const url = `${purl(`/identifications/${record.id}/match.png`)}?artwork=${result.artwork}&r=${state.project.revision}`;
  const action = record.status==='open'
    ? btn('Confirm this version','confirm-identification',{id:record.id,art:result.artwork},'primary')
    : (record.choice===result.artwork ? tag('Confirmed version','good') : '');
  return `<article class="identify-card"><h3>${e(result.title)}</h3><img src="${e(url)}" alt="Matched area on the original of ${e(result.title)}">${metrics([['Local fit',qty(result.local_fit)],['Inside its crop',percent(result.coverage)],['Score',qty(result.combined)]])}<p class="small">${e(result.note)}</p><div class="toolline">${action}</div></article>`;
}

export function showIdentificationResult(record) {
  const cards = record.results.map(result=>resultCard(record,result)).join('');
  const footer = record.status==='open'
    ? `<hr class="dialog-separator"><div class="remove-row">${btn('Discard this identification','discard-identification',{id:record.id},'danger')}</div>` : '';
  openDialog(record.name,
    `<p>${e(record.verdict)}</p><p class="note">${e(record.method)}</p><div class="identify-grid">${cards}</div>${footer}`,
    null,{simple:true,wide:true});
}

export async function identificationView(id,selected=null) {
  const detail = await request(purl(`/identifications/${id}`));
  const record = detail.record;
  const active = selected || record.choice || record.results[0]?.artwork;
  const current = detail.current.find(row=>row.artwork===active);
  const revision = state.project.revision;
  const tabs = detail.current.map(row=>
    `<button type="button" class="button ${row.artwork===active?'primary':''}" data-do="identification-candidate" data-id="${e(id)}" data-art="${e(row.artwork)}"${row.available?'':' disabled'}>${e(row.title)}${row.chosen?' ✓':''}</button>`).join('');
  const outside = current?.inside_current_crop==null ? null : 1-current.inside_current_crop;
  const cropNote = outside==null ? '' : outside>0.005
    ? `<p class="note">${percent(outside)} of the originally matched area is now outside the display crop (shown in red). The stored match region does not move when the crop changes.</p>`
    : '<p class="small">The full matched area is still inside the current display crop.</p>';
  openDialog(record.name,
    `<p>${e(record.verdict)}</p><p class="note">${e(record.method)}</p><div class="toolline">${tabs}</div>`+
    `<div class="identify-pair"><figure><img src="${e(purl(`/identifications/${id}/photo.png`))}?r=${revision}" alt="On-site photograph with the compared area circled"><figcaption>On-site photograph · compared area circled</figcaption></figure>`+
    `<figure><img src="${e(purl(`/identifications/${id}/match.png`))}?artwork=${e(active)}&r=${revision}" alt="Matched area on the candidate original"><figcaption>${e(current?.title || 'Removed artwork')} · matched area (mint) · current display crop (amber dashes)</figcaption></figure></div>${cropNote}`,
    null,{simple:true,wide:true});
}

export function identificationListSection() {
  const records = state.project.identifications || [];
  if (!records.length) return '';
  const rows = records.map(record=>{
    const chosen = record.choice ? art(record.choice)?.title || 'removed artwork' : '';
    const action = record.status==='confirmed' ? 'identification-view' : 'identification-resume';
    const status = record.status==='confirmed' ? `Confirmed · ${chosen}` : 'Awaiting confirmation';
    return `<button class="place" type="button" data-do="${action}" data-id="${e(record.id)}"><b>${e(record.name)}</b><span>${e(status)} · ${e(shortDate(record.created))}</span></button>`;
  }).join('');
  return `<section class="shelf"><div class="panel-head"><h2>Site identifications</h2></div><div class="place-list">${rows}</div></section>`;
}
