# SNS 크롤링 프로젝트 - 점진적 구현 계획

## 🎯 프로젝트 목표

Playwright를 사용하여 로그인된 브라우저에서 SNS 게시글을 하나씩 점진적으로 크롤링

---

## 📋 Step 1: Threads 크롤링 구현 (현재 단계)

### 1.1 기본 환경 설정

- [x] **pyproject.toml 의존성 추가**

  - [x] playwright 추가
  - [x] pydantic 추가 (데이터 모델)
  - [x] typer 추가 (CLI)

- [x] **기본 프로젝트 구조**
  - [x] `src/` 폴더 생성
  - [x] `main.py` - 통합 CLI 및 크롤러
  - [x] `tests/` 폴더 생성

### 1.2 Threads 크롤러 핵심 구현

- [x] **기본 게시글 모델**

  - [x] Post 클래스 정의 (content, author, timestamp, url)
  - [x] JSON 저장 기능

- [ ] **Threads 크롤러 구현 (Playwright 기반)**

  - [ ] 로그인된 브라우저 세션 사용
  - [ ] DOM 선택자로 피드에서 게시글 추출
  - [ ] 기본 스크롤 처리 (3-5개 게시글)
  - [ ] JSON 파일로 저장

- [x] **CLI 인터페이스**
  - [x] `python main.py threads` 명령어
  - [x] 수집된 게시글 수 출력
  - [x] 저장 위치 출력

### 1.3 테스트 및 검증

- [ ] **기본 테스트**
  - [ ] Playwright MCP를 통한 DOM 구조 분석
  - [ ] 데이터 모델 검증 테스트
  - [ ] 실제 Threads 페이지 테스트 (수동)

---

## 📋 Step 2: LinkedIn 크롤링 추가

### 2.1 LinkedIn 크롤러 구현

- [ ] **LinkedIn Playwright 크롤러 구현**

  - [ ] LinkedIn DOM 구조 분석 (Playwright MCP 사용)
  - [ ] 기본 피드 게시글 추출
  - [ ] 기존 Post 모델 재사용

- [ ] **CLI 확장**
  - [ ] `python main.py linkedin` 명령어 추가
  - [ ] `python main.py all` 명령어 추가 (Threads + LinkedIn)

### 2.2 공통 기능 리팩토링

- [ ] **BaseCrawler 클래스 생성**
  - [ ] 공통 로직 추출 (세션 관리, 저장)
  - [ ] Threads, LinkedIn 크롤러가 상속

---

## 📋 Step 3: X (Twitter) 크롤링 추가

### 3.1 X 크롤러 구현

- [ ] **X Playwright 크롤러 구현**

  - [ ] X DOM 구조 분석 (Playwright MCP 사용)
  - [ ] 타임라인 게시글 추출
  - [ ] 리트윗 기본 처리

- [ ] **CLI 확장**
  - [ ] `python main.py x` 명령어 추가

---

## 📋 Step 4: GeekNews 크롤링 추가

### 4.1 GeekNews 크롤러 구현

- [ ] **GeekNews Playwright 크롤러 구현**
  - [ ] GeekNews 구조 분석 (Playwright MCP 사용)
  - [ ] 게시글 목록 추출

---

## 📋 Step 5: Reddit 크롤링 추가

### 5.1 Reddit 크롤러 구현

- [ ] **Reddit Playwright 크롤러 구현**
  - [ ] Reddit 피드 구조 분석 (Playwright MCP 사용)
  - [ ] 서브레딧 게시글 추출

---

## 📋 Step 6: 개선 및 최적화 (선택적)

### 6.1 성능 개선

- [ ] **병렬 처리**
  - [ ] 여러 플랫폼 동시 크롤링
  - [ ] 비동기 처리 적용

### 6.2 데이터 처리 개선

- [ ] **중복 제거**
  - [ ] 해시 기반 중복 감지
  - [ ] 날짜별 폴더 구조

### 6.3 에러 처리 강화

- [ ] **견고성 개선**
  - [ ] 재시도 로직
  - [ ] 로그인 만료 감지
  - [ ] Rate limiting 대응

---

## 🚀 즉시 시작할 작업 (현재 우선순위)

### 가장 먼저 해야 할 것들:

1. **Playwright 브라우저 설치 및 확인**
2. **Playwright MCP를 통한 Threads DOM 구조 분석**
3. **실제 DOM 선택자로 게시글 추출 로직 구현**
4. **로그인 세션 관리 구현**
5. **main.py에서 실행 테스트**

### 첫 번째 목표:

`python main.py threads` 실행하면 Playwright로 Threads에서 실제 게시글 3-5개 가져와서 JSON 파일로 저장하기

---

## 📝 구현 순서

1. **Step 1 완료** → Threads 크롤링 동작 (Playwright 기반)
2. **Step 2 시작** → LinkedIn 추가
3. **Step 3 시작** → X 추가
4. **Step 4 시작** → GeekNews 추가
5. **Step 5 시작** → Reddit 추가
6. **Step 6** → 필요시 개선

각 Step은 독립적으로 동작하며, 이전 Step이 완료되지 않아도 다음 Step을 시작할 수 있습니다.

## 🔧 기술 스택

- **브라우저 자동화**: Playwright (Chromium)
- **CLI**: Typer
- **데이터 모델**: Pydantic
- **출력 형식**: JSON
- **분석 도구**: Playwright MCP
