"""프로파일 설정: 저장/로드, DPAPI 암복호화, AWS CLI 프로파일 가져오기.

Secret Access Key는 메모리에서만 평문으로 다루고, 디스크에는 항상 DPAPI로
암호화된 값만 기록한다 (3.3).
"""
from __future__ import annotations

import base64
import configparser
import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Optional

from app.errors import AppError

try:
    import win32crypt  # type: ignore

    _HAS_DPAPI = True
except ImportError:  # Windows 외 환경 (예: 개발/테스트)
    win32crypt = None  # type: ignore[assignment]
    _HAS_DPAPI = False

_DPAPI_DESCRIPTION = "S3Explorer"

REQUIRED_FIELDS = ("name", "access_key_id", "secret_access_key", "region")


def dpapi_encrypt(data: bytes) -> bytes:
    if not _HAS_DPAPI:
        raise RuntimeError("DPAPI는 Windows에서만 사용할 수 있습니다")
    return win32crypt.CryptProtectData(data, _DPAPI_DESCRIPTION, None, None, None, 0)


def dpapi_decrypt(data: bytes) -> bytes:
    if not _HAS_DPAPI:
        raise RuntimeError("DPAPI는 Windows에서만 사용할 수 있습니다")
    return win32crypt.CryptUnprotectData(data, None, None, None, 0)[1]


@dataclass
class Profile:
    """3장 프로파일 필드. 기본 항목 + 고급 항목."""

    name: str
    access_key_id: str = ""
    secret_access_key: str = field(default="", repr=False)
    region: str = "us-east-1"
    endpoint_url: str = ""
    bucket: str = ""
    addressing_style: str = "auto"  # auto | path | virtual
    verify_ssl: str = "true"  # "true" | "false" | CA 파일 경로
    proxy: str = ""
    connect_timeout: int = 10
    read_timeout: int = 60
    max_retries: int = 3
    multipart_threshold: int = 64 * 1024 * 1024
    max_concurrency: int = 4
    role: str = ""  # 향후 IAM 정책 기반 권한 분리용 (5.5)

    def __post_init__(self) -> None:
        # 2.2 입력 정규화
        if self.bucket.startswith("s3://"):
            self.bucket = self.bucket[len("s3://"):]
        self.endpoint_url = self.endpoint_url.rstrip("/")

    def validate(self) -> None:
        for name in REQUIRED_FIELDS:
            if not getattr(self, name):
                raise AppError("E-1002", detail=f"missing field: {name}")


def default_config_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "S3Explorer" / "config.json"
    return Path.home() / ".s3explorer" / "config.json"


class ProfileStore:
    """config.json 저장/로드와 활성 프로파일 관리."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or default_config_path()
        self.profiles: list[Profile] = []
        self.active_profile: Optional[str] = None

    def load(self) -> None:
        if not self.config_path.exists():
            self.profiles = []
            self.active_profile = None
            return

        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AppError("E-1001", detail=str(exc)) from exc

        known_fields = {f.name for f in fields(Profile)}
        profiles = []
        for item in raw.get("profiles", []):
            item = dict(item)
            enc_secret = item.pop("secret_access_key_enc", None)
            secret = ""
            if enc_secret:
                try:
                    secret = dpapi_decrypt(base64.b64decode(enc_secret)).decode("utf-8")
                except Exception as exc:
                    raise AppError("E-1003", detail=str(exc)) from exc
            item = {k: v for k, v in item.items() if k in known_fields}
            profiles.append(Profile(secret_access_key=secret, **item))

        self.profiles = profiles
        self.active_profile = raw.get("active_profile")

    def save(self) -> None:
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AppError("E-1004", detail=str(exc)) from exc

        data: dict = {"active_profile": self.active_profile, "profiles": []}
        for profile in self.profiles:
            item = asdict(profile)
            secret = item.pop("secret_access_key")
            try:
                enc = dpapi_encrypt(secret.encode("utf-8"))
            except Exception as exc:
                raise AppError("E-1004", detail=str(exc)) from exc
            item["secret_access_key_enc"] = base64.b64encode(enc).decode("ascii")
            data["profiles"].append(item)

        try:
            tmp_path = self.config_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_path.replace(self.config_path)
        except OSError as exc:
            raise AppError("E-1004", detail=str(exc)) from exc

    def get_active(self) -> Profile:
        if not self.profiles:
            raise AppError("E-1005")
        for profile in self.profiles:
            if profile.name == self.active_profile:
                return profile
        raise AppError("E-1005")

    def upsert(self, profile: Profile) -> None:
        profile.validate()
        for idx, existing in enumerate(self.profiles):
            if existing.name == profile.name:
                self.profiles[idx] = profile
                return
        self.profiles.append(profile)

    def remove(self, name: str) -> None:
        self.profiles = [p for p in self.profiles if p.name != name]
        if self.active_profile == name:
            self.active_profile = self.profiles[0].name if self.profiles else None


def import_aws_cli_profiles(path: Optional[Path] = None) -> list[Profile]:
    """3.4: ~/.aws/credentials 에서 프로파일을 읽기 전용으로 가져온다. 파일에 쓰지 않는다."""
    cred_path = path or (Path.home() / ".aws" / "credentials")
    if not cred_path.exists():
        return []

    parser = configparser.ConfigParser()
    parser.read(cred_path, encoding="utf-8")

    profiles = []
    for section in parser.sections():
        access_key = parser.get(section, "aws_access_key_id", fallback="")
        secret_key = parser.get(section, "aws_secret_access_key", fallback="")
        if not access_key or not secret_key:
            continue
        profiles.append(
            Profile(name=section, access_key_id=access_key, secret_access_key=secret_key)
        )
    return profiles
