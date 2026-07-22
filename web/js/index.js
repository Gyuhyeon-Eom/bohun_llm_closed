/* ============================================================
   index.js — 메인 화면 로직 (index.html에서 분리)

   구성 (섹션 ── 주석 기준):
     1. 공통 헬퍼·전역 상태          $, esc, dash, cases/tab/panel ...
     2. LLM 연동                     askLLM, loadLlmStatus, llmChip
     3. 화면 A: 안건현황             loadCases, renderCaseTable, 일괄 다운로드
     4. 화면 A-1: 진입 전환          enterCase
     5. 워크스페이스                 renderWork, 요건심사 안건 목록(ws*)
     6. 상이등급심사                 renderTable, 진행 DAG, AI 판정예측(grade*)
     7. 요건심사 리포트(1~4장)       sec1~sec4, judge, finalize
     8. 우측 패널·체크리스트 바      renderPanel, renderCkBar, 산출물 다운로드
     9. AI 챗봇                      renderChat, sendChat
    10. T/F 피드백 진입              openFeedback (전용 페이지 /feedback.html)

   의존: icons.js (ICONS, icon()) 를 먼저 로드해야 한다.
   ============================================================ */
const $ = id => document.getElementById(id);
const esc = t => String(t ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;');
const dash = t => t == null || t === '' ? '—' : esc(t);
const SECTIONS = ['1. 신청사항', '2. 관련자료', '3. 관계법령·판단', '4. 종합판단'];
const SNB_ITEMS = ['민원접수','심사등록','보상급여','제대군인','의료지원','보훈복지','상이판정','대부지원','교육지원','취업지원','업무지원','시스템관리'];

let cases = [], selId = null, doc = null;
let tab = 'report', curSec = 0, panel = 'ai';
let visitedSecs = new Set(), ckState = [], ckCollapsed = true;   // 체크리스트 바는 기본 접힘 — 클릭 시 펼침
let editLog = [];                 // 수정이력 패널 (세션 단위)
let similarCache = null;          // 유사사례 패널 캐시
let aiPanelLog = [];              // AI 검토 패널 질의응답
let chatMsgs = [], chatSessions = [], chatSessionId = null;   // 대화는 세션 단위로 서버 저장
let llmStatus = null;             // /llm-status 캐시 {ok, backend, model, detail}

const CHAT_TIMEOUT_MS = 180000;

/* ── 동시 사용 안내 (개인 서버 시연) — /load 폴링, 챗봇·AI검토 질의창에 표시 ── */
let sysLoad = {active: 0};
function loadHtml(id){
  const n = (sysLoad && sysLoad.active) || 0;
  const busy = n >= 2;
  let dyn = '';
  if(n === 1) dyn = ' · 현재 1명 질의 처리 중';
  if(busy) dyn = ` · 현재 ${n}명 사용 중 — 순서대로 처리되니 잠시만 기다려주세요`;
  return `<div id="${id}" class="loadline${busy?' busy':''}">${busy?icon('IconAlertTriangle',12,'color:var(--amber-600);margin-right:4px'):''}` +
    `⚑ 개인 서버 시연 환경 — 로컬 LLM이라 응답 생성이 느릴 수 있습니다${dyn}</div>`;
}
async function pollLoad(){
  try{ sysLoad = await (await fetch('/load')).json(); }catch(e){ /* 서버 무응답 시 기존 값 유지 */ }
  ['loadchip','loadchip-panel'].forEach(id => { const el = $(id); if(el) el.outerHTML = loadHtml(id); });
}
setInterval(pollLoad, 6000);   // 화면 어디에 있든 6초마다 갱신 (안내줄이 있을 때만 반영)   // 로컬 7~8B 모델 생성 대기 상한 (3분)
async function askLLM(payload){
  /* /chatbot 호출 -> {answer, sources, error}. 네트워크·타임아웃도 error 문구로 변환 */
  const ctl = new AbortController();
  const timer = setTimeout(()=>ctl.abort(), CHAT_TIMEOUT_MS);
  try{
    const res = await fetch('/chatbot', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload), signal: ctl.signal});
    if(!res.ok) return {error:`서버 오류(${res.status}) - uvicorn 로그를 확인하세요`, sources:[]};
    const d = await res.json();
    if(d.error) return {error:d.error, sources:d.sources||[]};
    if((d.answer||'').startsWith('[MOCK'))
      return {error:'생성 LLM 미연결(mock 모드) - Ollama 연동: LLM_BACKEND=openai 로 서버 재시작 (SETUP.md 8장)', sources:d.sources||[]};
    return {answer:d.answer||'(응답 없음)', sources:d.sources||[]};
  }catch(e){
    return {error: e.name==='AbortError'
      ? `응답 시간 초과(${CHAT_TIMEOUT_MS/1000}초) - 모델 로딩·생성 지연입니다. 잠시 후 다시 시도하세요`
      : '요청 실패 - API 서버 상태를 확인하세요', sources:[]};
  }finally{ clearTimeout(timer); }
}
async function loadLlmStatus(force){
  if(llmStatus && !force) return llmStatus;
  try{ llmStatus = await (await fetch('/llm-status')).json(); }
  catch(e){ llmStatus = {ok:false, backend:'?', detail:'API 서버 응답 없음'}; }
  return llmStatus;
}
function llmChip(){
  if(!llmStatus) return '';
  const ok = llmStatus.ok;
  return `<span class="llm-chip ${ok?'ok':'bad'}" title="${esc(llmStatus.detail||'')}">●
    ${ok ? `Ollama 연결됨 · ${esc(llmStatus.model||'')}` : esc(llmStatus.detail||'LLM 미연결')}</span>`;
}

function nowStr(){ const d=new Date(),p=n=>String(n).padStart(2,'0');
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`; }
function logEvent(who, what){ editLog.unshift({who, when: nowStr(), what}); if(panel==='history') renderPanel(); }

function show(v){ document.querySelectorAll('.view').forEach(e=>e.classList.remove('on')); $(v).classList.add('on'); }
function goStatus(){ show('v-status'); if(snbActive==='심사등록') loadCases(); else { gv.mode='list'; renderTable(); } }

/* ── 화면 A: 안건현황 — 상단 GNB / 좌측 트리 (정적 프레임) ── */
$('snb-items').innerHTML = SNB_ITEMS.map(n =>
  `<div class="gi ${n==='심사등록'?'on':''}">${n}</div>`).join('');
$('gtools').innerHTML = `
  <button title="이전">${icon('IconArrowLeft',15)}</button>
  <button title="새로고침" onclick="loadCases()">${icon('IconInbox',15)}</button>
  <button title="닫기">${icon('IconX',15)}</button>`;
$('gtree').innerHTML = `
  <a>요건</a><a>요건조회</a><a>재심의 및 이의신청</a><a>대상자확인</a><a>요건사실</a>
  <a>업무지정</a><a>요건심사대상관리</a><a>육군본부연계관리</a><a>근로복지공단</a>
  <div class="cap">심의</div>
  <a>심의체계관리</a><a>심의안건 종합관리</a>
  <a class="on">안건처리</a>
  <a class="on" style="padding-left:34px">★ 안건현황</a>
  <a style="padding-left:34px">일반안건관리</a>
  <a style="padding-left:34px">안건처리현황</a>
  <a style="padding-left:34px">의결서관리</a>
  <a style="padding-left:46px">의결서 발송</a>
  <a style="padding-left:46px">의결서 사후보정</a>
  <a style="padding-left:46px">제안서 수정</a>
  <a>회의자료</a><a>심사사례조회</a><a>향후업무전망</a>
  <div class="sp"></div>`;

async function loadCases(){
  const w = $('listwrap');
  casePage = 1;
  try{ cases = await (await fetch('/cases')).json(); }
  catch(e){
    w.innerHTML = `<div class="errcard">${icon('IconAlertTriangle',28,'color:var(--red-500)')}
      <div class="h">안건현황을 불러오지 못했습니다</div>
      <div class="b">일시적인 서버 오류입니다. 잠시 후 다시 시도해주세요.</div>
      <button class="btn outline sm" style="margin-top:6px" onclick="loadCases()">다시 시도</button></div>`;
    $('caseCount').textContent = '0'; $('gpage').innerHTML = '';
    return;
  }
  if(!cases.length){
    w.innerHTML = `<div class="empty">등록된 안건이 없습니다.<br><br>
      <button class="btn" onclick="seedDemo()">시연용 안건 생성 (정형화틀 기반)</button></div>`;
    $('caseCount').textContent = '0'; $('gpage').innerHTML = '';
    return;
  }
  renderCaseTable();
}
let batchSel = new Set();   // 일괄 다운로드 선택 (행 클릭의 단일 선택과 별개)
function toggleBatch(id, on){
  on ? batchSel.add(id) : batchSel.delete(id);
  updateBatchBtn();
}
function toggleBatchAll(on){
  const q = ($('casesearch')?.value || '').trim();
  const visible = cases.filter(c => !q || String(c.applicant).includes(q) || String(c.recv_no).includes(q));
  batchSel = on ? new Set(visible.map(c=>c.app_id)) : new Set();
  renderCaseTable();
}
function updateBatchBtn(){
  const b = $('batchBtn'); if(!b) return;
  b.disabled = !batchSel.size;
  b.classList.toggle('primary', !!batchSel.size);
  b.textContent = `의결서 일괄 다운로드${batchSel.size?` (${batchSel.size})`:''}`;
}
function doBatchDownload(){
  if(!batchSel.size) return;
  logEvent('담당자', `심의의결서 일괄 다운로드 ${batchSel.size}건`);
  window.open(`/decision-doc/export-batch?ids=${[...batchSel].join(',')}`, '_blank');
}
let casePage = 1;
function renderCaseTable(){
  const w = $('listwrap');
  const q = ($('casesearch')?.value || '').trim();
  const visible = cases.filter(c => !q || String(c.applicant).includes(q) || String(c.recv_no).includes(q));
  $('caseCount').textContent = visible.length;
  const {slice, page, totalPages} = pageSlice(visible, casePage, 10);
  casePage = page;
  const rows = slice.map((c,i)=>`
    <tr id="row${c.app_id}" onclick="selectCase(${c.app_id})">
      <td style="width:26px;text-align:center" onclick="event.stopPropagation()"><input type="checkbox" ${batchSel.has(c.app_id)?'checked':''} onchange="toggleBatch(${c.app_id}, this.checked)" title="일괄 다운로드 선택"></td>
      <td style="width:34px">${(page-1)*10 + i + 1}</td>
      <td class="mono">${esc(c.recv_no)}</td>
      <td class="mut">-</td>
      <td class="mono">${esc(c.recv_no)}</td>
      <td class="nm">${esc(c.applicant)}${c.is_real?' <span class="realtag">실데이터</span>':''}</td>
      <td class="mut">-</td>
      <td>요건심사</td>
      <td>${esc(c.review_content)}</td>
      <td>${esc(c.duty_type)}${c.is_death?' <span class="note">사망</span>':''}</td>
      <td>제${c.subcommittee}분과</td>
      <td class="mut">-</td>
      <td class="mut">-</td>
      <td class="mut">-</td>
      <td>${c.is_death?'사망':'-'}</td>
      <td class="mut">-</td>
    </tr>`).join('');
  w.innerHTML = `<div class="tblcard"><table class="gx"><thead><tr>
    <th style="width:26px"><input type="checkbox" onchange="toggleBatchAll(this.checked)" title="전체 선택"></th><th style="width:34px">No</th><th>접수번호</th><th>민원접수일자</th><th>안건번호</th><th>성명</th><th>주민등록번호</th>
    <th>심의유형</th><th>심의내용</th><th>신분</th><th>분과</th><th>담당자</th><th>팀장</th><th>과장</th><th>비고</th><th>담당배정일자</th>
  </tr></thead>
  <tbody>${rows}</tbody></table>
  ${slice.length ? '' : `<div class="emptyrows">${icon('IconInbox',26,'color:var(--border-strong)')}조건에 맞는 안건이 없습니다.</div>`}</div>`;
  renderPager('gpage', page, totalPages, 'gotoCasePage');
  updateBatchBtn();
  if(selId && slice.some(c=>c.app_id===selId)) selectCase(selId);
  else { selId = null; $('aiBtn').disabled = true; }
}
function gotoCasePage(p){ casePage = p; renderCaseTable(); }
async function seedDemo(){
  $('listwrap').innerHTML = '<div class="empty"><span class="loading">생성 중</span> — 사건·의무기록·과거사례 풀·그래프</div>';
  await fetch('/cases/demo-seed', {method:'POST'}); loadCases();
}
function selectCase(id){
  selId = id;
  document.querySelectorAll('table.gx tbody tr').forEach(r=>r.classList.remove('sel'));
  const row = $('row'+id);
  if(row) row.classList.add('sel');
  $('aiBtn').disabled = false;
}

/* ── 화면 A-1: 진입 전환 — 전환 중 의결서 자료 패키지 선적재 ── */
async function enterCase(id){
  selId = id;
  const c = cases.find(x=>x.app_id===id);
  $('entry-msg').textContent = `${c.recv_no} 안건 정보를 불러오는 중`;
  show('v-entry');
  const [d] = await Promise.all([
    (await fetch('/decision-doc/'+id)).json(),
    new Promise(r=>setTimeout(r, 900)),
  ]);
  doc = d;
  tab='report'; curSec=0; panel='ai';
  visitedSecs = new Set(); ckState = (doc.checklist||[]).map(()=>false);
  editLog = []; similarCache = null; aiPanelLog = []; evOpen = {}; ckCollapsed = true; s3Modal = null;  // 진입 시에도 접힘 유지
  gv = {mode:'list', ga:null, tab:'info', diseaseInput:'', part:null, pred:null, predLoading:false, modal:null, predSel:0};
  logEvent('AI 자동생성', '공통뼈대 1~4장 자료 패키지 조립 (병적·의무기록·법령·분과기준)');
  $('gnav-back').innerHTML = icon('IconChevronLeft', 18);
  $('gnav-case').textContent = `${doc.recv_no} · ${doc.applicant} · 담당 ${doc.subcommittee_info.name}`;
  show('v-work'); renderWork();
}

/* ── 워크스페이스 ── */
const TABS = [
  {id:'report', icon:'IconFileText', label:'요건심사'},
  {id:'table',  icon:'IconColumns',  label:'상이등급심사'},
  {id:'chat',   icon:'IconCommand',  label:'AI 챗봇'},
];
function setTab(t){ tab=t; if(t!=='report'){ const b=$('ckbar'); if(b) b.style.display='none'; } renderWork(); }

/* 화면 상단 "e보훈심사AI지원시스템" 진입 — 특정 안건 없이 워크스페이스 내비 + 안건 목록만 표출 */
let wsSelId = null;
function enterWorkspaceList(){
  doc = null; tab = 'report'; wsSelId = null;
  $('gnav-back').innerHTML = icon('IconChevronLeft', 18);
  $('gnav-case').textContent = '';
  show('v-work'); renderWork();
}
function renderWork(){
  $('gnav-tabs').innerHTML = TABS.map(t=>
    `<button class="tab ${tab===t.id?'on':''}" onclick="setTab('${t.id}')">${icon(t.icon,15)} ${t.label}</button>`).join('');
  const wb = $('workbody');
  if(tab==='chat'){ renderChat(wb); $('ckbar').style.display='none'; return; }
  if(tab==='report' && !doc){ renderWsCaseList(wb); $('ckbar').style.display='none'; return; }
  wb.innerHTML = `
    ${tab==='report' ? `<div class="stepmenu">
      <div id="cksum-slot">${ckSummary()}</div>
      <div class="cap">심의의결서 구성</div>
      ${SECTIONS.map((s,i)=>`<button id="step${i}" class="${i===curSec?'on':''}" onclick="showSec(${i})">${s}</button>`).join('')}
    </div>` : ''}
    <div id="paper"></div>
    ${doc ? `<div id="rail">${[
      ['ai','IconWand2','AI 검토'],['ref','IconPaperclip','레퍼런스'],
      ['history','IconHistory','수정이력'],['similar','IconScale','유사사례'],
    ].map(([id,ic,l])=>`<button id="rail-${id}" class="${panel===id?'on':''}" title="${l}" onclick="setPanel('${id}')">${icon(ic,18)}</button>`).join('')}</div>
    <div id="rpanel" style="${panel?'':'display:none'}"></div>` : ''}`;
  if(tab==='report'){ showSec(curSec); }
  else { $('ckbar').style.display='none'; renderTable(); }   // 상이등급·기타 탭: 체크리스트 즉시 숨김
  if(doc && tab==='report'){ renderPanel(); renderCkBar(); } else { $('ckbar').style.display='none'; }
}

/* ── 요건심사 · 안건 목록 (특정 안건 미선택 상태) ── */
function renderWsCaseList(wb){
  wsCasePage = 1;
  wb.innerHTML = `
    <div class="stepmenu">
      <button class="on">요건심사</button>
      <button class="on" style="padding-left:24px">안건 목록</button>
    </div>
    <div id="paper"><div class="gnv">
      <div class="crumb">요건심사 <span style="color:var(--slate-300)">›</span> 안건 목록</div>
      <div class="gfilter">
        <div class="gfgrid">
          <div class="gfitem"><label>심의유형</label><input></div>
          <div class="gfitem"><label>심의내용</label><input></div>
          <div class="gfitem"><label>심사구분</label><input></div>
          <div class="gfitem"><label>조회번호</label><input></div>
          <div class="gfitem"><label>소속</label><input></div>
          <div class="gfitem"><label>성명</label><input id="wsCaseSearch" placeholder="성명 또는 접수번호" oninput="wsCasePage=1;wsRenderCaseTable()"></div>
          <div class="gfitem"><label>담당자</label><input></div>
          <div class="gfitem"><label>분과</label><input></div>
          <div class="gfitem"><label>신체부위</label><input></div>
          <div class="gfitem"><label>작업상태</label><input></div>
          <div class="gfitem"><label>전료과목</label><input></div>
          <div class="gfitem range"><label>접수기간</label><input type="date" value="2026-06-13"><span>~</span><input type="date" value="2026-07-15"></div>
          <div class="gfcheck"><input type="checkbox" checked disabled> 나의 안건현황 조회(기간 상관없음) 전체조회</div>
        </div>
        <div class="gfbtns"><button class="primary" onclick="wsCasePage=1;wsRenderCaseTable()">검색</button><button onclick="$('wsCaseSearch').value='';wsCasePage=1;wsRenderCaseTable()">초기화</button></div>
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:8px">
        <div class="glisthead" style="margin-bottom:0;white-space:nowrap">● 안건 목록 [총 <span class="n" id="wsCaseCount">0</span>건]</div>
        <div class="gactionbar" style="margin-bottom:0">
          <button class="primary" id="wsGenBtn" disabled onclick="wsGenerate()">AI 심의 의결서 생성</button>
          <button disabled>OCR 스캔 요약본 보기</button>
          <button disabled>유사사례 검색</button>
        </div>
      </div>
      <div class="samplenote">※ 아래 안건은 실제 사례가 아닌, 심사의결서 정형화 틀을 참고해 생성한 <b>시연용 표본 데이터</b>입니다 (개인정보 미포함 · 실사례 연계 시 의결서 원문 매핑 예정)</div>
      <div id="wsListWrap"></div>
      <div class="gpage" id="wsPage"></div>
    </div></div>`;
  wsLoadCases();
}
async function wsLoadCases(){
  if(cases.length){ wsRenderCaseTable(); return; }
  const w = $('wsListWrap');
  try{ cases = await (await fetch('/cases')).json(); }
  catch(e){
    w.innerHTML = `<div class="errcard">${icon('IconAlertTriangle',28,'color:var(--red-500)')}
      <div class="h">안건현황을 불러오지 못했습니다</div>
      <div class="b">일시적인 서버 오류입니다. 잠시 후 다시 시도해주세요.</div>
      <button class="btn outline sm" style="margin-top:6px" onclick="wsLoadCases()">다시 시도</button></div>`;
    return;
  }
  wsRenderCaseTable();
}
let wsCasePage = 1;
function wsRenderCaseTable(){
  const w = $('wsListWrap'); if(!w) return;
  const q = ($('wsCaseSearch')?.value || '').trim();
  const visible = cases.filter(c => !q || String(c.applicant).includes(q) || String(c.recv_no).includes(q));
  $('wsCaseCount').textContent = visible.length;
  const {slice, page, totalPages} = pageSlice(visible, wsCasePage, 10);
  wsCasePage = page;
  const rows = slice.map((c,i)=>`
    <tr id="wsrow${c.app_id}" onclick="wsSelectCase(${c.app_id})">
      <td style="width:26px;text-align:center" onclick="event.stopPropagation();wsSelectCase(${c.app_id})"><input type="checkbox" ${wsSelId===c.app_id?'checked':''} readonly></td>
      <td style="width:34px">${(page-1)*10 + i + 1}</td>
      <td class="mono">${esc(c.recv_no)}</td>
      <td class="mut">-</td>
      <td class="mono">${esc(c.recv_no)}</td>
      <td class="nm">${esc(c.applicant)}${c.is_real?' <span class="realtag">실데이터</span>':''}</td>
      <td class="mut">-</td>
      <td>요건심사</td>
      <td>${esc(c.review_content)}</td>
      <td>${esc(c.duty_type)}${c.is_death?' <span class="note">사망</span>':''}</td>
      <td>제${c.subcommittee}분과</td>
      <td class="mut">-</td>
      <td class="mut">-</td>
      <td class="mut">-</td>
      <td>${c.is_death?'사망':'-'}</td>
      <td class="mut">-</td>
    </tr>`).join('');
  w.innerHTML = `<div class="tblcard"><table class="gx"><thead><tr>
    <th style="width:26px"><input type="checkbox" onchange="toggleBatchAll(this.checked)" title="전체 선택"></th><th style="width:34px">No</th><th>접수번호</th><th>민원접수일자</th><th>안건번호</th><th>성명</th><th>주민등록번호</th>
    <th>심의유형</th><th>심의내용</th><th>신분</th><th>분과</th><th>담당자</th><th>팀장</th><th>과장</th><th>비고</th><th>담당배정일자</th>
  </tr></thead>
  <tbody>${rows}</tbody></table>
  ${slice.length ? '' : `<div class="emptyrows">${icon('IconInbox',26,'color:var(--border-strong)')}조건에 맞는 안건이 없습니다.</div>`}</div>`;
  renderPager('wsPage', page, totalPages, 'gotoWsCasePage');
  if(wsSelId && slice.some(c=>c.app_id===wsSelId)) wsSelectCase(wsSelId);
  else { wsSelId = null; const b=$('wsGenBtn'); if(b) b.disabled = true; }
}
function gotoWsCasePage(p){ wsCasePage = p; wsRenderCaseTable(); }
function wsSelectCase(id){
  wsSelId = id;
  document.querySelectorAll('#wsListWrap table.gx tbody tr').forEach(r=>{
    r.classList.remove('sel');
    const cb = r.querySelector('input[type=checkbox]'); if(cb) cb.checked = false;
  });
  const row = $('wsrow'+id);
  if(row){ row.classList.add('sel'); const cb = row.querySelector('input[type=checkbox]'); if(cb) cb.checked = true; }
  $('wsGenBtn').disabled = false;
}
function wsGenerate(){ if(wsSelId) enterCase(wsSelId); }

/* ── 상이등급심사 (화면 v0.4): 목록 -> 안건 상세(상세정보|AI 판정예측) -> 유사사례 모달 ── */
function GP(){ /* 등급 화면 컨테이너: 워크스페이스 탭 또는 SNB 상이판정 독립 화면 */
  return $('v-work').classList.contains('on') ? $('paper') : $('gpaper');
}
let gradeAgendas = null;
let gv = {mode:'list', ga:null, tab:'info', diseaseInput:'', part:null, pred:null, predLoading:false, modal:null, predSel:0};
const G_ST = {완료:'yes', 미흡:'hold', 부족:'no'};

async function renderTable(){
  if(gradeAgendas === null){
    GP().innerHTML = '<div class="mutetxt"><span class="loading">상이등급 안건을 불러오는 중</span></div>';
    try{ gradeAgendas = await (await fetch('/grade-agendas')).json(); }
    catch(e){
      GP().innerHTML = `<div class="errcard">${icon('IconAlertTriangle',28,'color:var(--red-500)')}
        <div class="h">상이등급 안건을 불러오지 못했습니다</div><div class="b">잠시 후 다시 시도해주세요.</div>
        <button class="btn outline sm" onclick="gradeAgendas=null;renderTable()">다시 시도</button></div>`;
      return;
    }
    if($('v-work').classList.contains('on') && tab!=='table') return;
  }
  if(gv.mode==='detail'){ renderGradeDetail(); return; }
  if(!$('gaListWrap')){
    gaListPage = 1;
    GP().innerHTML = `<div class="gnv">
      <div class="crumb">상이등급심사 · 심의 인정상이처 — 안건 선택 시 신검과목·검토사항 상세로 이동</div>
      <div class="gfilter">
        <div class="gfgrid">
          <div class="gfitem"><label>안건번호</label><input></div>
          <div class="gfitem"><label>성명</label><input id="gaSearch" placeholder="성명 또는 안건번호" oninput="gaListPage=1;renderGaList()"></div>
          <div class="gfitem"><label>신체부위</label><input></div>
          <div class="gfitem"><label>상이처</label><input></div>
          <div class="gfitem"><label>상이등급</label><input></div>
          <div class="gfitem"><label>상태</label><input></div>
          <div class="gfcheck"><input type="checkbox" checked disabled> 나의 상이등급 안건 조회(기간 상관없음) 전체조회</div>
        </div>
        <div class="gfbtns"><button class="primary" onclick="gaListPage=1;renderGaList()">검색</button><button onclick="$('gaSearch').value='';gaListPage=1;renderGaList()">초기화</button></div>
      </div>
      <div id="gaListWrap"></div>
      <div class="gpage" id="gaPager"></div>
    </div>`;
  }
  renderGaList();
}
let gaBatchSel = new Set();  // 심사표 일괄 다운로드 선택 (행 클릭의 상세 이동과 별개)
function toggleGaBatch(gaId, on){
  on ? gaBatchSel.add(gaId) : gaBatchSel.delete(gaId);
  const b = $('gaBatchBtn'); if(!b) return;
  b.disabled = !gaBatchSel.size;
  b.innerHTML = `${icon('IconDownload',13)} 심사표 일괄 다운로드${gaBatchSel.size?` (${gaBatchSel.size})`:''}`;
}
async function gaBatchDownload(){
  if(!gaBatchSel.size) return;
  for(const gid of gaBatchSel){
    const g = gradeAgendas.find(x=>x.ga_id===gid);
    if(g) await logGradeEvent(gid, g.progress||'검토', '심사표 일괄 다운로드', '담당자', `XLSX 일괄 산출 (${gaBatchSel.size}건 zip)`, null);
  }
  window.open(`/grade-agendas/export-batch?ids=${[...gaBatchSel].join(',')}`, '_blank');
}
let gaListPage = 1;
function renderGaList(){
  const q = ($('gaSearch')?.value || '').trim();
  const visible = gradeAgendas.filter(g => !q || String(g.applicant).includes(q) || String(g.agenda_no).includes(q));
  const {slice, page, totalPages} = pageSlice(visible, gaListPage, 10);
  gaListPage = page;
  const rows = slice.map(g=>`
    <tr onclick="openGrade(${g.ga_id})">
      <td style="width:26px;text-align:center" onclick="event.stopPropagation()"><input type="checkbox" ${gaBatchSel.has(g.ga_id)?'checked':''} onchange="toggleGaBatch(${g.ga_id}, this.checked)" title="일괄 다운로드 선택"></td>
      <td class="mono">${esc(g.agenda_no)}</td>
      <td class="ink">${esc(g.applicant)}${g.is_real?' <span class="realtag">실데이터</span>':''}</td>
      <td><span class="stepchip" style="${g.category==='고엽제'?'background:#fef3c7;color:#b45309':''}">${esc(g.category||'상이')}</span></td>
      <td class="ink" style="text-decoration:underline">${esc(g.body_part)}</td>
      <td>${esc(g.injury)}</td>
      <td class="mono" style="font-size:12px">${esc(g.grade_change)}</td>
      <td>${esc(g.ai_summary)}</td>
      <td><span class="res ${G_ST[g.status]||''}">${esc(g.status)}</span></td></tr>`).join('');
  $('gaListWrap').innerHTML = `
    <div class="glisthead" style="display:flex;align-items:center;gap:10px">● 안건 목록 [총 <span class="n">${visible.length}</span>건]
      <button id="gaBatchBtn" class="btn outline sm" style="margin-left:auto" ${gaBatchSel.size?'':'disabled'} onclick="gaBatchDownload()">${icon('IconDownload',13)} 심사표 일괄 다운로드${gaBatchSel.size?` (${gaBatchSel.size})`:''}</button></div>
    <div class="samplenote">※ 아래 안건은 실제 사례가 아닌, 심사의결서 정형화 틀을 참고해 생성한 <b>시연용 표본 데이터</b>입니다 (개인정보 미포함 · 실사례 연계 시 의결서 원문 매핑 예정)</div>
    <div class="tblcard"><table class="ds" style="min-width:760px"><thead><tr>
      <th style="width:26px"></th><th>안건번호</th><th>성명</th><th style="width:66px">구분</th><th>신체부위</th><th>상이처</th><th>상이등급(기존→재심의)</th><th>AI 판정근거 요약</th><th>상태</th>
    </tr></thead><tbody>${rows}</tbody></table>
    ${slice.length?'':`<div class="emptyrows">${icon('IconInbox',26,'color:var(--border-strong)')}${gradeAgendas.length?'조건에 맞는 안건이 없습니다.':'등록된 등급심사 안건이 없습니다 — 안건현황에서 시연용 안건을 생성하세요.'}</div>`}</div>`;
  renderPager('gaPager', page, totalPages, 'gotoGaPage');
}
function gotoGaPage(p){ gaListPage = p; renderGaList(); }
function openGrade(gaId){
  const g = gradeAgendas.find(x=>x.ga_id===gaId);
  gv = {mode:'detail', ga:g, tab:'info', diseaseInput:g.injury, part:g.body_part, pred:null, predLoading:false, modal:null, predSel:0};
  renderGradeDetail();
}
function gradeBack(){ gv.mode='list'; renderTable(); }
function setGTab(t){
  gv.tab = t;
  if(t==='predict' && !gv.pred && !gv.predLoading){ runGradePredict(); return; }
  renderGradeDetail();
}

function renderGradeDetail(){
  const g = gv.ga;
  const info = `<button class="backlink" onclick="gradeBack()">${icon('IconArrowLeft',14)} 목록으로</button>
    <div class="ginfo">${g.is_real?'<span class="realtag" style="margin-right:8px">실데이터</span>':''}${[['안건번호',g.agenda_no],['성명',g.applicant],['접수번호',g.recv_no],['신청구분',g.apply_type],['구분',g.category||'상이']]
      .map(([l,v])=>`<span><span class="l">${l}</span><span class="v ${l==='접수번호'?'mono':''}">${esc(v)}</span></span>`).join('')}</div>
    <div id="gradeDag">${gradeDagWidget(g)}</div>
    <div class="gtabs">
      <button class="${gv.tab==='info'?'on':''}" onclick="setGTab('info')">상세정보</button>
      <button class="${gv.tab==='predict'?'on':''}" onclick="setGTab('predict')">AI 판정예측</button>
      <button class="${gv.tab==='log'?'on':''}" onclick="setGTab('log')">작업로그</button>
      <button class="gdl" onclick="gradeExport()">${icon('IconDownload',13)} 심사표 XLSX</button>
    </div>`;
  const body = gv.tab==='predict' ? gradePredictBody(g)
             : gv.tab==='log' ? gradeLogBody(g)
             : gradeInfoBody(g);
  GP().innerHTML = info + body + gradeModal();
  GP().scrollTop = 0;
  loadGradeLog(g.ga_id);
  if(g.is_real && gv.tab==='info') loadGradeScan(g.ga_id);
}

/* 실데이터 안건: 연결된 스캔 하위문서 + LLM 정규화 결과 (근거 추적 — 0721 회의 반영) */
async function loadGradeScan(gaId){
  const box = $('gradeScanBox'); if(!box) return;
  try{
    const docs = await (await fetch(`/grade-agendas/${gaId}/scan`)).json();
    if(!docs.length){ box.innerHTML=''; return; }
    box.innerHTML = docs.map(sd=>{
      const rows = (sd.exams||[]).map(b=>{
        const n = b.norm||{};
        return `<tr>
          <td class="ink" style="white-space:nowrap">${esc(b.doc)}</td>
          <td class="mono" style="white-space:nowrap">${esc(n.date||b.fields?.date||'')}</td>
          <td style="white-space:normal;font-size:12px">${esc(n.summary||n.opinion||b.excerpt?.slice(0,90)||'')}</td>
          <td style="white-space:normal;font-size:12px" class="mut">${(n.key_findings||[]).slice(0,2).map(k=>`“${esc(k)}”`).join('<br>')}</td>
          <td class="mono mut" style="white-space:nowrap">원문 ${b.line}행${n.source?` · ${n.source==='llm'?'LLM':'규칙'}`:''}</td></tr>`;
      }).join('');
      return `<h4>스캔 원문 · 정규화 <span class="realtag">실데이터</span>
          <span class="mut">(${esc(sd.file_name)} — 하위문서 ${ (sd.exams||[]).length }건, 정규화는 LLM 정제·요약만/사실 생성 금지)</span>
          <a class="btn outline sm" style="float:right;text-decoration:none" href="/scan-docs/${sd.sd_id}/file" target="_blank">${icon('IconFileText',13)} OCR 원문 전체 보기</a></h4>
        <div class="tblcard" style="margin-bottom:16px"><table class="ds" style="min-width:820px"><thead><tr>
          <th>문서</th><th style="width:92px">날짜</th><th>정규화 요약</th><th>핵심 근거문장</th><th style="width:110px">출처</th></tr></thead>
          <tbody>${rows}</tbody></table></div>`;
    }).join('');
  }catch(e){ box.innerHTML=''; }
}

// ── 진행상태 DAG (가로 그래프) ──
const GRADE_STEPS = ['접수','자료수집','AI예측','검토','의결','완료'];
let gradeLogCache = {};
function gradeDagWidget(g){
  const cur = (gradeLogCache[g.ga_id]||{}).progress || g.progress || '접수';
  const ci = GRADE_STEPS.indexOf(cur);
  return `<div class="daglane">${GRADE_STEPS.map((s,i)=>{
    const st = i<ci?'done':(i===ci?'active':'todo');
    return `<div class="dagnode ${st}" title="${s}"><div class="dagdot">${i<ci?'✓':(i===ci?'●':i+1)}</div><div class="daglbl">${s}</div></div>${i<GRADE_STEPS.length-1?`<div class="dagedge ${i<ci?'done':''}"></div>`:''}`;
  }).join('')}</div>`;
}
function gradeLogBody(g){
  const c = gradeLogCache[g.ga_id];
  if(!c) return `<div class="mutetxt"><span class="loading">작업로그 불러오는 중</span></div>`;
  if(!c.logs || !c.logs.length) return `<div class="mutetxt">기록된 작업로그가 없습니다.</div>`;
  const rows = c.logs.map(l=>`<tr>
    <td class="mono" style="white-space:nowrap">${esc(l.created_at||'')}</td>
    <td><span class="stepchip">${esc(l.step)}</span></td>
    <td class="ink">${esc(l.event)}</td><td>${esc(l.actor||'')}</td>
    <td style="white-space:normal">${esc(l.detail||'')}</td>
    <td>${l.file_name?`<span class="filechip">${icon('IconPaperclip',11)} ${esc(l.file_name)}</span>`:''}</td>
    <td><span class="lgstat ${esc(l.status)}">${l.status==='running'?'진행중':(l.status==='failed'?'실패':'완료')}</span></td></tr>`).join('');
  return `<h4 style="margin-top:0">작업 이력 <span class="mut">(담당자·AI·시스템 활동 자동 기록)</span></h4>
    <div class="tblcard"><table class="ds" style="min-width:820px"><thead><tr>
      <th style="width:130px">시각</th><th style="width:84px">단계</th><th>이벤트</th><th style="width:70px">수행자</th>
      <th>상세</th><th style="width:150px">첨부</th><th style="width:70px">상태</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
}
async function loadGradeLog(gaId){
  if(gradeLogCache[gaId]){ const d=document.getElementById('gradeDag'); if(d)d.innerHTML=gradeDagWidget(gv.ga); return; }
  try{
    gradeLogCache[gaId] = await (await fetch(`/grade-agendas/${gaId}/log`)).json();
    const d=document.getElementById('gradeDag'); if(d)d.innerHTML=gradeDagWidget(gv.ga);
    if(gv.tab==='log') renderGradeDetail();
  }catch(e){}
}
async function gradeExport(){
  const g=gv.ga;
  await logGradeEvent(g.ga_id, g.progress||'검토', '심사표 다운로드', '담당자', 'XLSX 산출물 생성', null);
  window.open(`/grade-agendas/${g.ga_id}/export`, '_blank');
}
async function logGradeEvent(gaId, step, event, actor, detail, fileName, advance){
  try{
    await fetch(`/grade-agendas/${gaId}/log`, {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({step, event, actor, detail, file_name:fileName||null, advance:!!advance})});
    gradeLogCache[gaId]=null; await loadGradeLog(gaId);
  }catch(e){}
}
function gradeInfoBody(g){
  const items = Array.isArray(g.injury_items) ? g.injury_items
              : (typeof g.injury_items==='string' ? (()=>{try{return JSON.parse(g.injury_items)}catch(e){return[]}})() : []);
  const itemRows = items.length>1 ? items.map((it,i)=>`<tr>
    <td class="mut">${i+1}</td><td class="ink">${esc(it.injury)}</td><td>${esc(it.body_part||'')}</td>
    <td class="mono">${dash(it.prev_grade)}</td><td>${esc(it.exam_dept)}</td><td class="mono">${dash(it.exam_grade)}</td></tr>`).join('') : '';
  const meas = Array.isArray(g.measurements) ? g.measurements
             : (typeof g.measurements==='string' ? (()=>{try{return JSON.parse(g.measurements)}catch(e){return[]}})() : []);
  const tl = Array.isArray(g.med_timeline) ? g.med_timeline
             : (typeof g.med_timeline==='string' ? (()=>{try{return JSON.parse(g.med_timeline)}catch(e){return[]}})() : []);
  const measRows = (meas||[]).map(m=>`<tr>
    <td class="ink">${esc(m.name)}</td><td class="mono">${esc(String(m.value))}${esc(m.unit||'')}</td>
    <td class="mut">${esc(m.ref||'')}</td>
    <td>${m.result?`<span class="res ${['해당','정상범위'].includes(m.result)?'yes':['미달','음성'].includes(m.result)?'':'hold'}">${esc(m.result)}</span>`:''}</td></tr>`).join('');
  const tlRows = (tl||[]).map(r=>`<tr>
    <td class="mono" style="white-space:nowrap">${esc(r.date)}</td><td>${esc(r.hospital)}</td>
    <td><span class="stepchip">${esc(r.type)}</span></td><td class="ink">${esc(r.dx)}</td>
    <td style="white-space:normal">${esc(r.finding)}</td></tr>`).join('');
  return `<h4 style="margin-top:0">신체부위 · 신검과목</h4>
    <div class="tblcard" style="margin-bottom:20px"><table class="ds" style="min-width:760px"><thead><tr>
      <th>신체부위</th><th>상이처</th><th>기준일자</th><th>신검과목</th><th>신검등급</th><th>등급기준일</th><th>상이등급(기존→재심의)</th></tr></thead>
      <tbody><tr><td class="ink">${esc(g.body_part)}</td><td>${esc(g.injury)}</td><td class="mono">${esc(g.base_date)}</td>
        <td>${esc(g.exam_dept)}</td><td class="mono">${dash(g.exam_grade)}</td><td class="mono">${esc(g.grade_date)}</td><td style="white-space:normal">${esc(g.grade_change)}</td></tr></tbody></table></div>
    <div id="gradeScanBox"></div>
    ${itemRows?`<h4>요건인정 상이처별 현황 <span class="mut">(상이처별 직전등급·신검과목·신검등급 → 심사표에서 상이처별 제안등급·종합 제안등급 산출)</span></h4>
    <div class="tblcard" style="margin-bottom:20px"><table class="ds" style="min-width:720px"><thead><tr>
      <th style="width:30px">#</th><th>요건인정 상이처</th><th>신체부위</th><th>직전등급</th><th>신검과목</th><th>신검등급</th></tr></thead>
      <tbody>${itemRows}</tbody></table></div>
    <p class="mutetxt" style="margin:-10px 0 16px">※ 종합판정(등급 상향 검토) 대상 기준: 7급 상이처 3개 이상일 때만 — 그 외에는 상이처별 최고 등급으로 종합 제안 (요건심사 화면과 동일 규칙). 낮은 등급(7급 등) 상이처도 참고용으로 모두 표시됩니다.</p>`:''}
    ${g.onset_narrative?`<h4>상이 발생경위</h4><div class="card soft" style="line-height:22px">${esc(g.onset_narrative)}</div>`:''}
    ${tlRows?`<h4>의무기록 <span class="mut">(진료 시간순)</span></h4>
    <div class="tblcard" style="margin-bottom:20px"><table class="ds" style="min-width:720px"><thead><tr>
      <th style="width:92px">진료일</th><th>의료기관</th><th style="width:80px">유형</th><th>진단명</th><th>소견</th></tr></thead><tbody>${tlRows}</tbody></table></div>`:''}
    ${measRows?`<h4>신체검사 측정치 <span class="mut">(${esc(g.exam_dept)} 실측)</span></h4>
    <div class="tblcard" style="margin-bottom:20px"><table class="ds" style="min-width:0"><thead><tr>
      <th>검사항목</th><th>측정값</th><th>기준</th><th>판정</th></tr></thead><tbody>${measRows}</tbody></table></div>`:''}
    ${g.specialist_opinion?`<h4>보훈병원 전문의 소견</h4><div class="card soft" style="line-height:22px">${esc(g.specialist_opinion)}</div>`:''}
    ${g.prior_history?`<h4>이전 판정 · 재심의 경위</h4><div class="card" style="font-size:13px;color:var(--slate-700);line-height:22px">${esc(g.prior_history)}</div>`:''}
    ${g.past_history?`<h4>과거력 · 기왕증</h4><div style="font-size:13px;color:var(--slate-700);line-height:22px;margin-bottom:12px">${esc(g.past_history)}</div>`:''}
    ${g.route_note?`<h4>경로사항</h4><div class="card" style="font-size:13px;color:var(--slate-700);line-height:22px">${esc(g.route_note)}</div>`:''}
    <h4>검토사항 <span class="mut">(AI가 원문에서 추출·정리한 실무 체크포인트${items.length>1?' — 상이처별':''})</span></h4>
    ${items.length>1
      ? items.map((it,i)=>{const xs=i===0?(g.review_items||[]):(it.review_items||[]); return xs.length?`
        <div style="font-size:13px;font-weight:700;color:var(--ink);margin:10px 0 4px">[${esc(it.injury)}]</div>
        ${xs.map(t=>`<div style="font-size:13px;line-height:22px;color:var(--slate-700);margin-bottom:6px">○ ${esc(t)}</div>`).join('')}`:'';}).join('')
      : (g.review_items||[]).map(t=>`<div style="font-size:13px;line-height:22px;color:var(--slate-700);margin-bottom:6px">○ ${esc(t)}</div>`).join('')}
    <h4>비고</h4>
    ${items.length>1
      ? items.map((it,i)=>{const xs=i===0?(g.note_items||[]):(it.note_items||[]); return xs.length?`
        <div style="font-size:13px;font-weight:700;color:var(--ink);margin:10px 0 4px">[${esc(it.injury)}]</div>
        ${xs.map(t=>`<div style="font-size:13px;line-height:22px;color:var(--muted-fg);margin-bottom:6px">◇ ${esc(t)}</div>`).join('')}`:'';}).join('')
      : (g.note_items||[]).map(t=>`<div style="font-size:13px;line-height:22px;color:var(--muted-fg);margin-bottom:6px">◇ ${esc(t)}</div>`).join('')}`;
}
function gradePredictBody(g){
  const r = gv.pred;
  let left = '';
  if(gv.predLoading) left = `<div class="mutetxt" style="flex:1;min-width:280px"><span class="loading">신체검사 관련 규정 확인 중</span></div>`;
  else if(r && r.error) left = `<div style="flex:1;min-width:280px" class="pm err">⚠ ${esc(r.error)}</div>`;
  else if(r) left = `<div style="flex:1;min-width:320px">
    <h4 style="margin-top:0">예측결과</h4>
    <div class="tblcard" style="margin-bottom:14px"><table class="ds" style="min-width:0"><thead><tr>
      <th>부위명</th><th>1순위 등급</th><th>2순위 등급</th></tr></thead>
      <tbody><tr class="${gv.predSel===0?'sel':''}" onclick="gv.predSel=0;renderGradeDetail()">
        <td>${esc(gv.part||'—')}</td><td class="ink">${esc(r.grade1)}</td><td>${dash(r.grade2)}</td></tr></tbody></table></div>
    <div class="card soft"><div style="font-size:12px;font-weight:600;color:var(--green-600);margin-bottom:4px">판정 예측결과 · ${esc(r.grade1)}</div>
      <div style="font-size:13px;line-height:22px">${esc(r.rationale||'(별표3 기준 미적재 — 사례 기반 참고치)')}</div></div>
    <p class="mutetxt">${esc(r.note||'')}</p></div>`;
  const showDetail = r && !r.error && gv.predSel!==null;
  let right = '';
  if(showDetail){
    const crit = (r.criteria&&r.criteria.length) ? `<h4 style="margin-top:0">적용 기준 · 시행령 [별표3] 상이등급구분표</h4>
      ${r.criteria.map((c,i)=>`<div class="card" style="display:flex;gap:12px;align-items:baseline">
        <span class="res ${i===0?'yes':'hold'}" style="white-space:nowrap">${esc(c.grade)} ${esc(c.class_no)}호</span>
        <span style="flex:1">${esc(c.description)}</span>
        <span class="mutetxt mono" style="white-space:nowrap">부합도 ${c.similarity}</span></div>`).join('')}` : '';
    const sim = (r.similar&&r.similar.length) ? `
      <h4>유사 상이등급 관련 정보 · 유사 상이등급 조회 <span class="mut">(참고용)</span></h4>
      <div class="tblcard"><table class="ds" style="min-width:0"><thead><tr>
        <th>접수번호</th><th>회의일자</th><th>상병명</th><th>상이등급</th><th style="width:90px"></th></tr></thead>
        <tbody>${r.similar.map((c,i)=>`<tr style="cursor:default">
          <td class="mono">${esc(c.recv_no)}${c.pick==='pin'?' <span class="realtag" style="color:#7c3aed;background:#f5f3ff;border-color:#ddd6fe">★</span>':''}</td><td class="mono">${esc(c.meeting_date)}</td>
          <td>${esc(c.disease_name)}</td><td class="ink">${esc(c.grade)}</td>
          <td style="white-space:nowrap"><button class="backlink" style="margin:0;text-decoration:underline" onclick="gv.modal=${i};renderGradeDetail()">상세보기</button></td></tr>`).join('')}
        </tbody></table></div>` : '';
    right = `<div style="flex:1;min-width:280px">${crit}${sim}</div>`;
  }
  return `<div style="display:flex;gap:24px;margin-bottom:20px">${left}${right}</div>`;
}
async function runGradePredict(){
  const name = (gv.diseaseInput||'').trim();
  if(!name){ alert('상병명을 입력하세요.'); return; }
  gv.predLoading = true; gv.pred = null; renderGradeDetail();
  try{
    const res = await fetch('/grade-predict',{method:'POST',headers:{'Content-Type':'application/json'},
      body: JSON.stringify({disease_name:name, body_part:gv.part, n:5})});
    gv.pred = res.ok ? await res.json() : {error:`서버 오류(${res.status})`};
    if(gv.pred.grade1===null && gv.pred.note) gv.pred = {error: gv.pred.note};
  }catch(e){ gv.pred = {error:'요청 실패 — API 서버 상태를 확인하세요'}; }
  gv.predSel = 0;
  gv.predLoading = false; renderGradeDetail();
  if(gv.pred && !gv.pred.error && gv.ga){
    logGradeEvent(gv.ga.ga_id, 'AI예측', 'AI 등급예측 실행', 'AI',
      `${esc(name)} → ${esc(gv.pred.grade1||'')}`, null, true);
  }
}
function gradeModal(){
  if(gv.modal===null || !gv.pred || !gv.pred.similar) return '';
  const c = gv.pred.similar[gv.modal]; if(!c) return '';
  return `<div class="gmodal-ov" onclick="if(event.target===this){gv.modal=null;renderGradeDetail()}">
    <div class="gmodal">
      <div class="mh"><span>선택 사례 상세 보기</span>
        <button class="backlink" style="margin:0" onclick="gv.modal=null;renderGradeDetail()">${icon('IconX',18,'color:var(--slate-400)')}</button></div>
      <div class="ml">상병명</div><div class="mv">${esc(c.disease_name)}</div>
      <div style="display:flex;gap:24px;margin-bottom:16px">
        <div><div class="ml">상이등급</div><div class="mv" style="margin:0">${esc(c.grade)}</div></div>
        <div><div class="ml">접수번호</div><div class="mv mono" style="margin:0">${esc(c.recv_no)}</div></div>
      </div>
      <div class="mcap">심의회의 의결 주문</div><p>${esc(c.order_text)}</p>
      <div class="mcap">심의회의 의견</div><p>${esc(c.opinion_text)}</p>
    </div></div>`;
}

/* ── 요건심사(리포트형): 공통뼈대 1~4장 ── */
function showSec(i){
  curSec = i; visitedSecs.add(i);
  SECTIONS.forEach((_,n)=>{ const b=$('step'+n); if(b) b.classList.toggle('on', n===i); });
  [sec1,sec2,sec3,sec4][i]();
  renderCkBar();
  $('paper').scrollTop = 0;
}

function ckSummary(){
  const items = (doc && doc.checklist) || [];
  const done = ckState.filter(Boolean).length, total = items.length || 1;
  const pct = Math.round(done/total*100);
  return `<div class="cksum ${pct===100?'done':''}">
    <div class="cap">${esc(doc?doc.subcommittee_info.name:'')} 체크리스트</div>
    <div class="n">${done} / ${items.length} 완료</div>
    <div class="bar"><div style="width:${pct}%"></div></div></div>`;
}
const AI_ST = {완료:['IconCheckCircle','var(--green-600)',''], 미흡:['IconAlertCircle','var(--amber-600)','warn'], 부족:['IconAlertCircle','var(--red-500)','bad']};
function aiReview(status, text){
  const [ic, col, cls] = AI_ST[status] || AI_ST['완료'];
  return `<div class="aibox ${cls}">${icon(ic,18,`color:${col};margin-top:1px`)}
    <div><div class="h" style="color:${col}">AI 자동검토 · ${status==='완료'?'검토완료':status}</div>
    <div class="b">${esc(text)}</div></div></div>`;
}
let evOpen = {};
function evToggle(id, files){
  if(!files.length) return '';
  return `<div><button class="evtoggle" onclick="toggleEv('${id}')">${icon('IconFileText',13)} 자료보기</button>
    <div class="evlist" id="ev-${id}" style="${evOpen[id]?'':'display:none'}">
      ${files.map(f=>`<div>${icon('IconPaperclip',12,'color:var(--slate-400)')} ${esc(f)}</div>`).join('')}
    </div></div>`;
}
function toggleEv(id){ evOpen[id]=!evOpen[id]; const el=$('ev-'+id); if(el) el.style.display = evOpen[id]?'':'none'; }

function sec1(){
  const s = doc;
  $('paper').innerHTML = `<div class="crumb">1. 신청사항 › 가. 신청경위 · 나. 신청상이</div>
    <dl class="kv" style="margin-bottom:16px">
      <dt>신청인</dt><dd>${esc(s.applicant)}${s.is_real?' <span class="realtag">실데이터</span>':''} (${s.birth_year?s.birth_year+'년생, ':''}${esc(s.duty_type)})</dd>
      <dt>접수번호 / 차수</dt><dd class="mono">${esc(s.recv_no)} / ${s.round}차</dd>
      <dt>심의내용</dt><dd>${esc(s.review_content)}${s.is_death?' <span class="note">(사망 사건)</span>':''}</dd></dl>
    <h4 data-src="1-1 신청서 · 요건발급요청서">가. 신청경위 <span class="mut">(언제·어디서·무엇을·어떻게·왜 · 수정 시 판단문에 반영)</span></h4>
    ${editBlock('apply_story', s.apply_story, null, '1-1 신청서 · 요건발급요청서')}
    ${(s.apply_history&&s.apply_history.length)?`
    <h4>재신청 이력 <span class="mut">(${esc(s.apply_kind)} 사건 — 과거 신청·처분 경과)</span></h4>
    <div class="tblcard" style="margin-bottom:14px"><table class="ds" style="min-width:0"><thead><tr>
      <th style="width:64px">구분</th><th style="width:90px">일자</th><th style="width:90px">신청유형</th><th>내용</th><th style="width:180px">처분 결과</th></tr></thead>
      <tbody>${s.apply_history.map(h=>`<tr>
        <td class="ink">이력${h.seq}</td><td class="mono">${esc(h.date)}</td>
        <td><span class="stepchip">${esc(h.kind)}</span></td>
        <td style="white-space:normal">${esc(h.summary)}</td>
        <td><span class="res ${/해당\(|인정/.test(h.result||'')&&!/비해당/.test(h.result||'')?'yes':'no'}">${esc(h.result||'')}</span></td></tr>`).join('')}
      </tbody></table></div>`:''}
    <h4 data-src="1-1 신청서 (신청인 진술)">현재시점 후유증·합병증</h4>${editBlock('aftermath', s.aftermath, null, '1-1 신청서 (신청인 진술)')}
    ${evToggle('s1', s.disabilities.map(d=>'국가유공자 요건 사실 확인서 —' + d.name))}
    <h4>나. 신청상이</h4>` +
    s.disabilities.map(d=>`<div class="card" data-src="요건심의 의뢰공문 · 부위 불명확 시 전화조사" id="eb_onset_story_${d.dis_id}"><div class="t">${esc(d.name)} <span class="mono">— ${dash(d.body_side)} · KCD ${esc(d.kcd_code)}</span></div>
      <span class="ebval">발병년월 ${esc(d.onset_ym)} · ${esc(d.onset_story)}</span>
      <button class="rowact backlink" style="margin-left:8px" onclick="startEdit('onset_story',${d.dis_id})">✎ 수정</button></div>`).join('') +
    (s.apply_story
      ? aiReview('완료', '신청경위(육하원칙)와 신청상이 기재가 확인됩니다.')
      : aiReview('부족', '신청경위 기재가 없습니다 — 신청서 원문 확인이 필요합니다.'));
}
function sec2(){
  const s = doc, sv = s.service || {};
  $('paper').innerHTML = `<div class="crumb">2. 관련자료 › 병적 · 요건 사실 확인 · 의무기록</div>
    <h4 data-src="1-4 병적증명서 · 병적기록표/인사자력표">가. 병적관련자료</h4>
    <div class="card" data-src="1-4 병적증명서 · 인사자력표"><dl class="kv">
      <dt>입대 / 전역</dt><dd>${dash(sv.enlist_date)} / ${sv.discharge_date?esc(sv.discharge_date):'복무중'}</dd>
      <dt>병과·특기</dt><dd>${dash(sv.branch)}</dd><dt>근무경력</dt><dd>${dash(sv.career)}</dd>
      <dt>휴가내역</dt><dd>${dash(sv.leave_note)}</dd>
      ${sv.overtime?`<dt>초과근무·특별업무</dt><dd>${esc(sv.overtime)}</dd>`:''}</dl></div>
      ${evToggle('s2', ['병적증명서·인사자력표'].concat(s.disabilities.flatMap(d=>d.medical.map(m=>m.hospital + ' ' + m.rec_type + ' (' + (m.rec_date || '-') + ')'))))}
    <h4 data-src="1-4 국가유공자 요건 사실 확인서 (소속기관 통보)">나. 국가유공자 요건 사실 확인서</h4>` +
    s.disabilities.map(d=>`<div class="card" data-src="1-4 요건사실확인서" id="fact_${d.dis_id}"><div class="t">${esc(d.name)}</div>
      <span class="ebval">상이연월일 ${dash(d.fact_date)} · 상이장소 ${dash(d.fact_place)} · 최초부상명 ${dash(d.fact_first_dx)}</span>
      <button class="rowact backlink" style="margin-left:8px" onclick="startFactEdit(${d.dis_id})">✎ 수정</button></div>`).join('') +
    `<h4 data-src="1-3 의무기록사본증명서 · 2-1-1 전체 의무기록">다. 의무기록 <span class="mut">(시간순 · 음영 = 중요문서)</span></h4>` +
    s.disabilities.map(d=>`<div class="card"><div class="t">${esc(d.name)}</div>
      <table class="med"><thead><tr><th>일자</th><th>시기</th><th>기관</th><th>구분</th><th>진단·소견</th><th>급성/진구성</th></tr></thead>
      <tbody>${d.medical.map(m=>{
        const imp = ['영상','수술'].includes(m.rec_type);
        return `<tr class="${imp?'imp':''}"><td class="mono">${m.rec_date||''}</td><td>${dash(m.period)}</td>
          <td>${esc(m.hospital)}</td><td>${imp?'<b>'+esc(m.rec_type)+'</b>':esc(m.rec_type)}${m.by_applicant==='Y'?' <span class="note">신청인 제출</span>':''}</td>
          <td>${esc(m.diagnosis||m.chief||'')}${m.imaging?` — <b>${esc(m.imaging)}</b>: ${esc(m.finding)}`:m.finding?' — '+esc(m.finding):''}${m.surgery?' / 수술: '+esc(m.surgery):''}</td>
          <td>${m.chronic==='Y'?'<span class="res no">진구성</span>':m.chronic==='N'?'<span class="res yes">급성</span>':'—'}</td></tr>`;
      }).join('')}</tbody></table></div>`).join('') +
    (function(){ const n = s.disabilities.reduce((a,d)=>a+d.medical.length,0);
      return n ? aiReview('완료', `의무기록 ${n}건을 시간순으로 대조 완료했습니다.`)
               : aiReview('부족', '의무기록이 없습니다 — 진료내역 보완이 필요합니다.'); })();
}
function sec3(){
  const s = doc;
  $('paper').innerHTML = `<div class="crumb">3. 관계법령·판단 › 법령 · 유사사례 · 분과 판단기준</div>
    <h4>가. 관련법령 <span class="mut">(신분 기준 자동 결정 · 원문 발췌 RAG)</span></h4>` +
    s.laws.map(l=>`<div class="card"><div class="t mono" style="font-size:12px">${esc(l.clause)}</div>
      ${l.passage?esc(l.passage):'<span class="mutetxt">원문 미적재 — scripts/ingest_laws.py 실행 필요</span>'}</div>`).join('') +
      evToggle('s3', s.laws.map(l=>l.clause).concat([s.subcommittee_info.name + ' 심사 매뉴얼'])) +
    `<h4>나. 본 건 판단의 전제 — 판례 <span class="mut">(법원 판례 RAG)</span></h4>` +
    (s.disabilities.some(d=>(d.precedents||[]).length)
      ? s.disabilities.map(d=>(d.precedents||[]).map(p=>`<div class="card soft" style="padding:10px 14px">
          <span class="ink" style="font-weight:600">${esc(d.name)}</span> — ${esc(p.content).slice(0,180)}…
          <span class="mono" style="font-size:11px;color:var(--slate-400)">(${esc(p.source||'판례')})</span></div>`).join('')).join('')
      : '<div class="mutetxt" style="margin:4px 0 12px">판례 미적재 — 공개 판례 수집 후 scripts/ingest_precedents.py 실행 시 자동 표시</div>') +
    `<h4>나. 본 건 판단의 전제 — 유사사례 <span class="mut">(선별 결과가 AI 사전판단·판단문 생성에 반영)</span></h4>` +
    s.disabilities.map(d=>`
      <div style="display:flex;align-items:center;gap:8px;font-weight:700;font-size:13px;margin:8px 0 4px">${esc(d.name)}
        <button class="btn outline sm" onclick="openS3Modal(${d.dis_id})">＋ 사례 추가</button></div>
      ${d.similar.length?d.similar.map(x=>`<div class="card" style="display:flex;gap:10px;align-items:baseline;padding:8px 14px">
        <span class="res ${x.decision==='해당'?'yes':'no'}">${esc(x.decision||'대기')}</span>
        ${x.pick==='pin'?'<span class="realtag" style="color:#7c3aed;background:#f5f3ff;border-color:#ddd6fe">★</span>':''}
        <span style="flex:1">${x.summary?esc(String(x.summary)).slice(0,110):`과거사례 ${esc(String(x.case_id))}`}
          <span class="mono" style="font-size:11px;color:var(--slate-400)"> ${(x.matched_codes||x.kcd_codes||[]).map(esc).join(', ')}</span>
          ${x.pick_note?`<span class="mut" style="font-size:11px"> · ${esc(x.pick_note)}</span>`:''}</span>
        <button class="backlink rowact" style="margin:0" onclick="pickSimilar(${d.dis_id},${x.case_id},'${x.pick==='pin'?'clear':'pin'}')">${x.pick==='pin'?'해제':'★고정'}</button>
        <button class="backlink rowact" style="margin:0 0 0 6px;color:#e11d48" onclick="pickSimilar(${d.dis_id},${x.case_id},'exclude')">제외</button></div>`).join('')
        :'<div class="mutetxt" style="margin:4px 0">유사사례 없음 — [사례 추가]로 직접 검색해 넣을 수 있습니다</div>'}`).join('') +
    `<h4>다. 의학정보·분과 판단기준 <span class="mut">(${esc(s.subcommittee_info.name)} 매뉴얼 RAG)</span></h4>` +
    s.disabilities.map(d=>d.criteria.map(c=>`<div class="card soft"><div class="t">${esc(d.name)}</div>
      ${esc(c.content).slice(0,320)}… <span class="mono" style="font-size:11px;color:var(--slate-400)">(${esc(c.source)})</span></div>`).join('')).join('') +
    (function(){
      if(s.laws.some(l=>!l.passage)) return aiReview('미흡','관계법령 원문이 미적재된 조항이 있습니다 — scripts/ingest_laws.py 실행이 필요합니다.');
      const nc = s.disabilities.reduce((a,d)=>a+d.similar.filter(x=>x.decision==='비해당').length,0);
      if(nc) return aiReview('미흡',`동일 상이처 과거 비해당 판정 ${nc}건이 있습니다 — 본 건과의 사실관계 차이 검토가 필요합니다.`);
      return aiReview('완료','관계법령 조항과 분과 판단기준 대조가 완료되었습니다.');
    })() + s3ModalHtml();
}

/* ── 항목 인라인 편집 (텍스트박스) — 수정 즉시 반영 + 교정쌍 축적(field_edit) ── */
function editBlock(field, value, disId, src){
  return `<div class="card" ${src?`data-src="${esc(src)}"`:''} id="eb_${field}_${disId||0}">
    <span class="ebval" style="white-space:pre-wrap">${esc(value||'—')}</span>
    <button class="rowact backlink" style="margin-left:8px" onclick="startEdit('${field}',${disId||'null'})">✎ 수정</button></div>`;
}
function getField(field, disId){
  if(disId){ const d=doc.disabilities.find(x=>x.dis_id===disId); return (d&&d[field])||''; }
  return doc[field]||'';
}
function startEdit(field, disId){
  const el = $(`eb_${field}_${disId||0}`); if(!el) return;
  el.innerHTML = `<textarea id="ta_${field}_${disId||0}" style="width:100%;min-height:80px;padding:8px 10px;border:1px solid var(--border-strong);border-radius:6px;font-size:13px;line-height:20px;font-family:inherit">${esc(getField(field,disId))}</textarea>
    <div style="display:flex;gap:6px;justify-content:flex-end;margin-top:6px">
      <button class="btn outline sm" onclick="showSec(curSec)">취소</button>
      <button class="btn primary sm" onclick="saveEdit('${field}',${disId||'null'})">저장</button></div>`;
  $(`ta_${field}_${disId||0}`).focus();
}
async function saveEdit(field, disId){
  const v = ($(`ta_${field}_${disId||0}`)?.value||'').trim();
  await fetch(`/cases/${doc.app_id}/field`,{method:'POST',headers:{'Content-Type':'application/json'},
    body: JSON.stringify({field, value:v, dis_id:disId})});
  logEvent('담당자', `항목 수정(${field}) — 교정쌍 축적, 이후 판단문·정규화에 반영`);
  doc = await (await fetch(`/decision-doc/${doc.app_id}`)).json();
  showSec(curSec);
}
function startFactEdit(disId){
  const el = $(`fact_${disId}`); if(!el) return;
  const d = doc.disabilities.find(x=>x.dis_id===disId)||{};
  const inp = (f,v,ph)=>`<input id="fi_${f}_${disId}" value="${esc(v||'')}" placeholder="${ph}" style="flex:1;padding:6px 10px;border:1px solid var(--border-strong);border-radius:6px;font-size:12px">`;
  el.innerHTML = `<div class="t">${esc(d.name||'')}</div>
    <div style="display:flex;gap:6px;margin:6px 0">
      ${inp('fact_date', d.fact_date, '상이연월일')}${inp('fact_place', d.fact_place, '상이장소')}${inp('fact_first_dx', d.fact_first_dx, '최초부상명')}</div>
    <div style="display:flex;gap:6px;justify-content:flex-end">
      <button class="btn outline sm" onclick="showSec(curSec)">취소</button>
      <button class="btn primary sm" onclick="saveFactEdit(${disId})">저장</button></div>`;
}
async function saveFactEdit(disId){
  for(const f of ['fact_date','fact_place','fact_first_dx']){
    const v = ($(`fi_${f}_${disId}`)?.value||'').trim();
    await fetch(`/cases/${doc.app_id}/field`,{method:'POST',headers:{'Content-Type':'application/json'},
      body: JSON.stringify({field:f, value:v, dis_id:disId})});
  }
  logEvent('담당자', `요건사실확인서 수정(상이처 ${disId}) — 교정쌍 축적`);
  doc = await (await fetch(`/decision-doc/${doc.app_id}`)).json();
  showSec(curSec);
}

/* ── 3장 유사사례 '사례 추가' 팝업 — 기본은 인라인 리스트, 추가 버튼 시에만 ── */
let s3Modal = null, s3LastQ = '';   // null | dis_id (사례 추가 검색 팝업)
function openS3Modal(disId){ s3Modal = disId; showSec(curSec); }
function closeS3Modal(){ s3Modal = null; s3LastQ = ''; showSec(curSec); }
function s3ModalHtml(){
  if(!s3Modal || !doc) return '';
  const d = doc.disabilities.find(x=>x.dis_id===s3Modal);
  if(!d) return '';
  return `<div class="gmodal-ov" onclick="if(event.target===this)closeS3Modal()">
    <div class="gmodal" style="width:760px">
      <div class="mh"><span>사례 추가 — ${esc(d.name)} <span class="mut" style="font-size:12px;font-weight:400">(추가한 사례는 ★고정으로 목록 상단·판단문 생성에 반영)</span></span>
        <button class="backlink" style="margin:0" onclick="closeS3Modal()">${icon('IconX',18,'color:var(--slate-400)')}</button></div>
      <div style="display:flex;gap:6px;margin-bottom:10px">
        <input id="simq${d.dis_id}" value="${esc(s3LastQ)}" placeholder="요약문 키워드 또는 KCD 코드로 검색" style="flex:1;padding:8px 12px;border:1px solid var(--border-strong);border-radius:6px;font-size:13px" onkeydown="if(event.key==='Enter')searchSimilar(${d.dis_id})">
        <button class="btn primary sm" onclick="searchSimilar(${d.dis_id})">검색</button></div>
      <div id="simres${d.dis_id}"><div class="mutetxt">검색어를 입력하세요 — 과거사례 풀에서 찾아 ★추가할 수 있습니다.</div></div>
      <div style="margin-top:10px;text-align:right"><button class="backlink" style="margin:0;color:#e11d48" onclick="resetPicks(${d.dis_id})">이 상이처의 선별 전체 초기화</button></div>
    </div></div>`;
}

/* ── 유사사례 선별 (260721 회의 ③): 제외/고정/검색추가 → 재조회 반영 ── */
async function pickSimilar(disId, caseId, kind){
  let note = null;
  if(kind==='exclude'){ note = prompt('제외 사유 (감사 추적용, 생략 가능)') || null; }
  await fetch('/similar-picks',{method:'POST',headers:{'Content-Type':'application/json'},
    body: JSON.stringify({scope:'case', app_id:doc.app_id, dis_id:disId, case_id:caseId, kind, note})});
  logEvent('담당자', `유사사례 ${kind==='exclude'?'제외':kind==='pin'?'추가·고정':'선별 해제'} (사례 ${caseId})`);
  doc = await (await fetch(`/decision-doc/${doc.app_id}`)).json();
  showSec(curSec);
  if(s3Modal === disId && s3LastQ) searchSimilar(disId);  // 추가 팝업 유지 시 검색결과 복원
}
async function resetPicks(disId){
  const picks = await (await fetch(`/similar-picks?scope=case&app_id=${doc.app_id}&dis_id=${disId}`)).json();
  for(const p of picks)
    await fetch('/similar-picks',{method:'POST',headers:{'Content-Type':'application/json'},
      body: JSON.stringify({scope:'case', app_id:doc.app_id, dis_id:disId, case_id:p.case_id, kind:'clear'})});
  doc = await (await fetch(`/decision-doc/${doc.app_id}`)).json();
  showSec(curSec);
}
async function searchSimilar(disId){
  const q = ($('simq'+disId)?.value||'').trim(); if(!q) return;
  s3LastQ = q;
  const box = $('simres'+disId);
  box.innerHTML = '<span class="loading mutetxt">사례 검색 중</span>';
  const rows = await (await fetch(`/cases-search?q=${encodeURIComponent(q)}`)).json();
  box.innerHTML = rows.length ? rows.map(c=>`<div class="card" style="display:flex;gap:10px;align-items:baseline">
      <span class="res ${c.decision==='해당'?'yes':'no'}">${esc(c.decision||'')}</span>
      <span style="flex:1">${esc(c.summary).slice(0,120)} <span class="mono" style="font-size:11px;color:var(--slate-400)">${(c.kcd_codes||[]).map(esc).join(', ')}</span></span>
      <button class="btn outline sm" onclick="pickSimilar(${disId},${c.case_id},'pin')">★ 추가</button></div>`).join('')
    : '<div class="mutetxt">검색 결과 없음</div>';
}

function sec4(){
  const s = doc;
  $('paper').innerHTML = `<div class="crumb">4. 종합판단 › 상이처별 이원 판단(국가유공자/보훈보상) 선택 → 판단내용 생성 → 담당자 확정</div>
    <h4>가. 신청경위 요약</h4><div class="card soft">${esc(s.apply_story)}</div>
    ${evToggle('s1', s.disabilities.map(d=>'국가유공자 요건 사실 확인서 — ' + d.name))}
    <div id="ruleCheckBox"></div>
    <h4>나·다. 판단 내용 및 결론</h4>` +
    s.disabilities.map(d=>{
      const c = d.conclusion || {};
      const fixed = c.status === '확정';
      const pred = d.predicted || {};
      // conclusion(담당자 판단)이 있으면 그 값, 없으면 AI 예측값을 기본 선택
      const yeuSel = c.yeu_result || pred.yeu_result || '';
      const boSel = c.bosang_result || pred.bosang_result || '';
      const preFilled = !c.yeu_result && !!pred.yeu_result;   // AI가 미리 채운 상태(담당자 미확정)
      return `<div class="card" id="dis${d.dis_id}">
        <div class="t">${esc(d.name)} <span class="mono">(${dash(d.body_side)} · ${esc(d.kcd_code)})</span>
          ${fixed?`<span class="res yes" style="margin-left:8px">${icon('IconCheck',13)} 확정</span>`:''}</div>
        ${preFilled?`<div class="aibox" style="margin:6px 0 12px"><div><div class="h" style="color:var(--blue)">
          ${icon('IconWand2',15,'color:var(--blue);margin-right:4px')}AI 사전 판단 · 신뢰도 ${esc(pred.confidence||'')}
          <span class="mut" style="font-weight:400"> — 사건 자료·유사사례 기반 추천값을 미리 선택했습니다. 확인 후 수정·확정하세요.</span></div>
          <div class="b" style="margin-top:4px">${(pred.basis||[]).map(x=>`· ${esc(x)}`).join('<br>')}</div></div></div>`:''}
        <div class="axis">
          <fieldset><legend>국가유공자 축 — ${esc(d.yeu_clause)}${preFilled?` <span class="res ${yeuSel==='해당'?'yes':'hold'}" style="font-size:11px">AI: ${esc(yeuSel)}</span>`:''}</legend>
            <label><input type="radio" name="yeu${d.dis_id}" value="해당" ${yeuSel==='해당'?'checked':''} ${fixed?'disabled':''}> 해당</label>
            <label><input type="radio" name="yeu${d.dis_id}" value="비해당" ${yeuSel==='비해당'?'checked':''} ${fixed?'disabled':''}> 비해당</label></fieldset>
          <fieldset><legend>보훈보상 축 — ${esc(d.bosang_clause)}${preFilled?` <span class="res ${boSel==='해당'?'yes':'hold'}" style="font-size:11px">AI: ${esc(boSel)}</span>`:''}</legend>
            <label><input type="radio" name="bo${d.dis_id}" value="해당" ${boSel==='해당'?'checked':''} ${fixed?'disabled':''}> 해당</label>
            <label><input type="radio" name="bo${d.dis_id}" value="비해당" ${boSel==='비해당'?'checked':''} ${fixed?'disabled':''}> 비해당</label></fieldset>
        </div>
        ${fixed?'':`<button class="btn sm" onclick="judge(${d.dis_id})">${icon('IconWand2',13)} 판단내용 생성</button>`}
        <div id="body${d.dis_id}">${c.body_text?`
          <div class="gen" id="gen${d.dis_id}" ${fixed?'':'contenteditable="true"'}>${esc(c.body_text)}</div>
          <p style="margin-top:8px;font-size:13px"><b>결론:</b> ${esc(c.final_text)}</p>
          ${fixed?'':`<button class="btn outline sm" style="margin-top:8px" onclick="finalize(${d.dis_id})">담당자 확정</button>
          <span class="mutetxt"> 문안은 위 상자에서 직접 수정 후 확정하십시오 (HITL)</span>`}`:''}</div>
      </div>`;
    }).join('') +
    (function(){
      const cs = s.disabilities.map(d=>d.conclusion||{});
      if(cs.every(c=>c.status==='확정')){
        const pill = s.disabilities.map(d=>`${d.name}: ${d.conclusion.final_text}`).join(' / ');
        return aiReview('완료','전 상이처의 판단이 담당자 확정되었습니다.') +
          `<div class="concl-pill">${esc(pill).slice(0,160)}</div>`;
      }
      if(cs.some(c=>c.body_text)) return aiReview('미흡','생성된 판단내용 초안이 있으나 담당자 확정 전입니다 — 문안 검토 후 확정하십시오.');
      return aiReview('부족','판단내용이 작성되지 않았습니다 — 이원 판단 선택 후 생성하십시오.');
    })() +
    `<p class="mutetxt" style="margin-top:14px">※ 판단시 고려: ①관계법령 ②내부인정기준 ③유사판례 ④의학정보 ⑤최근 유사 의결서 — 3장에 표시됨</p>`;
  loadRuleCheck(s.app_id);
}

/* 분과 판단기준 자동대조 (정형화틀 v2.4) — 결정적 서류대조·계산, LLM 미사용 */
async function loadRuleCheck(appId){
  const box = $('ruleCheckBox'); if(!box) return;
  try{
    const r = await (await fetch(`/rule-check/${appId}`)).json();
    if(!r.rules || !r.rules.length){ box.innerHTML=''; return; }
    const st = {ok:['충족','yes'], lack:['자료 부족','no'], manual:['담당자 확인','hold']};
    box.innerHTML = `<h4>분과 판단기준 자동대조 <span class="mut">(정형화틀 v2.4 · ${r.rules.length}개 축 — 참고용, 확정은 담당자)</span></h4>
      <div class="tblcard" style="margin-bottom:16px"><table class="ds" style="min-width:680px"><thead><tr>
        <th style="width:150px">판단축</th><th>조건 (v2.4 기준)</th><th style="width:110px">대조 결과</th><th style="width:220px">비고</th></tr></thead><tbody>${
      r.rules.map(x=>`<tr>
        <td class="ink">${esc(x.axis)}</td>
        <td style="white-space:normal;font-size:12px">${esc(x.condition)}</td>
        <td><span class="res ${st[x.status][1]}">${st[x.status][0]}</span></td>
        <td style="white-space:normal;font-size:12px" class="mut">${esc(x.note||'')}</td></tr>`).join('')}</tbody></table></div>`;
  }catch(e){ box.innerHTML=''; }
}

async function judge(disId){
  const yeu = document.querySelector(`input[name=yeu${disId}]:checked`);
  const bo = document.querySelector(`input[name=bo${disId}]:checked`);
  if(!yeu || !bo){ alert('두 축(국가유공자/보훈보상)의 해당 여부를 먼저 선택하세요.'); return; }
  $('body'+disId).innerHTML = '<div class="mutetxt" style="margin-top:8px"><span class="loading">판단내용 생성 중</span> — 사건 자료·분과 기준 주입</div>';
  await fetch(`/decision-doc/${selId}/judge`, {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({dis_id:disId, yeu_result:yeu.value, bosang_result:bo.value})});
  doc = await (await fetch('/decision-doc/'+selId)).json();
  const d = doc.disabilities.find(x=>x.dis_id===disId);
  logEvent('AI 자동생성', `4. 종합판단 - ${d?d.name:disId} 판단내용 초안 생성 (${yeu.value}/${bo.value})`);
  showSec(3);
  document.getElementById('dis'+disId)?.scrollIntoView({behavior:'smooth', block:'center'});
}
async function finalize(disId){
  const before = (doc.disabilities.find(x=>x.dis_id===disId)?.conclusion?.body_text) || '';
  const body = document.getElementById('gen'+disId)?.innerText;
  await fetch(`/decision-doc/${selId}/finalize`, {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({dis_id:disId, body_text:body})});
  doc = await (await fetch('/decision-doc/'+selId)).json();
  const d = doc.disabilities.find(x=>x.dis_id===disId);
  if(body != null && body.trim() !== before.trim())
    logEvent('담당자', `4. 종합판단 - ${d?d.name:disId} 판단문안 수정`);
  logEvent('담당자', `4. 종합판단 - ${d?d.name:disId} 담당자 확정`);
  showSec(3);
}

/* ── 우측 아이콘 레일 + 패널 (BNM-U00-0104~0107) ── */
function setPanel(p){
  panel = (panel === p ? null : p);
  document.querySelectorAll('#rail button').forEach(b=>b.classList.toggle('on', b.id==='rail-'+panel));
  $('rpanel').style.display = panel ? '' : 'none';
  renderPanel();
}
function panelShell(title, ic, body){
  return `<div class="ph">${icon(ic,16)} ${title}</div><div class="pb">${body}</div>`;
}
function renderPanel(){
  const rp = $('rpanel'); if(!rp || !panel) return;
  if(panel==='ai'){     rp.innerHTML = panelShell('AI 검토','IconWand2', aiPanelBody());
                        loadLlmStatus().then(()=>{ if(panel==='ai') rp.innerHTML = panelShell('AI 검토','IconWand2', aiPanelBody()); }); }
  if(panel==='ref')     rp.innerHTML = panelShell('레퍼런스','IconPaperclip', refPanelBody());
  if(panel==='history') rp.innerHTML = panelShell('수정이력','IconHistory', historyPanelBody());
  if(panel==='similar'){ rp.innerHTML = panelShell('유사사례','IconScale', similarPanelBody()); loadSimilarPanel(); }
}

function aiPanelBody(){
  const nMed = doc.disabilities.reduce((a,d)=>a+d.medical.length, 0);
  const nLaw = doc.laws.length;
  const nSim = doc.disabilities.reduce((a,d)=>a+d.similar.length, 0);
  const nConflict = doc.disabilities.reduce((a,d)=>a+d.similar.filter(x=>x.decision==='비해당').length, 0);
  const line = (ok, txt) => `<div class="chk-line">${icon(ok?'IconCheck':'IconAlertCircle',14,
    `color:var(--${ok?'green-600':'red-500'});margin-top:3px`)}<span ${ok?'':'style="color:var(--red-500)"'}>${txt}</span></div>`;
  return line(true, `근거자료(의무기록) ${nMed}건 검토 완료`)
    + line(true, `관계법령 ${nLaw}건 대조 완료`)
    + line(nSim>0, `유사사례 ${nSim}건 조회${nSim?'':' — 일치 사례 없음'}`)
    + (nConflict ? line(false, `과거 판정 중 비해당 ${nConflict}건 — 상충 여부 확인 필요`) : '')
    + aiPanelLog.map(m=>`<div class="pcard" style="cursor:default"><div class="pt">${esc(m.q)}</div>
        ${m.loading ? `<div class="pm"><span class="loading">생성 중</span></div>`
          : `<div class="pm${m.err?' err':''}" style="white-space:pre-wrap;${m.err?'':'color:var(--slate-700)'}">${m.err?'⚠ ':''}${esc(stripMd(m.a))}${
              m.err?` <button class="backlink" style="margin:0 0 0 6px;text-decoration:underline" onclick="retryAiPanel(${aiPanelLog.indexOf(m)})">다시 검토</button>`:''}</div>${srcChips(m.sources)}`}</div>`).join('')
    + (llmStatus && !llmStatus.ok ? `<div class="mutetxt" style="margin-top:10px">${llmChip()}</div>` : '')
    + loadHtml('loadchip-panel')
    + `<div class="pinput"><input id="aipanel-q" placeholder="이 안건에 대해 질의 (예: 판정근거 재검토)"
        onkeydown="if(event.key==='Enter')askAiPanel()">
        <button class="btn sm" style="padding:2px 8px" onclick="askAiPanel()">${icon('IconSend',13)}</button></div>`;
}
function retryAiPanel(i){
  const m = aiPanelLog[i]; if(!m) return;
  aiPanelLog.splice(i,1); askAiPanel(m.q);
}
async function askAiPanel(preset){
  if(aiPanelLog.length && aiPanelLog[aiPanelLog.length-1].loading) return;
  const inp = $('aipanel-q'); const q = (preset || (inp && inp.value) || '').trim(); if(!q) return;
  if(inp) inp.value = '';
  const entry = {q, a:'', loading:true}; aiPanelLog.push(entry); renderPanel();
  setTimeout(pollLoad, 300);
  const ctx = `[안건 ${doc.recv_no} · ${doc.review_content} · ${doc.disabilities.map(d=>d.name+'('+d.kcd_code+')').join(', ')}] `;
  const hist = aiPanelLog.filter(m=>!m.loading && !m.err).slice(-3)
    .flatMap(m=>[{role:'user', text:m.q}, {role:'ai', text:m.a}]);
  const r = await askLLM({question: ctx + q, history: hist, persist: false});  // 안건 질의는 세션 기록 제외
  entry.loading = false;
  if(r.error){ entry.a = r.error; entry.err = true; } else { entry.a = r.answer; entry.sources = r.sources; }
  renderPanel();
}

function refPanelBody(){
  const laws = doc.laws.map((l,i)=>`<div class="refrow" onclick="toggleRef(${i})">${icon('IconFileText',15,'color:var(--slate-400)')} ${esc(l.clause)}</div>
    <div class="refbody" id="ref${i}" style="display:none">${l.passage?esc(l.passage):'원문 미적재 — scripts/ingest_laws.py 실행 필요'}</div>`).join('');
  const crits = doc.disabilities.flatMap(d=>d.criteria.map(c=>
    `<div class="refrow" style="cursor:default">${icon('IconFileText',15,'color:var(--slate-400)')} <span>${esc(c.source)} <span class="mutetxt">(${esc(d.name)})</span></span></div>`)).join('');
  return `<div style="font-size:11px;font-weight:700;color:var(--slate-400);text-transform:uppercase;margin-bottom:6px">관계법령</div>${laws}
    <div style="font-size:11px;font-weight:700;color:var(--slate-400);text-transform:uppercase;margin:14px 0 6px">분과 판단기준</div>${crits||'<div class="mutetxt">없음</div>'}
    <div class="mutetxt" style="margin-top:14px">조항명 클릭 시 원문 발췌를 펼칩니다.</div>`;
}
function toggleRef(i){ const el=$('ref'+i); el.style.display = el.style.display==='none' ? '' : 'none'; }

function historyPanelBody(){
  if(!editLog.length) return '<div class="mutetxt">이력이 없습니다.</div>';
  return editLog.map(it=>`<div class="hitem">
    <div class="who ${it.who==='AI 자동생성'?'ai':''}">${esc(it.who)}</div>
    <div class="when">${it.when}</div><div class="what">${esc(it.what)}</div></div>`).join('');
}

function similarPanelBody(){
  if(similarCache === null) return '<div class="mutetxt"><span class="loading">동일 상이처(KCD) 과거 판정 검색 중</span></div>';
  if(!similarCache.length) return '<div class="mutetxt">유사 사례 없음</div>';
  return similarCache.map(s=>`<div class="pcard" style="cursor:default">
    <div class="pt"><span class="res ${s.decision==='해당'?'yes':s.decision==='비해당'?'no':'hold'}">${esc(s.decision||'대기')}</span>
      <span class="mono" style="font-weight:400;color:var(--slate-400);font-size:11px;margin-left:6px">KCD ${(s.kcd_codes||[]).map(esc).join(',')} · 유사도 ${s.similarity}</span></div>
    <div class="pm">${esc(String(s.summary||'')).slice(0,120)}</div></div>`).join('');
}
async function loadSimilarPanel(){
  if(similarCache !== null) return;
  const kcd = doc.disabilities.map(d=>d.kcd_code).filter(Boolean);
  try{
    similarCache = await (await fetch('/similar-cases',{method:'POST',headers:{'Content-Type':'application/json'},
      body: JSON.stringify({summary: doc.disabilities.map(d=>d.name).join(' '), review_type:'요건심의', kcd_codes:kcd, n:5})})).json();
  }catch(e){ similarCache = []; }
  if(panel==='similar') renderPanel();
}

/* ── 하단 분과 체크리스트 바 — 복사/다운로드 게이팅 (BNM-U00-0108) ── */
function renderCkBar(){
  const bar = $('ckbar');
  const items = doc.checklist || [];
  if(tab!=='report'){ bar.style.display='none'; return; }
  bar.style.display = '';
  const done = ckState.filter(Boolean).length;
  const unread = [0,1,2,3].filter(i=>!visitedSecs.has(i));   // 표시용 (게이트 아님)
  // 다운로드 규칙: ① 분과 체크리스트 전부 체크 + ② 모든 상이처의 판단내용(4장) 생성 완료
  const checklistOk = !items.length || done === items.length;
  const judged = (doc.disabilities||[]).length
    ? doc.disabilities.every(d=>d.conclusion && d.conclusion.body_text)
    : false;
  const all = checklistOk && judged;
  const gateMsg = all ? '' : [checklistOk?null:`체크리스트 ${done}/${items.length}`,
                              judged?null:'판단내용 미생성'].filter(Boolean).join(' · ');
  const dlBtns = `
      <button class="btn outline sm" ${all?'':'disabled'} title="${all?'':gateMsg}" onclick="doCopy()">${icon('IconCopy',13)} 복사</button>
      <button class="btn outline sm" ${all?'':'disabled'} title="${all?'':gateMsg}" onclick="doDownloadTxt()">${icon('IconFileText',13)} TXT</button>
      <button class="btn sm" ${all?'':'disabled'} title="${all?'':gateMsg}" onclick="doDownloadPdf()">${icon('IconDownload',13)} PDF</button>
      ${(doc.disabilities||[]).length>1?`
      <button class="btn outline sm" ${all?'':'disabled'} title="${all?'':gateMsg}" onclick="dlSplit('txt')">상이처별 TXT·ZIP</button>
      <button class="btn outline sm" ${all?'':'disabled'} title="${all?'':gateMsg}" onclick="dlSplit('pdf')">상이처별 PDF·ZIP</button>`:''}`;
  bar.classList.toggle('collapsed', ckCollapsed);
  if(ckCollapsed){
    bar.innerHTML = `
      <span class="cnt-toggle" onclick="ckCollapsed=false;renderCkBar()" title="펼치기">
        <span class="cnt ${all?'ok':''}">${esc(doc.subcommittee_info.name)} 체크리스트 · ${done}/${items.length} 완료</span>
        ${icon('IconChevronUp',16,'color:var(--slate-400)')}</span>
      <div style="display:flex;gap:8px;align-items:center;margin-left:auto">
        ${all?'':`<span class="mutetxt" style="white-space:nowrap">${gateMsg} — 완료 후 다운로드 가능</span>`}${dlBtns}
      </div>`;
    bar.onclick = (e)=>{ if(e.target===bar){ ckCollapsed=false; renderCkBar(); } };
    return;
  }
  bar.onclick = null;
  bar.innerHTML = `
    <span class="cnt-toggle" onclick="ckCollapsed=true;renderCkBar()">
      <span class="cnt ${all?'ok':''}">${esc(doc.subcommittee_info.name)} 체크리스트 · ${done}/${items.length} 완료${unread.length?` · 미열람 ${unread.length}장`:''}</span>
      ${icon('IconChevronDown',16,'color:var(--slate-400)')}</span>
    <div class="items">${items.map((it,i)=>`
      <span class="ckit ${ckState[i]?'on':''}" title="${esc((it.subs||[]).join(' / '))}" onclick="toggleCk(${i})">
        <span class="ckbox">${ckState[i]?icon('IconCheck',11):''}</span>${esc(it.item)}</span>`).join('')}</div>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      ${all?'':`<span class="mutetxt" style="white-space:nowrap">${gateMsg} — 완료 후 다운로드 가능</span>`}${dlBtns}
    </div>`;
}
function toggleCk(i){ ckState[i] = !ckState[i]; renderCkBar(); const el=$('cksum-slot'); if(el) el.innerHTML=ckSummary(); }

function fullText(){
  const s = doc, sv = s.service || {};
  let t = `심 의 의 결 서${s.disabilities.every(d=>d.conclusion?.status==='확정')?'':' (초안)'}\n` +
    `접수번호 ${s.recv_no} / 신청인 ${s.applicant} (${s.duty_type}) / ${s.review_content} ${s.round}차 / 담당 ${s.subcommittee_info.name}\n\n` +
    `1. 신청사항\n가. 신청경위: ${s.apply_story}\n   현재 후유증: ${s.aftermath||'—'}\n` +
    s.disabilities.map(d=>`나. 신청상이: ${d.name} (${d.body_side||'—'}, ${d.kcd_code}, 발병 ${d.onset_ym})`).join('\n') +
    `\n\n2. 관련자료\n가. 병적: 입대 ${sv.enlist_date||'—'} / 전역 ${sv.discharge_date||'복무중'} / ${sv.branch||''} / ${sv.career||''}\n`;
  s.disabilities.forEach(d=>{
    t += `나. 요건사실확인서(${d.name}): ${d.fact_date} ${d.fact_place} ${d.fact_first_dx}\n다. 의무기록:\n`;
    d.medical.forEach(m=>{ t += `   - ${m.rec_date} [${m.period}/${m.rec_type}] ${m.hospital}: ${m.diagnosis||m.chief||''}` +
      (m.finding?` — ${m.finding}`:'') + (m.surgery?` / 수술 ${m.surgery}`:'') + '\n'; });
  });
  t += `\n3. 판단 법령·의학정보·판례\n가. 관련법령: ` + s.laws.map(l=>l.clause).join(', ') + '\n';
  t += `\n4. 종합 판단\n`;
  s.disabilities.forEach(d=>{
    const c = d.conclusion;
    t += c?.body_text ? `[${d.name}]\n${c.body_text}\n결론: ${c.final_text} (${c.status})\n\n`
                      : `[${d.name}] (판단 미작성)\n\n`;
  });
  return t + '※ 본 문서는 AI 지원으로 작성된 심의의결서이며, 담당자 확정 및 보훈심사위원회 의결로 효력이 발생함';
}
function doCopy(){ navigator.clipboard.writeText(fullText()); logEvent('담당자','심의의결서 전문 복사'); }
function doDownloadTxt(){
  logEvent('담당자','심의의결서 TXT 다운로드');
  window.open(`/decision-doc/${selId}/export?fmt=txt`, '_blank');
}
function doDownloadPdf(){
  logEvent('담당자','심의의결서 PDF 다운로드');
  window.open(`/decision-doc/${selId}/export?fmt=pdf`, '_blank');
}
function dlSplit(fmt){              // 상이처별 개별본 일괄 zip
  logEvent('담당자', `상이처별 의결서 ZIP 다운로드 (${fmt.toUpperCase()})`);
  window.open(`/decision-doc/${selId}/export-split?fmt=${fmt}`, '_blank');
}

/* ── AI 챗봇 (BNM-U00-0109) — /chatbot 연동, 세션별 과거기록 (chat_session/chat_message) ── */
function chistHtml(){
  return `<div id="chist">${chatSessions.map(cs=>`
    <div class="chist ${cs.cs_id===chatSessionId?'on':''}" onclick="openChatSession(${cs.cs_id})" title="${esc(cs.title)}">
      <div class="ct">${esc(cs.title)}</div>
      <div class="cmeta">${esc(cs.last_at)} · 문답 ${cs.n_q}건</div>
    </div>`).join('') || '<div class="mutetxt" style="padding:8px 14px;font-size:12px">저장된 대화가 없습니다</div>'}</div>`;
}
async function loadChatSessions(){
  try{ chatSessions = await (await fetch('/chat-sessions')).json(); }catch(e){ chatSessions = []; }
  const el = $('chist'); if(el) el.outerHTML = chistHtml();
}
async function openChatSession(id){
  let msgs;
  try{ msgs = await (await fetch('/chat-sessions/' + id)).json(); }catch(e){ return; }
  chatSessionId = id;
  chatMsgs = msgs.map(m => ({role: m.role === 'user' ? 'user' : 'ai', text: m.content, sources: m.sources || []}));
  renderChat($('workbody'));
}
function newChatSession(){ chatSessionId = null; chatMsgs = []; renderChat($('workbody')); }

function renderChat(wb){
  wb.innerHTML = `<div id="v-chat">
    <div class="csnb">
      <div class="cico">
        <div>${icon('IconHome',16)} 홈</div><div>${icon('IconCompass',16)} 탐색</div><div>${icon('IconHistory',16)} 기록</div>
      </div>
      <button class="cnew" onclick="newChatSession()">＋ 새 대화</button>
      <div class="ccap">지난 대화</div>
      ${chistHtml()}
    </div>
    <div class="cmain">
      <div id="cmsgs">${chatMsgs.length ? chatMsgs.map(renderMsg).join('')
        : `<div id="cempty"><div class="g1">안녕하세요, 심사관님</div><div class="g2">무엇을 도와드릴까요?</div><div id="llmchip-slot">${llmChip()}</div></div>`}</div>
      <div class="loadwrap">${loadHtml('loadchip')}</div>
      <div id="cform"><div class="wrap">
        <input id="chat-q" placeholder="심의 관련 질의를 입력하세요" onkeydown="if(event.key==='Enter'&&!event.isComposing&&event.keyCode!==229)sendChat()">
        <button onclick="sendChat()" title="전송">${icon('IconSend',16)}</button>
      </div></div>
    </div></div>`;
  const box = $('cmsgs'); box.scrollTop = box.scrollHeight;
  $('chat-q').focus();
  loadChatSessions();
  pollLoad();
  loadLlmStatus().then(()=>{ const el=$('llmchip-slot'); if(el) el.innerHTML=llmChip(); });
}
function fmtAI(text){
  /* LLM 답변 경량 포맷: 이스케이프 후 **굵게**, 불릿(*·-)만 변환 - 경로·원시 마크다운 노출 방지 */
  let t = esc(text);
  t = t.replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>');
  t = t.replace(/^[ \t]*[*-][ \t]+/gm, '&bull; ');
  t = t.replace(/^#{1,4}[ \t]+/gm, '');
  return t;
}
function stripMd(text){
  /* 볼드 렌더링이 없는 영역(AI 검토 패널)용: 마크다운 기호를 그냥 제거 */
  return String(text ?? '').replace(/\*\*([^*\n]+)\*\*/g, '$1').replace(/^#{1,4}[ \t]+/gm, '');
}

/* 근거 출처: 칩 클릭 -> 참고한 원문 발췌 모달 (문서명·페이지·해당 구절) */
function srcLabel(sp){
  // source_path 형태: '경로#파일명#업로드논스' 또는 '경로#태그' 또는 순수 경로
  const parts = String(sp).split('#');
  return parts.length > 1 ? parts[1] : parts[0].split(/[\\/]/).pop();
}
let SRC_REG = [];
function srcChips(sources){
  // 출처는 한 줄에 하나씩(불릿 목록) — 항목별로 원문 보기·다운로드 독립 제공
  if(!sources || !sources.length) return '';
  const seen = new Set(), items = [];
  for(const src of sources){
    const sp = String(src.source_path || '');
    const label = srcLabel(sp) + ' p.' + src.page_no;
    if(seen.has(label)) continue;
    seen.add(label);
    const id = SRC_REG.push(src) - 1;
    items.push(`<li><span class="dot" style="background:var(--slate-300);width:5px;height:5px"></span>
      <a class="src click" onclick="showSrc(${id})" title="원문 보기">${esc(label)}</a>
      ${src.doc_id ? `<a class="srcdl" href="/source-doc/${src.doc_id}/file?dl=1" title="원본 다운로드">${icon('IconDownload',12)} 다운로드</a>` : ''}</li>`);
    if(items.length >= 8) break;
  }
  return `<ul class="srclist">${items.join('')}</ul>`;
}
async function showSrc(i){
  const s = SRC_REG[i]; if(!s) return;
  const sp = String(s.source_path || '');
  const label = srcLabel(sp);
  const ov = document.createElement('div');
  ov.className = 'gmodal-ov';
  ov.onclick = e => { if(e.target === ov) ov.remove(); };
  document.body.appendChild(ov);

  // 원본 문서 조회 (doc_id 있으면 원문 뷰어, 없거나 실패 시 발췌 폴백)
  let doc = null;
  if(s.doc_id){
    ov.innerHTML = `<div class="gmodal" style="width:680px"><div class="mutetxt"><span class="loading">원문 불러오는 중</span></div></div>`;
    try{ doc = await (await fetch('/source-doc/' + s.doc_id)).json(); }catch(e){ doc = null; }
  }
  const head = (extra) => `<div class="mh"><span>${icon('IconPaperclip',16)} ${esc(label)}
      <span class="mutetxt" style="font-weight:400">p.${esc(s.page_no)}</span></span>
    <span style="display:flex;gap:8px;align-items:center">${extra||''}
      <button class="backlink" style="margin:0" onclick="this.closest('.gmodal-ov').remove()">${icon('IconX',18,'color:var(--slate-400)')}</button></span></div>`;
  const dlBtn = doc && doc.doc_id
    ? `<a class="btn outline sm" style="text-decoration:none" href="/source-doc/${doc.doc_id}/file?dl=1">${icon('IconDownload',13)} 원본 다운로드</a>` : '';

  if(doc && doc.kind === 'pdf'){
    // PDF: 브라우저 내장 뷰어로 해당 페이지 바로 열기
    const excerpt = String(s.content||'').trim();
    ov.innerHTML = `<div class="gmodal" style="width:880px;max-width:94vw">
      ${head(dlBtn)}
      <div class="mcap">${doc.scan?'스캔 원본':'PDF 원문'} · <b>p.${esc(s.page_no||1)}</b>(으)로 이동됨
        <span class="llm-chip bad" style="font-size:11px;padding:1px 8px;margin-left:6px">스캔 원문 — 마스킹 미적용 (개인정보 접근권한 연계 예정)</span></div>
      ${excerpt?`<div class="srcexcerpt">${icon('IconSearch',13,'color:var(--amber-600);margin-right:4px')}<b>p.${esc(s.page_no||1)}에서 이 구절을 확인하세요</b> <span class="mutetxt">— AI가 참고한 부분 (원문 대조용)</span>
        <p>${esc(excerpt.slice(0,320))}${excerpt.length>320?'…':''}</p></div>`:''}
      <iframe src="/source-doc/${doc.doc_id}/file#page=${s.page_no||1}" style="width:100%;height:${excerpt?'56vh':'66vh'};border:1px solid var(--border);border-radius:var(--radius)"></iframe></div>`;
    return;
  }
  if(doc && doc.kind === 'text'){
    // 텍스트: 전문 표시 + AI가 참고한 구절 하이라이트·스크롤 (개인정보 자동 마스킹 적용본)
    // 발췌는 마스킹 전 원문이므로, 서버와 동일한 마스킹을 적용한 뒤 마스킹본에서 찾는다
    const maskJs = t => String(t)
      .replace(/\b\d{6}[- ]\d{7}\b/g, '******-*******')
      .replace(/\b01\d[- ]?\d{3,4}[- ]?\d{4}\b/g, '01*-****-****')
      .replace(/\b0\d{1,2}-\d{3,4}-\d{4}\b/g, '0**-****-****')
      .replace(/[\w.+-]+@[\w-]+\.[\w.]+/g, '****@****');
    const masked = maskJs(String(s.content||'').trim());
    const needle = masked.slice(0, 60);
    const idx = needle ? doc.text.indexOf(needle) : -1;
    const hlLen = idx >= 0 ? Math.min(masked.length, 800) : 0;
    const body = idx >= 0
      ? esc(doc.text.slice(0, idx)) + `<mark id="src-hl" style="background:#fef08a;border-radius:2px">` + esc(doc.text.slice(idx, idx+hlLen)) + `</mark>` + esc(doc.text.slice(idx+hlLen))
      : esc(doc.text);
    ov.innerHTML = `<div class="gmodal" style="width:760px;max-width:94vw">
      ${head(dlBtn)}
      <div class="mcap">원문 전체 · <span class="llm-chip ok" style="font-size:11px;padding:1px 8px">개인정보 자동 마스킹 적용 (프로토타입)</span>${idx>=0?' · 노란 표시 = AI가 참고한 구절':''}</div>
      <p id="src-body" style="white-space:pre-wrap;max-height:60vh;overflow:auto;border:1px solid var(--border);border-radius:var(--radius);padding:12px 14px;background:var(--slate-50)">${body}</p>
      <div class="mutetxt" style="font-size:11px;word-break:break-all">문서구분: ${esc(doc.doc_type||'-')} · 출처: ${esc(sp)}</div></div>`;
    const hl = document.getElementById('src-hl');
    if(hl) hl.scrollIntoView({block:'center'});
    return;
  }
  // 폴백: 원본 파일이 없으면 저장된 발췌만 표시
  ov.innerHTML = `<div class="gmodal" style="width:640px">
    ${head('')}
    <div class="mcap">AI가 참고한 원문 발췌${doc&&doc.detail?` <span class="note">— ${esc(doc.detail)}</span>`:''}</div>
    <p style="white-space:pre-wrap;max-height:52vh;overflow:auto">${esc(s.content || '(발췌 내용 없음)')}</p>
    <div class="mutetxt" style="font-size:11px;word-break:break-all">출처: ${esc(sp)}</div></div>`;
}

function renderMsg(m){
  if(m.role==='user') return `<div class="msg user"><div class="who">담당자</div><div class="body">${esc(m.text)}</div></div>`;
  if(m.loading) return `<div class="msg"><div class="who">AI</div><div class="body mutetxt"><span class="loading">답변 생성 중</span></div></div>`;
  const idx = chatMsgs.indexOf(m);
  return `<div class="msg"><div class="who">AI</div><div class="body${m.err?' err':''}">${m.err?icon('IconAlertTriangle',14,'color:var(--amber-600);margin-right:4px'):''}${m.err?esc(m.text):fmtAI(m.text)}${
          m.err&&m.retryQ?` <button class="backlink" style="margin:0 0 0 8px;text-decoration:underline" onclick="retryChat(${idx})">다시 시도</button>`:''}</div>
    ${srcChips(m.sources)}</div>`;
}
async function sendChat(preset){
  const inp = $('chat-q');
  const q = (preset || (inp && inp.value) || '').trim(); if(!q) return;
  const hist = chatMsgs.filter(m=>!m.loading).slice(-6)
    .map(m=>({role:m.role, text:m.text}));           // 최근 3왕복 문맥 전달
  chatMsgs.push({role:'user', text:q}, {role:'ai', loading:true, sources:[]});
  renderChat($('workbody'));
  setTimeout(pollLoad, 300);   // 내 질의 포함 현황 갱신
  const r = await askLLM({question:q, history:hist, session_id:chatSessionId});
  chatMsgs[chatMsgs.length-1] = r.error
    ? {role:'ai', text:r.error, err:true, retryQ:q, sources:r.sources}
    : {role:'ai', text:r.answer, sources:r.sources};
  if(!r.error && r.session_id) chatSessionId = r.session_id;   // 첫 질문이면 새 세션 확보
  renderChat($('workbody'));
}
function retryChat(i){
  const q = chatMsgs[i] && chatMsgs[i].retryQ; if(!q) return;
  chatMsgs.splice(i-1, 2);          // 실패한 질문·오류 메시지 제거 후 재전송
  sendChat(q);
}


/* ── T/F 피드백 — 전용 페이지(/feedback.html)로 이동 (구 우측 드로어 대체) ── */
function fbBtnHtml(){ return `${icon('IconMessageSquare',14)} 게시판`; }
function initFbButtons(){ ['fb-btn-status','fb-btn-work'].forEach(id=>{ const b=$(id); if(b) b.innerHTML=fbBtnHtml(); }); }
function openFeedback(){ window.open('/feedback.html', '_blank'); }
initFbButtons();

loadCases();
