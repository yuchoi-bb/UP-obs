"""boto3 클라이언트 래퍼.

- 2.1: 주소 방식(path/virtual) 자동 판정, 고급 설정 override
- 4장: 프록시는 botocore.config.Config로 명시 주입. 사설 IP 대역은 강제 우회.
- 6.1: ListObjectsV2 + Delimiter, 1000개 단위 페이징
- 10.3: botocore 예외를 앱 에러 코드로 매핑
"""
from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urlparse

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import Profile
from app.errors import AppError, get_logger, log_error, map_botocore_exception, mask_access_key
from app.permissions import guard

# 4.1: 프록시를 강제로 비우는 사설 대역
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
]


def _is_private_host(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in net for net in _PRIVATE_NETWORKS)


def resolve_addressing_style(profile: Profile) -> str:
    """2.1 주소 방식 자동 판정. 고급 설정에서 수동 override 가능."""
    if profile.addressing_style in ("path", "virtual"):
        return profile.addressing_style
    return "path" if profile.endpoint_url else "virtual"


def resolve_proxies(profile: Profile) -> dict:
    """4장: 목적지가 사설 IP 대역이면 프록시를 강제로 비운다."""
    if not profile.proxy:
        return {}
    host = urlparse(profile.endpoint_url).hostname if profile.endpoint_url else ""
    if host and _is_private_host(host):
        return {}
    return {"http": profile.proxy, "https": profile.proxy}


def _resolve_verify(verify_ssl: str):
    if verify_ssl in ("true", "True", "1", ""):
        return True
    if verify_ssl in ("false", "False", "0"):
        return False
    return verify_ssl  # CA 파일 경로


def build_client(profile: Profile) -> BaseClient:
    """profile로부터 boto3 S3 클라이언트를 생성한다. 프록시는 환경변수에 의존하지 않는다."""
    addressing_style = resolve_addressing_style(profile)
    proxies = resolve_proxies(profile)
    verify = _resolve_verify(profile.verify_ssl)

    boto_config = Config(
        region_name=profile.region,
        signature_version="s3v4",
        s3={"addressing_style": addressing_style},
        proxies=proxies or None,
        connect_timeout=profile.connect_timeout,
        read_timeout=profile.read_timeout,
        retries={"max_attempts": profile.max_retries, "mode": "standard"},
    )

    logger = get_logger()
    logger.info(
        "INFO | S3 클라이언트 생성 | endpoint=%s addressing=%s access_key=%s",
        profile.endpoint_url or "(AWS)",
        addressing_style,
        mask_access_key(profile.access_key_id),
    )

    session = boto3.session.Session()
    return session.client(
        "s3",
        endpoint_url=profile.endpoint_url or None,
        aws_access_key_id=profile.access_key_id,
        aws_secret_access_key=profile.secret_access_key,
        verify=verify,
        config=boto_config,
    )


class S3Client:
    """S3 오퍼레이션 래퍼. 예외는 항상 AppError로 변환되어 던져진다."""

    def __init__(self, profile: Profile):
        self.profile = profile
        self._client = build_client(profile)

    def _raise(self, exc: Exception, code_hint: Optional[str] = None) -> None:
        code = map_botocore_exception(exc)
        if code == "E-9001" and code_hint:
            code = code_hint
        req_id = log_error(code, detail=str(exc))
        raise AppError(code, detail=str(exc), req_id=req_id) from exc

    def list_buckets(self) -> list[str]:
        try:
            resp = self._client.list_buckets()
        except Exception as exc:
            self._raise(exc)
        return [b["Name"] for b in resp.get("Buckets", [])]

    def list_objects(self, bucket: str, prefix: str = "", delimiter: str = "/") -> Iterator[dict]:
        """6.1: CommonPrefixes -> 폴더, Contents -> 파일. 페이지 단위로 원본 응답을 그대로 넘긴다.

        delimiter=""(빈 문자열)이면 CommonPrefixes 없이 하위 전체를 재귀적으로
        평탄하게 반환한다 (폴더 다운로드 시 전체 객체 목록을 구할 때 사용).
        """
        prefix = guard(prefix, "list")
        paginator = self._client.get_paginator("list_objects_v2")
        params = {"Bucket": bucket, "Prefix": prefix, "PaginationConfig": {"PageSize": 1000}}
        if delimiter:
            params["Delimiter"] = delimiter
        try:
            for page in paginator.paginate(**params):
                yield page
        except Exception as exc:
            self._raise(exc, code_hint="E-3002")

    def object_exists(self, bucket: str, key: str) -> bool:
        key = guard(key, "read")
        try:
            self._client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 404:
                return False
            self._raise(exc)
        return False

    def get_object(self, bucket: str, key: str, dest_path: Path) -> None:
        key = guard(key, "read")
        try:
            self._client.download_file(bucket, key, str(dest_path))
        except Exception as exc:
            self._raise(exc, code_hint="E-3003")

    def put_object(self, bucket: str, key: str, src_path: Path) -> None:
        key = guard(key, "write")
        try:
            self._client.upload_file(str(src_path), bucket, key)
        except Exception as exc:
            self._raise(exc, code_hint="E-3004")

    def create_folder(self, bucket: str, prefix: str) -> None:
        """6.2: 빈 폴더는 prefix/ 이름의 0바이트 객체로 표현한다."""
        key = prefix if prefix.endswith("/") else prefix + "/"
        key = guard(key, "write")
        try:
            self._client.put_object(Bucket=bucket, Key=key, Body=b"")
        except Exception as exc:
            self._raise(exc, code_hint="E-3004")

    def delete_object(self, bucket: str, key: str) -> None:
        key = guard(key, "delete")
        try:
            self._client.delete_object(Bucket=bucket, Key=key)
        except Exception as exc:
            self._raise(exc, code_hint="E-3005")

    def copy_object(self, bucket: str, src_key: str, dest_key: str) -> None:
        """이름변경/이동(6.3)의 기반 오퍼레이션이므로 op="rename"으로 검증한다."""
        src_key = guard(src_key, "rename")
        dest_key = guard(dest_key, "rename")
        try:
            self._client.copy_object(
                Bucket=bucket, CopySource={"Bucket": bucket, "Key": src_key}, Key=dest_key
            )
        except Exception as exc:
            self._raise(exc, code_hint="E-3006")

    def generate_presigned_url(self, bucket: str, key: str, expires_in: int = 3600) -> str:
        key = guard(key, "read")
        try:
            return self._client.generate_presigned_url(
                "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=expires_in
            )
        except Exception as exc:
            self._raise(exc)
