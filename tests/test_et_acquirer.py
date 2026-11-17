"""Test fixed Economic Times feed acquisition using injected fake transports."""

import socket
import ssl
import unittest
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, Request, build_opener as urllib_build_opener
from unittest.mock import patch

import certifi

from marketpulse.data.market_news import (
    MarketNewsCategory,
    NewsSourceStatus,
    NewsUnavailableReason,
)
from marketpulse.data.news_sources.et_acquirer import (
    DEFAULT_TIMEOUT_SECONDS,
    ET_COMPANY_URL,
    ET_ECONOMY_URL,
    ET_FEEDS,
    ET_STOCK_MARKET_URL,
    EtFeedConfig,
    EtRssFailureCategory,
    EtRssAcquirer,
    EtRssDownloadError,
    EtRssDownloader,
    _EtHttpsRedirectHandler,
)
from marketpulse.data.news_sources.et_rss import ET_PUBLISHER

RETRIEVED = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
ARTICLE_URL = "https://economictimes.indiatimes.com/news/synthetic/item.cms"
VALID_XML = (
    "<rss version='2.0'><channel><item><title>Synthetic feed item</title>"
    f"<link>{ARTICLE_URL}</link><guid>synthetic-guid</guid></item></channel></rss>"
).encode()
EMPTY_XML = b"<rss version='2.0'><channel></channel></rss>"


class _FakeResponse:
    """Provide a response-like object backed only by synthetic bytes."""

    def __init__(self, body, *, url, status=200, headers=None):
        """Store response fields and initialize its bounded read position."""
        self.body = body
        self.url = url
        self.status = status
        self.headers = headers or {}
        self.position = 0

    def __enter__(self):
        """Return this fake response as a context manager."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Close the fake response without external work."""
        return False

    def geturl(self):
        """Return the configured final URL for redirect validation."""
        return self.url

    def getcode(self):
        """Return the configured synthetic status code."""
        return self.status

    def read(self, size=-1):
        """Return no more than the requested bytes from this synthetic body."""
        if size < 0:
            size = len(self.body) - self.position
        chunk = self.body[self.position:self.position + size]
        self.position += len(chunk)
        return chunk


class _FakeOpener:
    """Record requests and return or raise predetermined fake outcomes."""

    def __init__(self, result):
        """Set a response factory or one safe synthetic exception."""
        self.result = result
        self.calls = []

    def open(self, request, timeout):
        """Record each request and return exactly one configured outcome."""
        self.calls.append((request, timeout))
        if isinstance(self.result, BaseException):
            raise self.result
        if callable(self.result):
            return self.result(request)
        return self.result


class _FakeDownloader:
    """Return per-feed bytes or failures without accessing a network."""

    def __init__(self, outcomes):
        """Store outcomes by fixed feed identifier and track all calls."""
        self.outcomes = outcomes
        self.calls = []

    def download(self, feed_id):
        """Return the configured outcome for one requested feed identifier."""
        self.calls.append(feed_id)
        outcome = self.outcomes[feed_id]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class EconomicTimesAcquirerTests(unittest.TestCase):
    """Verify fixed feed configuration, bounded HTTPS, and isolated outcomes."""

    def _successful_acquirer(self, outcomes=None):
        """Build an acquirer with three synthetic valid feed responses."""
        values = outcomes or {feed_id: VALID_XML for feed_id in ET_FEEDS}
        downloader = _FakeDownloader(values)
        return EtRssAcquirer(downloader), downloader

    def test_three_fixed_feeds_map_sections_categories_and_source_ids(self):
        """Bind official feed URLs to stable IDs, report sections, and generic categories."""
        self.assertEqual(
            [(feed.url, feed.report_section, feed.category) for feed in ET_FEEDS.values()],
            [
                (ET_STOCK_MARKET_URL, "stock_market_news", MarketNewsCategory.INDIAN_MARKETS),
                (ET_ECONOMY_URL, "economic_news", MarketNewsCategory.ECONOMY_POLICY),
                (ET_COMPANY_URL, "company_news", MarketNewsCategory.LISTED_COMPANIES),
            ],
        )
        self.assertEqual(
            [feed.source_id for feed in ET_FEEDS.values()],
            ["economic_times_stock_market", "economic_times_economy", "economic_times_company"],
        )
        self.assertTrue(all(isinstance(feed, EtFeedConfig) for feed in ET_FEEDS.values()))

    def test_collection_parses_each_feed_once_and_preserves_categories_and_time(self):
        """Collect each fixed feed once with its selected category and retrieval timestamp."""
        acquirer, downloader = self._successful_acquirer()

        results = acquirer.collect(RETRIEVED)

        self.assertEqual(downloader.calls, list(ET_FEEDS))
        self.assertEqual(len(results), 3)
        for result, config in zip(results, ET_FEEDS.values()):
            self.assertEqual(result.source_id, config.source_id)
            self.assertEqual(result.retrieved_at, RETRIEVED)
            self.assertEqual(result.status, NewsSourceStatus.AVAILABLE)
            self.assertEqual(result.items[0].source_id, config.source_id)
            self.assertEqual(result.items[0].category, config.category)
            self.assertEqual(result.items[0].publisher, ET_PUBLISHER)
            self.assertEqual(result.items[0].retrieved_at, RETRIEVED)
            self.assertEqual(result.items[0].article_url, ARTICLE_URL)

    def test_valid_empty_feed_is_successful(self):
        """Keep valid zero-item feeds distinct from acquisition and parser failures."""
        outcomes = {feed_id: EMPTY_XML for feed_id in ET_FEEDS}
        acquirer, _ = self._successful_acquirer(outcomes)

        results = acquirer.collect(RETRIEVED)

        self.assertTrue(all(result.status is NewsSourceStatus.AVAILABLE for result in results))
        self.assertTrue(all(result.items == () and result.unavailable_reason is None
                            for result in results))

    def test_one_feed_failure_does_not_suppress_other_successes(self):
        """Return one unavailable result and preserve the other two feed records."""
        outcomes = {
            "stock_market": EtRssDownloadError(NewsUnavailableReason.TIMEOUT),
            "economy": VALID_XML,
            "company": VALID_XML,
        }
        acquirer, downloader = self._successful_acquirer(outcomes)

        results = acquirer.collect(RETRIEVED)

        self.assertEqual(len(results), 3)
        self.assertEqual(results[0].status, NewsSourceStatus.UNAVAILABLE)
        self.assertEqual(results[0].unavailable_reason, NewsUnavailableReason.TIMEOUT)
        self.assertEqual(results[1].status, NewsSourceStatus.AVAILABLE)
        self.assertEqual(results[2].status, NewsSourceStatus.AVAILABLE)
        self.assertEqual([len(result.items) for result in results], [0, 1, 1])
        self.assertEqual(downloader.calls, list(ET_FEEDS))

    def test_multiple_failures_remain_independent_and_sanitized(self):
        """Continue through all configured feeds and expose only fixed reasons."""
        outcomes = {
            "stock_market": TimeoutError("secret endpoint detail"),
            "economy": EtRssDownloadError(NewsUnavailableReason.SOURCE_ERROR),
            "company": VALID_XML,
        }
        acquirer, downloader = self._successful_acquirer(outcomes)

        results = acquirer.collect(RETRIEVED)

        self.assertEqual([result.status for result in results], [
            NewsSourceStatus.UNAVAILABLE,
            NewsSourceStatus.UNAVAILABLE,
            NewsSourceStatus.AVAILABLE,
        ])
        self.assertEqual(results[0].unavailable_reason, NewsUnavailableReason.SOURCE_ERROR)
        self.assertNotIn("secret", repr(results))
        self.assertEqual(downloader.calls, list(ET_FEEDS))

    def test_invalid_xml_is_distinct_from_transport_failure(self):
        """Keep parser INVALID_RESPONSE separate from acquisition SOURCE_ERROR."""
        outcomes = {feed_id: VALID_XML for feed_id in ET_FEEDS}
        outcomes["economy"] = b"<rss><channel>broken"
        acquirer, _ = self._successful_acquirer(outcomes)

        results = acquirer.collect(RETRIEVED)

        self.assertEqual(results[1].status, NewsSourceStatus.UNAVAILABLE)
        self.assertEqual(results[1].unavailable_reason, NewsUnavailableReason.INVALID_RESPONSE)
        self.assertEqual(results[0].status, NewsSourceStatus.AVAILABLE)
        self.assertEqual(results[2].status, NewsSourceStatus.AVAILABLE)

    def test_downloader_requests_only_fixed_feed_urls_and_never_article_links(self):
        """Make three bounded feed requests and never follow an item URL."""
        opener = _FakeOpener(
            lambda request: _FakeResponse(VALID_XML, url=request.full_url)
        )
        acquirer = EtRssAcquirer(EtRssDownloader(opener=opener))

        results = acquirer.collect(RETRIEVED)

        self.assertEqual([call[0].full_url for call in opener.calls],
                         [feed.url for feed in ET_FEEDS.values()])
        self.assertNotIn(ARTICLE_URL, [call[0].full_url for call in opener.calls])
        self.assertTrue(all(timeout == DEFAULT_TIMEOUT_SECONDS for _, timeout in opener.calls))
        self.assertEqual(len(results), 3)
        self.assertEqual(acquirer._downloader.last_http_status, 200)
        self.assertIsNone(acquirer._downloader.last_redirect_encountered)

    def test_redirect_handler_rejects_http_external_credentials_ports_and_article_paths(self):
        """Allow redirects only among the three exact configured HTTPS feed URLs."""
        handler = _EtHttpsRedirectHandler()
        request = Request(ET_STOCK_MARKET_URL)
        rejected = (
            "http://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
            "https://evil.example/markets/stocks/rssfeeds/2146842.cms",
            "https://user:secret@economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
            "https://economictimes.indiatimes.com:8443/markets/stocks/rssfeeds/2146842.cms",
            "https://economictimes.indiatimes.com/news/synthetic/article.cms",
            "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms?token=secret",
        )

        for url in rejected:
            with self.subTest(url=url), self.assertRaises(EtRssDownloadError) as caught:
                handler.redirect_request(request, None, 302, "Found", {}, url)
            self.assertIs(caught.exception.failure_category, EtRssFailureCategory.REDIRECT_REJECTED)
            self.assertNotIn("secret", str(caught.exception))
            self.assertNotIn("token", caught.exception.redirect_destination or "")

    def test_injected_opener_final_url_is_validated(self):
        """Reject an injected transport that reports an unapproved final URL."""
        opener = _FakeOpener(_FakeResponse(VALID_XML, url="http://example.com/feed"))
        downloader = EtRssDownloader(opener=opener)

        with self.assertRaises(EtRssDownloadError):
            downloader.download("stock_market")

        self.assertEqual(len(opener.calls), 1)

    def test_final_url_cannot_switch_to_another_configured_category(self):
        """Reject redirects that land on a different valid feed category."""
        opener = _FakeOpener(_FakeResponse(VALID_XML, url=ET_ECONOMY_URL))
        downloader = EtRssDownloader(opener=opener)

        with self.assertRaises(EtRssDownloadError):
            downloader.download("stock_market")

        self.assertEqual(len(opener.calls), 1)

    def test_tls_verification_is_enabled_on_default_opener(self):
        """Pass certifi's verified context into the actual default urllib HTTPS opener."""
        ca_bundle = certifi.where()
        real_create_default_context = ssl.create_default_context
        contexts = []
        openers = []

        def create_verified_context(*args, **kwargs):
            """Create the real SSL context while retaining it for opener inspection."""
            context = real_create_default_context(*args, **kwargs)
            contexts.append(context)
            return context

        def build_real_opener(*handlers):
            """Build the real urllib opener while retaining it for inspection."""
            opener = urllib_build_opener(*handlers)
            openers.append(opener)
            return opener

        with (
            patch(
                "marketpulse.data.news_sources.et_acquirer.certifi.where",
                wraps=certifi.where,
            ) as where,
            patch(
                "marketpulse.data.news_sources.et_acquirer.ssl.create_default_context",
                side_effect=create_verified_context,
            ) as create_context,
            patch(
                "marketpulse.data.news_sources.et_acquirer.build_opener",
                side_effect=build_real_opener,
            ) as build,
        ):
            downloader = EtRssDownloader()

        where.assert_called_once_with()
        create_context.assert_called_once_with(cafile=ca_bundle)
        handlers = build.call_args.args
        https_handler = next(handler for handler in handlers if isinstance(handler, HTTPSHandler))
        self.assertTrue(https_handler._context.check_hostname)
        self.assertEqual(https_handler._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertIs(https_handler._context, contexts[0])
        self.assertIn(https_handler, openers[0].handlers)
        self.assertIs(downloader._opener, openers[0])

    def test_oversized_response_is_rejected(self):
        """Enforce the parser byte cap while streaming response data."""
        opener = _FakeOpener(lambda request: _FakeResponse(
            b"x" * 65, url=request.full_url
        ))
        downloader = EtRssDownloader(opener=opener, max_response_bytes=64)

        with self.assertRaises(EtRssDownloadError) as caught:
            downloader.download("stock_market")
        self.assertIs(caught.exception.failure_category, EtRssFailureCategory.RESPONSE_TOO_LARGE)

        self.assertEqual(len(opener.calls), 1)

    def test_http_status_codes_are_classified_without_exposing_messages(self):
        """Retain numeric HTTP status while suppressing server text and headers."""
        for status in (403, 404, 429, 500):
            with self.subTest(status=status):
                opener = _FakeOpener(_FakeResponse(
                    b"secret body", url=ET_STOCK_MARKET_URL, status=status
                ))
                downloader = EtRssDownloader(opener=opener)
                with self.assertRaises(EtRssDownloadError) as caught:
                    downloader.download("stock_market")
                self.assertIs(caught.exception.failure_category, EtRssFailureCategory.HTTP_STATUS_ERROR)
                self.assertEqual(caught.exception.http_status, status)
                self.assertNotIn("secret", str(caught.exception))
                self.assertEqual(len(opener.calls), 1)

                http_error = HTTPError(
                    ET_STOCK_MARKET_URL, status, "private HTTP detail",
                    {"Set-Cookie": "private-cookie"}, None,
                )
                http_opener = _FakeOpener(http_error)
                http_downloader = EtRssDownloader(opener=http_opener)
                with self.assertRaises(EtRssDownloadError) as http_caught:
                    http_downloader.download("stock_market")
                self.assertIs(http_caught.exception.failure_category,
                              EtRssFailureCategory.HTTP_STATUS_ERROR)
                self.assertEqual(http_caught.exception.http_status, status)
                self.assertNotIn("private", str(http_caught.exception))
                self.assertEqual(len(http_opener.calls), 1)
                self.assertEqual(http_downloader.last_http_status, status)

    def test_timeout_dns_tls_and_unknown_errors_have_fixed_classifications(self):
        """Classify transport failures from exception types without exposing details."""
        failures = (
            (TimeoutError("private timeout"), EtRssFailureCategory.TIMEOUT),
            (URLError(socket.gaierror("private DNS")), EtRssFailureCategory.CONNECTION_ERROR),
            (ConnectionError("private connection"), EtRssFailureCategory.CONNECTION_ERROR),
            (ssl.SSLError("private certificate"), EtRssFailureCategory.TLS_ERROR),
            (RuntimeError("private unknown detail"), EtRssFailureCategory.UNKNOWN_ERROR),
        )
        for failure, expected in failures:
            with self.subTest(expected=expected):
                opener = _FakeOpener(failure)
                downloader = EtRssDownloader(opener=opener)
                with self.assertRaises(EtRssDownloadError) as caught:
                    downloader.download("stock_market")
                self.assertIs(caught.exception.failure_category, expected)
                self.assertNotIn("private", str(caught.exception))
                self.assertEqual(len(opener.calls), 1)

    def test_malformed_content_length_is_invalid_response(self):
        """Classify a malformed length header without reading or exposing the body."""
        response = _FakeResponse(
            b"secret body", url=ET_STOCK_MARKET_URL,
            headers={"Content-Length": "not-a-number"},
        )
        opener = _FakeOpener(response)
        downloader = EtRssDownloader(opener=opener)
        with self.assertRaises(EtRssDownloadError) as caught:
            downloader.download("stock_market")
        self.assertIs(caught.exception.failure_category, EtRssFailureCategory.INVALID_RESPONSE)
        self.assertEqual(response.position, 0)

    def test_redirect_limit_is_distinct_from_http_status_failure(self):
        """Classify urllib's redirect cap after a redirect was actually encountered."""
        downloader = None

        class RedirectLoopOpener:
            """Simulate one redirect loop termination without a network request."""

            def open(self, request, timeout):
                """Record one fake request and raise a redirect status error."""
                del request, timeout
                downloader._last_redirect_encountered = True
                raise HTTPError(ET_STOCK_MARKET_URL, 302, "private redirect detail", {}, None)

        opener = RedirectLoopOpener()
        downloader = EtRssDownloader(opener=opener)
        with self.assertRaises(EtRssDownloadError) as caught:
            downloader.download("stock_market")
        self.assertIs(caught.exception.failure_category, EtRssFailureCategory.REDIRECT_LIMIT)
        self.assertEqual(caught.exception.http_status, 302)

    def test_declared_malformed_or_oversized_content_length_is_rejected(self):
        """Reject unsafe declared lengths before reading any response body."""
        for content_length, expected in (
            ("bad", EtRssFailureCategory.INVALID_RESPONSE),
            ("65", EtRssFailureCategory.RESPONSE_TOO_LARGE),
        ):
            with self.subTest(content_length=content_length):
                response = _FakeResponse(
                    VALID_XML,
                    url=ET_STOCK_MARKET_URL,
                    headers={"Content-Length": content_length},
                )
                opener = _FakeOpener(response)
                downloader = EtRssDownloader(opener=opener, max_response_bytes=64)
                with self.assertRaises(EtRssDownloadError) as caught:
                    downloader.download("stock_market")
                self.assertIs(caught.exception.failure_category, expected)
                self.assertEqual(response.position, 0)

    def test_timeout_tls_and_http_failures_are_sanitized_without_retries(self):
        """Map transport exceptions safely and make exactly one request per failed feed."""
        failures = (
            (TimeoutError("secret timeout"), NewsUnavailableReason.TIMEOUT),
            (URLError(TimeoutError("secret timeout")), NewsUnavailableReason.TIMEOUT),
            (socket.gaierror("secret dns"), NewsUnavailableReason.SOURCE_ERROR),
            (ssl.SSLError("secret certificate"), NewsUnavailableReason.SOURCE_ERROR),
            (HTTPError(ET_STOCK_MARKET_URL, 503, "secret HTTP text", {}, None),
             NewsUnavailableReason.SOURCE_ERROR),
        )
        for failure, expected_reason in failures:
            with self.subTest(failure_type=type(failure).__name__):
                opener = _FakeOpener(failure)
                downloader = EtRssDownloader(opener=opener)
                with self.assertRaises(EtRssDownloadError) as caught:
                    downloader.download("stock_market")
                self.assertEqual(caught.exception.reason, expected_reason)
                self.assertNotIn("secret", str(caught.exception))
                self.assertEqual(len(opener.calls), 1)

    def test_nonconfigured_feed_id_is_rejected_without_transport_use(self):
        """Prevent callers from supplying arbitrary URLs or unconfigured feed identities."""
        opener = _FakeOpener(_FakeResponse(EMPTY_XML, url=ET_STOCK_MARKET_URL))
        downloader = EtRssDownloader(opener=opener)

        with self.assertRaisesRegex(ValueError, "configured"):
            downloader.download("https://attacker.example/feed.xml")

        self.assertEqual(opener.calls, [])

    def test_naive_time_and_construction_do_not_trigger_requests(self):
        """Validate retrieval provenance and construct the acquisition path offline."""
        downloader = _FakeDownloader({feed_id: VALID_XML for feed_id in ET_FEEDS})
        acquirer = EtRssAcquirer(downloader)
        self.assertEqual(downloader.calls, [])

        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            acquirer.collect(datetime(2026, 11, 17, 12))

        self.assertEqual(downloader.calls, [])

    def test_models_need_no_dashboard_or_pdf_imports(self):
        """Collect with fakes while blocking imports of UI and PDF modules."""
        builtins_import = __import__

        def guarded_import(name, *args, **kwargs):
            """Reject UI or PDF dependencies during acquisition calls."""
            if name == "streamlit" or name.startswith("reportlab") or name.startswith(
                "marketpulse.reports.pdf_renderer"
            ):
                raise AssertionError("unexpected presentation dependency")
            return builtins_import(name, *args, **kwargs)

        acquirer, _ = self._successful_acquirer()
        with patch("builtins.__import__", guarded_import):
            results = acquirer.collect(RETRIEVED)

        self.assertEqual(len(results), 3)


if __name__ == "__main__":
    unittest.main()
