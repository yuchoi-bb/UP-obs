# UP-obs — Object Storage Explorer 요구사항

- **Repository**: https://github.com/yuchoi-bb/UP-obs
- **버전**: v0.1 (확정)
- **최종 갱신**: 2026-08-05

---

## 1. 개요

Windows 데스크톱 애플리케이션. S3 API 호환 오브젝트 스토리지(사내 MinIO)를
Windows 탐색기와 동일한 폴더 구조로 탐색하고, 드래그앤드롭으로 업로드/다운로드한다.

### 1.1 확정된 기술 결정

| 항목 | 결정 | 비고 |
|---|---|---|
| 언어/런타임 | Python 3.11 | |
| GUI | PySide6 | LGPL — 사내 배포 라이선스 이슈 없음 |
| S3 클라이언트 | boto3 / botocore | |
| 패키징 | PyInstaller `--onefile --windowed` | 단일 exe |
| 업데이트 | GitHub Releases + updater.cmd | public repo, 토큰 불필요 |
| 설정 저장 | `%LOCALAPPDATA%\S3Explorer\config.json` | Secret Key는 DPAPI 암호화 |
| CI | GitHub Actions (`windows-latest`) | 태그 push 트리거 |

### 1.2 비목표 (이번 범위 밖)

- macOS / Linux 지원
- 객체 버전관리(versioning) UI
- 서버사이드 암호화(SSE) 설정
- 멀티 계정 동시 접속 (프로파일 전환 방식으로 대체)

---

## 2. 대상 스토리지

사내 MinIO. 기존에 환경변수로 사용하던 값:

```
TH_BK  = s3://toolhub-objectstorage
TH_URL = http://10.169.148.36:10443
```

### 2.1 주소 방식 자동 판정

| Endpoint URL | 주소 방식 | 판정 |
|---|---|---|
| 비어 있음 | virtual-hosted | AWS S3로 간주 |
| 값 있음 | **path-style** | MinIO 등 호환 스토리지로 간주 |

고급 설정에서 수동 override 가능해야 한다.

### 2.2 입력 정규화

- Bucket 입력값에 `s3://` 접두어가 있으면 **자동으로 제거**한다.
- Endpoint URL 끝의 `/`는 제거한다.

---

## 3. 설정 (프로파일)

프로파일은 N개 저장 가능하며 툴바 드롭다운으로 전환한다.

### 3.1 기본 항목 (설정창 상시 노출)

| 필드 | 키 | 필수 | 기본값 |
|---|---|---|---|
| 프로파일 이름 | `name` | O | — |
| Endpoint URL | `endpoint_url` | X | 빈값(=AWS) |
| Access Key ID | `access_key_id` | O | — |
| Secret Access Key | `secret_access_key` | O | — (마스킹 표시) |
| Region | `region` | O | `us-east-1` |

> AWS CLI가 묻는 `Default output format`은 **사용하지 않는다** (GUI에 해당 없음).

### 3.2 고급 항목 (접이식)

| 필드 | 키 | 기본값 | 설명 |
|---|---|---|---|
| Bucket | `bucket` | 빈값 | 비우면 `ListBuckets` 시도 |
| 주소 방식 | `addressing_style` | `auto` | `auto` / `path` / `virtual` |
| SSL 검증 | `verify_ssl` | `true` | `true` / `false` / CA 파일 경로 |
| 프록시 | `proxy` | 빈값 | 비우면 프록시 미사용 |
| 연결 타임아웃 | `connect_timeout` | 10초 | |
| 읽기 타임아웃 | `read_timeout` | 60초 | |
| 재시도 횟수 | `max_retries` | 3 | |
| Multipart 임계값 | `multipart_threshold` | 64MB | |
| 동시 전송 수 | `max_concurrency` | 4 | |

### 3.3 자격증명 보호

- Secret Access Key는 Windows DPAPI(`CryptProtectData`)로 암호화 저장한다.
  동일 Windows 계정에서만 복호화된다.
- 복호화 실패 시 **E-1003**으로 처리하고 재입력을 유도한다.
- 로그에 Access Key / Secret Key를 **절대 기록하지 않는다.**
  Access Key는 앞 4자리만 남기고 마스킹한다 (`AKIA****`).

### 3.4 `~/.aws/credentials` 가져오기

설정창에 "AWS CLI 프로파일 가져오기" 버튼을 둔다.
읽기 전용이며, 우리 앱은 해당 파일에 쓰지 않는다.

---

## 4. 프록시 정책

**Windows 전역 설정에 의존하지 않는다.** 목적지별로 앱이 판단한다.

| 목적지 | 프록시 |
|---|---|
| MinIO endpoint (사설 IP 대역) | **우회** |
| `api.github.com` (업데이트) | 시스템 프록시 적용 |

### 4.1 구현 규칙

- boto3 클라이언트 생성 시 `botocore.config.Config(proxies=...)`로 명시 주입한다.
  환경변수에 의존하지 않는다.
- Endpoint 호스트가 아래 사설 대역이면 프록시를 **강제로 비운다**:
  `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`
- 업데이트 체크용 HTTP 세션은 별도로 만들며, 시스템 프록시를 따른다.

---

## 5. 권한 모델 (2단계)

### 5.1 모드별 권한

| 기능 | 일반 모드 | Superuser |
|---|---|---|
| 루트 경로 | `DA-share/` | 버킷 루트 `/` |
| 조회 / 다운로드 | O | O |
| 업로드 | O | O |
| 새 폴더 | O | O |
| 삭제 | **X** | O |
| 이름변경 / 이동 | **X** | O |

- `DA-share`는 `toolhub-objectstorage` 버킷 **내부의 prefix**다. 별도 버킷이 아니다.
- 일반 모드에서는 `DA-share/` 밖의 prefix가 트리와 목록에 **표시되지 않는다.**

### 5.2 모드 전환

- 메뉴 → "관리자 모드" → 비밀번호 입력
- 비밀번호는 **소스 코드 상수로 고정** (`123456`). 변경 시 재빌드 필요.
- 해제는 비밀번호 없이 가능.
- **앱 종료 시 자동 해제.** 다음 실행은 항상 일반 모드로 시작한다.
- Superuser 활성 중에는 상태바에 빨간 배지를 상시 표시한다.

### 5.3 경로 검증 (중요)

버튼 숨김만으로는 부족하다. **모든 S3 호출 직전 단일 지점에서 검증**한다.

```
def guard(key: str, op: str) -> None
```

- key를 정규화한다 (`..`, `//`, 백슬래시, URL 인코딩 해제)
- 일반 모드인데 정규화 결과가 `DA-share/`로 시작하지 않으면 → **E-3009**
- 일반 모드인데 `op`가 `delete` / `rename` / `move`면 → **E-3010**

### 5.4 덮어쓰기 처리

일반 모드에서 동일 키 업로드는 실질적으로 삭제와 같다.
따라서 업로드 전 존재 여부를 확인하고, 존재하면 **확인 대화상자**를 띄운다.
사용자가 취소하면 해당 항목만 건너뛴다.

### 5.5 알려진 한계 (문서화 필요)

exe에 박힌 비밀번호는 문자열 추출로 노출된다. 또한 앱에 설정된 Access Key
자체가 전체 권한을 가지므로, `mc`나 `aws cli`로 우회 접근이 가능하다.
**이 기능은 보안 통제가 아니라 오조작 방지 가드다.**

향후 MinIO에서 키를 2벌 발급하고 IAM 정책으로 `DA-share/*` 제한을 거는
방식으로 대체할 수 있도록, 프로파일에 `role` 필드를 둘 수 있는 구조로 설계한다.

---

## 6. 파일시스템 뷰

### 6.1 폴더 매핑

- `ListObjectsV2` + `Delimiter='/'` 사용
- `CommonPrefixes` → 폴더, `Contents` → 파일
- 좌측 트리는 **지연 로딩**(펼칠 때 조회), 우측 목록은 현재 prefix만
- 페이징: `ContinuationToken`으로 이어받되, 1회 1000개 단위

### 6.2 빈 폴더

S3에는 빈 폴더 개념이 없다. `prefix/` 이름의 0바이트 객체로 표현한다.
목록 표시 시 자기 자신과 동일한 키는 파일 목록에서 제외한다.

### 6.3 이름 변경 / 이동

`CopyObject` 후 `DeleteObject`. 폴더 대상이면 하위 전체를 순회한다.
**원자적이지 않으므로** 진행률과 실패 항목을 사용자에게 명시한다.

---

## 7. 드래그앤드롭

### 7.1 탐색기 → 앱 (업로드)

- `dragEnterEvent`에서 `text/uri-list` 수락
- 폴더 드롭 시 재귀 순회, 상대 경로를 그대로 prefix로 매핑
- 현재 보고 있는 prefix 하위로 업로드

### 7.2 앱 → 탐색기 (다운로드)

**캐시 폴더 경유 방식으로 구현한다.** (가상 파일 IDataObject 미구현)

1. 드래그 시작 → `%LOCALAPPDATA%\S3Explorer\cache\`로 다운로드
2. 완료 후 해당 로컬 경로를 `QDrag` + `QMimeData.setUrls()`로 전달

**임계값 규칙**: 선택 항목 합계가 50MB를 넘으면 드래그를 시작하지 않고,
"다운로드 폴더로 받기"를 안내하는 툴팁을 띄운다. (드래그 중 멈춤 현상 방지)

캐시는 앱 종료 시, 그리고 7일 경과 항목에 대해 정리한다.

### 7.3 경로 길이

Windows 260자 제한에 걸리면 **E-4004**. 다운로드 시 대상 경로를 사전 검사한다.

---

## 8. 전송 처리

- 모든 S3 I/O는 `QThreadPool` + `QRunnable`로 UI 스레드와 분리한다.
- 상태바에 진행률(`n/총개수` + 바)을 표시한다.
- 취소 버튼을 제공한다.
- 실패 항목은 전체 중단 없이 수집한 뒤, 종료 시 요약 대화상자로 보고한다.

---

## 9. 화면 구성

```
┌────────────────────────────────────────────────┐
│ [프로파일 ▾] [↑업로드] [↓받기] [새폴더] [⚙설정] │
├──────────────┬─────────────────────────────────┤
│ 📁 buckets   │ 이름      크기    수정일   타입  │
│  └toolhub-…  │ ───────────────────────────────  │
│    ├ builds  │ 📁 v1.2.0                        │
│    ├ logs    │ 📄 tool.zip   12MB   08-05  zip  │
│    └ tools   │                                  │
├──────────────┴─────────────────────────────────┤
│ 연결됨 · 24개 · 1.2GB     [▓▓▓░] 업로드 3/7     │
└────────────────────────────────────────────────┘
```

- 색상: GitHub Dark 계열 (기존 ToolHub 대시보드와 통일)
- 컨텍스트 메뉴: 열기 / 다운로드 / 이름변경 / 삭제 / 경로 복사 / presigned URL 복사
- presigned URL 기본 만료: 1시간 (설정 가능)

### 9.1 문구 규칙

- 오류 메시지는 **무엇이 잘못됐고 어떻게 고치는지**를 쓴다. 사과하지 않는다.
- 빈 화면은 다음 행동을 안내한다. ("여기에 파일을 끌어다 놓으세요")
- 버튼 이름과 결과 메시지의 동사를 일치시킨다. (업로드 → "업로드했습니다")

---

## 10. 에러 코드

로그 포맷:

```
2026-08-05 10:11:12 | ERROR | E-2002 | SSL 인증서 검증 실패 | detail=... | req_id=...
```

로그 위치: `%LOCALAPPDATA%\S3Explorer\logs\s3explorer.log`
회전: 2MB × 5개

### 10.1 대역

| 대역 | 범주 |
|---|---|
| E-1xxx | 설정 / 자격증명 |
| E-2xxx | 네트워크 / 프록시 / SSL / 인증 |
| E-3xxx | S3 오퍼레이션 / 권한 |
| E-4xxx | UI / 드래그앤드롭 / 로컬 I/O |
| E-5xxx | 업데이트 / 릴리즈 |
| E-9xxx | 미분류 |

### 10.2 전체 목록

| 코드 | 메시지 |
|---|---|
| E-1001 | 설정 파일을 읽을 수 없습니다 |
| E-1002 | 필수 설정값이 비어 있습니다 |
| E-1003 | 자격증명 복호화에 실패했습니다 |
| E-1004 | 설정을 저장하지 못했습니다 |
| E-1005 | 선택한 프로파일이 없습니다 |
| E-2001 | 엔드포인트에 연결할 수 없습니다 |
| E-2002 | SSL 인증서 검증에 실패했습니다 |
| E-2003 | 프록시를 통과하지 못했습니다 |
| E-2004 | 요청 시간이 초과되었습니다 |
| E-2005 | 인증에 실패했습니다 (키를 확인하세요) |
| E-2006 | 접근 권한이 없습니다 |
| E-3001 | 버킷을 찾을 수 없습니다 |
| E-3002 | 객체 목록 조회에 실패했습니다 |
| E-3003 | 다운로드에 실패했습니다 |
| E-3004 | 업로드에 실패했습니다 |
| E-3005 | 삭제에 실패했습니다 |
| E-3006 | 복사 또는 이름 변경에 실패했습니다 |
| E-3007 | 객체가 존재하지 않습니다 |
| E-3008 | 같은 이름의 객체가 이미 있습니다 |
| E-3009 | 허용된 경로를 벗어났습니다 |
| E-3010 | 일반 모드에서는 사용할 수 없는 기능입니다 |
| E-4001 | 드롭한 항목을 해석할 수 없습니다 |
| E-4002 | 로컬 파일을 읽을 수 없습니다 |
| E-4003 | 임시 폴더를 만들 수 없습니다 |
| E-4004 | 경로가 너무 깁니다 (260자 제한) |
| E-4005 | 폴더 이름에 사용할 수 없는 문자가 있습니다 |
| E-5001 | 업데이트 확인에 실패했습니다 |
| E-5002 | 릴리즈에서 실행 파일을 찾을 수 없습니다 |
| E-5003 | 업데이트 다운로드에 실패했습니다 |
| E-5004 | 업데이트 적용에 실패했습니다 |
| E-9001 | 처리되지 않은 오류가 발생했습니다 |

### 10.3 botocore 예외 매핑

| botocore | 앱 코드 |
|---|---|
| `EndpointConnectionError` | E-2001 |
| `SSLError` | E-2002 |
| `ProxyConnectionError` | E-2003 |
| `ConnectTimeoutError` / `ReadTimeoutError` | E-2004 |
| `InvalidAccessKeyId` / `SignatureDoesNotMatch` | E-2005 |
| `AccessDenied` | E-2006 |
| `NoSuchBucket` | E-3001 |
| `NoSuchKey` / `404` | E-3007 |

### 10.4 사용자 노출 방식

오류 대화상자에 `[E-3004] 업로드에 실패했습니다` 형태로 **코드를 항상 표시**한다.
"로그 폴더 열기" 버튼을 함께 둔다.

---

## 11. 자동 업데이트

repo가 public이므로 **토큰이 필요 없다.** 관련 설정 항목도 없다.

### 11.1 흐름

1. 앱 시작 후 3초 뒤 백그라운드로
   `GET https://api.github.com/repos/yuchoi-bb/UP-obs/releases/latest`
2. `tag_name`(예: `v1.3.0`)과 내장 `__version__` 비교
3. 신버전이면 상단에 배너: `v1.3.0 사용 가능 — 지금 받기`
4. 클릭 → 캐시 폴더로 exe 다운로드 (진행률 표시)
5. `updater.cmd` 생성 후 실행 → 본체 종료

### 11.2 updater.cmd 동작

단일 exe는 실행 중 자기 자신을 덮어쓸 수 없다. 헬퍼 스크립트가 처리한다.

1. 부모 PID가 사라질 때까지 대기 (최대 30초)
2. 기존 exe를 `.bak`으로 이동
3. 새 exe를 원래 경로로 이동
4. 새 exe 실행
5. 자기 자신 삭제

실패 시 `.bak`을 되돌리고 **E-5004**를 로그에 남긴다.

### 11.3 버전 정합성

`__version__`은 빌드 시 태그에서 주입한다.
태그와 내장 버전이 어긋나면 배너가 무한 반복되므로 반드시 일치시킬 것.

### 11.4 실패 정책

업데이트 체크 실패(E-5001)는 **조용히 로그만 남긴다.** 대화상자를 띄우지 않는다.
메뉴에 "업데이트 수동 확인"을 두고, 이때는 결과를 명시적으로 보여준다.

---

## 12. 빌드 / CI

### 12.1 GitHub Actions

- 트리거: `v*` 태그 push
- 러너: `windows-latest`
- 단계: checkout → Python setup → pip install → 버전 주입 → PyInstaller → `gh release create`
- 산출물: `UP-obs-v{version}.exe`

빌드 소요는 5~8분 예상 (PySide6 특성상 캐시로도 크게 줄지 않음).

### 12.2 PyInstaller 옵션

```
--onefile --windowed --name UP-obs --icon assets/app.ico
--exclude-module PySide6.QtWebEngineCore   # 용량 절감
```

예상 크기 60~90MB, 기동 3~5초.

### 12.3 알려진 이슈

`--onefile` 빌드는 백신 오탐이 잦다. 사내 배포 시 예외 등록이 필요할 수 있다.

---

## 13. 디렉토리 구조 (제안)

```
UP-obs/
├─ main.py
├─ app/
│  ├─ version.py          # __version__ (빌드 시 주입)
│  ├─ errors.py           # 에러 카탈로그 + 로깅
│  ├─ config.py           # 프로파일 + DPAPI
│  ├─ s3client.py         # boto3 래퍼 + 예외 매핑
│  ├─ permissions.py      # guard(), 모드 상태
│  ├─ updater.py          # 릴리즈 조회 + updater.cmd
│  └─ ui/
│     ├─ main_window.py
│     ├─ settings_dialog.py
│     ├─ tree_panel.py
│     ├─ object_table.py
│     └─ transfer_worker.py
├─ assets/app.ico
├─ requirements.txt
└─ .github/workflows/release.yml
```

---

## 14. 구현 순서

컨텍스트 관리를 위해 단계별로 진행하고, 각 단계 종료 시 `/compact` 한다.

| 단계 | 범위 | 검증 방법 |
|---|---|---|
| 1 | `errors.py`, `config.py`, `s3client.py` | CLI 스크립트로 list/get/put 확인 |
| 2 | 메인 윈도우, 트리, 목록 | 실제 MinIO 조회 |
| 3 | 드래그앤드롭 양방향 | 탐색기와 상호 확인 |
| 4 | 권한 모델 + guard | 일반 모드에서 우회 시도 테스트 |
| 5 | 업데이트 + Actions | 태그 push → 배너 → 교체 |

---

## 15. 미결 사항

- [ ] 앱 아이콘 (`assets/app.ico`) 제작 또는 확보
- [ ] `DA-share/` 하위의 실제 구조 확인 (트리 초기 표시 검증용)
- [ ] 사내 백신 정책상 미서명 exe 실행 가능 여부 확인
- [ ] 향후 MinIO 계정 분리(IAM 정책 기반) 적용 시점
