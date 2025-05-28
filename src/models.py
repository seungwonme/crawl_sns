"""
@file models.py
@description SNS 게시글 데이터 모델 정의

이 모듈은 SNS 플랫폼에서 크롤링한 게시글 데이터를 표현하는 Pydantic 모델을 제공합니다.

주요 기능:
1. Post 데이터 모델 - 모든 SNS 플랫폼의 공통 게시글 구조
2. 플랫폼별 추가 필드 지원 (extra = "allow")
3. 데이터 유효성 검증 및 직렬화

핵심 구현 로직:
- Pydantic BaseModel을 상속하여 타입 안전성 보장
- Optional 필드로 플랫폼별 차이점 수용
- JSON 직렬화/역직렬화 자동 지원

@dependencies
- pydantic: 데이터 모델링 및 유효성 검증

@see {@link /docs/data-models.md} - 데이터 모델 상세 문서
"""

from typing import Optional

from pydantic import BaseModel


class Post(BaseModel):
    """
    SNS 게시글 데이터 모델

    모든 SNS 플랫폼의 게시글을 표현하는 공통 데이터 구조입니다.
    플랫폼별 특성에 따라 일부 필드는 None일 수 있습니다.

    Attributes:
        platform (str): SNS 플랫폼 이름 (threads, linkedin, x, geeknews, reddit)
        author (str): 게시글 작성자 이름 또는 핸들
        content (str): 게시글 본문 내용
        timestamp (str): 게시 시간 (플랫폼별 형식)
        url (Optional[str]): 게시글 직접 링크
        likes (Optional[int]): 좋아요/추천 수
        comments (Optional[int]): 댓글 수
        shares (Optional[int]): 공유/리포스트 수
    """

    platform: str
    author: str
    content: str
    timestamp: str
    url: Optional[str] = None
    likes: Optional[int] = None
    comments: Optional[int] = None
    shares: Optional[int] = None

    class Config:
        extra = "allow"  # 플랫폼별 추가 필드 허용

    def __str__(self) -> str:
        """게시글 정보를 읽기 쉬운 형태로 반환"""
        return f"[{self.platform}] @{self.author}: {self.content[:50]}..."
