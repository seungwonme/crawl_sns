"""
@file main.py
@description SNS 크롤링 CLI 인터페이스

이 모듈은 여러 SNS 플랫폼의 게시글을 크롤링하기 위한 명령줄 인터페이스를 제공합니다.

주요 기능:
1. 플랫폼별 크롤링 명령어 (threads, linkedin, x, reddit)
2. 통일된 크롤링 옵션 설정 (게시글 수, 저장 위치, 디버그 모드)
3. 일관된 결과 출력 및 저장

핵심 구현 로직:
- Typer를 사용한 직관적인 CLI 인터페이스
- 로깅 데코레이터를 통한 통일된 작업 추적
- 모든 플랫폼에서 동일한 출력 형식 제공

@dependencies
- typer: CLI 프레임워크
- asyncio: 비동기 처리
- datetime: 파일명 생성용
- pathlib: 디렉토리 관리
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import typer

from src.crawlers.linkedin import LinkedInCrawler
from src.crawlers.reddit import RedditCrawler
from src.crawlers.threads import ThreadsCrawler
from src.crawlers.x import XCrawler
from src.exporters import SheetsExporter
from src.models import Post
from src.print import (
    log_crawl_operation,
    print_crawl_summary,
    print_no_posts_error,
    print_post_preview,
)

# === App Configuration ===
app = typer.Typer(
    name="crawl-sns",
    help="SNS 플랫폼(Threads, LinkedIn, X, Reddit) 크롤링 도구",
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)

__version__ = "0.1.0"


# === Utility Functions ===
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


def generate_output_filename(platform: str, custom_output: Optional[str] = None) -> str:
    """출력 파일명을 생성합니다."""
    if custom_output:
        return custom_output

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"data/{platform}/{timestamp}.json"


def ensure_data_directory(platform: str = None) -> None:
    """data 디렉토리와 플랫폼별 하위 디렉토리가 존재하는지 확인하고 생성합니다."""
    Path("data").mkdir(exist_ok=True)
    if platform:
        Path(f"data/{platform}").mkdir(exist_ok=True)


# === Platform Crawling Commands ===
@app.command()
@log_crawl_operation("threads")
def threads(
    count: int = typer.Option(5, "--count", "-c", help="수집할 게시글 수"),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="출력 파일명 (기본: 자동 생성)"
    ),
    debug: bool = typer.Option(
        False, "--debug", "-d", help="디버그 모드 활성화 (브라우저 표시, 상세 로그, 스크린샷)"
    ),
    sheets: bool = typer.Option(
        False, "--sheets", "-s", help="구글 시트에 저장 (GOOGLE_WEBAPP_URL 환경변수 필요)"
    ),
):
    """
    Threads에서 게시글을 크롤링합니다.

    예시:
    python main.py threads --count 10
    python main.py threads -c 3 -o my_threads.json
    python main.py threads --debug  # 디버그 모드로 실행
    python main.py threads --sheets  # 구글 시트에 저장
    python main.py threads -c 5 -s  # 5개 게시글을 구글 시트에 저장
    """
    crawler = ThreadsCrawler(debug_mode=debug)
    posts = asyncio.run(crawler.crawl(count))

    if not posts:
        print_no_posts_error("threads", debug)
        raise typer.Exit(1)

    # JSON 파일 저장 (기본)
    ensure_data_directory("threads")
    output_file = generate_output_filename("threads", output)
    save_posts_to_file(posts, output_file)

    # 구글 시트 저장 (옵션)
    sheets_success = False
    if sheets:
        try:
            exporter = SheetsExporter()
            sheets_success = exporter.export_posts(posts, "threads")
        except ValueError as e:
            typer.echo(f"❌ 구글 시트 설정 오류: {str(e)}")
            sheets_success = False
        except Exception as e:
            typer.echo(f"❌ 구글 시트 저장 중 오류: {str(e)}")
            sheets_success = False

    # 결과 출력
    print_crawl_summary("threads", len(posts), output_file, debug)
    if sheets:
        if sheets_success:
            typer.echo("   📊 구글 시트 저장: ✅ 성공")
        else:
            typer.echo("   📊 구글 시트 저장: ❌ 실패 (JSON 파일은 저장됨)")

    print_post_preview(posts[0], "threads")


@app.command()
@log_crawl_operation("linkedin")
def linkedin(
    count: int = typer.Option(5, "--count", "-c", help="수집할 게시글 수"),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="출력 파일명 (기본: 자동 생성)"
    ),
    debug: bool = typer.Option(
        False, "--debug", "-d", help="디버그 모드 활성화 (브라우저 표시, 상세 로그)"
    ),
    sheets: bool = typer.Option(
        False, "--sheets", "-s", help="구글 시트에 저장 (GOOGLE_WEBAPP_URL 환경변수 필요)"
    ),
):
    """
    LinkedIn에서 게시글을 크롤링합니다.

    예시:
    python main.py linkedin --count 10
    python main.py linkedin -c 3 -o my_linkedin.json
    python main.py linkedin --debug  # 디버그 모드로 실행
    python main.py linkedin --sheets  # 구글 시트에 저장
    python main.py linkedin -c 5 -s  # 5개 게시글을 구글 시트에 저장
    """
    crawler = LinkedInCrawler(debug_mode=debug)
    posts = asyncio.run(crawler.crawl(count))

    if not posts:
        print_no_posts_error("linkedin", debug)
        raise typer.Exit(1)

    # JSON 파일 저장 (기본)
    ensure_data_directory("linkedin")
    output_file = generate_output_filename("linkedin", output)
    save_posts_to_file(posts, output_file)

    # 구글 시트 저장 (옵션)
    sheets_success = False
    if sheets:
        try:
            exporter = SheetsExporter()
            sheets_success = exporter.export_posts(posts, "linkedin")
        except ValueError as e:
            typer.echo(f"❌ 구글 시트 설정 오류: {str(e)}")
            sheets_success = False
        except Exception as e:
            typer.echo(f"❌ 구글 시트 저장 중 오류: {str(e)}")
            sheets_success = False

    # 결과 출력
    print_crawl_summary("linkedin", len(posts), output_file, debug)
    if sheets:
        if sheets_success:
            typer.echo("   📊 구글 시트 저장: ✅ 성공")
        else:
            typer.echo("   📊 구글 시트 저장: ❌ 실패 (JSON 파일은 저장됨)")

    print_post_preview(posts[0], "linkedin")


@app.command()
@log_crawl_operation("x")
def x(
    count: int = typer.Option(10, "--count", "-c", help="수집할 게시글 수"),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="출력 파일명 (기본: 자동 생성)"
    ),
    debug: bool = typer.Option(False, "--debug", "-d", help="디버그 모드"),
    sheets: bool = typer.Option(
        False, "--sheets", "-s", help="구글 시트에 저장 (GOOGLE_WEBAPP_URL 환경변수 필요)"
    ),
):
    """
    X (Twitter)에서 게시글을 크롤링합니다.

    예시:
    python main.py x --count 10
    python main.py x -c 5 -o my_x_posts.json
    python main.py x --debug
    python main.py x --sheets  # 구글 시트에 저장
    python main.py x -c 5 -s  # 5개 게시글을 구글 시트에 저장
    """
    crawler = XCrawler(debug_mode=debug)
    posts = asyncio.run(crawler.crawl(count))

    if not posts:
        print_no_posts_error("x", debug)
        raise typer.Exit(1)

    # JSON 파일 저장 (기본)
    ensure_data_directory("x")
    output_file = generate_output_filename("x", output)
    save_posts_to_file(posts, output_file)

    # 구글 시트 저장 (옵션)
    sheets_success = False
    if sheets:
        try:
            exporter = SheetsExporter()
            sheets_success = exporter.export_posts(posts, "x")
        except ValueError as e:
            typer.echo(f"❌ 구글 시트 설정 오류: {str(e)}")
            sheets_success = False
        except Exception as e:
            typer.echo(f"❌ 구글 시트 저장 중 오류: {str(e)}")
            sheets_success = False

    # 결과 출력
    print_crawl_summary("x", len(posts), output_file, debug)
    if sheets:
        if sheets_success:
            typer.echo("   📊 구글 시트 저장: ✅ 성공")
        else:
            typer.echo("   📊 구글 시트 저장: ❌ 실패 (JSON 파일은 저장됨)")

    print_post_preview(posts[0], "x")


@app.command()
@log_crawl_operation("reddit")
def reddit(
    count: int = typer.Option(10, "--count", "-c", help="수집할 게시글 수"),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="출력 파일명 (기본: 자동 생성)"
    ),
    debug: bool = typer.Option(False, "--debug", "-d", help="디버그 모드"),
    sheets: bool = typer.Option(
        False, "--sheets", "-s", help="구글 시트에 저장 (GOOGLE_WEBAPP_URL 환경변수 필요)"
    ),
):
    """
    Reddit에서 게시글을 크롤링합니다.

    예시:
    python main.py reddit --count 10
    python main.py reddit -c 5 -o my_reddit_posts.json
    python main.py reddit --debug
    python main.py reddit --sheets  # 구글 시트에 저장
    python main.py reddit -c 5 -s  # 5개 게시글을 구글 시트에 저장
    """
    crawler = RedditCrawler(debug_mode=debug)
    posts = asyncio.run(crawler.crawl(count))

    if not posts:
        print_no_posts_error("reddit", debug)
        raise typer.Exit(1)

    # JSON 파일 저장 (기본)
    ensure_data_directory("reddit")
    output_file = generate_output_filename("reddit", output)
    save_posts_to_file(posts, output_file)

    # 구글 시트 저장 (옵션)
    sheets_success = False
    if sheets:
        try:
            exporter = SheetsExporter()
            sheets_success = exporter.export_posts(posts, "reddit")
        except ValueError as e:
            typer.echo(f"❌ 구글 시트 설정 오류: {str(e)}")
            sheets_success = False
        except Exception as e:
            typer.echo(f"❌ 구글 시트 저장 중 오류: {str(e)}")
            sheets_success = False

    # 결과 출력
    print_crawl_summary("reddit", len(posts), output_file, debug)
    if sheets:
        if sheets_success:
            typer.echo("   📊 구글 시트 저장: ✅ 성공")
        else:
            typer.echo("   📊 구글 시트 저장: ❌ 실패 (JSON 파일은 저장됨)")

    print_post_preview(posts[0], "reddit")


# === Utility Commands ===
@app.command()
def version():
    """버전 정보를 출력합니다."""
    typer.echo(f"SNS Crawler v{__version__}")
    typer.echo("Playwright 기반 크롤링 도구")


@app.command()
def status():
    """현재 크롤러 상태를 확인합니다."""
    typer.echo("📋 SNS Crawler 상태:")
    typer.echo("   ✅ Threads 크롤러 - 구현 완료")
    typer.echo("   ✅ LinkedIn 크롤러 - 구현 완료")
    typer.echo("   🔧 X 크롤러 - 구현 완료")
    typer.echo("   🔧 Reddit 크롤러 - 구현 완료")


if __name__ == "__main__":
    app()
