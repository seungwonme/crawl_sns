"""
@file reddit.py
@description Reddit 플랫폼 전용 크롤러

이 모듈은 Reddit 플랫폼에서 게시글을 크롤링하는 기능을 제공합니다.

주요 기능:
1. Reddit 피드에서 게시글 수집
2. Reddit 계정을 통한 로그인 지원
3. 작성자, 콘텐츠, 업보트/댓글 정보 추출
4. 세션 관리 (재로그인 방지)
5. 점진적 추출 시스템 (스크롤링, 서브레딧 탐색)

핵심 구현 로직:
- Reddit 로그인을 통한 피드 접근
- 점진적 스크롤링으로 더 많은 게시글 로드
- div[data-testid="post-container"] 기반 게시글 추출
- K/M 단위 업보트 수치 파싱

@dependencies
- playwright.async_api: 브라우저 자동화
- typer: CLI 출력
- .base: 베이스 크롤러 클래스

@see {@link https://www.reddit.com} - Reddit 플랫폼
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
from dotenv import load_dotenv
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from src.crawlers.base import BaseCrawler
from src.models import Post

# 환경 변수 로드
load_dotenv()


class RedditCrawler(BaseCrawler):
    """Reddit 전용 크롤러"""

    def __init__(self, debug_mode: bool = False):
        super().__init__(
            platform_name="reddit", base_url="https://www.reddit.com", debug_mode=debug_mode
        )
        self.username = os.getenv("REDDIT_USERNAME")
        self.password = os.getenv("REDDIT_PASSWORD")
        self.session_path = Path("data/sessions/reddit_session.json")
        self.max_scroll_attempts = 10

        if not self.username or not self.password:
            raise ValueError("REDDIT_USERNAME과 REDDIT_PASSWORD 환경 변수가 필요합니다")

    async def _crawl_implementation(self, page: Page, count: int) -> List[Post]:
        """Reddit 게시글 크롤링 구현"""
        try:
            # 세션 로드 시도
            if not await self._load_session(page):
                # 세션이 없거나 만료된 경우 로그인
                await self._login(page)
                await self._save_session(page)

            # 메인 피드로 이동
            await page.goto("https://www.reddit.com/", wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

            # 게시글 수집
            posts = await self._progressive_post_collection(page, count)

            if self.debug_mode:
                await self._save_debug_html(page, "reddit_posts.html")

            return posts

        except Exception as e:
            typer.echo(f"❌ 크롤링 중 오류 발생: {e}")
            if self.debug_mode:
                await self._save_debug_html(page, "reddit_error.html")
            return []

    async def crawl(self, count: int = 10) -> List[Post]:
        """Reddit 게시글 크롤링 - 베이스 클래스 오버라이드"""
        typer.echo(f"🔴 Reddit 크롤링 시작 (목표: {count}개)")
        return await super().crawl(count)

    async def _fill_login_field(
        self, page: Page, selectors: List[str], value: str, field_name: str
    ) -> bool:
        """로그인 필드 입력 헬퍼 메서드"""
        for selector in selectors:
            try:
                input_field = await page.wait_for_selector(selector, timeout=3000)
                if input_field and value:
                    await input_field.fill(value)
                    typer.echo(f"   ✅ {field_name} 입력 완료")
                    return True
            except PlaywrightTimeoutError:
                continue
        typer.echo(f"   ❌ {field_name} 입력 필드를 찾을 수 없음")
        return False

    async def _click_login_button(self, page: Page) -> bool:
        """로그인 버튼 클릭 헬퍼 메서드"""
        login_button_selectors = [
            'button[type="submit"]',
            'button:has-text("Log In")',
            'button:has-text("Sign In")',
            ".login-button",
            '[data-testid="login-button"]',
        ]

        for selector in login_button_selectors:
            try:
                login_button = await page.wait_for_selector(selector, timeout=3000)
                if login_button:
                    await login_button.click()
                    typer.echo("   🔄 로그인 버튼 클릭됨")
                    return True
            except PlaywrightTimeoutError:
                continue

        typer.echo("   ❌ 로그인 버튼을 찾을 수 없음")
        return False

    async def _verify_login_success(self, page: Page) -> bool:
        """로그인 성공 확인 헬퍼 메서드"""
        try:
            await page.wait_for_function(
                """() => {
                    return window.location.href.includes('reddit.com') &&
                           !window.location.href.includes('login') &&
                           (document.querySelector('[data-testid="user-drawer-button"]') ||
                            document.querySelector('.header-user-dropdown'))
                }""",
                timeout=15000,
            )
            typer.echo("✅ Reddit 로그인 성공!")
            return True
        except PlaywrightTimeoutError:
            current_url = page.url
            if "reddit.com" in current_url and "login" not in current_url:
                typer.echo("✅ Reddit 로그인 성공!")
                return True
            else:
                typer.echo("❌ Reddit 로그인 실패 - URL 확인")
                if self.debug_mode:
                    await self._save_debug_html(page, "reddit_login_failed.html")
                return False

    async def _login(self, page: Page) -> bool:
        """Reddit 로그인"""
        try:
            typer.echo("🔑 Reddit 로그인 중...")
            await page.goto("https://www.reddit.com/login/", wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

            # 사용자명 입력
            username_selectors = [
                "#login-username",
                "#loginUsername",
                'input[name="username"]',
                'input[placeholder*="username" i]',
                'input[type="text"]',
            ]
            if not await self._fill_login_field(
                page, username_selectors, self.username, "사용자명"
            ):
                return False

            # 비밀번호 입력
            password_selectors = [
                "#login-password",
                "#loginPassword",
                'input[name="password"]',
                'input[type="password"]',
            ]
            if not await self._fill_login_field(
                page, password_selectors, self.password, "비밀번호"
            ):
                return False

            # 로그인 버튼 클릭
            if not await self._click_login_button(page):
                return False

            # 로그인 성공 확인
            return await self._verify_login_success(page)

        except Exception as e:
            typer.echo(f"❌ 로그인 중 오류: {e}")
            if self.debug_mode:
                await self._save_debug_html(page, "reddit_login_error.html")
            return False

    async def _progressive_post_collection(self, page: Page, target_count: int) -> List[Post]:
        """점진적 게시글 수집"""
        posts = []
        scroll_attempts = 0

        typer.echo(f"🔄 게시글 수집 시작 (목표: {target_count}개)")

        while len(posts) < target_count and scroll_attempts < self.max_scroll_attempts:
            # 현재 페이지의 게시글 추출
            current_posts = await self._collect_posts(page)

            # 새로운 게시글만 추가
            new_posts_count = 0
            for post_data in current_posts:
                if len(posts) >= target_count:
                    break

                # 중복 확인
                is_duplicate = any(
                    existing_post.url == post_data.get("url")
                    or (
                        existing_post.content == post_data.get("content")
                        and existing_post.author == post_data.get("author")
                    )
                    for existing_post in posts
                )

                if not is_duplicate:
                    post = Post(
                        platform="reddit",
                        author=post_data.get("author", "Unknown"),
                        content=post_data.get("content", ""),
                        timestamp=post_data.get("timestamp", ""),
                        url=post_data.get("url"),
                        likes=post_data.get("likes"),
                        comments=post_data.get("comments"),
                        shares=post_data.get("shares"),
                    )
                    posts.append(post)
                    new_posts_count += 1

            typer.echo(f"   📊 수집 현황: {len(posts)}/{target_count} (+{new_posts_count}개 신규)")

            if len(posts) >= target_count:
                break

            if new_posts_count == 0:
                typer.echo("   ⚠️ 새로운 게시글이 없음")

            # 페이지 스크롤
            await self._scroll_for_more_posts(page)
            scroll_attempts += 1

        typer.echo(f"✅ 총 {len(posts)}개 게시글 수집 완료")
        return posts

    async def _collect_posts(self, page: Page) -> List[Dict[str, Any]]:
        """현재 페이지의 게시글 수집 - 실제 Reddit 구조에 맞춰 개선"""
        all_posts = []
        # 다양한 post_container 선택자
        post_selectors = [
            'div[data-testid="post-container"]',
            "div[data-ad-position]",
            "div.Post",
        ]

        post_container = None
        for selector in post_selectors:
            try:
                post_container = page.locator(selector)
                count = await post_container.count()
                if count > 0:
                    typer.echo(f"   🔎 '{selector}' 선택자로 {count}개 게시글 컨테이너 발견")
                    break
            except Exception:
                continue

        if not post_container or await post_container.count() == 0:
            typer.echo("   ❌ 게시글 컨테이너를 찾을 수 없음")
            if self.debug_mode:
                await self._save_debug_html(page, "reddit_no_posts_found.html")
            return []

        elements = await post_container.all()

        for element in elements:
            post_data = await self._extract_post_data(element)
            if post_data:
                all_posts.append(post_data)

        return all_posts

    async def _extract_post_data(self, element) -> Optional[Dict[str, Any]]:
        """게시글 요소에서 데이터 추출"""
        try:
            author = await self._extract_author(element)
            content = await self._extract_content(element)

            # 저자나 콘텐츠가 없으면 유효한 게시글이 아님
            if not author and not content:
                return None

            interactions = await self._extract_interactions(element)
            return {
                "author": author,
                "content": content,
                "timestamp": await self._extract_timestamp(element),
                "url": await self._extract_url(element),
                "likes": interactions.get("likes"),
                "comments": interactions.get("comments"),
                "shares": None,  # Reddit은 공유 수를 직접 표시하지 않음
            }
        except Exception:
            return None

    async def _extract_author(self, element) -> str:
        """게시글에서 작성자 추출"""
        author_selectors = [
            'a[data-testid="post_author_link"]',
            '[data-testid="post-meta-info"] > span:first-of-type',
            'a[href*="/user/"]',
            'span[class*="author"]',
        ]
        try:
            for selector in author_selectors:
                try:
                    author_element = await element.query_selector(selector)
                    if author_element:
                        return (await author_element.inner_text()).strip()
                except Exception:
                    continue
        except Exception:
            pass
        return "Unknown"

    async def _extract_content(self, element) -> str:
        """게시글에서 콘텐츠(제목) 추출"""
        title_selectors = [
            "h3",
            "h2",
            'div[data-testid="post-title"]',
            'a[data-click-id="body"] > div > h3',
        ]
        try:
            for selector in title_selectors:
                try:
                    title_element = await element.query_selector(selector)
                    if title_element:
                        return (await title_element.inner_text()).strip()
                except Exception:
                    continue
        except Exception:
            pass
        return ""

    async def _extract_timestamp(self, element) -> str:
        """게시글에서 타임스탬프 추출"""
        timestamp_selectors = [
            'span[data-testid="post_timestamp"]',
            'a[data-testid="post_timestamp"]',
            'span[class*="timestamp"]',
        ]
        try:
            for selector in timestamp_selectors:
                try:
                    time_element = await element.query_selector(selector)
                    if time_element:
                        return (await time_element.inner_text()).strip()
                except Exception:
                    continue
        except Exception:
            pass
        return ""

    async def _extract_url(self, element) -> Optional[str]:
        """게시글에서 URL 추출"""
        url_selectors = [
            'a[data-testid="post_title"]',
            'a[data-click-id="body"]',
            'a[href*="/comments/"]',
        ]
        try:
            for selector in url_selectors:
                try:
                    url_element = await element.query_selector(selector)
                    if url_element:
                        href = await url_element.get_attribute("href")
                        if href and href.startswith("/"):
                            return self.base_url + href
                        return href
                except Exception:
                    continue
        except Exception:
            pass
        return None

    async def _extract_interactions(self, element) -> Dict[str, int]:
        """게시글에서 상호작용(업보트, 댓글) 데이터 추출"""
        interactions = {"likes": 0, "comments": 0}

        try:
            # 업보트 추출
            upvote_text = ""
            upvote_elements = await element.query_selector_all(
                '[data-testid="post-content"] > div:last-child > div:first-child > button:first-child > span'
            )
            if upvote_elements:
                upvote_text = await upvote_elements[0].inner_text()
            else:
                # 다른 선택자 시도
                upvote_text_element = await element.query_selector('[id*="vote-arrows"] > div')
                if upvote_text_element:
                    upvote_text = await upvote_text_element.inner_text()

            interactions["likes"] = self._parse_number_from_text(upvote_text)

        except Exception:
            interactions["likes"] = 0

        try:
            # 댓글 수 추출
            comment_text = ""
            comment_element = await element.query_selector('a[data-testid="comment-button"]')
            if comment_element:
                comment_text_span = await comment_element.query_selector("span")
                if comment_text_span:
                    comment_text = await comment_text_span.inner_text()

            interactions["comments"] = self._parse_number_from_text(comment_text)

        except Exception:
            interactions["comments"] = 0

        return interactions

    def _parse_number_from_text(self, text: str) -> int:
        """텍스트에서 숫자 파싱 (예: '1.7k' -> 1700)"""
        if not text:
            return 0
        text = text.lower().strip()
        try:
            if "k" in text:
                num_part = text.replace("k", "").strip()
                return int(float(num_part) * 1000)
            if "m" in text:
                num_part = text.replace("m", "").strip()
                return int(float(num_part) * 1_000_000)

            # 숫자만 있는지 확인
            cleaned_text = re.sub(r"[^\d.]", "", text)
            if cleaned_text:
                return int(float(cleaned_text))

        except (ValueError, TypeError):
            return 0
        return 0

    async def _scroll_for_more_posts(self, page: Page):
        """더 많은 게시글을 로드하기 위해 스크롤"""
        try:
            typer.echo("   📜 페이지 스크롤...")
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            # 스크롤 후 콘텐츠가 로드될 시간을 줌
            await page.wait_for_timeout(3000)
        except Exception as e:
            typer.echo(f"   ⚠️ 스크롤 중 오류 발생: {e}")

    async def _load_session(self, page: Page) -> bool:
        """저장된 세션 로드"""
        if self.session_path.exists():
            typer.echo("💾 저장된 세션 로드 중...")
            try:
                await page.context.storage_state(path=self.session_path)
                # 세션 유효성 검사
                await page.goto(self.base_url, wait_until="domcontentloaded")
                # 로그인 상태를 나타내는 요소 확인
                is_logged_in = await page.is_visible('[data-testid="user-drawer-button"]')
                if is_logged_in:
                    typer.echo("   ✅ 세션 유효함, 로그인 건너뜀")
                    return True
                else:
                    typer.echo("   ⚠️ 세션 만료됨, 재로그인 필요")
                    return False
            except Exception:
                typer.echo("   ❌ 세션 로드 실패, 재로그인 필요")
                return False
        return False

    async def _save_session(self, page: Page):
        """현재 세션 저장"""
        try:
            typer.echo("💾 현재 세션 저장 중...")
            await page.context.storage_state(path=self.session_path)
            typer.echo("   ✅ 세션 저장 완료")
        except Exception as e:
            typer.echo(f"   ❌ 세션 저장 실패: {e}")

    async def _save_debug_html(self, page: Page, filename: str):
        """디버그용 HTML 파일 저장"""
        if self.debug_mode:
            debug_path = Path("debug/reddit")
            debug_path.mkdir(parents=True, exist_ok=True)
            full_path = debug_path / filename
            try:
                content = await page.content()
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
                typer.echo(f"   🐛 디버그 HTML 저장: {full_path}")
            except Exception as e:
                typer.echo(f"   ❌ 디버그 HTML 저장 실패: {e}")
