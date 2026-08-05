"""에러 카탈로그과 로깅.

- 로그 포맷: `YYYY-MM-DD HH:MM:SS | ERROR | E-xxxx | 메시지 | detail=... | req_id=...`
- 로그 위치: %LOCALAPPDATA%\\S3Explorer\\logs\\s3explorer.log (2MB x 5 회전)
- Access Key / Secret Key는 절대 로그에 남기지 않는다.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import uuid
from pathlib import Path
from typing import Optional

ERROR_CATALOG: dict[str, str] = {
    # E-1xxx: 설정 / 자격증명
    "E-1001": "설정 파일을 읽을 수 없습니다",
    "E-1002": "필수 설정값이 비어 있습니다",
    "E-1003": "자격증명 복호화에 실패했습니다",
    "E-1004": "설정을 저장하지 못했습니다",
    "E-1005": "선택한 프로파일이 없습니다",
    # E-2xxx: 네트워크 / 프록시 / SSL / 인증
    "E-2001": "엔드포인트에 연결할 수 없습니다",
    "E-2002": "SSL 인증서 검증에 실패했습니다",
    "E-2003": "프록시를 통과하지 못했습니다",
    "E-2004": "요청 시간이 초과되었습니다",
    "E-2005": "인증에 실패했습니다 (키를 확인하세요)",
    "E-2006": "접근 권한이 없습니다",
    # E-3xxx: S3 오퍼레이션 / 권한
    "E-3001": "버킷을 찾을 수 없습니다",
    "E-3002": "객체 목록 조회에 실패했습니다",
    "E-3003": "다운로드에 실패했습니다",
    "E-3004": "업로드에 실패했습니다",
    "E-3005": "삭제에 실패했습니다",
    "E-3006": "복사 또는 이름 변경에 실패했습니다",
    "E-3007": "객체가 존재하지 않습니다",
    "E-3008": "같은 이름의 객체가 이미 있습니다",
    "E-3009": "허용된 경로를 벗어났습니다",
    "E-3010": "일반 모드에서는 사용할 수 없는 기능입니다",
    # E-4xxx: UI / 드래그앤드롭 / 로컬 I/O
    "E-4001": "드롭한 항목을 해석할 수 없습니다",
    "E-4002": "로컬 파일을 읽을 수 없습니다",
    "E-4003": "임시 폴더를 만들 수 없습니다",
    "E-4004": "경로가 너무 깁니다 (260자 제한)",
    "E-4005": "폴더 이름에 사용할 수 없는 문자가 있습니다",
    # E-5xxx: 업데이트 / 릴리즈
    "E-5001": "업데이트 확인에 실패했습니다",
    "E-5002": "릴리즈에서 실행 파일을 찾을 수 없습니다",
    "E-5003": "업데이트 다운로드에 실패했습니다",
    "E-5004": "업데이트 적용에 실패했습니다",
    # E-9xxx: 미분류
    "E-9001": "처리되지 않은 오류가 발생했습니다",
}

# 10.3 botocore 예외 매핑
BOTOCORE_ERROR_MAP: dict[str, str] = {
    "EndpointConnectionError": "E-2001",
    "SSLError": "E-2002",
    "ProxyConnectionError": "E-2003",
    "ConnectTimeoutError": "E-2004",
    "ReadTimeoutError": "E-2004",
    "InvalidAccessKeyId": "E-2005",
    "SignatureDoesNotMatch": "E-2005",
    "AccessDenied": "E-2006",
    "NoSuchBucket": "E-3001",
    "NoSuchKey": "E-3007",
}


class AppError(Exception):
    """앱 전역에서 사용하는 에러. 항상 카탈로그의 코드를 가진다."""

    def __init__(self, code: str, detail: str = "", *, req_id: Optional[str] = None):
        if code not in ERROR_CATALOG:
            code = "E-9001"
        self.code = code
        self.message = ERROR_CATALOG[code]
        self.detail = detail
        self.req_id = req_id or uuid.uuid4().hex[:8]
        super().__init__(f"[{code}] {self.message}")

    def display_text(self) -> str:
        """오류 대화상자에 표시할 문구. 코드를 항상 포함한다 (10.4)."""
        return f"[{self.code}] {self.message}"


def mask_access_key(access_key: str) -> str:
    """Access Key는 앞 4자리만 남기고 마스킹한다 (3.3)."""
    if not access_key:
        return ""
    return f"{access_key[:4]}{'*' * 4}"


def get_log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "S3Explorer" / "logs"
    return Path.home() / ".s3explorer" / "logs"


_logger: Optional[logging.Logger] = None


def get_logger() -> logging.Logger:
    """s3explorer 로거. 2MB x 5 회전 파일 핸들러 하나만 부착한다."""
    global _logger
    if _logger is not None:
        return _logger

    log_dir = get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("s3explorer")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    handler = logging.handlers.RotatingFileHandler(
        log_dir / "s3explorer.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s", "%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)
    _logger = logger
    return logger


def log_error(code: str, detail: str = "", req_id: Optional[str] = None) -> str:
    """에러를 표준 포맷으로 기록하고 사용된 req_id를 반환한다."""
    message = ERROR_CATALOG.get(code, ERROR_CATALOG["E-9001"])
    req_id = req_id or uuid.uuid4().hex[:8]
    get_logger().error("ERROR | %s | %s | detail=%s | req_id=%s", code, message, detail, req_id)
    return req_id


def map_botocore_exception(exc: Exception) -> str:
    """botocore/일반 예외를 앱 에러 코드로 변환한다 (10.3)."""
    try:
        from botocore.exceptions import ClientError
    except ImportError:
        ClientError = ()  # type: ignore[assignment]

    if isinstance(exc, ClientError):
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in BOTOCORE_ERROR_MAP:
            return BOTOCORE_ERROR_MAP[error_code]
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status == 404:
            return "E-3007"
        return "E-9001"

    return BOTOCORE_ERROR_MAP.get(type(exc).__name__, "E-9001")
