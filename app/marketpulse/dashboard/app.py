from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import escape

import pandas as pd
import streamlit as st
from pandas.io.formats.style import Styler
from marketpulse.config.settings import settings as app_settings
from marketpulse.dashboard.market_data import (
    CommodityMarketCard,
    DashboardMarketData,
    INDEX_WATCHLIST,
    SectorHeatmapTile,
    build_year_high_table,
    build_market_movers_tables,
    build_sector_heatmap_tiles,
    build_commodity_cards,
    create_dashboard_services,
    format_retrieved_at,
    refresh_dashboard_data,
    safe_error_message,
)
from marketpulse.data.index_quote import IndexQuote
from marketpulse.reports.file_store import ReportFileStoreError
from marketpulse.reports.generation import generate_market_report
from marketpulse.reports.snapshot import ReportSnapshot, SectionStatus

_DATA_STATE_KEY = "market_data_snapshot"
_ERROR_STATE_KEY = "market_data_errors"
_REPORT_DOWNLOAD_KEY = "generated_report_download"
_REPORT_ERROR_KEY = "report_generation_error"
_REPORT_SUPPRESS_DOWNLOAD_KEY = "report_download_suppressed"
MARKET_POSITIVE = "#287A45"
MARKET_NEGATIVE = "#C23A3A"
MARKET_NEUTRAL = "#888888"
SECTOR_POSITIVE = "#A6CF98"
SECTOR_NEGATIVE = "#FF8080"
SECTOR_NEUTRAL = "#E5E4DD"


def main() -> None:
    """Render the read-only dashboard and refresh datasets only on button click."""
    st.set_page_config(page_title="Morning Market Pulse", layout="wide")
    st.title("Morning Market Pulse")

    if _DATA_STATE_KEY not in st.session_state:
        st.session_state[_DATA_STATE_KEY] = DashboardMarketData()
        st.session_state[_ERROR_STATE_KEY] = ()
    st.session_state.setdefault(_REPORT_DOWNLOAD_KEY, None)
    st.session_state.setdefault(_REPORT_ERROR_KEY, None)
    st.session_state.setdefault(_REPORT_SUPPRESS_DOWNLOAD_KEY, False)

    if st.button("Refresh Market Data", type="primary"):
        with st.spinner("Retrieving market quotes..."):
            try:
                services = create_dashboard_services()
                outcome = refresh_dashboard_data(
                    services,
                    previous=st.session_state[_DATA_STATE_KEY],
                )
                st.session_state[_DATA_STATE_KEY] = outcome.market_data
                st.session_state[_ERROR_STATE_KEY] = outcome.errors
            except Exception as exc:
                # Service errors may contain SDK payloads, so only fixed safe text reaches UI.
                st.session_state[_ERROR_STATE_KEY] = (safe_error_message(exc, "market"),)

    commodity_prefixes = ("Gold futures", "Silver futures", "Crude Oil futures")
    for message in st.session_state[_ERROR_STATE_KEY]:
        if not message.startswith(commodity_prefixes):
            st.warning(message)
    _render_market_data(
        st.session_state[_DATA_STATE_KEY],
        st.session_state[_ERROR_STATE_KEY],
    )
    _render_pdf_reports()


def _render_pdf_reports() -> None:
    """Render the manual Morning report controls and expose only a saved PDF."""
    st.subheader("Generate / Download Morning PDF")
    st.caption(
        "PDF generation collects market data independently and may produce a different "
        "snapshot from the dashboard cards."
    )
    overwrite = st.checkbox(
        "Replace existing report",
        key="report_replace_existing",
    )
    if st.button("Generate PDF Report", key="generate_pdf_report"):
        st.session_state[_REPORT_SUPPRESS_DOWNLOAD_KEY] = True
        st.session_state[_REPORT_ERROR_KEY] = None
        if not app_settings.kotak_consumer_key:
            st.session_state[_REPORT_ERROR_KEY] = "Kotak Neo is not configured."
        else:
            try:
                with st.spinner("Collecting report data and generating PDF..."):
                    result = generate_market_report(overwrite=overwrite)
                    pdf_bytes = result.path.read_bytes()
                    if not pdf_bytes.startswith(b"%PDF-"):
                        raise OSError("Generated PDF failed local validation.")
                st.session_state[_REPORT_DOWNLOAD_KEY] = {
                    "pdf_bytes": pdf_bytes,
                    "filename": result.path.name,
                    "snapshot": result.snapshot,
                }
                st.session_state[_REPORT_SUPPRESS_DOWNLOAD_KEY] = False
            except ReportFileStoreError as error:
                if str(error) == "A report with this name already exists.":
                    message = (
                        "A report with this filename already exists. Enable “Replace "
                        "existing report” to replace it."
                    )
                else:
                    message = "The report PDF could not be saved."
                st.session_state[_REPORT_ERROR_KEY] = message
            except Exception:
                st.session_state[_REPORT_ERROR_KEY] = (
                    "The report could not be generated. Check credentials, local market "
                    "data files, and report storage, then try again."
                )

    error = st.session_state[_REPORT_ERROR_KEY]
    if error:
        st.error(error)

    artifact = st.session_state[_REPORT_DOWNLOAD_KEY]
    if artifact and not st.session_state[_REPORT_SUPPRESS_DOWNLOAD_KEY]:
        snapshot = artifact["snapshot"]
        available, unavailable = _report_section_summary(snapshot)
        st.success(
            f"Morning report for {snapshot.report_date} "
            f"generated at {_format_timestamp(snapshot.generated_at)}."
        )
        st.caption(f"File: {artifact['filename']}")
        if unavailable:
            st.caption(
                f"Available sections: {available} of {available + len(unavailable)} · "
                f"Unavailable: {', '.join(unavailable)}"
            )
        else:
            st.caption(f"Available sections: {available} of {available}")
        st.download_button(
            "Download PDF",
            data=artifact["pdf_bytes"],
            file_name=artifact["filename"],
            mime="application/pdf",
            key="download_generated_pdf",
        )


def _report_section_summary(snapshot: ReportSnapshot) -> tuple[int, tuple[str, ...]]:
    """Count available report sections and name any unavailable sections."""
    sections = (
        ("Market indices", snapshot.indices),
        ("Sector performance", snapshot.sectors),
        ("NIFTY 100 gainers and losers", snapshot.market_movers),
        ("NIFTY 100 52-week highs", snapshot.stocks_at_52_week_high),
        ("Gold futures", snapshot.gold),
        ("Silver futures", snapshot.silver),
        ("Crude Oil futures", snapshot.crude_oil),
    )
    unavailable = tuple(
        label
        for label, section in sections
        if section.status is SectionStatus.UNAVAILABLE
    )
    return len(sections) - len(unavailable), unavailable


def _render_market_data(
    market_data: DashboardMarketData,
    errors: tuple[str, ...] = (),
) -> None:
    """Render indices, commodities, and the quote panels in the approved dashboard order."""
    cards = build_commodity_cards(market_data, errors)
    if not market_data.index_quotes and not any(
        card.retrieved_at is not None for card in cards
    ) and not market_data.sector_performance and market_data.market_movers is None and market_data.stocks_at_52_week_high is None:
        st.info("No market data has been retrieved yet. Select Refresh Market Data to begin.")

    st.subheader("Major Market Indices")
    if market_data.index_retrieved_at is not None:
        index_cached = any(message.startswith("Index quotes") for message in errors)
        label = "Cached data · Retrieved at" if index_cached else "Retrieved at"
        st.caption(f"{label}: {_format_timestamp(market_data.index_retrieved_at)}")
    else:
        st.caption("Unavailable · No successful index retrieval yet.")
    index_quotes = {quote.index_name: quote for quote in market_data.index_quotes}
    index_cards: list[str] = []
    for index_name, _exchange in INDEX_WATCHLIST:
        quote = index_quotes.get(index_name)
        title = "BANK NIFTY" if index_name == "NIFTY BANK" else index_name
        value = _format_value(quote.index_value if quote is not None else None)
        point = _format_index_change(quote.change if quote is not None else None)
        percent = _format_index_change(
            quote.percent_change if quote is not None else None,
            percentage=True,
        )
        index_cards.append(
            _market_card_html(title, value, f"{point} ({percent})", _index_direction(quote))
        )
    st.markdown(_market_card_grid_html(index_cards), unsafe_allow_html=True)

    st.subheader("Commodities")
    commodity_cards: list[str] = []
    for card in cards:
        commodity_cards.append(_render_commodity_card(card))
        if card.error:
            st.warning(card.error)
    st.markdown(_market_card_grid_html(commodity_cards), unsafe_allow_html=True)
    st.subheader("Last Trading Day Data")
    _render_sector_heatmap(market_data, errors)
    _render_market_movers(market_data, errors)
    _render_year_highs(market_data, errors)


def _render_sector_heatmap(
    market_data: DashboardMarketData,
    errors: tuple[str, ...] = (),
) -> None:
    """Render the complete ordered sector heatmap or a clear unavailable state."""
    st.subheader("Sector Performance Heatmap")
    tiles = build_sector_heatmap_tiles(market_data.sector_performance, errors)
    if not tiles:
        st.info("NSE sector heatmap unavailable · No complete validated sector snapshot.")
        return

    state = "Cached data · Retrieved at" if tiles[0].cached else "Retrieved at"
    st.caption(f"{state}: {_format_timestamp(tiles[0].retrieved_at)}")
    st.markdown(_sector_heatmap_html(tiles), unsafe_allow_html=True)


def _sector_tile_html(tile: SectorHeatmapTile) -> str:
    """Build an escaped, sign-colored heatmap tile with high-contrast text."""
    from html import escape

    background = {
        "positive": SECTOR_POSITIVE,
        "negative": SECTOR_NEGATIVE,
        "neutral": SECTOR_NEUTRAL,
    }.get(tile.direction, SECTOR_NEUTRAL)
    name = escape(tile.sector_name)
    change = escape(tile.percent_change)
    value = escape(tile.index_value)
    return (
        f'<div style="background:{background};color:#292929;padding:9px 10px;'
        'border-radius:8px;min-height:76px;box-sizing:border-box;display:flex;'
        'flex-direction:column;justify-content:center;gap:2px">'
        f'<div style="font-weight:700;line-height:1.15">{name}</div>'
        f'<div style="font-size:1.05rem;font-weight:600;line-height:1.2">{change}</div>'
        f'<div style="font-size:.78rem;line-height:1.15">Index value: {value}</div></div>'
    )


def _sector_heatmap_html(tiles: tuple[SectorHeatmapTile, ...]) -> str:
    """Build a responsive four-column sector grid with equal row and column gaps."""
    return (
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));'
        'gap:8px;width:100%">'
        + "".join(_sector_tile_html(tile) for tile in tiles)
        + "</div>"
    )


def _render_market_movers(
    market_data: DashboardMarketData,
    errors: tuple[str, ...] = (),
) -> None:
    """Render validated NIFTY 100 gainers and losers in responsive side-by-side tables."""
    st.subheader("NIFTY 100 Top Gainers and Losers")
    tables = build_market_movers_tables(market_data.market_movers, errors)
    if tables is None:
        st.info("NIFTY 100 gainers and losers unavailable · No complete validated ranking.")
        return

    state = "Cached data · Retrieved at" if tables.cached else "Retrieved at"
    st.caption(f"{state}: {_format_timestamp(tables.retrieved_at)}")
    columns = st.columns(2)
    for column, title, rows in zip(
        columns, ("Top 10 Gainers", "Top 10 Losers"),
        (tables.gainers, tables.losers), strict=True,
    ):
        column.markdown(f"### {title}")
        column.markdown(
            _compact_html_table(
                ("#", "Stock", "Change", "Quote value (₹)"),
                tuple(
                    (
                        str(row.rank), row.symbol, row.percent_change,
                        row.last_traded_price,
                    )
                    for row in rows
                ),
                colors=tuple(
                    (
                        None, None,
                        MARKET_POSITIVE if row.direction == "positive"
                        else MARKET_NEGATIVE if row.direction == "negative"
                        else MARKET_NEUTRAL,
                        None,
                    )
                    for row in rows
                ),
            ),
            unsafe_allow_html=True,
        )


def _styled_market_mover_frame(frame: pd.DataFrame) -> object:
    """Apply sign-based text colors only to market-change columns in a table."""
    change_columns = [
        name for name in ("Point change", "Absolute change", "Change", "Percentage change")
        if name in frame.columns
    ]
    if "_direction" in frame.columns:
        directions = tuple(frame["_direction"])
        visible = frame.drop(columns=["_direction"])
        styled = visible.style.apply(
            _market_mover_row_styles,
            axis=1,
            directions=directions,
            change_columns=tuple(change_columns),
        )
        return styled
    styled = frame.style
    if change_columns:
        styled = styled.map(_market_mover_change_style, subset=change_columns)
    return styled


def _market_mover_row_styles(
    row: pd.Series,
    *,
    directions: tuple[str, ...],
    change_columns: tuple[str, ...],
) -> list[str]:
    """Style only change cells from each row's validated source sign."""
    direction = directions[row.name]
    color = {
        "positive": f"color: {MARKET_POSITIVE}",
        "negative": f"color: {MARKET_NEGATIVE}",
        "neutral": f"color: {MARKET_NEUTRAL}",
    }.get(direction, "color: #888888")
    return [color if name in change_columns else "" for name in row.index]


def _market_mover_change_style(value: object) -> str:
    """Choose green, red, or neutral styling from the displayed value's sign."""
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, str):
        try:
            number = Decimal(value.strip().replace(",", "").removesuffix("%"))
        except InvalidOperation:
            return f"color: {MARKET_NEUTRAL}"
    else:
        return f"color: {MARKET_NEUTRAL}"
    if not number.is_finite() or number == 0:
        return f"color: {MARKET_NEUTRAL}"
    return f"color: {MARKET_POSITIVE}" if number > 0 else f"color: {MARKET_NEGATIVE}"


def _center_dataframe(frame: Styler) -> Styler:
    """Center table headers and cells for Streamlit's static Styler-capable table."""
    return frame.set_properties(
        subset=list(frame.columns), **{"text-align": "center"}
    ).set_table_styles([
        {"selector": "th", "props": [("text-align", "center")]},
        {"selector": "td", "props": [("text-align", "center")]},
    ], overwrite=False)


def _compact_html_table(
    headers: tuple[str, ...],
    rows: tuple[tuple[str, ...], ...],
    *,
    colors: tuple[tuple[str | None, ...], ...] | None = None,
) -> str:
    """Render compact centered table markup while escaping all displayed values."""
    head = "".join(f"<th>{escape(value)}</th>" for value in headers)
    body_rows = []
    for row_index, row in enumerate(rows):
        cells = []
        for column_index, value in enumerate(row):
            color = colors[row_index][column_index] if colors else None
            style = f' style="color:{color}"' if color else ""
            cells.append(f"<td{style}>{escape(str(value))}</td>")
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        '<div class="mp-compact-table"><style>'
        '.mp-compact-table table{width:100%;border-collapse:collapse;font-size:.82rem;}'
        '.mp-compact-table th,.mp-compact-table td{padding:3px 7px;line-height:1.15;'
        'text-align:center;vertical-align:middle;white-space:nowrap;}'
        '.mp-compact-table th{padding-top:4px;padding-bottom:4px;font-weight:600;}'
        '</style><table><thead><tr>' + head + '</tr></thead><tbody>'
        + "".join(body_rows) + "</tbody></table></div>"
    )


def _format_index_change(value: Decimal | None, *, percentage: bool = False) -> str:
    """Format a finite index change to two decimals, preserving zero and unavailable states."""
    if value is None or not value.is_finite():
        return "—"
    formatted = "0.00" if value == 0 else f"{value:+,.2f}"
    return f"{formatted}%" if percentage else formatted


def _index_direction(quote: IndexQuote | None) -> str:
    """Choose the compact card sign from an available index change field."""
    value = quote.change if quote is not None and quote.change is not None else (
        quote.percent_change if quote is not None else None
    )
    if value is None or value == 0:
        return "neutral"
    return "positive" if value > 0 else "negative"


def _market_card_color(direction: str) -> str:
    """Return the shared vivid change-text color for a market-card direction."""
    return {
        "positive": MARKET_POSITIVE,
        "negative": MARKET_NEGATIVE,
        "neutral": MARKET_NEUTRAL,
    }.get(direction, MARKET_NEUTRAL)


def _market_card_html(title: str, value: str, change: str, direction: str) -> str:
    """Render one escaped market card with shared padding, spacing, and typography."""
    from html import escape

    return (
        '<article style="background:#F5F5F0;border:1px solid #E4E1DC;border-radius:10px;'
        'padding:10px 12px;min-height:78px;box-sizing:border-box;display:flex;'
        'flex-direction:column;justify-content:center;gap:3px">'
        f'<div style="font-size:.78rem;font-weight:600;line-height:1.15;color:#292929">{escape(title)}</div>'
        f'<div style="font-size:1.22rem;font-weight:600;line-height:1.15;color:#292929">{escape(value)}</div>'
        f'<div style="font-size:.82rem;font-weight:400;line-height:1.15;color:{_market_card_color(direction)}">'
        f'Change: {escape(change)}</div></article>'
    )


def _market_card_grid_html(cards: list[str]) -> str:
    """Lay out market cards responsively with equal row and column gaps."""
    return (
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));'
        'gap:12px;width:100%">' + "".join(cards) + "</div>"
    )


def _render_year_highs(
    market_data: DashboardMarketData,
    errors: tuple[str, ...] = (),
) -> None:
    """Render complete NIFTY 100 high qualifiers or the correct empty/error state."""
    st.subheader("NIFTY 100 Stocks at 52-Week High")
    table = build_year_high_table(market_data.stocks_at_52_week_high, errors)
    if table is None:
        st.info("NIFTY 100 52-week-high scan unavailable · No complete validated result.")
        return

    state = "Cached data · Retrieved at" if table.cached else "Retrieved at"
    st.caption(f"{state}: {_format_timestamp(table.retrieved_at)}")
    if not table.rows:
        st.info("No NIFTY 100 stocks currently meet the 52-week-high criterion.")
        return
    st.markdown(
        _compact_html_table(
            ("#", "Stock symbol", "LTP (₹)", "Reported 52-week high (₹)"),
            tuple(
                (str(row.rank), row.symbol, row.last_traded_price, row.year_high)
                for row in table.rows
            ),
        ),
        unsafe_allow_html=True,
    )


def _render_commodity_card(card: CommodityMarketCard) -> str:
    """Build a commodity card with the shared index-card layout and typography."""
    return _market_card_html(card.title, card.price, card.change, card.direction)


def _format_value(value: object) -> str:
    """Format a retrieved market value or show an em dash when unavailable."""
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        return "—"
    return f"{value:,.2f}"


def _format_timestamp(value: datetime | None) -> str:
    """Format retrieval time in IST, keeping it distinct from market-data time."""
    return format_retrieved_at(value)


if __name__ == "__main__":
    main()
