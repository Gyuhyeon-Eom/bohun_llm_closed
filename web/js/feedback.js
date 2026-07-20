/* ============================================================
   feedback.js — 화면설계 피드백 전용 페이지 로직

   구성:
     1. 정적 데이터        의견유형·부서·화면·영역·체크리스트 서식
     2. 등록 폼            연동 셀렉트, 항목추가 제안(삽입위치), 임시저장
     3. 등록된 의견        GET/POST /board, 필터(유형·내 의견), 공감
     4. 확인 필요사항(Q&A) 부서별 답변 등록, 필터(미답변·우리 분과)

   API: /board 계열 (api/main.py) — board.html(수행사 목업)과 동일 데이터 공유
   ============================================================ */
const API = '';
const $ = id => document.getElementById(id);
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));

/* ── 1. 정적 데이터 ── */
const VTYPES = [
  ['proc','var(--amber-600)','업무절차 불일치','실제 심사 절차·지침과 화면 흐름이 다름'],
  ['item','var(--blue-600)','항목(영역) 추가 제안','체크리스트·서식·입력폼에 항목 추가 필요'],
  ['func','var(--cyan-700)','기능 추가 요청','화면에 없는 필요 기능'],
  ['improve','var(--green-600)','개선 제안','기존 기능의 사용성·표시 방식 개선'],
  ['ai','#7c3aed','AI 산출물 관련','AI 검토·요약·판단문안·유사사례의 정확성'],
  ['term','var(--slate-500)','용어·표기 오류','법령 용어, 서식 명칭, 오탈자'],
  ['qna','var(--slate-400)','단순 질문','화면·기능에 대한 문의'],
];
const DEPTS = {
  '보훈심사위원회': ['심사1과','심사2과','심사3과','심사4과','행정지원'],
  '국가보훈부': ['심사기준과','정보화담당관','등록관리과','보상정책과'],
  '지방보훈청': ['보상과','등록관리과'],
};
const SCREENS = {
  '심사등록': ['안건현황','안건 등록','문서 매핑'],
  '요건심사': ['1. 신청사항','2. 관련자료','3. 관계법령·판단','4. 종합판단','분과별 체크리스트','AI 검토 패널'],
  '패스트 트랙': ['대상 확인·선별','구비서류 확인','우선처리 현황','검토서 작성(패스트트랙)'],
  '상이판정': ['신체검사 결과 입력','등급 후보 매칭','종합등급 산정','판정 확정'],
  'AI 챗봇': ['챗봇 대화','근거 표시'],
  '문서 접수(OCR)': ['업로드','인식 결과 검수'],
  '공통·기타': ['로그인·권한','전체 레이아웃','기타'],
};
const AREAS = {
  '안건현황': ['목록 컬럼 구성(접수번호·상이처 등)','성명·접수번호 검색','AI 심사 버튼','상태 표시(접수·심사중·의결)'],
  '1. 신청사항': ['신청인·접수번호 기본정보','가. 신청경위','현재시점 후유증·합병증','나. 신청상이','AI 자동검토 표시'],
  '2. 관련자료': ['가. 병적관련자료','나. 국가유공자 요건 사실 확인서','다. 의무기록(시간순 표)','신청서류 보완 체크리스트'],
  '3. 관계법령·판단': ['가. 관련법령(원문 발췌 RAG)','나. 유사사례','다. 의학정보·분과 판단기준'],
  '4. 종합판단': ['가. 신청경위 요약','나·다. 판단 내용 및 결론','해당/비해당 선택(국가유공자·보훈보상 축)','결론 문안'],
  '분과별 체크리스트': ['체크 항목 구성','완료·미열람 표시','복사하기·다운로드'],
  'AI 검토 패널': ['근거자료 검토 완료 표시','관계법령 대조 표시','유사사례 조회 결과','안건 질의 입력'],
  '대상 확인·선별': ['신청 대상·요건 표시','패스트트랙 대상 표시(안건현황 연계)','접수 단계(지방청) 정보'],
  '구비서류 확인': ['구비서류 체크리스트(별첨 기준)','자료 보완 요청','첨부 확인 상태 표시'],
  '우선처리 현황': ['접수 즉시 검토 알림','처리 기한·경과 표시','이후 단계 안내'],
  '검토서 작성(패스트트랙)': ['「관련자료」 항목 작성','「판단내용」 항목 작성','분과별 작성 사례 참조(2·3·5분과)'],
  '신체검사 결과 입력': ['상이처별 측정항목 입력폼','OCR 추출 결과 검수'],
  '등급 후보 매칭': ['등급 후보 표시','근거 조문 표시'],
  '종합등급 산정': ['병합·준용 규칙 계산','산정 과정 표시'],
  '판정 확정': ['확정 입력(HITL)','판정 이력'],
};
/* 업로드된 체크리스트 서식의 실제 항목 (T/F 제공 서식 기준) */
const LISTS = {
  docs: { title: '매건별 자료 보완 요청 체크리스트', items: [
    '요건관련사실확인서 발급요청서','요건관련사실확인서','발병경위서','병적증명서','병적기록표',
    '의무기록','영상자료(CT, MRI)','면담일지','훈련일지','추가자료(직접 기재)','의학자문','과거력 여부(건보자료 확인 등)',
  ]},
  chk2: { title: '(공통) 검토서 체크리스트', items: [
    '보훈 심사 대상이 맞는지 여부',
    '기존 등록 또는 등록신청 여부',
    '병적사항 및 경력사항이 제대로 작성되었는지(병역필 또는 퇴임 등 여부 확인)',
    '신청상이 확인',
    '원상병명이 제대로 기입되었는지(확진병명 확인 등)',
    '공적기록상 발병경위 및 시기가 제대로 작성되었는지',
    '상이 발생경위부터 전역 시까지 일련의 과정들이 시계열적으로 맞게 작성되어 있는지',
    '수술적 처치 여부',
    '의학자문 결과 수용여부에 따른 논리전개에 무리는 없는지',
    '해당 및 비해당 검토에 대한 논리적 모순은 없는지',
    '재신청 및 재심의 경우',
    '비슷한 타 사례와 비교하여 형평성에 크게 어긋나지 않는지',
    '오탈자 및 기록의 정확성 여부',
    '해당 법 및 시행령의 기준 적용이 제대로 되었는지',
  ]},
  basic: { title: '안건 기본정보 항목', items: ['상이일','수술일','부위','병명','소속/직급'] },
  etc: { title: '기타 목록 (직접 입력)', items: [] },
};
const ST_CLASS = {'접수':'recv','검토중':'rev','반영예정':'plan','반영완료':'done','반영불가':'rej','협의요청':'consult'};
const POS_LABEL = {need:'필요', noneed:'불필요', review:'검토필요', consult:'협의필요'};

let vtype = 'item', imp = 'm', insertAfter = 4, opFilter = 'all', qaFilter = 'all', boardData = [];

/* ── 2. 등록 폼 ── */
function initTypes(){
  $('typeGrid').innerHTML = VTYPES.map(([v, c, t, d]) =>
    `<label class="type-item${v === vtype ? ' checked' : ''}" onclick="pickType('${v}')">
      <span class="t"><span class="dot" style="background:${c}"></span>${t}</span>
      <span class="d">${d}</span></label>`).join('');
  $('itemProposal').classList.toggle('show', vtype === 'item');
}
function pickType(v){ vtype = v; initTypes(); }
function syncDept(){
  const o = $('org').value;
  $('dept').innerHTML = (DEPTS[o] || []).map(x => `<option>${x}</option>`).join('') || '<option value="">기관 먼저 선택</option>';
  syncAuthor();
}
function syncScreen(){
  const m = $('menu').value;
  $('screen').innerHTML = (SCREENS[m] || []).map(x => `<option>${x}</option>`).join('');
  if (m === '요건심사') $('screen').value = '2. 관련자료';
  syncArea();
}
function syncArea(){
  const sc = $('screen').value;
  $('area').innerHTML = ((AREAS[sc] || []).concat(['항목 추가 필요(현재 항목 외)','화면 전체·기타']))
    .map(x => `<option>${x}</option>`).join('');
}
function renderChecklist(){
  const L = LISTS[$('targetList').value];
  $('clTitle').textContent = `${L.title} · 현재 ${L.items.length}개 항목`;
  const nm = esc($('newItem').value) || '(추가 항목명)';
  if (insertAfter >= L.items.length) insertAfter = Math.max(L.items.length - 1, 0);
  $('clItems').innerHTML = L.items.map((it, i) => {
    const chip = i === insertAfter
      ? `<div class="cl-insert"><span class="new-item-chip">＋ ${nm}</span></div>` : '';
    return `<li${i === insertAfter ? ' class="sel"' : ''} onclick="insertAfter=${i};renderChecklist()">
      <span class="no">${i + 1}</span>${it}<span class="pos">${i === insertAfter ? '▼ 이 뒤에 추가' : ''}</span></li>${chip}`;
  }).join('') || '<li><span class="hint">항목이 없는 목록입니다 — 사유란에 직접 기재해 주세요.</span></li>';
}
document.querySelectorAll('[data-seg=imp] label').forEach(lb => lb.onclick = () => {
  document.querySelectorAll('[data-seg=imp] label').forEach(x => x.classList.remove('on'));
  lb.classList.add('on'); imp = lb.dataset.v;
});
function showFiles(input){
  $('fileChips').innerHTML = [...input.files]
    .map(f => `<span class="fchip-item">📎 ${esc(f.name)}</span>`).join('')
    + (input.files.length ? '<span class="hint" style="align-self:center">— 프로토타입: 파일은 아직 서버에 저장되지 않습니다</span>' : '');
}

/* 작성자 공통 */
function author(){
  return { org: $('org').value, dept: $('dept').value, bunkwa: $('bunkwa').value, writer: $('writer').value.trim() };
}
function syncAuthor(){
  const a = author();
  const label = a.org && a.writer
    ? `${a.org} ${a.dept || ''}${a.bunkwa && a.bunkwa !== '해당 없음' ? ' · ' + a.bunkwa : ''} · ${a.writer}` : null;
  $('onlineOrg').textContent = label || '작성자 미입력';
  document.querySelectorAll('.qa-author').forEach(e => {
    e.textContent = label || '⚠ 상단 ① 작성자 정보를 먼저 입력';
    e.classList.toggle('missing', !label);
  });
}
function checkAuthor(){
  const a = author();
  if (!a.org || !a.writer){
    toast('작성자 정보(소속·성명)를 먼저 입력하세요');
    window.scrollTo({ top: 0, behavior: 'smooth' });
    $(!a.org ? 'org' : 'writer').focus();
    return false;
  }
  return true;
}
function toast(m){
  const t = $('toast'); t.textContent = m; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2200);
}

/* 임시저장 (localStorage) */
const DRAFT_KEY = 'bohun_fb_draft';
const DRAFT_IDS = ['org','dept','bunkwa','writer','menu','screen','area','targetList','newItem','propBasis','propReason','content'];
function saveDraft(){
  const d = { vtype, imp, insertAfter };
  DRAFT_IDS.forEach(id => d[id] = $(id).value);
  localStorage.setItem(DRAFT_KEY, JSON.stringify(d));
  toast('임시저장되었습니다 — 이 브라우저에서 다시 열면 복원됩니다');
}
function loadDraft(){
  let d; try { d = JSON.parse(localStorage.getItem(DRAFT_KEY)); } catch(e){ return; }
  if (!d) return;
  if (d.org){ $('org').value = d.org; syncDept(); }
  if (d.menu){ $('menu').value = d.menu; syncScreen(); }
  ['dept','bunkwa','writer','screen','targetList','newItem','propBasis','propReason','content'].forEach(id => {
    if (d[id] != null) $(id).value = d[id];
  });
  if (d.screen){ syncArea(); if (d.area != null) $('area').value = d.area; }
  vtype = d.vtype || vtype; imp = d.imp || imp; insertAfter = d.insertAfter ?? insertAfter;
  document.querySelectorAll('[data-seg=imp] label').forEach(x => x.classList.toggle('on', x.dataset.v === imp));
  initTypes(); renderChecklist(); syncAuthor();
}

/* 의견 제출 */
async function submitOpinion(e){
  e.preventDefault();
  if (!checkAuthor()) return false;
  const content = $('content').value.trim();
  let proposal = null;
  if (vtype === 'item'){
    const L = LISTS[$('targetList').value];
    proposal = { list: L.title, item: $('newItem').value,
      pos: (L.items[insertAfter] || '(위치)') + ' 뒤',
      basis: $('propBasis').value, reason: $('propReason').value };
    if (!proposal.item.trim()){ toast('추가할 항목명을 입력하세요'); $('newItem').focus(); return false; }
  }
  if (!content && !proposal){ toast('의견 내용을 입력하세요'); $('content').focus(); return false; }
  const body = { ...author(), menu: $('menu').value, screen: $('screen').value, area: $('area').value,
    vtype, importance: imp, content, proposal };
  try {
    const j = await (await fetch(API + '/board', { method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) })).json();
    if (j.error){ toast(j.error); return false; }
    toast('의견이 등록되었습니다');
    $('content').value = ''; $('fileChips').innerHTML = ''; $('file').value = '';
    localStorage.removeItem(DRAFT_KEY);
    await loadBoard();
    document.querySelector('.list-card').scrollIntoView({ behavior: 'smooth' });
  } catch(err){ toast('등록 실패 — API 서버 확인'); }
  return false;
}

/* ── 3. 등록된 의견 ── */
async function loadBoard(){
  try { boardData = await (await fetch(API + '/board')).json(); }
  catch(e){ boardData = []; }
  opPage = 1; qaPage = 1;
  renderOpinions(); renderQA();
}
let opPage = 1;
function renderOpinions(){
  const ops = boardData.filter(x => x.kind === 'opinion');
  const cnt = { done: ops.filter(o => o.status === '반영완료').length,
                rev: ops.filter(o => o.status === '검토중').length,
                con: ops.filter(o => o.status === '협의요청').length };
  $('opCnt').textContent = `전체 ${ops.length}건 · 반영완료 ${cnt.done} · 검토중 ${cnt.rev} · 협의요청 ${cnt.con}`;
  let list = ops;
  if (opFilter === 'mine'){
    const me = $('writer').value.trim().replace(/\s*[\/(].*$/, '');
    list = me ? ops.filter(o => (o.writer || '').includes(me)) : [];
    if (!me){ $('opTableWrap').innerHTML = '<div class="empty">상단 ① 작성자 정보에 성명을 입력하면 내 의견만 볼 수 있습니다.</div>'; $('opPager').innerHTML = ''; return; }
  } else if (opFilter !== 'all'){
    list = ops.filter(o => o.vtype === opFilter);
  }
  const tlabel = { proc:'업무절차', item:'항목 추가', func:'기능 추가', improve:'개선 제안', ai:'AI 산출물', term:'용어·표기', qna:'단순 질문' };
  if (!list.length){ $('opTableWrap').innerHTML = '<div class="empty">해당하는 의견이 없습니다.</div>'; $('opPager').innerHTML = ''; return; }
  const {slice, page, totalPages} = pageSlice(list, opPage, 10);
  opPage = page;
  $('opTableWrap').innerHTML = `<table><thead><tr>
    <th style="width:48px">No</th><th style="width:104px">유형</th><th>의견</th>
    <th style="width:170px">작성</th><th style="width:130px">처리상태</th><th style="width:80px">공감</th></tr></thead><tbody>${
    slice.map(o => `<tr>
      <td>${o.fb_id}</td>
      <td><span class="tag ${esc(o.vtype)}">${tlabel[o.vtype] || '기타'}</span></td>
      <td>${esc(o.content || (o.proposal ? `[항목추가] ${o.proposal.item}` : ''))}
        <span class="loc">${esc([o.menu, o.screen, o.area].filter(Boolean).join(' › '))}</span></td>
      <td>${esc(o.dept || o.org || '')}${o.bunkwa && o.bunkwa !== '해당 없음' ? ' · ' + esc(o.bunkwa) : ''}<br>
        ${esc((o.writer || '').replace(/\(.*\)/, ''))} <span class="hint">${esc(o.created_at)}</span></td>
      <td><span class="st ${ST_CLASS[o.status] || ''}">${esc(o.status || '접수')}</span>${
        o.status_note ? `<br><span class="hint">${esc(o.status_note)}</span>` : ''}</td>
      <td><button type="button" class="like" onclick="like(${o.fb_id}, this)">&#128077; ${o.likes || 0}</button></td>
    </tr>`).join('')}</tbody></table>`;
  renderPager('opPager', page, totalPages, 'gotoOpPage');
}
function gotoOpPage(p){ opPage = p; renderOpinions(); }
async function like(id, btn){
  try {
    const j = await (await fetch(API + `/board/${id}/like`, { method: 'POST' })).json();
    btn.classList.add('on'); btn.innerHTML = '&#128077; ' + j.likes;
  } catch(e){}
}
document.querySelectorAll('#opFilters .chip').forEach(c => c.onclick = () => {
  document.querySelectorAll('#opFilters .chip').forEach(x => x.classList.remove('on'));
  c.classList.add('on'); opFilter = c.dataset.f; opPage = 1; renderOpinions();
});

/* ── 4. 확인 필요사항 (Q&A) ── */
document.querySelectorAll('#qaFilters .chip').forEach(c => c.onclick = () => {
  document.querySelectorAll('#qaFilters .chip').forEach(x => x.classList.remove('on'));
  c.classList.add('on'); qaFilter = c.dataset.f; qaPage = 1; renderQA();
});
let qaPage = 1;
function renderQA(){
  const all = boardData.filter(x => x.kind === 'qa');
  const answers = boardData.filter(x => x.kind === 'answer');
  let qas = all;
  if (qaFilter === 'open') qas = all.filter(q => !answers.some(a => a.parent_id === q.fb_id));
  if (qaFilter === 'mybk'){
    const bk = $('bunkwa').value;
    qas = all.filter(q => !q.target || q.target.includes('전체') || (bk !== '해당 없음' && q.target.includes(bk)));
  }
  $('qaCnt').textContent = `전체 ${all.length}건${qaFilter !== 'all' ? ` · 표시 ${qas.length}건` : ''}`;
  if (!qas.length){ $('qaWrap').innerHTML = '<div class="empty">해당하는 확인 필요사항이 없습니다.</div>'; $('qaPager').innerHTML = ''; return; }
  const {slice, page, totalPages} = pageSlice(qas, qaPage, 5);
  qaPage = page;
  $('qaWrap').innerHTML = slice.map((q, i) => {
    const idx = (page - 1) * 5 + i;
    const ans = answers.filter(a => a.parent_id === q.fb_id);
    const cnt = {}; ans.forEach(a => cnt[a.answer_pos] = (cnt[a.answer_pos] || 0) + 1);
    const stat = Object.entries(cnt).map(([p, n]) =>
      `<span class="ans-chip ${esc(p)}" style="font-size:11.5px;padding:2px 8px">${POS_LABEL[p] || p} ${n}</span>`).join(' ');
    return `<div class="qa-item${idx === 0 && qaFilter === 'all' ? ' open' : ''}">
      <div class="qa-head" onclick="this.parentElement.classList.toggle('open')">
        <span class="qa-badge">Q${idx + 1}</span>
        <div class="qa-title-wrap"><div class="qa-title">${esc(q.content)}</div>
          <div class="qa-meta">${esc([q.menu, q.screen, q.area].filter(Boolean).join(' › ') || q.screen || '')} · 대상: ${esc(q.target || '전체 분과')} · 수행사 ${esc(q.created_at)}</div></div>
        <div class="qa-stat">${stat || '<span class="tag">미답변</span>'}</div>
        <span class="qa-arrow">&#9662;</span></div>
      <div class="qa-body">
        ${q.qa_context ? `<div class="qa-context">배경: ${esc(q.qa_context)}</div>` : ''}
        <div class="qa-answers">${ans.map(a => `<div class="qa-answer">
          <span class="ans-chip ${esc(a.answer_pos)}">${POS_LABEL[a.answer_pos] || ''}</span>
          <div class="ans-body"><b>${esc(a.org || '')} ${esc(a.dept || '')}${
            a.bunkwa && a.bunkwa !== '해당 없음' ? ' · ' + esc(a.bunkwa) : ''} · ${esc((a.writer || '').replace(/\(.*\)/, ''))}</b>
            <span class="hint">${esc(a.created_at)}</span><br>${esc(a.content)}</div>
        </div>`).join('') || '<div class="hint" style="padding:4px">아직 등록된 답변이 없습니다 — 첫 답변을 남겨 주세요.</div>'}</div>
        <div class="qa-form">
          <div class="qa-form-title">우리 부서 답변 등록 <span class="hint">— <b class="qa-author missing">⚠ 상단 ① 작성자 정보를 먼저 입력</b> · 자동 반영</span></div>
          <div class="seg" data-qseg="${q.fb_id}" style="margin-bottom:10px">
            <label class="on" data-v="need">필요</label><label data-v="noneed">불필요</label>
            <label data-v="review">검토필요</label><label data-v="consult">협의필요</label></div>
          <div style="display:flex;gap:10px">
            <textarea id="qaAns${q.fb_id}" style="min-height:62px;flex:1" placeholder="선택 사유나 조건을 적어주세요. (예: 필요하지만 ○○ 조건이 전제되어야 함)"></textarea>
            <button type="button" class="btn" style="align-self:flex-end" onclick="submitAnswer(${q.fb_id})">답변 등록</button></div>
        </div>
      </div></div>`;
  }).join('');
  document.querySelectorAll('[data-qseg] label').forEach(lb => lb.onclick = () => {
    lb.parentElement.querySelectorAll('label').forEach(x => x.classList.remove('on'));
    lb.classList.add('on');
  });
  syncAuthor();
  renderPager('qaPager', page, totalPages, 'gotoQaPage');
}
function gotoQaPage(p){ qaPage = p; renderQA(); }
async function submitAnswer(qid){
  if (!checkAuthor()) return;
  const seg = document.querySelector(`[data-qseg="${qid}"] label.on`);
  const body = { ...author(), answer_pos: seg ? seg.dataset.v : 'review', content: $('qaAns' + qid).value.trim() };
  try {
    const j = await (await fetch(API + `/board/${qid}/answer`, { method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) })).json();
    if (j.error){ toast(j.error); return; }
    toast('답변이 등록되었습니다'); await loadBoard();
  } catch(e){ toast('등록 실패 — API 서버 확인'); }
}

/* ── 초기화 ── */
['org','dept','bunkwa'].forEach(id => $(id).addEventListener('change', syncAuthor));
initTypes(); syncDept(); syncScreen(); renderChecklist(); loadDraft(); syncAuthor(); loadBoard();
