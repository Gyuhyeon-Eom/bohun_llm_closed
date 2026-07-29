# OCR 골드셋 — 35137719 04 영상판독지 (중앙보훈병원, 5쪽)

원본: data/originals/scans/35137719-________04_________.pdf (텍스트층 없는 스캔)
전사 기준: 페이지에 보이는 글자 그대로. 공백·줄바꿈은 평가기(eval_ocr_accuracy.py)가 정규화하므로 무시.
[로고]·판독의 표기는 vlm_ocr.py 전사 프롬프트 규칙과 동일 관례.

사용:
  python3 scripts/vlm_ocr.py <이 PDF> -o out_vlm            # VLM 전사
  python3 scripts/eval_ocr_accuracy.py \
    --ref-dir data/eval/ocr_gold/35137719_04_판독지 \
    --hyp-dir out_vlm/<pdf명>/ out_tess/<pdf명>/            # 파일명(page_NN.txt) 매칭

주의: 골드는 사람이 화면 대조로 작성 — 문서 유형 추가 시(소견서·진단서·통보서) 같은 방식으로 페이지를 늘려갈 것.
