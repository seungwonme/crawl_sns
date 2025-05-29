"""
@file threads.py
@description Threads 플랫폼 전용 크롤러

이 모듈은 Meta의 Threads 플랫폼에서 게시글을 크롤링하는 기능을 제공합니다.

주요 기능:
1. Threads 메인 피드에서 게시글 수집
2. Instagram 계정을 통한 로그인 지원
3. 작성자, 콘텐츠, 상호작용 정보 추출

핵심 구현 로직:
- Instagram 로그인을 통한 Threads 계정 접근
- DOM 구조 분석을 통한 게시글 컨테이너 탐지
- aria-label 기반 상호작용 버튼 추출

@dependencies
- playwright.async_api: 브라우저 자동화
- typer: CLI 출력
- .base: 베이스 크롤러 클래스

@see {@link https://threads.net} - Threads 플랫폼
"""

import json
import os
import random
import re
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
        """Instagram 계정을 통한 Threads 로그인 시도"""
        if not self.username or not self.password:
            typer.echo("⚠️ 환경 변수에 계정 정보가 없음 (.env 파일 확인 필요)")
            self.username = typer.prompt("Instagram 사용자명")
            self.password = typer.prompt("Instagram 비밀번호", hide_input=True)

        for attempt in range(self.login_retry_count):
            try:
                typer.echo(f"🔐 로그인 시도 {attempt + 1}/{self.login_retry_count}")

                # 로그인 버튼 찾기
                login_button_selectors = [
                    'div[role="button"]:has-text("Continue with Instagram")',
                    'div[role="button"] span:has-text("Continue with Instagram")',
                    'input[type="submit"]',
                    'button[type="submit"]',
                    'button:has-text("Continue with Instagram")',
                    'button:has-text("Log in")',
                ]

                login_button = None
                for selector in login_button_selectors:
                    try:
                        login_button = await page.query_selector(selector)
                        if login_button:
                            break
                    except Exception:
                        continue

                if not login_button:
                    if await self._verify_login_status(page):
                        typer.echo("ℹ️ 이미 로그인된 상태입니다")
                        self.is_logged_in = True
                        return True

                    if attempt < self.login_retry_count - 1:
                        await page.wait_for_timeout(3000)
                        continue
                    else:
                        typer.echo("❌ 로그인 버튼을 찾을 수 없습니다")
                        return False

                # 로그인 버튼 클릭
                await page.wait_for_timeout(random.randint(1000, 2000))
                await login_button.click()
                await page.wait_for_timeout(2000)

                current_url = page.url
                if "instagram.com" in current_url or await page.query_selector(
                    'input[name="username"]'
                ):
                    # Instagram 로그인 페이지에서 계정 정보 입력
                    await page.wait_for_selector(
                        'input[name="username"]', timeout=self.login_timeout
                    )

                    # 사용자명 입력
                    username_input = await page.query_selector('input[name="username"]')
                    if username_input:
                        await username_input.click()
                        await username_input.fill("")
                        await page.wait_for_timeout(300)
                        for char in self.username:
                            await username_input.type(char, delay=random.randint(50, 150))

                    # 비밀번호 입력
                    password_input = await page.query_selector('input[name="password"]')
                    if password_input:
                        await password_input.click()
                        await password_input.fill("")
                        await page.wait_for_timeout(300)
                        for char in self.password:
                            await password_input.type(char, delay=random.randint(50, 120))

                    # 로그인 버튼 클릭
                    await page.wait_for_timeout(random.randint(1000, 2000))
                    submit_button = await page.query_selector('button[type="submit"]')
                    if submit_button:
                        await submit_button.click()

                        try:
                            await page.wait_for_load_state("networkidle", timeout=15000)
                            await page.wait_for_url("**/threads.net**", timeout=10000)
                        except PlaywrightTimeoutError:
                            typer.echo("   ⚠️ 로그인 처리 중 타임아웃")
                else:
                    # 직접 Threads 로그인
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10000)
                    except PlaywrightTimeoutError:
                        typer.echo("   ⚠️ 페이지 로드 타임아웃")

                # 2FA 및 로그인 후 단계 처리
                await self._handle_two_factor_auth(page)
                await self._handle_post_login_steps(page)

                # 로그인 성공 확인
                await page.wait_for_timeout(3000)
                if await self._verify_login_status(page):
                    typer.echo("✅ 로그인 성공!")
                    self.is_logged_in = True
                    await self._save_session(page)
                    return True
                else:
                    typer.echo("   ❌ 로그인 실패")
                    if attempt < self.login_retry_count - 1:
                        await page.wait_for_timeout(random.randint(3000, 5000))

            except PlaywrightTimeoutError:
                typer.echo(f"   ⏱️ 타임아웃")
                if attempt < self.login_retry_count - 1:
                    await page.wait_for_timeout(random.randint(2000, 4000))
            except Exception as e:
                typer.echo(f"   ❌ 로그인 중 오류: {e}")
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
            if self.debug_mode:
                typer.echo(f"📜 스크롤 라운드 {scroll_round + 1}")

            # 현재 화면의 게시글 요소들 찾기
            current_elements = await self._find_current_post_elements(page)
            typer.echo(f"   현재 DOM에서 {len(current_elements)}개 요소 발견")

            # 현재 요소들에서 데이터 추출
            new_posts_in_round = 0
            for element in current_elements:
                try:
                    post_data = await self._extract_post_data(element)
                    post_id = self._generate_post_id(post_data)

                    if post_id not in extracted_urls and self._is_valid_post(post_data):
                        all_posts.append(post_data)
                        extracted_urls.add(post_id)
                        new_posts_in_round += 1

                        if len(all_posts) >= target_count:
                            typer.echo(f"🎯 목표 달성! {len(all_posts)}개 수집 완료")
                            return all_posts
                except Exception:
                    continue

            if self.debug_mode:
                typer.echo(
                    f"   ➕ 이번 라운드에서 {new_posts_in_round}개 새 게시글 추가 (총 {len(all_posts)}개)"
                )

            # 새로운 게시글이 없으면 종료
            if new_posts_in_round == 0:
                no_new_posts_count += 1
                if no_new_posts_count >= 3:
                    break
            else:
                no_new_posts_count = 0

            # 목표 90% 달성시 종료
            if len(all_posts) >= target_count * 0.9:
                break

            # 다음 스크롤
            if scroll_round < max_scroll_attempts - 1:
                await self._perform_scroll(page)
                await page.wait_for_timeout(3000)

        typer.echo(f"📊 점진적 추출 완료: {len(all_posts)}개 게시글 수집")
        return all_posts

    async def _find_current_post_elements(self, page: Page) -> List[Any]:
        """현재 DOM에 있는 게시글 요소들을 찾습니다"""
        try:
            # data 속성 기반으로 게시글 컨테이너 찾기
            post_containers = await page.query_selector_all('div[data-pressable-container="true"]')

            if not post_containers:
                # 대안: 게시글 링크가 있는 상위 컨테이너 찾기
                post_links = await page.query_selector_all('a[href*="/@"][href*="/post/"]')
                containers = []
                for link in post_links:
                    try:
                        container = await link.evaluate_handle(
                            """(element) => {
                                let current = element;
                                for (let i = 0; i < 8; i++) {
                                    if (current.parentElement) {
                                        current = current.parentElement;
                                        if (current.hasAttribute('data-pressable-container') &&
                                            current.querySelector('a[href*="/@"]:not([href*="/post/"])') &&
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
        except Exception:
            return []

    async def _perform_scroll(self, page: Page) -> None:
        """스크롤을 수행합니다"""
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(2000)
        except Exception:
            pass

    def _generate_post_id(self, post_data: Dict[str, Any]) -> str:
        """게시글의 고유 ID를 생성합니다"""
        if post_data.get("url"):
            return post_data["url"]

        author = post_data.get("author", "")
        content = post_data.get("content", "")
        return f"{author}:{content[:100]}"

    async def _extract_post_data(self, element) -> Dict[str, Any]:
        """단일 게시글에서 데이터를 추출합니다"""
        author = await self._extract_author(element)
        post_url = await self._extract_post_url(element)
        timestamp = await self._extract_timestamp(element)
        content = await self._extract_content(element)
        interactions = await self._extract_interactions(element)

        return {
            "author": author,
            "content": content,
            "timestamp": timestamp,
            "url": post_url,
            **interactions,
        }

    async def _extract_author(self, element) -> str:
        """작성자 정보 추출"""
        try:
            # href 링크에서 직접 추출
            author_links = await element.query_selector_all('a[href*="/@"]:not([href*="/post/"])')

            for author_link in author_links:
                href = await author_link.get_attribute("href")
                if href and "/@" in href and "/post/" not in href:
                    author = href.split("/@")[-1].split("/")[0]
                    if len(author) > 1 and author.replace("_", "").replace(".", "").isalnum():
                        return author

            # fallback: 텍스트 분석
            full_text = await element.inner_text()
            if full_text:
                lines = full_text.split("\n")
                skip_texts = [
                    "For you",
                    "Following",
                    "What's new?",
                    "Post",
                    "Translate",
                    "Sorry,",
                    "reposted",
                ]

                for line in lines:
                    line = line.strip()

                    if re.match(r"^\d+[hdmws]$|^\d+\s?(시간|분|일|주).*", line):
                        break

                    if (
                        line
                        and len(line) > 2
                        and len(line) < 50
                        and not any(skip in line.lower() for skip in skip_texts)
                        and not line.isdigit()
                        and not re.match(r"^\d+[KMB]?$", line)
                        and not any(word in line for word in ["Like", "Comment", "Share"])
                    ):

                        potential_author = line.strip()
                        if potential_author.startswith("@"):
                            potential_author = potential_author[1:]

                        if re.match(r"^[a-zA-Z0-9_.]+$", potential_author):
                            return potential_author

        except Exception:
            pass

        return "Unknown"

    async def _extract_post_url(self, element) -> Optional[str]:
        """게시글 URL 추출"""
        time_link = await element.query_selector("time")
        if time_link:
            parent_link = await time_link.query_selector("xpath=..")
            if parent_link:
                href = await parent_link.get_attribute("href")
                if href:
                    return href if href.startswith("http") else f"https://threads.net{href}"
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
        """콘텐츠 추출"""
        try:
            full_text = await element.inner_text()
            if not full_text:
                return ""

            lines = full_text.split("\n")

            # 필터링할 패턴들
            skip_patterns = [
                r"^\d+[hdmws]$",  # 시간 패턴
                r"^\d+\s?(시간|분|일|주)",  # 한국어 시간 패턴
                r"^[a-zA-Z0-9_.]+$",  # 사용자명만 있는 라인
                r"^\d+[KMB]?$",  # 숫자만 있는 라인
                r"^(Like|Comment|Reply|Repost|Share|More|Translate)$",  # 버튼 텍스트
                r"^(For you|Following|What\'s new\?|Post|Sorry,)$",  # 헤더 텍스트
                r"reposted.*ago$",  # 리포스트 정보
            ]

            skip_keywords = ["Translate", "Learn more", "reposted"]
            content_parts = []
            content_started = False

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # 건너뛸 패턴인지 확인
                should_skip = any(re.match(pattern, line) for pattern in skip_patterns)

                if not should_skip:
                    should_skip = any(keyword in line for keyword in skip_keywords)

                # 실제 콘텐츠로 판단되는 조건
                if not should_skip and len(line) > 5:
                    content_started = True
                    content_parts.append(line)
                elif content_started and should_skip:
                    break

            full_content = " ".join(content_parts).strip()
            full_content = re.sub(r"\s+", " ", full_content)  # 연속 공백 정리
            full_content = re.sub(r"\S+…", "", full_content)  # URL 단축 표시 제거

            return full_content[:500] if full_content else ""

        except Exception:
            return ""

    async def _extract_interactions(self, element) -> Dict[str, Optional[int]]:
        """상호작용 정보 추출"""
        interactions: Dict[str, Optional[int]] = {
            "likes": 0,
            "comments": 0,
            "reposts": 0,
            "shares": 0,
        }

        try:
            # aria-label 기반으로 각 상호작용 버튼 찾기
            interaction_types = [
                ("Like", "likes"),
                ("Comment", "comments"),
                ("Reply", "comments"),
                ("Repost", "reposts"),
                ("Share", "shares"),
            ]

            for aria_label, field_name in interaction_types:
                comments_count = interactions.get("comments", 0)
                if field_name == "comments" and comments_count and comments_count > 0:
                    continue  # Comment가 이미 추출되었으면 Reply 건너뛰기

                svg = await element.query_selector(f'svg[aria-label="{aria_label}"]')
                if svg:
                    try:
                        button = await svg.evaluate_handle(
                            "(svg) => svg.closest('div[role=\"button\"]') || svg.closest('button')"
                        )
                        if button:
                            number_text = await button.evaluate(
                                """(button) => {
                                    const spans = button.querySelectorAll('span');
                                    for (let span of spans) {
                                        const text = span.textContent?.trim();
                                        if (text && /^\\d+[KMB]?$/.test(text)) {
                                            return text;
                                        }
                                    }
                                    const buttonText = button.textContent || '';
                                    const numbers = buttonText.match(/\\d+[KMB]?/g);
                                    return numbers ? numbers[0] : '0';
                                }"""
                            )

                            interactions[field_name] = (
                                self._parse_interaction_count(number_text) if number_text else 0
                            )

                    except Exception:
                        pass

        except Exception:
            pass

        return interactions

    def _parse_interaction_count(self, count_str: str) -> int:
        """상호작용 숫자 파싱 (K, M, B 단위 처리)"""
        try:
            count_str = count_str.strip()

            if count_str.isdigit():
                return int(count_str)

            if count_str.endswith("K"):
                return int(float(count_str[:-1]) * 1000)
            elif count_str.endswith("M"):
                return int(float(count_str[:-1]) * 1000000)
            elif count_str.endswith("B"):
                return int(float(count_str[:-1]) * 1000000000)

            numbers = re.findall(r"\d+", count_str)
            if numbers:
                return int(numbers[0])

            return 0
        except (ValueError, IndexError):
            return 0

    def _is_valid_post(self, post_data: Dict[str, Any]) -> bool:
        """게시글 데이터가 유효한지 확인"""
        content = post_data.get("content", "")
        author = post_data.get("author", "")

        return bool(
            (content and len(str(content).strip()) >= 1) and author and str(author) != "Unknown"
        )

    async def _handle_post_login_steps(self, page: Page) -> None:
        """로그인 후 추가 단계 처리"""
        try:
            # "Save your login info?" 화면에서 버튼 클릭
            save_info_selectors = [
                'button:has-text("Save info")',
                'button:has-text("Save")',
                'button[type="button"]:has-text("Save")',
            ]

            save_button_found = False
            not_now_button = None

            for selector in save_info_selectors:
                try:
                    save_button = await page.query_selector(selector)
                    if save_button:
                        await save_button.click()
                        save_button_found = True
                        await page.wait_for_timeout(5000)
                        break
                except Exception:
                    continue

            # Save info 버튼을 찾지 못한 경우, Not now 버튼 시도
            if not save_button_found:
                not_now_button = await page.query_selector('div[role="button"]:has-text("Not now")')
                if not_now_button:
                    await not_now_button.click()
                    await page.wait_for_timeout(3000)

            # 로그인 완료 후 안정화 대기
            if save_button_found or not_now_button:
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass

        except Exception:
            pass
