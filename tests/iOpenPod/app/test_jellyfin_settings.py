"""Unit tests for Jellyfin settings definitions, persistence, and helpers."""

from __future__ import annotations

from iOpenPod.app.core.settings.service import SettingsService
from iOpenPod.app.core.settings.stores import DeviceSettingsStore, GlobalSettingsStore
from iOpenPod.app.jellyfin.models import JellyfinConfig, OverlapPolicy
from iOpenPod.app.jellyfin.settings import (
    JELLYFIN_ACCESS_TOKEN,
    JELLYFIN_ENABLED,
    JELLYFIN_OVERLAP_POLICY,
    JELLYFIN_SERVER_NAME,
    JELLYFIN_SERVER_URL,
    JELLYFIN_SYNC_MUSIC,
    JELLYFIN_SYNC_VIDEOS,
    JELLYFIN_USER_ID,
    JELLYFIN_USER_NAME,
    clear_jellyfin_config,
    load_jellyfin_config,
    save_jellyfin_config,
)


def _service() -> SettingsService:
    return SettingsService(GlobalSettingsStore(), DeviceSettingsStore())


def test_jellyfin_default_settings() -> None:
    settings = _service()
    assert settings.get(JELLYFIN_ENABLED) is False
    assert settings.get(JELLYFIN_SERVER_URL) == ""
    assert settings.get(JELLYFIN_ACCESS_TOKEN) == ""
    assert settings.get(JELLYFIN_USER_ID) == ""
    assert settings.get(JELLYFIN_USER_NAME) == ""
    assert settings.get(JELLYFIN_SERVER_NAME) == ""
    assert settings.get(JELLYFIN_OVERLAP_POLICY) == "prefer-local"
    assert settings.get(JELLYFIN_SYNC_MUSIC) is True
    assert settings.get(JELLYFIN_SYNC_VIDEOS) is True

    config = load_jellyfin_config(settings)
    assert config.is_authenticated is False
    assert config.overlap_policy is OverlapPolicy.PREFER_LOCAL


def test_save_and_load_jellyfin_config() -> None:
    settings = _service()
    config = JellyfinConfig(
        server_url="https://media.myhouse.net",
        access_token="tok_12345",
        user_id="uid_999",
        user_name="Valkai",
        server_name="Home Jellyfin",
        overlap_policy=OverlapPolicy.PREFER_JELLYFIN,
        sync_music=True,
        sync_videos=False,
    )

    save_jellyfin_config(settings, config)

    assert settings.get(JELLYFIN_ENABLED) is True
    assert settings.get(JELLYFIN_SERVER_URL) == "https://media.myhouse.net"
    assert settings.get(JELLYFIN_ACCESS_TOKEN) == "tok_12345"
    assert settings.get(JELLYFIN_USER_ID) == "uid_999"
    assert settings.get(JELLYFIN_USER_NAME) == "Valkai"
    assert settings.get(JELLYFIN_SERVER_NAME) == "Home Jellyfin"
    assert settings.get(JELLYFIN_OVERLAP_POLICY) == "prefer-jellyfin"
    assert settings.get(JELLYFIN_SYNC_MUSIC) is True
    assert settings.get(JELLYFIN_SYNC_VIDEOS) is False

    loaded = load_jellyfin_config(settings)
    assert loaded == config
    assert loaded.is_authenticated is True


def test_clear_jellyfin_config() -> None:
    settings = _service()
    config = JellyfinConfig(
        server_url="https://media.myhouse.net",
        access_token="tok_12345",
        user_id="uid_999",
        user_name="Valkai",
    )
    save_jellyfin_config(settings, config)
    assert settings.get(JELLYFIN_ENABLED) is True

    clear_jellyfin_config(settings)

    assert settings.get(JELLYFIN_ENABLED) is False
    assert settings.get(JELLYFIN_SERVER_URL) == ""
    assert settings.get(JELLYFIN_ACCESS_TOKEN) == ""
    assert settings.get(JELLYFIN_USER_ID) == ""
    assert settings.get(JELLYFIN_USER_NAME) == ""

    loaded = load_jellyfin_config(settings)
    assert loaded.is_authenticated is False
