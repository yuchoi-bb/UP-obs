"""권한 모델 (5장): Superuser 모드 상태 + guard() 단일 검증 지점.

알려진 한계 (5.5, 반드시 문서화):
    exe에 박힌 비밀번호는 문자열 추출로 노출된다. 또한 앱에 설정된 Access
    Key 자체가 전체 권한을 가지므로, mc/aws cli 등으로 우회 접근이 가능하다.
    이 기능은 보안 통제가 아니라 오조작 방지 가드다. 향후 MinIO에서 키를
    2벌 발급하고 IAM 정책으로 DA-share/* 제한을 거는 방식으로 대체할 수
    있도록 Profile에 role 필드를 이미 두었다 (config.py 참고).
"""
from __future__ import annotations

import posixpath
from urllib.parse import unquote

from app.errors import AppError

SUPERUSER_PASSWORD = "123456"  # 5.2: 소스 코드 상수로 고정. 변경 시 재빌드 필요.
DA_SHARE_PREFIX = "DA-share/"
_FORBIDDEN_OPS_IN_NORMAL_MODE = {"delete", "rename", "move"}

_superuser = False  # 앱 종료 시 자동 해제되어야 하므로 디스크에 저장하지 않는다.


def is_superuser() -> bool:
    return _superuser


def enable_superuser(password: str) -> None:
    """비밀번호가 맞으면 Superuser 모드로 전환한다. 앱 내부 검증용이라 별도 에러 코드는 없다."""
    global _superuser
    if password != SUPERUSER_PASSWORD:
        raise ValueError("비밀번호가 올바르지 않습니다")
    _superuser = True


def disable_superuser() -> None:
    """5.2: 해제는 비밀번호 없이 가능하다."""
    global _superuser
    _superuser = False


def normalize_key(key: str) -> str:
    """5.3: URL 인코딩 해제, 백슬래시 통일, `..`/`//` 정규화.

    선행 슬래시는 제거하고, 원래 key가 `/`로 끝났으면 정규화 후에도 유지한다
    (S3 prefix 의미 보존).
    """
    decoded = unquote(key)
    decoded = decoded.replace("\\", "/")
    had_trailing_slash = decoded.endswith("/")

    normalized = posixpath.normpath(decoded)
    if normalized in (".", "/"):
        normalized = ""
    normalized = normalized.lstrip("/")

    if had_trailing_slash and normalized and not normalized.endswith("/"):
        normalized += "/"
    return normalized


def guard(key: str, op: str) -> str:
    """모든 S3 호출 직전 단일 지점에서 검증한다. 정규화된 key를 반환한다.

    - 일반 모드인데 정규화 결과가 DA-share/ 로 시작하지 않으면 -> E-3009
    - 일반 모드인데 op가 delete/rename/move면 -> E-3010
    """
    normalized = normalize_key(key)

    if not is_superuser():
        within_da_share = normalized == DA_SHARE_PREFIX.rstrip("/") or normalized.startswith(DA_SHARE_PREFIX)
        if not within_da_share:
            raise AppError("E-3009", detail=f"key={normalized!r}")
        if op in _FORBIDDEN_OPS_IN_NORMAL_MODE:
            raise AppError("E-3010", detail=f"op={op} key={normalized!r}")

    return normalized


def root_prefix() -> str:
    """5.1: 일반 모드 루트는 DA-share/, Superuser는 버킷 루트(빈 prefix)."""
    return "" if is_superuser() else DA_SHARE_PREFIX
