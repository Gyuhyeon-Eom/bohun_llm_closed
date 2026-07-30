# -*- coding: utf-8 -*-
"""객체 스토리지 계층 — DB정의서 v0.6 (원본=MinIO, 열람=presigned URL) 구현.

설계 원칙:
  - presigned URL은 만료형이므로 DB에 저장하지 않고 요청 시마다 발급한다.
  - STORAGE_BACKEND=local(기본)이면 현행 파일시스템 경로가 그대로 동작 —
    minio 미반입 환경(개발·초기 폐쇄망)에서 코드 수정 없이 기동.
  - 객체 키 규약: {카테고리}/{식별자}/{파일명}  예: scans/15/판독지.pdf
"""
import os
from pathlib import Path

from config.settings import (
    STORAGE_BACKEND, MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY,
    MINIO_BUCKET, MINIO_SECURE, MINIO_PUBLIC_ENDPOINT, PRESIGNED_EXPIRES_S, ROOT,
)


class LocalStorage:
    """파일시스템 백엔드 — 현행 동작 유지. presigned_url은 None(앱 경로로 서빙)."""
    backend = "local"
    base = Path(os.getenv("LOCAL_STORE_DIR", str(ROOT / "data" / "originals")))

    def put_file(self, key: str, src_path: str) -> dict:
        dest = self.base / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        if Path(src_path).resolve() != dest.resolve():
            import shutil
            shutil.copy2(src_path, dest)
        return {"backend": "local", "bucket": None, "obj_key": str(dest)}

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> dict:
        dest = self.base / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return {"backend": "local", "bucket": None, "obj_key": str(dest)}

    def presigned_url(self, obj_key: str, page: int | None = None) -> str | None:
        return None  # 로컬은 앱 파일 서빙 경로 사용 (/scan-docs/{id}/file 등)

    def get_bytes(self, obj_key: str) -> bytes:
        return Path(obj_key).read_bytes()

    def exists(self, obj_key: str) -> bool:
        return bool(obj_key) and Path(obj_key).exists()


class MinioStorage:
    """MinIO 백엔드 — 버킷 자동 생성, presigned GET URL 발급.

    MINIO_PUBLIC_ENDPOINT: 브라우저가 접근하는 주소가 내부 주소와 다를 때(도커 내부
    minio:9000 ↔ 외부 localhost:9000) presigned 서명이 깨지지 않도록 발급용 클라이언트를
    공개 주소로 하나 더 둔다.
    """
    backend = "minio"

    def __init__(self):
        from minio import Minio
        self.bucket = MINIO_BUCKET
        self.cli = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY,
                         secret_key=MINIO_SECRET_KEY, secure=MINIO_SECURE)
        self._pub = self.cli
        if MINIO_PUBLIC_ENDPOINT and MINIO_PUBLIC_ENDPOINT != MINIO_ENDPOINT:
            self._pub = Minio(MINIO_PUBLIC_ENDPOINT, access_key=MINIO_ACCESS_KEY,
                              secret_key=MINIO_SECRET_KEY, secure=MINIO_SECURE)
        if not self.cli.bucket_exists(self.bucket):
            self.cli.make_bucket(self.bucket)

    def put_file(self, key: str, src_path: str) -> dict:
        ctype = "application/pdf" if key.lower().endswith(".pdf") else "application/octet-stream"
        self.cli.fput_object(self.bucket, key, src_path, content_type=ctype)
        return {"backend": "minio", "bucket": self.bucket, "obj_key": key}

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> dict:
        import io
        self.cli.put_object(self.bucket, key, io.BytesIO(data), len(data), content_type=content_type)
        return {"backend": "minio", "bucket": self.bucket, "obj_key": key}

    def presigned_url(self, obj_key: str, page: int | None = None) -> str | None:
        from datetime import timedelta
        url = self._pub.presigned_get_object(self.bucket, obj_key,
                                             expires=timedelta(seconds=PRESIGNED_EXPIRES_S))
        return f"{url}#page={page}" if page else url

    def get_bytes(self, obj_key: str) -> bytes:
        resp = self.cli.get_object(self.bucket, obj_key)
        try:
            return resp.read()
        finally:
            resp.close(); resp.release_conn()

    def exists(self, obj_key: str) -> bool:
        if not obj_key:
            return False
        try:
            self.cli.stat_object(self.bucket, obj_key)
            return True
        except Exception:
            return False


_storage = None


def get_storage():
    """전역 스토리지 싱글턴. minio 지정인데 접속 불가·패키지 미반입이면 local 폴백
    (기동은 살리고 stderr 경고 — 운영에서 조용히 죽지 않게)."""
    global _storage
    if _storage is None:
        if STORAGE_BACKEND == "minio":
            try:
                _storage = MinioStorage()
            except Exception as e:  # 미반입·미기동 환경 폴백
                import sys
                print(f"[storage] MinIO 사용 불가({e}) — local 폴백", file=sys.stderr)
                _storage = LocalStorage()
        else:
            _storage = LocalStorage()
    return _storage
