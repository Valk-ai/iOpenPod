"""Data models and exceptions for Jellyfin media server integration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


class JellyfinError(Exception):
    """Base exception for Jellyfin client interactions."""


class JellyfinConnectionError(JellyfinError):
    """Raised when the Jellyfin server cannot be reached."""


class JellyfinAuthError(JellyfinError):
    """Raised when authentication fails or credentials are rejected."""


class JellyfinNotFoundError(JellyfinError):
    """Raised when a requested resource is not found."""


class OverlapPolicy(StrEnum):
    """Policy for resolving media that exists both locally and on Jellyfin."""

    PREFER_LOCAL = "prefer-local"
    PREFER_JELLYFIN = "prefer-jellyfin"
    PROMPT = "prompt"


@dataclass(frozen=True, slots=True)
class JellyfinConfig:
    """Configured connection details for a Jellyfin server."""

    server_url: str
    access_token: str
    user_id: str
    user_name: str = ""
    server_name: str = ""
    overlap_policy: OverlapPolicy = OverlapPolicy.PREFER_LOCAL
    sync_music: bool = True
    sync_videos: bool = True

    @property
    def is_authenticated(self) -> bool:
        return bool(self.server_url and self.access_token and self.user_id)


@dataclass(frozen=True, slots=True)
class QuickConnectInitiateResult:
    """Result of initiating a Quick Connect session."""

    code: str
    secret: str


@dataclass(frozen=True, slots=True)
class JellyfinAuthResult:
    """Authentication response payload."""

    access_token: str
    user_id: str
    user_name: str
    server_id: str = ""


@dataclass(frozen=True, slots=True)
class JellyfinView:
    """A top-level library view in Jellyfin (e.g. Music, Movies, TV Shows)."""

    view_id: str
    name: str
    collection_type: str = ""  # e.g. "music", "movies", "tvshows"


@dataclass(frozen=True, slots=True)
class JellyfinItem:
    """A media item retrieved from Jellyfin."""

    item_id: str
    name: str
    album: str = ""
    album_artist: str = ""
    artist: str = ""
    duration_ms: int = 0
    track_number: int = 0
    disc_number: int = 0
    year: int = 0
    genres: tuple[str, ...] = ()
    media_type: str = "Audio"  # "Audio" or "Video"
    container: str = ""  # e.g. "mp3", "flac", "m4a", "mp4"
    size_bytes: int = 0
    bitrate_bps: int = 0
    image_tag: str = ""
    album_id: str = ""
    album_primary_image_tag: str = ""
    path: str = ""
    overview: str = ""

    @property
    def is_audio(self) -> bool:
        return self.media_type.casefold() == "audio"

    @property
    def is_video(self) -> bool:
        return self.media_type.casefold() == "video"


@dataclass(frozen=True, slots=True)
class JellyfinResponse:
    """Internal HTTP response payload."""

    status: int
    data: dict[str, object]
    headers: Mapping[str, str]
