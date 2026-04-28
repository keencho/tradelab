# tradelab-widget

Tauri-based always-on-top mini widget for /api/my/refresh.

## 빌드/실행

```powershell
# 1) 사전: Rust + cargo (이미 설치됨)
# 2) tauri-cli 설치 (한번만)
cargo install tauri-cli --version "^2"

# 3) 아이콘 생성 (한번만, 256x256 png 한 장 준비)
#    desktop/src-tauri/icons/source.png 에 png 두고:
cargo tauri icon src-tauri/icons/source.png

# 4) 개발 실행
cd desktop
cargo tauri dev

# 5) 릴리즈 빌드 (단일 exe + nsis 인스톨러)
cargo tauri build
# 결과물: src-tauri/target/release/Notepad.exe
#         src-tauri/target/release/bundle/nsis/*.exe
```

## 사용

1. 첫 실행 → 트레이 아이콘만 보임 (작업표시줄에 안 뜸)
2. 트레이 좌클릭 또는 `Ctrl+Shift+X` → 위젯 토글
3. 트레이 우클릭 → "설정" 으로 서버 URL/계정 입력
4. `Ctrl+Shift+H` → 즉시 숨기기 (boss key)
5. 위젯 타이틀바 더블클릭 → 설정 창 열기
6. 위젯 우상단 ◐ 버튼 → 위장 모드 (점만 표시)
7. 종료는 트레이 우클릭 → "종료"

## 서버 변경

`/api/my/refresh` 는 인증 필요 — Basic Auth (`AUTH_USERS` 의 user:pw) 로 호출.
서버측은 추가 변경 불필요 (쿠키 외에 Basic Auth 도 이미 받음).

회사망에서 https://oci-server.com 만 도달 가능하면 됨.
