# 인증된 읽기 전용 HTTPS 조회

구현일: 2026-11-15. 외부 공개용 코드와 로컬 검증을 완료했다. 실제 Cloudflare 계정·도메인·Access 애플리케이션 값이 제공되지 않아 공개 터널 생성, DNS 연결, 실제 Access 로그인 및 Pickle 접속은 수행하지 않았다.

## 연결 구조

```text
Pickle 브라우저 → Cloudflare Access 로그인 → Cloudflare Tunnel
                                               ↓
                                PC 127.0.0.1:8766 읽기 전용 서버
                                               ↓ 읽기만
                                      data/remote/summary.json
                                               ↑ 원자적 게시
                  로컬 게시 프로세스 ← SQLite 읽기 전용 연결

기존 로컬 관리 화면 127.0.0.1:8765 → 정책 관리·작업 실행·카카오톡
```

원격 서버에는 DB 경로나 수집기 호출 코드가 없다. 별도 게시 프로세스가 사용자가 선택한 정책만 최소 요약으로 만든다. 원격 포트의 경로는 `/`, `/style.css`, `/api/summary` 세 개뿐이며 GET/HEAD만 허용한다. 원격 서버를 띄우는 것만으로 카카오톡 점검이 시작되지 않는다.

공유 항목: 정책별 사용자가 지정한 별칭, 자동 점검 대상 여부, 최근 작업 상태, 최근 24시간 감지 건수, 마지막 감지 시각, 마지막 수집 성공 시각. 요약 생성 시각은 수집 성공 시각과 다르다.

제외 항목: 원래 정책 이름·톡방 이름·정책 ID·키워드·발신자·대화 본문·TXT·로컬 파일 경로·오류 원문·로컬 관리 API 토큰·감사 로그. 감지의 문맥을 바탕으로 Pickle이 대응을 판단하는 기능은 이 단계에 포함하지 않는다.

## 로컬 준비

루트 PowerShell에서 의존성 설치 후, 기존 정책을 조회한다. 이 명령은 터널이나 카카오톡을 조작하지 않는다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-demo.txt
.\.venv\Scripts\python.exe kakao-mention-watch\kakao-mention-watch\remote_control.py list-policies
Copy-Item -LiteralPath remote.example.json -Destination remote.local.json
Copy-Item -LiteralPath cloudflared.example.yml -Destination cloudflared.local.yml
```

`remote.local.json`은 Git 제외 대상이다. 다음 값을 실제 값으로 변경한다.

| 항목 | 값 |
|---|---|
| `public_origin` | 경로·포트 없는 정확한 HTTPS 주소, 예: `https://monitor.자신의도메인` |
| `team_domain` | 자신의 Access 팀 도메인, 예: `my-team.cloudflareaccess.com` |
| `audience` | Access 애플리케이션의 AUD 태그(64자리 16진수) |
| `allowed_emails` | 실제로 접속을 허용할 개인 이메일 목록. 와일드카드 불가 |
| `shared_policies` | 아래처럼 실제 정책 ID와 외부 표시용 별칭을 지정. 기본 빈 목록은 아무 정책도 공유하지 않음 |
| `enabled` | 나머지 설정이 완료된 뒤 `true`로 변경. 기본값 `false` |

```json
"shared_policies": [
  {"id": "목록에서_복사한_실제_32자리_ID", "alias": "모니터링 A"}
]
```

별칭에는 민감한 이름이나 실제 톡방 이름을 넣지 않는 것을 권장한다. 이 설정은 관리 화면의 정책을 생성·수정하지 않는다.

설정 검사 및 읽기 전용 원본 실행:

```powershell
.\.venv\Scripts\python.exe kakao-mention-watch\kakao-mention-watch\remote_control.py validate --config remote.local.json
.\start-remote.ps1
```

`start-remote.ps1`은 PowerShell 7 이상에서 실행한다. 게시 프로세스를 숨겨 실행하고, 원격 조회 서버를 foreground로 실행한다. Ctrl+C로 종료하면 게시 프로세스도 종료한다. `cloudflared`는 자동으로 실행하지 않는다. 실행 정책이 스크립트를 차단하면 정책을 완화하지 않고 두 Python 명령을 별도 창에서 직접 실행한다.

```powershell
.\.venv\Scripts\python.exe kakao-mention-watch\kakao-mention-watch\share_snapshot.py --config remote.local.json --data data\demo --output data\remote\summary.json --watch
.\.venv\Scripts\python.exe kakao-mention-watch\kakao-mention-watch\remote_server.py --config remote.local.json --snapshot data\remote\summary.json --audit-log data\remote\access.log
```

그냥 `http://127.0.0.1:8766`을 열면 Host/인증 검증으로 차단되는 것이 정상이다. 테스트를 위해 인증을 끄는 옵션이나 URL 토큰은 제공하지 않는다.

## Cloudflare 측 설정 — 사용자 계정에서 수행

1. Cloudflare 계정과 사용할 도메인을 준비한다. 공식 배포 경로에서 `cloudflared`를 설치한다. 이 작업에서 자동 설치하거나 로그인하지 않았다.
2. **터널을 실행하거나 DNS 경로를 공개하기 전에** 해당 정확한 호스트명을 대상으로 Self-hosted Access 애플리케이션을 만든다.
3. 허용 정책은 `allowed_emails`와 일치하는 개별 이메일만 포함한다. `Everyone` 또는 `Bypass` 규칙을 두지 않는다. 다른 하위 경로에 인증 예외를 만들지 않는다. IdP의 MFA/패스키를 사용할 수 있으면 적용한다. 초기 로그인은 Pickle의 브라우저에서 사용자가 직접 수행하고, Pickle이 실제로 그 로그인 세션을 사용할 수 있는지 확인한다.
4. Access 세션은 1시간 이하로 설정한다. 로컬은 기본적으로 발급 후 1시간이 지난 토큰을 거부한다. 만료 후에는 재인증이 필요하다. 이를 불편하다고 인증 해제나 영구 토큰으로 대체하지 않는다.
5. 로컬 관리형 named Tunnel을 생성하고, `cloudflared.local.yml`의 UUID·credentials-file·hostname·teamName·audTag를 실제 값으로 바꾼다. 앱의 `public_origin`·`team_domain`·`audience`와 반드시 같아야 한다.
6. 파일의 서비스는 **`http://127.0.0.1:8766`만** 지정한다. **8765 관리 포트를 연결하지 않는다.** 다른 모든 호스트·경로를 전달하는 catch-all 대신 마지막 `http_status:404`를 유지한다.
7. 공식 CLI 절차로 DNS를 해당 터널에 연결하고 실행한다.

```powershell
# Cloudflare 계정 로그인 및 터널 생성은 사용자가 수행한다.
cloudflared tunnel login
cloudflared tunnel create kakao-summary
# 위에서 받은 실제 UUID/credential 경로를 cloudflared.local.yml에 기록한 후:
cloudflared tunnel --config .\cloudflared.local.yml ingress validate
cloudflared tunnel route dns kakao-summary monitor.YOUR_DOMAIN
cloudflared tunnel --config .\cloudflared.local.yml run kakao-summary
```

예시 호스트와 파일을 그대로 실행하지 않는다. `credentials-file`은 저장소 밖의 개인 경로에 두며 파일·터널 토큰·Access JWT·세션 쿠키를 채팅이나 Git에 복사하지 않는다. 도메인 연결 방식이 다른 경우 공식 문서에 맞춰 조정한다. 인증 없는 Quick Tunnel로 관리 페이지를 임시 공개하는 우회는 사용하지 않는다.

## 차단 및 복구

즉시 공유 중지:

```powershell
.\.venv\Scripts\python.exe kakao-mention-watch\kakao-mention-watch\remote_control.py disable --config remote.local.json
```

서버는 매 요청마다 로컬 설정을 읽으므로, 이후 요청은 차단된다. 이어서 `cloudflared`를 실행한 창에서 Ctrl+C로 터널도 끈다. 단, 이미 내려받은 정보나 처리 중이던 응답을 회수할 수는 없다. 새로 공유할 때에는 설정과 인증 정책을 확인하고 `enabled=true`로 변경한다.

- 이메일 허용 목록에서 계정을 제거하면 다음 요청부터 차단한다.
- 공유 정책 선택이 바뀌면 이전 요약의 선택 해시가 맞지 않아 새 게시 전까지 503을 반환한다. 같은 별칭을 다른 정책에 재사용해도 이전 정책의 요약이 전달되지 않는다.
- 요약이 5분 이상 오래되거나 파일이 없거나 형식이 잘못되면 503으로 차단한다. 게시 프로세스의 `data/remote/publisher.stderr.log`를 확인한다.
- 키 조회 장애 시 유효한 캐시 키는 최대 5분 사용하며, 캐시가 없거나 만료됐으면 차단한다. 알 수 없는 `kid`에 대한 키 갱신은 최대 30초에 한 번 수행한다.

## 보호 장치 및 한계

적용: RS256 고정, 설정에 고정한 issuer/AUD, 서명·exp·iat·sub·email 검증, 개별 이메일 허용 목록, 토큰 길이 제한, 임의 JWK URL 거부, HTTPS JWKS URL 고정·리다이렉트 거부·크기/시간 제한, 최대 8개 요청 스레드·소켓 5초 타임아웃·전역 요청량 제한, 정확한 Host/Origin, GET 전용 경로 허용 목록, HTML escaping, script 없는 페이지·엄격한 CSP, no-store·referrer 제한, 최대 크기·유효기간이 있는 요약, 인증 값/대화가 없는 회전 접근 로그.

기존 관리 서버는 `CF-*` 헤더가 있는 요청도 거부하여 Cloudflare를 관리 포트에 잘못 연결했을 때의 노출을 줄인다. 이는 별도 인증을 대신하지 않으며 임의의 다른 프록시로 8765를 공개해도 안전하다는 뜻이 아니다.

이 구조는 **OS 수준 격리가 아니다.** 서버를 같은 Windows 사용자로 실행하므로 원격 프로세스 자체에 코드 실행 취약점이 생기면 그 계정의 파일에 접근할 수 있다. 위험을 더 줄이려면 전용 PC/계정에서 원격 조회 프로세스에 요약·설정·로그에 필요한 권한만 주는 추가 운영 구성이 필요하다. 이 작업에서 Windows 권한·방화벽·보안 설정은 변경하지 않았다.

서명된 토큰 검증은 실시간 세션 폐기 조회가 아니다. 탈취 토큰은 로컬 허용 목록 삭제·공유 중지·토큰 만료 등의 조건 전까지 재사용될 수 있다. Cloudflare와 Pickle을 통해 별칭·건수·활동 시각이 외부 처리된다는 점도 남는다. HTTPS는 Cloudflare에서 종료되며 Cloudflare가 처리한 요청을 PC로 전달한다. 사용자 브라우저에서 PC까지 중계자에게도 내용이 보이지 않는 종단간 암호화라고 설명하지 않는다.

## 검증

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s kakao-mention-watch\kakao-mention-watch -p 'test_*.py' -v
```

새 보안 테스트는 임시 RSA 키·가상 대화를 사용한다. 실제 Cloudflare 인증을 우회하도록 제품에 넣은 테스트 모드는 없다. 테스트 항목: 정상 서명, 위조·만료·알고리즘 혼동·다른 issuer/AUD/계정·누락 claims·키 조회 실패·갱신 제한, 공유 선택/스키마/신선도, 민감 데이터 제외, HTML 이스케이프, HTTP 인증·호스트·Origin·쓰기 메서드·다른 API 차단, 로그 비밀 제외, 관리 포트 CF 요청 차단.

2026-11-15 로컬 검증: 기존 기능을 포함한 자동 테스트 **27개 통과**, Python 문법 검사 및 PowerShell 시작 스크립트 구문 검사 통과. 별도 loopback 미리보기에서 가상 정책 2개로 조회 화면의 렌더링을 확인했고, 미리보기 서버는 종료했다. 이 미리보기는 제품의 인증을 끈 서버가 아니라 가상 HTML/CSS만 제공한 테스트용 파일 서버다.

이 테스트는 외부 침투 테스트나 안전성 보증이 아니다. 실제 연결 후에는 비로그인/다른 계정/만료 세션으로 차단을 확인하고, Pickle에서 페이지 조회가 가능한지 확인해야 한다. 카카오톡 자동 수집의 기존 포커스·대화 연결 및 운영정책 확인 사항도 계속 적용된다.

## 공식 근거

- [Cloudflare Access JWT 검증](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/): 원본 서버에서 서명 검증, 계정별 JWKS 및 키 회전.
- [터널 원본 설정](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/origin-parameters/): `httpHostHeader`, `access.required`, `teamName`, `audTag`.
- [로컬 관리형 터널 생성](https://developers.cloudflare.com/tunnel/features/locally-managed/create-local-tunnel/): 계정 로그인·터널·DNS·실행 절차.
