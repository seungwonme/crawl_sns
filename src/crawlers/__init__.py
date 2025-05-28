"""
@file __init__.py
@description 크롤러 모듈 초기화

이 모듈은 모든 SNS 플랫폼 크롤러를 통합 관리하는 패키지 초기화 파일입니다.

주요 기능:
1. 베이스 크롤러 클래스 노출
2. 플랫폼별 크롤러 임포트 간소화
3. 크롤러 팩토리 패턴 지원

@dependencies
- .base: 베이스 크롤러 클래스
"""

from .base import BaseCrawler

__all__ = ["BaseCrawler"]
