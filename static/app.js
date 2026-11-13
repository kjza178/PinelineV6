
const STAGES=[
  {key:'download',name:'Tải video',sub:'yt-dlp · H.264',opt:false},
  {key:'asr',name:'Nhận diện lời',sub:'Fun-ASR-Nano · Whisper · Paraformer',opt:false},
  {key:'translate',name:'Dịch',sub:'gemma4 · bảng nhân vật + xưng hô',opt:false},
  {key:'tts',name:'Lồng tiếng Việt',sub:'VieNeu/OmniVoice · clone',opt:false},
  {key:'ocr',name:'OCR chữ trên hình',sub:'RapidOCR → dịch overlay',opt:true},
  // 'separate' (Tách nền) ĐÃ BỎ khỏi danh sách bước: Render tự chạy Demucs khi cần.
  {key:'mux',name:'Ghép + xuất',sub:'che watermark/hardsub + sub + trộn',opt:false},
];
let selected=null, expanded=new Set(), lastFull=null, cache=[], lastDetailSig=null, logOpen=true;
let composeOpen=false;  // true chỉ khi user bấm + Thêm link

const fmtDur=s=>{ if(!s) return ''; const m=Math.floor(s/60),ss=Math.floor(s%60); return m+':'+String(ss).padStart(2,'0'); };
const statClass=s=>s==='done'?'ok':s==='running'?'run':s==='failed'?'err':'wait';
const statText=s=>s==='done'?'Xong':s==='running'?'Đang chạy':s==='failed'?'Lỗi':'Chờ';
const escapeHtml=s=>String(s).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
function fmtSize(b){ if(!b) return ''; b=+b; if(b>1048576) return (b/1048576).toFixed(1)+' MB'; if(b>1024) return (b/1024).toFixed(0)+' KB'; return b+' B'; }
function hhmm(iso){ if(!iso) return ''; try{return new Date(iso).toTimeString().slice(0,5);}catch(e){return '';} }

function parseUrls(raw){
  const seen=new Set(), out=[];
  String(raw||'').split(/\r?\n/).forEach(line=>{
    let s=line.trim(); if(!s) return;
    // bỏ số thứ tự "1. url" / "- url"
    s=s.replace(/^\s*[-*•]\s+/,'').replace(/^\s*\d+[.)]\s+/,'');
    // lấy http(s) nếu dòng lẫn chữ
    const m=s.match(/https?:\/\/\S+/i);
    if(m) s=m[0].replace(/[),.;]+$/,'');
    if(!/^https?:\/\//i.test(s)) return;
    if(seen.has(s)) return; seen.add(s); out.push(s);
  });
  return out;
}
function updateUrlCount(){
  const n=parseUrls(document.getElementById('url').value).length;
  const el=document.getElementById('url-count');
  if(el){ el.textContent=n?`${n} link`:'0 link'; el.classList.toggle('on',n>0); }
  const btn=document.getElementById('add');
  if(btn && !btn.dataset.busy){
    btn.textContent=n>1?`+ Thêm ${n} vào hàng chờ`:(n===1?'+ Thêm vào hàng chờ':'+ Thêm vào hàng chờ');
    btn.disabled=!n;
  }
}
async function addJob(){
  const box=document.getElementById('url');
  const urls=parseUrls(box.value); if(!urls.length) return;
  const btn=document.getElementById('add');
  const target=document.getElementById('target').value;
  btn.disabled=true; btn.dataset.busy='1';
  let ok=0, fail=0, first=null;
  try{
    for(let i=0;i<urls.length;i++){
      btn.textContent=`Đang thêm ${i+1}/${urls.length}…`;
      try{
        const r=await fetch('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({url:urls[i],target})});
        const d=await r.json();
        if(!r.ok){ fail++; continue; }
        ok++;
        const id=(d.job_ids&&d.job_ids[0])||d.job_id;
        if(id && !first) first=id;
      }catch(e){ fail++; }
    }
    box.value=''; updateUrlCount();
    // Giữ màn Thêm link (full body) để dán tiếp — không nhảy sang job
    selected=null;
    await refresh();
    showCompose();
    if(fail) alert(`Đã thêm ${ok} link, lỗi ${fail}.`);
  }catch(e){ alert('Lỗi tạo job: '+e); }
  delete btn.dataset.busy; btn.disabled=false; updateUrlCount();
}
async function addLocalPath(){
  const el=document.getElementById('local-path');
  const raw=(el&&el.value||'').trim();
  if(!raw){ alert('Nhập đường dẫn file video (1 dòng / 1 file)'); return; }
  const paths=raw.split(/\r?\n/).map(s=>s.trim().replace(/^["']|["']$/g,'')).filter(Boolean);
  if(!paths.length) return;
  const target=(document.getElementById('target')||{}).value||'Tiếng Việt';
  const move=!!(document.getElementById('local-move')||{}).checked;
  const btn=document.getElementById('add-local');
  const st=document.getElementById('local-up-st');
  if(btn) btn.disabled=true;
  let ok=0, fail=0, first=null, errs=[];
  for(let i=0;i<paths.length;i++){
    if(st) st.textContent=`path ${i+1}/${paths.length}…`;
    if(btn) btn.textContent=`Đang thêm ${i+1}/${paths.length}…`;
    try{
      const r=await fetch('/api/jobs/local-path',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({path:paths[i],target,move})});
      const d=await r.json();
      if(!r.ok||d.error){ fail++; errs.push((d.error||r.status)+''); continue; }
      ok++;
      const id=(d.job_ids&&d.job_ids[0])||d.job_id;
      if(id && !first) first=id;
    }catch(e){ fail++; errs.push(String(e)); }
  }
  if(el) el.value='';
  if(st) st.textContent=ok?`xong ${ok}`:'';
  if(btn){ btn.disabled=false; btn.textContent='+ Thêm file local'; }
  await refresh();
  if(first) select(first);
  if(fail) alert(`Đã thêm ${ok} file, lỗi ${fail}`+(errs[0]?`\n${errs[0]}`:''));
}
async function addLocalFile(input){
  const files=[...(input.files||[])]; if(!files.length) return;
  const big=files.filter(f=>f.size>800*1024*1024);
  if(big.length && !confirm(`${big.length} file >800MB. Upload HTTP chậm — nên dùng Path local. Vẫn upload?`)){ input.value=''; return; }
  const target=(document.getElementById('target')||{}).value||'Tiếng Việt';
  const st=document.getElementById('local-up-st');
  input.disabled=true;
  let ok=0, fail=0, first=null, errs=[];
  for(let i=0;i<files.length;i++){
    const f=files[i];
    if(st) st.textContent=`file ${i+1}/${files.length}…`;
    try{
      const d=await new Promise((resolve,reject)=>{
        const fd=new FormData(); fd.append('file',f); fd.append('target',target);
        const xhr=new XMLHttpRequest();
        xhr.open('POST','/api/jobs/local');
        xhr.upload.onprogress=e=>{
          if(st&&e.lengthComputable) st.textContent=`file ${i+1}/${files.length} · ${Math.round(e.loaded/e.total*100)}%`;
        };
        xhr.onload=()=>{ let j={}; try{j=JSON.parse(xhr.responseText);}catch(err){}
          if(xhr.status>=200&&xhr.status<300&&!j.error) resolve(j);
          else reject(new Error((j&&j.error)||('HTTP '+xhr.status))); };
        xhr.onerror=()=>reject(new Error('network'));
        xhr.send(fd);
      });
      ok++;
      const id=(d.job_ids&&d.job_ids[0])||d.job_id;
      if(id && !first) first=id;
    }catch(e){ fail++; errs.push((e.message||e)+''); }
  }
  if(st) st.textContent=ok?`xong ${ok}`:'';
  input.disabled=false; input.value='';
  await refresh();
  if(first) select(first);
  if(fail) alert(`Đã thêm ${ok} file, lỗi ${fail}`+(errs[0]?`\n${errs[0]}`:''));
}
function openQueue(){
  composeOpen=false;
  applyComposeUi();
  if(cache && cache.length){
    select(cache[0].job_id);
  } else {
    selected=null;
    const mid=document.getElementById('mid'), panel=document.getElementById('panel');
    if(mid) mid.innerHTML='<div class="empty">Chưa có video — bấm <b>+ Thêm link</b>.</div>';
    if(panel) panel.innerHTML='<div class="empty pnl-empty">—</div>';
  }
}
function applyComposeUi(){
  const app=document.querySelector('.app');
  const box=document.getElementById('compose');
  if(app) app.classList.toggle('compose-on', !!composeOpen);
  if(box){ box.hidden=!composeOpen; box.setAttribute('aria-hidden', composeOpen?'false':'true'); }
}
function setJobMode(on){
  if(on) composeOpen=false;
  applyComposeUi();
  document.querySelector('.app')?.classList.toggle('job', !!on || !composeOpen);
}
function showCompose(){
  composeOpen=true;
  selected=null; lastDetailSig=null; lastFull=null; lastPanelSig=null;
  applyComposeUi();
  document.querySelector('.app')?.classList.remove('job');
  clearSideCta();
  renderList(cache);
  updateUrlCount();
  const mid=document.getElementById('mid'), panel=document.getElementById('panel');
  if(mid) mid.innerHTML='<div class="empty">Đang thêm link — xong bấm <b>← Hàng chờ</b>.</div>';
  if(panel) panel.innerHTML='<div class="empty pnl-empty">—</div>';
  setTimeout(()=>{ const u=document.getElementById('url'); if(u) u.focus(); },40);
}
function clearSideCta(){
  const slot=document.getElementById('side-cta');
  if(slot) slot.innerHTML='';
}
function renderSideCta(j){
  const slot=document.getElementById('side-cta');
  if(!slot) return;
  if(!j || uiMode()!=='studio'){ slot.innerHTML=''; return; }
  const isRun=j.status==='running', isDone=j.status==='done';
  const voice=document.getElementById('auto-voice');
  const keepVoice=voice?voice.value:null;
  const eng=document.getElementById('auto-engine');
  const keepEng=eng?eng.value:null;
  const asr=document.getElementById('auto-asr');
  const keepAsr=asr?asr.value:null;
  const spd=document.getElementById('auto-speed');
  const keepSpd=spd?spd.value:null;
  slot.innerHTML=`
    <div class="studio-cta">
      <button class="btn pri big" ${isRun||isDone?'disabled':''} onclick="runAll('${j.job_id}')">▶ Chạy tự động</button>
      <button class="btn stop" ${isRun?'':'disabled'} onclick="stopJob('${j.job_id}')">⏹ Dừng</button>
      <div class="studio-pipe">${pipelineControlsHtml(j)}</div>
    </div>`;
  fillAutoVoices(j);
  // giữ lựa chọn đang chọn nếu còn trong DOM
  const v2=document.getElementById('auto-voice'); if(v2&&keepVoice){ [...v2.options].some(o=>o.value===keepVoice)&&(v2.value=keepVoice); }
  const e2=document.getElementById('auto-engine'); if(e2&&keepEng) e2.value=keepEng;
  const a2=document.getElementById('auto-asr'); if(a2&&keepAsr) a2.value=keepAsr;
  const s2=document.getElementById('auto-speed'); if(s2&&keepSpd) s2.value=keepSpd;
}
function select(id){ composeOpen=false; selected=id; setJobMode(true); refresh(); }

let groupOpen={};   // group_id -> mở/đóng (mặc định mở)
let listSel=new Set();
function toggleListSel(id,ev){
  if(ev) ev.stopPropagation();
  if(listSel.has(id)) listSel.delete(id); else listSel.add(id);
  const bar=document.getElementById('qbar');
  if(bar){ const h=bar.querySelector('.qhint'); if(h) h.textContent=listSel.size?`${listSel.size} chọn`:''; }
}
function jrow(j){
  const c=statClass(j.status);
  const p = j.started ? (j.status==='running'?`${j.overall}%`:statText(j.status)) : 'chờ';
  const start=(j.status!=='running'&&j.status!=='done')?`<button class="jstart" title="Bắt đầu" onclick="event.stopPropagation();startJob('${j.job_id}')">▶</button>`:'';
  const moveBtns = (j.status !== 'done') ? `
    <span class="jmove-box" title="Thứ tự làm việc (ưu tiên hàng chờ)">
      <button type="button" class="jmove-btn" title="Đưa lên ĐẦU hàng chờ (Làm trước nhất)" onclick="event.stopPropagation();moveQueueJob('${j.job_id}','top')">🔝</button>
      <button type="button" class="jmove-btn" title="Lên 1 bậc" onclick="event.stopPropagation();moveQueueJob('${j.job_id}','up')">▲</button>
      <button type="button" class="jmove-btn" title="Xuống 1 bậc" onclick="event.stopPropagation();moveQueueJob('${j.job_id}','down')">▼</button>
      <button type="button" class="jmove-btn" title="Đưa xuống CUỐI hàng chờ" onclick="event.stopPropagation();moveQueueJob('${j.job_id}','bottom')">🔻</button>
    </span>` : '';
  const part=j.group_id?`<span class="jpart">P${j.group_index||'?'}</span>`:'';
  const tip=escapeHtml((j.title_zh&&j.title_zh!==j.title)?j.title_zh+' — '+j.title:j.title);
  const ck=listSel.has(j.job_id)?'checked':'';
  return `<div class="jrow ${selected===j.job_id?'sel':''}" data-jid="${j.job_id}" onclick="select('${j.job_id}')">
    <input class="jchk" type="checkbox" ${ck} onclick="toggleListSel('${j.job_id}',event)" title="Chọn để gom nhóm">
    <span class="jdot d-${c}"></span>${part}<span class="jt" title="${tip}">${escapeHtml(j.title)}</span>
    <span class="jp">${p}</span>${moveBtns}${start}
    <button class="jdel" title="Xoá" onclick="delJob(event,'${j.job_id}')">✕</button></div>`;
}

async function _requeueFallback(newOrder){
  const st = await (await fetch('/api/queue/jobs')).json();
  const oldQueue = st.queue || [];
  if(oldQueue.length){
    await fetch('/api/queue/jobs/remove', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({job_ids: oldQueue})
    });
  }
  if(newOrder && newOrder.length){
    const defaultOpts = st.opts || {};
    const body = {job_ids: newOrder, skip_done: false, ...defaultOpts};
    await fetch('/api/queue/jobs', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
  }
  return await (await fetch('/api/queue/jobs')).json();
}

async function moveQueueJob(jobId, action){
  try{
    const st = await (await fetch('/api/queue/jobs')).json();
    let q = [...(st.queue || [])];
    let idx = q.indexOf(jobId);

    if(idx === -1){
      if(action === 'bottom'){
        q.push(jobId);
      } else {
        q.unshift(jobId);
      }
    } else {
      if(action === 'top' && idx > 0){
        q.splice(idx, 1);
        q.unshift(jobId);
      } else if(action === 'up' && idx > 0){
        const tmp = q[idx - 1];
        q[idx - 1] = q[idx];
        q[idx] = tmp;
      } else if(action === 'down' && idx < q.length - 1){
        const tmp = q[idx + 1];
        q[idx + 1] = q[idx];
        q[idx] = tmp;
      } else if(action === 'bottom' && idx < q.length - 1){
        q.splice(idx, 1);
        q.push(jobId);
      }
    }

    const newSt = await _requeueFallback(q);
    void refresh();
  }catch(e){
    console.error('Lỗi di chuyển:', e);
    alert('Lỗi di chuyển: ' + e);
  }
}

function renderList(jobs){
  document.getElementById('qty').textContent = jobs.length ? `Hàng chờ · ${jobs.length}` : 'Hàng chờ';
  const bq=document.getElementById('btn-queue');
  if(bq) bq.textContent = jobs.length ? `Hàng chờ · ${jobs.length}` : 'Hàng chờ';
  const el=document.getElementById('list');
  if(!jobs.length){ el.innerHTML='<div class="jp" style="padding:4px 6px">Chưa có video.</div>'; listSel.clear(); return; }
  const groups={}; jobs.forEach(j=>{ if(j.group_id){ (groups[j.group_id]=groups[j.group_id]||[]).push(j); } });
  const seen=new Set(); let html='';
  html+=`<div class="qbar" id="qbar">
    <button type="button" class="btn" onclick="groupSelected()" title="Gom các job đã tick thành 1 series">＋ Gom nhóm</button>
    <button type="button" class="btn" onclick="addSelectedToGroup()" title="Thêm job đã tick vào nhóm có sẵn">↳ Vào nhóm</button>
    <button type="button" class="btn" onclick="ungroupSelected()" title="Tách job đã tick khỏi nhóm">⤢ Tách</button>
    <span class="qhint">${listSel.size?listSel.size+' chọn':''}</span>
  </div>`;
  jobs.forEach(j=>{
    if(j.group_id){
      if(seen.has(j.group_id)) return; seen.add(j.group_id);
      const mem=groups[j.group_id].slice().sort((a,b)=>(a.group_index||0)-(b.group_index||0));
      const open=groupOpen[j.group_id]!==false;
      const gt=mem[0].group_title||'Nhóm';
      const nd=mem.filter(x=>x.status==='done').length;
      const anyRun=mem.some(x=>x.status==='running');
      html+=`<div class="jgroup${anyRun?' run':''}" data-gid="${j.group_id}">
        <div class="jghead" onclick="toggleGroup('${escapeHtml(j.group_id)}')" title="${escapeHtml(gt)}">
          <span class="jgcaret">${open?'▾':'▸'}</span>
          <span class="jt jgtitle">${escapeHtml(gt)}</span>
          <span class="jgcount">${nd}/${mem.length}</span>
          <span class="jgacts">
            <button type="button" title="Đổi tên series" onclick="event.stopPropagation();renameGroup('${escapeHtml(j.group_id)}',${escapeHtml(JSON.stringify(gt))})">✎</button>
            <button type="button" title="Giải tán nhóm" onclick="event.stopPropagation();dissolveGroup('${escapeHtml(j.group_id)}')">⤢</button>
          </span>
        </div>
        <div class="jgparts" style="${open?'':'display:none'}">${mem.map(jrow).join('')}</div>
      </div>`;
    } else { html+=jrow(j); }
  });
  el.innerHTML=html;
}
/** Chỉ đổi % / chấm trạng thái / nút ▶ — không rebuild list (hết giật sidebar). */
function patchListLive(jobs){
  (jobs||[]).forEach(j=>{
    const row=[...document.querySelectorAll('.jrow')].find(r=>(r.dataset.jid===j.job_id)||(r.getAttribute('onclick')||'').includes(j.job_id));
    if(!row) return;
    row.classList.toggle('sel', selected===j.job_id);
    const c=statClass(j.status);
    const dot=row.querySelector('.jdot'); if(dot) dot.className='jdot d-'+c;
    const jp=row.querySelector('.jp');
    if(jp) jp.textContent=j.started?(j.status==='running'?`${j.overall}%`:statText(j.status)):'chờ';
    const chk=row.querySelector('.jchk'); if(chk) chk.checked=listSel.has(j.job_id);
    let start=row.querySelector('.jstart');
    if(j.status==='running'||j.status==='done'){ if(start) start.remove(); }
    else if(!start){
      const btn=document.createElement('button');
      btn.className='jstart'; btn.title='Bắt đầu'; btn.textContent='▶';
      btn.addEventListener('click',ev=>{ev.stopPropagation(); startJob(j.job_id);});
      const del=row.querySelector('.jdel');
      if(del) row.insertBefore(btn,del); else row.appendChild(btn);
    }
  });
  document.querySelectorAll('.jgroup[data-gid]').forEach(g=>{
    const gid=g.dataset.gid;
    const mem=(jobs||[]).filter(x=>x.group_id===gid);
    if(!mem.length) return;
    g.classList.toggle('run', mem.some(x=>x.status==='running'));
    const cnt=g.querySelector('.jgcount');
    if(cnt) cnt.textContent=`${mem.filter(x=>x.status==='done').length}/${mem.length}`;
  });
  const h=document.querySelector('#qbar .qhint'); if(h) h.textContent=listSel.size?`${listSel.size} chọn`:'';
}
function toggleGroup(gid){ groupOpen[gid]=(groupOpen[gid]===false); renderList(cache); }
async function groupSelected(){
  const ids=[...listSel];
  if(ids.length<1){ alert('Tick ≥1 job rồi bấm Gom nhóm'); return; }
  const title=prompt('Tên series / nhóm:', (cache.find(j=>j.job_id===ids[0])||{}).title||'Series');
  if(title===null) return;
  try{
    const r=await fetch('/api/queue/group',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action:'create',job_ids:ids,title:title||undefined})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){ alert(d.error||r.status); return; }
    listSel.clear(); lastDetailSig=null; void refresh();
  }catch(e){ alert('Lỗi: '+e); }
}
async function addSelectedToGroup(){
  const ids=[...listSel];
  if(!ids.length){ alert('Tick job cần thêm vào nhóm'); return; }
  const gids=[...new Set((cache||[]).filter(j=>j.group_id).map(j=>j.group_id))];
  if(!gids.length){ alert('Chưa có nhóm — dùng Gom nhóm trước'); return; }
  const opts=gids.map(g=>{
    const m=(cache||[]).find(j=>j.group_id===g);
    return `${g} — ${(m&&m.group_title)||'Nhóm'}`;
  }).join('\n');
  const pick=prompt('Nhập group_id để thêm vào:\n'+opts, gids[0]);
  if(!pick) return;
  const gid=pick.split('—')[0].trim();
  try{
    const r=await fetch('/api/queue/group',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action:'add',group_id:gid,job_ids:ids})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){ alert(d.error||r.status); return; }
    listSel.clear(); lastDetailSig=null; void refresh();
  }catch(e){ alert('Lỗi: '+e); }
}
async function ungroupSelected(){
  const ids=[...listSel];
  if(!ids.length){ alert('Tick job cần tách khỏi nhóm'); return; }
  if(!confirm('Tách '+ids.length+' job khỏi nhóm?')) return;
  try{
    const r=await fetch('/api/queue/group',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action:'ungroup',job_ids:ids})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){ alert(d.error||r.status); return; }
    listSel.clear(); lastDetailSig=null; void refresh();
  }catch(e){ alert('Lỗi: '+e); }
}
async function renameGroup(gid, cur){
  const title=prompt('Đổi tên series:', cur||'Series');
  if(title===null || !String(title).trim()) return;
  try{
    const r=await fetch('/api/queue/group',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action:'rename',group_id:gid,title:String(title).trim()})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){ alert(d.error||r.status); return; }
    lastDetailSig=null; void refresh();
  }catch(e){ alert('Lỗi: '+e); }
}
async function dissolveGroup(gid){
  if(!confirm('Giải tán nhóm này? (job vẫn giữ, chỉ bỏ gom)')) return;
  try{
    const r=await fetch('/api/queue/group',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action:'dissolve',group_id:gid})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){ alert(d.error||r.status); return; }
    lastDetailSig=null; void refresh();
  }catch(e){ alert('Lỗi: '+e); }
}
async function mergeAllSeries(){
  try{
    const lib=await (await fetch('/api/hg/library')).json();
    const arr=lib.items||[];
    const pending=arr.filter(x=>!(x.done||(x.merged&&!x.fragments))).map(x=>x.name).filter(Boolean);
    if(!pending.length){ alert('Hongguo: tất cả series đã DONE / không có gì để merge'); return; }
    if(!confirm('Enqueue merge '+pending.length+' series Hongguo chưa xong?')) return;
    const r=await fetch('/api/hg/merge',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({names:pending,force:false})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){ alert(d.error||r.status); return; }
    const skip=(d.skipped_done||[]).length;
    alert('Đã xếp '+((d.queue||pending).length)+' series vào hàng merge'
      +(skip?` · bỏ qua ${skip} đã done`:'')
      +'\nXem tiến độ tại /hongguo');
  }catch(e){ alert('Lỗi merge: '+e+'\n(Hongguo Downloader đang chạy?)'); }
}

let selectedStep=null, panelJobId=null, lastPanelSig=null;
function defaultStep(j){
  if((j.stages.separate||{}).status==='running') return 'asr';
  const run=STAGES.find(s=>(j.stages[s.key]||{}).status==='running'); if(run) return run.key;
  const dn=STAGES.filter(s=>(j.stages[s.key]||{}).status==='done'); if(dn.length) return dn[dn.length-1].key;
  return 'download';
}
function selectStep(key){ selectedStep=key; if(lastFull){ document.querySelectorAll('.step').forEach((el,i)=>el.classList.toggle('sel',STAGES[i]&&STAGES[i].key===key)); document.querySelectorAll('.tpill').forEach(el=>el.classList.toggle('sel',el.dataset.key===key)); renderPanel(lastFull,true); } }


function uiMode(){ return (document.body&&document.body.dataset.ui)||'classic'; }
// Giữ lựa chọn form khi panel/mid bị dựng lại (click → refresh → innerHTML mặc định).
const FORM_IDS=['auto-asr','auto-engine','auto-voice','auto-speed','auto-audio','auto-ovol','auto-tr-mode','auto-review-backup','auto-ocr','auto-fit','auto-res','auto-zoom','auto-motion','auto-lut','auto-grain','auto-vbit','auto-flip','auto-frame','auto-brand-mode','auto-brand-text','auto-brand-logo'];
let _formPrefs={}; // job_id -> {field: value|bool}
function snapFormPrefs(jobId){
  if(!jobId) return;
  const p=_formPrefs[jobId]||{};
  FORM_IDS.forEach(id=>{
    const el=document.getElementById(id); if(!el) return;
    p[id]=(el.type==='checkbox')?!!el.checked:el.value;
  });
  _formPrefs[jobId]=p;
}
function rememberForm(jobId,id,val){
  if(!jobId) return;
  (_formPrefs[jobId]=_formPrefs[jobId]||{})[id]=val;
}
function prefOf(jobId,id,fallback){
  const p=_formPrefs[jobId];
  if(p && id in p && p[id]!=='' && p[id]!=null) return p[id];
  return fallback;
}
function applyFormPrefs(jobId){
  const p=_formPrefs[jobId]; if(!p) return;
  FORM_IDS.forEach(id=>{
    if(!(id in p)) return;
    const el=document.getElementById(id); if(!el) return;
    if(el.type==='checkbox'){ el.checked=!!p[id]; return; }
    if(el.tagName==='SELECT'){
      const v=String(p[id]);
      if([...el.options].some(o=>o.value===v)) el.value=v;
      return;
    }
    el.value=p[id];
  });
}
function autoEngineOpts(j){
  const e=prefOf(j.job_id,'auto-engine',((j.stages.tts||{}).tts_engine||'vieneu'));
  return `<option value="vieneu" ${e==='vieneu'?'selected':''}>VieNeu</option><option value="omnivoice" ${e==='omnivoice'?'selected':''}>OmniVoice</option>`;
}
function autoAsrOpts(j){
  const e=prefOf(j.job_id,'auto-asr',(((j.stages.asr||{}).asr_engine||(j.stages.asr||{}).engine||'hybrid_fa')));
  const o=[['nano_fa','⭐ Nano + Direct FA (Khuyên dùng — Không đúp câu)'],['hybrid_fa','Hybrid FA (no repair)'],['fun_asr_nano','Fun-ASR-Nano — chỉ chữ'],['paraformer_zh','Paraformer — chỉ timing'],['sensevoice','SenseVoice — nhanh'],['firered_asr','FireRedASR2 — dự phòng']];
  return o.map(([v,t])=>`<option value="${v}" ${v===e?'selected':''}>${t}</option>`).join('');
}
function lookControlsHtml(j){
  const id=j.job_id;
  const zoom=String(prefOf(id,'auto-zoom','1.12'));
  const motion=!!prefOf(id,'auto-motion',true);
  const lut=!!prefOf(id,'auto-lut',true);
  const grain=String(prefOf(id,'auto-grain','weak'));
  const vbit=String(prefOf(id,'auto-vbit','8M'));
  const flip=String(prefOf(id,'auto-flip',''));
  const frame=String(prefOf(id,'auto-frame','off'));
  const bmode=String(prefOf(id,'auto-brand-mode','off'));
  const btext=String(prefOf(id,'auto-brand-text',''));
  const blogo=String(prefOf(id,'auto-brand-logo','off'));
  const sel=(cur,v)=>cur===v?'selected':'';
  return `<div class="look-grid">
    <div class="look-sec"><span class="look-k">Hình</span>
      <label class="chk">Zoom <select id="auto-zoom" onchange="rememberForm('${id}','auto-zoom',this.value)"><option value="1.10" ${sel(zoom,'1.10')}>10%</option><option value="1.12" ${sel(zoom,'1.12')}>12%</option><option value="1.15" ${sel(zoom,'1.15')}>15%</option></select></label>
      <label class="chk"><input type="checkbox" id="auto-motion" ${motion?'checked':''} onchange="rememberForm('${id}','auto-motion',this.checked)"> Ken Burns</label>
      <label class="chk"><input type="checkbox" id="auto-lut" ${lut?'checked':''} onchange="rememberForm('${id}','auto-lut',this.checked)"> LUT</label>
      <label class="chk">Grain <select id="auto-grain" onchange="rememberForm('${id}','auto-grain',this.value)"><option value="off" ${sel(grain,'off')}>off</option><option value="weak" ${sel(grain,'weak')}>weak</option><option value="medium" ${sel(grain,'medium')}>medium</option><option value="strong" ${sel(grain,'strong')}>strong</option></select></label>
      <label class="chk" title="Bitrate H264">Nén <select id="auto-vbit" onchange="rememberForm('${id}','auto-vbit',this.value)"><option value="8M" ${sel(vbit,'8M')}>8M đẹp</option><option value="4M" ${sel(vbit,'4M')}>4M cân bằng</option><option value="2M" ${sel(vbit,'2M')}>2M nhẹ</option></select></label>
      <label class="chk">Lật <select id="auto-flip" onchange="rememberForm('${id}','auto-flip',this.value)"><option value="" ${sel(flip,'')}>không</option><option value="h" ${sel(flip,'h')}>ngang</option><option value="v" ${sel(flip,'v')}>dọc</option><option value="hv" ${sel(flip,'hv')}>cả hai</option></select></label>
    </div>
    <div class="look-sec"><span class="look-k">Brand</span>
      <label class="chk">Khung <select id="auto-frame" onchange="rememberForm('${id}','auto-frame',this.value)"><option value="off" ${sel(frame,'off')}>tắt</option><option value="auto" ${sel(frame,'auto')}>auto</option></select></label>
      <label class="chk">Mode <select id="auto-brand-mode" onchange="rememberForm('${id}','auto-brand-mode',this.value)"><option value="off" ${sel(bmode,'off')}>tắt</option><option value="drift" ${sel(bmode,'drift')}>drift</option><option value="corners" ${sel(bmode,'corners')}>corners</option><option value="drift_seed" ${sel(bmode,'drift_seed')}>drift_seed</option></select></label>
      <input id="auto-brand-text" placeholder="Tên kênh" title="Chữ brand" value="${escapeHtml(btext)}" oninput="rememberForm('${id}','auto-brand-text',this.value)">
      <label class="chk">logo <select id="auto-brand-logo" onchange="rememberForm('${id}','auto-brand-logo',this.value)"><option value="off" ${sel(blogo,'off')}>tắt</option><option value="auto" ${sel(blogo,'auto')}>auto</option></select></label>
    </div>
  </div>`;
}
function pipelineControlsHtml(j){
  const id=j.job_id;
  const audio=String(prefOf(id,'auto-audio','voice_bgm'));
  const tr=String(prefOf(id,'auto-tr-mode','batch'));
  const ocr=!!prefOf(id,'auto-ocr',false);
  const fit=!!prefOf(id,'auto-fit',true);
  const res=String(prefOf(id,'auto-res','source'));
  const spd=prefOf(id,'auto-speed',(j.stages.tts||{}).speed);
  const sel=(cur,v)=>cur===v?'selected':'';
  return `
    <label class="chk">🎤 <select id="auto-asr" onchange="rememberForm('${id}','auto-asr',this.value);saveAsrEngine('${id}',this.value)">${autoAsrOpts(j)}</select></label>
    <label class="chk">🧠 <select id="auto-engine" onchange="rememberForm('${id}','auto-engine',this.value);autoEngineChanged('${id}')">${autoEngineOpts(j)}</select></label>
    <label class="chk">🗣️ <select id="auto-voice" onchange="rememberForm('${id}','auto-voice',this.value);saveTtsVoice('${id}',this.value)"></select></label>
    <label class="chk">⏩ <select id="auto-speed" onchange="rememberForm('${id}','auto-speed',this.value);saveTtsSpeed('${id}',this.value)">${speedOptions(spd)}</select></label>
    <label class="chk">🔊 <select id="auto-audio" onchange="rememberForm('${id}','auto-audio',this.value);autoAudioChanged()">
      <option value="voice_bgm" ${sel(audio,'voice_bgm')}>Giọng + BGM thư viện</option>
      <option value=\"sfx_lib\" ${sel(audio,'sfx_lib')}>Tiếng động gốc + nhạc kho sạch (bỏ nhạc gốc)</option>
      <option value=\"duck_bgm\" ${sel(audio,'duck_bgm')}>Ducking + BGM thư viện</option>
      <option value="voice_only" ${sel(audio,'voice_only')}>Chỉ giọng Việt</option>
      <option value="duck" ${sel(audio,'duck')}>Ducking (giữ gốc)</option>
      <option value="demucs_keep" ${sel(audio,'demucs_keep')}>Demucs keep</option>
      <option value="demucs_clean" ${sel(audio,'demucs_clean')}>Demucs clean</option>
    </select></label>
    <label class="chk" id="row-auto-ovol" style="display:${(audio==='duck'||audio==='duck_bgm')?'':'none'}" title="Mức tiếng gốc còn lại">Tiếng gốc nền <select id="auto-ovol" onchange="rememberForm('${id}','auto-ovol',this.value)">
      <option value="0.12" ${sel(String(prefOf(id,'auto-ovol','0.22')),'0.12')}>rất nhỏ</option>
      <option value="0.18" ${sel(String(prefOf(id,'auto-ovol','0.22')),'0.18')}>nhỏ</option>
      <option value="0.22" ${sel(String(prefOf(id,'auto-ovol','0.22')),'0.22')}>nhỏ+</option>
      <option value="0.28" ${sel(String(prefOf(id,'auto-ovol','0.22')),'0.28')}>vừa</option>
      <option value="0.45" ${sel(String(prefOf(id,'auto-ovol','0.22')),'0.45')}>to</option>
      <option value="0.6" ${sel(String(prefOf(id,'auto-ovol','0.22')),'0.6')}>rất to</option>
    </select></label>
    <label class="chk">🈶 <select id="auto-tr-mode" onchange="rememberForm('${id}','auto-tr-mode',this.value)"><option value="batch" ${sel(tr,'batch')}>batch (lô 20)</option><option value="auto" ${sel(tr,'auto')}>auto</option><option value="oneshot" ${sel(tr,'oneshot')}>oneshot</option></select></label>
    <label class="chk" title="off = REVIEW_MODEL config; Aurora∥ = rà chatgpt/gpt-6 (Aurora/:3377)">🛡 <select id="auto-review-backup" onchange="rememberForm('${id}','auto-review-backup',this.value)"><option value="off" ${sel(String(prefOf(id,'auto-review-backup','aurora_parallel')),'off')}>REVIEW: config</option><option value="aurora_parallel" ${sel(String(prefOf(id,'auto-review-backup','aurora_parallel')),'aurora_parallel')}>Aurora GPT∥</option></select></label>
    <label class="chk"><input type="checkbox" id="auto-ocr" ${ocr?'checked':''} onchange="rememberForm('${id}','auto-ocr',this.checked)"> OCR</label>
    <label class="chk"><input type="checkbox" id="auto-fit" ${fit?'checked':''} onchange="rememberForm('${id}','auto-fit',this.checked)"> Khớp time</label>
    <label class="chk">🖥 <select id="auto-res" onchange="rememberForm('${id}','auto-res',this.value)"><option value="source" ${sel(res,'source')}>Nguồn</option><option value="1080" ${sel(res,'1080')}>1080p</option></select></label>`;
}
function timelinePills(j){
  return STAGES.map(s=>{
    let st=(j.stages[s.key]||{}).status||'pending';
    if(s.key==='asr' && (j.stages.separate||{}).status==='running' && st!=='running' && st!=='done') st='running';
    const cls=['tpill', selectedStep===s.key?'sel':'', st==='done'?'ok':'', st==='running'?'run':'', st==='failed'?'err':''].filter(Boolean).join(' ');
    const n=st==='done'?'Xong':st==='running'?'●':st==='failed'?'Lỗi':'';
    return `<button type="button" class="${cls}" data-key="${s.key}" onclick="selectStep('${s.key}')"><b>${escapeHtml(s.name)}</b><span>${n}</span></button>`;
  }).join('');
}


function renderDetailClassic(j){
  const mid=document.getElementById('mid'), panel=document.getElementById('panel');
  if(!j){
    if(!composeOpen){
      applyComposeUi();
      if(mid) mid.innerHTML='<div class="empty">Chọn một video trong hàng chờ.</div>';
      if(panel) panel.innerHTML='<div class="empty pnl-empty">Chọn một bước để xem chi tiết.</div>';
    }
    lastFull=null; lastPanelSig=null;
    return;
  }
  composeOpen=false;
  setJobMode(true);
  if(panelJobId!==j.job_id){ panelJobId=j.job_id; selectedStep=defaultStep(j); }
  if(!selectedStep) selectedStep=defaultStep(j);
  if(lastFull && lastFull.job_id===j.job_id && document.querySelector('#mid .autobar') && document.querySelector('#mid .steps')){
    patchClassicLive(j);
    return;
  }
  const badges=[
    j.platform?`<span>${j.platform}</span>`:'',
    j.height?`<span><b>${j.height}p</b></span>`:'',
    j.duration_sec?`<span class="mono">${fmtDur(j.duration_sec)}</span>`:'',
    j.target?`<span>→ ${j.target}</span>`:'',
  ].filter(Boolean).join('');
  let eta=''; const dl=j.stages.download||{}; if(dl.status==='running'&&dl.eta>0) eta='còn '+fmtDur(dl.eta);
  const tr=j.stages.translate||{};
  const model=(tr.model||'gemma4').replace('local/','').replace('ollama/','').replace('gc/','').replace('kr/','');
  const stat=`<div class="stat">
    <div><div class="k">Đã dịch</div><div class="v" id="stat-tr">${tr.n_done!=null?tr.n_done:(tr.n_segments||0)}<small> /${tr.n_total||tr.n_segments||0}</small></div></div>
    <div><div class="k">Model</div><div class="v">${escapeHtml(model)}</div></div>
    <div><div class="k">Trạng thái</div><div class="v t-${statClass(j.status)}" id="stat-st">${statText(j.status)}</div></div>
  </div>`;
  const isRun=j.status==='running';
  const isDone=j.status==='done';
  snapFormPrefs(j.job_id);
  const steps=STAGES.map(s=>stepRow(j,s)).join('');
  mid.innerHTML=`
    <div class="mtop"><div><div class="mtitle">${escapeHtml(j.title)}</div><div class="mbadges">${badges}</div></div>
      <div class="pct"><div class="n">${j.overall||0}%</div><div class="s">${eta}</div></div></div>
    <div class="pline"><i style="width:${j.overall||0}%"></i></div>
    ${stat}
    <div class="autobar">
      <button class="btn pri big" ${isRun||isDone?'disabled':''} onclick="runAll('${j.job_id}')">▶ Chạy tự động (toàn bộ)</button>
      <button class="btn stop" ${isRun?'':'disabled'} onclick="stopJob('${j.job_id}')">⏹ Dừng</button>
      <span class="autosep"></span>
      ${pipelineControlsHtml(j)}
    </div>
    <div class="autobar-fx"><span class="alabel">Render</span>${lookControlsHtml(j)}</div>
    <div class="seclabel">Các bước</div><div class="steps">${steps}</div>
    ${buildLog(j)}`;
  lastFull=j;
  renderPanel(j, false);
  Promise.resolve(fillAutoVoices(j)).then(()=>applyFormPrefs(j.job_id));
  fillAutoFrames(); fillAutoLogos();
  if(logOpen) feedSentences(j);
}
function patchClassicLive(j){
  lastFull=j;
  const n=document.querySelector('#mid .pct .n'); if(n) n.textContent=(j.overall||0)+'%';
  const sEl=document.querySelector('#mid .pct .s');
  if(sEl){ let eta=''; const dl=j.stages.download||{}; if(dl.status==='running'&&dl.eta>0) eta='còn '+fmtDur(dl.eta); sEl.textContent=eta; }
  const bar=document.querySelector('#mid .pline i'); if(bar) bar.style.width=(j.overall||0)+'%';
  const stEl=document.getElementById('stat-st');
  if(stEl){ stEl.className='v t-'+statClass(j.status); stEl.textContent=statText(j.status); }
  const tr=j.stages.translate||{};
  const trEl=document.getElementById('stat-tr');
  if(trEl) trEl.innerHTML=`${tr.n_done!=null?tr.n_done:(tr.n_segments||0)}<small> /${tr.n_total||tr.n_segments||0}</small>`;
  const isRun=j.status==='running', isDone=j.status==='done';
  document.querySelectorAll('#mid .autobar .btn.pri.big').forEach(b=>b.disabled=!!(isRun||isDone));
  document.querySelectorAll('#mid .autobar .btn.stop').forEach(b=>b.disabled=!isRun);
  patchStepsLive(j);
  patchLogLive(j);
  snapFormPrefs(j.job_id);
  renderPanel(j, false);
  feedSentences(j);
}
function patchStepsLive(j){
  const box=document.querySelector('#mid .steps');
  if(!box) return;
  const html=STAGES.map(s=>stepRow(j,s)).join('');
  if(box.dataset.sig===html) return;
  box.dataset.sig=html;
  box.innerHTML=html;
}
function patchLogLive(j){
  const body=document.getElementById('logbody');
  const liveWrap=document.querySelector('.studio-live, #mid .log');
  if(document.querySelector('.studio-live')){
    const wrap=document.createElement('div');
    wrap.innerHTML=buildLiveLog(j);
    const neu=wrap.firstElementChild;
    const cur=document.querySelector('.studio-live');
    if(neu && cur){
      const a=cur.querySelector('.logbody')?.innerHTML||'';
      const b=neu.querySelector('.logbody')?.innerHTML||'';
      if(a!==b) cur.replaceWith(neu);
      else {
        const live=cur.querySelector('.live');
        const want=j.status==='running';
        if(want && !live){ /* ignore */ }
        const h=cur.querySelector('.logh');
        if(h){
          const has=!!h.querySelector('.live');
          if(want && !has) h.insertAdjacentHTML('beforeend','<span class="live"><i></i> trực tiếp</span>');
          if(!want && has) h.querySelector('.live')?.remove();
        }
      }
    }
    return;
  }
  if(!body || !liveWrap) return;
  const tmp=document.createElement('div');
  tmp.innerHTML=buildLog(j);
  const nb=tmp.querySelector('#logbody');
  if(nb && body.innerHTML!==nb.innerHTML) body.innerHTML=nb.innerHTML;
  const h=liveWrap.querySelector('.logh');
  if(h){
    const has=!!h.querySelector('.live');
    const want=j.status==='running';
    if(want && !has) h.insertAdjacentHTML('beforeend',' <span class="live"><i></i> trực tiếp</span>');
    if(!want && has) h.querySelector('.live')?.remove();
  }
}

function renderDetail(j){
  const mode=uiMode();
  if(mode==='studio') return renderDetailStudio(j);
  if(mode==='console') return renderDetailConsole(j);
  return renderDetailClassic(j);
}
function renderDetailStudio(j){
  const mid=document.getElementById('mid'), panel=document.getElementById('panel');
  if(!j){
    if(!composeOpen){
      applyComposeUi();
      clearSideCta();
      if(mid) mid.innerHTML='<div class="empty">Chọn một video trong hàng chờ.</div>';
      if(panel) panel.innerHTML='<div class="empty pnl-empty">Look & chi tiết bước.</div>';
    }
    lastFull=null; lastPanelSig=null; return;
  }
  composeOpen=false;
  setJobMode(true);
  clearSideCta();
  if(panelJobId!==j.job_id){ panelJobId=j.job_id; selectedStep=defaultStep(j); }
  if(!selectedStep) selectedStep=defaultStep(j);
  // Đã có khung studio cùng job → chỉ patch live (tránh giật video/form mỗi poll)
  if(lastFull && lastFull.job_id===j.job_id && document.getElementById('studio-pslot') && document.querySelector('.studio-head')){
    patchStudioLive(j);
    return;
  }
  const badges=[
    j.platform?`<span>${j.platform}</span>`:'',
    j.height?`<span><b>${j.height}p</b></span>`:'',
    j.duration_sec?`<span class="mono">${fmtDur(j.duration_sec)}</span>`:'',
  ].filter(Boolean).join('');
  let eta=''; const dl=j.stages.download||{}; if(dl.status==='running'&&dl.eta>0) eta='còn '+fmtDur(dl.eta);
  const dlDone=(j.stages.download||{}).status==='done';
  mid.innerHTML=`
    <div class="studio-head">
      <div><div class="mtitle">${escapeHtml(j.title)}</div><div class="mbadges">${badges}</div></div>
      <div class="pct"><div class="n">${j.overall||0}%</div><div class="s">${eta||statText(j.status)}</div></div>
    </div>
    <div class="pline"><i style="width:${j.overall||0}%"></i></div>
    <div class="studio-preview"><div id="studio-pslot" class="pslot">${dlDone?'':'<div class="empty soft">Chưa có video — chạy bước Tải</div>'}</div></div>
    <div class="timeline">${timelinePills(j)}</div>
    ${buildLiveLog(j)}`;
  lastFull=j;
  clearSideCta();
  snapFormPrefs(j.job_id);
  renderPanel(j, false);
  Promise.resolve(fillAutoVoices(j)).then(()=>applyFormPrefs(j.job_id));
  fillAutoFrames(); fillAutoLogos();
  if(dlDone) ensureVideo(j,true);
  feedSentences(j);
}
/** Cập nhật % / timeline / nhật ký / nút — KHÔNG innerHTML cả mid (giữ video + form). */
function patchTtsArtifactLive(j){
  if(selectedStep!=='tts') return;
  const cur=j.stages.tts||{};
  if(cur.status!=='running') return;
  const box=document.getElementById('panel-body'); if(!box) return;
  const pct=cur.progress!=null?cur.progress:0;
  let line=box.querySelector('.afile.tts-live');
  let bar=box.querySelector('.pline.big i');
  let curline=box.querySelector('.curline');
  if(!line || !bar || !curline){
    // chưa có khung running → để renderPanel/loadArtifact dựng lại
    return false;
  }
  line.innerHTML=`🔊 Đang đọc <b>${cur.n_done||0}/${cur.n_total||0}</b> câu · ${pct}%`;
  bar.style.width=Math.min(pct,100)+'%';
  curline.innerHTML=`🗣️ ${cur.cur_text?escapeHtml(cur.cur_text):'…'}`;
  return true;
}
function patchStudioLive(j){
  lastFull=j;
  const n=document.querySelector('.studio-head .pct .n');
  if(n) n.textContent=(j.overall||0)+'%';
  const sEl=document.querySelector('.studio-head .pct .s');
  if(sEl){
    let eta=''; const dl=j.stages.download||{}; if(dl.status==='running'&&dl.eta>0) eta='còn '+fmtDur(dl.eta);
    sEl.textContent=eta||statText(j.status);
  }
  const bar=document.querySelector('#mid .pline i');
  if(bar) bar.style.width=(j.overall||0)+'%';
  const title=document.querySelector('.studio-head .mtitle');
  if(title && j.title && title.textContent!==j.title) title.textContent=j.title;
  document.querySelectorAll('.timeline .tpill').forEach(el=>{
    const key=el.dataset.key; if(!key) return;
    const st=(j.stages[key]||{}).status||'pending';
    el.classList.toggle('ok', st==='done');
    el.classList.toggle('run', st==='running');
    el.classList.toggle('err', st==='failed');
    el.classList.toggle('sel', selectedStep===key);
    const span=el.querySelector('span');
    if(span) span.textContent=st==='done'?'Xong':st==='running'?'●':st==='failed'?'Lỗi':'';
  });
  patchLogLive(j);
  patchTtsArtifactLive(j);
  const dlDone=(j.stages.download||{}).status==='done';
  const slot=document.getElementById('studio-pslot');
  if(slot && dlDone && (!slot.querySelector('video') || ((j.stages.mux||{}).status==='done' && videoSrcKind!=='output')))
    ensureVideo(j,true);
  snapFormPrefs(j.job_id);
  renderPanel(j, false);
  feedSentences(j);
}
function renderDetailConsole(j){
  const mid=document.getElementById('mid'), panel=document.getElementById('panel');
  if(!j){
    if(!composeOpen){
      applyComposeUi();
      if(mid) mid.innerHTML='<div class="empty">Chọn một video trong hàng chờ.</div>';
      if(panel) panel.innerHTML='<div class="empty pnl-empty">Chọn tab / bước.</div>';
    }
    lastFull=null; lastPanelSig=null; return;
  }
  composeOpen=false;
  setJobMode(true);
  if(panelJobId!==j.job_id){ panelJobId=j.job_id; selectedStep=defaultStep(j); window._consoleTab=window._consoleTab||'pipeline'; }
  if(!selectedStep) selectedStep=defaultStep(j);
  if(!window._consoleTab) window._consoleTab='pipeline';
  if(lastFull && lastFull.job_id===j.job_id && document.querySelector('#mid .console-run') && document.querySelector('#mid .steps')){
    patchConsoleLive(j);
    return;
  }
  const badges=[
    j.platform?`<span>${j.platform}</span>`:'',
    j.height?`<span><b>${j.height}p</b></span>`:'',
    j.duration_sec?`<span class="mono">${fmtDur(j.duration_sec)}</span>`:'',
  ].filter(Boolean).join('');
  let eta=''; const dl=j.stages.download||{}; if(dl.status==='running'&&dl.eta>0) eta='còn '+fmtDur(dl.eta);
  const isRun=j.status==='running', isDone=j.status==='done';
  const steps=STAGES.map(s=>stepRow(j,s)).join('');
  mid.innerHTML=`
    <div class="mtop"><div><div class="mtitle">${escapeHtml(j.title)}</div><div class="mbadges">${badges}</div></div>
      <div class="pct"><div class="n">${j.overall||0}%</div><div class="s">${eta||statText(j.status)}</div></div></div>
    <div class="pline"><i style="width:${j.overall||0}%"></i></div>
    <div class="console-run">
      <button class="btn pri big" ${isRun||isDone?'disabled':''} onclick="runAll('${j.job_id}')">▶ Chạy tự động</button>
      <button class="btn stop" ${isRun?'':'disabled'} onclick="stopJob('${j.job_id}')">⏹ Dừng</button>
    </div>
    <div class="seclabel">Pipeline</div><div class="steps">${steps}</div>
    ${buildLog(j)}`;
  lastFull=j;
  renderPanel(j, false);
  fillAutoVoices(j); fillAutoFrames(); fillAutoLogos();
  if(logOpen) feedSentences(j);
}
function patchConsoleLive(j){
  lastFull=j;
  const n=document.querySelector('#mid .pct .n'); if(n) n.textContent=(j.overall||0)+'%';
  const sEl=document.querySelector('#mid .pct .s');
  if(sEl){ let eta=''; const dl=j.stages.download||{}; if(dl.status==='running'&&dl.eta>0) eta='còn '+fmtDur(dl.eta); sEl.textContent=eta||statText(j.status); }
  const bar=document.querySelector('#mid .pline i'); if(bar) bar.style.width=(j.overall||0)+'%';
  const isRun=j.status==='running', isDone=j.status==='done';
  document.querySelectorAll('#mid .console-run .btn.pri.big').forEach(b=>b.disabled=!!(isRun||isDone));
  document.querySelectorAll('#mid .console-run .btn.stop').forEach(b=>b.disabled=!isRun);
  patchStepsLive(j);
  patchLogLive(j);
  renderPanel(j, false);
  feedSentences(j);
}
function setConsoleTab(tab){
  window._consoleTab=tab;
  if(lastFull) renderPanel(lastFull,true);
}


async function fillAutoVoices(j){
  const sel=document.getElementById('auto-voice'); if(!sel) return;
  await loadVoices();
  const eng=(document.getElementById('auto-engine')||{}).value||(j.stages.tts||{}).tts_engine||'vieneu';
  const list=(VOICES&&VOICES[eng])||[];
  const want=prefOf(j.job_id,'auto-voice',(j.stages.tts||{}).voice);
  const cur=want||(j.stages.tts||{}).voice;
  sel.innerHTML=list.length?list.map(v=>`<option value="${v.id}" ${v.id===cur?'selected':''}>${escapeHtml(v.label||v.id)}</option>`).join(''):`<option value="">(chưa có giọng — Thêm giọng)</option>`;
  if(cur) rememberForm(j.job_id,'auto-voice',sel.value);
}
function autoEngineChanged(id){ const e=(document.getElementById('auto-engine')||{}).value; saveTtsEngine(id,e); if(lastFull) fillAutoVoices(lastFull); }

function panelSig(j){
  // Không gồm progress/% — % patch riêng (tránh rebuild cột phải mỗi poll)
  const st=j.stages[selectedStep]||{};
  return [j.job_id, selectedStep, st.status, st.n_done, st.n_total, st.n_segments,
          st.n_clips, st.n_texts, st.reviewed, st.model, st.review_progress, st.n_missing, st.tts_engine, st.asr_engine, st.engine].join('|');
}
function renderPanel(j, force){
  const mode=uiMode();
  if(mode==='studio') return renderPanelStudio(j, force);
  if(mode==='console') return renderPanelConsole(j, force);
  return renderPanelClassic(j, force);
}
function renderPanelClassic(j, force){
  const panel=document.getElementById('panel'); const s=STAGES.find(x=>x.key===selectedStep);
  if(!s){ panel.innerHTML='<div class="empty pnl-empty">Chọn một bước.</div>'; lastPanelSig=null; return; }
  const sig=panelSig(j);
  if(!force && document.getElementById('panel-body') && document.querySelector('#panel .pnl-h')){
    // Soft: chỉ đổi nút chạy/% — không wipe artifact (hết giật cột phải)
    const st=j.stages[selectedStep]||{};
    const run=panel.querySelector('.pnl-run');
    if(run && st.status==='running'){
      const ss=run.querySelector('.sstat.run');
      if(ss && st.progress!=null) ss.textContent=st.progress+'%';
      else if(!ss){ /* status vừa chuyển running → rebuild header only */ 
        const hdr=panel.querySelector('.pnl-h');
        if(hdr){ const idx=STAGES.findIndex(x=>x.key===selectedStep)+1;
          hdr.innerHTML=`<span><span class="pn">${idx}</span> ${s.name}</span>${stageCtrl(j,s)}`; }
      }
    } else if(sig!==lastPanelSig){
      lastPanelSig=sig;
      const idx=STAGES.findIndex(x=>x.key===selectedStep)+1;
      const hdr=panel.querySelector('.pnl-h');
      if(hdr) hdr.innerHTML=`<span><span class="pn">${idx}</span> ${s.name}</span>${stageCtrl(j,s)}`;
      // chỉ reload artifact khi status/số liệu bước đổi (không mỗi %)
      const artSig=[selectedStep,st.status,st.n_done,st.n_total,st.n_segments,st.n_clips,st.n_missing].join('|');
      if(panel.dataset.artSig!==artSig){ panel.dataset.artSig=artSig; loadArtifact(j, selectedStep); }
    }
    return;
  }
  lastPanelSig=sig;
  const idx=STAGES.findIndex(x=>x.key===selectedStep)+1;
  panel.innerHTML=`<div class="pnl-h"><span><span class="pn">${idx}</span> ${s.name}</span>${stageCtrl(j,s)}</div>`+
    `<div class="artifact" id="panel-body"><div class="afile amuted">Đang tải…</div></div>`;
  loadArtifact(j, selectedStep);
}
function runBoxOpen(){
  try{ return localStorage.getItem('runBoxOpen')!=='0'; }catch(e){ return true; }
}
function toggleRunBox(){
  const box=document.getElementById('run-box'); if(!box) return;
  const open=!box.classList.contains('collapsed');
  box.classList.toggle('collapsed', open);
  try{ localStorage.setItem('runBoxOpen', open?'0':'1'); }catch(e){}
  const c=box.querySelector('.run-box-h .caret'); if(c) c.textContent=open?'▸':'▾';
}
function runBoxHtml(j, isRun, isDone){
  const open=runBoxOpen();
  return `<div class="run-box${open?'':' collapsed'}" id="run-box">
    <div class="run-box-h" onclick="toggleRunBox()" title="Thu gọn / mở rộng">
      <span>Chạy · Look</span>
      <span class="caret">${open?'▾':'▸'}</span>
    </div>
    <div class="run-box-b">
      <div class="studio-cta">
        <button class="btn pri big" ${isRun||isDone?'disabled':''} onclick="runAll('${j.job_id}')">▶ Chạy tự động</button>
        <button class="btn stop" ${isRun?'':'disabled'} onclick="stopJob('${j.job_id}')">⏹ Dừng</button>
        <div class="studio-pipe">${pipelineControlsHtml(j)}</div>
      </div>
      <div>
        <div class="look-head">Look · xuất hình</div>
        ${lookControlsHtml(j)}
      </div>
    </div>
  </div>`;
}
function renderPanelStudio(j, force){
  const panel=document.getElementById('panel'); const s=STAGES.find(x=>x.key===selectedStep);
  if(!s){ panel.innerHTML='<div class="empty pnl-empty">Chọn một bước.</div>'; lastPanelSig=null; return; }
  const isRun=j.status==='running', isDone=j.status==='done';
  const bodySig=panelSig(j);
  const ctaOk=document.querySelector('#panel .studio-cta');
  const stepBox=document.getElementById('panel-step');
  if(!force && ctaOk && stepBox && document.getElementById('run-box')){
    const runBtn=panel.querySelector('.studio-cta .btn.pri.big');
    const stopBtn=panel.querySelector('.studio-cta .btn.stop');
    if(runBtn) runBtn.disabled=!!(isRun||isDone);
    if(stopBtn) stopBtn.disabled=!isRun;
    if(bodySig!==lastPanelSig){
      lastPanelSig=bodySig;
      const idx=STAGES.findIndex(x=>x.key===selectedStep)+1;
      stepBox.innerHTML=`<div class="pnl-h"><span><span class="pn">${idx}</span> ${s.name}</span>${stageCtrl(j,s)}</div>
        <div class="artifact" id="panel-body"><div class="afile amuted">Đang tải…</div></div>`;
      loadArtifact(j, selectedStep);
    } else if(selectedStep==='mux' && (j.stages.mux||{}).status==='running'){
      const ss=stepBox.querySelector('.sstat.run');
      if(ss && j.stages.mux.progress!=null) ss.textContent=j.stages.mux.progress+'%';
    }
    return;
  }
  snapFormPrefs(j.job_id);
  lastPanelSig=bodySig;
  const idx=STAGES.findIndex(x=>x.key===selectedStep)+1;
  panel.innerHTML=`
    ${runBoxHtml(j, isRun, isDone)}
    <div id="panel-step">
      <div class="pnl-h"><span><span class="pn">${idx}</span> ${s.name}</span>${stageCtrl(j,s)}</div>
      <div class="artifact" id="panel-body"><div class="afile amuted">Đang tải…</div></div>
    </div>`;
  Promise.resolve(fillAutoVoices(j)).then(()=>{ applyFormPrefs(j.job_id); fillAutoFrames(); fillAutoLogos(); applyFormPrefs(j.job_id); });
  loadArtifact(j, selectedStep);
}
function renderPanelConsole(j, force){
  const panel=document.getElementById('panel'); const s=STAGES.find(x=>x.key===selectedStep);
  if(!s){ panel.innerHTML='<div class="empty pnl-empty">Chọn một bước.</div>'; lastPanelSig=null; return; }
  const tab=window._consoleTab||'pipeline';
  const sig='console|'+tab+'|'+panelSig(j);
  if(!force && sig===lastPanelSig && document.getElementById('panel-body')) return;
  lastPanelSig=sig;
  const idx=STAGES.findIndex(x=>x.key===selectedStep)+1;
  panel.innerHTML=`
    <div class="ctabs">
      <button type="button" class="ctab ${tab==='pipeline'?'on':''}" data-tab="pipeline" onclick="setConsoleTab('pipeline')">Pipeline</button>
      <button type="button" class="ctab ${tab==='audio'?'on':''}" data-tab="audio" onclick="setConsoleTab('audio')">Audio</button>
      <button type="button" class="ctab ${tab==='look'?'on':''}" data-tab="look" onclick="setConsoleTab('look')">Look</button>
      <button type="button" class="ctab ${tab==='brand'?'on':''}" data-tab="brand" onclick="setConsoleTab('brand')">Brand</button>
    </div>
    <div class="cpanel ${tab==='pipeline'?'on':''}" data-tab="pipeline">
      <div class="pnl-h"><span><span class="pn">${idx}</span> ${s.name}</span>${stageCtrl(j,s)}</div>
      <div class="artifact" id="panel-body"><div class="afile amuted">Đang tải…</div></div>
    </div>
    <div class="cpanel ${tab==='audio'?'on':''}" data-tab="audio">
      <div class="seclabel">1-click audio / TTS</div>
      <div class="look-grid">${pipelineControlsHtml(j)}</div>
      <p class="amuted" style="margin-top:10px;font-size:12px">Chọn rồi bấm Chạy tự động ở cột giữa.</p>
    </div>
    <div class="cpanel ${tab==='look'?'on':''}" data-tab="look">
      <div class="seclabel">Zoom · FX · Frame</div>
      ${lookControlsHtml(j)}
    </div>
    <div class="cpanel ${tab==='brand'?'on':''}" data-tab="brand">
      <div class="seclabel">Brand kênh</div>
      <div class="look-grid">
        <label class="chk">Mode <select id="auto-brand-mode"><option value="off" selected>tắt</option><option value="drift">drift</option><option value="corners">corners</option><option value="drift_seed">drift_seed</option></select></label>
        <input id="auto-brand-text" placeholder="Tên kênh">
        <label class="chk">logo <select id="auto-brand-logo"><option value="off" selected>tắt</option><option value="auto">auto</option></select></label>
      </div>
      <p class="amuted" style="margin-top:10px;font-size:12px">Logo lấy từ assets/overlays/logos.</p>
    </div>`;
  fillAutoVoices(j); fillAutoFrames(); fillAutoLogos();
  if(tab==='pipeline') loadArtifact(j, selectedStep);
}

// Thanh Chạy/Dừng của bước đang xem (nằm ở header panel bên phải).
function stageCtrl(j,s){
  const stg=j.stages[s.key]||{}, st=stg.status||'pending';
  const running=st==='running', done=st==='done';
  const id=j.job_id;
  if(running){
    return `<span class="pnl-run"><span class="sstat run">${stg.progress!=null?stg.progress+'%':'…'}</span>`+
      `<button class="btn stop" onclick="stopJob('${id}')">⏹ Dừng</button></span>`;
  }
  // TTS & Render có điều khiển riêng trong panel (giọng / tùy chọn Demucs) -> header
  // không lặp nút Chạy, chỉ cho biết trạng thái.
  if(s.key==='tts'||s.key==='mux'){
    if(s.key==='tts' && !done){
      const hasPartial=(stg.n_done|0)>0 || (stg.n_clips|0)>0;
      const lab=hasPartial?'▶ Tiếp tục':'▶ Tạo giọng';
      return `<span class="pnl-run"><button class="btn pri" onclick="regenTts('${id}',false)">${lab}</button>`+
        ` <button class="btn" title="Đánh dấu xong ngay" onclick="markStepDone('${id}','tts')">✓ Đánh dấu xong</button></span>`;
    }
    if(s.key==='mux' && !done){
      return `<span class="pnl-run"><button class="btn" title="Đánh dấu xong ngay" onclick="markStepDone('${id}','mux')">✓ Đánh dấu xong</button></span>`;
    }
    return done?'<span class="pnl-run"><span class="sstat ok">Xong</span></span>':'';
  }
  const trD=(j.stages.translate||{}).status==='done', ttsD=(j.stages.tts||{}).status==='done',
        asrD=(j.stages.asr||{}).status==='done', dlD=(j.stages.download||{}).status==='done';
  const ready={download:true,asr:dlD,translate:asrD,tts:trD,ocr:asrD,separate:dlD,mux:(ttsD||j.dub)}[s.key];
  const force=done;
  const label=done?'↻ Chạy lại':'▶ Chạy';
  const cls=done?'btn':'btn pri';
  const dis=ready?'':'disabled title="Cần làm bước trước"';
  const mark=(!done&&!running)?` <button class="btn" title="Đánh dấu xong ngay (misclick / đã có file)" onclick="markStepDone('${id}','${s.key}')">✓ Đánh dấu xong</button>`:'';
  return `<span class="pnl-run"><button class="${cls}" ${dis} onclick="runStep('${id}','${s.key}',${force})">${label}</button>${mark}</span>`;
}

function stepRow(j,s){
  const sep = j.stages.separate || {};
  const sepRun = sep.status === 'running';
  const rawSt = (j.stages[s.key]||{}).status||'pending';
  const isAsrSep = (s.key === 'asr' && sepRun && rawSt !== 'running' && rawSt !== 'done');
  const st = isAsrSep ? 'running' : rawSt;
  const running=st==='running', done=st==='done', stg=j.stages[s.key]||{};
  // REVIEW CLI ngoài _PROCS đôi khi bị heal → pending; vẫn hiện soát nếu đang giữa chừng.
  const midReview = s.key==='translate' && stg.review && !done
    && Number(stg.review_progress||0) > 0 && Number(stg.review_progress||0) < 100
    && (running || st==='pending');
  let sub=s.sub;
  if(st==='failed'&&stg.error){ sub=`<span class="err">⚠ ${escapeHtml(String(stg.error).slice(0,60))}</span>`; }
  else if(running || midReview){
    const p=isAsrSep ? sep.progress : stg.progress; let d='';
    if(isAsrSep){ d=`Demucs tách tiếng ${sep.progress!=null?sep.progress+'%':''}`.trim(); }
    else if(s.key==='download'&&p!=null){ const pc=Math.min(Math.max(p,0),100); d=`${pc}%`+(stg.speed>0?' · '+fmtSize(stg.speed)+'/s':''); }
    else if(s.key==='asr'&&p!=null) d=`${escapeHtml(stg.asr_engine||stg.engine||'')} ${p}% · ${stg.n_segments||0} câu`.trim();
    else if(s.key==='translate'&&stg.review) d=`soát ${stg.review_progress||0}%`;
    else if((s.key==='translate'||s.key==='tts')&&p!=null) d=`${p}% · ${stg.n_done||0}/${stg.n_total||0}`;
    else if((s.key==='ocr'||s.key==='separate'||s.key==='mux')&&p!=null) d=`${p}%`;
    let beat=''; if(stg.updated_at && !isAsrSep){ const ago=Math.max(0,Math.round(Date.now()/1000-stg.updated_at));
      const cur=(stg.cur_text||'').toLowerCase();
      const booting=/start|xep hang|chuan bi|spawn|loading|enroll/.test(cur);
      if(ago>=(booting?180:60)) beat=` · ⚠ treo?`; }
    sub=`<span class="run">● ${d||'đang chạy'}</span>${beat}`;
  } else if(done){
    const miss=stg.n_missing||0;
    const warn=miss?` · <span class="err">⚠ thiếu ${miss}</span>`:'';
    if(s.key==='asr'){
      const src=stg.asr_source||'';
      const engFromSrc=src.includes('fun_nano')?'fun_asr_nano':src.includes('firered')?'firered_asr'
        :src.includes('paraformer')?'paraformer_zh':src.includes('sensevoice')?'sensevoice'
        :(src.includes('aligned')||src.includes('repaired')||src.includes('consensus'))?'hybrid_fa':'';
      const engShow=engFromSrc||stg.asr_engine||stg.engine||stg.model||'';
      const mdl=stg.model&&!((engShow==='fun_asr_nano'&&/firered/i.test(stg.model))||(engShow==='firered_asr'&&/nano|fun/i.test(stg.model)))?stg.model:'';
      sub=`${stg.n_segments||0} câu · ${escapeHtml(engShow)}${mdl&&mdl!==engShow?` · ${escapeHtml(mdl)}`:''}`;
    }
    else if(s.key==='translate'){
      const asrN=(j.stages.asr||{}).n_segments;
      const n=stg.n_segments||0;
      const splitNote=(asrN&&n&&n!==asrN)?` · tách sub từ ${asrN}`:'';
      const stale=stg.stale||(stg.asr_sync&&stg.asr_sync.stale)?` · <span class="err">⚠ lệch ASR</span>`:'';
      sub=`${n} dòng`+splitNote+(stg.reviewed?' · đã soát':'')+stale+warn;
    }
    else if(s.key==='ocr') sub=`${stg.n_texts||0} chữ`;
    else if(s.key==='tts'&&stg.n_clips!=null) sub=`${stg.n_clips} câu`+warn;
  }
  const icon=done?'✓':st==='failed'?'!':running?'':(s.opt?'+':(STAGES.findIndex(x=>x.key===s.key)+1));
  const trD=(j.stages.translate||{}).status==='done', ttsD=(j.stages.tts||{}).status==='done', asrD=(j.stages.asr||{}).status==='done', dlD=(j.stages.download||{}).status==='done';
  const ready={download:true,asr:dlD,translate:asrD,tts:trD,ocr:asrD,separate:dlD,mux:(ttsD||j.dub)}[s.key];
  // Cột 2 chỉ hiện TRẠNG THÁI; nút Chạy/Dừng nằm ở panel bên phải.
  let right='';
  if(running){
    const prg = isAsrSep ? sep.progress : stg.progress;
    right=`<span class="sstat run">${prg!=null?prg+'%':'…'}</span>`;
  }
  else if(done){ right=`<span class="sstat ok">Xong</span>`; }
  else if(st==='failed'){ right=`<span class="sstat err">Lỗi</span>`; }
  else if(ready){ right=`<span class="sstat rdy">Sẵn sàng</span>`; }
  else{ right=`<span class="sstat">Chờ</span>`; }
  const prgVal = isAsrSep ? sep.progress : stg.progress;
  const mini=(running&&prgVal!=null)?`<div class="miniline"><i style="width:${Math.min(prgVal,100)}%"></i></div>`:'';
  const nodeCls=done?'ok':st==='failed'?'err':running?'run':'';
  const opt=s.opt?`<span class="opt">tùy chọn</span>`:'';
  const sel=selectedStep===s.key?'sel':'';
  return `<div class="step clk ${sel}" onclick="selectStep('${s.key}')">
    <div class="snrel"><div class="snode ${nodeCls}">${icon}</div></div>
    <div class="sinfo"><div class="sn">${s.name}${opt}</div><div class="ssub">${sub}</div>${mini}</div>
    <div class="sright">${right}</div></div>`;
}

function buildLiveLog(j){
  const rows=[];
  const runS=STAGES.find(s=>(j.stages[s.key]||{}).status==='running');
  if(runS){
    const stg=j.stages[runS.key]||{};
    const pct=stg.progress!=null?` ${stg.progress}%`:'';
    const cur=stg.cur_text?` · ${escapeHtml(String(stg.cur_text).slice(0,120))}`:'';
    rows.push({t:'',cls:'',x:`<b style="color:var(--accent)">● ${escapeHtml(runS.name)}${pct}</b>${cur}`});
    if(stg.n_done!=null||stg.n_total!=null){
      rows.push({t:'',cls:'',x:`<span class="amuted">${stg.n_done||0}/${stg.n_total||'?'} · ${escapeHtml(stg.engine||stg.asr_engine||stg.tts_engine||'')}</span>`});
    }
  }
  STAGES.filter(s=>(j.stages[s.key]||{}).status==='done').map(s=>({s,at:(j.stages[s.key]||{}).finished_at||''}))
    .sort((a,b)=>(b.at||'').localeCompare(a.at||'')).slice(0,6).forEach(({s,at})=>{
      const stg=j.stages[s.key]||{}; let ex='';
      if(s.key==='asr') ex=` · ${stg.n_segments||0} câu`;
      if(s.key==='translate'){
        const miss=stg.n_missing|0;
        ex=` · ${stg.n_segments||stg.n_done||0} dòng`+(miss?` · <span class="er">thiếu ${miss}</span>`:'');
      }
      if(s.key==='tts') ex=` · ${stg.n_clips||stg.n_done||0} câu`;
      if(s.key==='ocr') ex=` · ${stg.n_texts||0} chữ`;
      if(s.key==='mux') ex=' · output.mp4';
      rows.push({t:hhmm(at),cls:'ok',x:`<span class="ok">✓</span> ${s.name} xong${ex}`});
    });
  STAGES.filter(s=>(j.stages[s.key]||{}).status==='failed').forEach(s=>{
    const err=((j.stages[s.key]||{}).error||'').toString().slice(0,80);
    rows.push({t:'',cls:'er',x:`<span class="er">⚠ ${s.name}${err?': '+escapeHtml(err):''}</span>`});
  });
  const body=rows.length?rows.map(r=>`<div class="le"><span class="t">${r.t}</span><span>${r.x}</span></div>`).join(''):'<div class="le amuted">Chưa bắt đầu — bấm Chạy ở cột trái.</div>';
  const live=j.status==='running'?'<span class="live"><i></i> trực tiếp</span>':'';
  return `<div class="studio-live"><div class="log"><div class="logh"><span>Nhật ký làm việc</span>${live}</div><div class="logbody" id="logbody">${body}</div></div></div>`;
}
// log thu gọn (classic / console)
function buildLog(j){
  const rows=[];
  const runS=STAGES.find(s=>(j.stages[s.key]||{}).status==='running');
  if(runS){ rows.push({t:'',cls:'',x:`<b style="color:var(--accent)">● Đang ${runS.name.toLowerCase()}…</b>`}); }
  STAGES.filter(s=>(j.stages[s.key]||{}).status==='done').map(s=>({s,at:(j.stages[s.key]||{}).finished_at||''}))
    .sort((a,b)=>(b.at||'').localeCompare(a.at||'')).forEach(({s,at})=>{
      const stg=j.stages[s.key]; let ex='';
      if(s.key==='asr') ex=` · ${stg.n_segments||0} câu`;
      if(s.key==='ocr') ex=` · ${stg.n_texts||0} chữ`;
      if(s.key==='mux') ex=' · output.mp4';
      rows.push({t:hhmm(at),cls:'ok',x:`<span class="ok">✓</span> ${s.name} xong${ex}`});
    });
  STAGES.filter(s=>(j.stages[s.key]||{}).status==='failed').forEach(s=>rows.push({t:'',cls:'er',x:`<span class="er">⚠ ${s.name} lỗi</span>`}));
  const body=rows.length?rows.map(r=>`<div class="le"><span class="t">${r.t}</span><span>${r.x}</span></div>`).join(''):'<div class="le amuted">Chưa bắt đầu.</div>';
  const live=j.status==='running'?'<span class="live"><i></i> trực tiếp</span>':'';
  return `<div class="log"><div class="logh" onclick="toggleLog()"><span class="caret">${logOpen?'▾':'▸'}</span> Nhật ký ${live}</div>${logOpen?`<div class="logbody" id="logbody">${body}</div>`:''}</div>`;
}
function toggleLog(){ logOpen=!logOpen; if(lastFull) renderDetail(lastFull); }
async function feedSentences(j){
  const tr=j.stages.translate||{}; if(tr.status!=='running'&&tr.status!=='done') return;
  try{ const rows=await (await fetch('/api/jobs/'+j.job_id+'/file/script.json')).json();
    const vi=rows.filter(r=>r.vi).slice(-3).reverse(); const b=document.getElementById('logbody'); if(!b||!vi.length) return;
    const sig=vi.map(r=>r.i+':'+(r.vi||'').slice(0,20)).join('|');
    if(b.dataset.feedSig===sig) return;
    b.dataset.feedSig=sig;
    // thay 3 dòng đầu thay vì insert mãi (tránh log dài + giật)
    const block=vi.map(r=>`<div class="le feed"><span class="t">câu ${r.i}</span><span><span class="zh2 mono">${escapeHtml((r.zh||'').slice(0,20))}</span> <span class="ar">→</span> ${escapeHtml(r.vi||'')}</span></div>`).join('');
    b.querySelectorAll('.le.feed').forEach(el=>el.remove());
    b.insertAdjacentHTML('afterbegin', block);
  }catch(e){}
}

function toggle(key){ if(expanded.has(key)) expanded.delete(key); else expanded.add(key); if(lastFull) renderDetail(lastFull); }

async function loadArtifact(j,key){
  const box=document.getElementById('panel-body'); if(!box) return;
  const F=name=>'/api/jobs/'+j.job_id+'/file/'+name;
  try{
    if(key==='download'){
      const dl=j.stages.download||{};
      const vp=dl.video_path||''; const name=vp.split(/[\\/]/).pop()||'source.mp4';
      const done=dl.status==='done';
      const impNote=dl.imported||(dl.source||'').startsWith('imported')
        ?` · <span class="ok">imported</span>${dl.orig_name?` · ${escapeHtml(dl.orig_name)}`:''}`:'';
      const preview=done
        ?`<div class="afile">🎬 <b>${escapeHtml(name)}</b> <span class="amuted" id="dlsize">…</span>${impNote} <button class="linkbtn" onclick="revealJob('${j.job_id}')">📂 Mở thư mục</button></div><div id="pslot"></div>`
        :`<div class="afile amuted">Chưa có source — tải bằng yt-dlp hoặc import file sẵn.</div>`;
      const importUi=`<div class="afile">📥 Import source <input type="file" accept="video/*,.mp4,.mkv,.webm,.mov" onchange="uploadSource('${j.job_id}',this)"> <span class="amuted" id="src-up-st"></span></div>
        <div class="afile">📁 Path local (file lớn): <input id="src-path" placeholder="D:\\video\\file.mp4" style="min-width:260px"> <button class="btn" onclick="importSourcePath('${j.job_id}')">Dùng path</button> <label class="amuted"><input type="checkbox" id="src-move"> move</label></div>`;
      box.innerHTML=importUi+preview;
      if(done){ ensureVideo(j,true); fetch('/api/jobs/'+j.job_id+'/video',{method:'HEAD'}).then(r=>{const e=document.getElementById('dlsize'); if(e) e.textContent=fmtSize(r.headers.get('content-length'));}).catch(()=>{}); }
    }
    else if(key==='asr'){
      const cur=j.stages.asr||{};
      // Nguồn đang dùng = sự thật (asr_source / số câu), KHÔNG lấy tip từ dropdown "chạy"
      const srcNow=cur.asr_source||'';
      const activeEng=srcNow.includes('fun_nano')?'fun_asr_nano'
        :srcNow.includes('firered')?'firered_asr'
        :srcNow.includes('paraformer')?'paraformer_zh'
        :srcNow.includes('sensevoice')?'sensevoice'
        :(srcNow.includes('aligned')||srcNow.includes('consensus')||srcNow.includes('repaired'))?'hybrid_fa'
        :(cur.asr_engine||cur.engine)||curAsrEngine(j);
      const runEng=activeEng;  // dropdown khớp bản đang dùng (không lấy engine lệch)
      const engSel=`🎤 Chạy ASR mới: <select id="asr-engine" onchange="asrEngineChanged('${j.job_id}')">
        <option value="nano_fa" ${runEng==='nano_fa'||!runEng?'selected':''}>⭐ Nano + Direct FA (Khuyên dùng — Không đúp câu)</option>
        <option value="hybrid_fa" ${runEng==='hybrid_fa'?'selected':''}>Hybrid FA (Nano+Para+FA, no repair)</option>
        <option value="fun_asr_nano" ${runEng==='fun_asr_nano'?'selected':''}>Fun-ASR-Nano</option>
        <option value="paraformer_zh" ${runEng==='paraformer_zh'?'selected':''}>Paraformer</option>
        <option value="sensevoice" ${runEng==='sensevoice'?'selected':''}>SenseVoice</option>
        <option value="firered_asr" ${runEng==='firered_asr'?'selected':''}>FireRedASR2</option>
      </select> <span class="amuted">· chọn rồi bấm Chạy = ASR lại từ audio</span>`;
      const activeTip={
        fun_asr_nano:'Fun-ASR-Nano · chữ tốt, timing VAD thô',
        firered_asr:'FireRedASR2-AED',
        paraformer_zh:'Paraformer · timing tốt',
        sensevoice:'SenseVoiceSmall',
        hybrid_fa:'Hybrid FA+Repair',
      }[activeEng]||'';
      let rows=null;
      try{ const r=await fetch(F('asr.json')+'?t='+Date.now(),{cache:'no-store'}); if(r.ok) rows=await r.json(); }catch(e){}
      let srcRow='', quick='';
      try{
        const sr=await fetch('/api/jobs/'+j.job_id+'/asr-sources',{cache:'no-store'});
        if(sr.ok){
          const sd=await sr.json();
          const srcItems=(sd.items||[]).filter(x=>x.file!=='asr.json');
          const act=srcNow||sd.active||'';
          const nano=srcItems.find(x=>x.file==='asr.fun_nano.json');
          const fire=srcItems.find(x=>x.file==='asr.firered.json');
          if(nano||fire){
            const onNano=act==='asr.fun_nano.json';
            const onFire=act==='asr.firered.json';
            quick=`<div class="afile" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
              <b>Đổi bản chữ đang xem:</b>
              ${nano?`<button class="btn ${onNano?'pri':''}" onclick="selectAsrSource('${j.job_id}','asr.fun_nano.json')">Nano · ${nano.n}${onNano?' ✓':''}</button>`:''}
              ${fire?`<button class="btn ${onFire?'pri':''}" onclick="selectAsrSource('${j.job_id}','asr.firered.json')">FireRed · ${fire.n}${onFire?' ✓':''}</button>`:''}
              <span class="amuted" id="asr-src-st"></span>
            </div>`;
          }
          if(srcItems.length>=1){
            const opts=srcItems.map(x=>`<option value="${escapeHtml(x.file)}" ${x.file===act?'selected':''}>${escapeHtml(x.label)} · ${x.n}</option>`).join('');
            srcRow=`<div class="afile">📂 Khác: <select id="asr-source" onchange="selectAsrSource('${j.job_id}')">${opts}</select></div>`;
          }
        }
      }catch(e){}
      const head=`<div class="afile">${engSel}</div>`+quick+srcRow;
      if(!Array.isArray(rows)){
        box.innerHTML=head+`<div class="afile amuted">${cur.status==='running'?'⏳ Đang nhận diện lời…':'Chưa có asr.json — chọn engine rồi bấm Chạy.'}</div>`;
        return;
      }
      const modelLbl=activeTip.split(' · ')[0]||activeEng;
      // Cảnh báo nếu nhãn engine lệch số câu / nguồn
      const expectN={fun_asr_nano:1825,firered_asr:2060}[activeEng];
      const warn=(srcNow.includes('fun_nano')&&rows.length!==1825&&rows.length>0)
        ||(srcNow.includes('firered')&&rows.length<2000)
        ||((cur.asr_engine||'')==='fun_asr_nano' && srcNow.includes('firered'))
        ||((cur.asr_engine||'')==='firered_asr' && srcNow.includes('fun_nano'))
        ?`<div class="afile err">⚠ Lệch nhãn/nguồn — bấm lại Nano/FireRed bên trên để đồng bộ.</div>`:'';
      const peek=rows.slice(0,5).map(r=>`<div class="amuted mono" style="padding:1px 0">${fmtDur(r.start)} · ${escapeHtml((r.text||'').slice(0,72))}</div>`).join('');
      const list=rows.map((r,ix)=>`<tr><td class="ct amuted">${r.i!=null?r.i:ix}</td><td class="ct" onclick="seekTo(${r.start})">${fmtDur(r.start)}</td><td class="ct amuted">${fmtDur(r.end)}</td><td>${escapeHtml(r.text||'')}</td></tr>`).join('');
      const csBtn=`<div class="afile">🧠 Context Studio (PRE) <button class="btn pri" onclick="runContextStudio('${j.job_id}',true)">▶ Analyze · LLM</button> <button class="btn" onclick="runContextStudio('${j.job_id}',false)">heuristic</button> <button class="btn" onclick="runContextStudio('${j.job_id}',true,true)">↻ Force</button> <button class="linkbtn" onclick="showContextStudio('${j.job_id}')">👁 Xem pack</button> <span class="amuted" id="cs-st"></span></div><div id="cs-box"></div>`;
      box.innerHTML=head+warn
        +`<div class="afile" style="border-left:3px solid var(--accent);padding-left:10px"><b>ĐANG DÙNG:</b> ${escapeHtml(modelLbl)} · <b>${rows.length}</b> câu (full asr.json)${srcNow?` · <code>${escapeHtml(srcNow)}</code>`:''}${activeTip?` · <span class="amuted">${escapeHtml(activeTip)}</span>`:''} · <a href="/api/jobs/${j.job_id}/srt/zh">⬇ .srt</a> · <a href="/api/jobs/${j.job_id}/file/asr.json" target="_blank">⬇ asr.json</a><div style="margin-top:6px">${peek}</div></div>`
        +csBtn+`<div class="atable"><table><thead><tr><th>#</th><th>bắt đầu</th><th>kết</th><th>text</th></tr></thead><tbody>${list}</tbody></table></div>`;
      void showContextStudio(j.job_id, true);
    }
    else if(key==='translate'){ await artTranslate(j,box,F); }
    else if(key==='tts'){ await artTts(j,box,F); }
    else if(key==='ocr'){ await artOcr(j,box,F); }
    else if(key==='separate'){ artSeparate(j,box,F); }
    else if(key==='mux'){ artMux(j,box); }
  }catch(e){ box.innerHTML=`<div class="afile amuted">Không đọc được: ${e}</div>`; }
}

let CACHED_LLM_MODELS = null;
async function loadDynamicModels(selectId, preferredVal, force = false){
  const sel = document.getElementById(selectId || 'tr-model');
  if(!sel) return;
  const cur = preferredVal || sel.value;
  try {
    if(!CACHED_LLM_MODELS || force){
      const r = await fetch('/api/models', {cache: 'no-store'});
      if(r.ok){
        CACHED_LLM_MODELS = await r.json();
      }
    }
    if(CACHED_LLM_MODELS && CACHED_LLM_MODELS.models && CACHED_LLM_MODELS.models.length){
      const items = CACHED_LLM_MODELS.models;
      let html = '';
      items.forEach(m => {
        const isSel = (m.id === cur) ? 'selected' : '';
        html += `<option value="${escapeHtml(m.id)}" ${isSel}>${escapeHtml(m.label || m.id)}</option>`;
      });
      html += `<option value="" ${!cur ? 'selected' : ''}>(mặc định config)</option>`;
      sel.innerHTML = html;
      if(cur) sel.value = cur;
    }
  } catch(e){}
}

async function artTranslate(j,box,F){
  const trSt=j.stages.translate||{}; let rows=null,gloss=null;
  // Ưu tiên bản đã CUT (display) để UI/SRT không hiện câu VI dài → nhảy 2–3 dòng
  try{const r=await fetch(F('script.display.json'),{cache:'no-store'}); if(r.ok){ const d=await r.json(); if(Array.isArray(d)&&d.length) rows=d; }}catch(e){}
  if(!rows||!rows.length){ try{const r=await fetch(F('script.json'),{cache:'no-store'}); if(r.ok) rows=await r.json();}catch(e){} }
  try{const g=await fetch(F('glossary.json'),{cache:'no-store'}); if(g.ok) gloss=await g.json();}catch(e){}
  if(trSt.status==='running'){ box.innerHTML=`<div class="afile">⏳ Đang ${trSt.review?'soát':'dịch'}… theo dõi ở dòng bước.</div>`+(rows?tblTrans(j,rows):''); return; }
  const done=trSt.model&&trSt.status==='done'?'· đã dịch: '+escapeHtml(trSt.model):''; const rev=trSt.reviewed?' · đã soát: '+escapeHtml(trSt.review_model||'?'):'';
  const modeNow=trSt.mode||'auto';
  const ctrl=`<div class="afile">🈺 <select id="tr-model"><option value="gemma4-12b-qat-mtp-fast">gemma4-12b-qat-mtp-fast (:8083 ⭐)</option><option value="gemma4-12b">gemma4-12b (:8083)</option><option value="qwen36-tq3">qwen36-tq3 (:8083)</option><option value="local/gemma4">gemma4 (Ollama)</option><option value="local/gemma3:12b">gemma3:12b (Ollama)</option><option value="">(mặc định config)</option></select> <button class="btn sm" onclick="loadDynamicModels('tr-model',null,true)" title="Làm mới danh sách models đang chạy trên 8083 / Ollama">🔄</button> <select id="tr-mode" title="Cách dịch — batch giữ mạch tốt hơn"><option value="batch" selected>batch (lô nhỏ · mạch tốt)</option><option value="oneshot">oneshot (cả file 1 lần)</option><option value="auto">auto</option></select> <button class="btn pri" onclick="doTranslate('${j.job_id}','single')" title="Xoá script cũ và dịch lại toàn bộ">↻ Dịch lại toàn bộ</button><span class="amuted"> ${done}${rev}${modeNow&&trSt.status==='done'?' · mode '+escapeHtml(modeNow):''}</span></div>`+
    `<div class="afile">⚡ 2 lượt: <button class="btn" onclick="doTranslate('${j.job_id}','2pass')">batch nháp → gemma3 rà văn phong</button>`+(rows?` <button class="btn" onclick="doTranslate('${j.job_id}','review')">🖌 Rà soát văn phong</button>`:'')+` · hoặc header bước: <b>↻ Chạy lại</b></div>`+
    (rows?`<div class="afile">🎯 <b>Dịch tối ưu</b>: <button class="btn pri" onclick="runTranslateAlts('${j.job_id}')">Quét phương án (ngữ cảnh+văn phong)</button> <button class="linkbtn" onclick="showTranslateAlts('${j.job_id}')">👁 Xem & chọn</button> <span class="amuted" id="alts-st"></span></div><div id="alts-box"></div>`:'');
  if(!rows){ box.innerHTML=ctrl+`<div class="afile">🧠 Context Studio (PRE) <button class="btn pri" onclick="runContextStudio('${j.job_id}',true)">▶ Analyze · LLM</button> <button class="btn" onclick="runContextStudio('${j.job_id}',false)">heuristic</button> <button class="linkbtn" onclick="showContextStudio('${j.job_id}')">👁 Xem</button> <span class="amuted" id="cs-st"></span></div><div id="cs-box"></div><div class="afile amuted">Chưa có bản dịch — Analyze sau ASR, rồi Dịch.</div>`; void showContextStudio(j.job_id, true); void loadDynamicModels('tr-model', trSt.model); return; }
  const gsum=gloss?`<div class="afile amuted">📘 ${((gloss.characters||[]).length)} nhân vật · ${((gloss.terms||[]).length)} thuật ngữ</div>`:'';
  const csRow=`<div class="afile">🧠 Context Studio (PRE) <button class="btn pri" onclick="runContextStudio('${j.job_id}',true)">▶ Analyze · LLM</button> <button class="btn" onclick="runContextStudio('${j.job_id}',false)">heuristic</button> <button class="btn" onclick="runContextStudio('${j.job_id}',true,true)">↻ Force</button> <button class="linkbtn" onclick="showContextStudio('${j.job_id}')">👁 Xem</button> <span class="amuted" id="cs-st"></span></div><div id="cs-box"></div>`;
  const miss=rows.filter(r=>!r.vi).length;
  const asrN=(j.stages.asr||{}).n_segments;
  const splitNote=(asrN&&rows.length!==asrN)?` · <span class="amuted">ASR ${asrN} → tách phụ đề ${rows.length}</span>`:'';
  const missBox=miss?`<div class="afile"><span class="err">⚠ ${miss} câu chưa dịch (trống)</span> <button class="btn pri" onclick="fillTranslate('${j.job_id}')">🩹 Dịch vá ${miss} câu</button> <span class="amuted">sau dịch đã tự kiểm tra missing · bấm để vá thêm</span></div>`:`<div class="afile amuted">✓ Kiểm tra missing: đủ bản dịch</div>`;
  const sync=trSt.asr_sync||{};
  const staleBox=sync.stale?`<div class="afile" style="border-left:3px solid var(--err);padding-left:10px"><span class="err">⚠ Dịch lệch ASR:</span> ${escapeHtml(sync.reason||'')} · ASR <b>${sync.n_asr||'?'}</b> · script <b>${sync.n_script||'?'}</b> · ZH khớp ${sync.zh_match||0}. <button class="btn pri" onclick="doTranslate('${j.job_id}','single')">↻ Dịch lại theo ASR hiện tại</button></div>`:'';
  box.innerHTML=ctrl+csRow+gsum+staleBox+missBox+`<div class="afile">📄 ${rows.length} dòng${miss?` · <span style="color:var(--ok)">${rows.length-miss} đã dịch</span>`:''}${splitNote} · <a href="/api/jobs/${j.job_id}/srt/vi">⬇ .srt (Việt)</a> · <a href="/api/jobs/${j.job_id}/srt/zh">⬇ .srt (Trung)</a> <button class="linkbtn" onclick="revealJob('${j.job_id}','sub.vi.srt')">📂</button> <button class="btn" onclick="cleanZhSpam('${j.job_id}')" title="Rút 哈哈/呃呃 spam từ ASR">🧹 Rút spam ZH</button></div>`+
    `<div class="afile amuted">✏️ Sửa ô Trung hoặc Việt · Enter/blur = lưu · Shift+Enter xuống dòng</div>`+tblTrans(j,rows);
  void showContextStudio(j.job_id, true);
  void loadDynamicModels('tr-model', trSt.model);
}
async function runContextStudio(id, llm, force){
  const st=document.getElementById('cs-st');
  if(st) st.textContent=llm?'⏳ PRE-SCAN LLM…':'⏳ PRE-SCAN heuristic…';
  try{
    const r=await fetch('/api/jobs/'+id+'/context-studio',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({llm:!!llm, force:!!force})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){ if(st) st.textContent=''; alert(d.error||r.status); return; }
    if(d.already==='ready' && !force){
      if(st) st.textContent='đã có pack';
      await showContextStudio(id);
      if(confirm('Đã có context_pack.json. Chạy lại (force)?')) return runContextStudio(id, llm, true);
      return;
    }
    if(d.already==='running'){ if(st) st.textContent='đang chạy…'; pollContextStudio(id); return; }
    if(d.started){ pollContextStudio(id); return; }
    await showContextStudio(id);
  }catch(e){ if(st) st.textContent=''; alert('Lỗi: '+e); }
}
function pollContextStudio(id){
  let n=0;
  const tick=async()=>{
    n++;
    try{
      const d=await (await fetch('/api/jobs/'+id+'/context-studio',{cache:'no-store'})).json();
      const st=document.getElementById('cs-st');
      const s=d.stage||{};
      if(st) st.textContent=(s.status==='running'?`⏳ ${s.progress||0}% ${s.cur_text||''}`:(s.status==='done'?'✓ xong':(s.status==='failed'?'✗ '+(s.error||'lỗi'):'')));
      if(s.status==='done'||s.status==='failed'){ await showContextStudio(id); lastDetailSig=null; void refresh(); return; }
    }catch(e){}
    if(n<120) setTimeout(tick, 2000);
  };
  setTimeout(tick, 800);
}
async function showContextStudio(id, quiet){
  const box=document.getElementById('cs-box');
  const stEl=document.getElementById('cs-st');
  if(!box && quiet) return;
  try{
    const d=await (await fetch('/api/jobs/'+id+'/context-studio',{cache:'no-store'})).json();
    const s=d.stage||{}, p=d.preview||{}, f=d.flags||{};
    if(stEl && s.status) stEl.textContent=s.status==='done'?'✓ pack sẵn sàng':(s.status==='running'?`⏳ ${s.progress||0}%`:(s.status==='failed'?'✗ lỗi':''));
    if(!box) return;
    if(!d.ready && !p.genre){
      if(!quiet) box.innerHTML=`<div class="afile amuted">Chưa có pack — bấm Analyze.</div>`;
      return;
    }
    const chars=(p.characters||[]).slice(0,12).map(c=>`${escapeHtml(c.zh||'')}${c.vi?' → '+escapeHtml(c.vi):''}${c.self_ref?' ('+escapeHtml(c.self_ref)+')':''}`).join(' · ')||'—';
    const terms=(p.terms||[]).slice(0,10).map(t=>escapeHtml(t.zh||'')+(t.vi?'='+escapeHtml(t.vi):'')).join(', ')||'—';
    box.innerHTML=`<div class="afile">📦 <b>${escapeHtml(p.genre||'?')}</b> · style <b>${escapeHtml(p.style||'?')}</b>${p.source?` · ${escapeHtml(p.source)}`:''}${f.n_suspicious!=null?` · ⚠ số nghi ${f.n_suspicious}/${f.n_total||0}`:''}<br><span class="amuted">${escapeHtml(p.tone||'')}</span><br>👤 ${chars}<br>📖 ${terms}${p.forbid&&p.forbid.length?`<br>🚫 forbid: ${p.forbid.map(escapeHtml).join(', ')}`:''}<br><button class="linkbtn" onclick="revealJob('${id}','glossary.json')">📂 glossary</button> · <button class="linkbtn" onclick="revealJob('${id}','style_lock.json')">style_lock</button> · <button class="linkbtn" onclick="revealJob('${id}','qa_flags.json')">qa_flags</button></div>`;
  }catch(e){ if(box && !quiet) box.innerHTML=`<div class="afile amuted">Không đọc pack: ${e}</div>`; }
}
function tblTrans(j,rows){
  const list=rows.map(r=>{
    const ii=(r.i!=null?r.i:0);
    const empty=!(r.vi||'').trim();
    const mark=r.manual?'<span class="vi-mark" title="đã sửa tay">✎</span>':(r.rv?'<span class="vi-mark" style="color:var(--ok)" title="đã soát">✓</span>':(empty?'<span class="vi-mark err" title="trống">⚠</span>':''));
    return `<tr data-i="${ii}" class="${empty?'miss-tr':''}" id="tr-line-${ii}"><td class="ct" onclick="seekTo(${r.start})">${fmtDur(r.start)}</td><td class="zh"><span class="zh-edit" contenteditable="true" data-job="${j.job_id}" data-i="${ii}" data-field="zh" onfocus="lineEditFocus(this)" oninput="lineEditInput(this)" onkeydown="lineEditKey(event,this)" onblur="lineEditSave(this)">${escapeHtml(r.zh||'')}</span></td><td class="vi">${mark}<span class="vi-edit" contenteditable="true" data-job="${j.job_id}" data-i="${ii}" data-field="vi" onfocus="lineEditFocus(this)" oninput="lineEditInput(this)" onkeydown="lineEditKey(event,this)" onblur="lineEditSave(this)">${escapeHtml(r.vi||'')}</span></td></tr>`;
  }).join('');
  return `<div class="atable" id="tbl-trans"><table><colgroup><col class="c-t"><col class="c-zh"><col class="c-vi"></colgroup><tbody>${list}</tbody></table></div>`;
}
function lineEditFocus(el){ if(el.dataset.orig==null) el.dataset.orig=el.innerText; }
function lineEditInput(el){ el.classList.toggle('dirty', (el.innerText||'').trim()!==(el.dataset.orig||'').trim()); el.classList.remove('saved'); }
function lineEditKey(ev,el){
  if(ev.key==='Enter' && !ev.shiftKey){ ev.preventDefault(); el.blur(); }
  if(ev.key==='Escape'){ el.innerText=el.dataset.orig||''; el.classList.remove('dirty'); el.blur(); }
}
async function lineEditSave(el){
  const job=el.dataset.job, i=parseInt(el.dataset.i,10), field=el.dataset.field||'vi';
  const val=(el.innerText||'').trim();
  const orig=(el.dataset.orig||'').trim();
  if(!job || Number.isNaN(i)) return;
  if(val===orig){ el.classList.remove('dirty'); return; }
  el.classList.add('dirty');
  const body={i}; body[field]=val;
  try{
    const r=await fetch('/api/jobs/'+job+'/script-line',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){ alert('Không lưu được: '+(d.error||r.status)); return; }
    if(field==='zh' && d.zh!=null) el.innerText=d.zh;
    el.dataset.orig=(field==='zh' && d.zh!=null)?d.zh:val;
    el.classList.remove('dirty'); el.classList.add('saved');
    const tr=el.closest('tr');
    if(tr){ let m=tr.querySelector('.vi-mark'); if(!m){ m=document.createElement('span'); m.className='vi-mark'; const vi=tr.querySelector('.vi-edit'); if(vi) vi.parentElement.insertBefore(m, vi); } m.textContent='✎'; m.title='đã sửa tay'; m.style.color=''; }
    if(d.tts_stale){ const tip=document.createElement('span'); tip.className='amuted'; tip.style.marginLeft='6px'; tip.textContent='· TTS câu này cần đọc lại'; el.parentElement.appendChild(tip); setTimeout(()=>tip.remove(),4000); }
    setTimeout(()=>el.classList.remove('saved'),1200);
  }catch(e){ alert('Lỗi lưu: '+e); }
}
async function cleanZhSpam(id){
  if(!confirm('Rút chuỗi 哈哈/呃呃 spam trong toàn bộ bản dịch (ZH)? Câu VI trống kiểu cười sẽ được điền gọn.')) return;
  try{
    const r=await fetch('/api/jobs/'+id+'/script-clean-spam',{method:'POST'});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){ alert('Lỗi: '+(d.error||r.status)); return; }
    alert(`Đã rút spam: ${d.n_zh_cleaned||0} câu ZH · điền ${d.n_vi_filled||0} câu VI trống`);
    if(typeof lastDetailSig!=='undefined'){ lastDetailSig=null; } if(typeof lastPanelSig!=='undefined'){ lastPanelSig=null; }
    if(typeof refresh==='function') await refresh();
  }catch(e){ alert('Lỗi: '+e); }
}
function viEditFocus(el){ lineEditFocus(el); }
function viEditInput(el){ lineEditInput(el); }
function viEditKey(ev,el){ lineEditKey(ev,el); }
async function viEditSave(el){ return lineEditSave(el); }

let _ttsCache=null;   // {jobId, script, byI} cho nút "Xem tất cả clip"
async function artTts(j,box,F){
  const dub=j.dub, cur=j.stages.tts||{}; await loadVoices();
  const made=cur.status==='done', running=cur.status==='running';
  const partial=!made && !running && ((cur.n_done|0)>0 || (cur.n_clips|0)>0);
  // Nhãn giọng: CHỈ hiện "đã đọc bằng" khi ĐÃ có bản đọc (tts done). Chưa tạo thì
  // dropdown chính là giọng sẽ dùng -> không hiện "đang dùng" gây hiểu nhầm.
  const usingLbl=made?`<span class="amuted" id="tts-using"> · đã đọc bằng: ${escapeHtml(cur.voice||'?')}</span>`:`<span class="amuted" id="tts-using"></span>`;
  const eng=cur.tts_engine||'vieneu';
  const engSel=`🧠 <select id="tts-engine" onchange="engineChanged('${j.job_id}')"><option value="vieneu" ${eng==='vieneu'?'selected':''}>VieNeu (GPU, chữ chuẩn)</option><option value="omnivoice" ${eng==='omnivoice'?'selected':''}>OmniVoice (GPU, non-AR)</option></select>`;
  const mainBtn=running?'':(partial
    ?`<button class="btn pri" onclick="regenTts('${j.job_id}',false)">▶ Tiếp tục (${cur.n_done||cur.n_clips||0} đã có)</button> <button class="btn" onclick="regenTts('${j.job_id}',true)">↻ Tạo lại từ đầu</button>`
    :made
      ?`<button class="btn pri" onclick="regenTts('${j.job_id}',false)">🩹 Đọc bù / tiếp</button> <button class="btn" onclick="regenTts('${j.job_id}',true)">↻ Tạo lại từ đầu</button>`
      :`<button class="btn pri" onclick="regenTts('${j.job_id}',false)">▶ Tạo giọng</button>`);
  const ctrl=`<div class="afile">${engSel} 🗣️ <select id="tts-voice" onchange="voiceChanged('${j.job_id}',this,${made})"></select> ⏩ <select id="tts-speed" onchange="saveTtsSpeed('${j.job_id}',this.value)">${speedOptions(cur.speed)}</select> <button class="linkbtn" onclick="previewVoice()">🔊 Nghe thử</button> ${mainBtn}<span class="amuted" id="tts-prev-st"></span>${usingLbl}<audio id="voice-preview" controls preload="none" class="dubplayer" style="display:none"></audio></div>`+
    `<div class="afile amuted">${eng==='omnivoice'?'OmniVoice-vietnamese: non-AR (ít rớt chữ nhất), chạy GPU. License chỉ nghiên cứu.':'VieNeu v3-Turbo: G2P tiếng Việt riêng, chạy GPU (~1.1GB VRAM). Zero-shot clone từ mẫu ngắn. Dừng rồi bấm Tiếp tục = resume, không mất câu đã đọc.'}</div>`+
    `<div class="afile">🎙️ Thêm giọng (clone): <input id="clone-name" placeholder="tên giọng" style="width:110px"> <input type="file" id="clone-file" accept="audio/*"> <button class="linkbtn" onclick="cloneVoice()">➕ Thêm</button><span class="amuted" id="clone-st"> · mẫu 3-8s, tự cắt</span></div>`
     + `<div class="afile">📁 Audio ngoài: <input type="file" accept="audio/*" onchange="uploadAudio('${j.job_id}',this)"></div>`;
  let dubBox=''; if(dub&&dub.source==='imported') dubBox=`<div class="afile ok">✅ File ngoài: <b>${escapeHtml(dub.orig_name||dub.file)}</b> <button class="linkbtn" onclick="removeAudio('${j.job_id}')">bỏ</button></div>`;

  // ĐANG CHẠY -> chỉ hiện thanh loading + câu đang đọc (không list hết audio)
  if(running){
    const pct=cur.progress!=null?cur.progress:0;
    const prog=`<div class="afile tts-live">🔊 Đang đọc <b>${cur.n_done||0}/${cur.n_total||0}</b> câu · ${pct}%</div>
      <div class="pline big"><i style="width:${Math.min(pct,100)}%"></i></div>
      <div class="curline">🗣️ ${cur.cur_text?escapeHtml(cur.cur_text):'…'}</div>`;
    box.innerHTML=ctrl+dubBox+prog; fillVoices(); return;
  }

  // KHÔNG chạy -> tóm tắt + CHỈ list câu lỗi/thiếu + nút xem tất cả
  let ttsBox='';
  try{
    const script=await (await fetch(F('script.json'))).json(); let rr=[]; try{rr=await (await fetch(F('tts.json'))).json();}catch(e){}
    const byI={}; rr.forEach(r=>byI[r.i]=r);
    let wavOk=new Set();
    try{ const w=await (await fetch('/api/jobs/'+j.job_id+'/tts-wav-ok')).json(); (w.ids||[]).forEach(i=>wavOk.add(i)); }catch(e){}
    _ttsCache={jobId:j.job_id,script,byI,wavOk};
    const lines=script.filter(s=>s.vi);
    const bad=lines.filter(s=>{
      const r=byI[s.i];
      if(r&&r.audio&&r.dur>0.1) return false;
      if(wavOk.has(s.i)) return false;  // có wav đĩa dù chưa vào tts.json
      return true;
    });
    const have=lines.length-bad.length;
    const sum=`<div class="afile">🔊 <b>${lines.length}</b> câu`+(bad.length?` · <span style="color:var(--ok)">${have} có audio</span> · <span style="color:var(--err)">${bad.length} lỗi/thiếu</span>`:' · <span style="color:var(--ok)">OK</span>')+
      (bad.length?` <button class="btn pri" onclick="fillTts('${j.job_id}')">🩹 Đọc bù ${bad.length} câu</button>`:'')+
      ` <button class="linkbtn" onclick="ttsShowAll('${j.job_id}')">Xem tất cả clip ▾</button></div>`;
    let badList='';
    if(bad.length){ const rows=bad.slice(0,200).map(s=>clipRow(j,s,byI[s.i],F,wavOk)).join('');
      badList=`<div class="afile amuted">⚠ Câu lỗi/thiếu (đọc lại từng câu):</div><div class="acliplist" id="tts-badlist">${rows}</div>`; }
    ttsBox=sum+badList+`<div id="tts-alllist"></div>`;
  }catch(e){ ttsBox=`<div class="afile amuted">Chưa có bản đọc.</div>`; }

  const merge=made?`<div class="afile">🔗 <button class="linkbtn" onclick="assembleJob('${j.job_id}')">Ghép khớp time</button> <select id="merge-speed"><option selected value="1.0">1.0× đủ câu</option><option value="1.3">1.3× nén nhẹ</option><option value="1.5">1.5×</option><option value="2.0">2.0×</option></select> <span class="amuted">· ghép cạnh, không cắt · trống sau bắt kịp hình</span> <span class="amuted" id="merge-st"></span></div><div id="merge-result"></div>`:'';
  box.innerHTML=ctrl+merge+dubBox+ttsBox; fillVoices();
}
function clipRow(j,s,r,F,wavOk){
  const onDisk=wavOk&&wavOk.has&&wavOk.has(s.i);
  const bad=!(r&&r.audio&&r.dur>0.1)&&!onDisk;
  const rel=r&&r.audio?'tts/'+r.audio.split(/tts[\\/]/).pop():(onDisk?('tts/seg_'+String(s.i).padStart(4,'0')+'.wav'):null);
  const pl=bad?`<span style="color:var(--err);width:180px;display:inline-block">⚠ lỗi/thiếu</span>`:`<audio controls preload="none" src="${F(rel)}"></audio>`;
  return `<div class="aclip ${bad?'clipbad':''}"><span class="ct" onclick="seekTo(${s.start})">${fmtDur(s.start)}</span><button class="linkbtn" onclick="regenClip('${j.job_id}',${s.i},this)">↻</button>${pl}<span class="cliptext">${escapeHtml(s.vi||'')}</span></div>`;
}
function ttsShowAll(id){
  const box=document.getElementById('tts-alllist'); if(!box||!_ttsCache||_ttsCache.jobId!==id) return;
  if(box.dataset.on){ box.innerHTML=''; box.dataset.on=''; return; }
  const F=name=>'/api/jobs/'+id+'/file/'+name;
  const lines=_ttsCache.script.filter(s=>s.vi).slice(0,500);
  const wavOk=_ttsCache.wavOk||new Set();
  box.innerHTML=`<div class="afile amuted">Tất cả clip (${lines.length}):</div><div class="acliplist">`+
    lines.map(s=>clipRow({job_id:id},s,_ttsCache.byI[s.i],F,wavOk)).join('')+`</div>`;
  box.dataset.on='1';
}
// Vá câu trống ở bước Dịch: dịch lại lô NHỎ, chỉ các câu chưa có bản dịch (resume).
async function fillTranslate(id){
  if(!confirm('Dịch vá các câu còn trống? (lô nhỏ, chỉ dịch câu thiếu)')) return;
  try{ const r=await fetch('/api/jobs/'+id+'/translate-fill',{method:'POST'}); if(!r.ok){const d=await r.json().catch(()=>({})); alert('Không chạy được: '+(d.error||r.status)); return;} lastDetailSig=null; await refresh(); }catch(e){ alert('Lỗi: '+e); } }

async function artOcr(j,box,F){
  const st=j.stages.ocr||{};
  let rows=null; try{const r=await fetch(F('ocr.json')); if(r.ok) rows=await r.json();}catch(e){}
  let head=`<div class="afile">🔤 Chữ trên hình (tên/địa danh không có trong sub)${st.watermark?` <span class="amuted">· lọc watermark: ${escapeHtml((st.watermark||[]).slice(0,2).join(', '))}</span>`:''}</div>`;
  if(!rows){ box.innerHTML=head+(st.status==='running'?'<div class="afile amuted">⏳ đang quét…</div>':`<div class="afile amuted">Chưa quét (bấm ▶ Chạy ở trên; cần đã nhận diện lời).</div>`); return; }
  const list=rows.map(e=>`<tr><td class="ct" onclick="seekTo(${e.start})">${fmtDur(e.start)}</td><td><span class="badge">${e.kind==='static'?'tĩnh':'động'}</span></td><td class="zh">${escapeHtml(e.zh)}</td><td>${escapeHtml(e.vi||'')}</td></tr>`).join('');
  box.innerHTML=head+`<div class="afile amuted">${rows.length} chữ · tĩnh: che+chèn khi render · động: ghi chú</div><div class="atable"><table><tbody>${list}</tbody></table></div>`;
}
function artSeparate(j,box,F){
  const st=j.stages.separate||{};
  let body=`<div class="afile">🎚️ Bỏ giọng Trung, giữ nhạc/SFX (để render trộn với giọng Việt).${st.status==='running'?` <span class="amuted">⏳ đang tách… ${st.progress!=null?st.progress+'%':''}</span>`:''}</div>`;
  if(st.status==='done') body+=`<div class="afile ok">✅ Đã tách · <audio class="dubplayer" controls preload="none" src="${F('no_vocals.wav')}"></audio></div>`;
  else if(st.status!=='running') body+=`<div class="afile amuted">Tùy chọn: chạy trước ở đây, HOẶC cứ chọn chế độ Demucs ở bước Render — Render sẽ TỰ tách nền. Không dùng Demucs thì bỏ qua (render dùng ducking).</div>`;
  box.innerHTML=body;
}
function audioModeOptions(sel){
  const o=[['voice_bgm','Giọng Việt + BGM thư viện (bỏ nhạc gốc) ⭐'],
           ['sfx_lib','Tiếng động gốc + nhạc kho YouTube theo mức (bỏ hẳn nhạc gốc, an toàn bản quyền)'],
           ['duck_bgm','Ducking + BGM thư viện (át nhạc gốc, giữ SFX nhẹ)'],
           ['voice_only','Chỉ giọng Việt — không BGM'],
           ['duck','Ducking — giữ tiếng gốc (rủi ro bản quyền BGM)'],
           ['demucs_keep','Demucs — nhạc gốc + giọng Trung nhỏ (rủi ro BGM)'],
           ['demucs_clean','Demucs — nhạc gốc, bỏ giọng Trung (rủi ro BGM)']];
  const cur=sel||'voice_bgm';
  return o.map(([v,t])=>`<option value="${v}" ${v===cur?'selected':''}>${t}</option>`).join('');
}
function artMux(j,box){
  const st=j.stages.mux||{}, sep=j.stages.separate||{};
  const sepRun=sep.status==='running';
  const muxRun=st.status==='running';
  const running=muxRun||sepRun;
  const done=st.status==='done'&&!running;
  const hasSep=sep.status==='done';
  const raw=(st.audio_mode)||'voice_bgm';
  const mode=raw.startsWith('sfx_lib')?'sfx_lib':raw.startsWith('duck_bgm')?'duck_bgm':raw.startsWith('voice_bgm')?'voice_bgm':raw.startsWith('voice_only')?'voice_only':raw.startsWith('demucs_keep')?'demucs_keep':raw.startsWith('demucs_clean')?'demucs_clean':raw.startsWith('duck')?'duck':'voice_bgm';
  // Demucs tự chạy trước render: hiện % separate (không để UI trống vì mux còn "done")
  let rpct='', barPct=0;
  if(sepRun){
    barPct=Math.min(sep.progress||0,100);
    rpct=`⏳ đang tách nền (Demucs) trước render… ${sep.progress!=null?sep.progress+'%':''}`;
  }else if(muxRun && st.phase==='separate'){
    barPct=Math.min(sep.progress||st.progress||0,100);
    rpct=`⏳ đang tách nền (Demucs)… ${barPct||''}%`;
  }else if(muxRun && st.phase==='assemble'){
    rpct='⏳ đang ghép giọng…';
  }else if(muxRun){
    barPct=Math.min(st.progress||0,100);
    rpct=st.progress!=null?`⏳ đang render… ${st.progress}%`:'⏳ đang render…';
  }
  const rbar=running?`<div class="pline big" style="margin-top:8px"><i style="width:${barPct}%"></i></div>`:'';
  const zCur=st.zoom||1.12;
  const vbCur=st.vbitrate||'8M';
  const lean=uiMode()==='studio'||uiMode()==='console';
  // Studio/Console: Look panel đã có zoom/frame/brand — không lặp lại ở đây
  let lookBlock='';
  if(!lean){
    lookBlock=
    `<div class="afile">🔍 Zoom crop: <select id="mux-zoom"><option value="1.10" ${zCur==1.1||zCur==1.10?'selected':''}>10%</option><option value="1.12" ${!zCur||zCur==1.12?'selected':''}>12%</option><option value="1.15" ${zCur==1.15?'selected':''}>15%</option></select> · <label class="chk"><input type="checkbox" id="mux-motion" checked> Ken Burns+Drift</label> · <label class="chk"><input type="checkbox" id="mux-lut" checked> LUT</label> · Grain <select id="mux-grain"><option value="off">off</option><option value="weak" selected>weak</option><option value="medium">medium</option><option value="strong">strong</option></select></div>`+
    `<div class="afile">🖼 Khung <select id="mux-frame"><option value="off" selected>tắt</option><option value="auto">auto</option></select></div>`+
    `<div class="afile">™️ Brand <select id="mux-brand-mode" title="Chữ/logo kênh"><option value="off" selected>tắt</option><option value="drift">chạy sin (mượt)</option><option value="corners">đổi góc mỗi 40s</option><option value="drift_seed">sin + random theo job</option></select> · chữ <input id="mux-brand-text" placeholder="Tên kênh" style="width:140px"> · logo <select id="mux-brand-logo"><option value="off" selected>tắt</option><option value="auto">auto</option></select></div>`+
    `<div class="afile">🖥 Độ phân giải: <select id="mux-res"><option value="source" selected>Nguồn (giữ gốc)</option><option value="1080">1080p / 1K (Full HD)</option></select> · Lật: <select id="mux-flip"><option value="" selected>không</option><option value="h">ngang (gương trái-phải)</option><option value="v">dọc (lật trên-dưới)</option><option value="hv">cả hai</option></select> · Nén H264: <select id="mux-vbit" title="Bitrate video"><option value="8M" ${!vbCur||vbCur==='8M'?'selected':''}>8M đẹp (~5–6GB/90p)</option><option value="4M" ${vbCur==='4M'?'selected':''}>4M cân bằng (~2.5GB)</option><option value="2M" ${vbCur==='2M'?'selected':''}>2M nhẹ (~1.3GB)</option></select></div>`;
  } else {
    lookBlock=`<div class="afile amuted">Look (zoom/frame/brand/lật) chỉnh ở panel trên · che watermark/hardsub luôn bật</div>`+
    `<div class="afile">Nén H264: <select id="mux-vbit" title="Bitrate video"><option value="8M" ${!vbCur||vbCur==='8M'?'selected':''}>8M đẹp</option><option value="4M" ${vbCur==='4M'?'selected':''}>4M cân bằng</option><option value="2M" ${vbCur==='2M'?'selected':''}>2M nhẹ</option></select> · 🖥 <select id="mux-res"><option value="source" selected>Nguồn</option><option value="1080">1080p</option></select></div>`;
  }
  const ctrl=`<div class="afile">🎬 <button class="btn pri" onclick="muxJob('${j.job_id}')" ${running?'disabled':''}>${done?'↻ Render lại':'Render video'}</button> <span class="amuted" id="mux-st">${rpct}</span></div>${rbar}`+
    `<div class="afile">🔊 Âm thanh: <select id="mux-audio" onchange="muxModeChanged()">${audioModeOptions(mode)}</select></div>`+
    `<div class="afile" id="row-vvol">Giọng Trung giữ lại: <select id="mux-vvol"><option value="0.12">rất nhỏ</option><option value="0.2" selected>nhỏ</option><option value="0.32">vừa</option></select></div>`+
    `<div class="afile" id="row-ovol">Tiếng gốc nền: <select id="mux-ovol"><option value="0.12">rất nhỏ</option><option value="0.18" selected>nhỏ (duck+BGM)</option><option value="0.28">vừa</option><option value="0.45">to</option><option value="0.6">rất to</option></select></div>`+
    `<div class="afile amuted">Giọng Việt tự boost ×1.55 khi render (RENDER_DUB_VOL) · BGM mặc định 0.14</div>`+
    lookBlock+
    `<div class="afile"><label class="chk"><input type="checkbox" id="opt-sub" checked> phụ đề Việt</label> <label class="chk" title="Ghép đủ câu + bắt kịp khoảng trống"><input type="checkbox" id="opt-fit" checked> Khớp time</label></div>`;
  let player=''; if(done){ const resLbl=(st.width&&st.height)?`${st.width}×${st.height}`:(st.resolution||'');
    const vbLbl=st.vbitrate?` · H264 ${st.vbitrate}`:'';
    player=`<div class="afile ok">✅ output.mp4${resLbl?' · '+resLbl:''}${vbLbl} · <button class="linkbtn" onclick="revealJob('${j.job_id}','output.mp4')">📂 Mở thư mục</button> · <a href="/api/jobs/${j.job_id}/file/output.mp4" target="_blank">tải</a></div>`;
    if(uiMode()==='studio'){
      /* preview chỉ ở studio-pslot — ensureVideo ưu tiên output */
    } else {
      player+=`<div id="pslot"></div>`;
    }
  }
  else if((j.stages.mux||{}).status==='running') player=`<div class="afile amuted">⏳ đang render — xem preview sau khi xong</div>`;
  box.innerHTML=ctrl+player; muxModeChanged();
  if(!lean){ fillMuxFrames(); fillMuxLogos(); }
  if(done) ensureVideo(j,true);
}
async function fillMuxFrames(){
  const sel=document.getElementById('mux-frame'); if(!sel) return;
  try{
    const d=await (await fetch('/api/frames')).json();
    const files=(d&&d.frames)||[];
    const cur=sel.value||'off';
    sel.innerHTML=`<option value="off">tắt</option><option value="auto">auto</option>`+
      files.map(f=>`<option value="${escapeHtml(f)}">${escapeHtml(f)}</option>`).join('');
    sel.value=[...sel.options].some(o=>o.value===cur)?cur:'off';
  }catch(e){}
}
async function fillMuxLogos(){
  const sel=document.getElementById('mux-brand-logo'); if(!sel) return;
  try{
    const d=await (await fetch('/api/brand-logos')).json();
    const files=(d&&d.logos)||[];
    const cur=sel.value||'off';
    sel.innerHTML=`<option value="off">tắt</option><option value="auto">auto</option>`+
      files.map(f=>`<option value="${escapeHtml(f)}">${escapeHtml(f)}</option>`).join('');
    sel.value=[...sel.options].some(o=>o.value===cur)?cur:'off';
  }catch(e){}
}
async function fillAutoFrames(){
  const sel=document.getElementById('auto-frame'); if(!sel) return;
  try{
    const d=await (await fetch('/api/frames')).json();
    const files=(d&&d.frames)||[];
    const cur=sel.value||'off';
    sel.innerHTML=`<option value="off">tắt</option><option value="auto">auto</option>`+
      files.map(f=>`<option value="${escapeHtml(f)}">${escapeHtml(f)}</option>`).join('');
    sel.value=[...sel.options].some(o=>o.value===cur)?cur:'off';
  }catch(e){}
}
async function fillAutoLogos(){
  const sel=document.getElementById('auto-brand-logo'); if(!sel) return;
  try{
    const d=await (await fetch('/api/brand-logos')).json();
    const files=(d&&d.logos)||[];
    const cur=sel.value||'off';
    sel.innerHTML=`<option value="off">tắt</option><option value="auto">auto</option>`+
      files.map(f=>`<option value="${escapeHtml(f)}">${escapeHtml(f)}</option>`).join('');
    sel.value=[...sel.options].some(o=>o.value===cur)?cur:'off';
  }catch(e){}
}
function muxModeChanged(){
  const m=(document.getElementById('mux-audio')||{}).value;
  const vv=document.getElementById('row-vvol'), ov=document.getElementById('row-ovol');
  if(vv) vv.style.display=(m==='demucs_keep')?'':'none';
  if(ov) ov.style.display=(m==='duck'||m==='duck_bgm')?'':'none';
}
function autoAudioChanged(){
  const m=(document.getElementById('auto-audio')||{}).value;
  const ov=document.getElementById('row-auto-ovol');
  if(ov) ov.style.display=(m==='duck'||m==='duck_bgm')?'':'none';
}

let VOICES=null;
async function loadVoices(){ if(!VOICES){ try{VOICES=await (await fetch('/api/voices')).json();}catch(e){VOICES={vieneu:[],omnivoice:[]};} } return VOICES; }
function curEngine(){ const es=document.getElementById('tts-engine'); if(es) return es.value; return (lastFull&&lastFull.stages.tts&&lastFull.stages.tts.tts_engine)||'vieneu'; }
function curAsrEngine(j){
  // Panel Nhận diện đang mở — ưu tiên lựa chọn live
  const panel=document.getElementById('asr-engine');
  if(panel&&panel.value) return panel.value;
  // Đã lưu trên job (sau save / poll)
  const src=j||lastFull; const st=(src&&src.stages&&src.stages.asr)||{};
  if(st.asr_engine||st.engine) return st.asr_engine||st.engine;
  // Pipeline auto — chỉ khi chưa có lựa chọn panel/job
  const auto=document.getElementById('auto-asr');
  if(auto&&auto.value) return auto.value;
  return 'nano_fa';
}
async function saveAsrEngine(id,e){
  try{
    const r=await fetch('/api/jobs/'+id+'/asr-engine',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({engine:e})});
    if(!r.ok){ const d=await r.json().catch(()=>({})); alert('Không lưu được ASR engine: '+(d.error||r.status)+'\n(Thường do server chưa restart sau khi thêm engine mới)'); return false; }
    return true;
  }catch(err){ alert('Không lưu được ASR engine: '+err); return false; }
}
async function asrEngineChanged(id){
  const el=document.getElementById('asr-engine');
  const e=el&&el.value?el.value:curAsrEngine();
  // Đổi dropdown = muốn XEM/DÙNG file đó (nếu có). Không ghi engine trước khi promote.
  const map={fun_asr_nano:'asr.fun_nano.json',firered_asr:'asr.firered.json',
             paraformer_zh:'asr.paraformer_zh.json',sensevoice:'asr.sensevoice.json',
             hybrid_fa:'asr.aligned_repaired.json'};
  const src=map[e];
  if(src){
    try{
      const sr=await fetch('/api/jobs/'+id+'/asr-sources',{cache:'no-store'});
      if(sr.ok){
        const items=((await sr.json()).items||[]).map(x=>x.file);
        let pick=src;
        if(e==='hybrid_fa' && !items.includes(pick)){
          pick=['asr.aligned_repaired.json','asr.aligned_fa.json','asr.aligned.json','asr.consensus.json']
            .find(f=>items.includes(f));
        }
        if(pick && items.includes(pick)){
          await selectAsrSource(id, pick);
          try{ if(typeof rememberForm==='function') rememberForm(id,'auto-asr',e); }catch(err){}
          const a=document.getElementById('auto-asr'); if(a && [...a.options].some(o=>o.value===e)) a.value=e;
          return;
        }
      }
    }catch(err){}
  }
  // Không có file sẵn → chỉ lưu engine để lần Chạy tới
  if(!(await saveAsrEngine(id,e))) return;
  try{ if(typeof rememberForm==='function') rememberForm(id,'auto-asr',e); }catch(err){}
  const a=document.getElementById('auto-asr'); if(a && [...a.options].some(o=>o.value===e)) a.value=e;
  if(lastFull){ (lastFull.stages.asr=lastFull.stages.asr||{}).asr_engine=e; lastFull.stages.asr.engine=e; renderPanel(lastFull,true); }
}
async function selectAsrSource(id, forced){
  const el=document.getElementById('asr-source');
  const st=document.getElementById('asr-src-st');
  const src=forced || (el&&el.value);
  if(!src){ alert('Chưa chọn file ASR'); return; }
  if(st) st.textContent='⏳ đang ghi '+src+'…';
  try{
    const r=await fetch('/api/jobs/'+id+'/asr-select',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source:src})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){ if(st) st.textContent=''; alert(d.error||r.status); return; }
    if(st) st.textContent='✓ '+src+' · '+d.n_segments+' câu'+(d.translate_stale?' · ⚠ cần dịch lại':'');
    if(lastFull){
      const a=lastFull.stages.asr=lastFull.stages.asr||{};
      a.asr_source=src; a.n_segments=d.n_segments; a.status='done';
      if(d.engine){ a.asr_engine=d.engine; a.engine=d.engine; }
      if(d.model) a.model=d.model;
      else if(src.includes('fun_nano')) a.model='Fun-ASR-Nano';
      else if(src.includes('firered')) a.model='FireRedASR2-AED';
    }
    lastDetailSig=null;
    // reload panel ngay — thấy số câu + 5 dòng đầu đổi
    if(lastFull) await loadArtifact(lastFull,'asr');
    else void refresh();
  }catch(e){ if(st) st.textContent=''; alert('Lỗi: '+e); }
}
function fillVoices(){ const vs=document.getElementById('tts-voice'); if(!vs||!VOICES) return; const list=VOICES[curEngine()]||[]; const cur=(lastFull&&lastFull.stages.tts)?lastFull.stages.tts.voice:null;
  const noneMsg='(chưa có giọng — Thêm giọng)';
  vs.innerHTML=list.length?list.map(v=>`<option value="${v.id}" ${v.id===cur?'selected':''}>${escapeHtml(v.label||v.id)}</option>`).join(''):`<option value="">${noneMsg}</option>`; }
async function saveTtsEngine(id,e){ try{ await fetch('/api/jobs/'+id+'/tts-voice',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({engine:e})}); }catch(err){} }
function engineChanged(id){ const e=curEngine(); saveTtsEngine(id,e);
  if(lastFull){ (lastFull.stages.tts=lastFull.stages.tts||{}).tts_engine=e; renderPanel(lastFull,true); }  // dựng lại: đổi form clone↔train + danh sách giọng
}
async function doTranslate(id,mode){ return guardAct('tr:'+id, async()=>{
  let body,label;
  const trMode=(document.getElementById('tr-mode')||{value:'batch'}).value||'batch';
  const trModel=(document.getElementById('tr-model')||{}).value||null;
  if(mode==='single'){ body={model:trModel,mode:trMode}; label='↻ Dịch LẠI TOÀN BỘ ('+trMode+') — xoá script.json cũ'; }
  else if(mode==='2pass'){ body={model:trModel||'gemma4-12b-qat-mtp-fast',review_model:'local/gemma3:12b',mode:trMode}; label='Dịch 2 lượt LẠI (batch → gemma3 rà) — xoá bản cũ'; }
  else{ body={review_only:true,review_model:'local/gemma3:12b'}; label='Rà soát văn phong (giữ bản dịch, chỉ soát)'; }
  if(!confirm(label+'?\nTTS cũ sẽ lỗi thời.')) return;
  paintRunning(id,'translate', mode==='review'?'đang rà soát…':'đang xếp hàng dịch…');
  try{ const r=await fetch('/api/jobs/'+id+'/translate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); if(!r.ok){const d=await r.json().catch(()=>({})); busyHint(d.error||r.status); return;} lastDetailSig=null; await refresh(); }catch(e){ alert('Lỗi: '+e); }
}); }
async function runTranslateAlts(id){ return guardAct('alts:'+id, async()=>{
  const st=document.getElementById('alts-st'); if(st) st.textContent='đang quét…';
  try{
    const r=await fetch('/api/jobs/'+id+'/translate-alts',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({force:true,limit:120,apply_pick:false})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){ if(st) st.textContent=d.error||r.status; return; }
    if(d.already==='running'){ if(st) st.textContent='đang chạy…'; }
    // poll nhẹ
    for(let i=0;i<90;i++){
      await new Promise(res=>setTimeout(res,2000));
      const g=await (await fetch('/api/jobs/'+id+'/translate-alts',{cache:'no-store'})).json();
      const s=g.stage||{};
      if(st) st.textContent=(s.status||'')+' · '+(s.n_flagged!=null?s.n_flagged+' câu':'')+(s.progress!=null?' · '+s.progress+'%':'');
      if(s.status==='done'||s.status==='failed'){ await showTranslateAlts(id); lastDetailSig=null; void refresh(); return; }
    }
  }catch(e){ if(st) st.textContent=String(e); }
}); }
async function showTranslateAlts(id){
  const box=document.getElementById('alts-box'); const st=document.getElementById('alts-st');
  try{
    const d=await (await fetch('/api/jobs/'+id+'/translate-alts',{cache:'no-store'})).json();
    const rep=d.report||{};
    const items=(d.alts&&d.alts.items)||[];
    if(st) st.textContent=`pack ${rep.score!=null?rep.score+'/100':'?'} · ${items.length} câu đa nghĩa`;
    if(!box) return;
    const gaps=(rep.gaps||[]).length?`<div class="amuted">Thiếu: ${rep.gaps.map(escapeHtml).join(', ')}</div>`:'';
    const head=`<div class="afile">📦 Analyze <b>${escapeHtml(rep.genre||'?')}</b> · mode <b>${escapeHtml(rep.mode||'?')}</b> · score <b>${rep.score??'?'}</b>${rep.ok?' · OK':' · chưa đủ'}${gaps}</div>`;
    const rows=items.slice(0,40).map(it=>{
      const alts=(it.alts||[]).map((a,ai)=>
        `<button class="btn" style="margin:2px" onclick="applyAltPick('${id}',${it.i},${ai})">${ai===(it.pick||0)?'✓ ':''}${escapeHtml(a)}</button>`
      ).join(' ');
      return `<div class="afile"><b>#${it.i}</b> <span class="amuted">${escapeHtml(it.reason||'')}</span><br>`+
        `<span class="amuted">${escapeHtml(it.zh||'')}</span><br>hiện: <b>${escapeHtml(it.vi||'')}</b><br>${alts}</div>`;
    }).join('');
    box.innerHTML=head+(items.length?rows:'<div class="afile amuted">Chưa có vi_alts.json — bấm Quét phương án.</div>');
  }catch(e){ if(box) box.innerHTML='<div class="afile">'+escapeHtml(String(e))+'</div>'; }
}
async function applyAltPick(id,i,pick){
  try{
    const r=await fetch('/api/jobs/'+id+'/apply-alt',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({i,pick})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){ alert(d.error||r.status); return; }
    await showTranslateAlts(id); lastDetailSig=null; void refresh();
  }catch(e){ alert(e); }
}
function _muxEl(muxId, autoId){ return document.getElementById(muxId)||document.getElementById(autoId); }
async function muxJob(id){ return guardAct('mux:'+id, async()=>{
  paintRunning(id,'mux','đang render…'); await flushPaint();
  const st=document.getElementById('mux-st'); if(st) st.textContent=' ⏳ đang render...';
  const audio_mode=(_muxEl('mux-audio','auto-audio')||{value:'voice_bgm'}).value;
  const voice_vol=parseFloat((_muxEl('mux-vvol',null)||{value:'0.2'}).value);
  const orig_vol=parseFloat((_muxEl('mux-ovol',null)||{value:'0.6'}).value);
  const add_sub=(document.getElementById('opt-sub')||{checked:true}).checked;
  const fit=(document.getElementById('opt-fit')||document.getElementById('auto-fit')||{checked:true}).checked;
  const resolution=(_muxEl('mux-res','auto-res')||{value:'source'}).value||'source';
  const flip=(_muxEl('mux-flip','auto-flip')||{value:''}).value||'';
  const zoom=parseFloat((_muxEl('mux-zoom','auto-zoom')||{value:'1.12'}).value)||1.12;
  const motion=(_muxEl('mux-motion','auto-motion')||{checked:true}).checked;
  const lut=(_muxEl('mux-lut','auto-lut')||{checked:true}).checked;
  const grain=(_muxEl('mux-grain','auto-grain')||{value:'weak'}).value||'weak';
  const brand_mode=(_muxEl('mux-brand-mode','auto-brand-mode')||{value:'off'}).value||'off';
  const brand=brand_mode!=='off';
  const brand_text=(_muxEl('mux-brand-text','auto-brand-text')||{value:''}).value||'';
  const brand_logo=(_muxEl('mux-brand-logo','auto-brand-logo')||{value:'off'}).value||'off';
  const frame=(_muxEl('mux-frame','auto-frame')||{value:'off'}).value||'off';
  const vbitrate=(_muxEl('mux-vbit','auto-vbit')||{value:'8M'}).value||'8M';
  try{ const r=await fetch('/api/jobs/'+id+'/mux',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({audio_mode,voice_vol,orig_vol,add_sub,fit,resolution,flip,zoom,motion,lut,grain,brand,brand_mode,brand_text,brand_logo,frame,vbitrate})}); const d=await r.json(); if(d&&d.error){alert('Lỗi render: '+d.error); if(st)st.textContent=''; void refresh(); return;} lastDetailSig=null; void refresh(); }catch(e){ alert('Lỗi: '+e); void refresh(); }
}); }
async function regenClip(id,i,btn){ const o=btn.textContent; btn.textContent='⏳'; btn.disabled=true;
  try{ const r=await fetch('/api/jobs/'+id+'/tts-clip',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({i})}); const d=await r.json(); if(d&&d.error){alert('Lỗi: '+d.error); btn.textContent=o; btn.disabled=false; return;} lastDetailSig=null; await refresh(); }catch(e){ btn.textContent=o; btn.disabled=false; } }
async function assembleJob(id){ const st=document.getElementById('merge-st'), bx=document.getElementById('merge-result'), spd=(document.getElementById('merge-speed')||{}).value||'1.0';
  if(st) st.textContent=' ⏳ ghép...';
  try{ const rep=await (await fetch('/api/jobs/'+id+'/assemble?fit=true&max_speed='+spd,{method:'POST'})).json(); if(rep.error){if(st)st.textContent=' lỗi: '+rep.error; return;} if(st)st.textContent='';
    const fn=(rep.out||'dub_fit.wav').split(/[\\/]/).pop(); const v=rep.n_overlap===0?'✅ không đè':('🔴 '+rep.n_overlap+' đè');
    bx.innerHTML=`<div class="afile">🔗 ${rep.track_dur}s · ${v} · nén ${rep.n_compressed}/${rep.n_clips} · <a href="/api/jobs/${id}/file/${fn}" target="_blank">tải</a></div><audio class="dubplayer" controls src="/api/jobs/${id}/file/${fn}?_t=${Date.now()}"></audio>`;
  }catch(e){ if(st)st.textContent=' lỗi: '+e; } }
async function cloneVoice(){
  const nameEl=document.getElementById('clone-name'), fileEl=document.getElementById('clone-file');
  const name=(nameEl&&nameEl.value||'').trim(), f=fileEl&&fileEl.files[0], st=document.getElementById('clone-st');
  const btn=[...document.querySelectorAll('button')].find(b=>b.getAttribute('onclick')==='cloneVoice()');
  if(!name||!f){ alert('Nhập tên + file mẫu 3-8s.'); return; }
  const fd=new FormData(); fd.append('name',name); fd.append('file',f);
  if(st) st.textContent=' ⏳ đang thêm giọng…';
  if(btn) btn.disabled=true;
  try{
    const r=await fetch('/api/clone-voice',{method:'POST',body:fd});
    let d={}; try{ d=await r.json(); }catch(_){ d={error:'server không trả JSON (HTTP '+r.status+')'}; }
    if(!r.ok || (d&&d.error)){
      const msg=(d&&d.error)||('HTTP '+r.status);
      alert('Lỗi: '+msg);
      if(st) st.textContent=' · ❌ '+msg;
      return;
    }
    const ok=` · ✅ xong — «${d.voice||name}»${d.ref_sec!=null?` · mẫu ${d.ref_sec}s`:''}`;
    if(st) st.textContent=ok;
    VOICES=null; await loadVoices(); fillVoices();
    // giữ thông báo thành công; refresh panel sau 1 nhịp nhưng ghi lại status
    lastDetailSig=null; await refresh();
    const st2=document.getElementById('clone-st'); if(st2) st2.textContent=ok;
  }catch(e){
    alert('Lỗi: '+e);
    if(st) st.textContent=' · ❌ '+e;
  }finally{
    if(btn) btn.disabled=false;
  }
}
async function previewVoice(){
  const v=(document.getElementById('tts-voice')||{}).value;
  if(!v){ alert('Chưa chọn giọng.'); return; }
  const a=document.getElementById('voice-preview'), st=document.getElementById('tts-prev-st');
  const speed=parseFloat((document.getElementById('tts-speed')||{value:'1.0'}).value)||1.0;
  const eng=curEngine();
  const url='/api/voice-sample?engine='+encodeURIComponent(eng)
    +'&voice='+encodeURIComponent(v)+'&speed='+encodeURIComponent(speed)
    +'&_t='+Date.now();
  if(st) st.textContent=' ⏳ đang tạo mẫu nghe thử (lần đầu có thể lâu)…';
  if(a){ a.style.display='block'; a.pause(); a.removeAttribute('src'); a.load(); }
  try{
    const r=await fetch(url);
    if(!r.ok){
      let msg='HTTP '+r.status;
      try{ const d=await r.json(); if(d&&d.error) msg=d.error; }catch(_){}
      if(st) st.textContent=' · ❌ '+msg;
      alert('Nghe thử lỗi: '+msg);
      return;
    }
    const blob=await r.blob();
    if(!blob || blob.size<100){ if(st) st.textContent=' · ❌ file rỗng'; return; }
    const obj=URL.createObjectURL(blob);
    if(!a){ if(st) st.textContent=' · ✅ (không có player)'; return; }
    a.onended=()=>{ try{ URL.revokeObjectURL(obj); }catch(_){} };
    a.onerror=()=>{ if(st) st.textContent=' · ❌ không phát được'; };
    await new Promise((resolve,reject)=>{
      a.oncanplay=()=>resolve();
      a.onerror=()=>reject(new Error('audio error'));
      a.src=obj; a.load();
    });
    if(st) st.textContent=' · ▶ đang phát…';
    try{ await a.play(); }catch(e){ if(st) st.textContent=' · ✅ sẵn sàng — bấm ▶ trên player'; }
    a.onended=()=>{ if(st) st.textContent=' · ✅ xong'; try{ URL.revokeObjectURL(obj); }catch(_){} };
  }catch(e){
    if(st) st.textContent=' · ❌ '+e;
    alert('Nghe thử lỗi: '+e);
  }
}
async function regenTts(id, force){ return guardAct('tts:'+id, async()=>{
  const v=document.getElementById('tts-voice').value;
  if(!v){alert('Chưa có giọng — Thêm giọng trước.'); return;}
  if(force===undefined) force=false;
  if(force && !confirm('Tạo LẠI TỪ ĐẦU? Sẽ XOÁ toàn bộ câu đã đọc.')) return;
  paintRunning(id,'tts', force?'đang tạo lại…':'đang tiếp tục…'); await flushPaint();
  const st=document.getElementById('tts-prev-st'); if(st)st.textContent=force?' ⏳ Đang tạo lại...':' ⏳ Đang tiếp tục...';
  const speed=parseFloat((document.getElementById('tts-speed')||{value:'1.0'}).value);
  try{
    const r=await fetch('/api/jobs/'+id+'/tts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({engine:curEngine(),voice:v,speed,force:!!force})});
    const d=await r.json(); if(d&&d.error){alert('Lỗi: '+d.error); if(st)st.textContent=''; void refresh(); return;}
    lastDetailSig=null; void refresh();
  }catch(e){ alert('Lỗi: '+e); if(st)st.textContent=''; void refresh(); }
}); }
async function fillTts(id){ return guardAct('tts:'+id, async()=>{
  if(!confirm('Đọc bù các câu còn thiếu audio? (giữ câu đã có)')) return;
  const vs=document.getElementById('tts-voice');
  const v=vs&&vs.value;
  const speed=parseFloat((document.getElementById('tts-speed')||{value:'1.0'}).value);
  try{
    const body={force:false,speed};
    if(v) body.voice=v;
    body.engine=curEngine();
    const r=await fetch('/api/jobs/'+id+'/tts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(!r.ok){const d=await r.json().catch(()=>({})); alert('Không chạy được: '+(d.error||r.status)); return;}
    lastDetailSig=null; await refresh();
  }catch(e){ alert('Lỗi: '+e); }
}); }
async function uploadAudio(id,input){ const f=input.files&&input.files[0]; if(!f) return; const fd=new FormData(); fd.append('file',f); input.disabled=true;
  try{ const r=await fetch('/api/jobs/'+id+'/audio',{method:'POST',body:fd}); const d=await r.json(); if(d&&d.error) alert('Lỗi: '+d.error); lastDetailSig=null; await refresh(); }catch(e){ alert('Lỗi: '+e); } input.disabled=false; }
async function removeAudio(id){ if(!confirm('Bỏ audio ngoài?')) return; try{await fetch('/api/jobs/'+id+'/audio',{method:'DELETE'}); lastDetailSig=null; await refresh();}catch(e){} }
async function uploadSource(id,input){
  const f=input.files&&input.files[0]; if(!f) return;
  if(f.size>800*1024*1024 && !confirm('File ~'+fmtSize(f.size)+'. Upload HTTP chậm — nên dùng Path local. Vẫn upload?')){ input.value=''; return; }
  const st=document.getElementById('src-up-st'); if(st) st.textContent='đang gửi…';
  input.disabled=true;
  try{
    const fd=new FormData(); fd.append('file',f);
    await new Promise((resolve,reject)=>{
      const xhr=new XMLHttpRequest();
      xhr.open('POST','/api/jobs/'+id+'/source');
      xhr.upload.onprogress=e=>{ if(st&&e.lengthComputable) st.textContent=Math.round(e.loaded/e.total*100)+'%'; };
      xhr.onload=()=>{ let d={}; try{d=JSON.parse(xhr.responseText);}catch(err){} if(xhr.status>=200&&xhr.status<300&&!d.error) resolve(d); else reject(new Error((d&&d.error)||('HTTP '+xhr.status))); };
      xhr.onerror=()=>reject(new Error('network'));
      xhr.send(fd);
    });
    if(st) st.textContent='xong';
    lastDetailSig=null; await refresh();
  }catch(e){ if(st) st.textContent=''; alert('Lỗi import: '+e.message); }
  input.disabled=false; input.value='';
}
async function importSourcePath(id){
  const el=document.getElementById('src-path'); const path=(el&&el.value||'').trim();
  if(!path){ alert('Nhập đường dẫn file video'); return; }
  const move=!!(document.getElementById('src-move')||{}).checked;
  const st=document.getElementById('src-up-st'); if(st) st.textContent='đang copy…';
  try{
    const r=await fetch('/api/jobs/'+id+'/source-path',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path,move})});
    const d=await r.json(); if(d&&d.error){ alert('Lỗi: '+d.error); if(st) st.textContent=''; return; }
    if(st) st.textContent='xong'; lastDetailSig=null; await refresh();
  }catch(e){ if(st) st.textContent=''; alert('Lỗi: '+e); }
}
async function revealJob(id,file){ const q=file?('?file='+encodeURIComponent(file)):''; try{await fetch('/api/jobs/'+id+'/reveal'+q,{method:'POST'});}catch(e){alert('Không mở được: '+e);} }
// Chống double-click + paint trạng thái NGAY (không chờ server).
const _actBusy=new Set();
let _optLock=null; // {id, stage, text, until}
async function guardAct(key, fn){
  if(_actBusy.has(key)){ console.warn('[guard] bỏ qua click trùng', key); return; }
  _actBusy.add(key);
  document.body.classList.add('acting');
  try{ return await fn(); }
  finally{
    setTimeout(()=>{ _actBusy.delete(key); document.body.classList.remove('acting'); }, 1200);
  }
}
function busyHint(err){
  const m=String(err||'');
  if(/đang chạy|không gửi trùng|đã nhận/i.test(m)) alert(m);
  else alert('Không chạy được: '+m);
}
/** Optimistic UI: patch DOM tức thì + khóa poll ghi đè pending. */
function paintRunning(id, stage, hint){
  const now=Math.floor(Date.now()/1000);
  const text=hint||'đang xếp hàng…';
  _optLock={id, stage, text, until:Date.now()+12000};
  document.querySelectorAll('.autobar .btn.pri.big, .studio-cta .btn.pri.big, .console-run .btn.pri.big').forEach(b=>{ b.disabled=true; });
  document.querySelectorAll('.autobar .btn.stop, .studio-cta .btn.stop, .console-run .btn.stop').forEach(b=>{ b.disabled=false; });
  const pnl=document.querySelector('.pnl-run');
  if(pnl) pnl.innerHTML=`<span class="sstat run">…</span><button class="btn stop" onclick="stopJob('${id}')">⏹ Dừng</button>`;
  const muxSt=document.getElementById('mux-st'); if(muxSt && stage==='mux') muxSt.textContent=' ⏳ '+text;
  document.querySelectorAll('.jrow').forEach(row=>{
    if((row.getAttribute('onclick')||'').indexOf(id)<0) return;
    const jp=row.querySelector('.jp'); if(jp) jp.textContent='…';
    const dot=row.querySelector('.jdot'); if(dot){ dot.className='jdot d-run'; }
    const start=row.querySelector('.jstart'); if(start) start.remove();
  });
  if(lastFull && lastFull.job_id===id){
    lastFull.status='running';
    lastFull.stages=lastFull.stages||{};
    const prev=lastFull.stages[stage]||{};
    lastFull.stages[stage]={...prev, status:'running', progress:prev.progress!=null?prev.progress:0, cur_text:text, updated_at:now};
    lastDetailSig=null;
  }
  const j=(cache||[]).find(x=>x.job_id===id);
  if(j){
    j.status='running';
    j.stages=j.stages||{};
    j.stages[stage]=Object.assign({}, j.stages[stage]||{}, {status:'running', cur_text:text});
  }
}
function paintIdle(id){
  if(_optLock && _optLock.id===id) _optLock=null;
  if(lastFull && lastFull.job_id===id) lastFull.status='pending';
  const j=(cache||[]).find(x=>x.job_id===id);
  if(j && j.status==='running') j.status='pending';
}
function applyOpt(full){
  if(!_optLock || !_optLock.id || !_optLock.until) return full;
  if(Date.now()>_optLock.until){ _optLock=null; return full; }
  if(!full || full.job_id!==_optLock.id) return full;
  if(full.status==='running'){ _optLock=null; return full; }
  const stage=_optLock.stage;
  full={...full, status:'running', stages:{...(full.stages||{})}};
  const prev=full.stages[stage]||{};
  full.stages[stage]={...prev, status:'running', cur_text:_optLock.text||prev.cur_text||'đang xếp hàng…'};
  return full;
}
async function flushPaint(){
  await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
}
async function startJob(id){ return guardAct('start:'+id, async()=>{
  paintRunning(id,'asr','đang bắt đầu…'); await flushPaint();
  try{
    const r=await fetch('/api/jobs/'+id+'/start',{method:'POST'});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){ busyHint(d.error||r.status); paintIdle(id); lastDetailSig=null; void refresh(); return; }
    lastDetailSig=null; void refresh();
  }catch(e){ alert('Lỗi: '+e); void refresh(); }
}); }
async function runStage(id,stage,force){ return guardAct('run:'+id+':'+stage, async()=>{
  const nm={download:'Tải',asr:'Nhận diện',translate:'Dịch',tts:'Lồng tiếng',ocr:'OCR',separate:'Tách nền',mux:'Render'};
  if(force){ const msg=stage==='download'?'🗑 XOÁ đã tải & TẢI LẠI TỪ ĐẦU?':'Chạy lại "'+(nm[stage]||stage)+'"? Ghi đè kết quả cũ.'; if(!confirm(msg)) return; }
  paintRunning(id,stage,'đang xếp hàng…'); await flushPaint();
  let q=force?'?force=true':'';
  if(stage==='asr'){
    const eng=curAsrEngine();
    q+=(q?'&':'?')+'engine='+encodeURIComponent(eng);
    void saveAsrEngine(id,eng);
  }
  if(stage==='tts'){ q+=(q?'&':'?')+'engine='+encodeURIComponent(curEngine()); }
  try{
    const r=await fetch('/api/jobs/'+id+'/run/'+stage+q,{method:'POST'});
    if(!r.ok){ const d=await r.json().catch(()=>({})); busyHint(d.error||r.status); paintIdle(id); lastDetailSig=null; void refresh(); return; }
    lastDetailSig=null; void refresh();
  }catch(e){ alert('Lỗi: '+e); void refresh(); }
}); }
// Chạy 1 bước từ PANEL. TTS: kèm giọng đang chọn (sửa bug chạy giọng mặc định).
async function runStep(id,stage,force){ return guardAct('run:'+id+':'+stage, async()=>{
  const nm={download:'Tải',asr:'Nhận diện',translate:'Dịch',tts:'Lồng tiếng',ocr:'OCR',separate:'Tách nền',mux:'Render'};
  if(force){ const msg=stage==='download'?'🗑 XOÁ đã tải & TẢI LẠI TỪ ĐẦU?':'Chạy lại "'+(nm[stage]||stage)+'"? Ghi đè kết quả cũ.'; if(!confirm(msg)) return; }
  paintRunning(id,stage,'đang xếp hàng…'); await flushPaint();
  let q=force?'?force=true':'';
  if(stage==='tts'){ const vs=document.getElementById('tts-voice'); const v=vs&&vs.value; if(!v){alert('Chưa có giọng — chọn giọng trước.'); paintIdle(id); void refresh(); return;} q+=(q?'&':'?')+'voice='+encodeURIComponent(v);
    const sp=(document.getElementById('tts-speed')||{}).value; if(sp) q+='&speed='+sp;
    q+='&engine='+curEngine(); }
  if(stage==='asr'){
    const eng=curAsrEngine();
    q+=(q?'&':'?')+'engine='+encodeURIComponent(eng);
    void saveAsrEngine(id,eng);
  }
  try{
    const r=await fetch('/api/jobs/'+id+'/run/'+stage+q,{method:'POST'});
    if(!r.ok){ const d=await r.json().catch(()=>({})); busyHint(d.error||r.status); paintIdle(id); lastDetailSig=null; void refresh(); return; }
    lastDetailSig=null; void refresh();
  }catch(e){ alert('Lỗi: '+e); void refresh(); }
}); }
async function stopJob(id){ return guardAct('stop:'+id, async()=>{
  paintIdle(id);
  if(lastFull&&lastFull.job_id===id){
    Object.keys(lastFull.stages||{}).forEach(k=>{
      if((lastFull.stages[k]||{}).status==='running') lastFull.stages[k]={...lastFull.stages[k], status:'pending'};
    });
    lastFull.status='pending'; lastDetailSig=null;
    document.querySelectorAll('.autobar .btn.pri.big, .studio-cta .btn.pri.big, .console-run .btn.pri.big').forEach(b=>{ b.disabled=false; });
    document.querySelectorAll('.autobar .btn.stop, .studio-cta .btn.stop, .console-run .btn.stop').forEach(b=>{ b.disabled=true; });
  }
  await flushPaint();
  try{ await fetch('/api/jobs/'+id+'/stop',{method:'POST'}); lastDetailSig=null; void refresh(); }catch(e){ alert('Lỗi: '+e); }
}); }
async function markStepDone(id,stage){ return guardAct('mark:'+id+':'+stage, async()=>{
  if(!confirm('Đánh dấu bước "'+stage+'" = Xong ngay? (không chạy lại)')) return;
  try{
    let r=await fetch('/api/jobs/'+id+'/mark-done',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({stage})});
    let d=await r.json().catch(()=>({}));
    if(!r.ok && /chưa có file/i.test(d.error||'')){
      if(!confirm((d.error||'')+'\n\nVẫn ép đánh dấu xong?')) return;
      r=await fetch('/api/jobs/'+id+'/mark-done',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({stage,force:true})});
      d=await r.json().catch(()=>({}));
    }
    if(!r.ok){ alert(d.error||r.status); return; }
    lastDetailSig=null; void refresh();
  }catch(e){ alert('Lỗi: '+e); }
}); }
async function markJobDone(id){ return guardAct('markall:'+id, async()=>{
  if(!confirm('Đánh dấu MỌI bước đã có file kết quả = Xong?')) return;
  try{
    const r=await fetch('/api/jobs/'+id+'/mark-done',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({all:true})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){ alert(d.error||r.status); return; }
    lastDetailSig=null; void refresh();
  }catch(e){ alert('Lỗi: '+e); }
}); }
async function saveTtsVoice(id,v){ try{ await fetch('/api/jobs/'+id+'/tts-voice',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({voice:v})}); }catch(e){} }
// Đổi giọng: lưu ngay + cập nhật nhãn (đỡ hiểu nhầm dropdown ≠ 'đang dùng').
function voiceChanged(id,sel,made){ saveTtsVoice(id,sel.value); const u=document.getElementById('tts-using');
  if(u) u.textContent=made?` · sẽ đọc lại bằng: ${sel.value}`:` · sẽ dùng: ${sel.value}`; }
// Tốc độ đọc: <1 chậm, 1 chuẩn, >1 nhanh (ffmpeg atempo — VieNeu + OmniVoice).
function speedOptions(sel){ const cur=String(sel||1.0);
  return [['0.8','0.8× chậm'],['0.9','0.9×'],['1.0','1.0× chuẩn'],['1.1','1.1×'],['1.2','1.2× nhanh']]
    .map(([v,t])=>`<option value="${v}" ${v===cur||(cur==='1'&&v==='1.0')?'selected':''}>${t}</option>`).join(''); }
async function saveTtsSpeed(id,v){ try{ await fetch('/api/jobs/'+id+'/tts-voice',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({speed:parseFloat(v)})}); }catch(e){} }
async function runAll(id){ return guardAct('all:'+id, async()=>{
  const firstStage=(((lastFull||{}).stages||{}).download||{}).status==='done'?'asr':'download';
  paintRunning(id, firstStage, 'Chạy tự động…'); await flushPaint();
  await loadVoices();
  const av=document.getElementById('auto-voice'), vs=document.getElementById('tts-voice');
  const stored=(lastFull&&lastFull.stages.tts)?lastFull.stages.tts.voice:null;
  const engNow=(document.getElementById('auto-engine')||{value:'vieneu'}).value;
  const voice=(av&&av.value)||(vs&&vs.value)||stored||((VOICES&&VOICES[engNow]&&VOICES[engNow][0])?VOICES[engNow][0].id:null);
  if(!voice){ alert('Chưa chọn giọng — hãy chọn giọng (hoặc Thêm giọng) trước khi Chạy tự động. Không chạy render tiếng gốc.'); paintIdle(id); void refresh(); return; }
  const audio_mode=(document.getElementById('auto-audio')||{value:'voice_only'}).value;
  const speed=parseFloat((document.getElementById('auto-speed')||{value:'1.0'}).value);
  const tts_engine=(document.getElementById('auto-engine')||{value:'vieneu'}).value;
  const asr_engine=(document.getElementById('auto-asr')||{value:'hybrid_fa'}).value;
  const ocr=!!(document.getElementById('auto-ocr')||{}).checked;
  const fit=(document.getElementById('auto-fit')||{checked:true}).checked;
  const resolution=(document.getElementById('auto-res')||{value:'source'}).value||'source';
  const translate_mode=(document.getElementById('auto-tr-mode')||{value:'batch'}).value||'batch';
  const review_backup=(document.getElementById('auto-review-backup')||{value:'off'}).value||'off';
  const zoom=parseFloat((document.getElementById('auto-zoom')||{value:'1.12'}).value)||1.12;
  const motion=!!(document.getElementById('auto-motion')||{checked:true}).checked;
  const lut=!!(document.getElementById('auto-lut')||{checked:true}).checked;
  const grain=(document.getElementById('auto-grain')||{value:'weak'}).value||'weak';
  const vbitrate=(document.getElementById('auto-vbit')||{value:'8M'}).value||'8M';
  const flip=(document.getElementById('auto-flip')||{value:''}).value||'';
  const frame=(document.getElementById('auto-frame')||{value:'off'}).value||'off';
  const brand_mode=(document.getElementById('auto-brand-mode')||{value:'off'}).value||'off';
  const brand_text=(document.getElementById('auto-brand-text')||{value:''}).value||'';
  const brand_logo=(document.getElementById('auto-brand-logo')||{value:'off'}).value||'off';
  const brand=brand_mode!=='off';
  const orig_vol=parseFloat((document.getElementById('auto-ovol')||{value:(audio_mode==='duck_bgm')?'0.22':'0.6'}).value);
  const body={voice,speed,tts_engine,asr_engine,audio_mode,voice_vol:0.2,orig_vol,ocr,fit,resolution,translate_mode,review_backup,
    zoom,motion,lut,grain,flip,frame,brand,brand_mode,brand_text,brand_logo,vbitrate};
  try{
    const r=await fetch('/api/jobs/'+id+'/run-all',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(!r.ok){ const d=await r.json().catch(()=>({})); busyHint(d.error||r.status); paintIdle(id); lastDetailSig=null; void refresh(); return; }
    lastDetailSig=null; void refresh();
  }catch(e){ alert('Lỗi: '+e); void refresh(); }
}); }
async function delJob(ev,id){ ev.stopPropagation(); if(!confirm('Xoá job (cả video/audio đã tải)?')) return; try{await fetch('/api/jobs/'+id,{method:'DELETE'}); if(selected===id)selected=null; await refresh();}catch(e){} }
function seekTo(sec){
  const go=()=>{ if(videoEl){ videoEl.currentTime=sec; videoEl.play().catch(()=>{}); videoEl.scrollIntoView({behavior:'smooth',block:'center'}); } };
  if(selectedStep!=='download'){ selectStep('download'); setTimeout(go,350); } else go();
}
let videoEl=null,videoJobId=null,videoSrcKind=null;
function ensureVideo(j,dlDone){
  const slot=document.getElementById('studio-pslot')||document.getElementById('pslot');
  if(!dlDone||!slot) return;
  const muxDone=(j.stages.mux||{}).status==='done';
  const kind=muxDone?'output':'source';
  const src=muxDone
    ?('/api/jobs/'+j.job_id+'/file/output.mp4#t=0.1')
    :('/api/jobs/'+j.job_id+'/video#t=0.1');
  if(videoEl && videoJobId===j.job_id && videoSrcKind===kind && slot.contains(videoEl)) return;
  slot.innerHTML='';
  videoEl=document.createElement('video');
  videoEl.className='preview';
  videoEl.controls=true;
  videoEl.preload='metadata';
  videoEl.src=src;
  videoJobId=j.job_id;
  videoSrcKind=kind;
  slot.appendChild(videoEl);
}

function setConn(ok){
  const b=document.getElementById('conn'); if(!b) return;
  b.classList.toggle('on',!ok);
}
let lastListSig=null;
function listStructureSig(jobs){
  // Không gồm overall/% — % patch riêng (tránh rebuild sidebar mỗi poll)
  return (jobs||[]).map(j=>[j.job_id,j.status,j.title||'',j.group_id||'',j.group_index||''].join(':')).join('|')+'#'+(selected||'');
}
function detailStableSig(j){
  if(!j) return '';
  const parts=[j.job_id,j.status,j.title||'',logOpen?'1':'0',selectedStep||''];
  STAGES.forEach(s=>{
    const st=j.stages[s.key]||{};
    parts.push(s.key,st.status||'',st.n_done|0,st.n_total|0,st.n_segments|0,st.n_missing|0,st.n_clips|0,String(st.error||'').slice(0,40));
  });
  return parts.join('|');
}
async function refresh(){
  try{ cache=await (await fetch('/api/jobs')).json();
    if(_optLock && Date.now()<=_optLock.until){
      const lockId = _optLock.id;
      cache=(cache||[]).map(j=>(j && lockId && j.job_id===lockId)?applyOpt(j):j);
    }
    const ls=listStructureSig(cache);
    if(ls!==lastListSig){ lastListSig=ls; renderList(cache); }
    else patchListLive(cache);
    setConn(true);
    const j=cache.find(x=>x.job_id===selected)||null;
    if(j){
      setJobMode(true);
      let full=await (await fetch('/api/jobs/'+selected)).json();
      full=applyOpt(full);
      const sig=detailStableSig(full);
      if(sig!==lastDetailSig){
        lastDetailSig=sig;
        renderDetail(full);
      } else {
        // cùng cấu trúc — chỉ patch live mọi UI mode
        const mode=uiMode();
        if(mode==='studio') patchStudioLive(full);
        else if(mode==='console') patchConsoleLive(full);
        else patchClassicLive(full);
      }
    } else {
      if(composeOpen) applyComposeUi();
      else { applyComposeUi(); if(lastDetailSig!==null){ lastDetailSig=null; renderDetail(null); } }
    }
  }catch(e){ setConn(false); }
}
// ===== Cookies: trạng thái + import =====
async function loadCookieStatus(){
  try{ const d=await (await fetch('/api/cookies')).json(); renderCookie(d); }
  catch(e){ renderCookie(null); }
}
function renderCookie(files){
  const box=document.getElementById('cookie-box'); if(!box) return;
  let c=null;
  if(files){ c=files['bilibili_cookies.txt'] || Object.values(files)[0] || null; }
  const ok=c && c.valid && c.n_cookies>0;
  const label=ok?'Cập nhật':'Nhập cookies';
  const st=ok
    ? `<span class="cst" title="${c.n_cookies} cookie">🍪 <b class="ok">${c.n_cookies} cookie</b></span>`
    : `<span class="cst">🍪 <b class="bad">thiếu / rỗng</b></span>`;
  box.innerHTML=`${st}<span class="cklink" onclick="openCookie()">${label}</span>`;
}

// ---- popup cookies ----
let ckFile=null;
function openCookie(){
  ckFile=null;
  document.getElementById('ck-text').value='';
  document.getElementById('ck-file').textContent='';
  const msg=document.getElementById('ck-msg'); msg.textContent=''; msg.className='dlg-msg';
  document.getElementById('ck-plat').value='auto';
  document.getElementById('ck-mask').classList.add('on');
  setTimeout(()=>document.getElementById('ck-text').focus(),50);
}
function closeCookie(){ document.getElementById('ck-mask').classList.remove('on'); }
function ckFilePick(input){
  const f=input.files&&input.files[0];
  ckFile=f||null;
  document.getElementById('ck-file').textContent=f?('· '+f.name):'';
}
async function saveCookie(){
  const text=document.getElementById('ck-text').value.trim();
  const plat=document.getElementById('ck-plat').value;
  const msg=document.getElementById('ck-msg');
  const btn=document.getElementById('ck-save');
  if(!text && !ckFile){ msg.className='dlg-msg bad'; msg.textContent='Hãy dán cookies hoặc chọn file.'; return; }
  const fd=new FormData();
  if(text) fd.append('text',text);
  if(ckFile) fd.append('file',ckFile);
  fd.append('platform',plat);
  btn.disabled=true; const o=btn.textContent; btn.textContent='Đang lưu...';
  msg.className='dlg-msg'; msg.textContent='';
  try{
    const r=await fetch('/api/cookies',{method:'POST',body:fd});
    const d=await r.json();
    if(!r.ok||d.error){ msg.className='dlg-msg bad'; msg.textContent='✕ '+(d.error||r.status); }
    else{
      msg.className='dlg-msg ok'; msg.textContent=`✓ Đã lưu ${d.n_cookies} cookie (${d.platform}).`;
      await loadCookieStatus();
      setTimeout(closeCookie,900);
    }
  }catch(e){ msg.className='dlg-msg bad'; msg.textContent='Lỗi: '+e; }
  finally{ btn.disabled=false; btn.textContent=o; }
}
document.addEventListener('keydown',e=>{
  if(e.key==='Escape') closeCookie();
  if((e.ctrlKey||e.metaKey) && e.key==='Enter'){
    const t=e.target;
    if(t && t.id==='url'){ e.preventDefault(); addJob(); }
  }
});
updateUrlCount();

// ===== Cột co giãn (kéo thanh giữa) =====
const COL_MIN={1:[150,460],2:[240,720]};   // [min,max] px cho c1, c2
function applyCols(){
  const app=document.querySelector('.app');
  const c1=localStorage.getItem('col1'), c2=localStorage.getItem('col2');
  if(c1) app.style.setProperty('--c1',c1+'px');
  if(c2) app.style.setProperty('--c2',c2+'px');
}
function initGutters(){
  const app=document.querySelector('.app');
  document.querySelectorAll('.gutter').forEach(g=>{
    g.addEventListener('mousedown',e=>{
      e.preventDefault();
      const which=g.dataset.g;
      const varName=which==='1'?'--c1':'--c2';
      const cur=parseFloat(getComputedStyle(app).getPropertyValue(varName))||(which==='1'?210:336);
      const startX=e.clientX;
      const [mn,mx]=COL_MIN[which];
      g.classList.add('drag'); document.body.classList.add('col-resizing');
      const move=ev=>{
        let w=cur+(ev.clientX-startX);
        w=Math.max(mn,Math.min(mx,w));
        app.style.setProperty(varName,w+'px');
      };
      const up=()=>{
        document.removeEventListener('mousemove',move);
        document.removeEventListener('mouseup',up);
        g.classList.remove('drag'); document.body.classList.remove('col-resizing');
        const w=parseFloat(getComputedStyle(app).getPropertyValue(varName));
        localStorage.setItem(which==='1'?'col1':'col2',Math.round(w));
      };
      document.addEventListener('mousemove',move);
      document.addEventListener('mouseup',up);
    });
    // đúp chuột -> đặt lại mặc định
    g.addEventListener('dblclick',()=>{
      const which=g.dataset.g;
      app.style.setProperty(which==='1'?'--c1':'--c2',(which==='1'?210:336)+'px');
      localStorage.removeItem(which==='1'?'col1':'col2');
    });
  });
}
applyCols(); initGutters();
applyComposeUi();

// Poll THÍCH ỨNG: 2s khi có job đang chạy (cần mượt), 5s khi rảnh (đỡ tải nền).
async function pollLoop(){
  try{ await refresh(); }catch(e){}
  const running=Array.isArray(cache)&&cache.some(j=>j.status==='running');
  setTimeout(pollLoop, running?2000:5000);
}
pollLoop();
loadCookieStatus(); setInterval(loadCookieStatus,30000);
