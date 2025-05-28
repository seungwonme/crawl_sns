"""
@file main.py
@description SNS 크롤링 CLI 인터페이스

이 모듈은 여러 SNS 플랫폼의 게시글을 크롤링하기 위한 명령줄 인터페이스를 제공합니다.

주요 기능:
1. 플랫폼별 크롤링 명령어 (threads, linkedin, x, etc.)
2. 크롤링 옵션 설정 (게시글 수, 저장 위치)
3. 결과 출력 및 저장

핵심 구현 로직:
- Typer를 사용한 직관적인 CLI 인터페이스
- Playwright를 사용한 브라우저 자동화 크롤링
- 결과를 JSON 파일로 자동 저장

@dependencies
- typer: CLI 프레임워크
- playwright: 브라우저 자동화
- asyncio: 비동기 처리
- datetime: 파일명 생성용
- json: 데이터 직렬화
- pydantic: 데이터 모델링
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import typer
from playwright.async_api import async_playwright

from src.crawlers.linkedin import LinkedInCrawler
from src.crawlers.threads import ThreadsCrawler
from src.crawlers.x import XCrawler
from src.models import Post

# === Version ===
__version__ = "0.1.0"


# === Data Models ===
# Post 모델은 src.models에서 import하여 사용


# === Core Functions ===
def save_posts_to_file(posts: List[Post], filepath: str) -> None:
    """게시글 목록을 JSON 파일로 저장합니다."""
    output_data = {
        "metadata": {
            "total_posts": len(posts),
            "crawled_at": datetime.now().isoformat(),
            "platform": posts[0].platform if posts else "unknown",
        },
        "posts": [post.model_dump() for post in posts],
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)


# === Platform Crawlers ===


async def crawl_x(count: int = 5) -> List[Post]:
    """X (Twitter) 크롤링 (구현 예정)"""
    typer.echo(f"🐦 X 크롤링 구현 예정")
    return []


async def crawl_geeknews(count: int = 5) -> List[Post]:
    """GeekNews 크롤링 (구현 예정)"""
    typer.echo(f"🔸 GeekNews 크롤링 구현 예정")
    return []


async def crawl_reddit(count: int = 5) -> List[Post]:
    """Reddit 크롤링 (구현 예정)"""
    typer.echo(f"🔸 Reddit 크롤링 구현 예정")
    return []


# === CLI Interface ===
app = typer.Typer(
    name="crawl-sns",
    help="SNS 플랫폼(Threads, LinkedIn, X, GeekNews, Reddit) 크롤링 도구",
    add_completion=False,
)


@app.command()
def threads(
    count: int = typer.Option(5, "--count", "-c", help="수집할 게시글 수"),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="출력 파일명 (기본: 자동 생성)"
    ),
    debug: bool = typer.Option(
        False, "--debug", "-d", help="디버그 모드 활성화 (브라우저 표시, 상세 로그, 스크린샷)"
    ),
):
    """
    Threads에서 게시글을 크롤링합니다.

    예시:
    python main.py threads --count 10
    python main.py threads -c 3 -o my_threads.json
    python main.py threads --debug  # 디버그 모드로 실행
    """
    if debug:
        typer.echo(f"🐛 디버그 모드로 Threads 크롤링을 시작합니다... (게시글 {count}개)")
        typer.echo("   - 브라우저가 표시됩니다")
        typer.echo("   - 상세한 로그와 스크린샷이 저장됩니다")
        typer.echo("   - 각 단계에서 사용자 입력을 기다릴 수 있습니다")
    else:
        typer.echo(f"🧵 Threads 크롤링을 시작합니다... (게시글 {count}개)")

    # ThreadsCrawler 클래스에 debug_mode 전달
    crawler = ThreadsCrawler(debug_mode=debug)
    posts = asyncio.run(crawler.crawl(count))

    if not posts:
        typer.echo("❌ 크롤링된 게시글이 없습니다.")
        if debug:
            typer.echo("💡 디버그 정보:")
            typer.echo("   - data/debug_screenshots/ 폴더의 스크린샷을 확인해보세요")
            typer.echo(
                "   - 환경 변수 THREADS_USERNAME, THREADS_PASSWORD가 설정되었는지 확인하세요"
            )
            typer.echo("   - 로그 메시지에서 오류 원인을 찾아보세요")
        raise typer.Exit(1)

    # 출력 파일명 생성
    if not output:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = f"data/threads_{timestamp}.json"

    # data 디렉토리 생성
    Path("data").mkdir(exist_ok=True)

    # 결과 저장
    save_posts_to_file(posts, output)

    # 결과 요약 출력
    typer.echo(f"\n📊 크롤링 완료 요약:")
    typer.echo(f"   - 플랫폼: Threads")
    typer.echo(f"   - 수집된 게시글: {len(posts)}개")
    typer.echo(f"   - 저장 위치: {output}")
    if debug:
        typer.echo(f"   - 디버그 스크린샷: data/debug_screenshots/")


@app.command()
def linkedin(
    count: int = typer.Option(5, "--count", "-c", help="수집할 게시글 수"),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="출력 파일명 (기본: 자동 생성)"
    ),
    debug: bool = typer.Option(
        False, "--debug", "-d", help="디버그 모드 활성화 (브라우저 표시, 상세 로그)"
    ),
):
    """
    LinkedIn에서 게시글을 크롤링합니다.

    예시:
    python main.py linkedin --count 10
    python main.py linkedin -c 3 -o my_linkedin.json
    python main.py linkedin --debug  # 디버그 모드로 실행
    """
    if debug:
        typer.echo(f"🐛 디버그 모드로 LinkedIn 크롤링을 시작합니다... (게시글 {count}개)")
        typer.echo("   - 브라우저가 표시됩니다")
        typer.echo("   - 상세한 로그가 출력됩니다")
    else:
        typer.echo(f"💼 LinkedIn 크롤링을 시작합니다... (게시글 {count}개)")

    # LinkedInCrawler 클래스에 debug_mode 전달
    crawler = LinkedInCrawler(debug_mode=debug)
    posts = asyncio.run(crawler.crawl(count))

    if not posts:
        typer.echo("❌ 크롤링된 게시글이 없습니다.")
        if debug:
            typer.echo("💡 디버그 정보:")
            typer.echo("   - 브라우저에서 직접 확인해보세요")
            typer.echo(
                "   - 환경 변수 LINKEDIN_USERNAME, LINKEDIN_PASSWORD가 설정되었는지 확인하세요"
            )
            typer.echo("   - 로그 메시지에서 오류 원인을 찾아보세요")
        raise typer.Exit(1)

    # 출력 파일명 생성
    if not output:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = f"data/linkedin_{timestamp}.json"

    # data 디렉토리 생성
    Path("data").mkdir(exist_ok=True)

    # 결과 저장
    save_posts_to_file(posts, output)

    # 결과 요약 출력
    typer.echo(f"\n📊 크롤링 완료 요약:")
    typer.echo(f"   - 플랫폼: LinkedIn")
    typer.echo(f"   - 수집된 게시글: {len(posts)}개")
    typer.echo(f"   - 저장 위치: {output}")
    if debug:
        typer.echo(f"   - 디버그 세션: data/linkedin_session.json")

    # 첫 번째 게시글 미리보기
    if posts:
        first_post = posts[0]
        typer.echo(f"\n📄 첫 번째 게시글 미리보기:")
        typer.echo(f"   작성자: {first_post.author}")
        typer.echo(f"   내용: {first_post.content[:100]}...")
        typer.echo(f"   시간: {first_post.timestamp}")
        if first_post.likes:
            typer.echo(f"   좋아요: {first_post.likes}")
        if first_post.comments:
            typer.echo(f"   댓글: {first_post.comments}")
        if first_post.shares:
            typer.echo(f"   공유: {first_post.shares}")
        if first_post.views:
            typer.echo(f"   조회수: {first_post.views}")


@app.command()
def x(
    count: int = typer.Option(10, "--count", "-c", help="수집할 게시글 수"),
    debug: bool = typer.Option(False, "--debug", "-d", help="디버그 모드"),
):
    """X (Twitter)에서 게시글을 크롤링합니다"""
    typer.echo(f"🐦 X 크롤링 시작 (목표: {count}개 게시글)")

    crawler = XCrawler(debug_mode=debug)
    posts = asyncio.run(crawler.crawl(count))

    if posts:
        typer.echo(f"✅ {len(posts)}개 게시글 수집 완료!")

        # 데이터 저장
        data = [post.model_dump() for post in posts]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"data/x_{timestamp}.json"

        # 데이터 디렉토리 생성
        Path("data").mkdir(exist_ok=True)

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

        typer.echo(f"📁 데이터가 저장되었습니다: {filename}")

        # 첫 번째 게시글 미리보기
        if posts:
            first_post = posts[0]
            typer.echo(f"\n📝 첫 번째 게시글 미리보기:")
            typer.echo(f"   작성자: {first_post.author}")
            typer.echo(f"   내용: {first_post.content[:100]}...")
            typer.echo(f"   시간: {first_post.timestamp}")
            if first_post.likes:
                typer.echo(f"   좋아요: {first_post.likes}")
            if first_post.comments:
                typer.echo(f"   댓글: {first_post.comments}")
            if first_post.shares:
                typer.echo(f"   공유: {first_post.shares}")
            if first_post.views:
                typer.echo(f"   조회수: {first_post.views}")
    else:
        typer.echo("❌ 수집된 게시글이 없습니다.")


@app.command()
def version():
    """버전 정보를 출력합니다."""
    typer.echo("SNS Crawler v0.1.0")
    typer.echo("Playwright 기반 크롤링 도구")


@app.command()
def status():
    """현재 상태를 확인합니다."""
    typer.echo("📋 SNS Crawler 상태:")
    typer.echo("   ✅ Threads 크롤러 - 구현 완료")
    typer.echo("   ✅ LinkedIn 크롤러 - 구현 완료")
    typer.echo("   ⏳ X 크롤러 - 개발 예정")
    typer.echo("   ⏳ Reddit 크롤러 - 개발 예정")
    typer.echo("   ⏳ GeekNews 크롤러 - 개발 예정")


if __name__ == "__main__":
    app()
