/* 공용 페이지네이션 — board.js / feedback.js / index.js 에서 사용.
   (645c2cb "feat:페이지네이션 추가" 커밋에서 이 파일이 누락되어 pageSlice 미정의로
    목록 렌더가 전부 중단되던 문제의 복구본. 호출 시그니처는 해당 커밋 기준.) */

/* list를 page(1-base)·per 단위로 자름. page가 범위를 벗어나면 보정해서 돌려준다. */
function pageSlice(list, page, per){
  const totalPages = Math.max(1, Math.ceil(list.length / per));
  const p = Math.min(Math.max(1, page || 1), totalPages);
  return { slice: list.slice((p - 1) * per, p * per), page: p, totalPages };
}

/* elId 요소에 ‹ 1 2 3 › 버튼 렌더. 클릭 시 window[fnName](페이지) 호출.
   1페이지뿐이면 비움(.pager:empty / .gpage 여백 최소화). 현재 페이지 ±2 창 + 양끝 생략(…) */
function renderPager(elId, page, totalPages, fnName){
  const el = document.getElementById(elId);
  if(!el) return;
  if(totalPages <= 1){ el.innerHTML = ''; return; }
  const btn = (p, label, on, dis) =>
    `<button ${dis ? 'disabled' : ''} class="${on ? 'on' : ''}" onclick="${fnName}(${p})">${label}</button>`;
  const parts = [btn(page - 1, '&lsaquo;', false, page <= 1)];
  const win = 2;
  let last = 0;
  for(let p = 1; p <= totalPages; p++){
    if(p === 1 || p === totalPages || Math.abs(p - page) <= win){
      if(last && p - last > 1) parts.push('<span>&hellip;</span>');
      parts.push(btn(p, p, p === page, false));
      last = p;
    }
  }
  parts.push(btn(page + 1, '&rsaquo;', false, page >= totalPages));
  el.innerHTML = parts.join('');
}
