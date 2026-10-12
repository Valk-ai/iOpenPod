"""Unit tests for Jellyfin HTTP client and Quick Connect authentication."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

import pytest

from iOpenPod.app.jellyfin.client import JellyfinClient
from iOpenPod.app.jellyfin.models import (
    JellyfinAuthError,
    JellyfinConnectionError,
    JellyfinResponse,
)


class MockTransport:
    """Mock transport that records requests and returns staged responses."""

    def __init__(self, *responses: JellyfinResponse | Exception) -> None:
        self.responses = list(responses)
        self.history: list[dict[str, Any]] = []

    def request(
        self,
        url: str,
        method: str = "GET",
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> JellyfinResponse:
        self.history.append(
            {
                "url": url,
                "method": method,
                "data": data,
                "headers": dict(headers or {}),
            }
        )
        if not self.responses:
            raise RuntimeError(f"Unexpected request: {method} {url}")
        next_resp = self.responses.pop(0)
        if isinstance(next_resp, Exception):
            raise next_resp
        return next_resp


def test_jellyfin_client_headers() -> None:
    transport = MockTransport(
        JellyfinResponse(status=200, data={"ServerName": "TestServer"}, headers={})
    )
    client = JellyfinClient(
        "http://jellyfin.local:8096",
        token="test-token",
        device_id="custom-device-id",
        transport=transport,
    )

    info = client.ping()
    assert info == {"ServerName": "TestServer"}
    assert len(transport.history) == 1

    req = transport.history[0]
    assert req["url"] == "http://jellyfin.local:8096/System/Info/Public"
    assert req["method"] == "GET"
    assert "Authorization" in req["headers"]
    assert "X-Emby-Authorization" in req["headers"]
    assert req["headers"]["X-MediaBrowser-Token"] == "test-token"
    assert 'DeviceId="custom-device-id"' in req["headers"]["Authorization"]
    assert 'Token="test-token"' in req["headers"]["Authorization"]


def test_initiate_quick_connect() -> None:
    transport = MockTransport(
        JellyfinResponse(
            status=200,
            data={"Code": "842910", "Secret": "sec-12345", "Authentication": False},
            headers={},
        )
    )
    client = JellyfinClient("http://jellyfin.local:8096", transport=transport)

    result = client.initiate_quick_connect()
    assert result.code == "842910"
    assert result.secret == "sec-12345"

    req = transport.history[0]
    assert req["url"] == "http://jellyfin.local:8096/QuickConnect/Initiate"
    assert req["method"] == "POST"
    assert req["headers"]["Content-Type"] == "application/json"


def test_check_quick_connect_pending_and_approved() -> None:
    transport = MockTransport(
        JellyfinResponse(
            status=200,
            data={"Authenticated": False, "Secret": "sec-12345"},
            headers={},
        ),
        JellyfinResponse(
            status=200,
            data={"Authenticated": True, "Secret": "sec-12345"},
            headers={},
        ),
    )
    client = JellyfinClient("http://jellyfin.local:8096", transport=transport)

    assert client.check_quick_connect("sec-12345") is False
    assert client.check_quick_connect("sec-12345") is True

    req = transport.history[0]
    assert (
        req["url"] == "http://jellyfin.local:8096/QuickConnect/Connect?Secret=sec-12345"
    )
    assert req["method"] == "GET"


def test_authenticate_quick_connect() -> None:
    transport = MockTransport(
        JellyfinResponse(
            status=200,
            data={
                "AccessToken": "jwt-token-quick",
                "ServerId": "srv-999",
                "User": {"Id": "user-abc", "Name": "Audiophile"},
            },
            headers={},
        )
    )
    client = JellyfinClient("http://jellyfin.local:8096", transport=transport)

    auth = client.authenticate_quick_connect("sec-12345")
    assert auth.access_token == "jwt-token-quick"
    assert auth.user_id == "user-abc"
    assert auth.user_name == "Audiophile"
    assert auth.server_id == "srv-999"

    req = transport.history[0]
    assert req["url"] == "http://jellyfin.local:8096/Users/AuthenticateWithQuickConnect"
    assert req["method"] == "POST"
    assert req["headers"]["Content-Type"] == "application/json"
    body = json.loads(req["data"].decode("utf-8"))
    assert body == {"Secret": "sec-12345"}


def test_authenticate_by_name() -> None:
    transport = MockTransport(
        JellyfinResponse(
            status=200,
            data={
                "AccessToken": "jwt-token-name",
                "ServerId": "srv-999",
                "User": {"Id": "user-xyz", "Name": "Tester"},
            },
            headers={},
        )
    )
    client = JellyfinClient("http://jellyfin.local:8096", transport=transport)

    auth = client.authenticate_by_name("Tester", "Password123")
    assert auth.access_token == "jwt-token-name"
    assert auth.user_id == "user-xyz"
    assert auth.user_name == "Tester"

    req = transport.history[0]
    assert req["url"] == "http://jellyfin.local:8096/Users/AuthenticateByName"
    assert req["method"] == "POST"
    body = json.loads(req["data"].decode("utf-8"))
    assert body == {"Username": "Tester", "Pw": "Password123"}


def test_auth_failure_raises_jellyfin_auth_error() -> None:
    transport = MockTransport(JellyfinAuthError("Invalid username or password"))
    client = JellyfinClient("http://jellyfin.local:8096", transport=transport)

    with pytest.raises(JellyfinAuthError, match="Invalid username or password"):
        client.authenticate_by_name("BadUser", "Wrong")


def test_connection_error_raises_jellyfin_connection_error() -> None:
    transport = MockTransport(JellyfinConnectionError("Connection refused"))
    client = JellyfinClient("http://jellyfin.local:8096", transport=transport)

    with pytest.raises(JellyfinConnectionError, match="Connection refused"):
        client.ping()


def test_get_user_views() -> None:
    transport = MockTransport(
        JellyfinResponse(
            status=200,
            data={
                "Items": [
                    {
                        "Id": "view-music-1",
                        "Name": "Music",
                        "CollectionType": "music",
                    },
                    {
                        "Id": "view-movies-2",
                        "Name": "Movies",
                        "CollectionType": "movies",
                    },
                ]
            },
            headers={},
        )
    )
    client = JellyfinClient(
        "http://jellyfin.local:8096", token="tok", transport=transport
    )

    views = client.get_user_views("user-abc")
    assert len(views) == 2
    assert views[0].view_id == "view-music-1"
    assert views[0].name == "Music"
    assert views[0].collection_type == "music"
    assert views[1].view_id == "view-movies-2"


def test_get_music_tracks_parsing() -> None:
    transport = MockTransport(
        JellyfinResponse(
            status=200,
            data={
                "Items": [
                    {
                        "Id": "track-101",
                        "Name": "Bohemian Rhapsody",
                        "Album": "A Night at the Opera",
                        "AlbumArtist": "Queen",
                        "Artists": ["Queen"],
                        "RunTimeTicks": 3540000000,  # 354,000 ms = 5m 54s
                        "IndexNumber": 11,
                        "ParentIndexNumber": 1,
                        "ProductionYear": 1975,
                        "Genres": ["Classic Rock", "Rock"],
                        "MediaType": "Audio",
                        "Container": "flac",
                        "MediaSources": [
                            {
                                "Container": "flac",
                                "Size": 45000000,
                                "Bitrate": 1024000,
                            }
                        ],
                        "ImageTags": {"Primary": "img-tag-123"},
                        "Path": "/media/music/queen/01.flac",
                    }
                ]
            },
            headers={},
        )
    )
    client = JellyfinClient(
        "http://jellyfin.local:8096", token="tok", transport=transport
    )

    tracks = client.get_music_tracks("user-abc", parent_id="view-music-1")
    assert len(tracks) == 1
    t = tracks[0]
    assert t.item_id == "track-101"
    assert t.name == "Bohemian Rhapsody"
    assert t.album == "A Night at the Opera"
    assert t.artist == "Queen"
    assert t.duration_ms == 354000
    assert t.track_number == 11
    assert t.disc_number == 1
    assert t.year == 1975
    assert t.genres == ("Classic Rock", "Rock")
    assert t.is_audio is True
    assert t.is_video is False
    assert t.container == "flac"
    assert t.size_bytes == 45000000
    assert t.bitrate_bps == 1024000
    assert t.image_tag == "img-tag-123"


def test_url_helpers() -> None:
    client = JellyfinClient("http://jellyfin.local:8096/", token="test-api-key")

    img_url = client.get_image_url("item-42", max_width=400, quality=85)
    assert (
        img_url
        == "http://jellyfin.local:8096/Items/item-42/Images/Primary?maxWidth=400&quality=85&api_key=test-api-key"
    )

    audio_url = client.get_audio_stream_url("item-42")
    assert (
        audio_url
        == "http://jellyfin.local:8096/Audio/item-42/stream?static=true&api_key=test-api-key"
    )

    video_url = client.get_video_stream_url("item-42")
    assert (
        video_url
        == "http://jellyfin.local:8096/Videos/item-42/stream?static=true&api_key=test-api-key"
    )

    dl_url = client.get_download_url("item-42")
    assert (
        dl_url
        == "http://jellyfin.local:8096/Items/item-42/Download?api_key=test-api-key"
    )
