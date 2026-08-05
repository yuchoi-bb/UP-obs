# UP-obs

Windows용 S3 호환 오브젝트 스토리지 탐색기. PySide6 + boto3.

## 원칙

- **요구사항은 `REQUIREMENTS.md`를 따른다.** 스펙에 없는 기능을 임의로 추가하지 않는다.
- 스펙이 모호하면 구현하기 전에 먼저 묻는다.
- 답변과 주석은 한국어. 결론부터 간결하게.

## 작업 단위

`REQUIREMENTS.md` 14장의 5단계를 순서대로 진행한다.
한 번에 한 단계만 구현하고, 단계 종료 시 동작 검증 후 `/compact`.

## 절대 하지 말 것

- Access Key / Secret Key를 로그, 콘솔, 커밋에 남기지 말 것
- 프록시를 환경변수에 의존하지 말 것 (botocore Config로 명시 주입)
- 경로 검증(`guard()`)을 우회하는 S3 호출을 만들지 말 것
- UI 스레드에서 S3 I/O를 호출하지 말 것

## 환경

- 대상 스토리지: MinIO `http://10.169.148.36:10443`, 버킷 `toolhub-objectstorage`
- path-style addressing 필수
- 사설 IP라 프록시 우회 필요
