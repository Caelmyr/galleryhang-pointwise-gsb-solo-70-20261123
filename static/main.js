import {state,e,photo,purl,lurl,wurl,layout,wall,shot,art,applyProject,request,change,notify,openDialog} from './ui.js';
import {artworksPanel,drawNavigation,issuePanel,libraryPanel,loadPanelDetails,planPanel,reviewPanel,sitePanel,viewPanel} from './panels.js';
import {arrangement,artDialog,artPreview,calibrateView,confirmDelete,cropArtwork,duplicateArtwork,duplicateLayout,exhibitionDialog,
        foregroundView,labelDialog,layoutComparison,layoutDialog,layoutOptions,markDialog,obstacleDialog,
        paintSample,photoRuler,pickArtwork,placementDialog,shotDialog,stationDialog,wallDialog} from './editing.js';
import {addObservation,colorsDialog,compareIssuedGeometry,comparePhotos,contactSheetDialog,installationCompare,issueDialog,printLabels,surveyOutline,surveyPicture,verifyIssue,visitRoute,wallObservations} from './review.js';

const root=document.querySelector('#page');
const dialog=document.querySelector('#dialog');
const panels={library:libraryPanel,plan:planPanel,sites:sitePanel,artworks:artworksPanel,view:viewPanel,review:reviewPanel,issues:issuePanel};
let currentGeneration=0;

function update() {
  drawNavigation();
  root.innerHTML=(panels[state.page] || panels.library)();
  loadPanelDetails();
}

async function refresh() {
  const intent=++currentGeneration;
  if (state.page==='library') state.projects=(await request('/api/projects')).projects;
  if (state.page==='issues' && state.project) state.issues=(await request(purl('/issues'))).issues;
  if (intent===currentGeneration) update();
}

function activePage(page) {
  state.page=page;
  if (state.project && page!=='library') {
    history.replaceState(null,'',`#${encodeURIComponent(state.project.id)}/${page}`);
  } else {
    history.replaceState(null,'',location.pathname+location.search);
  }
}

async function navigate(page) {
  activePage(page);
  await refresh();
  root.focus({preventScroll:true});
  window.scrollTo({top:0});
}

async function openProject(id) {
  const {project}=await request(`/api/projects/${id}`);
  state.wallId=state.layoutId=state.shotId=state.stationId=state.routeId=null;
  state.selections=[];
  applyProject(project);
  state.planView=wall()?.shots.length?'photo':'elevation';
  await navigate('plan');
}

async function demoProject() {
  const {project}=await request('/api/demo',{method:'POST'});
  await openProject(project.id);
}

function ensure(condition,message) {
  if (!condition) throw new Error(message);
}

function switchLayout(value) {
  state.layoutId=value;
  state.selections=[];
  applyProject(state.project);
  update();
}

async function deleteActiveLayout() {
  const id=layout().id;
  confirmDelete('this layout',lurl(''),async()=>{
    state.selections=[];
    await refresh();
  });
}

async function markPrimary(id) {
  await change(wurl(`/shots/${id}/primary`),{});
  state.shotId=id;
  dialog.close();
  await refresh();
}

const actions={
  library:()=>navigate('library'), navigate:({page})=>navigate(page),
  'open-project':({id})=>openProject(id), 'open-demo':()=>demoProject(),
  'new-project':()=>exhibitionDialog(null,async()=>{activePage('plan');await refresh();}),
  'edit-project':()=>exhibitionDialog(state.project,refresh),
  'close-dialog':()=>dialog.close(),
  'new-wall':()=>wallDialog(null,refresh), 'edit-wall':()=>wallDialog(wall(),refresh),
  'delete-wall':()=>confirmDelete('the measured wall',wurl(''),refresh),
  'new-layout':()=>layoutDialog(null,refresh), 'edit-layout':()=>layoutDialog(layout(),refresh),
  'layout-options':()=>layoutOptions(refresh), 'duplicate-layout':()=>duplicateLayout(refresh),
  'compare-layout':({id})=>layoutComparison(id), 'delete-layout':()=>deleteActiveLayout(),
  'plan-mode':({value})=>{state.planView=value;update();},
  'site-review':({value})=>{state.siteReview=value;update();},
  'review-mode':({value})=>{state.review=value;update();},
  'add-art':()=>artDialog(null,refresh),
  'edit-art':({id})=>artDialog(art(id),refresh), 'art-preview':({id})=>artPreview(id),
  'delete-art':({id})=>confirmDelete('the artwork',purl(`/artworks/${id}`),refresh),
  'duplicate-art':({id})=>duplicateArtwork(id,refresh), 'crop':({id})=>cropArtwork(id,refresh),
  'choose-art':()=>pickArtwork(),
  'place-work':({id})=>{ensure(layout(),'Create a wall layout before placing artwork.');placementDialog(id,null,async()=>{await navigate('plan');});},
  'pick-placement':({id})=>{state.selections=state.selections.includes(id)?state.selections.filter(value=>value!==id):[...state.selections,id];update();},
  'clear-selection':()=>{state.selections=[];update();},
  'edit-placement':({id})=>{const item=layout().placements.find(p=>p.id===id);placementDialog(item.artwork,item,refresh);},
  'remove-placement':({id})=>confirmDelete('the artwork placement',lurl(`/placements/${id}`),refresh),
  arrange:()=>arrangement(refresh),
  'add-shot':()=>shotDialog(null,refresh),
  'inspect-shot':({id})=>{state.shotId=id;update();},
  'shot-options':({id})=>shotDialog(wall().shots.find(row=>row.id===id),refresh),
  calibrate:({id})=>calibrateView(id,refresh), foreground:({id})=>foregroundView(id,refresh),
  'clear-foreground':async({id})=>{await change(wurl(`/shots/${id}`),{foreground:[]},{method:'PATCH'});dialog.close();await refresh();},
  'primary-shot':({id})=>markPrimary(id),
  'delete-shot':({id})=>confirmDelete('the site photograph',wurl(`/shots/${id}`),refresh),
  'wall-observations':()=>{ensure(shot() && layout(),'Import a site photograph with a working layout first.');return wallObservations();},
  'add-observation':()=>addObservation(refresh), 'delete-observation':({id})=>confirmDelete('the wall observation',lurl(`/observations/${id}`),refresh),
  'photo-ruler':()=>photoRuler(), 'paint-sample':()=>paintSample(refresh),
  obstacle:({id})=>obstacleDialog(id,refresh), 'delete-obstacle':({id})=>confirmDelete('the wall feature',wurl(`/obstacle/${id}`),refresh),
  mark:({id})=>markDialog(id,refresh), 'delete-mark':({id})=>confirmDelete('the existing fixing mark',wurl(`/fixing/${id}`),refresh),
  'add-label':()=>labelDialog(null,refresh), 'edit-label':({id})=>labelDialog(id,refresh),
  'print-labels':()=>printLabels(), 'delete-label':({id})=>confirmDelete('the wall label',lurl(`/labels/${id}`),refresh),
  station:({id})=>stationDialog(id,refresh), 'delete-station':({id})=>confirmDelete('the viewing position',lurl(`/stations/${id}`),refresh),
  route:({id})=>{ensure(layout()?.stations.length,'Add a viewing position first.');visitRoute(id,refresh);},
  'delete-route':({id})=>confirmDelete('this visit sequence',lurl(`/routes/${id}`),refresh),
  'compare-photos':()=>{ensure(wall()?.shots.length>=2,'Add a second calibrated view to compare the photographs.');comparePhotos();},
  'check-installed':()=>{ensure(wall()?.shots.length>=2,'Add a reference view and an installed view first.');installationCompare();},
  survey:()=>{ensure(wall()?.shots.length,'Import the installation photograph first.');surveyOutline(refresh);},
  'survey-picture':({id})=>surveyPicture(id), 'survey-reading':({id,reading})=>surveyPicture(id,reading),
  'survey-reshoot':({id})=>{const found=layout()?.installed_surveys?.find(row=>row.id===id);if (!found) throw new Error('The installed outline was not found.');dialog.close();surveyOutline(refresh,found);},
  'delete-survey':({id})=>confirmDelete('the measured outline',lurl(`/surveys/${id}`),refresh),
  palette:()=>{ensure(state.project.artworks.length,'Add at least one artwork first.');colorsDialog();},
  'contact-sheet':()=>{ensure(state.project.artworks.length,'Add at least one artwork first.');contactSheetDialog();},
  'new-issue':()=>issueDialog(async()=>{activePage('issues');await refresh();}), 'verify-issue':()=>verifyIssue(), 'compare-issues':()=>compareIssuedGeometry(),
};

const selects={layout:switchLayout,shot:value=>{state.shotId=value;update();},
               station:value=>{state.stationId=value;update();},route:value=>{state.routeId=value;update();}};

document.addEventListener('click',async event=>{
  const control=event.target.closest('button[data-do]');
  if (!control) return;
  const action=actions[control.dataset.do];
  if (!action || control.disabled) return;
  control.disabled=true;
  try { await action(control.dataset); }
  catch (error) { notify(error.message,true); }
  finally { control.disabled=false; }
});

document.addEventListener('change',event=>{
  const select=event.target.closest('select[data-change]');
  if (select) selects[select.dataset.change]?.(select.value);
});

async function boot() {
  const [id,section]=location.hash.slice(1).split('/');
  if (id && /^[0-9a-f]{16}$/.test(id)) {
    try {
      await openProject(id);
      if (section && Object.hasOwn(panels,section) && section!=='plan') await navigate(section);
      return;
    } catch { activePage('library'); }
  }
  await navigate('library');
}

boot().catch(error=>{root.innerHTML=`<div class="workspace"><p class="error">${e(error.message)}</p></div>`;});
