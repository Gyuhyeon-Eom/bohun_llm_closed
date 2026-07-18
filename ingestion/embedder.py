"""임베딩. 운영 = bge-m3 실모델 / 개발 = HashEmbedder(모델 다운로드 없이 파이프라인 검증).

선택: config.settings.EMBED_BACKEND ("bge" | "hash") 또는 get_embedder(backend=...)
주의: HashEmbedder는 의미 유사성이 없다 - 검색 품질 평가에는 반드시 bge-m3 사용.
"""
import hashlib
import math
import os
import time
from config.settings import EMBED_MODEL, EMBED_BATCH, EMBED_DIM, EMBED_BACKEND, EMBED_DEVICE


class BgeEmbedder:
    """운영용. bge-m3 dense 벡터. 최초 호출 시 모델 로드(수 GB, GPU 권장·CPU 가능).
    로그: 모델 로드 시간 + 호출별 건수/소요/처리량. EMBED_LOG=0으로 끌 수 있음."""

    def __init__(self):
        self._model = None
        self._log = os.getenv("EMBED_LOG", "1") != "0"

    def _load(self):
        if self._model is None:
            # 로컬 경로 판정: /, ., ~ 로 시작할 때만. (HF 저장소 ID 'BAAI/bge-m3'에도
            # 슬래시가 있으므로 os.sep 포함 여부로 판정하면 온라인 다운로드까지 차단됨)
            if EMBED_MODEL.startswith(("/", ".", "~")) and not os.path.isdir(os.path.expanduser(EMBED_MODEL)):
                raise FileNotFoundError(
                    f"EMBED_MODEL 경로에 모델이 없습니다: {EMBED_MODEL}\n"
                    "  다운로드: hf download BAAI/bge-m3 --local-dir <경로>\n"
                    "  또는 온라인 자동 다운로드: export EMBED_MODEL=BAAI/bge-m3")
            if self._log:
                print(f"[embed] bge-m3 로드 중... (model={EMBED_MODEL}, device={EMBED_DEVICE or 'auto'})")
            t0 = time.perf_counter()
            from FlagEmbedding import BGEM3FlagModel  # pip install FlagEmbedding
            kw = {"use_fp16": True}
            if EMBED_DEVICE:
                kw["device"] = EMBED_DEVICE
                if EMBED_DEVICE in ("cpu", "mps"):
                    kw["use_fp16"] = False   # CPU/Apple MPS는 fp32 (mps fp16 호환 이슈)
            self._model = BGEM3FlagModel(os.path.expanduser(EMBED_MODEL), **kw)
            if self._log:
                print(f"[embed] 로드 완료 ({time.perf_counter()-t0:.1f}초)")
        return self._model

    def encode(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        t0 = time.perf_counter()
        out = model.encode(texts, batch_size=EMBED_BATCH, return_dense=True)
        if self._log:
            dt = time.perf_counter() - t0
            print(f"[embed] {len(texts)}건 임베딩 {dt:.2f}초 ({len(texts)/dt:.1f}건/초)")
        return out["dense_vecs"].tolist()


class HashEmbedder:
    """개발용 대체. 텍스트 해시 기반 결정적 의사난수 벡터(정규화).
    파이프라인·DB·API 검증 전용 - 의미 검색 품질과 무관."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    @staticmethod
    def _one(text: str) -> list[float]:
        vec, seed = [], text.encode("utf-8")
        while len(vec) < EMBED_DIM:
            seed = hashlib.sha256(seed).digest()
            vec.extend(b / 255.0 - 0.5 for b in seed)
        vec = vec[:EMBED_DIM]
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


def get_embedder(backend: str = EMBED_BACKEND):
    return BgeEmbedder() if backend == "bge" else HashEmbedder()
