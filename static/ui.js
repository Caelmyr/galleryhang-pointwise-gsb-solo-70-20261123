export const state = {
  project: null, layoutId: null, wallId: null, shotId: null, stationId: null, routeId: null,
  page: 'library', planView: 'photo', review: 'clearances', selections: [], sceneVersion: 0,
  siteReview: 'coverage', shotCompare: null, issues: [], projects: [], surveyDraft: null,
};
const esc = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'};
export const e = value => String(value ?? '').replace(/[&<>"']/g, char => esc[char]);
export const photo = digest => `/api/photo/${encodeURIComponent(digest)}.png`;
export const purl = suffix => `/api/projects/${state.project.id}${suffix || ''}`;
export const lurl = suffix => purl(`/layouts/${layout()?.id}${suffix || ''}`);
export const wurl = suffix => purl(`/walls/${wall()?.id}${suffix || ''}`);
export const surl = suffix => wurl(`/shots/${shot()?.id}${suffix || ''}`);
export const layout = () => state.project?.layouts.find(item => item.id === state.layoutId);
export const wall = () => state.project?.walls.find(item => item.id === state.wallId);
export const shot = () => wall()?.shots.find(item => item.id === state.shotId);
export const art = id => state.project?.artworks.find(item => item.id === id);
export const station = () => layout()?.stations.find(item => item.id === state.stationId);
export const route = () => (layout()?.visit_sequences || []).find(item => item.id === state.routeId);
export const placed = () => (layout()?.placements || []).filter(item => state.selections.includes(item.id));
export const qty = (value, unit = '') => value == null ? '—' : `${Number(value).toLocaleString('en',{maximumFractionDigits:2})}${unit ? ' '+unit : ''}`;
export const percent = value => value == null ? '—' : qty(value * 100) + '%';
export const shortDate = date => new Intl.DateTimeFormat(undefined, {dateStyle:'medium',timeStyle:'short'}).format(new Date(date));
export const checked = value => value ? ' checked' : '';

export function opts(rows, selected, id = 'id', label = 'name') {
  return rows.map(row => `<option value="${e(row[id])}"${row[id] === selected ? ' selected' : ''}>${e(row[label])}</option>`).join('');
}
export function choices(values, selected) {
  return opts(values.map(value => Array.isArray(value) ? {id:value[0],name:value[1]} : {id:value,name:value}),selected);
}
export function attr(action, attrs = {}) {
  const extra = Object.entries(attrs).map(([key, value]) => ` data-${key}="${e(value)}"`).join('');
  return `data-do="${e(action)}"${extra}`;
}
export function btn(label, action, opts = {}, kind = '') {
  return `<button type="button" class="button ${e(kind)}" ${attr(action, opts)}>${e(label)}</button>`;
}
export function link(label, href, kind = '') {
  return `<a class="button ${e(kind)}" href="${e(href)}" download>${e(label)}</a>`;
}
export function headline(title, details, actions = '') {
  return `<div class="headline"><div><h1>${e(title)}</h1>${details ? `<p>${e(details)}</p>` : ''}</div>${actions ? `<div class="headline-actions">${actions}</div>` : ''}</div>`;
}
export function panel(title, body, actions = '', attrs = '') {
  return `<section class="panel" ${attrs}><div class="panel-head"><h2>${e(title)}</h2>${actions ? `<div class="toolline">${actions}</div>` : ''}</div>${body}</section>`;
}
export function empty(message, action = '') {
  return `<div class="empty-view"><p>${e(message)}</p>${action}</div>`;
}
export function tag(label, kind = '') {
  return `<span class="tag ${e(kind)}">${e(label)}</span>`;
}
export function table(headers, rows, emptyMessage = '') {
  if (!rows.length && emptyMessage) return `<p class="small">${e(emptyMessage)}</p>`;
  return `<div class="table-wrap"><table><thead><tr>${headers.map(value => `<th scope="col">${e(value)}</th>`).join('')}</tr></thead><tbody>${rows.map(cells => `<tr>${cells.map(cell => `<td>${typeof cell === 'object' && cell?.html != null ? cell.html : e(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
export function metrics(values) {
  return `<div class="metric-grid">${values.map(([title, value]) => `<div class="metric"><strong>${e(value)}</strong><span>${e(title)}</span></div>`).join('')}</div>`;
}
export function facts(values) {
  return `<dl class="facts">${values.map(([key,value]) => `<div><dt>${e(key)}</dt><dd>${e(value)}</dd></div>`).join('')}</dl>`;
}
export function segment(options, selected, action) {
  return `<div class="segment">${options.map(([id, title]) => `<button type="button" ${attr(action,{value:id})} aria-pressed="${id===selected}">${e(title)}</button>`).join('')}</div>`;
}

export async function request(url, {method='GET', body, file, fields} = {}) {
  const options = {method, headers:{Accept:'application/json'}};
  if (file) {
    const upload = new FormData();
    upload.set('file',file);
    if (fields) upload.set('metadata',JSON.stringify(fields));
    options.body = upload;
  } else if (body !== undefined) {
    options.headers['Content-Type']='application/json';
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  if (!response.ok) {
    let problem;
    try { problem = (await response.json()).error; } catch { /* Non-JSON response. */ }
    const error = new Error(problem || `The studio could not finish that request (${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

export function applyProject(project, {layoutId, wallId} = {}) {
  state.project = project;
  if (layoutId) state.layoutId = layoutId;
  if (wallId) state.wallId = wallId;
  const activeLayout = layout();
  state.wallId = activeLayout?.wall || wallId || wall()?.id || project.walls[0]?.id || null;
  state.layoutId = activeLayout?.id || project.layouts.find(item => item.wall===state.wallId)?.id || null;
  if (!shot()) state.shotId = wall()?.shots[0]?.id || null;
  if (!station()) state.stationId = layout()?.stations[0]?.id || null;
  if (!route()) state.routeId = layout()?.visit_sequences?.[0]?.id || null;
  state.selections = state.selections.filter(id => layout()?.placements.some(item => item.id===id));
  state.sceneVersion++;
  localStorage.setItem('galleryhang.lastProject',project.id);
}

export async function change(url, fields, {method = 'POST', file, select, quiet} = {}) {
  const body = {revision:state.project.revision,...fields};
  const result = await request(url,file ? {method,file,fields:body} : {method,body});
  applyProject(result.project,select ? select(result.item) : {});
  if (!quiet) notify(method==='DELETE' ? 'Removed' : 'Saved');
  return result.item;
}

let timer;
export function notify(text, failed = false) {
  const toast = document.querySelector('#toast');
  toast.textContent = text;
  toast.classList.toggle('failed',failed);
  toast.hidden = false;
  clearTimeout(timer);
  timer = setTimeout(() => { toast.hidden = true; },failed ? 7000 : 2800);
}

export function loadInside(key, url, fill) {
  const target = document.querySelector(`#${key}`);
  if (!target) return;
  request(url).then(result => {
    if (target.isConnected) target.innerHTML = fill(result);
  }).catch(error => {
    if (target.isConnected) target.innerHTML = `<p class="error">${e(error.message)}</p>`;
  });
}

export function field(name,label,value,opts = {}) {
  const {type='text',wide=false,required=false,options,attrs = {},disabled = false} = opts;
  const val = value ?? '';
  const supplied = Object.entries(attrs).map(([key,v]) => ` ${e(key)}="${e(v)}"`).join('');
  const attr = `${required ? ' required' : ''}${disabled ? ' disabled' : ''}${supplied}`;
  let input;
  if (type==='textarea') input=`<textarea name="${e(name)}"${attr}>${e(val)}</textarea>`;
  else if (type==='select') input=`<select name="${e(name)}"${attr}>${choices(options,val)}</select>`;
  else if (type==='checkbox') input=`<input name="${e(name)}" type="checkbox"${attr}${checked(val)}>`;
  else input=`<input name="${e(name)}" type="${e(type)}" value="${type==='file' ? '' : e(val)}"${attr}>`;
  return `<label class="field${wide?' wide':''}"><span>${e(label)}</span>${input}</label>`;
}
export function numeric(name,label,value,limits = {}) {
  return field(name,label,value,{type:'number',required:true,attrs:{step:'any',...limits}});
}
export function formValues(element) {
  const result = {};
  for (const input of [...element.elements]) {
    if (!input.name || input.disabled) continue;
    if (input.type==='number') result[input.name] = Number(input.value);
    else if (input.type==='checkbox') result[input.name] = input.checked;
    else if (input.type==='file') result[input.name] = input.files[0];
    else result[input.name] = input.value;
  }
  return result;
}

export function openDialog(title, fields, submit, {wide=false,footer='', confirm='Save', simple=false}={}) {
  const dialog = document.querySelector('#dialog');
  dialog.classList.toggle('wide',wide);
  dialog.innerHTML=`<h2>${e(title)}</h2><form><p class="error dialog-error" hidden></p><div class="${simple?'dialog-content':'field-grid'}">${fields}</div>${footer}<div class="dialog-actions"><button type="button" class="button" data-do="close-dialog">${submit?'Cancel':'Close'}</button>${submit?`<button type="submit" class="button primary">${e(confirm)}</button>`:''}</div></form>`;
  const form = dialog.querySelector('form');
  if (!dialog.open) dialog.showModal();
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!submit) return;
    const errorLine = form.querySelector('.dialog-error');
    const sender = form.querySelector('[type="submit"]');
    sender.disabled = true;
    errorLine.hidden = true;
    try {
      const outcome = await submit(formValues(form),form);
      if (outcome !== false) dialog.close();
    } catch (error) {
      errorLine.textContent = error.message;
      errorLine.hidden = false;
      errorLine.scrollIntoView({block:'nearest'});
    } finally {
      sender.disabled = false;
    }
  });
  return form;
}

export function collectImagePoints(container, initial, limit) {
  let points = initial.map(point => [...point]);
  const picture = container.querySelector('img');
  const layer = container.querySelector('.edit-marks');
  const full = container.parentNode;
  const btnReset = full.querySelector('[data-point-reset]');
  const btnBack = full.querySelector('[data-point-back]');
  const draw = () => {
    layer.innerHTML=points.map(([x,y],i) => `<i class="pick-point" aria-hidden="true" style="left:${x*100}%;top:${y*100}%">${i+1}</i>`).join('');
    if (btnBack) btnBack.disabled = !points.length;
    if (btnReset) btnReset.disabled = !points.length;
  };
  picture.addEventListener('click', event => {
    const box = picture.getBoundingClientRect();
    if (points.length>=limit) points=[];
    points.push([Math.min(1,Math.max(0,(event.clientX-box.left)/box.width)),
                 Math.min(1,Math.max(0,(event.clientY-box.top)/box.height))]);
    draw();
  });
  if (btnReset) btnReset.addEventListener('click',() => { points=[]; draw(); });
  if (btnBack) btnBack.addEventListener('click',() => { points.pop(); draw(); });
  draw();
  return () => points.map(xy => xy.map(value => Number(value.toFixed(6))));
}
