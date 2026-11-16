# 카카오톡 키워드 언급 감지기

이 문서는 초기 단발 CLI 사용법이다. 정책 관리 MCP·로컬 설정 홈페이지·읽기 전용 외부 조회의 최신 사용법과 구조도는 [프로젝트 README](../../README.md)를 참고한다.

Windows PC 카카오톡의 **이미 열려 있는 별도 대화방 창**에서 대화를 복사하고, 새 메시지 본문에 `두기` 등 키워드가 있으면 JSON으로 반환한다. 실행 주기와 전화/알림은 피클이 맡는다. LLM/API 키나 네트워크 전송은 없다.

## 현재 검증 범위

파서, 신규 범위 비교, SQLite 저장, 미확인 이벤트 재전달, CLI는 자동 테스트로 확인했다. **실제 카카오톡 설치본에 대한 창 복사는 검증하지 않았다.** 아래 `inspect`와 수동 수집 확인이 필요하다. 카카오톡은 버전별로 창 클래스·복사 포맷이 달라질 수 있어 이 코드를 설치 즉시 작동 보장하는 공식 연동으로 취급하면 안 된다.

## 설치

Python 3.11 이상 권장. Windows GUI 의존성 호환성은 3.11/3.12에서 먼저 확인하는 것을 권장한다.

Windows의 **Git Bash**에서 실행한다. WSL/Linux Bash에서는 PC 카톡 창에 접근할 수 없다. 가상환경 활성화 없이 run.sh가 Windows Python을 직접 실행한다.

```bash
cd ~/project_personal/kakao-mention-watch
bash setup.sh
```

현재 작업 폴더에 받은 경우 `bash install-to-personal.sh`을 먼저 실행하면 `~/project_personal/kakao-mention-watch`로 복사한다. 기존 대상 폴더는 덮어쓰지 않는다. `setup.sh`는 의존성을 설치하고 config.json이 없을 때만 예시를 복사한다. Python을 자동으로 찾지 못하면 `PYTHON='/c/경로/python.exe' bash setup.sh`처럼 지정한다.

## 카톡방 설정과 확인

1. PC 카카오톡에 로그인한다. 감시할 방을 별도 창으로 열고, 최신 메시지 위치로 이동한 상태로 최소화하지 않는다.
2. 다음 명령으로 창 제목과 하위 컨트롤 클래스를 확인한다. 메시지 본문은 출력하지 않는다.

```bash
bash run.sh inspect
```

3. `config.json`의 `window_title`에 정확한 방 창 제목, `history_class`에 실제 대화 목록 클래스를 입력한다. 예시의 `EVA_VH_ListControl_Dblclk`는 후보이며 설치본에서 확인해야 한다. **입력창 Edit/RichEdit 클래스는 지정하지 않는다.** 창 제목 또는 목록 컨트롤이 여러 개면 오류로 중단한다. 여러 방은 rooms 배열에 각각 추가한다.
4. 초기 기준점을 저장한다. 첫 실행은 과거 언급을 알리지 않는다.

```bash
bash run.sh check
```

5. 초기화 후 새로 들어온 `두기` 포함 메시지로 다시 `check`한다. `events`에 새 메시지가 나오는지 확인한다. 실제 메시지를 보내는 기능은 이 도구에 없다.
6. [PICKLE_INSTRUCTIONS.md](PICKLE_INSTRUCTIONS.md)를 피클에 제공하고, 같은 PC의 로그인 세션에서 5분마다 `check`를 실행시킨다. 이 프로젝트가 스케줄러나 피클 설정을 자동 등록하지는 않는다.

다른 위치에서 호출해도 기본 설정은 watcher.py 옆의 config.json이다. 상대 경로인 state_file과 file 소스 path는 설정 파일 위치를 기준으로 해석한다.

```bash
bash run.sh --config config.json check
bash run.sh --config config.json pending
bash run.sh --config config.json ack 이벤트ID
```

## 반환 계약

stdout은 UTF-8 JSON 객체 하나다. 정상 감지 여부와 무관하게 종료 코드 0, 수집 실패/일부 방 실패/설정 오류는 2다. 모든 명령에서 status를 확인한다.

```json
{
  "status": "ok",
  "detected": true,
  "events": [{
    "id": "고유한해시",
    "room": "친구 단톡방",
    "keywords": ["두기"],
    "sender": "친구",
    "message": "두기 회의 와줘",
    "date_label": "2026년 11월 14일 토요일",
    "time_label": "오전 8:30",
    "observed_at": "2026-11-13T23:35:00+00:00",
    "context": []
  }],
  "initialized_rooms": [],
  "errors": []
}
```

`events`는 이번 감지분뿐 아니라 **ack하지 않은 대기 이벤트 전체**다. 알림 성공 후 ack해야 다음 실행에서 빠진다. 일부 방이 실패해도 다른 방의 이벤트는 반환한다. 문맥은 앞선 최대 2개 메시지이며 첫 메시지에서는 비어 있다.

키워드는 본문에서만 부분 문자열로 매칭한다. `두기야`도 감지하지만 보낸 사람 이름만 `두기`인 메시지는 감지하지 않는다. 여러 키워드, 대소문자 무시, Unicode NFC 정규화를 지원한다. 의미 추론이나 긴급도 판정은 하지 않는다.

## 동작 조건과 한계

- 전용 PC 또는 다른 작업을 하지 않는 로그인 세션이 적합하다. 수집 중 잠시 카톡 창으로 포커스를 바꿔 대화 목록에 Ctrl+A/C를 보낸다. 화면 잠금·로그아웃·절전·사용자의 동시 입력에서는 수집을 보장하지 않는다. 작업 스케줄러를 쓴다면 '사용자가 로그온할 때만 실행'해야 한다.
- 카톡 창을 활성화하면 읽음 처리에 영향을 줄 수 있다. 클립보드는 OLE 객체로 보관 후 복원한다. 실행 중 다른 앱이 바꾼 클립보드는 복원하지 않는다. OS 클립보드 기록/동기화가 켜져 있다면 복사 내용이 남을 수 있다.
- 화면 OCR이 아니라 대화 목록의 선택/복사 기능을 사용한다. **복사 가능한 로드된 대화만** 읽는다. 과거 내역 자동 스크롤이나 무한 로딩은 구현하지 않았다. 실행 사이에 메시지가 많이 쌓이면 누락 구간이 생길 수 있다. 이전 끝부분의 2~8개 메시지(기존 1개일 때는 1개)가 고유하게 연결되지 않으면 `coverage_gap` 오류를 반환하고 기준점을 유지한다.
- 파서는 `[보낸사람] [시간] 본문` 형식과 한국어 날짜 구분선을 지원한다. 여러 줄 본문을 합친다. 본문 안에 동일한 헤더 형태의 줄이 있으면 경계가 잘못 나뉠 수 있다. 이미지/음성 내용 OCR·전사, 수정/삭제 추적은 지원하지 않는다.
- 공식 메시지 ID를 얻지 못하므로 해시와 주변 문맥으로 중복을 줄인다. 동일 시간·동일 내용 반복, 날짜 정보 누락/변경, 잘린 복사 내역에서는 완전한 중복 판별을 보장하지 않는다.
- 연결이 끊겼을 때 자동으로 새 기준점을 만들지 않는다. 누락 구간을 확인한 뒤 필요한 경우 상태 초기화를 직접 결정한다. DB 삭제는 대기 알림도 삭제한다.
- 로컬 DB에는 이전 메시지 해시와 알림 대상 메시지·문맥이 저장된다. ack한 이벤트도 기록이 남는다. 외부 전송은 피클의 알림 처리 범위에 달려 있다. 이 코드는 카카오의 공식 API 또는 정책상 허용을 보증하는 연동이 아니다.

## 카톡 없는 데모 및 테스트

의존성 설치 없이 Python 표준 라이브러리만으로 가능하다.

```bash
python watcher.py --config config.demo.json check
printf '\n%s\n' '[친구] [오전 8:30] 두기 회의 와줘' >> examples/chat.txt
python watcher.py --config config.demo.json check
python -m unittest -v
```

데모는 `data/demo.sqlite3`를 사용하므로 실제 카톡 감지 상태와 분리된다. file 소스는 텍스트 입력/테스트용이며 카톡 내보내기를 자동 갱신하지 않는다.

## 구현 참고

- [pywinauto HwndWrapper](https://pywinauto.readthedocs.io/en/latest/code/pywinauto.controls.hwndwrapper.html): 창·컨트롤 포커스.
- [Microsoft GetGUIThreadInfo](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getguithreadinfo): 키 입력 전 포커스 검증.
- [pywin32 PythonCOM 소스](https://github.com/mhammond/pywin32/blob/main/com/win32com/src/PythonCOM.cpp): OLE 클립보드 보관·복원.
