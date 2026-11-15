from datetime import datetime

import streamlit as st
from marketpulse.dashboard.market_data import (
    CommodityMarketCard,
    DashboardMarketData,
    INDEX_WATCHLIST,
    MarketMoverTableRow,
    SectorHeatmapTile,
    YearHighTableRow,
    build_year_high_table,
    build_market_movers_tables,
    build_sector_heatmap_tiles,
    build_commodity_cards,
    create_dashboard_services,
    format_retrieved_at,
    refresh_dashboard_data,
    safe_error_message,
)

_DATA_STATE_KEY = "market_data_snapshot"
_ERROR_STATE_KEY = "market_data_errors"


def main() -> None:
    """Render the read-only dashboard and refresh datasets only on button click."""
    st.set_page_config(page_title="MarketPulse India", layout="wide")
    st.title("MarketPulse India")
    st.caption("Read-only market data from Kotak Neo REST; market-data freshness is not independently verified.")

    if _DATA_STATE_KEY not in st.session_state:
        st.session_state[_DATA_STATE_KEY] = DashboardMarketData()
        st.session_state[_ERROR_STATE_KEY] = ()

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

    commodity_prefixes = ("Gold futures", "Silver futures", "USD/INR futures", "Crude Oil futures")
    for message in st.session_state[_ERROR_STATE_KEY]:
        if not message.startswith(commodity_prefixes):
            st.warning(message)
    _render_market_data(
        st.session_state[_DATA_STATE_KEY],
        st.session_state[_ERROR_STATE_KEY],
    )


def _render_market_data(
    market_data: DashboardMarketData,
    errors: tuple[str, ...] = (),
) -> None:
    """Render indices, commodities, sectors, and NIFTY 100 stock panels with cache state."""
    cards = build_commodity_cards(market_data, errors)
    if not market_data.index_quotes and not any(
        card.retrieved_at is not None for card in cards
    ) and not market_data.sector_performance and market_data.market_movers is None and market_data.stocks_at_52_week_high is None:
        st.info("No market data has been retrieved yet. Select Refresh Market Data to begin.")

    st.subheader("Market Indices")
    if market_data.index_retrieved_at is not None:
        index_cached = any(message.startswith("Index quotes") for message in errors)
        label = "Cached data · Retrieved at" if index_cached else "Retrieved at"
        st.caption(f"{label}: {_format_timestamp(market_data.index_retrieved_at)}")
    else:
        st.caption("Unavailable · No successful index retrieval yet.")
    index_quotes = {quote.index_name: quote for quote in market_data.index_quotes}
    st.dataframe(
        [
            {
                "Index": index_name,
                "Exchange": (
                    index_quotes[index_name].exchange
                    if index_name in index_quotes
                    else exchange
                ),
                "Index Value": _format_value(
                    index_quotes[index_name].index_value if index_name in index_quotes else None
                ),
            }
            for index_name, exchange in INDEX_WATCHLIST
        ],
        hide_index=True,
        use_container_width=True,
    )

    st.subheader("Commodities & Currency")
    st.caption("Retrieval timestamps are not exchange trading timestamps. Market-data freshness is not independently verified.")
    columns = st.columns(4)
    for column, card in zip(columns, cards, strict=True):
        with column.container(border=True):
            _render_commodity_card(card)

    _render_sector_heatmap(market_data, errors)
    _render_market_movers(market_data, errors)
    _render_year_highs(market_data, errors)


def _render_sector_heatmap(
    market_data: DashboardMarketData,
    errors: tuple[str, ...] = (),
) -> None:
    """Render the complete ordered sector heatmap or a clear unavailable state."""
    st.subheader("NSE Sector Heatmap")
    st.caption("Market-data freshness is not independently verified.")
    tiles = build_sector_heatmap_tiles(market_data.sector_performance, errors)
    if not tiles:
        st.info("NSE sector heatmap unavailable · No complete validated sector snapshot.")
        return

    state = "Cached data · Retrieved at" if tiles[0].cached else "Retrieved at"
    st.caption(f"{state}: {_format_timestamp(tiles[0].retrieved_at)}")
    for offset in range(0, len(tiles), 4):
        columns = st.columns(4)
        for column, tile in zip(columns, tiles[offset:offset + 4], strict=True):
            column.markdown(_sector_tile_html(tile), unsafe_allow_html=True)


def _sector_tile_html(tile: SectorHeatmapTile) -> str:
    """Build an escaped, direction-colored tile with readable signed change text."""
    from html import escape

    colors = {
        "positive": "#166534",
        "negative": "#991b1b",
        "neutral": "#374151",
    }
    background = colors[tile.direction]
    name = escape(tile.sector_name)
    change = escape(tile.percent_change)
    value = escape(tile.index_value)
    return (
        '<div style="background:' + background + ';color:#fff;padding:14px;'
        'border-radius:8px;min-height:104px;margin-bottom:12px">'
        f'<div style="font-weight:700">{name}</div>'
        f'<div style="font-size:1.2rem;margin-top:8px">{change}</div>'
        f'<div style="font-size:.9rem;opacity:.9">Index value: {value}</div></div>'
    )


def _render_market_movers(
    market_data: DashboardMarketData,
    errors: tuple[str, ...] = (),
) -> None:
    """Render validated NIFTY 100 gainers and losers in responsive side-by-side tables."""
    st.subheader("NIFTY 100 – Top Gainers & Losers")
    st.caption("Market-data freshness is not independently verified.")
    tables = build_market_movers_tables(market_data.market_movers, errors)
    if tables is None:
        st.info("NIFTY 100 gainers and losers unavailable · No complete validated ranking.")
        return

    state = "Cached data · Retrieved at" if tables.cached else "Retrieved at"
    st.caption(f"{state}: {_format_timestamp(tables.retrieved_at)}")
    columns = st.columns(2)
    columns[0].markdown("### Top 10 Gainers")
    columns[0].markdown(_market_movers_table_html(tables.gainers), unsafe_allow_html=True)
    columns[1].markdown("### Top 10 Losers")
    columns[1].markdown(_market_movers_table_html(tables.losers), unsafe_allow_html=True)


def _market_movers_table_html(rows: tuple[MarketMoverTableRow, ...]) -> str:
    """Build a compact escaped table with signed, direction-colored changes."""
    from html import escape

    rendered_rows = []
    for row in rows:
        color = "#15803d" if row.direction == "positive" else "#b91c1c"
        if row.direction == "neutral":
            color = "#374151"
        rendered_rows.append(
            "<tr>"
            f'<td style="padding:6px;border-bottom:1px solid #ddd">{row.rank}</td>'
            f'<td style="padding:6px;border-bottom:1px solid #ddd">{escape(row.symbol)}</td>'
            f'<td style="padding:6px;border-bottom:1px solid #ddd;color:{color};font-weight:700">'
            f"{escape(row.percent_change)}</td>"
            f'<td style="padding:6px;border-bottom:1px solid #ddd">'
            f"{escape(row.last_traded_price)}</td></tr>"
        )
    return (
        '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse">'
        "<thead><tr><th>Rank</th><th>Stock</th><th>Percentage change</th><th>LTP</th></tr></thead>"
        f"<tbody>{''.join(rendered_rows)}</tbody></table></div>"
    )


def _render_year_highs(
    market_data: DashboardMarketData,
    errors: tuple[str, ...] = (),
) -> None:
    """Render complete NIFTY 100 high qualifiers or the correct empty/error state."""
    st.subheader("NIFTY 100 – Stocks at 52-Week Highs")
    st.caption("Market-data freshness is not independently verified.")
    table = build_year_high_table(market_data.stocks_at_52_week_high, errors)
    if table is None:
        st.info("NIFTY 100 52-week-high scan unavailable · No complete validated result.")
        return

    state = "Cached data · Retrieved at" if table.cached else "Retrieved at"
    st.caption(f"{state}: {_format_timestamp(table.retrieved_at)}")
    if not table.rows:
        st.info("No NIFTY 100 stocks currently meet the 52-week-high criterion.")
        return
    st.markdown(_year_high_table_html(table.rows), unsafe_allow_html=True)


def _year_high_table_html(rows: tuple[YearHighTableRow, ...]) -> str:
    """Build an escaped compact table with prices explicitly labeled in rupees."""
    from html import escape

    body = "".join(
        "<tr>"
        f'<td style="padding:6px;border-bottom:1px solid #ddd">{row.rank}</td>'
        f'<td style="padding:6px;border-bottom:1px solid #ddd">{escape(row.symbol)}</td>'
        f'<td style="padding:6px;border-bottom:1px solid #ddd">{escape(row.last_traded_price)}</td>'
        f'<td style="padding:6px;border-bottom:1px solid #ddd">{escape(row.year_high)}</td>'
        "</tr>"
        for row in rows
    )
    return (
        '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse">'
        "<thead><tr><th>Rank</th><th>Stock symbol</th><th>LTP (₹)</th>"
        "<th>Reported 52-week high (₹)</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def _render_commodity_card(card: CommodityMarketCard) -> None:
    """Render one formatted futures card, including unavailable or cached states."""
    st.markdown(f"**{card.title}**")
    st.caption(card.subtitle)
    st.metric(card.price_label, card.price)
    st.caption(f"Change: {card.change}")
    st.caption(f"Contract: {card.contract_symbol}")
    if card.section == "usdinr":
        st.caption("Futures contract; not a spot rate or RBI reference rate.")
    if card.section == "crude_oil":
        st.caption(
            "Indicative USD equivalent (USD/barrel): "
            f"{card.usd_equivalent or 'Unavailable'}"
        )
        if card.usd_rate_contract_symbol:
            st.caption(
                "Conversion basis: USD/INR currency futures · "
                f"{card.usd_rate_contract_symbol}"
            )
            if card.usd_rate_retrieved_at is not None:
                state = "Cached USD/INR rate · Retrieved at" if card.usd_rate_cached else "USD/INR rate · Retrieved at"
                st.caption(f"{state}: {_format_timestamp(card.usd_rate_retrieved_at)}")
        st.caption(card.usd_note or "Indicative conversion; market-data freshness not independently verified.")
    if card.retrieved_at is None:
        st.caption("Unavailable · No successful retrieval yet.")
    else:
        state = "Cached data · Retrieved at" if card.cached else "Retrieved at"
        st.caption(f"{state}: {_format_timestamp(card.retrieved_at)}")
    if card.error:
        st.warning(card.error)


def _format_value(value: object) -> str:
    """Format a retrieved market value or show N/A when it is unavailable."""
    return "N/A" if value is None else str(value)


def _format_timestamp(value: datetime | None) -> str:
    """Format retrieval time in IST, keeping it distinct from market-data time."""
    return format_retrieved_at(value)


if __name__ == "__main__":
    main()
