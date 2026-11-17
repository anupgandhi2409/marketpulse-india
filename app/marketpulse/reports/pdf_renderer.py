"""Render validated MarketPulse report snapshots as deterministic PDF bytes."""

from datetime import datetime
from decimal import Decimal
from functools import partial
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    LongTable,
    Paragraph,
    PageBreak,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from marketpulse.analytics.market_movers import StockChange
from marketpulse.data.commodity_futures_quote import CommodityFuturesQuote
from marketpulse.data.gold_futures_quote import GoldFuturesQuote
from marketpulse.data.index_quote import IndexQuote
from marketpulse.data.market_movers_service import MarketMoversResult
from marketpulse.data.sector_performance import SectorPerformance
from marketpulse.data.silver_futures_quote import SilverFuturesQuote
from marketpulse.data.year_high_service import StocksAt52WeekHighResult
from marketpulse.reports.snapshot import (
    ReportArticle,
    ReportArticleSection,
    ReportArticleSections,
    ReportArticleStatus,
    ReportSection,
    ReportSnapshot,
    SectionStatus,
)

IST = ZoneInfo("Asia/Kolkata")
PAGE_BG = colors.HexColor("#F5F5F0")
CARD_BG = PAGE_BG
INK = colors.HexColor("#292929")
MUTED = colors.HexColor("#858585")
RULE = colors.HexColor("#E4E1DC")
POSITIVE = colors.HexColor("#287A45")
NEGATIVE = colors.HexColor("#C23A3A")
NEUTRAL = colors.HexColor("#888888")
SECTOR_POSITIVE = colors.HexColor("#A6CF98")
SECTOR_NEGATIVE = colors.HexColor("#FF8080")
SECTOR_NEUTRAL = colors.HexColor("#E5E4DD")
PAGE_WIDTH, _ = A4
PAGE_MARGIN = 15 * mm


class _ReportCard(Flowable):
    """Draw a compact, content-aligned quote card with regular change text."""

    def __init__(
        self,
        title: str,
        value: str,
        change: str,
        direction: str,
        width: float,
    ) -> None:
        """Store the three shared card lines and their sign color."""
        super().__init__()
        self.title = title
        self.value = value
        self.change = change
        self.direction = direction
        self.card_width = width
        registered_fonts = pdfmetrics.getRegisteredFontNames()
        self.value_font = (
            "MarketPulseNewsUnicode-Bold"
            if "MarketPulseNewsUnicode-Bold" in registered_fonts
            else "Helvetica-Bold"
        )
        self.height = 52

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        """Fit the card to its table cell while keeping a consistent height."""
        self.width = min(self.card_width, avail_width)
        return self.width, self.height

    def draw(self) -> None:
        """Paint the card surface and its three aligned text rows."""
        self.canv.setFillColor(CARD_BG)
        self.canv.setStrokeColor(RULE)
        self.canv.setLineWidth(0.7)
        self.canv.roundRect(0, 0, self.width, self.height, 7, stroke=1, fill=1)
        inner_width = max(1, self.width - 16)
        title = Paragraph(self.title, ParagraphStyle(
            "CardTitle", fontName="Helvetica-Bold", fontSize=7.4,
            leading=9, textColor=INK,
        ))
        value = Paragraph(self.value, ParagraphStyle(
            "CardValue", fontName=self.value_font, fontSize=14,
            leading=17, textColor=INK,
        ))
        color = {"positive": POSITIVE, "negative": NEGATIVE}.get(self.direction, NEUTRAL)
        change = Paragraph(self.change, ParagraphStyle(
            "CardChange", fontName="Helvetica", fontSize=7.2,
            leading=9, textColor=color,
        ))
        y = self.height - 7
        for paragraph in (title, value, change):
            _, height = paragraph.wrap(inner_width, self.height)
            y -= height
            paragraph.drawOn(self.canv, 8, y)
            y -= 1.5


class _SectorTile(Flowable):
    """Draw one rounded sector tile with three non-overlapping text lines."""

    def __init__(self, sector: SectorPerformance) -> None:
        """Capture the validated label and values for one colored heatmap tile."""
        super().__init__()
        self.sector = sector
        self.height = 56

    def wrap(self, avail_width: float, avail_height: float) -> tuple[float, float]:
        """Use the grid cell width while keeping each row's tile height uniform."""
        self.width = max(1, avail_width)
        return self.width, self.height

    def draw(self) -> None:
        """Paint the rounded fill and vertically center the tile's three text lines."""
        self.canv.setFillColor(_sector_fill_color(self.sector.percent_change))
        self.canv.roundRect(0, 0, self.width, self.height, 7, stroke=0, fill=1)
        inner_width = max(1, self.width - 14)
        styles = (
            ParagraphStyle("SectorName", fontName="Helvetica-Bold", fontSize=7.1,
                           leading=8.3, textColor=INK),
            ParagraphStyle("SectorChange", fontName="Helvetica-Bold", fontSize=10.2,
                           leading=12, textColor=INK),
            ParagraphStyle("SectorIndex", fontName="Helvetica", fontSize=6.8,
                           leading=8, textColor=INK),
        )
        contents = (
            escape(self.sector.sector_name),
            _format_percent(self.sector.percent_change),
            f"Index value: {_format_decimal(self.sector.index_value)}",
        )
        paragraphs: list[tuple[Paragraph, float]] = []
        for text, style in zip(contents, styles, strict=True):
            paragraph = Paragraph(text, style)
            _, height = paragraph.wrap(inner_width, self.height)
            paragraphs.append((paragraph, height))
        gap = 2
        total_height = sum(height for _, height in paragraphs) + gap * (len(paragraphs) - 1)
        y = (self.height + total_height) / 2
        for paragraph, height in paragraphs:
            y -= height
            paragraph.drawOn(self.canv, 7, y)
            y -= gap


class ReportPdfError(ValueError):
    """Signal an invalid report snapshot or a safe PDF rendering failure."""


class ReportPdfRenderer:
    """Render the Morning report snapshot using A4 Platypus flowables."""

    def __init__(self) -> None:
        """Prepare report styles and a system Unicode font for source headlines."""
        base = getSampleStyleSheet()
        article_font = _register_unicode_font()
        article_bold_font = "MarketPulseNewsUnicode-Bold" if article_font == "MarketPulseNewsUnicode" else "Helvetica-Bold"
        self._unicode_font = article_font
        self._styles = {
            "title": ParagraphStyle(
                "MarketPulseTitle", parent=base["Title"], fontName="Helvetica-Bold",
                fontSize=21, leading=25, textColor=INK, alignment=TA_LEFT, spaceAfter=3,
            ),
            "section": ParagraphStyle(
                "MarketPulseSection", parent=base["Heading2"], fontName="Helvetica-Bold",
                fontSize=12, leading=15, textColor=INK, spaceBefore=10, spaceAfter=3,
                keepWithNext=True,
            ),
            "subsection": ParagraphStyle(
                "MarketPulseSubsection", parent=base["Heading3"], fontName="Helvetica-Bold",
                fontSize=9, leading=11, textColor=INK, spaceBefore=3, spaceAfter=3,
                keepWithNext=True,
            ),
            "body": ParagraphStyle(
                "MarketPulseBody", parent=base["BodyText"], fontName="Helvetica",
                fontSize=8, leading=11, textColor=INK, spaceAfter=3,
            ),
            "small": ParagraphStyle(
                "MarketPulseSmall", parent=base["BodyText"], fontName="Helvetica",
                fontSize=6.8, leading=8.6, textColor=MUTED, spaceAfter=2,
            ),
            "cell_center": ParagraphStyle(
                "MarketPulseCellCenter", parent=base["BodyText"], fontName="Helvetica",
                fontSize=7.2, leading=9, textColor=INK, alignment=TA_CENTER,
            ),
            "cell_center_unicode": ParagraphStyle(
                "MarketPulseCellCenterUnicode", parent=base["BodyText"], fontName=article_bold_font,
                fontSize=7.2, leading=9, textColor=INK, alignment=TA_CENTER,
            ),
            "empty": ParagraphStyle(
                "MarketPulseEmpty", parent=base["BodyText"], fontName="Helvetica-Oblique",
                fontSize=8, leading=11, textColor=MUTED, spaceBefore=4, spaceAfter=5,
            ),
            "article_headline": ParagraphStyle(
                "MarketPulseArticleHeadline", parent=base["BodyText"], fontName=article_font,
                fontSize=8.3, leading=10, textColor=INK, leftIndent=10,
                bulletIndent=2, bulletFontName=article_font, bulletFontSize=5.5,
                bulletColor=MUTED, spaceAfter=1,
            ),
            "article_status": ParagraphStyle(
                "MarketPulseArticleStatus", parent=base["BodyText"], fontName=article_font,
                fontSize=6.2, leading=7.2, textColor=MUTED, leftIndent=10, spaceAfter=1,
            ),
        }

    def render(self, snapshot: ReportSnapshot) -> bytes:
        """Return an A4 PDF for the supplied snapshot without collecting data."""
        if not isinstance(snapshot, ReportSnapshot):
            raise ReportPdfError("A valid ReportSnapshot is required.")
        try:
            snapshot.__post_init__()
        except Exception:
            raise ReportPdfError("The supplied report snapshot is structurally invalid.") from None

        story = self._build_story(snapshot)
        buffer = BytesIO()
        document = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=PAGE_MARGIN,
            rightMargin=PAGE_MARGIN,
            topMargin=19 * mm,
            bottomMargin=17 * mm,
            title="Morning Market Pulse",
            author="",
            pageCompression=0,
        )
        try:
            page_callback = partial(self._draw_page_chrome, snapshot=snapshot)
            document.build(story, onFirstPage=page_callback, onLaterPages=page_callback)
        except ReportPdfError:
            raise
        except Exception:
            raise ReportPdfError("The report PDF could not be rendered from this snapshot.") from None
        return buffer.getvalue()

    def _build_story(self, snapshot: ReportSnapshot) -> list[Any]:
        """Build ordered report flowables from the snapshot's existing section models."""
        story: list[Any] = [
            Paragraph("Morning Market Pulse", self._styles["title"]),
            Paragraph(
                f"Report date: {snapshot.report_date.strftime('%d %B %Y')}"
                f" &nbsp;&nbsp; Generated at: {_format_timestamp(snapshot.generated_at)}",
                self._styles["body"],
            ),
            Spacer(1, 2),
        ]
        self._render_indices(story, snapshot.indices)
        self._render_commodities(story, snapshot)
        story.append(Paragraph("Last Trading Day Data", self._styles["section"]))
        self._render_sectors(story, snapshot.sectors)
        story.append(PageBreak())
        self._render_market_movers(story, snapshot.market_movers)
        self._render_year_highs(story, snapshot.stocks_at_52_week_high)
        if snapshot.source_news is not None:
            self._render_source_news(story, snapshot.source_news)
        return story

    def _render_source_news(
        self,
        story: list[Any],
        sections: ReportArticleSections,
    ) -> None:
        """Render the three capped news categories at the end of the Morning report."""
        headings = (
            ("Stock Market News", sections.stock_market),
            ("Economic News", sections.economic),
            ("Company News", sections.company),
        )
        for title, section in headings:
            intro, remaining = self._source_article_section_intro(title, section)
            story.append(KeepTogether(intro))
            for article in remaining:
                story.append(KeepTogether(self._source_article_flowables(article)))

    def _source_article_section_intro(
        self,
        title: str,
        section: ReportArticleSection,
    ) -> tuple[list[Any], tuple[ReportArticle, ...]]:
        """Build one listing heading, status, and first article for a page-safe group."""
        heading = Paragraph(escape(title), self._styles["section"])
        if section.status is ReportArticleStatus.NOT_CONFIGURED:
            return [
                heading,
                Paragraph("Status: Not configured", self._styles["article_status"]),
            ], ()
        if section.status is ReportArticleStatus.UNAVAILABLE:
            reason = section.unavailable_reason.value.replace("_", " ").title()
            return [
                heading,
                Paragraph(f"Status: Unavailable - {escape(reason)}", self._styles["article_status"]),
            ], ()
        if not section.articles:
            return [
                heading,
                Paragraph("No articles available.", self._styles["empty"]),
            ], ()
        return [
            heading,
            *self._source_article_flowables(section.articles[0]),
        ], section.articles[1:]

    def _source_article_flowables(
        self,
        article: ReportArticle,
    ) -> list[Any]:
        """Build an unnumbered linked headline and compact publication metadata."""
        url = escape(article.article_url, quote=True)
        headline = escape(article.headline)
        linked_headline = Paragraph(
            f"<link href=\"{url}\" color=\"#292929\">{headline}</link>",
            self._styles["article_headline"],
            bulletText="•",
        )
        published = (
            _format_timestamp(article.published_at)
            if article.published_at is not None else "Unavailable"
        )
        article_flowables = [
            Paragraph(
                f"{escape(article.publisher.value)} | Published: {escape(published)}",
                self._styles["article_status"],
            ),
            Spacer(1, 4),
        ]
        return [linked_headline, *article_flowables]

    def _render_indices(self, story: list[Any], section: ReportSection) -> None:
        """Render six ordered index cards without inventing unavailable changes."""
        self._section_heading(story, "Major Market Indices", section)
        expected = ("NIFTY 50", "BANK NIFTY", "NIFTY IT", "NIFTY MIDCAP 100", "NIFTY SMALLCAP 100", "SENSEX")
        quotes = {} if section.status is SectionStatus.UNAVAILABLE else {
            ("BANK NIFTY" if quote.index_name == "NIFTY BANK" else quote.index_name): quote
            for quote in section.data
            if isinstance(quote, IndexQuote)
        }
        if section.status is not SectionStatus.UNAVAILABLE and len(quotes) != 6:
            raise ReportPdfError("Index section does not contain six validated records.")
        width = (PAGE_WIDTH - 2 * PAGE_MARGIN - 16) / 3
        cards = [
            _ReportCard(
                name,
                _format_decimal(quotes[name].index_value) if name in quotes else "—",
                _index_card_changes(quotes.get(name)),
                _index_direction(quotes.get(name)),
                width,
            )
            for name in expected
        ]
        table = Table([cards[:3], cards[3:]], colWidths=[width] * 3, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(KeepTogether(table))

    def _render_sectors(self, story: list[Any], section: ReportSection) -> None:
        """Render twelve rounded sector tiles with equal horizontal and vertical gaps."""
        self._section_heading(story, "Sector Performance Heatmap", section)
        if section.status is SectionStatus.UNAVAILABLE:
            self._unavailable_body(story, section)
            return
        sectors = section.data
        if len(sectors) != 12 or any(not isinstance(item, SectorPerformance) for item in sectors):
            raise ReportPdfError("Sector section does not contain complete coverage.")
        cell_width = (PAGE_WIDTH - 2 * PAGE_MARGIN) / 4
        rows = [
            [_SectorTile(item) for item in sectors[offset : offset + 4]]
            for offset in range(0, 12, 4)
        ]
        table = Table(
            rows,
            colWidths=[cell_width] * 4,
            hAlign="LEFT",
            style=TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2.5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2.5),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ]),
        )
        story.append(table)

    def _render_market_movers(self, story: list[Any], section: ReportSection) -> None:
        """Render gainers and losers in their existing ranking order."""
        group: list[Any] = []
        self._section_heading(group, "NIFTY 100 Top Gainers and Losers", section)
        if section.status is SectionStatus.UNAVAILABLE:
            self._unavailable_body(group, section)
            story.append(KeepTogether(group))
            return
        result = section.data
        if not isinstance(result, MarketMoversResult) or not result.coverage_complete:
            raise ReportPdfError("Market-movers section is invalid or incomplete.")
        prices = {quote.symbol: quote.last_traded_price for quote in result.quotes}
        gainers = self._mover_table(result.gainers[:10], prices)
        losers = self._mover_table(result.losers[:10], prices)
        columns = Table(
            [[
                [Paragraph("Top 10 Gainers", self._styles["subsection"]), gainers],
                [Paragraph("Top 10 Losers", self._styles["subsection"]), losers],
            ]],
            colWidths=[(PAGE_WIDTH - 2 * PAGE_MARGIN) / 2] * 2,
            hAlign="LEFT",
        )
        columns.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        group.append(columns)
        story.append(KeepTogether(group))

    def _mover_table(
        self,
        changes: tuple[StockChange, ...],
        prices: dict[str, Decimal],
    ) -> LongTable:
        """Build a compact ranked table with changes colored by actual sign."""
        rows = [[
            Paragraph(label, self._styles["cell_center_unicode"])
                for label in ("#", "Symbol", "Change", "Quote value (₹)")
        ]]
        commands = _base_table_commands()
        for index, change in enumerate(changes, start=1):
            if not isinstance(change, StockChange) or change.symbol not in prices:
                raise ReportPdfError("Market-movers ranking contains invalid data.")
            color = _direction_style(change.percent_change)
            rows.append([
                str(index),
                escape(change.symbol),
                Paragraph(
                    f"<font color='{color.hexval()}'><b>"
                    f"{_format_percent(change.percent_change)}</b></font>",
                    self._styles["cell_center"],
                ),
                _format_decimal(prices[change.symbol]),
            ])
        return LongTable(
            rows,
            colWidths=[15, 55, 65, 90],
            repeatRows=1,
            hAlign="LEFT",
            style=TableStyle(commands + [
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ]),
        )

    def _render_year_highs(self, story: list[Any], section: ReportSection) -> None:
        """Render qualified 52-week highs or the successful empty-scan message."""
        group: list[Any] = []
        self._section_heading(group, "NIFTY 100 Stocks at 52-Week High", section)
        if section.status is SectionStatus.UNAVAILABLE:
            self._unavailable_body(group, section)
            story.append(KeepTogether(group))
            return
        result = section.data
        if not isinstance(result, StocksAt52WeekHighResult) or not result.coverage_complete:
            raise ReportPdfError("52-week-high section is invalid or incomplete.")
        if not result.stocks:
            group.append(Paragraph(
                "No NIFTY 100 stocks meet the 52-week-high criterion in this snapshot.",
                self._styles["empty"],
            ))
            story.append(KeepTogether(group))
            return
        rows = [[
            Paragraph(label, self._styles["cell_center_unicode"])
            for label in ("#", "Stock symbol", "LTP (₹)", "Reported 52-week high (₹)")
        ]]
        for rank, stock in enumerate(result.stocks[:10], start=1):
            rows.append([
                str(rank),
                escape(stock.symbol),
                _format_decimal(stock.last_traded_price),
                _format_decimal(stock.year_high),
            ])
        group.append(self._data_table(rows, [25, 125, 145, 185]))
        story.append(KeepTogether(group))

    def _render_commodities(self, story: list[Any], snapshot: ReportSnapshot) -> None:
        """Render the three commodity prices and validated changes as matching cards."""
        story.append(Paragraph("Commodities", self._styles["section"]))
        sections = (
            ("Gold", snapshot.gold, GoldFuturesQuote),
            ("Silver", snapshot.silver, SilverFuturesQuote),
            ("Crude Oil", snapshot.crude_oil, CommodityFuturesQuote),
        )
        width = (PAGE_WIDTH - 2 * PAGE_MARGIN - 16) / 3
        cards: list[_ReportCard] = []
        unavailable: list[str] = []
        for name, section, expected_type in sections:
            if section.status is SectionStatus.UNAVAILABLE:
                quote = None
                unavailable.append(f"{name}: {section.unavailable_reason or 'Unavailable.'}")
            else:
                quote = section.data
                if not isinstance(quote, expected_type):
                    raise ReportPdfError(f"{name} section contains invalid data.")
            value = f"₹{_format_decimal(quote.price)}" if quote is not None else "—"
            change = (
                f"Change: {_format_signed_decimal(quote.absolute_change)} "
                f"({_format_percent(quote.percentage_change)})"
                if quote is not None else "—"
            )
            direction = (
                "positive" if quote is not None and quote.absolute_change > 0
                else "negative" if quote is not None and quote.absolute_change < 0
                else "neutral"
            )
            cards.append(_ReportCard(name, value, change, direction, width))
        table = Table([cards], colWidths=[width] * 3, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(KeepTogether(table))
        for message in unavailable:
            story.append(Paragraph(escape(message), self._styles["small"]))

    def _section_heading(
        self,
        story: list[Any],
        title: str,
        section: ReportSection | None,
    ) -> None:
        """Show section status, retrieval time, and coverage without provider branding."""
        story.append(Paragraph(escape(title), self._styles["section"]))
        if section is None:
            return
        if section.status is SectionStatus.UNAVAILABLE:
            story.append(Paragraph(
                f"<b>Status: Unavailable</b> - {escape(section.unavailable_reason or 'Unavailable.')}",
                self._styles["small"],
            ))
            return
        status = "Cached" if section.status is SectionStatus.CACHED else "Available"
        pieces = [f"<b>Status: {status}</b>"]
        if section.retrieved_at is not None:
            label = "Original retrieval" if section.cached else "Retrieved at"
            pieces.append(f"{label}: {_format_timestamp(section.retrieved_at)}")
        if section.coverage is not None:
            pieces.append(
                "Coverage: "
                f"{section.coverage.validated_count}/{section.coverage.expected_count}"
            )
        elif section.derived_from:
            pieces.append("Source timestamps are shown for the input sections")
        story.append(Paragraph(" &nbsp; | &nbsp; ".join(pieces), self._styles["small"]))

    def _unavailable_body(self, story: list[Any], section: ReportSection) -> None:
        """Display an unavailable section reason without dropping its heading."""
        table = Table(
            [[Paragraph(escape(section.unavailable_reason or "Unavailable."), self._styles["body"])]],
            colWidths=[PAGE_WIDTH - 2 * PAGE_MARGIN],
        )
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), PAGE_BG),
            ("BOX", (0, 0), (-1, -1), 0.4, RULE),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(table)

    def _data_table(
        self,
        rows: list[list[Any]],
        widths: list[float],
        font_size: float = 7.2,
    ) -> LongTable:
        """Build a paginating table with a repeated, styled header row."""
        table = LongTable(
            rows,
            colWidths=widths,
            repeatRows=1,
            hAlign="LEFT",
            style=TableStyle(
                _base_table_commands(font_size)
                + [("ALIGN", (0, 0), (-1, -1), "CENTER")]
            ),
        )
        return table

    def _draw_page_chrome(self, canvas: Any, document: Any, snapshot: ReportSnapshot) -> None:
        """Draw the ivory page, running report title, page number, and compact footer."""
        canvas.saveState()
        canvas.setFillColor(PAGE_BG)
        canvas.rect(0, 0, A4[0], A4[1], stroke=0, fill=1)
        canvas.setFillColor(INK)
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.drawString(PAGE_MARGIN, A4[1] - 10 * mm, "MORNING MARKET PULSE")
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(PAGE_MARGIN, A4[1] - 12 * mm, A4[0] - PAGE_MARGIN, A4[1] - 12 * mm)
        canvas.line(PAGE_MARGIN, 12 * mm, A4[0] - PAGE_MARGIN, 12 * mm)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 6.5)
        canvas.drawRightString(A4[0] - PAGE_MARGIN, 8.5 * mm, f"Page {document.page}")
        canvas.restoreState()


def _sector_fill_color(change: Decimal) -> colors.Color:
    """Choose the heatmap fill from the validated sector percentage sign."""
    if change > 0:
        return SECTOR_POSITIVE
    if change < 0:
        return SECTOR_NEGATIVE
    return SECTOR_NEUTRAL


def _index_card_changes(quote: IndexQuote | None) -> str:
    """Format index changes on one compact, regular-weight line."""
    point = quote.change if quote is not None else None
    percent = quote.percent_change if quote is not None else None
    point_text = _format_index_delta(point)
    percent_text = _format_index_delta(percent, percentage=True)
    return (
        f"Change: {escape(point_text)} ({escape(percent_text)})"
    )


def _index_direction(quote: IndexQuote | None) -> str:
    """Choose the card's fallback text color from the first available change field."""
    value = (
        quote.percent_change if quote is not None and quote.percent_change is not None
        else quote.change if quote is not None else None
    )
    if value is None or value == 0:
        return "neutral"
    return "positive" if value > 0 else "negative"


def _format_index_delta(value: Decimal | None, *, percentage: bool = False) -> str:
    """Format a supplied index change to two decimals without inventing missing values."""
    if value is None or not value.is_finite():
        return "—"
    formatted = "0.00" if value == 0 else f"{value:+,.2f}"
    return f"{formatted}%" if percentage else formatted


def _base_table_commands(font_size: float = 7.2) -> list[tuple[Any, ...]]:
    """Return shared accessible table styling with compact repeating headers."""
    return [
        ("BACKGROUND", (0, 0), (-1, -1), PAGE_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), INK),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), font_size),
        ("LEADING", (0, 0), (-1, -1), font_size + 2),
        ("GRID", (0, 0), (-1, -1), 0.3, RULE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]


def _register_unicode_font() -> str:
    """Register an available Unicode font with rupee and headline glyphs."""
    candidates = (
        ("/System/Library/Fonts/Supplemental/Devanagari Sangam MN.ttc", 0, 1),
        ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", None, None),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", None, None),
        ("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf", None, None),
        ("C:/Windows/Fonts/arial.ttf", None, None),
    )
    for candidate, subfont_index, bold_subfont_index in candidates:
        if Path(candidate).is_file():
            font_name = "MarketPulseNewsUnicode"
            if font_name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(
                    TTFont(font_name, candidate, subfontIndex=subfont_index)
                )
                bold_name = f"{font_name}-Bold"
                pdfmetrics.registerFont(
                    TTFont(
                        bold_name,
                        candidate,
                        subfontIndex=(bold_subfont_index if bold_subfont_index is not None else subfont_index),
                    )
                )
                pdfmetrics.registerFontFamily(font_name, normal=font_name, bold=bold_name)
            return font_name
    return "Helvetica"


def _direction_style(value: Decimal) -> colors.Color:
    """Choose readable text color from the exact sign of a financial value."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ReportPdfError("A market percentage is missing or invalid.")
    if value > 0:
        return POSITIVE
    if value < 0:
        return NEGATIVE
    return NEUTRAL


def _format_percent(value: Decimal) -> str:
    """Format a finite percentage with an explicit positive or negative sign."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ReportPdfError("A market percentage is missing or invalid.")
    return f"{_format_signed_decimal(value)}%"


def _format_signed_decimal(value: Decimal) -> str:
    """Format a finite Decimal with a sign and exactly two fractional digits."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ReportPdfError("A market value is missing or invalid.")
    rendered = f"{abs(value):,.2f}"
    if value > 0:
        return f"+{rendered}"
    if value < 0:
        return f"-{rendered}"
    return f"{rendered}"


def _format_decimal(value: Decimal) -> str:
    """Format a finite Decimal with grouping and exactly two fractional digits."""
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ReportPdfError("A market value is missing or invalid.")
    return f"{value:,.2f}"


def _format_timestamp(value: datetime) -> str:
    """Format an aware timestamp in IST without calling it exchange market time."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ReportPdfError("A report timestamp is missing or invalid.")
    return value.astimezone(IST).strftime("%d %b %Y %H:%M:%S IST")
