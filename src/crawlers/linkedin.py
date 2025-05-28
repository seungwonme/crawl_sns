"""
@file linkedin.py
@description LinkedIn 플랫폼 전용 크롤러

이 모듈은 LinkedIn 플랫폼에서 게시글을 크롤링하는 기능을 제공합니다.

주요 기능:
1. LinkedIn 피드에서 게시글 수집
2. 작성자, 콘텐츠, 상호작용 정보 추출
3. 로그인이 필요한 경우 공개 게시글 페이지로 자동 이동

핵심 구현 로직:
- 데스크톱 User-Agent 사용
- 로그인 상태 감지 및 공개 페이지 자동 리다이렉트
- 게시글 컨테이너 다중 선택자 지원
- 리액션 및 댓글 수 추출

@dependencies
- playwright.async_api: 브라우저 자동화
- typer: CLI 출력
- .base: 베이스 크롤러 클래스

@see {@link https://linkedin.com} - LinkedIn 플랫폼
"""

from typing import Any, Dict, List, Optional

import typer
from playwright.async_api import Page

from ..models import Post
from .base import BaseCrawler


class LinkedInCrawler(BaseCrawler):
    """
    LinkedIn 플랫폼 전용 크롤러

    LinkedIn에서 게시글을 크롤링하는 클래스입니다.
    로그인이 필요한 경우 공개 게시글 페이지로 자동 이동합니다.
    """

    def __init__(self):
        super().__init__(
            platform_name="LinkedIn",
            base_url="https://www.linkedin.com/feed/",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )

    async def _crawl_implementation(self, page: Page, count: int) -> List[Post]:
        """
        LinkedIn 플랫폼에서 게시글 크롤링 실행

        Args:
            page (Page): Playwright 페이지 객체
            count (int): 수집할 게시글 수

        Returns:
            List[Post]: 크롤링된 게시글 목록
        """
        posts = []

        # LinkedIn 피드 페이지로 이동
        await page.goto(self.base_url, wait_until="networkidle")
        typer.echo(f"✅ 페이지 로드 성공")

        # 로그인이 필요한 경우 처리
        await self._handle_login_redirect(page)

        # 페이지 로드 추가 대기
        await page.wait_for_timeout(3000)

        # 게시글 요소 찾기
        post_elements = await self._find_post_elements(page)
        typer.echo(f"🔍 {len(post_elements)}개의 게시글 요소를 찾았습니다")

        # 각 게시글에서 데이터 추출
        for i, element in enumerate(post_elements[:count]):
            try:
                post_data = await self._extract_post_data(element)

                if self._is_valid_post(post_data):
                    post = Post(platform="linkedin", **post_data)
                    posts.append(post)
                    typer.echo(
                        f"   ✅ 게시글 {len(posts)}: {post_data['author']} - {post_data['content'][:50]}..."
                    )
                else:
                    typer.echo(f"   ⚠️  게시글 {i+1}: 데이터 부족")

            except Exception as e:
                typer.echo(f"   ❌ 게시글 {i+1} 파싱 중 오류: {e}")
                continue

        return posts

    async def _handle_login_redirect(self, page: Page) -> None:
        """로그인이 필요한 경우 공개 게시글 페이지로 이동"""
        login_input = await page.query_selector('input[name="session_key"]')
        if login_input:
            typer.echo(f"⚠️  LinkedIn은 로그인이 필요합니다. 공개 게시글 페이지로 이동합니다.")
            await page.goto("https://www.linkedin.com/posts/", wait_until="networkidle")
            await page.wait_for_timeout(2000)

    async def _find_post_elements(self, page: Page) -> List[Any]:
        """게시글 DOM 요소들을 찾습니다."""
        # LinkedIn 게시글 컨테이너 찾기
        post_elements = await page.query_selector_all(
            "div[data-id], article, .feed-shared-update-v2, .occludable-update"
        )

        # 대안 방법: 더 일반적인 선택자 사용
        if not post_elements:
            post_elements = await page.query_selector_all(
                'div:has(a[href*="/posts/"]), div:has(a[href*="/activity-"])'
            )

        return post_elements

    async def _extract_post_data(self, element) -> Dict[str, Any]:
        """단일 게시글에서 데이터를 추출합니다."""
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
        """작성자 정보 추출"""
        author_link = await element.query_selector('a[href*="/in/"], a[href*="/company/"]')
        if author_link:
            text = await author_link.inner_text()
            if text and text.strip():
                return text.strip().split("\n")[0]
        return "Unknown"

    async def _extract_post_url(self, element) -> Optional[str]:
        """게시글 URL 추출"""
        post_link = await element.query_selector('a[href*="/posts/"], a[href*="/activity-"]')
        if post_link:
            href = await post_link.get_attribute("href")
            if href:
                return f"https://www.linkedin.com{href}" if not href.startswith("http") else href
        return None

    async def _extract_timestamp(self, element) -> str:
        """게시 시간 추출"""
        time_element = await element.query_selector(
            'time, span:has-text("ago"), span:has-text("일"), span:has-text("시간")'
        )
        if time_element:
            time_text = await time_element.inner_text()
            if time_text:
                return time_text.strip()
        return "알 수 없음"

    async def _extract_content(self, element) -> str:
        """게시글 콘텐츠 추출"""
        content_text = ""

        # LinkedIn 게시글 콘텐츠 선택자들
        content_selectors = [
            ".feed-shared-text",
            ".feed-shared-inline-show-more-text",
            'div[data-test-id="main-feed-activity-card"] span[dir="ltr"]',
            ".break-words span",
        ]

        for selector in content_selectors:
            content_element = await element.query_selector(selector)
            if content_element:
                text = await content_element.inner_text()
                if text and len(text.strip()) > 20:
                    content_text = text.strip()
                    break

        # 대안: 전체 텍스트에서 추출
        if not content_text:
            full_text = await element.inner_text()
            if full_text:
                content_text = self._clean_content(
                    full_text,
                    exclude_keywords=["like", "comment", "share", "repost", "follow", "connection"],
                )

        return content_text[:500] if content_text else ""

    async def _extract_interactions(self, element) -> Dict[str, Optional[int]]:
        """상호작용 정보 (좋아요, 댓글) 추출"""
        interactions: Dict[str, Optional[int]] = {"likes": None, "comments": None, "shares": None}

        # 좋아요 수 (LinkedIn은 다양한 리액션 포함)
        reaction_elements = await element.query_selector_all(
            'button:has-text("reaction"), span:has-text("reaction"), .social-action'
        )
        for elem in reaction_elements:
            try:
                text = await elem.inner_text()
                likes = self._extract_numbers_from_text(text)
                if likes > 0:
                    interactions["likes"] = likes
                    break
            except:
                pass

        # 댓글 수
        comment_elements = await element.query_selector_all(
            'button:has-text("comment"), span:has-text("comment")'
        )
        for elem in comment_elements:
            try:
                text = await elem.inner_text()
                comments = self._extract_numbers_from_text(text)
                if comments > 0:
                    interactions["comments"] = comments
                    break
            except:
                pass

        return interactions

    def _is_valid_post(self, post_data: Dict[str, Any]) -> bool:
        """게시글 데이터가 유효한지 확인"""
        content = post_data.get("content")
        author = post_data.get("author")

        return bool(
            content and len(str(content).strip()) > 15 and author and str(author) != "Unknown"
        )
