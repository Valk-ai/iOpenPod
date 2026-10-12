"""HTTP client for communicating with the Jellyfin API."""

from __future__ import annotations

import json
import logging
import ssl
from typing import TYPE_CHECKING, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

import certifi

from iOpenPod.app.core.version import get_version

from .models import (
    JellyfinAuthError,
    JellyfinAuthResult,
    JellyfinConnectionError,
    JellyfinError,
    JellyfinItem,
    JellyfinNotFoundError,
    JellyfinResponse,
    JellyfinView,
    QuickConnectInitiateResult,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

CLIENT_NAME = "iOpenPod"
DEVICE_NAME = "iOpenPod Desktop"
DEFAULT_DEVICE_ID = "iOpenPod-Desktop-2.0"
MAX_RESPONSE_BYTES = 32 * 1024 * 1024  # 32 MB response safety cap


class Transport(Protocol):
    """Protocol for transport layer to allow straightforward unit testing."""

    def request(
        self,
        url: str,
        method: str = "GET",
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> JellyfinResponse: ...


class _NoRedirect(HTTPRedirectHandler):
    """Refuse HTTP redirects so credentials are never sent to third-party endpoints."""

    def redirect_request(
        self,
        req: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


class DefaultTransport:
    """Production transport wrapping urllib with safe TLS configuration."""

    def __init__(self) -> None:
        context = ssl.create_default_context(cafile=certifi.where())
        self._opener = build_opener(HTTPSHandler(context=context), _NoRedirect())

    def request(
        self,
        url: str,
        method: str = "GET",
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> JellyfinResponse:
        req = Request(url, data=data, method=method)
        if headers:
            for key, val in headers.items():
                req.add_header(key, val)

        try:
            with self._opener.open(req, timeout=15) as resp:
                status = resp.status
                resp_headers = dict(resp.headers.items())
                raw_bytes = resp.read(MAX_RESPONSE_BYTES)
                parsed_data: dict[str, object] = {}
                if raw_bytes:
                    try:
                        parsed = json.loads(raw_bytes.decode("utf-8"))
                        if isinstance(parsed, dict):
                            parsed_data = parsed
                        elif isinstance(parsed, list):
                            parsed_data = {"Items": parsed}
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        parsed_data = {}
                return JellyfinResponse(
                    status=status,
                    data=parsed_data,
                    headers=resp_headers,
                )
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            logger.warning(
                "Jellyfin HTTP request %s %s failed with %s: %s",
                method,
                url,
                exc.code,
                body,
            )
            if exc.code in (401, 403):
                raise JellyfinAuthError(
                    f"Authentication failed ({exc.code}): {exc.reason}"
                ) from exc
            if exc.code == 404:
                raise JellyfinNotFoundError(
                    f"Resource not found ({exc.code}): {url}"
                ) from exc
            raise JellyfinError(
                f"Jellyfin server returned HTTP {exc.code}: {body or exc.reason}"
            ) from exc
        except URLError as exc:
            logger.warning(
                "Jellyfin connection error for %s %s: %s", method, url, exc.reason
            )
            raise JellyfinConnectionError(
                f"Failed to connect to Jellyfin server: {exc.reason}"
            ) from exc
        except Exception as exc:
            logger.warning("Unexpected error during Jellyfin request: %s", exc)
            raise JellyfinError(f"Request failed: {exc}") from exc


class JellyfinClient:
    """Client for interacting with Jellyfin media server."""

    def __init__(
        self,
        server_url: str,
        token: str = "",
        *,
        device_id: str = DEFAULT_DEVICE_ID,
        transport: Transport | None = None,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.token = token
        self.device_id = device_id
        self.transport = transport if transport is not None else DefaultTransport()
        self.version = get_version()

    def _headers(self, content_type: str | None = None) -> dict[str, str]:
        auth_parts = [
            f'Client="{CLIENT_NAME}"',
            f'Device="{DEVICE_NAME}"',
            f'DeviceId="{self.device_id}"',
            f'Version="{self.version}"',
        ]
        if self.token:
            auth_parts.append(f'Token="{self.token}"')

        auth_header = f"MediaBrowser {', '.join(auth_parts)}"
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Authorization": auth_header,
            "X-Emby-Authorization": auth_header,
        }
        if self.token:
            headers["X-MediaBrowser-Token"] = self.token
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    def ping(self) -> dict[str, object]:
        """Verify server connectivity and return public server info."""
        url = f"{self.server_url}/System/Info/Public"
        resp = self.transport.request(url, method="GET", headers=self._headers())
        return resp.data

    def initiate_quick_connect(self) -> QuickConnectInitiateResult:
        """Start a Quick Connect flow on the server."""
        url = f"{self.server_url}/QuickConnect/Initiate"
        headers = self._headers(content_type="application/json")
        resp = self.transport.request(
            url,
            method="POST",
            data=b"{}",
            headers=headers,
        )
        code = str(resp.data.get("Code", ""))
        secret = str(resp.data.get("Secret", ""))
        if not code or not secret:
            raise JellyfinError("Server returned invalid Quick Connect response")
        return QuickConnectInitiateResult(code=code, secret=secret)

    def check_quick_connect(self, secret: str) -> bool:
        """Poll the server to check whether the Quick Connect code has been approved."""
        url = f"{self.server_url}/QuickConnect/Connect?Secret={quote(secret)}"
        resp = self.transport.request(url, method="GET", headers=self._headers())
        return bool(resp.data.get("Authenticated", False))

    def authenticate_quick_connect(self, secret: str) -> JellyfinAuthResult:
        """Exchange an approved Quick Connect secret for an access token."""
        url = f"{self.server_url}/Users/AuthenticateWithQuickConnect"
        body = json.dumps({"Secret": secret}).encode("utf-8")
        headers = self._headers(content_type="application/json")
        resp = self.transport.request(url, method="POST", data=body, headers=headers)
        return self._parse_auth_result(resp.data)

    def authenticate_by_name(self, username: str, password: str) -> JellyfinAuthResult:
        """Authenticate using username and password."""
        url = f"{self.server_url}/Users/AuthenticateByName"
        body = json.dumps({"Username": username, "Pw": password}).encode("utf-8")
        headers = self._headers(content_type="application/json")
        resp = self.transport.request(url, method="POST", data=body, headers=headers)
        return self._parse_auth_result(resp.data)

    def _parse_auth_result(self, data: dict[str, object]) -> JellyfinAuthResult:
        access_token = str(data.get("AccessToken", ""))
        server_id = str(data.get("ServerId", ""))
        user = data.get("User")
        user_id = ""
        user_name = ""
        if isinstance(user, dict):
            user_id = str(user.get("Id", ""))
            user_name = str(user.get("Name", ""))

        if not access_token or not user_id:
            raise JellyfinAuthError("Server returned incomplete authentication payload")

        return JellyfinAuthResult(
            access_token=access_token,
            user_id=user_id,
            user_name=user_name,
            server_id=server_id,
        )

    def get_user_views(self, user_id: str) -> tuple[JellyfinView, ...]:
        """Fetch the top-level libraries (views) available to the user."""
        url = f"{self.server_url}/Users/{quote(user_id)}/Views"
        resp = self.transport.request(url, method="GET", headers=self._headers())
        items = resp.data.get("Items")
        if not isinstance(items, list):
            return ()

        return tuple(
            JellyfinView(
                view_id=str(item["Id"]),
                name=str(item["Name"]),
                collection_type=str(item.get("CollectionType", "")),
            )
            for item in items
            if isinstance(item, dict) and "Id" in item and "Name" in item
        )

    def get_music_tracks(
        self,
        user_id: str,
        parent_id: str | None = None,
    ) -> tuple[JellyfinItem, ...]:
        """Query audio items from the user's music library."""
        params: dict[str, str] = {
            "Recursive": "true",
            "IncludeItemTypes": "Audio",
            "Fields": "MediaSources,Genres,Path,Overview",
        }
        if parent_id:
            params["ParentId"] = parent_id

        url = f"{self.server_url}/Users/{quote(user_id)}/Items?{urlencode(params)}"
        resp = self.transport.request(url, method="GET", headers=self._headers())
        items = resp.data.get("Items")
        if not isinstance(items, list):
            return ()

        return tuple(self._parse_item(item) for item in items if isinstance(item, dict))

    def get_video_items(
        self,
        user_id: str,
        parent_id: str | None = None,
    ) -> tuple[JellyfinItem, ...]:
        """Query movie and video items from the user's library."""
        params: dict[str, str] = {
            "Recursive": "true",
            "IncludeItemTypes": "Movie,Video",
            "Fields": "MediaSources,Genres,Path,Overview",
        }
        if parent_id:
            params["ParentId"] = parent_id

        url = f"{self.server_url}/Users/{quote(user_id)}/Items?{urlencode(params)}"
        resp = self.transport.request(url, method="GET", headers=self._headers())
        items = resp.data.get("Items")
        if not isinstance(items, list):
            return ()

        return tuple(self._parse_item(item) for item in items if isinstance(item, dict))

    def get_item(self, user_id: str, item_id: str) -> JellyfinItem:
        """Fetch details for a specific item."""
        url = (
            f"{self.server_url}/Users/{quote(user_id)}/Items/{quote(item_id)}"
            "?Fields=MediaSources,Genres,Path,Overview"
        )
        resp = self.transport.request(url, method="GET", headers=self._headers())
        return self._parse_item(resp.data)

    def _parse_item(self, item: dict[str, object]) -> JellyfinItem:
        item_id = str(item.get("Id", ""))
        name = str(item.get("Name", ""))
        album = str(item.get("Album", ""))
        album_artist = str(item.get("AlbumArtist", ""))

        artists = item.get("Artists")
        artist = ""
        if isinstance(artists, list) and artists:
            artist = str(artists[0])
        elif "Artist" in item:
            artist = str(item["Artist"])

        ticks = item.get("RunTimeTicks")
        duration_ms = 0
        if isinstance(ticks, (int, float)):
            duration_ms = int(ticks // 10_000)

        idx = item.get("IndexNumber")
        track_number = idx if isinstance(idx, int) else 0

        parent_idx = item.get("ParentIndexNumber")
        disc_number = parent_idx if isinstance(parent_idx, int) else 0

        prod_year = item.get("ProductionYear")
        year = prod_year if isinstance(prod_year, int) else 0

        genres_raw = item.get("Genres")
        genres: tuple[str, ...] = ()
        if isinstance(genres_raw, list):
            genres = tuple(str(g) for g in genres_raw)

        media_type = str(item.get("MediaType", "Audio"))
        container = str(item.get("Container", ""))
        overview = str(item.get("Overview", ""))
        path = str(item.get("Path", ""))

        # Check media sources for container, size, bitrate
        size_bytes = 0
        bitrate_bps = 0
        media_sources = item.get("MediaSources")
        if isinstance(media_sources, list) and media_sources:
            first_source = media_sources[0]
            if isinstance(first_source, dict):
                if not container and "Container" in first_source:
                    container = str(first_source["Container"])
                if "Size" in first_source and isinstance(first_source["Size"], int):
                    size_bytes = first_source["Size"]
                if "Bitrate" in first_source and isinstance(
                    first_source["Bitrate"], int
                ):
                    bitrate_bps = first_source["Bitrate"]

        album_id = str(item.get("AlbumId", ""))
        album_primary_image_tag = str(item.get("AlbumPrimaryImageTag", ""))

        image_tags = item.get("ImageTags")
        image_tag = ""
        if isinstance(image_tags, dict) and "Primary" in image_tags:
            image_tag = str(image_tags["Primary"])
        elif album_primary_image_tag:
            image_tag = album_primary_image_tag

        return JellyfinItem(
            item_id=item_id,
            name=name,
            album=album,
            album_artist=album_artist,
            artist=artist,
            duration_ms=duration_ms,
            track_number=track_number,
            disc_number=disc_number,
            year=year,
            genres=genres,
            media_type=media_type,
            container=container,
            size_bytes=size_bytes,
            bitrate_bps=bitrate_bps,
            image_tag=image_tag,
            album_id=album_id,
            album_primary_image_tag=album_primary_image_tag,
            path=path,
            overview=overview,
        )

    def get_image_url(
        self,
        item_id: str,
        max_width: int = 600,
        quality: int = 90,
    ) -> str:
        """Return the URL to fetch an item's primary cover image."""
        url = (
            f"{self.server_url}/Items/{quote(item_id)}/Images/Primary"
            f"?maxWidth={max_width}&quality={quality}"
        )
        if self.token:
            url += f"&api_key={quote(self.token)}"
        return url

    def get_audio_stream_url(self, item_id: str) -> str:
        """Return the direct audio stream URL with authentication query parameter."""
        url = f"{self.server_url}/Audio/{quote(item_id)}/stream?static=true"
        if self.token:
            url += f"&api_key={quote(self.token)}"
        return url

    def get_video_stream_url(self, item_id: str) -> str:
        """Return the direct video stream URL with authentication query parameter."""
        url = f"{self.server_url}/Videos/{quote(item_id)}/stream?static=true"
        if self.token:
            url += f"&api_key={quote(self.token)}"
        return url

    def get_download_url(self, item_id: str) -> str:
        """Return the item download URL with authentication query parameter."""
        url = f"{self.server_url}/Items/{quote(item_id)}/Download"
        if self.token:
            url += f"?api_key={quote(self.token)}"
        return url
