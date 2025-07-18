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

    async def _login(self, page: Page) -> bool:
        """Reddit 로그인"""
        try:
            typer.echo("🔑 Reddit 로그인 중...")
            await page.goto("https://www.reddit.com/login/", wait_until="domcontentloaded")

            # 페이지 로드 대기
            await page.wait_for_timeout(2000)

            typer.echo("   - 사용자명 입력 시도...")
            username_input = page.get_by_role("textbox", name="Email or username")
            await username_input.fill(self.username)
            typer.echo(f"   ✅ 사용자명 입력 완료: {self.username}")

            typer.echo("   - 비밀번호 입력 시도...")
            password_input = page.get_by_role("textbox", name="Password")
            await password_input.fill(self.password)
            typer.echo("   ✅ 비밀번호 입력 완료")

            typer.echo("   - 로그인 버튼 활성화 대기...")
            login_button = page.get_by_role("button", name="Log In")

            # 로그인 버튼이 활성화될 때까지 기다리기
            await login_button.wait_for(state="visible", timeout=5000)

            # 버튼이 활성화되었는지 확인
            for _ in range(10):  # 최대 5초 대기
                is_enabled = await login_button.is_enabled()
                if is_enabled:
                    break
                await page.wait_for_timeout(500)

            if not await login_button.is_enabled():
                typer.echo("   ❌ 로그인 버튼이 활성화되지 않음")
                if self.debug_mode:
                    await self._save_debug_html(page, "reddit_login_button_disabled.html")
                return False

            typer.echo("   - 로그인 버튼 클릭 시도...")
            await login_button.click()
            typer.echo("   🔄 로그인 버튼 클릭됨")

            typer.echo("   - 로그인 성공 확인 중...")
            try:
                # 성공: 메인 페이지로 리디렉션될 때까지 기다립니다 (최대 15초).
                await page.wait_for_url("https://www.reddit.com/", timeout=15000)
                typer.echo("✅ Reddit 로그인 성공!")
                return True
            except PlaywrightTimeoutError:
                # 실패: 오류 메시지가 나타나는지 확인합니다.
                error_message = page.locator('text="Invalid username or password."')
                if await error_message.is_visible():
                    typer.echo("❌ Reddit 로그인 실패: 잘못된 사용자 이름 또는 비밀번호")
                else:
                    typer.echo("❌ Reddit 로그인 실패: 알 수 없는 오류")
                if self.debug_mode:
                    await self._save_debug_html(page, "reddit_login_failed.html")
                return False

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

        # 실제 Reddit 구조에 따라 article 태그 사용
        try:
            post_containers = page.locator("article")
            count = await post_containers.count()

            if count > 0:
                typer.echo(f"   🔎 {count}개 게시글 (article) 발견")

                elements = await post_containers.all()

                for element in elements:
                    post_data = await self._extract_post_data(element)
                    if post_data:
                        all_posts.append(post_data)

            else:
                typer.echo("   ❌ 게시글 컨테이너를 찾을 수 없음")
                if self.debug_mode:
                    await self._save_debug_html(page, "reddit_no_posts_found.html")

        except Exception as e:
            typer.echo(f"   ❌ 게시글 수집 중 오류: {e}")
            if self.debug_mode:
                await self._save_debug_html(page, "reddit_collection_error.html")

        return all_posts

    async def _extract_post_data(self, element) -> Optional[Dict[str, Any]]:
        """게시글 요소에서 데이터 추출"""
        try:
            author = await self._extract_author(element)
            content = await self._extract_content(element)

            # 디버그 로그 추가
            if self.debug_mode:
                typer.echo(f"   🔍 추출 데이터: author='{author}', content='{content[:50]}...'")

            # 저자나 콘텐츠 중 하나라도 있으면 유효한 게시글로 판단 (조건 완화)
            if not author and not content:
                if self.debug_mode:
                    typer.echo("   ⚠️ 유효하지 않은 게시글 건너뜀")
                return None

            interactions = await self._extract_interactions(element)

            post_data = {
                "author": author or "Unknown",
                "content": content or "No title",
                "timestamp": await self._extract_timestamp(element),
                "url": await self._extract_url(element),
                "likes": interactions.get("likes", 0),
                "comments": interactions.get("comments", 0),
                "shares": None,  # Reddit은 공유 수를 직접 표시하지 않음
            }

            if self.debug_mode:
                typer.echo(
                    f"   ✅ 게시글 데이터 추출 성공: {post_data['author']} - {post_data['content'][:30]}..."
                )

            return post_data
        except Exception as e:
            if self.debug_mode:
                typer.echo(f"   ❌ 게시글 데이터 추출 실패: {e}")
            return None

    async def _extract_author(self, element) -> str:
        """게시글에서 작성자 추출 - 실제 Reddit 구조에 맞춘 개선"""
        try:
            # Reddit 구조에서 서브레딧 정보 추출 (r/subreddit 형태)
            subreddit_selectors = [
                'a[href*="/r/"]',  # r/subreddit 링크
                'link[href*="/r/"]',  # generic link 형태
            ]

            for selector in subreddit_selectors:
                try:
                    subreddit_element = await element.query_selector(selector)
                    if subreddit_element:
                        href = await subreddit_element.get_attribute("href")
                        if href and "/r/" in href:
                            # /r/subreddit 형태에서 서브레딧명 추출
                            subreddit_name = href.split("/r/")[-1].split("/")[0]
                            if subreddit_name:
                                return f"r/{subreddit_name}"
                except Exception:
                    continue

            # 서브레딧을 찾을 수 없으면 텍스트에서 직접 찾기
            text_content = await element.inner_text()
            if "r/" in text_content:
                subreddit_match = re.search(r"r/([a-zA-Z0-9_]+)", text_content)
                if subreddit_match:
                    return f"r/{subreddit_match.group(1)}"

        except Exception:
            pass
        return "Unknown"

    async def _extract_content(self, element) -> str:
        """게시글에서 콘텐츠(제목) 추출 - 실제 Reddit 구조에 맞춘 개선"""
        try:
            # 1. article 태그의 aria-label 속성 확인 (가장 정확한 방법)
            try:
                article_aria_label = await element.get_attribute("aria-label")
                if article_aria_label and len(article_aria_label) > 3:
                    return article_aria_label.strip()
            except Exception:
                pass

            # 2. Reddit 구조에서 제목은 heading 태그에 있음 (level=2)
            title_selectors = [
                'heading[level="2"]',  # 정확한 heading 태그
                "h2",  # 일반적인 h2 태그
                "h3",  # 대체용 h3 태그
            ]

            for selector in title_selectors:
                try:
                    title_element = await element.query_selector(selector)
                    if title_element:
                        title_text = (await title_element.inner_text()).strip()
                        if title_text and len(title_text) > 3:
                            return title_text
                except Exception:
                    continue

            # 3. 제목을 찾을 수 없으면 링크 텍스트에서 찾기
            link_selectors = [
                'a[href*="/comments/"]',  # 댓글 링크
                'a[href*="/r/"]',  # 서브레딧 링크
            ]

            for selector in link_selectors:
                try:
                    link_element = await element.query_selector(selector)
                    if link_element:
                        link_text = (await link_element.inner_text()).strip()
                        if link_text and not link_text.startswith("r/") and len(link_text) > 3:
                            return link_text
                except Exception:
                    continue

        except Exception:
            pass
        return ""

    async def _extract_timestamp(self, element) -> str:
        """게시글에서 타임스탬프 추출 - 실제 Reddit 구조에 맞춘 개선"""
        try:
            # Reddit 구조에서 시간 정보는 time 태그에 있음
            timestamp_selectors = [
                "time",  # 실제 time 태그
                'span[class*="timestamp"]',  # 대체용 선택자
                'span[class*="time"]',  # 시간 관련 span
            ]

            for selector in timestamp_selectors:
                try:
                    time_element = await element.query_selector(selector)
                    if time_element:
                        time_text = (await time_element.inner_text()).strip()
                        if time_text:
                            return time_text
                except Exception:
                    continue

            # 시간을 찾을 수 없으면 텍스트에서 패턴 찾기
            text_content = await element.inner_text()
            if text_content:
                # "X hr. ago", "X min. ago", "X days ago" 등의 패턴 찾기
                time_patterns = [
                    r"(\d+)\s+(hr|hour|hours)\.?\s+ago",
                    r"(\d+)\s+(min|minute|minutes)\.?\s+ago",
                    r"(\d+)\s+(day|days)\.?\s+ago",
                    r"(\d+)\s+(sec|second|seconds)\.?\s+ago",
                ]

                for pattern in time_patterns:
                    match = re.search(pattern, text_content, re.IGNORECASE)
                    if match:
                        return match.group(0)

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
        """게시글에서 상호작용(업보트, 댓글) 데이터 추출 - 실제 Reddit 구조에 맞춘 개선"""
        interactions = {"likes": 0, "comments": 0}

        try:
            # 전체 텍스트 내용 가져오기
            text_content = await element.inner_text()
            if text_content:
                # 1. 업보트 수 추출 - 실제 Reddit 구조에서 패턴 찾기
                upvote_patterns = [
                    r"Upvote\s+(\d+\.?\d*[KM]?)\s+Downvote",  # "Upvote 307 Downvote"
                    r"generic:\s*\"(\d+\.?\d*[KM]?)\"\s+.*Downvote",  # "generic: "307" ... Downvote"
                    r"(\d+\.?\d*[KM]?)\s+Go to comments",  # 때로는 업보트 수가 댓글 전에 나타남
                ]

                for pattern in upvote_patterns:
                    match = re.search(pattern, text_content)
                    if match:
                        interactions["likes"] = self._parse_number_from_text(match.group(1))
                        break

                # 2. 댓글 수 추출 - "Go to comments" 링크에서 숫자 찾기
                comment_patterns = [
                    r"(\d+\.?\d*[KM]?)\s+Go to comments",  # "67 Go to comments"
                    r"link\s+\"(\d+\.?\d*[KM]?)\s+Go to comments\"",  # 링크 내 텍스트
                ]

                for pattern in comment_patterns:
                    match = re.search(pattern, text_content)
                    if match:
                        interactions["comments"] = self._parse_number_from_text(match.group(1))
                        break

                # 3. 대체 방법 - 각 라인에서 숫자 찾기
                if interactions["likes"] == 0 or interactions["comments"] == 0:
                    lines = text_content.split("\n")
                    for i, line in enumerate(lines):
                        line = line.strip()

                        # 업보트 수 찾기
                        if interactions["likes"] == 0:
                            if "Upvote" in line and "Downvote" in line:
                                numbers = re.findall(r"(\d+\.?\d*[KM]?)", line)
                                if numbers:
                                    interactions["likes"] = self._parse_number_from_text(numbers[0])
                            elif line.isdigit() or (re.match(r"^\d+\.?\d*[KM]?$", line)):
                                # 다음 라인이 Downvote인지 확인
                                next_line = lines[i + 1] if i + 1 < len(lines) else ""
                                if "Downvote" in next_line:
                                    interactions["likes"] = self._parse_number_from_text(line)

                        # 댓글 수 찾기
                        if interactions["comments"] == 0 and "Go to comments" in line:
                            numbers = re.findall(r"(\d+\.?\d*[KM]?)", line)
                            if numbers:
                                interactions["comments"] = self._parse_number_from_text(numbers[0])

        except Exception:
            pass

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
