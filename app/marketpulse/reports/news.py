"""Convert offline market-news source results into report snapshot data."""

from datetime import datetime
from typing import Iterable
from urllib.parse import urlsplit

from marketpulse.data.market_news import NewsUnavailableReason
from marketpulse.reports.snapshot import (
    ReportArticle,
    ReportArticleCategory,
    ReportArticleSection,
    ReportArticleSections,
    ReportArticleStatus,
)


MAX_SOURCE_LISTING_ARTICLES = 15


def build_report_article_section(
    category: ReportArticleCategory,
    status: ReportArticleStatus,
    articles: Iterable[ReportArticle] = (),
    *,
    retrieved_at: datetime | None = None,
    unavailable_reason: NewsUnavailableReason | None = None,
) -> ReportArticleSection:
    """Deduplicate and cap one source listing while preserving feed order."""
    if status is not ReportArticleStatus.AVAILABLE:
        return ReportArticleSection(
            category=category,
            status=status,
            retrieved_at=retrieved_at,
            unavailable_reason=unavailable_reason,
        )
    unique: list[ReportArticle] = []
    seen_urls: set[str] = set()
    for article in articles:
        if not isinstance(article, ReportArticle) or article.source_category is not category:
            raise ValueError("Every article must match the supplied source-page category.")
        parts = urlsplit(article.article_url)
        identity = (
            f"{parts.scheme.lower()}://{parts.netloc.lower()}"
            f"{parts.path or '/'}?{parts.query}"
        )
        if identity not in seen_urls:
            seen_urls.add(identity)
            unique.append(article)
            if len(unique) == MAX_SOURCE_LISTING_ARTICLES:
                break
    return ReportArticleSection(
        category=category,
        status=status,
        retrieved_at=retrieved_at,
        articles=tuple(unique),
        unavailable_reason=unavailable_reason,
    )


def build_report_article_sections(
    stock_market: ReportArticleSection,
    economic: ReportArticleSection,
    company: ReportArticleSection,
) -> ReportArticleSections:
    """Validate and group the three independently collected listing sections."""
    return ReportArticleSections(stock_market, economic, company)


