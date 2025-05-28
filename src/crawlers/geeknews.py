"""
@file geeknews.py
@description GeekNews 플랫폼 전용 크롤러

이 모듈은 GeekNews 플랫폼에서 게시글을 크롤링하는 기능을 제공합니다.

주요 기능:
1. GeekNews 메인 페이지에서 뉴스 아이템 수집
2. 제목, 설명, 댓글 수, 점수 정보 추출
3. 기술 뉴스 특화 콘텐츠 처리

핵심 구현 로직:
- 제목이 주요 콘텐츠인 뉴스 사이트 특성 반영
- 링크 패턴 기반 게시글 식별
- 점수/추천 시스템 정보 추출
- 추가 설명/요약 텍스트 병합 처리

@dependencies
- playwright.async_api: 브라우저 자동화
- typer: CLI 출력
- .base: 베이스 크롤러 클래스

@see {@link https://news.hada.io} - GeekNews 플랫폼
"""

from typing import Any, Dict, List, Optional

import typer
from playwright.async_api import Page

from ..models import Post
from .base import BaseCrawler


class GeekNewsCrawler(BaseCrawler):
    """
    GeekNews 플랫폼 전용 크롤러

    GeekNews에서 기술 뉴스를 크롤링하는 클래스입니다.
    제목 중심의 뉴스 사이트 특성에 맞춰 최적화되어 있습니다.
    """

    def __init__(self):
        super().__init__(
            platform_name="GeekNews",
            base_url="https://news.hada.io/",
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )

    async def _crawl_implementation(self, page: Page, count: int) -> List[Post]:
        """
        GeekNews 플랫폼에서 게시글 크롤링 실행

        Args:
            page (Page): Playwright 페이지 객체
            count (int): 수집할 게시글 수

        Returns:
            List[Post]: 크롤링된 게시글 목록
        """
        posts = []

        # GeekNews 메인 페이지로 이동
        await page.goto(self.base_url, wait_until="networkidle")
        typer.echo(f"✅ 페이지 로드 성공")

        # 페이지 로드 추가 대기
        await page.wait_for_timeout(2000)

        # 게시글 요소 찾기
        post_elements = await self._find_post_elements(page, count)
        typer.echo(f"🔍 {len(post_elements)}개의 게시글 요소를 찾았습니다")

        # 각 게시글에서 데이터 추출
        for i, element in enumerate(post_elements[:count]):
            try:
                post_data = await self._extract_post_data(element)

                if self._is_valid_post(post_data):
                    post = Post(platform="geeknews", **post_data)
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

    async def _find_post_elements(self, page: Page, count: int) -> List[Any]:
        """게시글 DOM 요소들을 찾습니다."""
        # GeekNews 게시글 컨테이너 찾기
        post_elements = await page.query_selector_all(
            ".topic_row, .article-item, .news-item, .post-item"
        )

        # 대안: 링크 기반으로 찾기
        if not post_elements:
            post_elements = await page.query_selector_all('div:has(a[href*="/topic/"])')

        # 더 일반적인 방법: 제목이 있는 요소들
        if not post_elements:
            title_links = await page.query_selector_all('a[href*="/topic/"]')
            containers = []

            for link in title_links[: count * 2]:
                try:
                    # 상위 컨테이너 찾기
                    container = await link.evaluate_handle(
                        """(element) => {
                        let current = element;
                        for (let i = 0; i < 4; i++) {
                            if (current.parentElement) {
                                current = current.parentElement;
                                if (current.textContent && current.textContent.length > 50) {
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

            post_elements = containers[:count]

        return post_elements

    async def _extract_post_data(self, element) -> Dict[str, Any]:
        """단일 게시글에서 데이터를 추출합니다."""
        # 제목/콘텐츠 추출 (GeekNews는 제목이 주요 콘텐츠)
        content, post_url = await self._extract_title_and_url(element)

        # 작성자 정보 추출 (GeekNews는 보통 작성자가 명시되지 않음)
        author = await self._extract_author(element)

        # 게시 시간 추출
        timestamp = await self._extract_timestamp(element)

        # 추가 설명이나 요약 추출하여 콘텐츠에 추가
        description = await self._extract_description(element)
        if description:
            content += f"\n{description}"

        # 상호작용 정보 추출
        interactions = await self._extract_interactions(element)

        return {
            "author": author,
            "content": content.strip()[:500] if content else "",
            "timestamp": timestamp,
            "url": post_url,
            **interactions,
        }

    async def _extract_title_and_url(self, element) -> tuple[str, Optional[str]]:
        """제목과 URL을 함께 추출합니다."""
        title_link = await element.query_selector('a[href*="/topic/"]')
        content_text = ""
        post_url = None

        if title_link:
            content_text = await title_link.inner_text()
            href = await title_link.get_attribute("href")
            if href:
                post_url = f"https://news.hada.io{href}" if not href.startswith("http") else href

        return content_text or "", post_url

    async def _extract_author(self, element) -> str:
        """작성자 정보 추출"""
        author_element = await element.query_selector(".author, .user, .by")
        if author_element:
            author_text = await author_element.inner_text()
            if author_text and author_text.strip():
                return author_text.strip()
        return "GeekNews"

    async def _extract_timestamp(self, element) -> str:
        """게시 시간 추출"""
        time_element = await element.query_selector(
            'time, .time, .date, span:has-text("시간"), span:has-text("일"), span:has-text("ago")'
        )
        if time_element:
            time_text = await time_element.inner_text()
            if time_text:
                return time_text.strip()
        return "알 수 없음"

    async def _extract_description(self, element) -> str:
        """추가 설명이나 요약 추출"""
        description_element = await element.query_selector(".description, .summary, .excerpt")
        if description_element:
            desc_text = await description_element.inner_text()
            if desc_text and len(desc_text.strip()) > 10:
                return desc_text.strip()
        return ""

    async def _extract_interactions(self, element) -> Dict[str, Optional[int]]:
        """상호작용 정보 (댓글 수, 점수) 추출"""
        interactions: Dict[str, Optional[int]] = {"likes": None, "comments": None, "shares": None}

        # 댓글 수 찾기
        comment_elements = await element.query_selector_all(
            'span:has-text("댓글"), .comment, a[href*="#comment"]'
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

        # 점수나 추천 수 찾기
        score_elements = await element.query_selector_all(".score, .points, .vote")
        for elem in score_elements:
            try:
                text = await elem.inner_text()
                likes = self._extract_numbers_from_text(text)
                if likes > 0:
                    interactions["likes"] = likes
                    break
            except:
                pass

        return interactions

    def _is_valid_post(self, post_data: Dict[str, Any]) -> bool:
        """게시글 데이터가 유효한지 확인"""
        content = post_data.get("content")

        return bool(content and len(str(content).strip()) > 10)
