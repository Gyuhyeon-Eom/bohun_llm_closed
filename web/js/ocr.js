/* 문서 접수(OCR) 페이지 — 업로드 → 목록 → 상세(하위문서·정규화) → 사건/안건 변환 */
const $ = id => document.getElementById(id);
const esc = s => (s ?? '').toString().replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let docs = [], cur = null;

function toast(msg){ const t=$('toast'); t.textContent=msg; t.classList.add('show');
  clearTimeout(t._h); t._h=setTimeout(()=>t.classList.remove('show'), 2600); }

/* ── 업로드 ─────────────────────────────────────────────── */
$('file').addEventListener('change', e => uploadFiles([...e.target.files]));
const drop = $('drop');
drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('over');
  uploadFiles([...e.dataTransfer.files].filter(f => f.name.toLowerCase().endsWith('.txt'))); });

async function uploadFiles(files){
  if (!files.length) return;
  for (const f of files){
    const row = document.createElement('div');
    row.className = 'uprow'; row.textContent = `${f.name} — 적재 중…`;
    $('upLog').prepend(row);
    try {
      const text = await f.text();
      const r = await (await fetch('/scan-docs/upload', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({filename: f.name, text})})).json();
      if (r.error) { row.className='uprow err'; row.textContent = `${f.name} — ${r.error}`; }
      else { row.className='uprow ok';
        row.innerHTML = `${esc(f.name)} → <b>[${r.sd_id}] ${esc(r.person||'성명 미상')}</b>`
          + ` ${esc(r.disease||'')} · 하위문서 ${r.blocks}건`; }
    } catch (e) { row.className='uprow err'; row.textContent = `${f.name} — 업로드 실패: ${e}`; }
  }
  $('file').value = '';
  await loadList();
}

/* ── 목록 ───────────────────────────────────────────────── */
async function loadList(){
  docs = await (await fetch('/scan-docs')).json();
  $('cnt').textContent = `${docs.length}건`;
  renderList();
}

function normBadge(d){
  const n = d.n_exams||0, m = d.n_norm||0;
  if (!n) return '<span class="badge none">블록 없음</span>';
  if (m >= n) return '<span class="badge norm">정규화 완료</span>';
  if (m > 0)  return `<span class="badge part">정규화 ${m}/${n}</span>`;
  return '<span class="badge none">미정규화</span>';
}

function renderList(){
  const q = ($('q').value||'').trim();
  const rows = docs.filter(d => !q || (d.person||'').includes(q) || (d.file_name||'').includes(q));
  if (!rows.length) { $('listWrap').innerHTML = '<div class="empty">적재된 문서 없음 — 위에서 txt를 업로드하세요</div>'; return; }
  $('listWrap').innerHTML = `<table><thead><tr>
      <th>ID</th><th>성명</th><th>문서종류</th><th>병원</th><th>하위문서</th><th>정규화</th><th>구분</th><th>사건</th><th>파일명</th><th>적재일</th>
    </tr></thead><tbody>` + rows.map(d => `
    <tr class="${cur && cur.sd_id===d.sd_id ? 'sel':''}" onclick="openDetail(${d.sd_id})">
      <td class="mono">${d.sd_id}</td>
      <td><b>${esc(d.person||'—')}</b></td>
      <td>${esc(d.doc_kind||'—')}</td>
      <td>${esc(d.hospital||'—')}</td>
      <td>${d.n_exams||0}건</td>
      <td>${normBadge(d)}</td>
      <td>${d.is_real ? '<span class="badge real">실데이터</span>' : '<span class="badge none">표본</span>'}</td>
      <td>${d.app_id ? '#'+d.app_id : '—'}</td>
      <td class="hint" style="max-width:230px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(d.file_name||'')}</td>
      <td class="hint">${(d.created_at||'').slice(0,10)}</td>
    </tr>`).join('') + '</tbody></table>';
}

/* ── 상세 ───────────────────────────────────────────────── */
async function openDetail(id){
  cur = await (await fetch(`/scan-docs/${id}`)).json();
  if (cur.error) return toast(cur.error);
  renderList();
  $('detailCard').style.display = '';
  $('dTitle').textContent = `[${cur.sd_id}] ${cur.person||'성명 미상'} — ${cur.doc_kind||''}`;
  $('dMeta').innerHTML = [
    cur.hospital && `병원 <b>${esc(cur.hospital)}</b>`,
    cur.reg_no && `등록번호 <b>${esc(cur.reg_no)}</b>`,
    `하위문서 <b>${(cur.exams||[]).length}건</b>`,
    cur.app_id && `연결 사건 <b>#${cur.app_id}</b>`,
    `파일 <b>${esc(cur.file_name||'')}</b>`,
    `<a href="/scan-docs/${cur.sd_id}/clean.pdf" target="_blank">정리본 PDF ↗</a>`,
    `<a href="/scan-docs/${cur.sd_id}/file" target="_blank">원본(raw) ↗</a>`,
  ].filter(Boolean).join(' · ');
  _setPane(null);
  // 다른 문서로 이동 시 이전 정규화 진행바 숨김 (같은 문서 갱신이면 normalize()가 다시 표시)
  const _p = document.getElementById('normProg'); if (_p) _p.style.display = 'none';
  renderBlocks();
  $('detailCard').scrollIntoView({behavior:'smooth', block:'start'});
}

function kv(label, v){ return v ? `<div class="kv"><b>${label}</b>${esc(v)}</div>` : ''; }

function renderBlocks(){
  const blocks = cur.exams||[];
  $('dBlocks').innerHTML = blocks.map((b,i) => {
    const f = b.fields||{}, n = b.norm||null;
    // 판독지(구 PDF 파서) 블록: doc 없이 exam_name/finding 구조
    const title = b.doc || b.exam_name || '검사 블록';
    const src = n ? (n.source==='llm' ? '<span class="badge llm">LLM 정규화</span>'
                                      : '<span class="badge rule">규칙 폴백</span>') : '';
    return `<div class="blk">
      <div class="blk-head"><span class="t">${i+1}. ${esc(title)}</span>
        ${b.line ? `<span class="ln">원문 ${b.line}행~</span>` : (b.page ? `<span class="ln">p.${b.page}</span>`:'')}
        ${src}</div>
      <div class="blk-body">
        ${kv('질병명', n?.disease ?? f.disease)}
        ${kv('등급', n?.grade ?? f.grade)}
        ${kv('신검종류', n?.exam_kind ?? f.exam_kind)}
        ${kv('KCD', f.kcd)}
        ${kv('일자', n?.date ?? f.date ?? b.exam_date)}
        ${kv('소견', n?.opinion ?? f.opinion)}
        ${kv('검사명', b.exam_name)} ${kv('임상진단', b.dx)}
        ${kv('Finding', b.finding)} ${kv('Conclusion', b.conclusion)}
        ${kv('판독의', b.reader)}
        ${n?.summary ? `<div class="kv kf"><b>요약</b>${esc(n.summary)}</div>` : ''}
        ${n?.key_findings?.length ? `<div class="kv kf"><b>핵심 근거문장</b><ul>`
            + n.key_findings.map(s=>`<li>${esc(s)}</li>`).join('') + '</ul></div>' : ''}
        ${b.excerpt ? `<div class="excerpt">${esc(b.excerpt.slice(0,260))}…</div>` : ''}
      </div></div>`;
  }).join('') || '<div class="empty">하위문서 블록 없음</div>';
}

/* 정리본(기본)·raw 원문은 같은 패널을 배타적으로 사용 */
let _pane = null;   // 'clean' | 'raw' | null
function _setPane(mode, text){
  const r = $('dRaw');
  _pane = mode;
  if (mode === null) r.style.display = 'none';
  else { r.textContent = text; r.style.display = ''; }
  $('rawBtn').textContent = mode === 'raw' ? '원문 닫기' : '원문(raw)';
  $('cleanBtn').textContent = mode === 'clean' ? '정리본 닫기' : '정리본 보기';
}

function toggleRaw(){
  if (_pane === 'raw') return _setPane(null);
  _setPane('raw', cur.raw_text || '(원문 없음)');
}

async function toggleClean(){
  if (_pane === 'clean') return _setPane(null);
  const d = await (await fetch(`/scan-docs/${cur.sd_id}/clean`)).json();
  if (d.error) return toast(d.error);
  _setPane('clean', JSON.stringify(d, null, 2));
}

/* ── 액션 ───────────────────────────────────────────────── */

/* 정규화 진행바 — detailCard에 지연 생성. LLM 호출은 블록당 수 초씩 걸릴 수 있어
   limit=1 스텝 호출로 "몇 번째 블록 처리 중"을 실시간 표시한다. */
function progEl(){
  let p = $('normProg');
  if (!p){
    p = document.createElement('div'); p.id = 'normProg'; p.className = 'prog';
    p.innerHTML = '<div class="prog-label" id="progLabel"></div><div class="prog-track"><div class="prog-fill" id="progFill"></div></div>';
    $('dBlocks').parentNode.insertBefore(p, $('dBlocks'));
  }
  return p;
}
function setProg(done, total, msg){
  progEl().style.display = '';
  $('progLabel').textContent = msg;
  $('progFill').style.width = total ? `${Math.round(done/total*100)}%` : '0%';
}

async function normalize(force){
  if (!cur) return;
  const nb = $('normBtn'); nb.disabled = true;
  try {
    if (force){
      const c = await (await fetch(`/scan-docs/${cur.sd_id}/normalize-clear`, {method:'POST'})).json();
      if (c.error) return toast(c.error);
    }
    let llm = 0, rule = 0, total = (cur.exams||[]).length, r;
    setProg(0, total, `정규화 시작 — 총 ${total}블록`);
    do {
      r = await (await fetch(`/scan-docs/${cur.sd_id}/normalize?limit=1`, {method:'POST'})).json();
      if (r.error){ toast(r.error); return; }
      total = r.blocks; llm += r.llm; rule += r.rule_fallback;
      const done = total - r.remaining;
      setProg(done, total, `정규화 중 ${done}/${total} 블록 — LLM ${llm} · 규칙 폴백 ${rule}`);
    } while (r.remaining > 0 && (r.llm + r.rule_fallback) > 0);   // 진행 없으면 안전 중단
    toast(`정규화 완료 — LLM ${llm} · 규칙 폴백 ${rule}`);
    await openDetail(cur.sd_id); await loadList();
    // openDetail이 진행바를 숨기므로 완료 상태를 다시 표시 (배지·필드는 이미 갱신됨)
    setProg(total, total, `정규화 완료 — LLM ${llm} · 규칙 폴백 ${rule}${rule && !llm ? ' (LLM 미연결: FabriX/Ollama 설정 시 LLM 정제)' : ''}`);
  } finally { nb.disabled = false; }
}

async function indexClean(){
  if (!cur) return;
  $('idxBtn').disabled = true; toast('정리본을 챗봇 검색(RAG)에 적재 중…');
  try {
    const r = await (await fetch(`/scan-docs/${cur.sd_id}/index-clean`, {method:'POST'})).json();
    if (r.error) return toast(r.error);
    toast(r.skipped ? '이미 최신 정리본이 적재되어 있습니다'
                    : `적재 완료 — ${r.chunks}청크 (${r.label})${r.replaced ? ' · 구버전 교체' : ''}`);
  } finally { $('idxBtn').disabled = false; }
}

async function toCase(){
  if (!cur) return;
  const r = await (await fetch(`/scan-docs/${cur.sd_id}/to-case`, {method:'POST'})).json();
  if (r.error) return toast(r.error);
  toast(`요건심사 사건 변환 완료 — 사건 #${r.app_id ?? r.id ?? ''}`);
  await openDetail(cur.sd_id); await loadList();
}

async function toGrade(){
  if (!cur) return;
  const r = await (await fetch(`/scan-docs/${cur.sd_id}/to-grade`, {method:'POST'})).json();
  if (r.error) return toast(r.error);
  toast(`상이등급 안건 변환 완료 — 안건 #${r.ga_id ?? r.id ?? ''}`);
  await openDetail(cur.sd_id); await loadList();
}

async function toGradeAll(){
  const b = $('allBtn'); b.disabled = true; b.textContent = '일괄 변환 중…';
  try {
    const r = await (await fetch('/scan-docs/to-grade-all', {method:'POST'})).json();
    if (r.error) return toast(r.error);
    toast(`일괄 변환 완료 — 성공 ${r.converted}건, 건너뜀 ${r.skipped}건`);
    if (r.errors?.length) console.warn('변환 건너뜀:', r.errors);
    await loadList(); if (cur) await openDetail(cur.sd_id);
  } finally { b.disabled = false; b.textContent = '미변환 전체 → 상이등급 안건 일괄 변환'; }
}

loadList();
