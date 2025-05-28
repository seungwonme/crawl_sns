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

# Import crawler and models
from src.crawlers.threads import ThreadsCrawler
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


async def crawl_linkedin(count: int = 5) -> List[Post]:
    """LinkedIn에서 게시글을 크롤링합니다."""
    posts = []

    try:
        typer.echo(f"💼 LinkedIn 크롤링을 시작합니다... (게시글 {count}개)")

        async with async_playwright() as p:
            # 브라우저 실행
            browser = await p.chromium.launch(headless=False)

            # User-Agent를 포함한 컨텍스트 생성
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )

            page = await context.new_page()

            # LinkedIn 피드 페이지로 이동
            await page.goto("https://www.linkedin.com/feed/", wait_until="networkidle")
            typer.echo(f"✅ 페이지 로드 성공")

            # 페이지 로드 추가 대기
            await page.wait_for_timeout(3000)

            # LinkedIn은 로그인이 필요한 경우 처리
            if await page.query_selector('input[name="session_key"]'):
                typer.echo(f"⚠️  LinkedIn은 로그인이 필요합니다. 공개 게시글 페이지로 이동합니다.")
                await page.goto("https://www.linkedin.com/posts/", wait_until="networkidle")
                await page.wait_for_timeout(2000)

            # 게시글 컨테이너 찾기
            post_elements = await page.query_selector_all(
                "div[data-id], article, .feed-shared-update-v2, .occludable-update"
            )

            # 대안 방법: 더 일반적인 선택자 사용
            if not post_elements:
                post_elements = await page.query_selector_all(
                    'div:has(a[href*="/posts/"]), div:has(a[href*="/activity-"])'
                )

            typer.echo(f"🔍 {len(post_elements)}개의 게시글 요소를 찾았습니다")

            # 게시글 데이터 추출
            for i, element in enumerate(post_elements[:count]):
                try:
                    # 작성자 정보 추출
                    author_link = await element.query_selector(
                        'a[href*="/in/"], a[href*="/company/"]'
                    )
                    author = "Unknown"
                    if author_link:
                        text = await author_link.inner_text()
                        if text and text.strip():
                            author = text.strip().split("\n")[0]

                    # 게시글 URL 추출
                    post_link = await element.query_selector(
                        'a[href*="/posts/"], a[href*="/activity-"]'
                    )
                    post_url = None
                    if post_link:
                        href = await post_link.get_attribute("href")
                        if href:
                            post_url = (
                                f"https://www.linkedin.com{href}"
                                if not href.startswith("http")
                                else href
                            )

                    # 게시 시간 추출
                    time_element = await element.query_selector(
                        'time, span:has-text("ago"), span:has-text("일"), span:has-text("시간")'
                    )
                    timestamp = "알 수 없음"
                    if time_element:
                        time_text = await time_element.inner_text()
                        if time_text:
                            timestamp = time_text.strip()

                    # 콘텐츠 추출
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
                            lines = full_text.split("\n")
                            for line in lines:
                                line = line.strip()
                                if (
                                    len(line) > 30
                                    and not any(
                                        exclude in line.lower()
                                        for exclude in [
                                            "like",
                                            "comment",
                                            "share",
                                            "repost",
                                            "follow",
                                            "connection",
                                        ]
                                    )
                                    and not line.isdigit()
                                ):
                                    content_text = line
                                    break

                    # 상호작용 정보 추출
                    likes = 0
                    comments = 0

                    # 좋아요 수 (LinkedIn은 다양한 리액션 포함)
                    reaction_elements = await element.query_selector_all(
                        'button:has-text("reaction"), span:has-text("reaction"), .social-action'
                    )
                    for elem in reaction_elements:
                        try:
                            text = await elem.inner_text()
                            numbers = "".join(filter(str.isdigit, text))
                            if numbers:
                                likes = int(numbers)
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
                            numbers = "".join(filter(str.isdigit, text))
                            if numbers:
                                comments = int(numbers)
                                break
                        except:
                            pass

                    # 유효한 게시글인지 확인
                    if content_text and len(content_text.strip()) > 15 and author != "Unknown":
                        post = Post(
                            platform="linkedin",
                            author=author,
                            content=content_text[:500],
                            timestamp=timestamp,
                            url=post_url,
                            likes=likes if likes > 0 else None,
                            comments=comments if comments > 0 else None,
                            shares=None,
                        )
                        posts.append(post)
                        typer.echo(f"   ✅ 게시글 {len(posts)}: {author} - {content_text[:50]}...")
                    else:
                        typer.echo(
                            f"   ⚠️  게시글 {i+1}: 데이터 부족 - author={author}, content_len={len(content_text) if content_text else 0}"
                        )

                except Exception as e:
                    typer.echo(f"   ❌ 게시글 {i+1} 파싱 중 오류: {e}")
                    continue

            await browser.close()
            typer.echo(f"📊 총 {len(posts)}개의 게시글을 추출했습니다.")

            if not posts:
                typer.echo(f"❌ 게시글을 추출하지 못했습니다.")
                typer.echo(f"💡 힌트: LinkedIn은 로그인이 필요할 수 있습니다.")

    except Exception as e:
        typer.echo(f"❌ 크롤링 중 오류 발생: {e}")

    return posts


async def crawl_x(count: int = 5) -> List[Post]:
    """X (Twitter) 크롤링 (구현 예정)"""
    typer.echo(f"🐦 X 크롤링 구현 예정")
    return []


async def crawl_geeknews(count: int = 5) -> List[Post]:
    """GeekNews에서 게시글을 크롤링합니다."""
    posts = []

    try:
        typer.echo(f"🤓 GeekNews 크롤링을 시작합니다... (게시글 {count}개)")

        async with async_playwright() as p:
            # 브라우저 실행
            browser = await p.chromium.launch(headless=False)

            # User-Agent를 포함한 컨텍스트 생성
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )

            page = await context.new_page()

            # GeekNews 메인 페이지로 이동
            await page.goto("https://news.hada.io/", wait_until="networkidle")
            typer.echo(f"✅ 페이지 로드 성공")

            # 페이지 로드 추가 대기
            await page.wait_for_timeout(2000)

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

            typer.echo(f"🔍 {len(post_elements)}개의 게시글 요소를 찾았습니다")

            # 게시글 데이터 추출
            for i, element in enumerate(post_elements[:count]):
                try:
                    # 제목/콘텐츠 추출 (GeekNews는 제목이 주요 콘텐츠)
                    title_link = await element.query_selector('a[href*="/topic/"]')
                    content_text = ""
                    post_url = None

                    if title_link:
                        content_text = await title_link.inner_text()
                        href = await title_link.get_attribute("href")
                        if href:
                            post_url = (
                                f"https://news.hada.io{href}"
                                if not href.startswith("http")
                                else href
                            )

                    # 작성자 정보 추출 (GeekNews는 보통 작성자가 명시되지 않음)
                    author = "GeekNews"
                    author_element = await element.query_selector(".author, .user, .by")
                    if author_element:
                        author_text = await author_element.inner_text()
                        if author_text and author_text.strip():
                            author = author_text.strip()

                    # 게시 시간 추출
                    time_element = await element.query_selector(
                        'time, .time, .date, span:has-text("시간"), span:has-text("일"), span:has-text("ago")'
                    )
                    timestamp = "알 수 없음"
                    if time_element:
                        time_text = await time_element.inner_text()
                        if time_text:
                            timestamp = time_text.strip()

                    # 댓글 수나 점수 추출
                    comments = 0
                    likes = 0

                    # 댓글 수 찾기
                    comment_elements = await element.query_selector_all(
                        'span:has-text("댓글"), .comment, a[href*="#comment"]'
                    )
                    for elem in comment_elements:
                        try:
                            text = await elem.inner_text()
                            numbers = "".join(filter(str.isdigit, text))
                            if numbers:
                                comments = int(numbers)
                                break
                        except:
                            pass

                    # 점수나 추천 수 찾기
                    score_elements = await element.query_selector_all(".score, .points, .vote")
                    for elem in score_elements:
                        try:
                            text = await elem.inner_text()
                            numbers = "".join(filter(str.isdigit, text))
                            if numbers:
                                likes = int(numbers)
                                break
                        except:
                            pass

                    # 추가 설명이나 요약 추출
                    description_element = await element.query_selector(
                        ".description, .summary, .excerpt"
                    )
                    if description_element:
                        desc_text = await description_element.inner_text()
                        if desc_text and len(desc_text.strip()) > 10:
                            content_text += f"\n{desc_text.strip()}"

                    # 유효한 게시글인지 확인
                    if content_text and len(content_text.strip()) > 10:
                        post = Post(
                            platform="geeknews",
                            author=author,
                            content=content_text.strip()[:500],
                            timestamp=timestamp,
                            url=post_url,
                            likes=likes if likes > 0 else None,
                            comments=comments if comments > 0 else None,
                            shares=None,
                        )
                        posts.append(post)
                        typer.echo(f"   ✅ 게시글 {len(posts)}: {author} - {content_text[:50]}...")
                    else:
                        typer.echo(
                            f"   ⚠️  게시글 {i+1}: 데이터 부족 - content_len={len(content_text) if content_text else 0}"
                        )

                except Exception as e:
                    typer.echo(f"   ❌ 게시글 {i+1} 파싱 중 오류: {e}")
                    continue

            await browser.close()
            typer.echo(f"📊 총 {len(posts)}개의 게시글을 추출했습니다.")

            if not posts:
                typer.echo(f"❌ 게시글을 추출하지 못했습니다.")
                typer.echo(f"💡 GeekNews 사이트 구조가 변경되었을 수 있습니다.")

    except Exception as e:
        typer.echo(f"❌ 크롤링 중 오류 발생: {e}")

    return posts


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

    # 첫 번째 게시글 미리보기
    if posts:
        first_post = posts[0]
        typer.echo(f"\n📄 첫 번째 게시글 미리보기:")
        typer.echo(f"   작성자: {first_post.author}")
        typer.echo(f"   내용: {first_post.content[:100]}...")
        typer.echo(f"   시간: {first_post.timestamp}")


@app.command()
def linkedin(
    count: int = typer.Option(5, "--count", "-c", help="수집할 게시글 수"),
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="출력 파일명 (기본: 자동 생성)"
    ),
):
    """
    LinkedIn에서 게시글을 크롤링합니다.

    예시:
    python main.py linkedin --count 10
    python main.py linkedin -c 3 -o my_linkedin.json
    """
    typer.echo(f"💼 LinkedIn 크롤링을 시작합니다... (게시글 {count}개)")

    # 비동기 함수 실행
    posts = asyncio.run(crawl_linkedin(count))

    if not posts:
        typer.echo("❌ 크롤링된 게시글이 없습니다.")
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

    # 첫 번째 게시글 미리보기
    if posts:
        first_post = posts[0]
        typer.echo(f"\n📄 첫 번째 게시글 미리보기:")
        typer.echo(f"   작성자: {first_post.author}")
        typer.echo(f"   내용: {first_post.content[:100]}...")
        typer.echo(f"   시간: {first_post.timestamp}")


@app.command()
def version():
    """버전 정보를 출력합니다."""
    typer.echo("SNS Crawler v0.1.0")
    typer.echo("Playwright 기반 크롤링 도구")


@app.command()
def status():
    """현재 상태를 확인합니다."""
    typer.echo("📋 SNS Crawler 상태:")
    typer.echo("   ✅ Threads 크롤러 - 구현 완료 (Playwright 기반)")
    typer.echo("   🔧 LinkedIn 크롤러 - 구현 완료 (테스트 필요)")
    typer.echo("   🔧 GeekNews 크롤러 - 구현 완료 (테스트 필요)")
    typer.echo("   ⏳ X 크롤러 - 개발 예정")
    typer.echo("   ⏳ Reddit 크롤러 - 개발 예정")


if __name__ == "__main__":
    app()
