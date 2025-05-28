"""
@file linkedin.py
@description LinkedIn 플랫폼 전용 크롤러

이 모듈은 LinkedIn 플랫폼에서 게시글을 크롤링하는 기능을 제공합니다.

주요 기능:
1. LinkedIn 피드에서 게시글 수집
2. LinkedIn 계정을 통한 로그인 지원
3. 작성자, 콘텐츠, 상호작용 정보 추출
4. 세션 관리 (재로그인 방지)
5. 점진적 추출 시스템 (스크롤링, 다중 선택자)

핵심 구현 로직:
- LinkedIn 로그인을 통한 피드 접근
- 점진적 스크롤링으로 더 많은 게시글 로드
- 다중 선택자 시스템으로 강건한 DOM 추출
- 단계별 추출 및 검증

@dependencies
- playwright.async_api: 브라우저 자동화
- typer: CLI 출력
- .base: 베이스 크롤러 클래스

@see {@link https://linkedin.com} - LinkedIn 플랫폼
"""

import json
import os
import random
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


class LinkedInCrawler(BaseCrawler):
    """
    LinkedIn 플랫폼 전용 크롤러

    LinkedIn에서 게시글을 크롤링하는 클래스입니다.
    계정 로그인을 통해 피드에 접근할 수 있습니다.

    Features:
    - Storage State 기반 세션 관리 (재로그인 방지)
    - 환경 변수 기반 보안 계정 관리
    - 점진적 스크롤링 및 추출 시스템
    - 다중 선택자 기반 강건한 DOM 파싱
    - 실제 사용자 행동 시뮬레이션
    - 강건한 오류 처리 및 재시도 로직
    """

    def __init__(self, debug_mode: bool = False):
        super().__init__(
            platform_name="LinkedIn",
            base_url="https://www.linkedin.com/feed/",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            debug_mode=debug_mode,
        )

        # 환경 변수 기반 설정
        self.username = os.getenv("LINKEDIN_USERNAME")
        self.password = os.getenv("LINKEDIN_PASSWORD")
        self.session_path = Path(os.getenv("LINKEDIN_SESSION_PATH", "./data/linkedin_session.json"))
        self.login_timeout = int(os.getenv("LINKEDIN_LOGIN_TIMEOUT", "30000"))
        self.login_retry_count = int(os.getenv("LINKEDIN_LOGIN_RETRY_COUNT", "3"))

        # 점진적 추출 설정
        self.max_scroll_attempts = 5
        self.scroll_delay = 2000

        # 상태 관리
        self.is_logged_in = False

        # 세션 디렉토리 생성
        self.session_path.parent.mkdir(parents=True, exist_ok=True)

    async def _crawl_implementation(self, page: Page, count: int) -> List[Post]:
        """
        LinkedIn 플랫폼에서 게시글 크롤링 실행 - 개선된 버전
        """
        posts = []

        # 기존 세션 로드 시도 (개선된 방식)
        await self._load_session(page)

        # 세션이 로드되지 않았다면 직접 페이지 로드 시도
        if not self.is_logged_in:
            typer.echo("🌐 새로운 세션으로 페이지 접근 중...")

            # 단계적 페이지 로드
            if not await self._gradual_page_load(page):
                typer.echo("❌ 페이지 로드 실패")
                return posts

            # 로그인 상태 확인
            if await self._verify_login_status(page):
                typer.echo("✅ 이미 로그인된 상태입니다")
                self.is_logged_in = True
            else:
                # 로그인 시도
                await self._attempt_login(page)

        # 추가 안정화 대기
        await page.wait_for_timeout(3000)

        # 점진적 게시글 수집
        posts = await self._progressive_post_collection(page, count)

        return posts

    async def _progressive_post_collection(self, page: Page, target_count: int) -> List[Post]:
        """개선된 점진적 게시글 수집 시스템"""
        posts = []
        scroll_attempts = 0

        typer.echo(f"🔄 게시글 수집 시작 (목표: {target_count}개)")

        while len(posts) < target_count and scroll_attempts < self.max_scroll_attempts:
            # 1단계: 페이지 전체의 더보기 버튼 모두 클릭
            await self._expand_all_posts_on_page(page)

            # 2단계: 상단에서부터 순차적으로 게시글 추출
            current_posts = await self._collect_expanded_posts(page, target_count)

            # 새로운 게시글만 추가
            new_posts_count = 0
            for post_data in current_posts:
                if len(posts) >= target_count:
                    break

                # 중복 확인
                is_duplicate = any(
                    existing.url == post_data.get("url")
                    or (
                        existing.content == post_data.get("content")
                        and existing.author == post_data.get("author")
                    )
                    for existing in posts
                )

                if not is_duplicate and self._is_valid_post(post_data):
                    try:
                        post = Post(platform="linkedin", **post_data)
                        posts.append(post)
                        new_posts_count += 1
                        typer.echo(
                            f"   ✅ 게시글 {len(posts)}: {post_data['author']} - {post_data['content'][:50]}..."
                        )
                    except Exception as e:
                        typer.echo(f"   ⚠️ 게시글 생성 중 오류: {e}")

            # 목표 달성 시 종료
            if len(posts) >= target_count:
                typer.echo(f"✅ 목표 달성: {len(posts)}개 게시글 수집 완료")
                break

            # 새로운 게시글이 없으면 스크롤
            if new_posts_count == 0:
                await self._scroll_for_more_posts(page)
                scroll_attempts += 1
                await page.wait_for_timeout(self.scroll_delay)

        typer.echo(f"📊 수집 완료: {len(posts)}개 게시글")
        return posts[:target_count]

    async def _expand_all_posts_on_page(self, page: Page):
        """현재 페이지의 모든 더보기 버튼을 클릭합니다"""
        try:
            # 더보기 버튼들 찾기
            see_more_selectors = [
                ".feed-shared-inline-show-more-text__see-more-less-toggle.see-more",
                'button:has-text("더보기")',
                'button:has-text("…더보기")',
                'button[aria-label*="더보기"]',
                'button:has-text("see more")',
                'button:has-text("...more")',
                ".feed-shared-inline-show-more-text__see-more-less-toggle",
                ".see-more",
            ]

            expanded_count = 0
            for selector in see_more_selectors:
                try:
                    buttons = await page.query_selector_all(selector)
                    for button in buttons:
                        try:
                            if await button.is_visible():
                                await button.scroll_into_view_if_needed()
                                await page.wait_for_timeout(200)
                                await button.click()
                                await page.wait_for_timeout(300)
                                expanded_count += 1
                        except:
                            continue
                except:
                    continue

            if expanded_count > 0:
                typer.echo(f"   📖 {expanded_count}개 더보기 버튼 클릭 완료")

        except Exception as e:
            typer.echo(f"   ⚠️ 더보기 확장 중 오류: {e}")

    async def _collect_expanded_posts(self, page: Page, target_count: int) -> List[Dict[str, Any]]:
        """확장된 게시글들을 상단에서부터 순차적으로 수집합니다"""
        post_elements = await self._find_post_elements(page)
        posts_data = []

        for i, element in enumerate(post_elements[: target_count * 2]):  # 여유분 확보
            try:
                post_data = await self._extract_post_data_simple(element)
                if post_data:
                    posts_data.append(post_data)
                if len(posts_data) >= target_count:
                    break
            except Exception:
                continue

        return posts_data

    async def _extract_post_data_simple(self, element) -> Optional[Dict[str, Any]]:
        """단순화된 게시글 데이터 추출 (더보기가 이미 클릭된 상태)"""
        try:
            # 기본 정보 추출
            author = await self._extract_author_progressive(element)
            post_url = await self._extract_post_url_progressive(element)
            timestamp = await self._extract_timestamp_progressive(element)
            content = await self._extract_content_progressive(element)
            interactions = await self._extract_interactions_progressive(element)

            post_data = {
                "author": author,
                "content": content,
                "timestamp": timestamp,
                "url": post_url,
                **interactions,
            }

            return post_data

        except Exception:
            return None

    async def _scroll_for_more_posts(self, page: Page):
        """더 많은 게시글을 로드하기 위한 스크롤"""
        try:
            # 페이지 하단으로 스크롤
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1000)

            # 추가 스크롤 (LinkedIn의 무한 스크롤 트리거)
            await page.evaluate("window.scrollBy(0, 500)")
            await page.wait_for_timeout(1000)

            # 네트워크 요청 완료 대기
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except PlaywrightTimeoutError:
                pass

        except Exception as e:
            typer.echo(f"   ⚠️ 스크롤 중 오류: {e}")

    async def _find_post_elements(self, page: Page) -> List[Any]:
        """게시글 DOM 요소들을 찾습니다 (다중 선택자 시스템)"""
        try:
            # LinkedIn 게시글 컨테이너 선택자들
            post_selectors = [
                ".feed-shared-update-v2[data-urn]",
                '[data-id*="urn:li:activity:"]',
                ".feed-shared-update-v2",
                '[data-urn*="update"]',
                "div[data-id]",
                'article[role="article"]',
                ".occludable-update",
                ".scaffold-finite-scroll__content > div > div",
            ]

            post_elements = []
            for selector in post_selectors:
                elements = await page.query_selector_all(selector)
                if elements:
                    for element in elements:
                        if element not in post_elements:
                            if await self._is_valid_post_element(element):
                                post_elements.append(element)
                    break

            # 중복 제거
            unique_elements = []
            seen_elements = set()

            for element in post_elements:
                try:
                    element_id = await element.get_attribute(
                        "data-id"
                    ) or await element.get_attribute("data-urn")
                    if not element_id:
                        content = await element.inner_text()
                        element_id = hash(content[:100]) if content else id(element)

                    if element_id not in seen_elements:
                        seen_elements.add(element_id)
                        unique_elements.append(element)
                except:
                    unique_elements.append(element)

            return unique_elements

        except Exception as e:
            typer.echo(f"⚠️ 게시글 요소 탐색 중 오류: {e}")
            return []

    async def _is_valid_post_element(self, element) -> bool:
        """게시글 요소가 유효한지 검증"""
        try:
            # 게시글에 필수적인 요소들이 있는지 확인
            text_content = await element.inner_text()
            if not text_content or len(text_content.strip()) < 20:
                return False

            # 작성자 링크가 있는지 확인
            author_link = await element.query_selector('a[href*="/in/"], a[href*="/company/"]')
            if not author_link:
                return False

            # 게시글 컨텐츠 영역이 있는지 확인
            content_area = await element.query_selector(
                ".update-components-text, .break-words, .feed-shared-text"
            )
            if not content_area:
                return False

            return True

        except:
            return False

    async def _extract_author_progressive(self, element) -> str:
        """작성자 정보 점진적 추출"""
        try:
            # LinkedIn 작성자 링크 선택자들 (실제 HTML 구조 기반)
            author_selectors = [
                # 개인 프로필
                '.update-components-actor__meta-link .update-components-actor__title span[dir="ltr"] span[aria-hidden="true"]',
                ".update-components-actor__meta-link .update-components-actor__title span",
                '.update-components-actor__title span[dir="ltr"]',
                'a[href*="/in/"] .update-components-actor__title',
                # 회사 페이지
                'a[href*="/company/"] span',
                '.update-components-actor__meta-link span[dir="ltr"]',
                # 일반적인 선택자
                'a[href*="/in/"]',
                'a[href*="/company/"]',
                ".feed-shared-actor__name a",
                '[data-control-name="actor"] a',
            ]

            for selector in author_selectors:
                try:
                    author_element = await element.query_selector(selector)
                    if author_element:
                        text = await author_element.inner_text()
                        if text and text.strip() and len(text.strip()) > 1:
                            # 첫 번째 줄만 가져오기 (이름 부분)
                            author_name = text.strip().split("\n")[0].strip()
                            if len(author_name) > 1 and not author_name.isdigit():
                                return author_name
                except:
                    continue

            # fallback: href에서 추출
            for selector in ['a[href*="/in/"]', 'a[href*="/company/"]']:
                try:
                    author_link = await element.query_selector(selector)
                    if author_link:
                        href = await author_link.get_attribute("href")
                        if href:
                            if "/in/" in href:
                                username = href.split("/in/")[-1].split("/")[0].split("?")[0]
                            elif "/company/" in href:
                                username = href.split("/company/")[-1].split("/")[0].split("?")[0]
                            else:
                                continue

                            if username and len(username) > 1 and not username.isdigit():
                                return username.replace("-", " ").title()
                except:
                    continue

        except Exception:
            pass

        return "Unknown"

    async def _extract_content_progressive(self, element) -> str:
        """게시글 콘텐츠 점진적 추출 (더보기 클릭 후)"""
        try:
            content_text = ""

            # LinkedIn 게시글 콘텐츠 선택자들 (확장된 상태)
            content_selectors = [
                '.update-components-text .break-words span[dir="ltr"]',
                ".update-components-update-v2__commentary .break-words span",
                '.feed-shared-inline-show-more-text .update-components-text span[dir="ltr"]',
                ".feed-shared-inline-show-more-text .break-words",
                ".update-components-text .break-words",
                ".feed-shared-text",
                ".feed-shared-inline-show-more-text span",
                ".break-words span",
                ".update-components-text",
                '[data-test-id="main-feed-activity-card"] span[dir="ltr"]',
                '[data-control-name="text"] span',
            ]

            for selector in content_selectors:
                try:
                    content_element = await element.query_selector(selector)
                    if content_element:
                        text = await content_element.inner_text()
                        if text and len(text.strip()) > 20:
                            content_text = text.strip()
                            break
                except:
                    continue

            # 대안: 전체 텍스트에서 추출 및 정리
            if not content_text:
                full_text = await element.inner_text()
                if full_text:
                    content_text = self._clean_linkedin_content(full_text)

            # 여전히 콘텐츠가 없다면 개별 텍스트 요소들을 조합
            if not content_text or len(content_text.strip()) < 20:
                content_text = await self._extract_content_fallback(element)

            return content_text[:1000] if content_text else ""

        except Exception:
            return ""

    async def _extract_content_fallback(self, element) -> str:
        """콘텐츠 추출 폴백 방법 (개별 텍스트 노드 조합)"""
        try:
            # 모든 텍스트 요소들을 찾아서 조합
            text_elements = await element.query_selector_all("span, p, div")
            content_parts = []

            for text_elem in text_elements:
                try:
                    text = await text_elem.inner_text()
                    if text and len(text.strip()) > 10:
                        # 버튼이나 UI 텍스트가 아닌 실제 콘텐츠만 추출
                        if not any(
                            ui_word in text.lower()
                            for ui_word in [
                                "like",
                                "comment",
                                "share",
                                "follow",
                                "connect",
                                "추천",
                                "댓글",
                                "퍼가기",
                                "팔로우",
                                "연결",
                            ]
                        ):
                            content_parts.append(text.strip())

                except:
                    continue

            if content_parts:
                # 중복 제거 및 정리
                unique_parts = []
                for part in content_parts:
                    if part not in unique_parts and len(part) > 15:
                        unique_parts.append(part)

                return " ".join(unique_parts[:3])  # 상위 3개 부분만 조합

        except:
            pass

        return ""

    def _clean_linkedin_content(self, content: str) -> str:
        """LinkedIn 특화 콘텐츠 정리"""
        if not content:
            return ""

        # LinkedIn 특화 제외 키워드
        exclude_keywords = [
            "like",
            "comment",
            "share",
            "repost",
            "more",
            "ago",
            "추천",
            "댓글",
            "퍼가기",
            "보내기",
            "시간",
            "일",
            "분",
            "celebration",
            "love",
            "insightful",
            "curious",
            "팔로워",
            "connection",
            "1촌",
            "2촌",
            "3촌",
            "linkedin",
            "프로필",
            "follow",
            "connect",
        ]

        # 줄바꿈으로 분할하여 각 줄 검사
        lines = content.split("\n")
        clean_lines = []

        for line in lines:
            line = line.strip()
            if (
                len(line) > 15
                and not any(keyword in line.lower() for keyword in exclude_keywords)
                and not line.isdigit()
                and not all(c in "•·-=+*" for c in line.replace(" ", ""))
            ):
                clean_lines.append(line)

        # 연속된 중복 줄 제거
        final_lines = []
        prev_line = ""
        for line in clean_lines:
            if line != prev_line:
                final_lines.append(line)
                prev_line = line

        return "\n".join(final_lines)

    async def _extract_interactions_progressive(self, element) -> Dict[str, Optional[int]]:
        """상호작용 정보 점진적 추출"""
        interactions: Dict[str, Optional[int]] = {"likes": None, "comments": None, "shares": None}

        try:
            # LinkedIn 상호작용 카운트 영역 찾기
            social_counts = await element.query_selector(".social-details-social-counts")

            if social_counts:
                # 좋아요/반응 수 추출
                reactions_button = await social_counts.query_selector(
                    "button[data-reaction-details], .social-details-social-counts__reactions"
                )
                if reactions_button:
                    reactions_text = await reactions_button.inner_text()
                    if reactions_text:
                        likes = self._extract_numbers_from_text(reactions_text)
                        if likes > 0:
                            interactions["likes"] = likes

                # 댓글 수 추출
                comments_button = await social_counts.query_selector(
                    'button[aria-label*="댓글"], .social-details-social-counts__comments'
                )
                if comments_button:
                    comments_text = await comments_button.inner_text()
                    if comments_text:
                        comments = self._extract_numbers_from_text(comments_text)
                        if comments > 0:
                            interactions["comments"] = comments

                # 공유 수 추출 (퍼감)
                shares_elements = await social_counts.query_selector_all(
                    'button[aria-label*="퍼감"], span:has-text("퍼감")'
                )
                for elem in shares_elements:
                    shares_text = await elem.inner_text()
                    if shares_text and "퍼감" in shares_text:
                        shares = self._extract_numbers_from_text(shares_text)
                        if shares > 0:
                            interactions["shares"] = shares
                            break

            # 대안: 액션 버튼에서 추출
            if not any(interactions.values()):
                action_buttons = await element.query_selector_all(
                    ".social-actions-button, .feed-shared-social-action-bar button"
                )

                for button in action_buttons:
                    try:
                        button_text = await button.inner_text()
                        if not button_text:
                            continue

                        button_text_lower = button_text.lower()

                        # 좋아요/반응 수
                        if any(word in button_text_lower for word in ["like", "추천", "reaction"]):
                            likes = self._extract_numbers_from_text(button_text)
                            if likes > 0:
                                interactions["likes"] = likes

                        # 댓글 수
                        elif "댓글" in button_text or "comment" in button_text_lower:
                            comments = self._extract_numbers_from_text(button_text)
                            if comments > 0:
                                interactions["comments"] = comments

                        # 공유 수
                        elif any(
                            word in button_text_lower for word in ["share", "퍼가기", "repost"]
                        ):
                            shares = self._extract_numbers_from_text(button_text)
                            if shares > 0:
                                interactions["shares"] = shares

                    except Exception:
                        continue

        except Exception:
            pass

        return interactions

    async def _extract_post_url_progressive(self, element) -> Optional[str]:
        """게시글 URL 점진적 추출"""
        try:
            # data-id에서 URN 추출
            data_id = await element.get_attribute("data-id")
            if data_id and "urn:li:activity:" in data_id:
                activity_id = data_id.split("urn:li:activity:")[-1]
                return f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}/"

            # data-urn에서 추출
            data_urn = await element.get_attribute("data-urn")
            if data_urn and "activity:" in data_urn:
                return f"https://www.linkedin.com/feed/update/{data_urn}/"

            # 게시글 링크 직접 찾기
            url_selectors = [
                'a[href*="/posts/"]',
                'a[href*="/activity-"]',
                'a[href*="/feed/update/"]',
                '[data-control-name="overlay"] a',
            ]

            for selector in url_selectors:
                post_link = await element.query_selector(selector)
                if post_link:
                    href = await post_link.get_attribute("href")
                    if href and (
                        "/posts/" in href or "/activity-" in href or "/feed/update/" in href
                    ):
                        return (
                            f"https://www.linkedin.com{href}"
                            if not href.startswith("http")
                            else href
                        )

        except Exception:
            pass

        return None

    async def _extract_timestamp_progressive(self, element) -> str:
        """게시 시간 점진적 추출 - 개선된 버전"""
        try:
            # 1단계: 정확한 타임스탬프 선택자로 직접 추출
            timestamp_selectors = [
                '.update-components-actor__sub-description span[aria-hidden="true"]',
                ".update-components-actor__sub-description",
                "time",
                ".feed-shared-actor__sub-description time",
                '[data-control-name="actor"] time',
            ]

            for selector in timestamp_selectors:
                try:
                    time_element = await element.query_selector(selector)
                    if time_element:
                        time_text = await time_element.inner_text()
                        if time_text and time_text.strip():
                            # 타임스탬프 텍스트에서 시간 정보만 추출
                            cleaned_timestamp = self._extract_time_from_text(time_text.strip())
                            if cleaned_timestamp:
                                return cleaned_timestamp
                except:
                    continue

            # 2단계: 대안 검색 - 시간 관련 키워드가 포함된 요소 찾기
            fallback_selectors = [
                'span:has-text("시간")',
                'span:has-text("일")',
                'span:has-text("분")',
                'span:has-text("ago")',
                'span:has-text("hour")',
                'span:has-text("day")',
                'span:has-text("week")',
                'span:has-text("주")',
            ]

            for selector in fallback_selectors:
                try:
                    time_element = await element.query_selector(selector)
                    if time_element:
                        time_text = await time_element.inner_text()
                        if time_text:
                            cleaned_timestamp = self._extract_time_from_text(time_text.strip())
                            if cleaned_timestamp:
                                return cleaned_timestamp
                except:
                    continue

        except Exception:
            pass

        return "알 수 없음"

    def _extract_time_from_text(self, text: str) -> str:
        """텍스트에서 시간 정보만 추출"""
        if not text:
            return ""

        # 줄바꿈 제거 및 정리
        text = text.replace("\n", " ").strip()

        # 시간 관련 패턴들
        time_patterns = [
            # 한국어 패턴
            r"(\d+분\s*[•·]?)",
            r"(\d+시간\s*[•·]?)",
            r"(\d+일\s*[•·]?)",
            r"(\d+주\s*[•·]?)",
            r"(\d+달\s*[•·]?)",
            r"(\d+개월\s*[•·]?)",
            r"(\d+년\s*[•·]?)",
            # 영어 패턴
            r"(\d+\s*minute?s?\s*ago)",
            r"(\d+\s*hour?s?\s*ago)",
            r"(\d+\s*day?s?\s*ago)",
            r"(\d+\s*week?s?\s*ago)",
            r"(\d+\s*month?s?\s*ago)",
            r"(\d+\s*year?s?\s*ago)",
            # 간단한 패턴
            r"(\d+분)",
            r"(\d+시간)",
            r"(\d+일)",
            r"(\d+주)",
            r"(\d+개월)",
            r"(현재\s*시간)",
            r"(남은\s*시간)",
        ]

        import re

        # 각 패턴으로 시간 정보 찾기
        for pattern in time_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                time_part = match.group(1).strip()
                # 추가 정보가 있으면 포함 (수정됨 등)
                if "수정됨" in text:
                    return f"{time_part} • 수정됨 •"
                return f"{time_part} •"

        # 패턴이 매치되지 않으면 첫 번째 문장에서 시간 관련 키워드 찾기
        first_sentence = text.split("\n")[0].split(".")[0].strip()

        # 시간 키워드가 포함된 짧은 텍스트라면 그대로 반환
        time_keywords = [
            "분",
            "시간",
            "일",
            "주",
            "달",
            "개월",
            "년",
            "ago",
            "minute",
            "hour",
            "day",
            "week",
            "month",
            "year",
            "현재",
            "남은",
        ]

        if (
            any(keyword in first_sentence.lower() for keyword in time_keywords)
            and len(first_sentence) < 50
        ):
            # 불필요한 부분 제거
            cleaned = first_sentence
            # 아이콘이나 기타 불필요한 텍스트 제거
            unwanted_parts = ["웹상에서 누구에게나 보임", "인증됨", "1촌", "2촌", "3촌", "팔로워"]
            for unwanted in unwanted_parts:
                cleaned = cleaned.replace(unwanted, "").strip()

            # 연속된 공백이나 특수문자 정리
            cleaned = re.sub(r"\s+", " ", cleaned)
            cleaned = re.sub(r"[•·]{2,}", "•", cleaned)

            if len(cleaned) < 30:  # 충분히 짧으면 반환
                return cleaned

        return ""

    async def _load_session(self, page: Page) -> bool:
        """저장된 세션 상태를 로드합니다"""
        try:
            if self.session_path.exists():
                typer.echo("🔄 기존 세션 로드 중...")

                # Storage State 로드
                with open(self.session_path, "r") as f:
                    storage_state = json.load(f)

                # 브라우저 컨텍스트에 Storage State 적용
                await page.context.add_cookies(storage_state.get("cookies", []))

                # 단계적 페이지 로드
                if await self._gradual_page_load(page):
                    # 로그인 상태 확인
                    if await self._verify_login_status(page):
                        self.is_logged_in = True
                        typer.echo("✅ 기존 세션으로 로그인 성공!")
                        return True
                    else:
                        typer.echo("⚠️ 기존 세션이 만료됨")
                        if self.session_path.exists():
                            self.session_path.unlink()
                        return False
                else:
                    typer.echo("⚠️ 페이지 로드 실패 - 세션 무효화")
                    if self.session_path.exists():
                        self.session_path.unlink()
                    return False
            else:
                return False

        except Exception as e:
            typer.echo(f"⚠️ 세션 로드 중 오류: {e}")
            if self.session_path.exists():
                self.session_path.unlink()
            return False

    async def _gradual_page_load(self, page: Page) -> bool:
        """LinkedIn에 최적화된 단계적 페이지 로드"""
        try:
            # 기본 페이지 로드
            try:
                await page.goto(
                    self.base_url,
                    wait_until="domcontentloaded",
                    timeout=15000,
                )
            except PlaywrightTimeoutError:
                pass

            # 페이지 안정화 대기
            await page.wait_for_timeout(2000)

            # LinkedIn 특정 요소 대기
            try:
                await page.wait_for_selector(
                    'header, .global-nav, .feed-container, [data-test-id="nav-top"]', timeout=10000
                )
            except PlaywrightTimeoutError:
                pass

            # 추가 JavaScript 실행 대기
            try:
                await page.wait_for_load_state("load", timeout=5000)
            except PlaywrightTimeoutError:
                pass

            # 최종 확인
            current_url = page.url
            return "linkedin.com" in current_url

        except Exception:
            return False

    async def _verify_login_status(self, page: Page) -> bool:
        """로그인 상태 확인"""
        try:
            # URL 확인
            current_url = page.url
            if "/login" in current_url or "/uas/login" in current_url:
                return False

            # 피드 특정 요소 확인
            post_composer = await page.query_selector('button[aria-label*="Start a post"]')
            if post_composer:
                return True

            # 피드 게시글 확인
            feed_posts = await page.query_selector_all(
                '.feed-shared-update-v2, [data-urn*="update"]'
            )
            if len(feed_posts) > 0:
                return True

            # 프로필 메뉴 확인
            profile_menu = await page.query_selector(
                '[data-control-name="identity_welcome_message"]'
            )
            if profile_menu:
                return True

            return False

        except Exception:
            return False

    async def _attempt_login(self, page: Page) -> bool:
        """LinkedIn 계정 로그인 시도"""
        if not self.username or not self.password:
            typer.echo("⚠️ 환경 변수에 계정 정보가 없음 (.env 파일 확인 필요)")
            self.username = typer.prompt("LinkedIn 사용자명 (이메일)")
            self.password = typer.prompt("LinkedIn 비밀번호", hide_input=True)

        for attempt in range(self.login_retry_count):
            try:
                typer.echo(f"🔐 로그인 시도 {attempt + 1}/{self.login_retry_count}")

                # 로그인 페이지로 이동
                current_url = page.url
                if "/uas/login" not in current_url and "/checkpoint" not in current_url:
                    await page.goto("https://www.linkedin.com/login", wait_until="networkidle")

                # 로그인 폼 대기
                await page.wait_for_selector(
                    'input[name="session_key"]', timeout=self.login_timeout
                )

                # 사용자명 입력
                username_input = await page.query_selector('input[name="session_key"]')
                if username_input:
                    await username_input.click()
                    await username_input.fill("")
                    await page.wait_for_timeout(300)
                    for char in self.username:
                        await username_input.type(char, delay=random.randint(50, 150))

                # 비밀번호 입력
                password_input = await page.query_selector('input[name="session_password"]')
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
                        # 페이지 로드 대기
                        await page.wait_for_load_state("networkidle", timeout=15000)
                        # LinkedIn 피드로 리다이렉트 대기
                        await page.wait_for_url("**/feed/**", timeout=10000)
                    except PlaywrightTimeoutError:
                        pass

                # 보안 확인 단계 처리
                await self._handle_security_challenges(page)

                # 로그인 성공 확인
                await page.wait_for_timeout(3000)
                if await self._verify_login_status(page):
                    typer.echo("✅ 로그인 성공!")
                    self.is_logged_in = True
                    await self._save_session(page)
                    return True
                else:
                    if attempt < self.login_retry_count - 1:
                        await page.wait_for_timeout(random.randint(3000, 5000))

            except PlaywrightTimeoutError:
                if attempt < self.login_retry_count - 1:
                    await page.wait_for_timeout(random.randint(2000, 4000))
            except Exception as e:
                typer.echo(f"   ❌ 로그인 중 오류: {e}")
                if attempt < self.login_retry_count - 1:
                    await page.wait_for_timeout(random.randint(2000, 4000))

        typer.echo(f"❌ {self.login_retry_count}번 시도 후 로그인 실패")
        return False

    async def _handle_security_challenges(self, page: Page) -> None:
        """보안 확인 단계 처리"""
        try:
            await page.wait_for_timeout(2000)

            # 이메일 인증 코드 입력 화면
            verification_input = await page.query_selector('input[name="pin"]')
            if verification_input:
                typer.echo("🔐 이메일 인증 코드 입력 필요")
                verification_code = typer.prompt("LinkedIn 인증 코드 입력")

                await verification_input.click()
                await verification_input.fill(verification_code)

                submit_button = await page.query_selector('button[type="submit"]')
                if submit_button:
                    await submit_button.click()
                    await page.wait_for_timeout(3000)

            # "Trust this browser" 화면
            trust_button = await page.query_selector('button:has-text("Trust this browser")')
            if trust_button:
                await trust_button.click()
                await page.wait_for_timeout(2000)

        except Exception as e:
            typer.echo(f"⚠️ 보안 확인 처리 중 오류: {e}")

    def _is_valid_post(self, post_data: Dict[str, Any]) -> bool:
        """게시글 데이터가 유효한지 확인"""
        content = post_data.get("content")
        author = post_data.get("author")

        return bool(
            content and len(str(content).strip()) > 15 and author and str(author) != "Unknown"
        )

    async def _save_session(self, page: Page) -> bool:
        """현재 세션 상태를 Storage State로 저장합니다"""
        try:
            # Storage State 추출
            storage_state = await page.context.storage_state()

            # 세션 파일에 저장
            with open(self.session_path, "w") as f:
                json.dump(storage_state, f, indent=2)

            typer.echo(f"💾 세션이 저장됨")
            return True

        except Exception as e:
            typer.echo(f"⚠️ 세션 저장 중 오류: {e}")
            return False
