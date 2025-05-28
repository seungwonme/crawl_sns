"""
@file base.py
@description SNS 크롤러 베이스 클래스

이 모듈은 모든 SNS 플랫폼 크롤러의 공통 기능을 제공하는 추상 베이스 클래스를 정의합니다.

주요 기능:
1. 공통 크롤링 인터페이스 정의
2. Playwright 브라우저 관리
3. 에러 핸들링 및 로깅
4. 크롤링 결과 검증

핵심 구현 로직:
- ABC(Abstract Base Class)를 사용한 인터페이스 강제
- Playwright async context manager 패턴
- 플랫폼별 User-Agent 설정
- 크롤링 진행 상황 표시

@dependencies
- abc: 추상 베이스 클래스
- playwright.async_api: 브라우저 자동화
- typer: CLI 출력

@see {@link /docs/crawler-architecture.md} - 크롤러 아키텍처 문서
"""

import asyncio
import re
from abc import ABC, abstractmethod
from typing import List, Optional

import typer
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from ..models import Post


class BaseCrawler(ABC):
    """
    SNS 크롤러 베이스 클래스

    모든 플랫폼별 크롤러가 상속해야 하는 추상 베이스 클래스입니다.
    공통 브라우저 관리 기능과 크롤링 인터페이스를 제공합니다.
    """

    def __init__(
        self,
        platform_name: str,
        base_url: str,
        user_agent: Optional[str] = None,
        debug_mode: bool = False,
    ):
        """
        베이스 크롤러 초기화

        Args:
            platform_name (str): 플랫폼 이름 (예: threads, linkedin)
            base_url (str): 플랫폼 기본 URL
            user_agent (Optional[str]): 사용할 User-Agent 문자열
            debug_mode (bool): 디버그 모드 활성화 여부
        """
        self.platform_name = platform_name
        self.base_url = base_url
        self.user_agent = user_agent or self._get_default_user_agent()
        self.debug_mode = debug_mode

    def _get_default_user_agent(self) -> str:
        """플랫폼별 기본 User-Agent 반환"""
        return "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    async def crawl(self, count: int = 5) -> List[Post]:
        """
        메인 크롤링 실행 함수

        Args:
            count (int): 수집할 게시글 수

        Returns:
            List[Post]: 크롤링된 게시글 목록
        """
        posts = []

        try:
            if self.debug_mode:
                typer.echo(
                    f"🐛 디버그 모드로 {self.platform_name} 크롤링을 시작합니다... (게시글 {count}개)"
                )
                typer.echo("   - 브라우저 창이 표시됩니다")
            else:
                typer.echo(f"🔄 {self.platform_name} 크롤링을 시작합니다... (게시글 {count}개)")

            async with async_playwright() as p:
                # 항상 브라우저 창 표시 (일반 모드, 디버그 모드 모두)
                browser = await p.chromium.launch(
                    headless=False,  # 항상 브라우저 창 표시
                    devtools=self.debug_mode,  # 디버그 모드에서만 개발자 도구 열기
                )

                context = await browser.new_context(user_agent=self.user_agent)
                page = await context.new_page()

                try:
                    posts = await self._crawl_implementation(page, count)
                finally:
                    if self.debug_mode:
                        typer.echo(
                            "🐛 디버그 모드: 브라우저를 수동으로 닫으세요 (완료 후 Enter를 누르면 자동 종료)"
                        )
                        try:
                            # 사용자가 Enter를 누를 때까지 대기 (5분 타임아웃)
                            await asyncio.wait_for(asyncio.to_thread(input), timeout=300)
                        except asyncio.TimeoutError:
                            typer.echo("⏰ 5분 타임아웃 - 브라우저를 자동으로 닫습니다")
                        except:
                            pass
                    await browser.close()

            typer.echo(f"📊 총 {len(posts)}개의 게시글을 추출했습니다.")

            if not posts:
                typer.echo(f"❌ 게시글을 추출하지 못했습니다.")
                if self.debug_mode:
                    typer.echo(f"💡 디버그 힌트:")
                    typer.echo(f"   - 브라우저에서 직접 확인해보세요")
                    typer.echo(f"   - 네트워크 연결 상태를 점검하세요")
                    typer.echo(f"   - 플랫폼 접근 권한을 확인하세요")
                else:
                    typer.echo(f"💡 힌트: {self.platform_name}은(는) 로그인이 필요할 수 있습니다.")
                    typer.echo(f"   디버그 모드로 다시 실행해보세요: --debug")

        except Exception as e:
            typer.echo(f"❌ 크롤링 중 오류 발생: {e}")
            if self.debug_mode:
                typer.echo(f"🐛 디버그 정보: {e}")

        return posts

    @abstractmethod
    async def _crawl_implementation(self, page: Page, count: int) -> List[Post]:
        """
        플랫폼별 구체적인 크롤링 구현 (하위 클래스에서 구현 필수)

        Args:
            page (Page): Playwright 페이지 객체
            count (int): 수집할 게시글 수

        Returns:
            List[Post]: 크롤링된 게시글 목록
        """
        pass

    def _extract_numbers_from_text(self, text: str) -> int:
        """텍스트에서 숫자 추출 (K, M 단위 지원)"""
        text = text.lower().replace(",", "")
        m = re.search(r"(\d+(?:\.\d+)?)([kKmM]?)", text)
        if not m:
            return 0
        value, suffix = m.groups()
        num = float(value)
        if suffix == "k":
            num *= 1_000
        elif suffix == "m":
            num *= 1_000_000
        return int(num)

    def _clean_content(self, content: str, exclude_keywords: Optional[List[str]] = None) -> str:
        """
        콘텐츠 텍스트 정리

        Args:
            content (str): 원본 콘텐츠
            exclude_keywords (Optional[List[str]]): 제외할 키워드 목록

        Returns:
            str: 정리된 콘텐츠
        """
        if not content:
            return ""

        if exclude_keywords is None:
            exclude_keywords = ["like", "comment", "share", "repost", "more", "ago"]

        # 줄바꿈으로 분할하여 각 줄 검사
        lines = content.split("\n")
        clean_lines = []

        for line in lines:
            line = line.strip()
            if (
                len(line) > 10
                and not any(keyword in line.lower() for keyword in exclude_keywords)
                and not line.isdigit()
            ):
                clean_lines.append(line)

        return "\n".join(clean_lines)[:500]  # 길이 제한
