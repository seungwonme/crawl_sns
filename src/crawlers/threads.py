"""
@file threads.py
@description Threads 플랫폼 전용 크롤러

이 모듈은 Meta의 Threads 플랫폼에서 게시글을 크롤링하는 기능을 제공합니다.

주요 기능:
1. Threads 메인 피드에서 게시글 수집
2. Instagram 계정을 통한 로그인 지원
3. 작성자, 콘텐츠, 상호작용 정보 추출
4. 모바일 User-Agent를 사용한 접근

핵심 구현 로직:
- Instagram 로그인을 통한 Threads 계정 접근
- 로그인 후 더 많은 게시글과 상호작용 정보 수집
- DOM 구조 분석을 통한 게시글 컨테이너 탐지
- 상호작용 버튼에서 숫자 추출

@dependencies
- playwright.async_api: 브라우저 자동화
- typer: CLI 출력
- .base: 베이스 크롤러 클래스

@see {@link https://threads.net} - Threads 플랫폼
"""

import asyncio
import json
import os
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
from dotenv import load_dotenv
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from ..models import Post
from .base import BaseCrawler

# 환경 변수 로드
load_dotenv()


class ThreadsCrawler(BaseCrawler):
    """
    Threads 플랫폼 전용 크롤러

    Meta의 Threads에서 게시글을 크롤링하는 클래스입니다.
    Instagram 로그인을 통해 더 많은 콘텐츠에 접근할 수 있습니다.

    Features:
    - Storage State 기반 세션 관리 (재로그인 방지)
    - 환경 변수 기반 보안 계정 관리
    - 실제 사용자 행동 시뮬레이션
    - 강건한 오류 처리 및 재시도 로직
    """

    def __init__(self, debug_mode: bool = False):
        # 기본 User-Agent를 데스크톱 Chrome으로 변경 (모바일에서 데스크톱으로)
        default_user_agent = os.getenv(
            "THREADS_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )

        super().__init__(
            platform_name="Threads",
            base_url="https://threads.net",
            user_agent=default_user_agent,
            debug_mode=debug_mode,  # 부모 클래스에 debug_mode 전달
        )

        # 환경 변수 기반 설정
        self.username = os.getenv("THREADS_USERNAME")
        self.password = os.getenv("THREADS_PASSWORD")
        self.session_path = Path(os.getenv("THREADS_SESSION_PATH", "./data/threads_session.json"))
        self.login_timeout = int(os.getenv("THREADS_LOGIN_TIMEOUT", "30000"))
        self.login_retry_count = int(os.getenv("THREADS_LOGIN_RETRY_COUNT", "3"))

        # 디버그 모드 설정 (부모에서 이미 설정되지만 여기서도 명시적으로 설정)
        self.debug_mode = debug_mode or os.getenv("THREADS_DEBUG_MODE", "false").lower() == "true"
        self.debug_screenshot_path = Path(
            os.getenv("THREADS_DEBUG_SCREENSHOT_PATH", "./data/debug_screenshots")
        )

        # 상태 관리
        self.is_logged_in = False
        self.session_storage_state = None

        # 세션 및 디버그 디렉토리 생성
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        if self.debug_mode:
            self.debug_screenshot_path.mkdir(parents=True, exist_ok=True)
            typer.echo(f"🐛 디버그 모드 활성화 - 스크린샷 저장 경로: {self.debug_screenshot_path}")

    async def _crawl_implementation(self, page: Page, count: int) -> List[Post]:
        """
        Threads 플랫폼에서 게시글 크롤링 실행

        Args:
            page (Page): Playwright 페이지 객체
            count (int): 수집할 게시글 수

        Returns:
            List[Post]: 크롤링된 게시글 목록
        """
        posts = []

        # 기존 세션 로드 시도
        await self._load_session(page)

        # Threads 메인 페이지로 이동
        await page.goto(self.base_url, wait_until="networkidle")
        typer.echo(f"✅ 페이지 로드 성공")

        # 로그인 시도 (세션이 유효하지 않은 경우만)
        if not self.is_logged_in:
            # 추가 로그인 상태 확인 (세션 로드가 실패했지만 실제로는 로그인된 경우 대비)
            if await self._verify_login_status(page):
                typer.echo("✅ 이미 로그인된 상태입니다 (세션 확인)")
                self.is_logged_in = True
            else:
                await self._attempt_login(page)

        # 페이지 로드 추가 대기
        await page.wait_for_timeout(3000)

        # 점진적 게시글 추출 (스크롤 중 DOM 요소 제거 문제 해결)
        post_elements = await self._extract_posts_incrementally(page, count)
        typer.echo(f"🔍 총 {len(post_elements)}개의 게시글을 수집했습니다")

        # 각 게시글에서 데이터 추출
        for i, post_data in enumerate(post_elements[:count]):
            try:
                if self._is_valid_post(post_data):
                    post = Post(platform="threads", **post_data)
                    posts.append(post)
                    typer.echo(
                        f"   ✅ 게시글 {len(posts)}: @{post_data['author']} - {post_data['content'][:50]}..."
                    )
                else:
                    typer.echo(
                        f"   ⚠️  게시글 {i+1}: 데이터 부족 - author={post_data.get('author')}, content_len={len(str(post_data.get('content', '')))}"
                    )

            except Exception as e:
                typer.echo(f"   ❌ 게시글 {i+1} 파싱 중 오류: {e}")
                continue

        return posts

    async def _load_session(self, page: Page) -> bool:
        """
        저장된 세션 상태를 로드합니다 (Storage State 기반)

        Args:
            page (Page): Playwright 페이지 객체

        Returns:
            bool: 세션 로드 성공 여부
        """
        try:
            if self.session_path.exists():
                typer.echo("🔄 기존 세션 로드 중...")

                # Storage State 로드
                with open(self.session_path, "r") as f:
                    storage_state = json.load(f)

                # 브라우저 컨텍스트에 Storage State 적용
                await page.context.add_cookies(storage_state.get("cookies", []))

                # Local Storage 적용 (SecurityError 방지)
                if storage_state.get("origins"):
                    for origin in storage_state["origins"]:
                        if origin.get("localStorage"):
                            for item in origin["localStorage"]:
                                try:
                                    await page.evaluate(
                                        f"localStorage.setItem('{item['name']}', '{item['value']}')"
                                    )
                                except Exception:
                                    # localStorage 접근 오류 무시
                                    pass

                # 세션 유효성 확인을 위해 페이지 로드
                await page.goto(self.base_url, wait_until="networkidle")

                # 로그인 상태 확인 (더 정확한 방법 사용)
                if await self._verify_login_status(page):
                    self.is_logged_in = True
                    typer.echo("✅ 기존 세션으로 로그인 성공!")
                    return True
                else:
                    typer.echo("⚠️ 기존 세션이 만료됨")
                    # 만료된 세션 파일 삭제
                    if self.session_path.exists():
                        self.session_path.unlink()
                    return False
            else:
                typer.echo("ℹ️ 저장된 세션이 없음")
                return False

        except Exception as e:
            typer.echo(f"⚠️ 세션 로드 중 오류: {e}")
            if self.debug_mode:
                typer.echo(f"   디버그: {e}")
            # 오류 발생 시 세션 파일 삭제
            if self.session_path.exists():
                self.session_path.unlink()
            return False

    async def _save_session(self, page: Page) -> bool:
        """
        현재 세션 상태를 Storage State로 저장합니다

        Args:
            page (Page): Playwright 페이지 객체

        Returns:
            bool: 세션 저장 성공 여부
        """
        try:
            # Storage State 추출
            storage_state = await page.context.storage_state()

            # 세션 파일에 저장
            with open(self.session_path, "w") as f:
                json.dump(storage_state, f, indent=2)

            typer.echo(f"💾 세션이 {self.session_path}에 저장됨")
            return True

        except Exception as e:
            typer.echo(f"⚠️ 세션 저장 중 오류: {e}")
            if self.debug_mode:
                typer.echo(f"   디버그: {e}")
            return False

    async def _attempt_login(self, page: Page) -> bool:
        """
        Instagram 계정을 통한 Threads 로그인 시도 (모범 사례 적용)

        Features:
        - 환경 변수 기반 계정 정보 사용
        - 실제 사용자 행동 시뮬레이션 (랜덤 지연, 타이핑 시뮬레이션)
        - 강건한 오류 처리 및 재시도 로직
        - 다단계 인증 대비
        - Storage State 자동 저장
        - 디버그 모드 지원

        Returns:
            bool: 로그인 성공 여부
        """
        # 환경 변수에서 계정 정보 확인
        if not self.username or not self.password:
            typer.echo("⚠️ 환경 변수에 계정 정보가 없음 (.env 파일 확인 필요)")

            # 사용자에게 계정 정보 요청 (fallback)
            self.username = typer.prompt("Instagram 사용자명")
            self.password = typer.prompt("Instagram 비밀번호", hide_input=True)

        # 디버그 모드: 초기 페이지 상태 확인
        await self._debug_screenshot(page, "00_initial_page")
        await self._debug_show_available_buttons(page)

        for attempt in range(self.login_retry_count):
            try:
                typer.echo(f"🔐 로그인 시도 {attempt + 1}/{self.login_retry_count}")

                # 다양한 로그인 버튼 선택자 시도 (실제 HTML 구조 기반으로 개선)
                login_button_selectors = [
                    # 실제 HTML 구조 기반 선택자들 (login.html 분석 결과)
                    'div[role="button"]:has-text("Continue with Instagram")',
                    'div[role="button"] span:has-text("Continue with Instagram")',
                    'div[role="button"]:has(span:has-text("Continue with Instagram"))',
                    # role="button" 속성을 가진 div 중에서 Instagram 텍스트 포함
                    'div[role="button"]:has-text("Instagram")',
                    # Submit 버튼 (login.html에서 발견된 input[type="submit"])
                    'input[type="submit"]',
                    'button[type="submit"]',
                    # 기존 선택자들 (호환성 유지)
                    'button:has-text("Continue with Instagram")',
                    'button:has-text("Log in with Instagram")',
                    'a:has-text("Log in")',
                    'button:has-text("Log in")',
                    'button:has-text("Login")',
                    '[data-testid="loginButton"]',
                    '[data-testid="login-button"]',
                    'button[type="submit"]:has-text("Log")',
                    ".login-button",
                    "#login-button",
                    # 추가 대안 선택자들
                    'div[tabindex="0"]:has-text("Instagram")',
                    'div[tabindex="0"][role="button"]',
                ]

                login_button = None
                found_selector = None

                for selector in login_button_selectors:
                    try:
                        login_button = await page.query_selector(selector)
                        if login_button:
                            found_selector = selector
                            typer.echo(f"✅ 로그인 버튼 발견: {selector}")
                            break
                    except Exception:
                        continue

                if not login_button:
                    typer.echo("❌ 로그인 버튼을 찾을 수 없습니다.")

                    # 디버그 모드: 사용자가 수동으로 확인할 수 있도록 지원
                    if self.debug_mode:
                        await self._debug_screenshot(page, f"01_no_login_button_attempt_{attempt}")
                        typer.echo(f"🌐 현재 페이지 URL: {page.url}")
                        typer.echo(f"📄 현재 페이지 제목: {await page.title()}")
                        await self._debug_show_available_buttons(page)
                        await self._debug_wait_for_user("로그인 버튼을 수동으로 확인해보세요.", 60)

                    # 이미 로그인된 상태인지 확인
                    if await self._verify_login_status(page):
                        typer.echo("ℹ️ 이미 로그인된 상태입니다")
                        self.is_logged_in = True
                        return True

                    if attempt < self.login_retry_count - 1:
                        await page.wait_for_timeout(random.randint(3000, 5000))
                        continue
                    else:
                        return False

                # 디버그 모드: 로그인 버튼 클릭 전 상태
                await self._debug_screenshot(page, f"02_before_login_click_attempt_{attempt}")

                # 실제 사용자 행동 시뮬레이션: 버튼 클릭 전 잠시 대기
                await page.wait_for_timeout(random.randint(1000, 2000))

                # 로그인 버튼 클릭 (버튼 타입에 따라 다른 처리)
                button_tag = await login_button.evaluate("el => el.tagName.toLowerCase()")
                button_type = await login_button.get_attribute("type")

                typer.echo(
                    f"   🔍 버튼 정보: <{button_tag}> type='{button_type}' selector='{found_selector}'"
                )

                if button_tag == "input" and button_type == "submit":
                    # Submit 버튼의 경우 form submit 시도
                    typer.echo("   📝 Submit 버튼 감지 - form submit 시도")

                    # 먼저 사용자명과 비밀번호를 입력해야 할 수도 있음
                    username_input = await page.query_selector(
                        'input[name="username"], input[placeholder*="username"], input[placeholder*="Username"]'
                    )
                    password_input = await page.query_selector(
                        'input[name="password"], input[type="password"]'
                    )

                    if username_input and password_input:
                        typer.echo("   📝 로그인 폼 감지 - 계정 정보 입력")
                        await username_input.fill(self.username)
                        await password_input.fill(self.password)
                        await page.wait_for_timeout(1000)

                    # Submit 버튼 클릭
                    await login_button.click()
                else:
                    # 일반 버튼이나 div[role="button"]의 경우
                    await login_button.click()

                await page.wait_for_timeout(random.randint(2000, 3000))

                # 디버그 모드: 로그인 버튼 클릭 후 상태
                await self._debug_screenshot(page, f"03_after_login_click_attempt_{attempt}")

                # 디버그 모드: 로그인 시도 후 상태
                await self._debug_screenshot(page, f"06_after_submit_attempt_{attempt}")

                # 페이지 변화 확인 - Instagram 로그인 페이지로 이동했는지 또는 직접 로그인 폼인지 확인
                await page.wait_for_timeout(2000)
                current_url = page.url
                typer.echo(f"   🌐 클릭 후 현재 URL: {current_url}")

                if "instagram.com" in current_url or await page.query_selector(
                    'input[name="username"]'
                ):
                    # Instagram 로그인 페이지로 이동했거나 Instagram 스타일 로그인 폼
                    typer.echo("   📱 Instagram 로그인 페이지 감지")

                    # Instagram 로그인 페이지 대기
                    try:
                        await page.wait_for_selector(
                            'input[name="username"]', timeout=self.login_timeout
                        )
                    except PlaywrightTimeoutError:
                        typer.echo("   ⚠️ Instagram 로그인 페이지 로드 타임아웃")
                        continue

                    # 디버그 모드: Instagram 로그인 페이지 로드 확인
                    await self._debug_screenshot(page, f"04_instagram_login_page_attempt_{attempt}")

                    # 사용자명 입력 (타이핑 시뮬레이션)
                    username_input = await page.query_selector('input[name="username"]')
                    if username_input:
                        await username_input.click()
                        await page.wait_for_timeout(random.randint(500, 1000))

                        # 기존 내용 지우기
                        await username_input.fill("")
                        await page.wait_for_timeout(300)

                        # 한 글자씩 타이핑 시뮬레이션
                        for char in self.username:
                            await username_input.type(char, delay=random.randint(50, 150))

                    # 비밀번호 입력 (타이핑 시뮬레이션)
                    password_input = await page.query_selector('input[name="password"]')
                    if password_input:
                        await password_input.click()
                        await page.wait_for_timeout(random.randint(500, 1000))

                        # 기존 내용 지우기
                        await password_input.fill("")
                        await page.wait_for_timeout(300)

                        # 한 글자씩 타이핑 시뮬레이션
                        for char in self.password:
                            await password_input.type(char, delay=random.randint(50, 120))

                    # 디버그 모드: 로그인 정보 입력 완료 후
                    await self._debug_screenshot(page, f"05_credentials_entered_attempt_{attempt}")

                    # 로그인 버튼 클릭 전 잠시 대기 (실제 사용자 행동)
                    await page.wait_for_timeout(random.randint(1000, 2000))

                    # Instagram 로그인 버튼 클릭
                    submit_button = await page.query_selector('button[type="submit"]')
                    if submit_button:
                        await submit_button.click()

                        # 로그인 처리 대기
                        try:
                            # 네트워크 요청 완료 대기
                            await page.wait_for_load_state("networkidle", timeout=15000)

                            # URL 변화 대기 (로그인 성공 시 리다이렉트)
                            await page.wait_for_url("**/threads.net**", timeout=10000)

                        except PlaywrightTimeoutError:
                            typer.echo("   ⚠️ 로그인 처리 중 타임아웃")
                else:
                    # 직접 Threads 로그인이 처리된 경우
                    typer.echo("   🧵 Threads 직접 로그인 시도")

                    # 로그인 처리 대기
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10000)
                    except PlaywrightTimeoutError:
                        typer.echo("   ⚠️ 페이지 로드 타임아웃")

                # 디버그 모드: 최종 로그인 시도 후 상태
                await self._debug_screenshot(page, f"07_final_login_attempt_{attempt}")

                # 다단계 인증 확인
                if await self._handle_two_factor_auth(page):
                    typer.echo("   🔐 다단계 인증 처리 완료")

                # 로그인 후 추가 단계 처리 (Save login info 등)
                await self._handle_post_login_steps(page)

                # 로그인 성공 확인
                await page.wait_for_timeout(3000)

                # 여러 가지 방법으로 로그인 상태 확인
                login_success = await self._verify_login_status(page)

                if login_success:
                    typer.echo("✅ 로그인 성공!")
                    self.is_logged_in = True

                    # 디버그 모드: 로그인 성공 상태
                    await self._debug_screenshot(page, f"08_login_success_attempt_{attempt}")

                    # 세션 저장
                    await self._save_session(page)
                    return True
                else:
                    # 오류 메시지 확인
                    error_message = await self._get_login_error_message(page)
                    if error_message:
                        typer.echo(f"   ❌ 로그인 실패: {error_message}")
                    else:
                        typer.echo("   ❌ 로그인 실패 (원인 불명)")

                    # 디버그 모드: 로그인 실패 상태
                    await self._debug_screenshot(page, f"09_login_failed_attempt_{attempt}")

                    # 재시도 전 대기
                    if attempt < self.login_retry_count - 1:
                        await page.wait_for_timeout(random.randint(3000, 5000))

            except PlaywrightTimeoutError as e:
                typer.echo(f"   ⏱️ 타임아웃: {e}")
                await self._debug_screenshot(page, f"10_timeout_attempt_{attempt}")
                if attempt < self.login_retry_count - 1:
                    await page.wait_for_timeout(random.randint(2000, 4000))

            except Exception as e:
                typer.echo(f"   ❌ 로그인 중 오류: {e}")
                if self.debug_mode:
                    typer.echo(f"   디버그: {e}")
                await self._debug_screenshot(page, f"11_error_attempt_{attempt}")
                if attempt < self.login_retry_count - 1:
                    await page.wait_for_timeout(random.randint(2000, 4000))

        typer.echo(f"❌ {self.login_retry_count}번 시도 후 로그인 실패")
        return False

    async def _handle_two_factor_auth(self, page: Page) -> bool:
        """
        다단계 인증 (2FA) 처리

        Args:
            page (Page): Playwright 페이지 객체

        Returns:
            bool: 2FA 처리 성공 여부
        """
        try:
            # 2FA 코드 입력 필드 확인 (여러 패턴 시도)
            auth_input = await page.query_selector('input[name="verificationCode"]')
            if not auth_input:
                auth_input = await page.query_selector('input[placeholder*="인증"]')
            if not auth_input:
                auth_input = await page.query_selector('input[aria-label*="인증"]')

            if auth_input:
                typer.echo("🔐 다단계 인증 코드 입력 필요")

                # 사용자에게 인증 코드 요청
                auth_code = typer.prompt("Instagram 인증 코드 (6자리)")

                # 인증 코드 입력 (타이핑 시뮬레이션)
                await auth_input.click()
                await page.wait_for_timeout(500)

                for char in auth_code:
                    await auth_input.type(char, delay=random.randint(100, 200))

                # 제출 버튼 클릭
                submit_button = await page.query_selector('button[type="submit"]')
                if submit_button:
                    await page.wait_for_timeout(random.randint(500, 1000))
                    await submit_button.click()

                    # 인증 처리 대기
                    await page.wait_for_timeout(3000)
                    return True

            return False

        except Exception as e:
            typer.echo(f"⚠️ 2FA 처리 중 오류: {e}")
            return False

    async def _verify_login_status(self, page: Page) -> bool:
        """
        로그인 상태 확인 (더 정확한 방법)

        Args:
            page (Page): Playwright 페이지 객체

        Returns:
            bool: 로그인 성공 여부
        """
        try:
            # 방법 1: URL 확인 (로그인 페이지가 아니어야 함)
            current_url = page.url
            if "/login" in current_url:
                return False

            # 방법 2: 로그인 버튼 부재 확인 (정확한 선택자 사용)
            login_button = await page.query_selector(
                'div[role="button"]:has-text("Continue with Instagram")'
            )
            if login_button:
                return False

            # 방법 3: 피드 특정 요소 확인 (로그인된 상태에서만 보이는 요소들)
            # "What's new?" 텍스트가 있는 버튼 (게시글 작성)
            new_post_button = await page.query_selector(
                'div[role="button"]:has-text("What\'s new?")'
            )
            if new_post_button:
                typer.echo(f"   ✅ 로그인 상태 확인: 게시글 작성 버튼 발견")
                return True

            # 방법 4: "Post" 버튼 확인
            post_button = await page.query_selector('div[role="button"]:has-text("Post")')
            if post_button:
                typer.echo(f"   ✅ 로그인 상태 확인: Post 버튼 발견")
                return True

            # 방법 5: "For you" 탭 확인 (로그인된 사용자만 보임)
            for_you_tab = await page.query_selector('text="For you"')
            if for_you_tab:
                typer.echo(f"   ✅ 로그인 상태 확인: For you 탭 발견")
                return True

            # 방법 6: 사용자 프로필 이미지나 링크 확인
            profile_elements = await page.query_selector_all('img[alt*="프로필"], a[href*="/@"]')
            if len(profile_elements) > 2:  # 여러 사용자 프로필이 있으면 피드 상태
                typer.echo(
                    f"   ✅ 로그인 상태 확인: 다수의 프로필 요소 발견 ({len(profile_elements)}개)"
                )
                return True

            typer.echo(f"   ❌ 로그인 상태 확인: 로그인 필요한 상태로 판단")
            return False

        except Exception as e:
            if self.debug_mode:
                typer.echo(f"   ⚠️ 로그인 상태 확인 중 오류: {e}")
            return False

    async def _get_login_error_message(self, page: Page) -> Optional[str]:
        """
        로그인 오류 메시지 추출

        Args:
            page (Page): Playwright 페이지 객체

        Returns:
            Optional[str]: 오류 메시지 (없으면 None)
        """
        try:
            # 일반적인 오류 메시지 선택자들
            error_selectors = [
                '[role="alert"]',
                ".error-message",
                '[data-testid="error"]',
                'div:has-text("잘못된")',
                'div:has-text("오류")',
                'div:has-text("실패")',
                'span:has-text("확인")',
            ]

            for selector in error_selectors:
                error_element = await page.query_selector(selector)
                if error_element:
                    error_text = await error_element.inner_text()
                    if error_text and len(error_text.strip()) > 0:
                        return error_text.strip()

            return None

        except Exception as e:
            if self.debug_mode:
                typer.echo(f"오류 메시지 추출 중 문제: {e}")
            return None

    async def _extract_posts_incrementally(
        self, page: Page, target_count: int
    ) -> List[Dict[str, Any]]:
        """
        점진적 게시글 추출 - 스크롤 중 DOM 요소 제거 문제 해결

        Args:
            page (Page): Playwright 페이지 객체
            target_count (int): 목표 게시글 수

        Returns:
            List[Dict[str, Any]]: 추출된 게시글 데이터 목록
        """
        all_posts = []
        extracted_urls = set()  # 중복 방지용
        max_scroll_attempts = 15  # 스크롤 시도 횟수 증가
        no_new_posts_count = 0  # 새로운 게시글이 없는 연속 횟수

        typer.echo(f"🔄 점진적 추출 시작 - 목표: {target_count}개")

        for scroll_round in range(max_scroll_attempts):
            typer.echo(f"📜 스크롤 라운드 {scroll_round + 1}")

            # 현재 화면의 게시글 요소들 찾기
            current_elements = await self._find_current_post_elements(page)
            typer.echo(f"   현재 DOM에서 {len(current_elements)}개 요소 발견")

            # 현재 요소들에서 데이터 추출
            new_posts_in_round = 0
            for element in current_elements:
                try:
                    post_data = await self._extract_post_data(element)

                    # 중복 체크 (URL 또는 작성자+콘텐츠 조합)
                    post_id = self._generate_post_id(post_data)
                    if post_id not in extracted_urls:
                        if self._is_valid_post(post_data):
                            all_posts.append(post_data)
                            extracted_urls.add(post_id)
                            new_posts_in_round += 1

                            if self.debug_mode:
                                typer.echo(
                                    f"   ✅ 새 게시글 {len(all_posts)}: @{post_data.get('author')} - {post_data.get('content', '')[:30]}..."
                                )

                            # 목표 달성 시 조기 종료
                            if len(all_posts) >= target_count:
                                typer.echo(f"🎯 목표 달성! {len(all_posts)}개 수집 완료")
                                return all_posts
                except Exception as e:
                    if self.debug_mode:
                        typer.echo(f"   ⚠️ 게시글 처리 중 오류: {e}")
                    continue

            typer.echo(
                f"   ➕ 이번 라운드에서 {new_posts_in_round}개 새 게시글 추가 (총 {len(all_posts)}개)"
            )

            # 새로운 게시글이 없으면 카운트 증가
            if new_posts_in_round == 0:
                no_new_posts_count += 1
                if no_new_posts_count >= 3:
                    typer.echo(f"⏹️ 3라운드 연속 새 게시글 없음 - 추출 종료")
                    break
            else:
                no_new_posts_count = 0  # 새 게시글이 있으면 카운트 리셋

            # 목표에 충분히 가까우면 종료
            if len(all_posts) >= target_count * 0.9:  # 90% 이상 달성
                typer.echo(f"🏁 목표의 90% 달성 - 추출 종료")
                break

            # 다음 스크롤을 위한 대기 및 스크롤
            if scroll_round < max_scroll_attempts - 1:  # 마지막 라운드가 아니면 스크롤
                await self._perform_scroll(page)
                await page.wait_for_timeout(3000)  # 스크롤 후 로딩 대기

        typer.echo(f"📊 점진적 추출 완료: {len(all_posts)}개 게시글 수집")
        return all_posts

    async def _find_current_post_elements(self, page: Page) -> List[Any]:
        """현재 DOM에 있는 게시글 요소들을 찾습니다."""
        try:
            # 주요 패턴들로 게시글 컨테이너 찾기
            post_containers = await page.query_selector_all("div.x78zum5.xdt5ytf")

            if not post_containers:
                # 대안 패턴
                post_containers = await page.query_selector_all(
                    'div[data-pressable-container="true"]'
                )

            if not post_containers:
                # 게시글 링크 기반으로 상위 컨테이너 찾기
                post_links = await page.query_selector_all('a[href*="/@"][href*="/post/"]')
                containers = []
                for link in post_links:
                    try:
                        container = await link.evaluate_handle(
                            """(element) => {
                                let current = element;
                                for (let i = 0; i < 6; i++) {
                                    if (current.parentElement) {
                                        current = current.parentElement;
                                        if (current.querySelector('a[href*="/@"]:not([href*="/post/"])') &&
                                            current.textContent && current.textContent.length > 50) {
                                            return current;
                                        }
                                    }
                                }
                                return null;
                            }"""
                        )
                        if container:
                            element = container.as_element()
                            if element and element not in containers:
                                containers.append(element)
                    except Exception:
                        continue
                post_containers = containers

            return post_containers

        except Exception as e:
            typer.echo(f"⚠️ 현재 게시글 요소 찾기 실패: {e}")
            return []

    async def _perform_scroll(self, page: Page) -> None:
        """스크롤을 수행합니다."""
        try:
            # 맨 끝으로 바로 스크롤 (더 효율적)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

            # 스크롤 완료 대기
            await page.wait_for_timeout(2000)

        except Exception as e:
            typer.echo(f"⚠️ 스크롤 수행 중 오류: {e}")

    def _generate_post_id(self, post_data: Dict[str, Any]) -> str:
        """게시글의 고유 ID를 생성합니다 (중복 체크용)."""
        # URL이 있으면 URL 사용
        if post_data.get("url"):
            return post_data["url"]

        # URL이 없으면 작성자 + 콘텐츠 조합
        author = post_data.get("author", "")
        content = post_data.get("content", "")
        return f"{author}:{content[:100]}"  # 첫 100자로 제한

    async def _find_post_elements(self, page: Page, count: int) -> List[Any]:
        """실제 HTML 구조 기반으로 게시글 DOM 요소들을 찾습니다."""
        post_elements = []

        # 로그인 상태에 따라 다른 접근 방법 사용
        if self.is_logged_in:
            await self._scroll_to_load_more_posts(page, count)

        try:
            # 피드백 분석: 실제 HTML에서 각 게시글은 div.x78zum5.xdt5ytf로 시작하는 블록
            typer.echo(f"🔍 실제 HTML 구조 기반으로 게시글 컨테이너 찾기")

            # 방법 1: 특정 클래스 패턴으로 게시글 컨테이너 찾기
            post_containers = await page.query_selector_all("div.x78zum5.xdt5ytf")
            typer.echo(f"   div.x78zum5.xdt5ytf 패턴: {len(post_containers)}개 발견")

            if not post_containers:
                # 방법 2: data-pressable-container 속성 활용
                post_containers = await page.query_selector_all(
                    'div[data-pressable-container="true"]'
                )
                typer.echo(f"   data-pressable-container 패턴: {len(post_containers)}개 발견")

            if not post_containers:
                # 방법 3: 게시글 링크 기반으로 상위 컨테이너 찾기
                post_links = await page.query_selector_all('a[href*="/@"][href*="/post/"]')
                typer.echo(f"   게시글 링크: {len(post_links)}개 발견")

                containers = []
                for link in post_links[: count * 2]:
                    try:
                        # 상위 6단계까지 올라가서 게시글 컨테이너 찾기
                        container = await link.evaluate_handle(
                            """(element) => {
                                let current = element;
                                for (let i = 0; i < 6; i++) {
                                    if (current.parentElement) {
                                        current = current.parentElement;
                                        // 게시글 컨테이너 조건: 작성자, 시간, 콘텐츠 모두 포함
                                        if (current.querySelector('a[href*="/@"]:not([href*="/post/"])') &&
                                            current.querySelector('time[datetime]') &&
                                            current.textContent && current.textContent.length > 50) {
                                            return current;
                                        }
                                    }
                                }
                                return null;
                            }"""
                        )

                        if container:
                            element = container.as_element()
                            if element and element not in containers:
                                containers.append(element)

                    except Exception:
                        continue

                post_containers = containers

            # 각 후보 컨테이너 검증
            valid_containers = []
            for container_candidate in post_containers:
                try:
                    # 피드백 분석: 실제 게시글인지 확인 (작성자, 시간, 콘텐츠)

                    # 1. 작성자 확인 (링크 또는 텍스트에서)
                    has_author = False
                    author_link = await container_candidate.query_selector(
                        'a[href*="/@"]:not([href*="/post/"])'
                    )
                    if author_link:
                        has_author = True
                    else:
                        # 텍스트에서 작성자명 패턴 확인
                        text = await container_candidate.inner_text()
                        if text and re.search(
                            r"^[a-zA-Z0-9_.]+$", text.split("\n")[0] if text.split("\n") else ""
                        ):
                            has_author = True

                    # 2. 시간 정보 확인
                    time_element = await container_candidate.query_selector("time[datetime]")
                    has_time = time_element is not None

                    # 시간이 없다면 텍스트에서 시간 패턴 확인
                    if not has_time:
                        text = await container_candidate.inner_text()
                        if text and re.search(r"\d+[hdmws]|\d+\s?(시간|분|일|주)", text):
                            has_time = True

                    # 3. 콘텐츠 확인 (최소한의 텍스트)
                    has_content = False
                    content_spans = await container_candidate.query_selector_all(
                        'span[class*="xi7mnp6"]'
                    )
                    if len(content_spans) > 0:
                        has_content = True
                    else:
                        # 전체 텍스트 길이로 판단
                        text = await container_candidate.inner_text()
                        if (
                            text and len(text.strip()) > 50
                        ):  # 50자 이상의 텍스트가 있으면 콘텐츠로 간주
                            has_content = True

                    # 4. 기본 조건 확인
                    if has_author and (has_time or has_content):
                        # 중복 방지
                        is_duplicate = False
                        for existing in valid_containers:
                            try:
                                existing_text = await existing.inner_text()
                                current_text = await container_candidate.inner_text()

                                # 텍스트 유사도로 중복 체크 (첫 100자 비교)
                                if existing_text and current_text:
                                    existing_sample = existing_text[:100].strip()
                                    current_sample = current_text[:100].strip()
                                    if existing_sample == current_sample:
                                        is_duplicate = True
                                        break
                            except:
                                continue

                        if not is_duplicate:
                            valid_containers.append(container_candidate)
                            if self.debug_mode:
                                typer.echo(
                                    f"   ✅ 유효한 게시글 {len(valid_containers)} 추가 (작성자:{has_author}, 시간:{has_time}, 콘텐츠:{has_content})"
                                )
                            else:
                                typer.echo(f"   ✅ 유효한 게시글 {len(valid_containers)} 추가")

                    if len(valid_containers) >= count:
                        break

                except Exception as e:
                    if self.debug_mode:
                        typer.echo(f"   ⚠️ 컨테이너 검증 중 오류: {e}")
                    continue

            post_elements = valid_containers

        except Exception as e:
            typer.echo(f"❌ 게시글 요소 찾기 중 오류: {e}")

        typer.echo(
            f"🔗 실제 HTML 구조 기반으로 {len(post_elements)}개의 게시글 컨테이너를 찾았습니다"
        )
        return post_elements[:count]

    async def _scroll_to_load_more_posts(self, page: Page, target_count: int) -> None:
        """
        스크롤하여 더 많은 게시글을 로드합니다.

        Args:
            page (Page): Playwright 페이지 객체
            target_count (int): 목표 게시글 수
        """
        try:
            max_scrolls = 10

            for scroll_attempt in range(max_scrolls):
                # 페이지 맨 아래로 스크롤
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(3000)  # 로딩 대기 시간 증가

                # 현재 로드된 게시글 수 확인 (실제 HTML 구조 기반)
                current_posts = await page.query_selector_all("div.x78zum5.xdt5ytf")
                if not current_posts:
                    # 대안 패턴
                    current_posts = await page.query_selector_all(
                        'div[data-pressable-container="true"]'
                    )

                typer.echo(f"   스크롤 {scroll_attempt + 1}: {len(current_posts)}개 게시글 로드됨")

                # 충분한 게시글이 로드되었는지 확인 (목표의 1.5배)
                if len(current_posts) >= target_count * 1.5:
                    break

                # 추가 스크롤이 필요한지 확인 (이전 스크롤과 비교)
                if scroll_attempt > 0:
                    # 이전 스크롤에서 새로운 게시글이 거의 로드되지 않았다면 중단
                    if hasattr(self, "_previous_post_count"):
                        new_posts = len(current_posts) - self._previous_post_count
                        if new_posts < 3:  # 새로 로드된 게시글이 3개 미만이면 중단
                            typer.echo(
                                f"   새로운 게시글 로딩이 부족하여 스크롤 중단 (새 게시글: {new_posts}개)"
                            )
                            break

                self._previous_post_count = len(current_posts)

        except Exception as e:
            typer.echo(f"⚠️  스크롤 중 오류: {e}")

    async def _extract_post_data(self, element) -> Dict[str, Any]:
        """단일 게시글에서 데이터를 추출합니다."""

        # 디버깅: 전체 요소 구조 확인 (디버그 모드에서만)
        if self.debug_mode:
            try:
                full_text = await element.inner_text()
                typer.echo(f"   🔍 전체 요소 텍스트: {full_text[:200]}...")
            except:
                pass

        # 작성자 정보 추출
        author = await self._extract_author(element)

        # 게시글 URL 추출
        post_url = await self._extract_post_url(element)

        # 게시 시간 추출
        timestamp = await self._extract_timestamp(element)

        # 콘텐츠 추출
        content = await self._extract_content(element)

        # 상호작용 정보 추출
        interactions = await self._extract_interactions(element)

        return {
            "author": author,
            "content": content,
            "timestamp": timestamp,
            "url": post_url,
            **interactions,
        }

    async def _extract_author(self, element) -> str:
        """작성자 정보 추출 (완전히 재작성된 로직)"""
        try:
            # 방법 1: 게시글 컨테이너에서 직접 작성자 텍스트 찾기
            full_text = await element.inner_text()
            if full_text:
                lines = full_text.split("\n")

                # 디버그 모드에서 텍스트 분석
                if self.debug_mode:
                    typer.echo(f"   📝 텍스트 분석 (첫 10줄): {lines[:10]}")

                # "For you", "Following", "What's new?", "Post" 등 헤더 텍스트 건너뛰기
                skip_texts = ["For you", "Following", "What's new?", "Post", "Translate", "Sorry,"]

                for line in lines:
                    line = line.strip()

                    # 시간 표시 패턴 체크 (작성자 바로 다음에 나옴)
                    if re.match(r"^\d+[hdmws]$|^\d+\s?(시간|분|일|주).*", line):
                        break

                    # 건너뛸 텍스트가 아니고, 적절한 길이의 텍스트인 경우
                    if (
                        line
                        and len(line) > 2
                        and len(line) < 50
                        and not any(skip in line for skip in skip_texts)
                        and not line.isdigit()
                        and not re.match(
                            r"^\d+[KMB]?$", line
                        )  # 숫자만 있는 라인 제외 (좋아요 수 등)
                        and not "reposted" in line.lower()
                    ):

                        # 잠재적 작성자명인지 확인
                        potential_author = line.strip()

                        # @기호 제거
                        if potential_author.startswith("@"):
                            potential_author = potential_author[1:]

                        # 유효한 사용자명 패턴인지 확인
                        if re.match(r"^[a-zA-Z0-9_.]+$", potential_author):
                            if self.debug_mode:
                                typer.echo(
                                    f"   👤 텍스트에서 발견된 작성자: '{potential_author}' (라인: '{line}')"
                                )
                            return potential_author

            # 방법 2: href 링크에서 추출 (fallback)
            author_links = await element.query_selector_all('a[href*="/@"]:not([href*="/post/"])')

            for author_link in author_links:
                href = await author_link.get_attribute("href")
                if href and "/@" in href and "/post/" not in href:
                    author = href.split("/@")[-1].split("/")[0]

                    if self.debug_mode:
                        link_text = await author_link.inner_text()
                        typer.echo(
                            f"   👤 링크에서 발견된 작성자: '{author}' (링크: {href}, 텍스트: '{link_text[:30]}')"
                        )

                    if len(author) > 1 and author.replace("_", "").replace(".", "").isalnum():
                        return author

        except Exception as e:
            if self.debug_mode:
                typer.echo(f"   ⚠️ 작성자 추출 중 오류: {e}")

        return "Unknown"

    async def _extract_post_url(self, element) -> Optional[str]:
        """게시글 URL 추출"""
        # 시간 링크가 게시글로 연결됨
        time_link = await element.query_selector("time")
        if time_link:
            parent_link = await time_link.query_selector("xpath=..")
            if parent_link:
                href = await parent_link.get_attribute("href")
                if href:
                    if href.startswith("http"):
                        return href
                    else:
                        return f"https://threads.net{href}"
        return None

    async def _extract_timestamp(self, element) -> str:
        """게시 시간 추출"""
        time_element = await element.query_selector("time")
        if time_element:
            time_text = await time_element.inner_text()
            if time_text:
                return time_text.strip()
        return "알 수 없음"

    async def _extract_content(self, element) -> str:
        """피드백 분석에 따른 콘텐츠 추출 - 특정 span 태그 기반"""
        try:
            # 피드백: 콘텐츠는 특정 클래스를 가진 span 안에 있음
            # 예: <span class="x1lliihq x1plvlek xryxfnj x1n2onr6 x1ji0vk5 x18bv5gf xi7mnp6 ...">
            content_spans = await element.query_selector_all('span[class*="xi7mnp6"]')

            content_parts = []
            for span in content_spans:
                text = await span.inner_text()
                text = text.strip()

                # "Translate" 버튼 텍스트 제거
                if "Translate" in text:
                    text = text.split("Translate")[0].strip()

                # 상호작용 수치 제외 (4자 이하 숫자)
                if not (
                    len(text) <= 4
                    and text.replace("K", "")
                    .replace("M", "")
                    .replace("B", "")
                    .replace(".", "")
                    .isdigit()
                ):
                    if text and len(text) > 2:
                        content_parts.append(text)

            # 여러 span에 나뉘어 있을 수 있으므로 조합
            full_content = " ".join(content_parts).strip()

            # 알려진 버튼 텍스트들 제거
            known_button_texts = ["Like", "Comment", "Repost", "Share", "More", "Verified"]
            for btn_text in known_button_texts:
                full_content = full_content.replace(btn_text, "").strip()

            # URL 단축 표시 제거 (예: mazdafitment.com/2025…)
            full_content = re.sub(r"\S+…", "", full_content).strip()

            # 연속된 공백 정리
            full_content = re.sub(r"\s+", " ", full_content).strip()

            return full_content[:500] if full_content else ""

        except Exception as e:
            typer.echo(f"   콘텐츠 추출 중 오류: {e}")
            return ""

    async def _extract_interactions(self, element) -> Dict[str, Optional[int]]:
        """피드백 분석에 따른 상호작용 정보 추출 - SVG aria-label 기반"""
        interactions: Dict[str, Optional[int]] = {"likes": 0, "comments": 0, "shares": 0}

        try:
            # 피드백: SVG의 aria-label을 활용하여 상호작용 찾기

            # 좋아요 (Like)
            like_svg = await element.query_selector('svg[aria-label="Like"]')
            if like_svg:
                try:
                    # 피드백: svg -> ancestor::div[@role='button'] -> span 경로
                    like_button = await like_svg.evaluate_handle(
                        "(svg) => svg.closest('div[role=\"button\"]') || svg.closest('button')"
                    )
                    if like_button:
                        # 피드백: div[class^="xu9jpxn"] > span[class^="x17qophe"] 패턴
                        count_span = await like_button.query_selector(
                            'div[class*="xu9jpxn"] span[class*="x17qophe"]'
                        )
                        if count_span:
                            count_text = await count_span.inner_text()
                            interactions["likes"] = (
                                self._parse_interaction_count(count_text.strip())
                                if count_text
                                else 0
                            )
                            if self.debug_mode:
                                typer.echo(
                                    f"   ✅ Like 추출: {count_text} → {interactions['likes']}"
                                )
                        else:
                            # 대안: 버튼 전체 텍스트에서 숫자 찾기
                            button_text = await like_button.inner_text()
                            numbers = re.findall(r"\d+", button_text)
                            if numbers:
                                interactions["likes"] = int(numbers[0])
                                typer.echo(
                                    f"   ✅ Like 추출 (대안): {button_text} → {interactions['likes']}"
                                )
                except Exception as e:
                    typer.echo(f"   ⚠️ Like 추출 중 오류: {e}")

            # 댓글 (Comment)
            comment_svg = await element.query_selector('svg[aria-label="Comment"]')
            if comment_svg:
                try:
                    comment_button = await comment_svg.evaluate_handle(
                        "(svg) => svg.closest('div[role=\"button\"]') || svg.closest('button')"
                    )
                    if comment_button:
                        count_span = await comment_button.query_selector(
                            'div[class*="xu9jpxn"] span[class*="x17qophe"]'
                        )
                        if count_span:
                            count_text = await count_span.inner_text()
                            interactions["comments"] = (
                                self._parse_interaction_count(count_text.strip())
                                if count_text
                                else 0
                            )
                            if self.debug_mode:
                                typer.echo(
                                    f"   ✅ Comment 추출: {count_text} → {interactions['comments']}"
                                )
                        else:
                            # 댓글은 숫자가 없을 때 span 자체가 없을 수 있음
                            interactions["comments"] = 0
                            typer.echo(f"   ✅ Comment 추출: 숫자 없음 → 0")
                except Exception as e:
                    typer.echo(f"   ⚠️ Comment 추출 중 오류: {e}")

            # 리포스트/공유 (Repost)
            repost_svg = await element.query_selector('svg[aria-label="Repost"]')
            if repost_svg:
                try:
                    repost_button = await repost_svg.evaluate_handle(
                        "(svg) => svg.closest('div[role=\"button\"]') || svg.closest('button')"
                    )
                    if repost_button:
                        count_span = await repost_button.query_selector(
                            'div[class*="xu9jpxn"] span[class*="x17qophe"]'
                        )
                        if count_span:
                            count_text = await count_span.inner_text()
                            interactions["shares"] = (
                                self._parse_interaction_count(count_text.strip())
                                if count_text
                                else 0
                            )
                            if self.debug_mode:
                                typer.echo(
                                    f"   ✅ Repost 추출: {count_text} → {interactions['shares']}"
                                )
                        else:
                            interactions["shares"] = 0
                            typer.echo(f"   ✅ Repost 추출: 숫자 없음 → 0")
                except Exception as e:
                    typer.echo(f"   ⚠️ Repost 추출 중 오류: {e}")

            # Share 버튼 (Repost가 없을 경우)
            if interactions["shares"] == 0:
                share_svg = await element.query_selector('svg[aria-label="Share"]')
                if share_svg:
                    try:
                        share_button = await share_svg.evaluate_handle(
                            "(svg) => svg.closest('div[role=\"button\"]') || svg.closest('button')"
                        )
                        if share_button:
                            count_span = await share_button.query_selector(
                                'div[class*="xu9jpxn"] span[class*="x17qophe"]'
                            )
                            if count_span:
                                count_text = await count_span.inner_text()
                                interactions["shares"] = (
                                    self._parse_interaction_count(count_text.strip())
                                    if count_text
                                    else 0
                                )
                                if self.debug_mode:
                                    typer.echo(
                                        f"   ✅ Share 추출: {count_text} → {interactions['shares']}"
                                    )
                    except Exception as e:
                        typer.echo(f"   ⚠️ Share 추출 중 오류: {e}")

        except Exception as e:
            typer.echo(f"   상호작용 추출 중 오류: {e}")

        return interactions

    def _parse_interaction_count(self, count_str: str) -> int:
        """상호작용 숫자 파싱 (K, M, B 단위 처리)"""
        try:
            count_str = count_str.strip()

            # 숫자만 있는 경우
            if count_str.isdigit():
                return int(count_str)

            # K, M, B 단위 처리
            if count_str.endswith("K"):
                return int(float(count_str[:-1]) * 1000)
            elif count_str.endswith("M"):
                return int(float(count_str[:-1]) * 1000000)
            elif count_str.endswith("B"):
                return int(float(count_str[:-1]) * 1000000000)

            # 정규식으로 숫자 추출
            numbers = re.findall(r"\d+", count_str)
            if numbers:
                return int(numbers[0])

            return 0

        except (ValueError, IndexError):
            return 0

    def _is_valid_post(self, post_data: Dict[str, Any]) -> bool:
        """게시글 데이터가 유효한지 확인 (조건 완화)"""
        content = post_data.get("content", "")
        author = post_data.get("author", "")

        # 조건을 완화: 콘텐츠가 1자 이상이고 작성자가 있으면 유효
        # (이미지 전용 게시글도 수집하기 위해)
        is_valid = bool(
            (content and len(str(content).strip()) >= 1) and author and str(author) != "Unknown"
        )

        # 디버그 모드에서 유효성 검사 정보 출력
        if self.debug_mode:
            typer.echo(
                f"   🔍 유효성 검사: author='{author}', content_len={len(str(content).strip())}, valid={is_valid}"
            )

        return is_valid

    async def _debug_screenshot(self, page: Page, step_name: str) -> None:
        """디버그 모드에서 스크린샷을 저장합니다."""
        if not self.debug_mode:
            return

        try:
            timestamp = datetime.now().strftime("%H%M%S")
            screenshot_path = self.debug_screenshot_path / f"{timestamp}_{step_name}.png"
            await page.screenshot(path=str(screenshot_path))
            typer.echo(f"📸 스크린샷 저장: {screenshot_path}")
        except Exception as e:
            typer.echo(f"⚠️ 스크린샷 저장 실패: {e}")

    async def _debug_wait_for_user(self, message: str, timeout: int = 30) -> None:
        """디버그 모드에서 사용자 입력을 기다립니다."""
        if not self.debug_mode:
            return

        typer.echo(f"🐛 {message}")
        typer.echo(f"   계속하려면 Enter를 누르세요 (또는 {timeout}초 후 자동 진행)...")

        try:
            # 비동기로 사용자 입력 대기 (타임아웃 포함)
            await asyncio.wait_for(asyncio.to_thread(input), timeout=timeout)
        except asyncio.TimeoutError:
            typer.echo(f"   ⏰ {timeout}초 타임아웃 - 자동 진행")
        except:
            pass

    async def _debug_show_available_buttons(self, page: Page) -> None:
        """디버그 모드에서 현재 페이지의 모든 버튼을 표시합니다."""
        if not self.debug_mode:
            return

        try:
            # 모든 클릭 가능한 요소들 찾기
            buttons = await page.query_selector_all(
                'button, input[type="submit"], a[role="button"], div[role="button"], span[role="button"]'
            )
            typer.echo(f"🔍 현재 페이지의 클릭 가능한 요소들 ({len(buttons)}개):")

            for i, button in enumerate(buttons[:15]):  # 최대 15개만 표시
                try:
                    text = await button.inner_text()
                    tag_name = await button.evaluate("el => el.tagName")
                    class_attr = await button.get_attribute("class") or ""
                    type_attr = await button.get_attribute("type") or ""
                    role_attr = await button.get_attribute("role") or ""
                    tabindex_attr = await button.get_attribute("tabindex") or ""

                    typer.echo(f"   {i+1}. <{tag_name.lower()}>")
                    if text.strip():
                        typer.echo(f"       텍스트: '{text.strip()[:80]}'")
                    if role_attr:
                        typer.echo(f"       role: '{role_attr}'")
                    if type_attr:
                        typer.echo(f"       type: '{type_attr}'")
                    if tabindex_attr:
                        typer.echo(f"       tabindex: '{tabindex_attr}'")
                    if class_attr:
                        typer.echo(f"       클래스: '{class_attr[:80]}...' (일부)")
                    typer.echo("")
                except:
                    continue

            # Instagram 관련 텍스트가 있는 요소들 별도 검색
            instagram_elements = await page.query_selector_all('*:has-text("Instagram")')
            if instagram_elements:
                typer.echo(f"📱 'Instagram' 텍스트를 포함한 요소들 ({len(instagram_elements)}개):")
                for i, element in enumerate(instagram_elements[:5]):  # 최대 5개만 표시
                    try:
                        text = await element.inner_text()
                        tag_name = await element.evaluate("el => el.tagName")
                        role_attr = await element.get_attribute("role") or ""
                        typer.echo(
                            f"   {i+1}. <{tag_name.lower()}> role='{role_attr}' - '{text.strip()[:60]}'"
                        )
                    except:
                        continue

        except Exception as e:
            typer.echo(f"⚠️ 요소 정보 수집 실패: {e}")

    async def _handle_post_login_steps(self, page: Page) -> None:
        """로그인 후 추가 단계 처리"""
        try:
            # "Save your login info?" 화면에서 "Save info" 버튼 클릭
            # 다양한 선택자로 시도
            save_info_selectors = [
                'button:has-text("Save info")',
                'button:has-text("Save")',
                'button[type="button"]:has-text("Save")',
            ]

            save_button_found = False
            for selector in save_info_selectors:
                try:
                    save_button = await page.query_selector(selector)
                    if save_button:
                        await save_button.click()
                        typer.echo("✅ 'Save info' 버튼 클릭 - 로그인 정보 저장 완료")
                        save_button_found = True
                        await page.wait_for_timeout(5000)  # 처리 대기 (2초 -> 5초로 증가)
                        break
                except Exception:
                    continue

            # Save info 버튼을 찾지 못한 경우, Not now 버튼 시도
            not_now_button = None
            if not save_button_found:
                not_now_button = await page.query_selector('div[role="button"]:has-text("Not now")')
                if not_now_button:
                    await not_now_button.click()
                    typer.echo("✅ 'Not now' 버튼 클릭 완료")
                    await page.wait_for_timeout(3000)  # 처리 대기 (2초 -> 3초로 증가)

            # 로그인 완료 후 추가 안정화 대기
            if save_button_found or not_now_button:
                typer.echo("   ⏳ 로그인 정보 처리 완료 대기 중...")
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    # 네트워크 대기 실패 시에도 계속 진행
                    pass

        except Exception as e:
            typer.echo(f"⚠️ 로그인 후 추가 단계 처리 중 오류: {e}")
            if self.debug_mode:
                typer.echo(f"   디버그: {e}")
