import {state,e,photo,purl,lurl,wurl,surl,layout,wall,shot,art,station,route,placed,qty,percent,shortDate,
        opts,choices,attr,btn,link,headline,panel,empty,tag,table,metrics,facts,segment,loadInside} from './ui.js';

const sections = [['plan','▤','Wall plan'],['sites','◇','Site photographs'],['artworks','▧','Artworks'],
                  ['view','◉','Viewing positions'],['review','⊞','Checks'],['issues','▣','Issues']];
const planViews = [['photo','Site preview'],['elevation','Elevation'],['registered','Aligned photo']];
const reviewTabs = [['clearances','Clearances'],['mounts','Fixing points'],['labels','Labels'],['balance','Wall balance'],['survey','Installed check']];

export function drawNavigation() {
  const navigation = document.querySelector('#navigation');
  const project = state.project;
  document.querySelector('#app').classList.toggle('library',state.page==='library' || !project);
  document.querySelector('#top-project').textContent = state.page==='library' ? '' : project?.name || '';
  if (!project) { navigation.innerHTML=''; return; }
  navigation.innerHTML=`<div class="nav-project"><strong>${e(project.name)}</strong><span>${e(project.venue || 'Exhibition workspace')}</span></div><div class="nav-list">${sections.map(([id,glyph,label])=>`<button type="button" class="nav-item" ${attr('navigate',{page:id})}${state.page===id?' aria-current="page"':''}><span class="nav-glyph" aria-hidden="true">${glyph}</span>${label}</button>`).join('')}</div><div class="nav-bottom"><button type="button" class="nav-item" data-do="library"><span class="nav-glyph" aria-hidden="true">←</span>Exhibitions</button><button type="button" class="nav-item" data-do="edit-project"><span class="nav-glyph" aria-hidden="true">⚙</span>Exhibition details</button></div>`;
}

export function libraryPanel() {
  const projects = state.projects.map(project => `<button type="button" class="project-card" ${attr('open-project',{id:project.id})}>${project.cover?`<img src="${photo(project.cover)}" alt="">`:'<span class="project-placeholder"></span>'}<div><strong>${e(project.name)}</strong><small>${e(project.venue || 'Local exhibition')}</small><small>${project.wall_count} ${project.wall_count===1?'wall':'walls'} · ${project.artwork_count} artworks${project.synthetic?' · Synthetic sample':''}</small></div></button>`).join('');
  return `<div class="landing"><section class="landing-hero"><div class="hero-copy"><h1>Plan the hang.<br>See it on the wall.</h1><p>Arrange artworks on a measured wall, preview them in your site photography, and issue a plan for installation.</p><div class="toolline">${btn('Open sample exhibition','open-demo',{},'primary')}${btn('New exhibition','new-project')}</div></div><img src="/api/demo/cover.png" alt="Synthetic gallery with a measured plaster wall and stone plinth"></section>${projects?`<section class="project-library"><h2 class="section-title">Your exhibitions</h2><div class="project-cards">${projects}</div></section>`:''}</div>`;
}

export function preLayoutPage(title,details) {
  const action = wall() ? btn('New layout','new-layout',{},'primary') : btn('Add measured wall','new-wall',{},'primary');
  return `<div class="workspace">${headline(title,details,action)}${empty(wall()?'Create a layout for this measured wall.':'Add a wall with surveyed width and height to start a layout.',action)}</div>`;
}

export function layoutPicker() {
  const {project} = state;
  return `<label class="small">Layout <select class="compact-select" aria-label="Layout" data-change="layout">${opts(project.layouts,state.layoutId)}</select></label>`;
}

function sitePicker() {
  if (!wall()?.shots.length) return '';
  return `<label class="small">Site view <select class="compact-select" aria-label="Site view" data-change="shot">${opts(wall().shots,state.shotId)}</select></label>`;
}

function imageQuery(base,fields) {
  const search = new URLSearchParams({...fields,revision:String(state.project.revision)});
  return `${base}?${search}`;
}

function planRenderUrl(mode,download=false) {
  const fields = {view:mode};
  if (shot()) fields.shot=shot().id;
  if (download) fields.download='1';
  return imageQuery(lurl('/render.png'),fields);
}

function selectableName(placement,index) {
  return `<button type="button" class="place" ${attr('pick-placement',{id:placement.id})} aria-pressed="${state.selections.includes(placement.id)}"><i>${index+1}</i><b>${e(art(placement.artwork)?.title)}</b><span>${placement.pinned?'Pinned':placement.visible?'':'Hidden'}</span></button>`;
}

function planDots(active, activeWall, view) {
  if (view!=='elevation') return '';
  return `<div class="scene-points">${active.placements.filter(placement=>placement.visible).map((placement,index)=>`<button type="button" class="scene-dot" aria-label="Select ${e(art(placement.artwork)?.title)}" aria-pressed="${state.selections.includes(placement.id)}" ${attr('pick-placement',{id:placement.id})} style="left:${100*placement.x/activeWall.width}%;top:${100*(1-placement.y/activeWall.height)}%">${index+1}</button>`).join('')}</div>`;
}

function artCard(item, compact=false) {
  const url = photo(item.photo.thumbnail_digest);
  const canPlace = Boolean(layout());
  return `<article class="art-card"><div class="thumb"><img src="${e(url)}" alt="${e(item.title)}"></div><div class="body"><h3>${e(item.title)}</h3><p>${e(compact ? `${item.width} × ${item.height} mm` : [item.artist,item.inventory,`${item.width} × ${item.height} mm`].filter(Boolean).join(' · '))}</p><div class="toolline">${canPlace?btn('Place','place-work',{id:item.id}):''}${compact?btn('Details','edit-art',{id:item.id}):btn('Edit artwork','edit-art',{id:item.id})}${compact?'':btn('Image preview','art-preview',{id:item.id})}</div></div></article>`;
}

export function planPanel() {
  const active = layout(), activeWall=wall();
  if (!active || !activeWall) return preLayoutPage('Wall plan','Measured artwork arrangements and site previews.');
  const project = state.project;
  const view = activeWall.shots.length ? state.planView : 'elevation';
  const modes = activeWall.shots.length ? planViews : [['elevation','Elevation']];
  const activeSelection = placed();
  const details = `${qty(activeWall.width)} × ${qty(activeWall.height)} mm · ${active.placements.filter(row=>row.visible).length} artworks on wall`;
  const actions = btn('New wall','new-wall')+btn('Layout options','layout-options')+btn('Place artwork','choose-art',{},'primary');
  const fitting = activeSelection.length===1?activeSelection[0]:null;
  const fittingArt = fitting?art(fitting.artwork):null;
  const placeActions = activeSelection.length>1
    ? `<h3>Arrange ${activeSelection.length} selected</h3><div class="toolline">${btn('Arrange','arrange')}${btn('Clear selection','clear-selection')}</div>`
    : fitting?`<h3>${e(fittingArt.title)}</h3>${facts([['From left',qty(fitting.x,'mm')],['Above floor',qty(fitting.y,'mm')],['Angle',qty(fitting.angle,'°')]])}<div class="toolline">${btn('Edit position','edit-placement',{id:fitting.id},'primary')}${btn('Deselect','clear-selection')}${fitting.pinned?'':btn('Remove','remove-placement',{id:fitting.id})}</div>`:'';
  return `<div class="workspace">${headline(activeWall.name,details,actions)}<div class="layout-grid"><div><section class="stage-card"><div class="stage-head">${layoutPicker()}${segment(modes,view,'plan-mode')}${view!=='elevation'?sitePicker():''}</div><div class="frame-screen"><div class="image-stage"><img id="scene-img" src="${e(planRenderUrl(view))}" alt="${e(active.name)} ${e(view==='photo'?'in site photograph':'on measured wall')}">${planDots(active,activeWall,view)}</div></div><div class="stage-foot"><span>${e(active.name)}${shot() && view!=='elevation'?' · '+e(shot().name):''}</span><span><a href="${e(planRenderUrl(view,true))}" download>Save PNG</a></span></div></section><section class="shelf"><div class="panel-head"><h2>Artwork library</h2>${btn('Add artwork','add-art')}</div>${project.artworks.length?`<div class="shelf-grid">${project.artworks.map(item=>artCard(item,true)).join('')}</div>`:empty('Upload an artwork image to put it on the wall.',btn('Add artwork','add-art'))}</section></div><aside class="inspector" aria-label="Layout details"><h2>Layout details</h2>${facts([['Working layout',active.name],['Eye line',qty(active.eye_level,'mm')],['Minimum gap',qty(active.gap,'mm')]])}<div class="toolline">${btn('Edit layout','edit-layout')}${btn('Edit wall','edit-wall')}</div><hr><h2>Artwork positions</h2>${active.placements.length?`<div class="place-list">${active.placements.map(selectableName).join('')}</div><p class="small">Select multiple artworks to align or space them.</p>`:'<p class="empty">No artwork is placed on this wall yet.</p>'}${placeActions}<hr><div class="panel-head"><h2>Exhibition labels</h2>${btn('Add label','add-label')}</div>${active.labels.length?active.labels.map(label=>labelItem(label,active)).join(''):'<p class="small">Place labels beside a work or write a standalone caption.</p>'}</aside></div></div>`;
}

function labelItem(label,active) {
  const linkedPlacement=active.placements.find(p=>p.id===label.placement);
  const title=label.name || art(linkedPlacement?.artwork)?.title || 'Artwork label';
  const held=label.follow_blocked;
  const status=held?tag('Held: '+held.blockers.map(b=>b.name).join(', '),'attention')
    :label.follow?tag('Anchored to work','good'):tag(label.style);
  const suggest=label.placement?btn('Suggest card','propose-label-card',{id:label.id}):'';
  return `<div class="label-row">
    <button class="place" type="button" ${attr('edit-label',{id:label.id})}><b>${e(title)}</b><span class="label-status">${status}</span></button>
    ${suggest}
  </div>`;
}

export function artworksPanel() {
  const works = state.project.artworks;
  const actions = btn('Compare colors','palette')+btn('Contact sheet','contact-sheet')+btn('Add artwork','add-art',{},'primary');
  return `<div class="workspace">${headline('Artwork library',`${works.length} registered artworks`,actions)}${works.length?`<div class="art-grid">${works.map(item=>artCard(item)).join('')}</div>`:empty('Add a work with its artwork photograph and measured outside size.',btn('Add artwork','add-art',{},'primary'))}</div>`;
}

function shotCard(shotItem,index) {
  return `<article class="shot-card"><div class="thumb"><img src="${photo(shotItem.photo.thumbnail_digest)}" alt="${e(shotItem.name)}"></div><div class="body"><h3>${e(shotItem.name)}</h3><p>${e(shotItem.role)} · ${shotItem.photo.width} × ${shotItem.photo.height} px${index===0?' · primary view':''}</p><div class="toolline">${btn('Inspect','inspect-shot',{id:shotItem.id},shot()?.id===shotItem.id?'primary':'')}${btn('Mark corners','calibrate',{id:shotItem.id})}${btn('Options','shot-options',{id:shotItem.id})}</div></div></article>`;
}

export function sitePanel() {
  const activeWall = wall();
  if (!activeWall) return preLayoutPage('Site photographs','Import room photographs and mark their measured wall planes.');
  const mainShot = shot();
  const actions = btn('Wall observations','wall-observations')+btn('Compare photos','compare-photos')+btn('Add site photograph','add-shot',{},'primary');
  const siteTabs = [['coverage','Coverage'],['detail','Detail'],['exposure','Clipping'],['lighting','Light map'],['samples','Paint samples']];
  const views = activeWall.shots.length ? `<div class="shot-grid">${activeWall.shots.map(shotCard).join('')}</div>` : empty('Import and calibrate at least one site photograph for this wall.',btn('Add site photograph','add-shot'));
  const review = mainShot ? `<div class="review-layout">${panel(mainShot.name,`<img alt="Rectified wall and sample areas" src="${e((state.siteReview==='lighting'?imageQuery(surl('/lighting.png'),{}):imageQuery(surl('/preview.png'),{kind:state.siteReview})))}">`,segment(siteTabs,state.siteReview,'site-review'))}${panel(state.siteReview==='samples'?'Measured paint samples':'Capture checks',`<div id="site-readout">Loading the wall image…</div>`,btn('Measure a line','photo-ruler')+btn('Add paint sample','paint-sample'))}</div>`:'';
  const features = `<div class="review-layout">${panel('Wall features',table(['Feature','Type','Extent'],activeWall.obstacles.map(obstacle=>[ {html:btn(obstacle.name,'obstacle',{id:obstacle.id})}, obstacle.kind, `${qty(obstacle.width)} × ${qty(obstacle.height)} mm` ]),'No measured fixtures or no-hang areas.'),btn('Add feature','obstacle'))}${panel('Existing fixings',table(['Fixing','From left','Above floor'],activeWall.mount_marks.map(mark=>[{html:btn(mark.name,'mark',{id:mark.id})},qty(mark.x,'mm'),qty(mark.y,'mm')]),'No existing wall fixings are marked.'),btn('Add fixing','mark'))}</div>`;
  return `<div class="workspace">${headline('Site photographs',`${activeWall.name} · ${activeWall.shots.length} photographs`,actions)}${layoutPicker()}${views}${review}${features}</div>`;
}

function mountReadout(report) {
  const headers = ['Work','Attachment','Point from datum','Existing mark'];
  const lines = report.works.flatMap(work => work.points.length ? work.points.map(p=>[work.title,p.name,`${qty(p.datum_x)}, ${qty(p.datum_y)} mm`,p.nearby_mark?.name || '—']) : [[work.title,work.mount,'—','—']]);
  return metrics([['Artwork positions',report.work_count],['Fixing points',report.point_count],['Field checks',report.work_without_verified_clearance]])+`<p class="small">Datum ${qty(report.origin.left,'mm')} from left, ${qty(report.origin.from_floor,'mm')} above floor.</p>`+table(headers,lines,'Place artworks to see fixing positions.');
}

function clearReadout(report) {
  const sums=report.summary;
  const obstacles=report.items.flatMap(item=>item.obstructions.map(o=>[item.name,o.name,qty(o.distance,'mm')]));
  const held=report.held_labels?.length
    ? `<h3>Labels held in place while their work moved</h3><p class="small">The anchored card would cover a neighbour or a marked wall item, so it kept its adopted position. Move the work back or re-anchor the card to clear it.</p>`+
      table(['Label','Blocked by','Gap / required'],report.held_labels.map(item=>[
        {html:btn(item.name,'edit-label',{id:item.label})},
        item.blockers.map(b=>b.name).join(', '),
        item.blockers.map(b=>`${qty(b.distance_mm,'mm')} / ${qty(b.required_mm,'mm')}`).join(', '),
      ])) : '';
  return metrics([['Outside wall',sums.off_wall],['Feature conflicts',sums.obstacle_conflicts],['Overlaps',sums.overlaps],['Short gaps',sums.short_gaps],['Labels held',sums.labels_held||0]])+held+table(['Item','Against','Gap'],[...obstacles,...report.pairs.map(p=>[p.first_name,p.second_name,qty(p.distance,'mm')])],'No conflicts at the current gap settings.');
}

function labelsReadout(report) {
  const labels=report.results.map(row=>[{html:btn(row.title || 'Custom label','edit-label',{id:row.id})},row.backing,row.contrast_low==null?'—':`${row.contrast_low}:1`,row.lines.overflow?'Copy overflow':row.needs_attention?'Review':'Clear']);
  return minerals(report)+table(['Caption','Backing','Low contrast','Review'],labels,'Add captions from the wall plan.');
}
function minerals(report) { return metrics([['Wall labels',report.count],['Needs attention',report.needs_attention]]); }
function distributionReadout(report) {
  return metrics([['Artwork coverage',percent(report.artwork_coverage_fraction)],['Overlap',percent(report.overlap_fraction)],['Eye line',qty(report.eye_level_mm,'mm')]])+table(['Artwork','Area overlapping'],report.works.map(row=>[row.name,percent(row.overlap_fraction)]),'Place artworks to measure their distribution.');
}
function surveyReadout(response) {
  return metrics([['Observed outlines',response.count],['Needs review',response.review_count]])+table(['Artwork','State','Observed center','Recorded view'],response.checks.map(row=>[{html:btn(row.title,'survey-picture',{id:row.id})},row.conclusion.status,row.observed.center.map(v=>qty(v)).join(', ')+' mm',row.current_source]),'Mark a work’s four visible corners on an installation photograph to check placement.');
}

export function reviewPanel() {
  if (!layout()) return preLayoutPage('Checks','Review the clear area, fixing points, wall labels, and image evidence.');
  const variant = state.review;
  const mode = variant==='mounts' || variant==='clearances' ? variant : 'elevation';
  const snapshot = layout().installed_surveys?.at(-1);
  let image;
  if (variant==='balance') image=imageQuery(lurl('/balance.png'),{});
  else if (variant==='survey' && snapshot) image=imageQuery(lurl(`/surveys/${snapshot.id}/preview.png`),{});
  else image=planRenderUrl(mode);
  const action = variant==='survey'?btn('Measure installed outline','survey')+btn('Compare with plan','check-installed'):
    variant==='labels'?btn('Place label','add-label')+btn('Print label cards','print-labels'):
    variant==='balance'?'':link('Download diagram',planRenderUrl(mode,true));
  return `<div class="workspace">${headline('Installation checks',`${wall().name} · ${layout().name}`,btn('Save an issue','new-issue',{},'primary'))}<div class="toolline">${layoutPicker()}${segment(reviewTabs,variant,'review-mode')}</div><div class="review-layout">${panel(variant==='survey'?'Observed and planned outlines':'Wall diagram',`<img src="${e(image)}" alt="Annotated wall review">`)}${panel('Review readings','<div id="review-readout">Checking layout…</div>',action)}</div></div>`;
}

function tourResult(report) {
  return metrics([['Stop time',qty(report.viewing_seconds,'s')],['Distance',qty(report.distance_mm/1000,'m')],['Fully visible',report.whole_works.length]])+
    table(['Stop','Works fully visible','Duration'],report.stops.map(row=>[row.name,row.titles.join(', ') || '—',qty(row.duration,'s')]))+
    (report.out_of_view_works.length?`<p class="note">Outside every stop: ${e(report.out_of_view_works.join(', '))}</p>`:'');
}
function viewerResult(report) {
  return metrics([['Fully visible',report.works_fully_visible],['Partial view',report.works_partially_visible],['Outside view',report.works_out_of_view]])+
    table(['Artwork','Clear in frame','Obstruction'],report.works.map(row=>[row.name,percent(row.clear_in_view),row.obstacles.map(w=>w.name).join(', ') || '—']),'No artworks placed.');
}

export function viewPanel() {
  if (!layout()) return preLayoutPage('Viewing positions','Simulate a viewer’s perspective from recorded floor positions.');
  const active=layout(), viewer=station(), tour=route();
  const viewerContent=viewer?`<div class="review-layout">${panel(viewer.name,`<img src="${e(imageQuery(lurl(`/stations/${viewer.id}/render.png`),{}))}" alt="Preview from ${e(viewer.name)}">`)}${panel('Artwork visibility','<div id="viewer-readout">Projecting sightlines…</div>',btn('Edit viewpoint','station',{id:viewer.id}))}</div>`:empty('Add a viewpoint with measured eye height and distance from wall.',btn('Add viewpoint','station',{},'primary'));
  const tourContent=tour?`<div class="review-layout">${panel(tour.name,`<img src="${e(imageQuery(lurl(`/routes/${tour.id}/plan.png`),{}))}" alt="Floor plan showing the recorded visit sequence">`)}${panel('Visit sequence','<div id="tour-readout">Checking stops…</div>',btn('Edit sequence','route',{id:tour.id}))}</div>`:'';
  const selectTour=active.visit_sequences?.length?`<select class="compact-select" aria-label="Visit sequence" data-change="route">${opts(active.visit_sequences,state.routeId)}</select>`:'';
  return `<div class="workspace">${headline('Viewing positions',`${wall().name} · ${layout().name}`,btn('Add viewpoint','station',{},'primary'))}<div class="toolline">${layoutPicker()}${active.stations.length?`<select class="compact-select" data-change="station" aria-label="Viewing position">${opts(active.stations,state.stationId)}</select>`:''}</div>${viewerContent}${panel('Visit sequence',`${selectTour}${tour?'':'<p>Combine saved viewpoints to see how much of the hang a visitor can see along a route.</p>'}`,btn('New sequence','route'))}${tourContent}</div>`;
}

export function issuePanel() {
  if (!layout()) return preLayoutPage('Issues','Save photography, installation drawings and verified issue downloads.');
  const issues=state.issues.map(issue=>`<article class="issue-card"><h3>${e(issue.name)}</h3><p>${e(issue.wall_name)} · ${e(issue.layout_name)} · Revision ${issue.revision} · ${e(shortDate(issue.created))}</p><div class="toolline">${Object.keys(issue.artifacts).filter(name=>['issue.zip','installation.pdf','proof.pdf','fixings.csv','elevation.png'].includes(name)).map(name=>link({'issue.zip':'Issue ZIP','installation.pdf':'Installation PDF','proof.pdf':'Proof PDF','fixings.csv':'Fixing CSV','elevation.png':'Elevation PNG'}[name],purl(`/issues/${issue.id}/${name}`),name==='issue.zip'?'primary':'')).join('')}</div></article>`).join('');
  return `<div class="workspace">${headline('Saved issues',`${state.issues.length} saved release${state.issues.length===1?'':'s'} for ${state.project.name}`,(state.issues.length>1?btn('Compare issues','compare-issues'):'')+btn('Verify received ZIP','verify-issue')+btn('Save an issue','new-issue',{},'primary'))}${layoutPicker()}<div class="issue-list" style="margin-top:15px">${issues || empty('Save the current layout as an installation issue or a client proof.')}</div></div>`;
}

export function loadPanelDetails() {
  if (state.page==='sites' && shot()) {
    if (state.siteReview==='samples') {
      loadInside('site-readout',surl('/samples'),report=>table(['Sample','Observed','Reference','Support'],report.samples.map(row=>[row.name,row.observed||'—',row.reference,percent(row.clear_fraction)]),'No paint samples marked on this photo.'));
    } else if (state.siteReview==='lighting') {
      loadInside('site-readout',surl('/lighting'),report=>metrics([['Observed',percent(report.coverage)],['Light spread',qty(report.contrast_per_80_percent,'stops')],['Lower light',percent(report.lower_fraction)]])+table(['Tile','Relative median','Variation'],report.zones.map(row=>[`${row.row}.${row.column}`,qty(row.median_stops,'stops'),qty(row.variation_stops,'stops')])));
    } else {
      loadInside('site-readout',surl('/review'),report=>metrics([['Visible wall',percent(report.clear_fraction)],['Photograph support',percent(report.supported_fraction)],['Foreground',percent(report.foreground_fraction)]])+table(['Tile','Visible','Clipping'],report.cells.map(row=>[`${row.row}.${row.column}`,percent(row.clear),percent(row.clipped)])));
    }
  }
  if (state.page==='review' && layout()) {
    const variant=state.review;
    const generators={mounts:mountReadout,clearances:clearReadout,labels:labelsReadout,balance:distributionReadout,survey:surveyReadout};
    const path=variant==='balance'?'/balance':variant==='survey'?'/surveys':`/audit?kind=${variant}`;
    loadInside('review-readout',lurl(path),generators[variant]);
  }
  if (state.page==='view' && station()) loadInside('viewer-readout',lurl(`/stations/${station().id}/readout`),viewerResult);
  if (state.page==='view' && route()) loadInside('tour-readout',lurl(`/routes/${route().id}/report`),tourResult);
  const img = document.querySelector('#scene-img');
  if (img) {
    const update = () => {
      const markers=img.parentElement.querySelector('.scene-points');
      if (!markers) return;
      const supplied = img.naturalHeight ? img.naturalWidth/img.naturalHeight : img.clientWidth/img.clientHeight;
      const local = img.clientWidth/img.clientHeight;
      if (local>supplied) {
        const width=img.clientHeight*supplied;
        markers.style.left=`${(img.clientWidth-width)/2}px`;
        markers.style.right=markers.style.left;
      } else {
        markers.style.left='0'; markers.style.right='0';
      }
    };
    if (img.complete) update();
    else img.addEventListener('load',update,{once:true});
    img.addEventListener('error',()=>{ img.alt='Preview unavailable. Try another view or reload the layout.'; },{once:true});
  }
}
