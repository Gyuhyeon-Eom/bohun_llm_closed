/* ============================================================
   intake.js — 구 화면: OCR 접수·질의 (intake.html에서 분리)
   ============================================================ */
const $=id=>document.getElementById(id);
const STEPS=['접수','검증','청킹','임베딩','적재'];

$('file').onchange = e=>{
  const f=e.target.files[0]; if(!f) return;
  f.text().then(t=>{ $('text').value=t; });
};
$('clear').onclick = ()=>{ $('text').value=''; $('file').value=''; };

function ledgerEntry(name){
  const div=document.createElement('div'); div.className='entry';
  div.innerHTML=`<span class="name">${name}</span>
    <div class="steps">${STEPS.map(s=>`<span>${s}</span>`).join('')}</div>
    <div class="stat"></div>`;
  $('entries').prepend(div); return div;
}
function mark(div,n,cls='done'){ div.querySelectorAll('.steps span')
  .forEach((el,i)=>{ if(i<n) el.className=cls; }); }

$('submit').onclick = async ()=>{
  const text=$('text').value.trim();
  if(!text){ $('text').focus(); return; }
  const name=$('file').files[0]?.name || '붙여넣기 '+new Date().toLocaleTimeString('ko-KR');
  const div=ledgerEntry(name); mark(div,1);
  $('submit').disabled=true;
  try{
    const r=await fetch('/ingest',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text, filename:name, low_quality:$('lowq').checked})});
    if(!r.ok) throw new Error(await r.text());
    const d=await r.json(); mark(div,5);
    div.querySelector('.stat').innerHTML=
      `블록 ${d.blocks} · 교정 <b>${d.corrected}</b> · 청크 <b>${d.chunks}</b> · ${d.seconds}초`;
  }catch(e){
    div.querySelectorAll('.steps span').forEach(el=>{if(!el.className)el.className='fail';});
    div.querySelector('.stat').textContent='실패: '+e.message;
  }
  $('submit').disabled=false;
};

$('ask').onsubmit = async e=>{
  e.preventDefault();
  const q=$('qbox').value.trim(); if(!q) return;
  $('qbox').value='';
  const log=$('chatlog'); log.querySelector('.empty')?.remove();
  const qd=document.createElement('div'); qd.className='q'; qd.textContent=q; log.appendChild(qd);
  const ad=document.createElement('div'); ad.className='a'; ad.textContent='검색 중…'; log.appendChild(ad);
  try{
    const r=await fetch('/chatbot',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({question:q, only_uploaded:$('onlyup').checked})});
    const d=await r.json();
    const mock=(d.answer||'').startsWith('[MOCK');
    ad.innerHTML = mock
      ? `근거 구절 ${d.sources.length}건을 찾았습니다.<div class="note">생성 LLM 미연결 상태 — FabriX 연결 시 아래 근거로 답변이 작성됩니다.</div>`
      : d.answer.replace(/</g,'&lt;');
    d.sources.forEach(s=>{
      const el=document.createElement('div'); el.className='src';
      el.innerHTML=`<div class="meta">${s.source_path.split('/').pop().split('#').slice(1,2)||s.source_path.split('/').pop()} · p.${s.page_no} · <span class="score">score ${(+s.score).toFixed(4)}</span></div>${String(s.content).slice(0,260).replace(/</g,'&lt;')}`;
      ad.appendChild(el);
    });
    if(!d.sources.length) ad.textContent='관련 문서를 찾지 못했습니다. 문서를 먼저 접수했는지 확인하세요.';
  }catch(err){ ad.textContent='질의 실패: '+err.message; }
  window.scrollTo(0,document.body.scrollHeight);
};
