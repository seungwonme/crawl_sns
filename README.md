# SNS 크롤러

Playwright 기반 SNS 플랫폼 크롤링 도구

## 🚀 주요 기능

- **Threads 크롤링**: Meta의 Threads 플랫폼에서 게시글 수집
- **자동 로그인**: Instagram 계정으로 Threads 자동 로그인
- **세션 관리**: Storage State 기반 재로그인 방지
- **디버그 모드**: 실시간 브라우저 확인 및 스크린샷 저장
- **다양한 User-Agent**: 모바일/데스크톱 User-Agent 지원

## 📦 설치

```bash
# 가상환경 생성 및 활성화
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# 또는
.venv\Scripts\activate  # Windows

# 패키지 설치
uv pip install -r requirements.txt

# Playwright 브라우저 설치
playwright install
```

## ⚙️ 환경 설정

`.env` 파일을 생성하고 다음 내용을 추가:

```bash
# Threads 로그인 정보
THREADS_USERNAME=your_instagram_username
THREADS_PASSWORD=your_instagram_password

# 선택적 설정
THREADS_USER_AGENT=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36
THREADS_DEBUG_MODE=false
THREADS_DEBUG_SCREENSHOT_PATH=./data/debug_screenshots
```

## 🎯 사용법

### 기본 크롤링

```bash
# 기본 5개 게시글 수집
python main.py threads

# 10개 게시글 수집
python main.py threads --count 10

# 출력 파일 지정
python main.py threads --count 5 --output my_threads.json
```

### 🐛 디버그 모드

로그인이 안 될 때나 문제를 진단할 때 사용:

```bash
# 디버그 모드로 실행
python main.py threads --debug

# 또는 짧은 옵션
python main.py threads -d
```

**디버그 모드 특징:**

- 브라우저 창이 표시됨 (headless=false)
- 개발자 도구 자동 열림
- 각 단계별 스크린샷 자동 저장 (`./data/debug_screenshots/`)
- 사용자 입력 대기 (수동 확인 가능)
- 페이지의 모든 버튼 정보 출력
- 상세한 오류 로그

## 🔧 문제 해결

### 로그인 버튼을 찾을 수 없는 경우

1. **디버그 모드로 실행**:

   ```bash
   python main.py threads --debug
   ```

2. **User-Agent 확인**: 환경 변수에서 데스크톱 User-Agent 사용 확인

3. **수동 확인**: 디버그 모드에서 브라우저가 열리면 수동으로 로그인 시도

### 주요 로그인 버튼 선택자들

코드에서 다음 선택자들을 순서대로 시도합니다:

- `button:has-text("Continue with Instagram")`
- `button:has-text("Log in with Instagram")`
- `a:has-text("Log in")`
- `button:has-text("Log in")`
- `[data-testid="loginButton"]`
- 기타 다양한 패턴...

## 📊 출력 형식

```json
{
  "metadata": {
    "total_posts": 5,
    "crawled_at": "2025-01-01T12:00:00",
    "platform": "threads"
  },
  "posts": [
    {
      "platform": "threads",
      "author": "username",
      "content": "게시글 내용...",
      "timestamp": "2시간",
      "url": "https://threads.net/...",
      "likes": 42,
      "comments": 5,
      "shares": 2
    }
  ]
}
```

## 🔍 디버그 정보

디버그 모드에서는 다음 정보들이 저장됩니다:

### 스크린샷 파일명 패턴

- `HHMMSS_00_initial_page.png` - 초기 페이지
- `HHMMSS_01_no_login_button_attempt_N.png` - 로그인 버튼 없음
- `HHMMSS_02_before_login_click_attempt_N.png` - 로그인 클릭 전
- `HHMMSS_03_after_login_click_attempt_N.png` - 로그인 클릭 후
- `HHMMSS_04_instagram_login_page_attempt_N.png` - Instagram 로그인 페이지
- `HHMMSS_05_credentials_entered_attempt_N.png` - 계정 정보 입력 후
- `HHMMSS_06_after_submit_attempt_N.png` - 로그인 제출 후
- `HHMMSS_07_login_success_attempt_N.png` - 로그인 성공
- `HHMMSS_08_login_failed_attempt_N.png` - 로그인 실패
- `HHMMSS_09_timeout_attempt_N.png` - 타임아웃
- `HHMMSS_10_error_attempt_N.png` - 오류 발생

## 🚨 주의사항

- Instagram 계정 정보는 안전하게 관리하세요
- 과도한 크롤링은 플랫폼 이용약관에 위배될 수 있습니다
- 디버그 모드는 개발/테스트 용도로만 사용하세요
