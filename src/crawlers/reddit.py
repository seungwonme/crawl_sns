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

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer
from dotenv import load_dotenv
from playwright.async_api import Page

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
            login_success = await self._load_session(page)

            if not login_success:
                # 세션이 없거나 만료된 경우 로그인
                login_success = await self._login(page)

                if not login_success:
                    typer.echo("❌ Reddit 로그인 실패로 크롤링을 중단합니다.")
                    return []

                # 로그인 성공한 경우에만 세션 저장
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
            await page.wait_for_timeout(3000)

            # 1. 로그인 폼 입력
            if not await self._fill_login_form(page):
                return False

            # 2. 로그인 버튼 찾기 및 클릭
            login_button = await self._find_login_button(page)
            if not login_button:
                return False

            if not await self._wait_and_click_login_button(page, login_button):
                return False

            # 3. 로그인 성공 확인
            return await self._verify_login_success(page)

        except Exception as e:
            typer.echo(f"❌ 로그인 중 오류: {e}")
            if self.debug_mode:
                await self._save_debug_html(page, "reddit_login_exception.html")
            return False

    async def _fill_login_form(self, page: Page) -> bool:
        """로그인 폼 입력"""
        try:
            typer.echo("   - 사용자명 입력 시도...")
            username_input = page.locator(
                'input#login-username, input[name="username"], input[id="loginUsername"]'
            ).first
            await username_input.wait_for(state="visible", timeout=5000)
            await username_input.fill(self.username)
            typer.echo(f"   ✅ 사용자명 입력 완료: {self.username}")

            typer.echo("   - 비밀번호 입력 시도...")
            password_input = page.locator(
                'input#login-password, input[name="password"], input[id="loginPassword"]'
            ).first
            await password_input.wait_for(state="visible", timeout=5000)
            await password_input.fill(self.password)
            typer.echo("   ✅ 비밀번호 입력 완료")

            # 입력 필드 변경 이벤트 트리거
            await username_input.press("Tab")
            await password_input.press("Tab")
            await page.wait_for_timeout(500)
            return True

        except Exception as e:
            typer.echo(f"   ❌ 로그인 폼 입력 실패: {e}")
            return False

    async def _find_login_button(self, page: Page):
        """로그인 버튼 찾기"""
        typer.echo("   - 로그인 버튼 찾기...")
        login_button_selectors = [
            'button:has-text("Log in")',
            'button:has-text("LOG IN")',
            'button:has-text("Sign in")',
            'button[type="submit"]',
            'fieldset button[class*="button"]',
            'fieldset button[class*="AnimatedForm"]',
            'button[class*="AnimatedForm__submitButton"]',
        ]

        for selector in login_button_selectors:
            try:
                btn = page.locator(selector).first
                if await btn.is_visible():
                    typer.echo(f"   ✅ 로그인 버튼 찾음: {selector}")
                    return btn
            except Exception:
                continue

        typer.echo("   ❌ 로그인 버튼을 찾을 수 없음")
        if self.debug_mode:
            await self._save_debug_html(page, "reddit_no_login_button.html")
        return None

    async def _wait_and_click_login_button(self, page: Page, login_button) -> bool:
        """로그인 버튼 활성화 대기 및 클릭"""
        password_input = page.locator(
            'input#login-password, input[name="password"], input[id="loginPassword"]'
        ).first

        # 버튼이 활성화될 때까지 기다리기
        for i in range(10):  # 최대 5초 대기
            is_enabled = await login_button.is_enabled()
            if is_enabled:
                break
            await page.wait_for_timeout(500)
            if i == 4:  # 2.5초 후 다시 입력 시도
                await password_input.press("Tab")

        if not await login_button.is_enabled():
            typer.echo("   ❌ 로그인 버튼이 활성화되지 않음")
            if self.debug_mode:
                await self._save_debug_html(page, "reddit_login_button_disabled.html")
            return False

        typer.echo("   - 로그인 버튼 클릭 시도...")
        await login_button.click()
        typer.echo("   🔄 로그인 버튼 클릭됨")
        return True

    async def _verify_login_success(self, page: Page) -> bool:
        """로그인 성공 확인"""
        typer.echo("   - 로그인 성공 확인 중...")

        for _ in range(30):  # 최대 15초 대기
            # 메인 페이지로 리디렉션 확인
            current_url = page.url
            if current_url in ["https://www.reddit.com/", "https://www.reddit.com"]:
                typer.echo("✅ Reddit 로그인 성공!")
                return True

            # 로그인된 상태 확인 (사용자 메뉴 버튼)
            if await self._check_user_menu(page):
                typer.echo("✅ Reddit 로그인 성공! (사용자 메뉴 확인)")
                return True

            # 오류 메시지 확인
            error_msg = await self._check_login_error(page)
            if error_msg:
                typer.echo(f"❌ Reddit 로그인 실패: {error_msg}")
                return False

            await page.wait_for_timeout(500)

        typer.echo("❌ Reddit 로그인 실패: 타임아웃")
        if self.debug_mode:
            await self._save_debug_html(page, "reddit_login_timeout.html")
        return False

    async def _check_user_menu(self, page: Page) -> bool:
        """사용자 메뉴 확인"""
        try:
            user_menu = page.locator(
                'button[aria-label*="Expand user menu"], button[id*="USER_DROPDOWN"], div[class*="header-user-dropdown"]'
            ).first
            return await user_menu.is_visible()
        except Exception:
            return False

    async def _check_login_error(self, page: Page) -> Optional[str]:
        """로그인 오류 메시지 확인"""
        error_selectors = [
            'div[class*="error"]',
            'span[class*="error"]',
            'div[class*="AnimatedForm__errorMessage"]',
            ".status-error",
            '[class*="status"][class*="error"]',
        ]

        try:
            for error_selector in error_selectors:
                error_element = page.locator(error_selector).first
                if await error_element.is_visible():
                    error_text = await error_element.inner_text()
                    if self.debug_mode:
                        await self._save_debug_html(page, "reddit_login_error.html")
                    return error_text
        except Exception:
            pass
        return None

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

        # 다양한 선택자 시도
        post_selectors = [
            "shreddit-post",  # 새로운 Reddit 웹 컴포넌트
            "article",  # 일반 article 태그 (shreddit-post가 감싸고 있을 수 있음)
            'div[data-testid="post-container"]',  # 이전 Reddit 구조
            'div[id^="t3_"]',  # 게시글 ID 패턴
            'div[class*="Post"]',  # 클래스 기반
            '[slot="post-container"]',  # slot 속성 기반
        ]

        posts_found = False

        for selector in post_selectors:
            try:
                post_containers = page.locator(selector)
                count = await post_containers.count()

                if count > 0:
                    typer.echo(f"   🔎 {count}개 게시글 발견 (선택자: {selector})")
                    posts_found = True

                    elements = await post_containers.all()

                    for i, element in enumerate(elements):
                        if self.debug_mode and i == 0:
                            typer.echo("   🔍 첫 번째 게시글 구조 분석...")

                        # shreddit-post 요소인 경우
                        if "shreddit-post" in selector:
                            post_data = await self._extract_post_data_from_shreddit(element)
                        elif selector == "article":
                            # article 태그인 경우 새로운 추출 방법 시도
                            post_data = await self._extract_post_data_from_article(element)
                        else:
                            # 다른 선택자의 경우 기존 추출 방법 사용
                            post_data = await self._extract_post_data(element)

                        if post_data:
                            all_posts.append(post_data)
                        elif self.debug_mode:
                            typer.echo(f"   ⚠️ {i+1}번째 게시글 추출 실패")

                    if all_posts:
                        break  # 게시글을 찾았으면 다음 선택자 시도하지 않음

            except Exception as e:
                if self.debug_mode:
                    typer.echo(f"   ❌ 선택자 {selector} 사용 중 오류: {e}")
                continue

        if not posts_found:
            typer.echo("   ❌ 어떤 선택자로도 게시글을 찾을 수 없음")
            if self.debug_mode:
                await self._save_debug_html(page, "reddit_no_posts_found.html")

                # 페이지 구조 분석
                typer.echo("   🔍 페이지 구조 분석 중...")
                try:
                    # 가능한 게시글 요소 찾기
                    possible_posts = await page.evaluate(
                        """
                        () => {
                            const selectors = ['article', 'div[id^="t3_"]', '[data-testid*="post"]', 'shreddit-post'];
                            const results = {};

                            selectors.forEach(sel => {
                                const elements = document.querySelectorAll(sel);
                                if (elements.length > 0) {
                                    results[sel] = {
                                        count: elements.length,
                                        firstElement: {
                                            tagName: elements[0].tagName,
                                            className: elements[0].className,
                                            id: elements[0].id,
                                            attributes: Array.from(elements[0].attributes).map(a => a.name)
                                        }
                                    };
                                }
                            });

                            return results;
                        }
                    """
                    )
                    typer.echo(f"      발견된 요소들: {json.dumps(possible_posts, indent=2)}")
                except Exception:
                    pass

        return all_posts

    async def _extract_post_data_from_article(self, element) -> Optional[Dict[str, Any]]:
        """article 요소에서 데이터 추출 (Reddit의 새로운 구조)"""
        try:
            # 1. 기본 데이터 추출
            title = await self._extract_title_from_element(element)
            subreddit = await self._extract_subreddit_from_element(element)
            url = await self._extract_url_from_element(element)
            timestamp = await self._extract_timestamp_from_element(element)

            # 2. 상호작용 데이터 추출
            likes, comments = await self._extract_interactions_from_element(element)

            # 3. 데이터 조합
            post_data = {
                "author": subreddit or "Unknown",
                "content": title or "No title",
                "timestamp": timestamp,
                "url": url,
                "likes": likes,
                "comments": comments,
                "shares": None,
            }

            # 유효성 검사
            if not title and not url:
                return None

            if self.debug_mode:
                typer.echo(
                    f"   ✅ article 추출: {post_data['author']} - {post_data['content'][:30]}..."
                )

            return post_data

        except Exception as e:
            if self.debug_mode:
                typer.echo(f"   ❌ article 추출 실패: {e}")
            return None

    async def _extract_title_from_element(self, element) -> Optional[str]:
        """요소에서 제목 추출"""
        # h3 태그에서 제목 찾기
        try:
            h3_elements = await element.locator("h3").all()
            for h3 in h3_elements:
                h3_text = await h3.inner_text()
                if h3_text and len(h3_text) > 5:
                    return h3_text.strip()
        except Exception:
            pass

        # a 태그의 텍스트에서 제목 찾기
        try:
            links = await element.locator('a[href*="/comments/"]').all()
            for link in links:
                link_text = await link.inner_text()
                if link_text and len(link_text) > 5:
                    return link_text.strip()
        except Exception:
            pass

        return None

    async def _extract_subreddit_from_element(self, element) -> Optional[str]:
        """요소에서 서브레딧 추출"""
        try:
            subreddit_links = await element.locator('a[href^="/r/"]').all()
            for link in subreddit_links:
                href = await link.get_attribute("href")
                if href and "/comments/" not in href:
                    match = re.search(r"/r/([^/]+)", href)
                    if match:
                        return f"r/{match.group(1)}"
        except Exception:
            pass
        return None

    async def _extract_url_from_element(self, element) -> Optional[str]:
        """요소에서 URL 추출"""
        try:
            comment_links = await element.locator('a[href*="/comments/"]').all()
            if comment_links:
                href = await comment_links[0].get_attribute("href")
                if href:
                    return f"https://www.reddit.com{href}" if href.startswith("/") else href
        except Exception:
            pass
        return None

    async def _extract_timestamp_from_element(self, element) -> str:
        """요소에서 시간 추출"""
        try:
            time_elements = await element.locator("time").all()
            if time_elements:
                return await time_elements[0].inner_text()
        except Exception:
            pass
        return ""

    async def _extract_interactions_from_element(self, element) -> tuple[int, int]:
        """요소에서 상호작용 데이터 추출"""
        likes = 0
        comments = 0

        try:
            text = await element.inner_text()

            # 업보트 패턴
            upvote_patterns = [
                r"(\d+\.?\d*[KkMm]?)\s*upvote",
                r"Vote.*?(\d+\.?\d*[KkMm]?)",
                r"^(\d+\.?\d*[KkMm]?)$",  # 숫자만 있는 라인
            ]

            for pattern in upvote_patterns:
                match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
                if match:
                    likes = self._parse_number_from_text(match.group(1))
                    break

            # 댓글 패턴
            comment_patterns = [
                r"(\d+\.?\d*[KkMm]?)\s*comment",
                r"💬\s*(\d+\.?\d*[KkMm]?)",
            ]

            for pattern in comment_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    comments = self._parse_number_from_text(match.group(1))
                    break

        except Exception:
            pass

        return likes, comments

    async def _extract_post_data_from_shreddit(self, element) -> Optional[Dict[str, Any]]:
        """shreddit-post 요소에서 데이터 추출"""
        try:
            # 1. 속성 디버그 (필요시)
            if self.debug_mode:
                await self._debug_shreddit_attributes(element)

            # 2. 기본 속성 추출
            attrs = await self._extract_shreddit_attributes(element)

            # 3. 각 데이터 추출
            title = await self._extract_shreddit_title(element, attrs.get("post_title"))
            subreddit = await self._extract_shreddit_subreddit(element, attrs.get("subreddit_name"))
            timestamp = await self._extract_shreddit_timestamp(
                element, attrs.get("created_timestamp")
            )
            upvotes = await self._extract_shreddit_upvotes(element, attrs.get("score"))

            # 4. URL 및 댓글수 처리
            url = (
                f"https://www.reddit.com{attrs.get('permalink')}"
                if attrs.get("permalink")
                else None
            )
            comments = self._parse_number_safe(attrs.get("comment_count", 0))

            # 5. 데이터 조합
            post_data = {
                "author": subreddit or "Unknown",
                "content": title or "No title",
                "timestamp": timestamp or "",
                "url": url,
                "likes": upvotes,
                "comments": comments,
                "shares": None,
            }

            # 6. 제목이 없는 경우 fallback
            if not title or title == "No title":
                fallback_title = await self._extract_fallback_title(element)
                if fallback_title:
                    post_data["content"] = fallback_title

            if self.debug_mode:
                typer.echo(
                    f"   ✅ 게시글 데이터 추출 완료: {post_data['author']} - {post_data['content'][:30]}..."
                )

            return post_data

        except Exception as e:
            if self.debug_mode:
                typer.echo(f"   ❌ 게시글 데이터 추출 실패: {e}")
            return None

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
                # 세션 파일 로드
                with open(self.session_path, "r", encoding="utf-8") as f:
                    session_data = json.load(f)

                # 세션 적용
                await page.context.add_cookies(session_data.get("cookies", []))

                # localStorage 및 sessionStorage 적용
                if "origins" in session_data:
                    for origin in session_data["origins"]:
                        if origin.get("origin") == "https://www.reddit.com":
                            # localStorage 설정
                            if "localStorage" in origin:
                                await page.goto(
                                    "https://www.reddit.com", wait_until="domcontentloaded"
                                )
                                for item in origin["localStorage"]:
                                    await page.evaluate(
                                        f'window.localStorage.setItem("{item["name"]}", {json.dumps(item["value"])})'
                                    )

                # 세션 유효성 검사
                await page.goto(self.base_url, wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)

                # 로그인 상태를 나타내는 요소 확인 - 다양한 선택자 시도
                login_indicators = [
                    'button[aria-label*="Expand user menu"]',
                    'button[id*="USER_DROPDOWN"]',
                    'div[class*="header-user-dropdown"]',
                    'button[aria-label*="profile"]',
                    'a[href="/submit"]',  # Create Post 버튼
                    'button:has-text("Create Post")',
                ]

                for indicator in login_indicators:
                    try:
                        element = page.locator(indicator).first
                        if await element.is_visible():
                            typer.echo(f"   ✅ 세션 유효함, 로그인 건너뜀 (확인: {indicator})")
                            return True
                    except Exception:
                        continue

                typer.echo("   ⚠️ 세션 만료됨, 재로그인 필요")
                return False

            except Exception as e:
                typer.echo(f"   ❌ 세션 로드 실패: {e}")
                # 손상된 세션 파일 삭제
                try:
                    self.session_path.unlink()
                    typer.echo("   🗑️ 손상된 세션 파일 삭제됨")
                except Exception:
                    pass
                return False
        return False

    async def _save_session(self, page: Page):
        """현재 세션 저장"""
        try:
            typer.echo("💾 현재 세션 저장 중...")
            # 세션 디렉토리가 없으면 생성
            self.session_path.parent.mkdir(parents=True, exist_ok=True)
            await page.context.storage_state(path=self.session_path)
            typer.echo("   ✅ 세션 저장 완료")
        except Exception as e:
            typer.echo(f"   ❌ 세션 저장 실패: {e}")

    async def _save_debug_html(self, page: Page, filename: str):
        """디버그용 HTML 파일 저장"""
        if self.debug_mode:
            debug_path = Path("data/debug/reddit")
            debug_path.mkdir(parents=True, exist_ok=True)
            full_path = debug_path / filename
            try:
                content = await page.content()
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
                typer.echo(f"   🐛 디버그 HTML 저장: {full_path}")
            except Exception as e:
                typer.echo(f"   ❌ 디버그 HTML 저장 실패: {e}")

    async def _debug_shreddit_attributes(self, element):
        """디버그: shreddit-post 속성 확인"""
        typer.echo("   🔍 shreddit-post 속성 확인 중...")
        try:
            attrs = await element.evaluate(
                """
                (el) => {
                    const attrs = {};
                    for (const attr of el.attributes) {
                        attrs[attr.name] = attr.value;
                    }
                    return attrs;
                }
            """
            )
            if attrs:
                typer.echo(f"      속성들: {list(attrs.keys())[:10]}...")
        except Exception as e:
            typer.echo(f"      속성 확인 실패: {e}")

    async def _extract_shreddit_attributes(self, element) -> Dict[str, Any]:
        """주요 shreddit-post 속성 추출"""
        attrs = {}
        attrs["permalink"] = await element.get_attribute("permalink")
        attrs["comment_count"] = await element.get_attribute("comment-count")
        attrs["created_timestamp"] = await element.get_attribute("created-timestamp")
        attrs["post_title"] = await element.get_attribute("post-title")
        attrs["subreddit_name"] = await element.get_attribute("subreddit-name")
        attrs["score"] = await element.get_attribute("score")

        if self.debug_mode:
            typer.echo(f"      permalink: {attrs['permalink']}")
            typer.echo(f"      comment-count: {attrs['comment_count']}")

        return attrs

    async def _extract_shreddit_title(self, element, attr_title: Optional[str]) -> Optional[str]:
        """제목 추출"""
        # 1. 속성에서
        if attr_title:
            if self.debug_mode:
                typer.echo(f"      제목(속성): {attr_title[:50]}...")
            return attr_title

        # 2. 댓글 링크에서
        try:
            title_links = await element.locator('a[href*="/comments/"]').all()
            for link in title_links:
                link_text = await link.inner_text()
                if link_text and len(link_text) > 5:
                    if self.debug_mode:
                        typer.echo(f"      제목(링크): {link_text[:50]}...")
                    return link_text
        except Exception:
            pass

        # 3. 헤딩 태그에서
        for heading in ["h1", "h2", "h3"]:
            try:
                heading_el = element.locator(heading).first
                if await heading_el.count() > 0:
                    title = await heading_el.inner_text()
                    if title:
                        if self.debug_mode:
                            typer.echo(f"      제목({heading}): {title[:50]}...")
                        return title
            except Exception:
                continue

        return None

    async def _extract_shreddit_subreddit(
        self, element, attr_subreddit: Optional[str]
    ) -> Optional[str]:
        """서브레딧 추출"""
        # 1. 속성에서
        if attr_subreddit:
            return f"r/{attr_subreddit}"

        # 2. 링크에서
        try:
            subreddit_links = await element.locator(
                'a[href^="/r/"]:not([href*="/comments/"])'
            ).all()
            for link in subreddit_links:
                href = await link.get_attribute("href")
                if href:
                    match = re.search(r"/r/([^/]+)", href)
                    if match:
                        return f"r/{match.group(1)}"
        except Exception:
            pass

        return None

    async def _extract_shreddit_timestamp(self, element, attr_timestamp: Optional[str]) -> str:
        """시간 추출"""
        # time 태그에서
        try:
            time_elements = await element.locator("time").all()
            if time_elements:
                return await time_elements[0].inner_text()
        except Exception:
            pass

        # 속성에서
        return attr_timestamp or ""

    async def _extract_shreddit_upvotes(self, element, attr_score: Optional[str]) -> int:
        """업보트 수 추출"""
        # 1. 속성에서
        if attr_score:
            upvotes = self._parse_number_safe(attr_score)
            if upvotes > 0:
                return upvotes

        # 2. faceplate-number에서
        try:
            faceplate_numbers = await element.locator("faceplate-number").all()
            for fn in faceplate_numbers:
                number_attr = await fn.get_attribute("number")
                if number_attr:
                    upvotes = self._parse_number_safe(number_attr)
                    if upvotes > 0:
                        return upvotes
        except Exception:
            pass

        # 3. 텍스트에서
        try:
            text_content = await element.inner_text()
            upvote_match = re.search(r"(\d+\.?\d*[KkMm]?)\s*upvote", text_content, re.IGNORECASE)
            if upvote_match:
                return self._parse_number_from_text(upvote_match.group(1))
        except Exception:
            pass

        return 0

    async def _extract_fallback_title(self, element) -> Optional[str]:
        """제목이 없는 경우 fallback 추출"""
        try:
            full_text = await element.inner_text()
            lines = [line.strip() for line in full_text.split("\n") if line.strip()]

            for line in lines:
                if (
                    not line.startswith("r/")
                    and not re.match(r"^\d+\.?\d*[KkMm]?\s*(upvote|comment)", line, re.IGNORECASE)
                    and len(line) > 10
                ):
                    if self.debug_mode:
                        typer.echo(f"      제목(텍스트 추출): {line[:50]}...")
                    return line
        except Exception:
            pass
        return None

    def _parse_number_safe(self, value: str) -> int:
        """안전한 숫자 파싱"""
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0
