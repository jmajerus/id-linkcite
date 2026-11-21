const $ = id => document.getElementById(id);
let registry = {}, state = null, active = 'draft_markdown', dirty = false, busy = false;
let working = {draft_markdown:'', private_markdown:''}, revision = null, timer, previewSeq = 0;

async function api(path, body) {
  const options = body === undefined ? {} : {method:'POST', headers:{
    'Content-Type':'application/json','X-LinkCite-Editor':'1'}, body:JSON.stringify(body)};
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw Error(data.error || 'Request failed');
  return data;
}
const endpoint = (name, iri, more={}) => '/api/' + name + '?' + new URLSearchParams({iri,...more});
function message(text) { $('status').textContent=text; }
function setDirty() { dirty = working.draft_markdown !== state.draft_markdown || working.private_markdown !== state.private_markdown; }
function filter() {
  const query=$('search').value.toLowerCase();
  const matches=Object.entries(registry).filter(([iri,label])=>(iri+' '+label).toLowerCase().includes(query));
  const shown=matches.slice(0,500);
  if (state && !shown.some(([iri])=>iri===state.iri)) shown.unshift([state.iri,registry[state.iri]]);
  $('resource').replaceChildren(...shown.map(([iri,label])=>new Option(label+' — '+iri,iri)));
  $('search').title=matches.length>500?'Showing first 500 results. Refine the search.':matches.length+' results';
  if (state && shown.some(([iri])=>iri===state.iri)) $('resource').value=state.iri;
  else $('resource').selectedIndex=-1;
}
async function preview() {
  const seq=++previewSeq, markdown=$('markdown').value;
  try {
    const {html}=await api('/api/preview',{markdown});
    if(seq!==previewSeq)return;
    // No scripts, network images, or outgoing clicks in a private preview.
    $('preview').srcdoc="<!doctype html><html><head><meta charset='utf-8'><meta http-equiv='Content-Security-Policy' content=\"default-src 'none'\"></head><body><div inert>"+html+"</div></body></html>";
  } catch(e) { if(seq===previewSeq)message(e.message); }
}
function schedule() { clearTimeout(timer); timer=setTimeout(preview,220); }
function tab(field) {
  active=field;
  $('content-tab').setAttribute('aria-pressed',String(field==='draft_markdown'));
  $('notes-tab').setAttribute('aria-pressed',String(field==='private_markdown'));
  $('hint').textContent=field==='private_markdown'?'Private notes are never included in publication.':'Draft content is private until explicitly published.';
  $('markdown').value=working[field]; schedule();
}
function apply(value) {
  state=value; working={draft_markdown:value.draft_markdown,private_markdown:value.private_markdown};dirty=false;
  $('resource-label').textContent=registry[value.iri]+' — '+value.iri;
  $('view-page').href=value.iri; $('view-page').hidden=false;
  // A reload or bookmark reopens this term (history() below is the revision list, not window.history).
  window.history.replaceState(null,'','?'+new URLSearchParams({iri:value.iri}));
  $('workspace').hidden=false; $('change-note').value='';
  $('publication').textContent=value.published_at?'Published '+value.published_at+(value.published_markdown!==value.draft_markdown?' · unpublished draft changes':''):'Not published';
  tab(active);
}
async function history() {
  revision=null;$('historical').textContent='';$('restore').disabled=true;
  const records=await api(endpoint('history',state.iri));
  $('history').replaceChildren(...records.map(r=>new Option('#'+r.revision_number+' · '+r.action+' · '+r.changed_at+' · '+r.changed_by+(r.change_note?' · '+r.change_note:''),r.revision_number)));
  $('history').selectedIndex=-1;
}
async function open(iri) {
  if(!iri||busy)return;
  if(dirty&&!confirm('Discard unsaved edits?')) { $('resource').value=state.iri;return; }
  busy=true; $('resource').disabled=true; $('markdown').disabled=true;
  try{apply(await api(endpoint('state',iri)));await history();message('Resource loaded.');}
  catch(e){message(e.message);}
  finally{busy=false; $('resource').disabled=false; $('markdown').disabled=false; filter();}
}
async function change(action) {
  if(!state||busy)return;
  if(action==='publish'&&!confirm('Publish the current public draft? Private notes remain private.'))return;
  if(action==='unpublish'&&!confirm('Remove the published editorial content?'))return;
  if(action==='restore'&&(!revision||!confirm('Restore draft and private notes from this revision? Published content is unchanged.')))return;
  busy=true; $('markdown').disabled=true;
  for(const id of ['save','publish','unpublish','restore'])$(id).disabled=true;
  try{
    const value=await api('/api/change',{iri:state.iri,revision_number:state.revision_number,action,...working,
      change_note:$('change-note').value,restore_revision:revision});
    apply(value);await history();message(action==='publish'?'Published. View page shows it now.':action==='restore'?'Draft and notes restored.':action==='unpublish'?'Unpublished.':'Saved.');
  }catch(e){message(e.message+' Your unsaved text is still here.');}
  finally{busy=false;$('markdown').disabled=false;for(const id of ['save','publish','unpublish'])$(id).disabled=false;$('restore').disabled=!revision;}
}
$('search').addEventListener('input',filter);
$('resource').addEventListener('change',()=>open($('resource').value));
$('markdown').addEventListener('input',()=>{working[active]=$('markdown').value;setDirty();schedule();});
$('content-tab').onclick=()=>tab('draft_markdown');$('notes-tab').onclick=()=>tab('private_markdown');
$('refresh-preview').onclick=preview;
$('toolbar').addEventListener('click',e=>{
  const fmt=e.target.closest('[data-format]')?.dataset.format;if(!fmt)return;
  const t=$('markdown'), start=t.selectionStart,end=t.selectionEnd,selected=t.value.slice(start,end)||'text';
  const next={bold:'**'+selected+'**',italic:'*'+selected+'*',heading:'\n## '+selected,list:'\n- '+selected,link:'['+selected+'](https://)'}[fmt];
  t.setRangeText(next,start,end,'end');t.dispatchEvent(new Event('input'));t.focus();
});
for(const action of ['save','publish','unpublish','restore'])$(action).onclick=()=>change(action);
$('history').addEventListener('change',async()=>{
  const iri=state.iri, n=Number($('history').value);revision=null;$('restore').disabled=true;
  if(!n)return;
  try{const r=await api(endpoint('revision',iri,{revision:n}));
    if(state.iri!==iri||Number($('history').value)!==n)return;
    revision=n;$('historical').textContent='PUBLIC DRAFT\n'+r.draft_markdown+'\n\nPRIVATE NOTES\n'+r.private_markdown+'\n\nPUBLISHED MARKDOWN AT THAT REVISION\n'+(r.published_markdown??'(unpublished)');$('restore').disabled=false;
  }catch(e){message(e.message);}
});
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
(async()=>{try{registry=await api('/api/resources');filter();const iri=new URLSearchParams(location.search).get('iri');if(registry[iri]){await open(iri);}else message('Select a resource to edit.');}catch(e){message(e.message);}})();
