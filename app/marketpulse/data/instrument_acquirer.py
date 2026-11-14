from __future__ import annotations

import ssl
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener

import certifi

DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_MAX_RESPONSE_BYTES = 25 * 1024 * 1024


class InstrumentAcquisitionError(Exception):
    """Raised when instrument-master data cannot be safely acquired."""


class InstrumentDownloadError(InstrumentAcquisitionError):
    """Raised for HTTP, transport, timeout, and redirect failures."""


class InstrumentResponseTooLargeError(InstrumentDownloadError):
    """Raised when a downloaded response exceeds the configured size limit."""


class InstrumentProvider(Protocol):
    def get_scrip_master(self, exchange_segment: str | None = None) -> Any: ...


class InstrumentStore(Protocol):
    def save_csv(self, exchange_segment: str, csv_text: str) -> Any: ...


class Downloader(Protocol):
    def __call__(self, url: str) -> bytes: ...


class _HttpsOnlyRedirectHandler(HTTPRedirectHandler):
    """Reject redirects that change the download scheme away from HTTPS."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request:
        if not _is_absolute_https_url(newurl):
            raise InstrumentDownloadError("Instrument download redirect must use HTTPS.")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            raise InstrumentDownloadError("Instrument download redirect was rejected.")
        return redirected


def _is_absolute_https_url(value: str) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(character.isspace() for character in value)
    ):
        return False
    try:
        parsed = urlsplit(value)
        parsed.port  # Access validates that any explicit port is numeric and in range.
        return (
            parsed.scheme.lower() == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return False


class HttpsInstrumentDownloader:
    """Download a bounded response over HTTPS using urllib from the standard library."""

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        opener: Any | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive.")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive.")
        self._timeout = timeout
        self._max_response_bytes = max_response_bytes
        if opener is not None:
            self._opener = opener
        else:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            self._opener = build_opener(
                _HttpsOnlyRedirectHandler(),
                HTTPSHandler(context=ssl_context),
            )

    def __call__(self, url: str) -> bytes:
        if not _is_absolute_https_url(url):
            raise InstrumentDownloadError("Instrument download URL must be an absolute HTTPS URL.")

        request = Request(url, headers={"Accept": "text/csv, application/octet-stream, */*"})
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                final_url = response.geturl()
                if not _is_absolute_https_url(final_url):
                    raise InstrumentDownloadError(
                        "Instrument download response URL must use HTTPS."
                    )

                status = response.getcode()
                if status is None or not 200 <= status < 300:
                    raise InstrumentDownloadError(
                        f"Instrument download returned unsuccessful HTTP status {status}."
                    )

                content = bytearray()
                chunk_size = min(64 * 1024, self._max_response_bytes + 1)
                while True:
                    remaining_capacity = self._max_response_bytes + 1 - len(content)
                    chunk = response.read(min(chunk_size, remaining_capacity))
                    if not chunk:
                        return bytes(content)
                    if len(content) + len(chunk) > self._max_response_bytes:
                        raise InstrumentResponseTooLargeError(
                            "Instrument download exceeded the configured response size limit."
                        )
                    content.extend(chunk)
        except HTTPError as exc:
            raise InstrumentDownloadError(
                f"Instrument download returned unsuccessful HTTP status {exc.code}."
            ) from exc
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError,)):
                raise InstrumentDownloadError("Instrument download timed out.") from exc
            raise InstrumentDownloadError("Instrument download failed.") from exc
        except TimeoutError as exc:
            raise InstrumentDownloadError("Instrument download timed out.") from exc
        except OSError as exc:
            raise InstrumentDownloadError("Instrument download failed.") from exc


class InstrumentMasterAcquirer:
    """Acquire a Kotak instrument-master CSV and hand it to a storage service."""

    def __init__(
        self,
        provider: InstrumentProvider,
        store: InstrumentStore,
        downloader: Downloader,
    ) -> None:
        self._provider = provider
        self._store = store
        self._downloader = downloader

    def acquire_and_save(self, exchange_segment: str) -> Any:
        """Acquire and save one segment; return the store's saved path/result."""
        location = self._provider.get_scrip_master(exchange_segment=exchange_segment)
        if isinstance(location, dict) and any(
            isinstance(key, str) and key.lower() in {"error", "message"} for key in location
        ):
            raise InstrumentAcquisitionError(
                "Kotak provider returned an unsuccessful scrip-master response."
            )
        if not _is_absolute_https_url(location):
            raise InstrumentAcquisitionError(
                "Kotak provider returned an unsupported location; expected an absolute HTTPS URL."
            )

        content = self._downloader(location)
        if not isinstance(content, bytes):
            raise InstrumentAcquisitionError("Instrument downloader must return raw bytes.")
        try:
            csv_text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise InstrumentAcquisitionError(
                "Downloaded instrument master is not valid UTF-8 text."
            ) from exc

        return self._store.save_csv(exchange_segment, csv_text)
