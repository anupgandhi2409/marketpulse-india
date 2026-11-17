"""Test source-page article sections with original synthetic records only."""

import re
import unittest
from dataclasses import replace
from datetime import datetime, timezone

from marketpulse.data.market_news import NewsUnavailableReason
from marketpulse.reports.news import (
    build_report_article_section,
    build_report_article_sections,
)
from marketpulse.reports.pdf_renderer import ReportPdfRenderer
from marketpulse.reports.snapshot import (
    ReportArticle,
    ReportArticleCategory,
    ReportArticlePublisher,
    ReportArticleStatus,
)
from tests.test_report_snapshot import _snapshot


RETRIEVED = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


class ReportSourceNewsTests(unittest.TestCase):
    """Verify independent listing categories, limits, states, and safe PDF links."""

    def _article(self, category, index, **changes):
        """Build one original synthetic listing article with overridable fields."""
        values = {
            "headline": f"Synthetic market update {index}",
            "article_url": f"https://economictimes.indiatimes.com/news/synthetic/{category.value}/{index}.html",
            "source_category": category,
            "retrieved_at": RETRIEVED,
        }
        values.update(changes)
        return ReportArticle(**values)

    def test_each_listing_is_independent_and_capped_at_fifteen(self):
        """Cap each category separately and retain category-specific source order."""
        categories = (
            ReportArticleCategory.STOCK_MARKET_NEWS,
            ReportArticleCategory.ECONOMIC_NEWS,
            ReportArticleCategory.COMPANY_NEWS,
        )
        sections = tuple(
            build_report_article_section(
                category,
                ReportArticleStatus.AVAILABLE,
                (self._article(category, index) for index in range(18)),
                retrieved_at=RETRIEVED,
            )
            for category in categories
        )
        grouped = build_report_article_sections(*sections)

        self.assertEqual(tuple(len(section.articles) for section in sections), (15, 15, 15))
        self.assertEqual(grouped.stock_market.articles[0].headline, "Synthetic market update 0")
        self.assertTrue(all(item.source_category is categories[2]
                            for item in grouped.company.articles))
        self.assertEqual(grouped.company.articles[0].headline, "Synthetic market update 0")

    def test_fewer_items_deduplicate_urls_and_preserve_non_nifty_company(self):
        """Keep listing membership and original order without NIFTY filtering."""
        category = ReportArticleCategory.COMPANY_NEWS
        articles = (
            self._article(
                category,
                1,
                headline="Synthetic private issuer expansion",
                article_url="https://economictimes.indiatimes.com/news/synthetic/company/1.html",
            ),
            self._article(
                category,
                2,
                headline="Duplicate URL must be dropped",
                article_url="https://economictimes.indiatimes.com/news/synthetic/company/1.html#latest",
            ),
            self._article(category, 3, headline="Synthetic infrastructure order update"),
        )
        section = build_report_article_section(
            category,
            ReportArticleStatus.AVAILABLE,
            articles,
            retrieved_at=RETRIEVED,
        )

        self.assertEqual(len(section.articles), 2)
        self.assertEqual(
            [item.headline for item in section.articles],
            ["Synthetic private issuer expansion", "Synthetic infrastructure order update"],
        )

    def test_empty_available_unavailable_and_unconfigured_are_distinct(self):
        """Preserve zero-item success separately from failure and no configuration."""
        category = ReportArticleCategory.ECONOMIC_NEWS
        available_empty = build_report_article_section(
            category, ReportArticleStatus.AVAILABLE, retrieved_at=RETRIEVED
        )
        unavailable = build_report_article_section(
            category,
            ReportArticleStatus.UNAVAILABLE,
            retrieved_at=RETRIEVED,
            unavailable_reason=NewsUnavailableReason.SOURCE_ERROR,
        )
        unconfigured = build_report_article_section(
            category, ReportArticleStatus.NOT_CONFIGURED
        )

        self.assertEqual(available_empty.articles, ())
        self.assertEqual(available_empty.status, ReportArticleStatus.AVAILABLE)
        self.assertEqual(unavailable.status, ReportArticleStatus.UNAVAILABLE)
        self.assertEqual(unconfigured.status, ReportArticleStatus.NOT_CONFIGURED)
        self.assertIsNone(unconfigured.retrieved_at)

    def test_model_accepts_only_et_urls_and_supported_publisher(self):
        """Accept the canonical ET host and reject unsafe links or publishers."""
        category = ReportArticleCategory.STOCK_MARKET_NEWS
        for url in (
            "http://economictimes.indiatimes.com/story",
            "https://user:secret@economictimes.indiatimes.com/story",
            "https://example.com/story",
            "https://economictimes.indiatimes.com.example.org/story",
            "https://evil.economictimes.indiatimes.com/story",
            "https://economictimes.indiatimes.com:8443/story",
            "javascript:alert(1)",
            "https://economictimes.indiatimes.com/story\nmalicious",
            "https://economictimes.indiatimes.com/story\x7fmalicious",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                self._article(category, 1, article_url=url)
        with self.assertRaises(ValueError):
            self._article(category, 1, retrieved_at=datetime(2026, 11, 17, 12))
        with self.assertRaises(ValueError):
            self._article(category, 1, publisher="Other Publisher")
        article = self._article(category, 1)
        self.assertIs(article.publisher, ReportArticlePublisher.ECONOMIC_TIMES)

    def test_pdf_renders_three_headings_unicode_wrapped_items_and_direct_links(self):
        """Render all categories with optional metadata and links to original URLs."""
        stock = ReportArticleCategory.STOCK_MARKET_NEWS
        economic = ReportArticleCategory.ECONOMIC_NEWS
        company = ReportArticleCategory.COMPANY_NEWS
        stock_article = self._article(
            stock,
            1,
            headline="सिंथेटिक बाजार अपडेट café <synthetic> " + ("long headline " * 18),
            published_at=RETRIEVED,
        )
        stock_articles = (stock_article,) + tuple(
            self._article(
                stock,
                index,
                headline=f"Synthetic long market headline {index} " + ("wrapped detail " * 65),
            )
            for index in range(1, 15)
        )
        sections = build_report_article_sections(
            build_report_article_section(stock, ReportArticleStatus.AVAILABLE,
                                         stock_articles, retrieved_at=RETRIEVED),
            build_report_article_section(economic, ReportArticleStatus.AVAILABLE,
                                         retrieved_at=RETRIEVED),
            build_report_article_section(company, ReportArticleStatus.NOT_CONFIGURED),
        )
        renderer = ReportPdfRenderer()
        baseline = renderer.render(_snapshot())
        pdf = renderer.render(replace(_snapshot(), source_news=sections))

        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertGreater(
            len(re.findall(rb"/Type /Page\b", pdf)),
            len(re.findall(rb"/Type /Page\b", baseline)),
        )
        self.assertNotIn(b"Economic Times News", pdf)
        self.assertLess(pdf.index(b"Major Market Indices"), pdf.index(b"Commodities"))
        self.assertLess(pdf.index(b"Commodities"), pdf.index(b"Last Trading Day Data"))
        self.assertLess(pdf.index(b"Last Trading Day Data"), pdf.index(b"Stock Market News"))
        self.assertEqual(pdf.count(b"Stock Market News"), 1)
        self.assertIn(b"Economic News", pdf)
        self.assertIn(b"Company News", pdf)
        self.assertIn(b"No articles available.", pdf)
        self.assertIn(b"Status: Not configured", pdf)
        self.assertIn(b"/URI", pdf)
        self.assertIn(b"The Economic Times", pdf)
        self.assertIn(b"Published: 17 Nov 2026 17:30:00 IST", pdf)
        self.assertIn(b"Published: Unavailable", pdf)
        self.assertNotIn(b"Excluded without publication time", pdf)
        self.assertIn(stock_article.article_url.encode(), pdf)
        self.assertNotIn(b"Synthetic source description", pdf)
        self.assertNotIn(b"<synthetic>", pdf)
        self.assertNotIn(b"Sources, Coverage, and Freshness", pdf)
        self.assertNotIn(b"1. Stock Market News", pdf)

    def test_news_flowables_use_bullets_without_numbering_or_summaries(self):
        """Render a bullet link and publication metadata without summary text."""
        article = self._article(
            ReportArticleCategory.STOCK_MARKET_NEWS,
            90,
            headline="Synthetic headline",
            published_at=None,
        )
        flowables = ReportPdfRenderer()._source_article_flowables(article)
        self.assertEqual(flowables[0].bulletText, "•")
        self.assertIn(article.article_url, flowables[0].text)
        self.assertIn("Synthetic headline", flowables[0].text)
        self.assertNotIn("<u>", flowables[0].text)
        self.assertFalse(flowables[0].text.startswith("1."))
        self.assertEqual(
            flowables[1].text,
            "The Economic Times | Published: Unavailable",
        )
        self.assertNotIn("Do not display", " ".join(
            getattr(flowable, "text", "") for flowable in flowables
        ))

    def test_all_sections_absent_preserves_existing_pdf_behavior(self):
        """Keep legacy market-data rendering unchanged when listing data is absent."""
        snapshot = _snapshot()
        self.assertIsNone(snapshot.source_news)
        pdf = ReportPdfRenderer().render(snapshot)
        self.assertNotIn(b"Sources, Coverage, and Freshness", pdf)
        self.assertNotIn(b"Stock Market News", pdf)

    def test_pdf_keeps_unavailable_listing_separate_from_empty_and_unconfigured(self):
        """Render independent category states without suppressing other sections."""
        stock = ReportArticleCategory.STOCK_MARKET_NEWS
        economic = ReportArticleCategory.ECONOMIC_NEWS
        company = ReportArticleCategory.COMPANY_NEWS
        sections = build_report_article_sections(
            build_report_article_section(
                stock,
                ReportArticleStatus.UNAVAILABLE,
                retrieved_at=RETRIEVED,
                unavailable_reason=NewsUnavailableReason.SOURCE_ERROR,
            ),
            build_report_article_section(
                economic, ReportArticleStatus.AVAILABLE, retrieved_at=RETRIEVED
            ),
            build_report_article_section(company, ReportArticleStatus.NOT_CONFIGURED),
        )

        pdf = ReportPdfRenderer().render(replace(_snapshot(), source_news=sections))

        self.assertIn(b"Stock Market News", pdf)
        self.assertIn(b"Status: Unavailable - Source Error", pdf)
        self.assertIn(b"Economic News", pdf)
        self.assertIn(b"No articles available.", pdf)
        self.assertIn(b"Company News", pdf)
        self.assertIn(b"Status: Not configured", pdf)


if __name__ == "__main__":
    unittest.main()
