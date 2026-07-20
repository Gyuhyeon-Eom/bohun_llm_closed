/* ============================================================
   board.js — T/F 피드백 게시판 로직 (board.html에서 분리)
   ============================================================ */
const API = '';   // 같은 오리진(FastAPI)에서 서빙 시 빈 문자열. 다르면 'http://localhost:8000' 등으로.
const VTYPES = [
  ['proc','#d97706','업무절차 불일치','실제 심사 절차·지침과 화면 흐름이 다름'],
  ['item','#2563eb','항목(영역) 추가 제안','체크리스트·서식·입력폼에 항목 추가 필요'],
  ['func','#0e7490','기능 추가 요청','화면에 없는 필요 기능'],
  ['improve','#15803d','개선 제안','기존 기능의 사용성·표시 개선'],
  ['ai','#7c3aed','AI 산출물 관련','AI 검토·요약·판단문안·유사사례 정확성'],
  ['term','#6b7280','용어·표기 오류','법령 용어, 서식 명칭, 오탈자'],
  ['qna','#94a3b8','단순 질문','화면·기능 문의'],
];
const DEPTS={'보훈심사위원회':['심사1과','심사2과','심사3과'],'국가보훈부':['심사기준과','보상정책과'],'지방보훈청':['보상과','등록관리과']};
const SCREENS={'심사등록':['안건현황'],'요건심사':['1. 신청사항','2. 관련자료','3. 관계법령·판단','4. 종합판단','분과별 체크리스트','AI 검토 패널'],'패스트 트랙':['대상 확인·선별','구비서류 확인','우선처리 현황','검토서 작성(패스트트랙)'],'상이판정':['신체검사 결과 입력','등급 후보 매칭','종합등급 산정','판정 확정'],'AI 챗봇':['대화'],'문서 접수(OCR)':['업로드·검수'],'공통·기타':['공통']};
const AREAS={'2. 관련자료':['가. 병적관련자료','나. 요건 사실 확인서','다. 의무기록(시간순 표)','신청서류 보완 체크리스트'],'4. 종합판단':['가. 신청경위 요약','나·다. 판단 내용 및 결론','해당/비해당 선택','결론 문안'],'안건현황':['목록 컬럼 구성','성명·접수번호 검색','AI 심사 버튼','상태 표시']};
const LISTS={docs:{title:'매건별 자료 보완 요청 체크리스트',items:['주민등록초본','병적증명서','전역증','병상일지','의무기록사본','요건사실확인서','발병경위서','공무상병인증서','전공상심사결정서','건강보험요양급여내역','국민연금 장애심사자료','인우보증서']},chk2:{title:'(공통) 검토서 체크리스트',items:['기존 등록 여부','병적·경력 작성 확인','신청상이 확인','원상병명 확인','발병경위·시기 확인','시계열 정합성','수술적 처치 여부','의학자문 수용여부','논리 모순 여부','재신청·재심의','형평성','오탈자','법령·시행령 적용']},basic:{title:'안건 기본정보 항목',items:['상이일','수술일','부위','병명','소속/직급']},etc:{title:'기타(직접 입력)',items:[]}};

let vtype='item', imp='m', insertAfter=4, opFilter='all', boardData=[];

/* 폼 초기화 */
function initTypes(){
  document.getElementById('typeGrid').innerHTML = VTYPES.map(([v,c,t,d])=>
    `<label class="type-item${v===vtype?' checked':''}" data-v="${v}" onclick="pickType('${v}')">
      <span class="t"><span class="dot" style="background:${c}"></span>${t}</span>
      <span class="d">${d}</span></label>`).join('');
  document.getElementById('itemProposal').classList.toggle('show', vtype==='item');
}
function pickType(v){ vtype=v; initTypes(); }
function syncDept(){ const o=document.getElementById('org').value,d=document.getElementById('dept');
  d.innerHTML=(DEPTS[o]||[]).map(x=>`<option>${x}</option>`).join('')||'<option value="">기관 먼저 선택</option>'; syncAuthor(); }
function syncScreen(){ const m=document.getElementById('menu').value,s=document.getElementById('screen');
  s.innerHTML=(SCREENS[m]||[]).map(x=>`<option>${x}</option>`).join(''); if(m==='요건심사')s.value='2. 관련자료'; syncArea(); }
function syncArea(){ const sc=document.getElementById('screen').value,a=document.getElementById('area');
  a.innerHTML=((AREAS[sc]||[]).concat(['항목 추가 필요(현재 항목 외)','화면 전체·기타'])).map(x=>`<option>${x}</option>`).join(''); }
function renderChecklist(){ const L=LISTS[document.getElementById('targetList').value];
  document.getElementById('clTitle').innerHTML=`${L.title} · 현재 ${L.items.length}개`;
  const nm=document.getElementById('newItem').value||'(추가 항목명)';
  if(insertAfter>=L.items.length)insertAfter=L.items.length-1;
  document.getElementById('clItems').innerHTML=L.items.map((it,i)=>{
    const sel=i===insertAfter?' class="sel"':''; const chip=i===insertAfter?`<div style="padding:4px 12px 6px 34px;border-top:1px dashed var(--blue-200);background:#f8faff"><span class="new-item-chip">＋ ${nm}</span></div>`:'';
    return `<li${sel} onclick="insertAfter=${i};renderChecklist()"><span style="color:var(--faint);width:18px;font-size:11px">${i+1}</span>${it}<span class="pos">${i===insertAfter?'▼ 이 뒤에':''}</span></li>${chip}`;
  }).join('')||'<li style="color:var(--faint)">항목 없음 — 사유란에 직접 기재</li>'; }
document.querySelectorAll('[data-seg=imp] label').forEach(lb=>lb.onclick=()=>{
  document.querySelectorAll('[data-seg=imp] label').forEach(x=>x.classList.remove('on')); lb.classList.add('on'); imp=lb.dataset.v; });
function syncAuthor(){ const o=document.getElementById('org').value,d=document.getElementById('dept').value||'',b=document.getElementById('bunkwa').value,n=document.getElementById('writer').value.trim();
  document.getElementById('onlineOrg').textContent=(o&&n)?`${o} ${d}${b&&b!=='해당 없음'?' · '+b:''}`:'—';
  document.querySelectorAll('.qa-author').forEach(e=>{ if(o&&n){e.textContent=`${o} ${d}${b&&b!=='해당 없음'?' · '+b:''} · ${n}`;e.classList.remove('missing');}else{e.textContent='⚠ 상단 ① 작성자 정보를 먼저 입력';e.classList.add('missing');} }); }
['bunkwa'].forEach(id=>document.getElementById(id).addEventListener('change',syncAuthor));

function author(){ return {org:document.getElementById('org').value,dept:document.getElementById('dept').value,
  bunkwa:document.getElementById('bunkwa').value,writer:document.getElementById('writer').value.trim()}; }
function checkAuthor(){ const a=author(); if(!a.org||!a.writer){ toast('작성자 정보(소속·성명)를 먼저 입력하세요'); window.scrollTo({top:0,behavior:'smooth'}); document.getElementById(!a.org?'org':'writer').focus(); return false;} return true; }
function toast(m){ const t=document.getElementById('toast'); t.textContent=m; t.classList.add('show'); setTimeout(()=>t.classList.remove('show'),2200); }

/* 제출 */
async function submitOpinion(e){ e.preventDefault(); if(!checkAuthor())return false;
  const a=author(); const content=document.getElementById('content').value.trim();
  let proposal=null;
  if(vtype==='item'){ const L=LISTS[document.getElementById('targetList').value];
    proposal={list:L.title,item:document.getElementById('newItem').value,
      pos:(L.items[insertAfter]||'(위치)')+' 뒤',basis:document.getElementById('propBasis').value,reason:document.getElementById('propReason').value}; }
  if(!content && !proposal){ toast('의견 내용을 입력하세요'); return false; }
  const body={...a,menu:document.getElementById('menu').value,screen:document.getElementById('screen').value,
    area:document.getElementById('area').value,vtype,importance:imp,content,proposal};
  try{ const r=await fetch(API+'/board',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const j=await r.json(); if(j.error){toast(j.error);return false;}
    toast('의견이 등록되었습니다'); document.getElementById('content').value=''; await loadBoard();
    document.querySelector('.list-card').scrollIntoView({behavior:'smooth'});
  }catch(err){ toast('등록 실패 — API 서버 확인'); } return false; }

/* 목록 로드·렌더 */
async function loadBoard(){
  try{ boardData=await (await fetch(API+'/board')).json(); }catch(e){ boardData=[]; }
  opPage=1; qaPage=1; renderOpinions(); renderQA();
}
let opPage=1;
function renderOpinions(){
  const ops=boardData.filter(x=>x.kind==='opinion');
  const cnt={done:ops.filter(o=>o.status==='반영완료').length,rev:ops.filter(o=>o.status==='검토중').length,con:ops.filter(o=>o.status==='협의요청').length};
  document.getElementById('opCnt').textContent=`전체 ${ops.length}건 · 반영완료 ${cnt.done} · 검토중 ${cnt.rev} · 협의요청 ${cnt.con}`;
  const list=opFilter==='all'?ops:ops.filter(o=>o.vtype===opFilter);
  const tlabel={proc:'업무절차',item:'항목 추가',func:'기능 추가',improve:'개선 제안',ai:'AI 산출물',term:'용어·표기',qna:'단순 질문'};
  if(!list.length){ document.getElementById('opTableWrap').innerHTML='<div class="empty">해당하는 의견이 없습니다.</div>'; document.getElementById('opPager').innerHTML=''; return; }
  const {slice, page, totalPages}=pageSlice(list, opPage, 10);
  opPage=page;
  document.getElementById('opTableWrap').innerHTML=`<table><thead><tr>
    <th style="width:48px">No</th><th style="width:104px">유형</th><th>의견</th>
    <th style="width:150px">작성</th><th style="width:120px">처리상태</th><th style="width:78px">공감</th></tr></thead><tbody>${
    slice.map(o=>`<tr>
      <td>${o.fb_id}</td>
      <td><span class="tag ${o.vtype}">${tlabel[o.vtype]||'기타'}</span></td>
      <td>${esc(o.content||(o.proposal?`[항목추가] ${o.proposal.item}`:''))}
        <span class="loc">${esc([o.menu,o.screen,o.area].filter(Boolean).join(' › '))}</span></td>
      <td>${esc(o.dept||o.org||'')}${o.bunkwa&&o.bunkwa!=='해당 없음'?' · '+esc(o.bunkwa):''}<br>${esc((o.writer||'').replace(/\(.*\)/,''))}<br><span class="hint">${o.created_at}</span></td>
      <td><span class="st ${o.status}">${o.status}</span>${o.status_note?`<br><span class="hint">${esc(o.status_note)}</span>`:''}</td>
      <td><button class="like" onclick="like(${o.fb_id},this)">&#128077; ${o.likes||0}</button></td>
    </tr>`).join('')}</tbody></table>`;
  renderPager('opPager', page, totalPages, 'gotoOpPage');
}
function gotoOpPage(p){ opPage=p; renderOpinions(); }
async function like(id,btn){ try{ const j=await (await fetch(API+`/board/${id}/like`,{method:'POST'})).json();
  btn.classList.add('on'); btn.innerHTML='&#128077; '+j.likes; }catch(e){} }
document.querySelectorAll('#opFilters .fchip').forEach(c=>c.onclick=()=>{
  document.querySelectorAll('#opFilters .fchip').forEach(x=>x.classList.remove('on')); c.classList.add('on'); opFilter=c.dataset.f; opPage=1; renderOpinions(); });

/* Q&A 렌더 */
const POS={need:['need','필요'],noneed:['noneed','불필요'],review:['review','검토필요'],consult:['consult','협의필요']};
let qaPage=1;
function renderQA(){
  const qas=boardData.filter(x=>x.kind==='qa');
  const answers=boardData.filter(x=>x.kind==='answer');
  document.getElementById('qaCnt').textContent=`전체 ${qas.length}건`;
  if(!qas.length){ document.getElementById('qaWrap').innerHTML='<div class="empty">확인 필요사항이 없습니다.</div>'; document.getElementById('qaPager').innerHTML=''; return; }
  const {slice, page, totalPages}=pageSlice(qas, qaPage, 5);
  qaPage=page;
  document.getElementById('qaWrap').innerHTML=slice.map((q,i)=>{
    const idx=(page-1)*5+i;
    const ans=answers.filter(a=>a.parent_id===q.fb_id);
    const cnt={};ans.forEach(a=>cnt[a.answer_pos]=(cnt[a.answer_pos]||0)+1);
    const stat=Object.entries(cnt).map(([p,n])=>`<span class="tag" style="${p==='need'?'background:#dcfce7;color:#15803d':p==='noneed'?'background:#fee2e2;color:#b91c1c':p==='review'?'background:#fef3c7;color:#b45309':'background:#f5f3ff;color:#7c3aed'}">${POS[p][1]} ${n}</span>`).join(' ');
    return `<div class="qa-item${idx===0?' open':''}">
      <div class="qa-head" onclick="this.parentElement.classList.toggle('open')">
        <span class="qa-badge">Q${idx+1}</span>
        <div class="qa-title-wrap"><div class="qa-title">${esc(q.content)}</div>
          <div class="qa-meta">${esc(q.screen||'')} · 대상: ${esc(q.target||'전체 분과')}</div></div>
        <div class="qa-stat">${stat||'<span class="tag">미답변</span>'}</div>
        <span class="qa-arrow">&#9662;</span></div>
      <div class="qa-body">
        <div class="qa-context">배경: ${esc(q.qa_context||'')}</div>
        <div class="qa-answers">${ans.map(a=>`<div class="qa-answer">
          <span class="ans-chip ${a.answer_pos}">${POS[a.answer_pos][1]}</span>
          <div class="ans-body"><b>${esc(a.org||'')} ${esc(a.dept||'')}${a.bunkwa&&a.bunkwa!=='해당 없음'?' · '+esc(a.bunkwa):''} · ${esc((a.writer||'').replace(/\(.*\)/,''))}</b> <span class="hint">${a.created_at}</span><br>${esc(a.content)}</div>
        </div>`).join('')||'<div class="hint" style="padding:4px">아직 답변이 없습니다.</div>'}</div>
        <div class="qa-form">
          <div class="qa-form-title">우리 부서 답변 등록 <span class="hint">— <b class="qa-author missing">⚠ 상단 ① 작성자 정보를 먼저 입력</b> 자동 반영</span></div>
          <div class="seg" data-qseg="${q.fb_id}" style="margin-bottom:10px">
            <label class="on" data-v="need">필요</label><label data-v="noneed">불필요</label>
            <label data-v="review">검토필요</label><label data-v="consult">협의필요</label></div>
          <div style="display:flex;gap:10px">
            <textarea id="qaAns${q.fb_id}" style="min-height:62px;flex:1" placeholder="선택 사유·조건 (예: 필요하지만 ○○ 조건 전제)"></textarea>
            <button type="button" class="btn btn-primary" style="align-self:flex-end" onclick="submitAnswer(${q.fb_id})">답변 등록</button></div>
        </div>
      </div></div>`;
  }).join('');
  document.querySelectorAll('[data-qseg] label').forEach(lb=>lb.onclick=()=>{
    lb.parentElement.querySelectorAll('label').forEach(x=>x.classList.remove('on')); lb.classList.add('on'); });
  syncAuthor();
  renderPager('qaPager', page, totalPages, 'gotoQaPage');
}
function gotoQaPage(p){ qaPage=p; renderQA(); }
async function submitAnswer(qid){ if(!checkAuthor())return;
  const a=author(); const seg=document.querySelector(`[data-qseg="${qid}"] label.on`);
  const body={...a,answer_pos:seg?seg.dataset.v:'review',content:document.getElementById('qaAns'+qid).value.trim()};
  try{ const r=await fetch(API+`/board/${qid}/answer`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const j=await r.json(); if(j.error){toast(j.error);return;} toast('답변이 등록되었습니다'); await loadBoard();
  }catch(e){ toast('등록 실패 — API 서버 확인'); } }

function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m])); }

/* 초기화 */
initTypes(); syncDept(); syncScreen(); renderChecklist(); loadBoard();
