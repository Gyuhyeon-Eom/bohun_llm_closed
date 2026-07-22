# 지식그래프 온톨로지 카탈로그

단일 `kg_nodes`/`kg_edges` 테이블 위의 3개 레이어. 모두 원천 테이블에서 재생성되는
파생물이라 언제든 지우고 다시 만들 수 있다 (동기화 문제 원천 차단).

```
재생성 순서 (build_graph.py가 kg 전체 TRUNCATE — 반드시 이 순서):
  python3 db/build_graph.py            # ① 심의체계 레이어
  python3 scripts/build_rule_graph.py  # ② 판단기준룰 레이어
  python3 db/build_instance_graph.py   # ③ 실데이터 인스턴스 레이어
```

## ① 심의체계 레이어 (원천: 시드 CSV — db/build_graph.py)

| ntype | key 예시 | 의미 |
|---|---|---|
| review_type | 요건심의 | 심의유형 |
| review_content | 상이공무원심의 | 심의내용 |
| agenda | … | 의제 |
| clause | 예우법 4-1-4 | 조문(표준문안 카탈로그) |
| kcd | M21.27 | 질병분류코드 (등장분만) |
| case | 17 | 과거 사례 |

엣지: `review_type -HAS_CONTENT-> review_content -HAS_AGENDA-> agenda`,
`review_content -APPLIES{target_cond,result,rule_no}-> clause` (주문안 296규칙),
`case -OF_TYPE-> review_type`, `case -HAS_KCD-> kcd`

## ② 판단기준룰 레이어 (원천: judgment_rule — scripts/build_rule_graph.py)

| ntype | key 예시 | 의미 |
|---|---|---|
| jr_sub | 제2분과 | 분과 |
| jr_disease | (정규식 패턴) | 질환 매칭 패턴 |
| jr_axis | MRI 3개월 이내 | 판단축(조건·근거) |
| jr_doc | 병상일지 | 필요서류 |

엣지: `jr_sub -HAS_DISEASE-> jr_disease -JUDGED_BY-> jr_axis -REQUIRES-> jr_doc`

## ③ 실데이터 인스턴스 레이어 (원천: scan_doc·grade_agenda — db/build_instance_graph.py)

| ntype | key 예시 | 의미 |
|---|---|---|
| person | 정기조:480603 | 대상자 (name=성명, meta.reg_no는 생년6자리만) |
| scan_doc | 15 (sd_id) | 스캔 묶음 문서 |
| disease | 방광암 | 정규화된 질환명 (잡음·라벨 필터링) |
| grade | 6급2항5108호 | 상이등급 표기 |
| hospital | 부산보훈병원 | 발행기관 |
| grade_agenda | 1 (ga_id) | 상이등급 안건 (name=안건번호) |

엣지:
```
person   -HAS_DOC->        scan_doc        대상자의 문서
person   -HAS_AGENDA->     grade_agenda    대상자의 안건
scan_doc -MENTIONS->       disease         문서에 언급된 질환 {doc,line: 근거 위치}
scan_doc -MENTIONS_KCD->   kcd             문서에 등장한 질병코드 {doc}
scan_doc -ASSIGNS_GRADE->  grade           문서에 기재된 등급 {doc,line}
scan_doc -ISSUED_BY->      hospital        발행기관
scan_doc -CONVERTED_TO->   grade_agenda    안건 변환 이력
disease  -CODED_AS->       kcd             같은 묶음 내 질환↔코드 (문서 단위 근사)
disease  -MATCHES_RULE->   jr_disease      실데이터 질환 ↔ 판단기준룰 연결(레이어 ②로 진입)
```

## 그래프 RAG에서의 활용 (core/graph_rag.py)

질의에서 엔티티(인명·질환·KCD·등급)를 추출 → 인스턴스 레이어에서 1~2홉 확장 →
`MATCHES_RULE`로 판단기준룰 레이어에 진입해 필요서류·근거까지 —
벡터 검색(core/retrieval.py)이 못 잡는 **관계 질의**(누가·몇 건·어떤 기준)를 결정적으로 답한다.
모든 사실 라인에 원천(문서·라인)이 붙어 근거 추적이 가능하다.
