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

from ..models import Post
from .base import BaseCrawler

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
        self.session_path = Path("data/reddit_session.json")
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

    async def _login(self, page: Page) -> bool:
        """Reddit 로그인"""
        try:
            typer.echo("🔑 Reddit 로그인 중...")
            await page.goto("https://www.reddit.com/login/", wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)

            # 사용자명 입력
            username_input = await page.wait_for_selector("#loginUsername", timeout=10000)
            if username_input and self.username:
                await username_input.fill(self.username)

            # 비밀번호 입력
            password_input = await page.wait_for_selector("#loginPassword", timeout=5000)
            if password_input and self.password:
                await password_input.fill(self.password)

            # 로그인 버튼 클릭
            login_button = await page.wait_for_selector('button[type="submit"]', timeout=5000)
            if login_button:
                await login_button.click()

            # 로그인 완료 대기
            try:
                await page.wait_for_url("https://www.reddit.com/", timeout=15000)
                typer.echo("✅ Reddit 로그인 성공!")
                return True
            except PlaywrightTimeoutError:
                # 대안: 로그인 후 홈페이지로 이동했는지 확인
                current_url = page.url
                if "reddit.com" in current_url and "login" not in current_url:
                    typer.echo("✅ Reddit 로그인 성공!")
                    return True
                else:
                    typer.echo("❌ Reddit 로그인 실패")
                    return False

        except Exception as e:
            typer.echo(f"❌ 로그인 중 오류: {e}")
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
        """현재 페이지의 게시글 수집"""
        posts = []

        # Reddit 게시글 선택자들 (우선순위 순)
        post_selectors = [
            'div[data-testid="post-container"]',
            "article",
            'div[data-click-id="body"]',
            ".Post",
        ]

        post_elements = None
        for selector in post_selectors:
            post_elements = await page.query_selector_all(selector)
            if post_elements:
                break

        if not post_elements:
            return posts

        for element in post_elements:
            try:
                post_data = await self._extract_post_data(element)
                if post_data:
                    posts.append(post_data)
            except Exception as e:
                if self.debug_mode:
                    typer.echo(f"   ⚠️ 게시글 추출 오류: {e}")
                continue

        return posts

    async def _extract_post_data(self, element) -> Optional[Dict[str, Any]]:
        """게시글 데이터 추출"""
        try:
            # 기본 정보 추출
            author = await self._extract_author(element)
            content = await self._extract_content(element)
            timestamp = await self._extract_timestamp(element)
            url = await self._extract_url(element)
            interactions = await self._extract_interactions(element)

            # 최소 요구사항 확인
            if not author or not content:
                return None

            post_data = {
                "author": author,
                "content": content,
                "timestamp": timestamp,
                "url": url,
                **interactions,
            }

            return post_data

        except Exception as e:
            if self.debug_mode:
                typer.echo(f"   ⚠️ 게시글 데이터 추출 오류: {e}")
            return None

    async def _extract_author(self, element) -> str:
        """작성자 추출"""
        author_selectors = [
            '[data-testid="post_author_link"]',
            'a[href*="/user/"]',
            ".author",
            '[data-click-id="user"]',
        ]

        for selector in author_selectors:
            try:
                author_element = await element.query_selector(selector)
                if author_element:
                    author_text = await author_element.inner_text()
                    if author_text:
                        # Reddit 사용자명 정제 (u/username -> username)
                        author_text = author_text.strip()
                        if author_text.startswith("u/"):
                            author_text = author_text[2:]
                        return author_text
            except:
                continue

        return "Unknown"

    async def _extract_content(self, element) -> str:
        """게시글 내용 추출"""
        content_selectors = [
            '[data-testid="post-content"]',
            ".RichTextJSON-root",
            '[data-click-id="text"]',
            'div[data-adclicklocation="title"]',
            ".title",
            "h3",
        ]

        for selector in content_selectors:
            try:
                content_element = await element.query_selector(selector)
                if content_element:
                    content_text = await content_element.inner_text()
                    if content_text:
                        return content_text.strip()
            except:
                continue

        # 대안: 전체 텍스트에서 추출
        try:
            full_text = await element.inner_text()
            lines = [line.strip() for line in full_text.split("\n") if line.strip()]

            # 첫 번째 의미있는 라인을 제목으로 사용
            for line in lines:
                if len(line) > 10 and not line.startswith(("u/", "r/", "•")):
                    return line[:500]  # 500자로 제한
        except:
            pass

        return ""

    async def _extract_timestamp(self, element) -> str:
        """게시 시간 추출"""
        timestamp_selectors = [
            "time",
            '[data-testid="post_timestamp"]',
            'a[data-click-id="timestamp"]',
        ]

        for selector in timestamp_selectors:
            try:
                time_element = await element.query_selector(selector)
                if time_element:
                    # datetime 속성 우선
                    datetime_attr = await time_element.get_attribute("datetime")
                    if datetime_attr:
                        return datetime_attr

                    # 텍스트 내용
                    time_text = await time_element.inner_text()
                    if time_text:
                        return time_text.strip()
            except:
                continue

        return ""

    async def _extract_url(self, element) -> Optional[str]:
        """게시글 URL 추출"""
        url_selectors = [
            'a[data-click-id="comments"]',
            'a[href*="/comments/"]',
            '[data-testid="post-content"] a',
        ]

        for selector in url_selectors:
            try:
                url_element = await element.query_selector(selector)
                if url_element:
                    href = await url_element.get_attribute("href")
                    if href:
                        if href.startswith("/"):
                            return f"https://www.reddit.com{href}"
                        elif href.startswith("https://"):
                            return href
            except:
                continue

        return None

    async def _extract_interactions(self, element) -> Dict[str, Optional[int]]:
        """상호작용 정보 추출"""
        interactions: Dict[str, Optional[int]] = {
            "likes": None,
            "comments": None,
            "shares": None,
        }

        try:
            # 업보트 수 추출
            upvote_selectors = [
                '[data-testid="vote-arrows"] button[aria-label*="upvote"]',
                'button[aria-label*="upvote"]',
                ".upvotes",
                '[data-click-id="upvote"]',
            ]

            for selector in upvote_selectors:
                try:
                    upvote_element = await element.query_selector(selector)
                    if upvote_element:
                        aria_label = await upvote_element.get_attribute("aria-label")
                        if aria_label:
                            upvotes = self._parse_number_from_text(aria_label)
                            if upvotes is not None:
                                interactions["likes"] = upvotes
                                break

                        # 대안: 텍스트에서 추출
                        upvote_text = await upvote_element.inner_text()
                        if upvote_text:
                            upvotes = self._parse_number_from_text(upvote_text)
                            if upvotes is not None:
                                interactions["likes"] = upvotes
                                break
                except:
                    continue

            # 댓글 수 추출
            comment_selectors = [
                'a[data-click-id="comments"]',
                'button[aria-label*="comment"]',
                '[data-testid="comment-count"]',
            ]

            for selector in comment_selectors:
                try:
                    comment_element = await element.query_selector(selector)
                    if comment_element:
                        # aria-label에서 추출
                        aria_label = await comment_element.get_attribute("aria-label")
                        if aria_label:
                            comments = self._parse_number_from_text(aria_label)
                            if comments is not None:
                                interactions["comments"] = comments
                                break

                        # 텍스트에서 추출
                        comment_text = await comment_element.inner_text()
                        if comment_text:
                            comments = self._parse_number_from_text(comment_text)
                            if comments is not None:
                                interactions["comments"] = comments
                                break
                except:
                    continue

        except Exception as e:
            if self.debug_mode:
                typer.echo(f"   ⚠️ 상호작용 추출 오류: {e}")

        return interactions

    def _parse_number_from_text(self, text: str) -> Optional[int]:
        """텍스트에서 숫자 추출 (K, M 단위 지원)"""
        if not text:
            return None

        # 숫자 + K/M 패턴 찾기
        patterns = [
            r"(\d+\.?\d*)\s*[kK]",  # 1.2k, 15k
            r"(\d+\.?\d*)\s*[mM]",  # 1.5m, 2m
            r"(\d+)",  # 순수 숫자
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    number = float(match.group(1))
                    if "k" in text.lower():
                        return int(number * 1000)
                    elif "m" in text.lower():
                        return int(number * 1000000)
                    else:
                        return int(number)
                except:
                    continue

        return None

    async def _scroll_for_more_posts(self, page: Page):
        """더 많은 게시글을 위한 스크롤"""
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)

            # 추가 스크롤
            await page.evaluate("window.scrollBy(0, 500)")
            await page.wait_for_timeout(1000)

            # 네트워크 완료 대기
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except PlaywrightTimeoutError:
                pass

        except Exception as e:
            typer.echo(f"   ⚠️ 스크롤 중 오류: {e}")

    async def _load_session(self, page: Page) -> bool:
        """저장된 세션 로드"""
        try:
            if self.session_path.exists():
                await page.context.storage_state(path=str(self.session_path))

                # 세션 유효성 확인
                await page.goto("https://www.reddit.com/", wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)

                # 로그인 상태 확인
                try:
                    user_menu = await page.query_selector('[data-testid="user-menu"]')
                    if user_menu:
                        typer.echo("✅ 저장된 Reddit 세션 로드 성공")
                        return True
                except:
                    pass

            return False

        except Exception as e:
            typer.echo(f"⚠️ 세션 로드 실패: {e}")
            return False

    async def _save_session(self, page: Page):
        """현재 세션 저장"""
        try:
            # 데이터 디렉토리 생성
            self.session_path.parent.mkdir(exist_ok=True)

            # 세션 상태 저장
            await page.context.storage_state(path=str(self.session_path))
            typer.echo("💾 Reddit 세션 저장 완료")

        except Exception as e:
            typer.echo(f"⚠️ 세션 저장 실패: {e}")

    async def _save_debug_html(self, page: Page, filename: str):
        """디버그용 HTML 저장"""
        try:
            debug_dir = Path("data/debug_screenshots")
            debug_dir.mkdir(exist_ok=True)

            html_content = await page.content()
            html_path = debug_dir / filename

            with open(html_path, "w", encoding="utf-8") as f:
                f.write(html_content)

            typer.echo(f"🐛 디버그 HTML 저장: {html_path}")

        except Exception as e:
            typer.echo(f"⚠️ 디버그 HTML 저장 실패: {e}")
