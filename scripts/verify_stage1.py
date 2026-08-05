#!/usr/bin/env python3
"""1단계(app/errors.py, config.py, s3client.py) 수동 검증용 CLI.

실 MinIO에 대해 list -> put -> get 순으로 확인한다. 자격증명은 프로파일
저장소(config.json) 대신 환경변수로 받아, 스크립트 실행 자체가 config.json에
자격증명을 남기지 않도록 한다.

사용법 (PowerShell):
    $env:UPOBS_ACCESS_KEY = "..."
    $env:UPOBS_SECRET_KEY = "..."
    python scripts/verify_stage1.py `
        --endpoint http://10.169.148.36:10443 `
        --bucket toolhub-objectstorage `
        --prefix DA-share/

사용법 (bash):
    UPOBS_ACCESS_KEY=... UPOBS_SECRET_KEY=... \\
      python3 scripts/verify_stage1.py --endpoint http://10.169.148.36:10443 \\
      --bucket toolhub-objectstorage --prefix DA-share/
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Profile
from app.errors import AppError
from app.s3client import S3Client


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="", help="MinIO endpoint URL. 비우면 AWS로 간주")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", default="", help="목록 조회할 prefix")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--proxy", default="", help="테스트용 프록시 (사설 IP면 자동 우회됨)")
    args = parser.parse_args()

    access_key = os.environ.get("UPOBS_ACCESS_KEY", "")
    secret_key = os.environ.get("UPOBS_SECRET_KEY", "")
    if not access_key or not secret_key:
        print("UPOBS_ACCESS_KEY / UPOBS_SECRET_KEY 환경변수를 설정하세요.", file=sys.stderr)
        return 2

    profile = Profile(
        name="verify",
        access_key_id=access_key,
        secret_access_key=secret_key,
        endpoint_url=args.endpoint,
        region=args.region,
        bucket=args.bucket,
        proxy=args.proxy,
    )

    client = S3Client(profile)

    try:
        print(f"[1/3] list_objects(bucket={args.bucket!r}, prefix={args.prefix!r})")
        found_any = False
        for page in client.list_objects(args.bucket, prefix=args.prefix):
            for cp in page.get("CommonPrefixes", []):
                print(f"  DIR  {cp['Prefix']}")
                found_any = True
            for obj in page.get("Contents", []):
                print(f"  FILE {obj['Key']}  ({obj['Size']} bytes)")
                found_any = True
        if not found_any:
            print("  (비어 있음)")

        probe_key = f"{args.prefix}_upobs_stage1_probe.txt"
        with tempfile.TemporaryDirectory() as tmp:
            upload_src = Path(tmp) / "probe.txt"
            upload_src.write_text("UP-obs stage1 verification\n", encoding="utf-8")

            print(f"[2/3] put_object(key={probe_key!r})")
            client.put_object(args.bucket, probe_key, upload_src)

            download_dest = Path(tmp) / "probe_downloaded.txt"
            print(f"[3/3] get_object(key={probe_key!r})")
            client.get_object(args.bucket, probe_key, download_dest)

            if download_dest.read_text(encoding="utf-8") == upload_src.read_text(encoding="utf-8"):
                print("  내용 일치 확인")
            else:
                print("  경고: 업로드/다운로드 내용이 다릅니다", file=sys.stderr)

            client.delete_object(args.bucket, probe_key)
            print("  probe 객체 정리 완료")

    except AppError as exc:
        print(f"검증 실패: {exc.display_text()} (detail={exc.detail})", file=sys.stderr)
        return 1

    print("Stage 1 검증 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
