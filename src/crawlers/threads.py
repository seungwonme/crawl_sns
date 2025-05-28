"""
@file threads.py
@description Threads 플랫폼 전용 크롤러

이 모듈은 Meta의 Threads 플랫폼에서 게시글을 크롤링하는 기능을 제공합니다.

주요 기능:
1. Threads 메인 피드에서 게시글 수집
2. 작성자, 콘텐츠, 상호작용 정보 추출
3. 모바일 User-Agent를 사용한 접근

핵심 구현 로직:
- 모바일 User-Agent로 접근하여 더 안정적인 크롤링
- DOM 구조 분석을 통한 게시글 컨테이너 탐지
- 링크 패턴 기반 게시글 식별 및 데이터 추출
- 상호작용 버튼에서 숫자 추출

@dependencies
- playwright.async_api: 브라우저 자동화
- typer: CLI 출력
- .base: 베이스 크롤러 클래스

@see {@link https://threads.net} - Threads 플랫폼
"""

import re
from typing import Any, Dict, List, Optional

import typer
from playwright.async_api import Page

from ..models import Post
from .base import BaseCrawler


class ThreadsCrawler(BaseCrawler):
    """
    Threads 플랫폼 전용 크롤러

    Meta의 Threads에서 게시글을 크롤링하는 클래스입니다.
    모바일 User-Agent를 사용하여 더 안정적인 접근을 제공합니다.
    """

    def __init__(self):
        super().__init__(
            platform_name="Threads",
            base_url="https://threads.net",
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        )

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

        # Threads 메인 페이지로 이동
        await page.goto(self.base_url, wait_until="networkidle")
        typer.echo(f"✅ 페이지 로드 성공")

        # 페이지 로드 추가 대기
        await page.wait_for_timeout(3000)

        # 게시글 요소 찾기
        post_elements = await self._find_post_elements(page, count)
        typer.echo(f"🔍 {len(post_elements)}개의 게시글 컨테이너를 찾았습니다")

        # 각 게시글에서 데이터 추출
        for i, element in enumerate(post_elements[:count]):
            try:
                post_data = await self._extract_post_data(element)

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

    async def _find_post_elements(self, page: Page, count: int) -> List[Any]:
        """게시글 DOM 요소들을 찾습니다."""
        post_elements = []

        # Column body 영역 찾기 - 실제 구조에 맞춰서
        column_body = await page.query_selector('region[role] >> text="Column body"')
        if not column_body:
            column_body = await page.query_selector('[aria-label="Column body"]')

        if column_body:
            # 실제 브라우저 구조: Column body > generic > 개별 게시글 generic들
            main_container = await column_body.query_selector("generic")
            if main_container:
                # 각 게시글은 프로필 링크, 시간, 콘텐츠, 상호작용을 포함한 generic 컨테이너
                potential_posts = await main_container.query_selector_all(
                    'generic[cursor="pointer"]'
                )

                for element in potential_posts:
                    try:
                        # 유효한 게시글인지 확인 - 프로필 링크와 시간이 있는지
                        profile_link = await element.query_selector(
                            'a[href*="/@"]:not([href*="/post/"])'
                        )
                        time_element = await element.query_selector("time")

                        if profile_link and time_element:
                            post_elements.append(element)
                            if len(post_elements) >= count:
                                break

                    except Exception:
                        continue

        typer.echo(f"🔗 Column body에서 {len(post_elements)}개의 게시글 컨테이너를 찾았습니다")
        return post_elements[:count]

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
        # 프로필 링크에서 사용자명 추출
        author_link = await element.query_selector('a[href*="/@"]:not([href*="/post/"])')
        if author_link:
            href = await author_link.get_attribute("href")
            if href and "/@" in href:
                # /@username 형태에서 username 추출
                author = href.split("/@")[-1].split("/")[0]
                return author
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
        """게시글 콘텐츠 추출 - 실제 구조에 맞춰서"""
        try:
            # 실제 구조에서 콘텐츠는 여러 generic 블록 중 특정 위치에 있음
            content_containers = await element.query_selector_all("generic")

            main_content = ""
            for container in content_containers:
                try:
                    # 상호작용 버튼이 있는 컨테이너는 제외
                    buttons = await container.query_selector_all("button")
                    has_interaction_buttons = False

                    for button in buttons:
                        button_text = await button.inner_text()
                        if any(
                            word in button_text.lower()
                            for word in ["like", "comment", "repost", "share"]
                        ):
                            has_interaction_buttons = True
                            break

                    # 상호작용 버튼이 없는 컨테이너에서 콘텐츠 찾기
                    if not has_interaction_buttons:
                        text = await container.inner_text()
                        if text and len(text.strip()) > 15:
                            # 작성자명과 시간 정보 제외
                            author = await self._extract_author(element)
                            timestamp = await self._extract_timestamp(element)

                            text = text.strip()
                            if author != "Unknown":
                                text = text.replace(author, "", 1).strip()
                            if timestamp != "알 수 없음":
                                text = text.replace(timestamp, "", 1).strip()

                            # "Translate" 버튼 텍스트 제거
                            text = text.replace("Translate", "").strip()

                            # 의미있는 콘텐츠인지 확인
                            if len(text) > main_content.__len__() and not text.startswith("More"):
                                main_content = text

                except Exception:
                    continue

            return main_content[:500] if main_content else ""

        except Exception as e:
            typer.echo(f"   콘텐츠 추출 중 오류: {e}")
            return ""

    async def _extract_interactions(self, element) -> Dict[str, Optional[int]]:
        """상호작용 정보 (좋아요, 댓글, 공유) 추출"""
        interactions: Dict[str, Optional[int]] = {"likes": None, "comments": None, "shares": None}

        try:
            # 실제 브라우저 구조에 맞는 버튼 선택자 사용

            # Like 버튼 - "Like 87" 형태
            like_button = await element.query_selector('button[cursor="pointer"]:has-text("Like")')
            if like_button:
                text = await like_button.inner_text()
                # "Like 87" -> 87 추출
                numbers = re.findall(r"\d+[KMB]?", text)
                if numbers:
                    # K, M, B 단위 처리
                    count_str = numbers[-1]
                    if "K" in count_str:
                        interactions["likes"] = int(float(count_str.replace("K", "")) * 1000)
                    elif "M" in count_str:
                        interactions["likes"] = int(float(count_str.replace("M", "")) * 1000000)
                    elif "B" in count_str:
                        interactions["likes"] = int(float(count_str.replace("B", "")) * 1000000000)
                    else:
                        interactions["likes"] = int(count_str)

            # Comment 버튼 - "Comment 161" 형태
            comment_button = await element.query_selector(
                'button[cursor="pointer"]:has-text("Comment")'
            )
            if comment_button:
                text = await comment_button.inner_text()
                numbers = re.findall(r"\d+[KMB]?", text)
                if numbers:
                    count_str = numbers[-1]
                    if "K" in count_str:
                        interactions["comments"] = int(float(count_str.replace("K", "")) * 1000)
                    elif "M" in count_str:
                        interactions["comments"] = int(float(count_str.replace("M", "")) * 1000000)
                    else:
                        interactions["comments"] = int(count_str)

            # Repost 버튼 - "Repost 33" 형태 (Threads에서는 Share 대신 Repost)
            repost_button = await element.query_selector(
                'button[cursor="pointer"]:has-text("Repost")'
            )
            if repost_button:
                text = await repost_button.inner_text()
                numbers = re.findall(r"\d+[KMB]?", text)
                if numbers:
                    count_str = numbers[-1]
                    if "K" in count_str:
                        interactions["shares"] = int(float(count_str.replace("K", "")) * 1000)
                    elif "M" in count_str:
                        interactions["shares"] = int(float(count_str.replace("M", "")) * 1000000)
                    else:
                        interactions["shares"] = int(count_str)

            # Share 버튼도 확인 - "Share 8" 형태
            if interactions["shares"] is None:
                share_button = await element.query_selector(
                    'button[cursor="pointer"]:has-text("Share")'
                )
                if share_button:
                    text = await share_button.inner_text()
                    numbers = re.findall(r"\d+[KMB]?", text)
                    if numbers:
                        count_str = numbers[-1]
                        if "K" in count_str:
                            interactions["shares"] = int(float(count_str.replace("K", "")) * 1000)
                        elif "M" in count_str:
                            interactions["shares"] = int(
                                float(count_str.replace("M", "")) * 1000000
                            )
                        else:
                            interactions["shares"] = int(count_str)

        except Exception as e:
            typer.echo(f"   상호작용 추출 중 오류: {e}")

        return interactions

    def _is_valid_post(self, post_data: Dict[str, Any]) -> bool:
        """게시글 데이터가 유효한지 확인"""
        content = post_data.get("content", "")
        author = post_data.get("author", "")

        # 조건을 완화: 콘텐츠가 3자 이상이고 작성자가 있으면 유효
        return bool(
            content and len(str(content).strip()) >= 3 and author and str(author) != "Unknown"
        )
