"""
@file x.py
@description X (Twitter) 플랫폼 전용 크롤러

이 모듈은 X 플랫폼에서 게시글을 크롤링하는 기능을 제공합니다.

주요 기능:
1. X 피드에서 게시글 수집
2. X 계정을 통한 로그인 지원
3. 작성자, 콘텐츠, 상호작용 정보 추출
4. 세션 관리 (재로그인 방지)
5. 점진적 추출 시스템 (스크롤링, 다중 선택자)

핵심 구현 로직:
- X 로그인을 통한 피드 접근
- 점진적 스크롤링으로 더 많은 게시글 로드
- article 기반 게시글 추출
- K/M 단위 상호작용 수치 파싱

@dependencies
- playwright.async_api: 브라우저 자동화
- typer: CLI 출력
- .base: 베이스 크롤러 클래스

@see {@link https://x.com} - X 플랫폼
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


class XCrawler(BaseCrawler):
    """
    X (Twitter) 플랫폼 전용 크롤러

    X에서 게시글을 크롤링하는 클래스입니다.
    계정 로그인을 통해 피드에 접근할 수 있습니다.

    Features:
    - Storage State 기반 세션 관리 (재로그인 방지)
    - 환경 변수 기반 보안 계정 관리
    - 점진적 스크롤링 및 추출 시스템
    - 다중 선택자 기반 강건한 DOM 파싱
    - X 특화 상호작용 데이터 파싱 (K/M 단위)
    - 실제 사용자 행동 시뮬레이션
    """

    def __init__(self, debug_mode: bool = False):
        super().__init__(
            platform_name="X",
            base_url="https://x.com/home",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            debug_mode=debug_mode,
        )

        # 환경 변수 기반 설정
        self.username = os.getenv("X_USERNAME")
        self.password = os.getenv("X_PASSWORD")
        self.session_path = Path(os.getenv("X_SESSION_PATH", "./data/x_session.json"))
        self.login_timeout = int(os.getenv("X_LOGIN_TIMEOUT", "30000"))
        self.login_retry_count = int(os.getenv("X_LOGIN_RETRY_COUNT", "3"))

        # 점진적 추출 설정
        self.max_scroll_attempts = 8
        self.scroll_delay = 2500

        # 상태 관리
        self.is_logged_in = False

        # 세션 디렉토리 생성
        self.session_path.parent.mkdir(parents=True, exist_ok=True)

    async def _crawl_implementation(self, page: Page, count: int) -> List[Post]:
        """
        X 플랫폼에서 게시글 크롤링 실행
        """
        posts = []

        # 기존 세션 로드 시도
        await self._load_session(page)

        # 세션이 로드되지 않았다면 직접 페이지 로드 시도
        if not self.is_logged_in:
            typer.echo("🌐 새로운 세션으로 X 접근 중...")

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
        """X 특화 점진적 게시글 수집 시스템"""
        posts = []
        scroll_attempts = 0

        typer.echo(f"🔄 X 게시글 수집 시작 (목표: {target_count}개)")

        while len(posts) < target_count and scroll_attempts < self.max_scroll_attempts:
            # 현재 페이지의 게시글 추출
            current_posts = await self._collect_posts_from_page(page, target_count)

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
                        post = Post(platform="x", **post_data)
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

    async def _collect_posts_from_page(self, page: Page, target_count: int) -> List[Dict[str, Any]]:
        """현재 페이지에서 게시글들을 수집합니다"""
        post_elements = await self._find_post_elements(page)
        posts_data = []

        for i, element in enumerate(post_elements[: target_count * 2]):  # 여유분 확보
            try:
                post_data = await self._extract_post_data(element)
                if post_data:
                    posts_data.append(post_data)
                if len(posts_data) >= target_count:
                    break
            except Exception:
                continue

        return posts_data

    async def _find_post_elements(self, page: Page) -> List[Any]:
        """X 게시글 DOM 요소들을 찾습니다"""
        try:
            # X 게시글 컨테이너 선택자들
            post_selectors = [
                'article[role="article"]',
                'article[data-testid="tweet"]',
                '[data-testid="tweet"]',
                "article",
            ]

            post_elements = []
            for selector in post_selectors:
                elements = await page.query_selector_all(selector)
                if elements:
                    for element in elements:
                        if await self._is_valid_post_element(element):
                            post_elements.append(element)
                    break

            # 중복 제거
            unique_elements = []
            seen_content = set()

            for element in post_elements:
                try:
                    # 게시글 내용으로 중복 체크
                    content_preview = await element.inner_text()
                    content_hash = hash(content_preview[:200])

                    if content_hash not in seen_content:
                        seen_content.add(content_hash)
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
            # 기본 텍스트 내용 확인
            text_content = await element.inner_text()
            if not text_content or len(text_content.strip()) < 20:
                return False

            # X 특화 검증: 시간 정보가 있는지 확인
            time_element = await element.query_selector("time")
            if not time_element:
                return False

            # 작성자 정보가 있는지 확인
            author_patterns = [
                '[data-testid="User-Name"]',
                'a[href*="/"]',
                '[role="link"]',
            ]

            has_author = False
            for pattern in author_patterns:
                author_elem = await element.query_selector(pattern)
                if author_elem:
                    has_author = True
                    break

            return has_author

        except:
            return False

    async def _extract_post_data(self, element) -> Optional[Dict[str, Any]]:
        """X 게시글 데이터 추출"""
        try:
            # 기본 정보 추출
            author = await self._extract_author(element)
            post_url = await self._extract_post_url(element)
            timestamp = await self._extract_timestamp(element)
            content = await self._extract_content(element)
            interactions = await self._extract_interactions(element)

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

    async def _extract_author(self, element) -> str:
        """작성자 정보 추출"""
        try:
            # X 작성자 선택자들
            author_selectors = [
                '[data-testid="User-Name"] span',
                '[data-testid="User-Name"]',
                'a[href*="/"] span',
                '[role="link"] span',
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
            author_links = await element.query_selector_all('a[href*="/"]')
            for link in author_links:
                try:
                    href = await link.get_attribute("href")
                    if href and href.startswith("/") and len(href) > 2:
                        username = href.split("/")[1].split("?")[0]
                        if username and len(username) > 1 and not username.isdigit():
                            return f"@{username}"
                except:
                    continue

        except Exception:
            pass

        return "Unknown"

    async def _extract_content(self, element) -> str:
        """게시글 콘텐츠 추출"""
        try:
            content_text = ""

            # X 게시글 콘텐츠 선택자들
            content_selectors = [
                '[data-testid="tweetText"]',
                "[lang] span",
                'span[dir="ltr"]',
                "article span",
            ]

            for selector in content_selectors:
                try:
                    content_elements = await element.query_selector_all(selector)
                    content_parts = []

                    for elem in content_elements:
                        text = await elem.inner_text()
                        if text and len(text.strip()) > 5:
                            # UI 텍스트 필터링
                            if not any(
                                ui_word in text.lower()
                                for ui_word in [
                                    "reply",
                                    "repost",
                                    "like",
                                    "bookmark",
                                    "share",
                                    "following",
                                    "followers",
                                    "verified",
                                ]
                            ):
                                content_parts.append(text.strip())

                    if content_parts:
                        content_text = " ".join(content_parts[:3])  # 상위 3개 부분만
                        break
                except:
                    continue

            # 대안: 전체 텍스트에서 추출 및 정리
            if not content_text or len(content_text.strip()) < 20:
                full_text = await element.inner_text()
                if full_text:
                    content_text = self._clean_x_content(full_text)

            return content_text[:1000] if content_text else ""

        except Exception:
            return ""

    def _clean_x_content(self, content: str) -> str:
        """X 특화 콘텐츠 정리"""
        if not content:
            return ""

        # X 특화 제외 키워드
        exclude_keywords = [
            "reply",
            "repost",
            "like",
            "bookmark",
            "share",
            "quote",
            "verified",
            "following",
            "followers",
            "views",
            "ago",
            "show this thread",
            "translate",
            "more",
            "less",
        ]

        # 줄바꿈으로 분할하여 각 줄 검사
        lines = content.split("\n")
        clean_lines = []

        for line in lines:
            line = line.strip()
            if (
                len(line) > 10
                and not any(keyword in line.lower() for keyword in exclude_keywords)
                and not line.isdigit()
                and not re.match(r"^[\d\s\.\,KMkm]+$", line)  # 숫자만 있는 줄 제외
            ):
                clean_lines.append(line)

        # 연속된 중복 줄 제거
        final_lines = []
        prev_line = ""
        for line in clean_lines:
            if line != prev_line:
                final_lines.append(line)
                prev_line = line

        return "\n".join(final_lines[:5])  # 상위 5줄만

    async def _extract_interactions(self, element) -> Dict[str, Optional[int]]:
        """X 상호작용 정보 추출 - 개선된 버전"""
        interactions: Dict[str, Optional[int]] = {
            "likes": None,
            "comments": None,
            "shares": None,
            "views": None,
        }

        try:
            # X 상호작용 그룹 찾기 (실제 DOM 구조 기반)
            interaction_group = await element.query_selector('group[role="group"]')
            if not interaction_group:
                # 대안: 상호작용 버튼들이 포함된 컨테이너 찾기
                interaction_group = element

            # 모든 버튼과 링크 요소들을 찾기
            interactive_elements = await interaction_group.query_selector_all(
                'button, a[href*="analytics"]'
            )

            for elem in interactive_elements:
                try:
                    # aria-label에서 정보 추출
                    aria_label = await elem.get_attribute("aria-label") or ""

                    # 텍스트 내용 추출 (K/M 단위 표시)
                    elem_text = await elem.inner_text()

                    # 결합된 텍스트로 분석
                    full_text = f"{aria_label} {elem_text}".lower()

                    # 댓글 (Reply/Replies)
                    if "reply" in full_text or "replies" in full_text:
                        # aria-label에서 정확한 숫자 추출 시도
                        count = self._extract_count_from_aria_label(aria_label, "reply")
                        if count == 0:
                            # 텍스트에서 K/M 단위 추출
                            count = self._parse_interaction_count(elem_text)
                        if count > 0:
                            interactions["comments"] = count

                    # 리트윗/리포스트 (Repost/Retweet)
                    elif "repost" in full_text or "retweet" in full_text:
                        count = self._extract_count_from_aria_label(aria_label, "repost")
                        if count == 0:
                            count = self._parse_interaction_count(elem_text)
                        if count > 0:
                            interactions["shares"] = count

                    # 좋아요 (Like/Likes)
                    elif "like" in full_text:
                        count = self._extract_count_from_aria_label(aria_label, "like")
                        if count == 0:
                            count = self._parse_interaction_count(elem_text)
                        if count > 0:
                            interactions["likes"] = count

                    # 조회수 (Views/Analytics)
                    elif "view" in full_text or "analytics" in full_text:
                        count = self._extract_count_from_aria_label(aria_label, "view")
                        if count == 0:
                            count = self._parse_interaction_count(elem_text)
                        if count > 0:
                            interactions["views"] = count

                except Exception:
                    continue

            # 대안: data-testid 기반 선택자로 추가 시도
            if not any(interactions.values()):
                await self._extract_interactions_fallback(element, interactions)

        except Exception:
            pass

        return interactions

    def _extract_count_from_aria_label(self, aria_label: str, interaction_type: str) -> int:
        """aria-label에서 정확한 상호작용 수치를 추출"""
        try:
            if not aria_label:
                return 0

            # "8683 Replies. Reply" 형태에서 숫자 추출
            import re

            # 숫자 패턴 찾기 (쉼표 포함)
            patterns = [
                rf"(\d{{1,3}}(?:,\d{{3}})*)\s*{interaction_type}",  # "8683 replies"
                rf"(\d{{1,3}}(?:,\d{{3}})*)\s*{interaction_type[:-1]}",  # "8683 reply" (단수형)
                r"(\d{1,3}(?:,\d{3})*)",  # 일반 숫자
            ]

            for pattern in patterns:
                match = re.search(pattern, aria_label.lower())
                if match:
                    number_str = match.group(1).replace(",", "")
                    return int(number_str)

            return 0

        except Exception:
            return 0

    async def _extract_interactions_fallback(self, element, interactions: Dict[str, Optional[int]]):
        """대안 상호작용 추출 방법"""
        try:
            # data-testid 기반 선택자들
            testid_selectors = [
                '[data-testid="reply"]',
                '[data-testid="retweet"]',
                '[data-testid="like"]',
                '[data-testid="analytics"]',
            ]

            for selector in testid_selectors:
                try:
                    elem = await element.query_selector(selector)
                    if elem:
                        # 부모나 형제 요소에서 숫자 찾기
                        parent = await elem.query_selector("xpath=..")
                        if parent:
                            text = await parent.inner_text()
                            count = self._parse_interaction_count(text)

                            if "reply" in selector and count > 0:
                                interactions["comments"] = count
                            elif "retweet" in selector and count > 0:
                                interactions["shares"] = count
                            elif "like" in selector and count > 0:
                                interactions["likes"] = count
                            elif "analytics" in selector and count > 0:
                                interactions["views"] = count

                except Exception:
                    continue

        except Exception:
            pass

    def _parse_interaction_count(self, text: str) -> int:
        """상호작용 수치 파싱 (K/M 단위 처리)"""
        try:
            # 숫자 패턴 찾기
            patterns = [
                r"(\d+(?:\.\d+)?)\s*[Mm]",  # 1.2M, 15M
                r"(\d+(?:\.\d+)?)\s*[Kk]",  # 172K, 1.5K
                r"(\d{1,3}(?:,\d{3})+)",  # 1,234,567
                r"(\d+)",  # 직접 숫자
            ]

            for pattern in patterns:
                match = re.search(pattern, text)
                if match:
                    number_str = match.group(1).replace(",", "")
                    number = float(number_str)

                    # 단위 적용
                    if "M" in text or "m" in text:
                        return int(number * 1_000_000)
                    elif "K" in text or "k" in text:
                        return int(number * 1_000)
                    else:
                        return int(number)

            return 0

        except Exception:
            return 0

    async def _extract_post_url(self, element) -> Optional[str]:
        """게시글 URL 추출"""
        try:
            # X 게시글 URL 패턴들
            url_selectors = [
                "time",
                'a[href*="/status/"]',
                '[role="link"]',
            ]

            for selector in url_selectors:
                try:
                    link_element = await element.query_selector(selector)
                    if link_element:
                        # time 요소의 경우 부모 링크 찾기
                        if selector == "time":
                            parent_link = await link_element.query_selector(
                                "xpath=ancestor::a[@href]"
                            )
                            if parent_link:
                                href = await parent_link.get_attribute("href")
                            else:
                                continue
                        else:
                            href = await link_element.get_attribute("href")

                        if href and "/status/" in href:
                            if href.startswith("/"):
                                return f"https://x.com{href}"
                            elif href.startswith("http"):
                                return href
                except:
                    continue

        except Exception:
            pass

        return None

    async def _extract_timestamp(self, element) -> str:
        """게시 시간 추출"""
        try:
            # time 요소에서 추출
            time_element = await element.query_selector("time")
            if time_element:
                # datetime 속성 우선
                datetime_attr = await time_element.get_attribute("datetime")
                if datetime_attr:
                    return datetime_attr

                # 텍스트 내용
                time_text = await time_element.inner_text()
                if time_text:
                    return time_text.strip()

            # 대안: 시간 관련 텍스트 패턴 찾기
            full_text = await element.inner_text()
            time_patterns = [
                r"(\d+[hms])",  # 1h, 5m, 30s
                r"(\d+\s*[hms])",  # 1 h, 5 m
                r"(yesterday)",  # yesterday
                r"(\w{3}\s+\d{1,2})",  # May 27, Dec 5
                r"(\d{1,2}/\d{1,2}/\d{4})",  # 12/25/2024
            ]

            for pattern in time_patterns:
                match = re.search(pattern, full_text, re.IGNORECASE)
                if match:
                    return match.group(1)

        except Exception:
            pass

        return "알 수 없음"

    async def _scroll_for_more_posts(self, page: Page):
        """더 많은 게시글을 로드하기 위한 스크롤"""
        try:
            # 페이지 하단으로 스크롤
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1500)

            # 추가 스크롤 (X의 무한 스크롤 트리거)
            await page.evaluate("window.scrollBy(0, 800)")
            await page.wait_for_timeout(1000)

            # 네트워크 요청 완료 대기
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except PlaywrightTimeoutError:
                pass

        except Exception as e:
            typer.echo(f"   ⚠️ 스크롤 중 오류: {e}")

    async def _load_session(self, page: Page) -> bool:
        """저장된 세션 상태를 로드합니다"""
        try:
            if self.session_path.exists():
                typer.echo("🔄 기존 X 세션 로드 중...")

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
                        typer.echo("✅ 기존 세션으로 X 로그인 성공!")
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
        """X에 최적화된 단계적 페이지 로드"""
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

            # X 특정 요소 대기
            try:
                await page.wait_for_selector(
                    'header, nav, main, [data-testid="primaryColumn"]', timeout=10000
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
            return "x.com" in current_url

        except Exception:
            return False

    async def _verify_login_status(self, page: Page) -> bool:
        """로그인 상태 확인"""
        try:
            # URL 확인
            current_url = page.url
            if "/login" in current_url or "/i/flow/login" in current_url:
                return False

            # 홈 피드 확인
            home_indicators = [
                '[data-testid="primaryColumn"]',
                '[data-testid="tweet"]',
                'article[role="article"]',
                '[aria-label*="Home timeline"]',
            ]

            for selector in home_indicators:
                try:
                    element = await page.query_selector(selector)
                    if element:
                        return True
                except:
                    continue

            return False

        except Exception:
            return False

    async def _attempt_login(self, page: Page) -> bool:
        """X 계정 로그인 시도"""
        if not self.username or not self.password:
            typer.echo("⚠️ 환경 변수에 X 계정 정보가 없음 (.env 파일 확인 필요)")
            self.username = typer.prompt("X 사용자명 (이메일 또는 전화번호)")
            self.password = typer.prompt("X 비밀번호", hide_input=True)

        for attempt in range(self.login_retry_count):
            try:
                typer.echo(f"🔐 X 로그인 시도 {attempt + 1}/{self.login_retry_count}")

                # 로그인 페이지로 이동
                await page.goto("https://x.com/i/flow/login", wait_until="networkidle")
                await page.wait_for_timeout(2000)

                # 로그인 폼 대기
                await page.wait_for_selector(
                    'input[name="text"], input[autocomplete="username"]', timeout=self.login_timeout
                )

                # 사용자명 입력
                username_selectors = [
                    'input[name="text"]',
                    'input[autocomplete="username"]',
                    'input[placeholder*="email"]',
                    'input[placeholder*="username"]',
                ]

                username_input = None
                for selector in username_selectors:
                    username_input = await page.query_selector(selector)
                    if username_input:
                        break

                if username_input:
                    await username_input.click()
                    await username_input.fill("")
                    await page.wait_for_timeout(300)
                    for char in self.username:
                        await username_input.type(char, delay=random.randint(50, 150))

                # Next 버튼 클릭
                await page.wait_for_timeout(1000)
                next_button = await page.query_selector(
                    'button:has-text("Next"), [role="button"]:has-text("Next")'
                )
                if next_button:
                    await next_button.click()
                    await page.wait_for_timeout(2000)

                # 비밀번호 입력
                await page.wait_for_selector(
                    'input[name="password"], input[type="password"]', timeout=10000
                )

                password_input = await page.query_selector(
                    'input[name="password"], input[type="password"]'
                )
                if password_input:
                    await password_input.click()
                    await password_input.fill("")
                    await page.wait_for_timeout(300)
                    for char in self.password:
                        await password_input.type(char, delay=random.randint(50, 120))

                # 로그인 버튼 클릭
                await page.wait_for_timeout(random.randint(1000, 2000))
                login_button = await page.query_selector(
                    'button:has-text("Log in"), [role="button"]:has-text("Log in")'
                )
                if login_button:
                    await login_button.click()

                    try:
                        # 페이지 로드 대기
                        await page.wait_for_load_state("networkidle", timeout=15000)
                        # X 홈으로 리다이렉트 대기
                        await page.wait_for_url("**/home**", timeout=10000)
                    except PlaywrightTimeoutError:
                        pass

                # 보안 확인 단계 처리
                await self._handle_security_challenges(page)

                # 로그인 성공 확인
                await page.wait_for_timeout(3000)
                if await self._verify_login_status(page):
                    typer.echo("✅ X 로그인 성공!")
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

        typer.echo(f"❌ {self.login_retry_count}번 시도 후 X 로그인 실패")
        return False

    async def _handle_security_challenges(self, page: Page) -> None:
        """보안 확인 단계 처리"""
        try:
            await page.wait_for_timeout(2000)

            # 이메일/전화번호 인증 코드 입력 화면
            verification_selectors = [
                'input[name="text"]',
                'input[placeholder*="code"]',
                'input[placeholder*="verification"]',
            ]

            for selector in verification_selectors:
                verification_input = await page.query_selector(selector)
                if verification_input:
                    typer.echo("🔐 X 인증 코드 입력 필요")
                    verification_code = typer.prompt("X 인증 코드 입력")

                    await verification_input.click()
                    await verification_input.fill(verification_code)

                    submit_button = await page.query_selector(
                        'button:has-text("Next"), [role="button"]:has-text("Next")'
                    )
                    if submit_button:
                        await submit_button.click()
                        await page.wait_for_timeout(3000)
                    break

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

            typer.echo(f"💾 X 세션이 저장됨")
            return True

        except Exception as e:
            typer.echo(f"⚠️ 세션 저장 중 오류: {e}")
            return False
