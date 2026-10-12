"""Settings definitions and resolution for Jellyfin media server."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from iOpenPod.app.core.settings.definitions import SettingDefinition

from .models import JellyfinConfig, OverlapPolicy

if TYPE_CHECKING:
    from iOpenPod.app.core.settings.service import SettingsService

JELLYFIN_ENABLED = SettingDefinition[bool](
    key="jellyfin/enabled",
    value_type=bool,
    default=False,
)

JELLYFIN_SERVER_URL = SettingDefinition[str](
    key="jellyfin/server-url",
    value_type=str,
    default="",
)

JELLYFIN_ACCESS_TOKEN = SettingDefinition[str](
    key="jellyfin/access-token",
    value_type=str,
    default="",
)

JELLYFIN_USER_ID = SettingDefinition[str](
    key="jellyfin/user-id",
    value_type=str,
    default="",
)

JELLYFIN_USER_NAME = SettingDefinition[str](
    key="jellyfin/user-name",
    value_type=str,
    default="",
)

JELLYFIN_SERVER_NAME = SettingDefinition[str](
    key="jellyfin/server-name",
    value_type=str,
    default="",
)

JELLYFIN_OVERLAP_POLICY = SettingDefinition[str](
    key="jellyfin/overlap-policy",
    value_type=str,
    default=OverlapPolicy.PREFER_LOCAL.value,
    validator=lambda v: (
        v
        in (
            OverlapPolicy.PREFER_LOCAL.value,
            OverlapPolicy.PREFER_JELLYFIN.value,
            OverlapPolicy.PROMPT.value,
        )
    ),
)

JELLYFIN_SYNC_MUSIC = SettingDefinition[bool](
    key="jellyfin/sync-music",
    value_type=bool,
    default=True,
)

JELLYFIN_SYNC_VIDEOS = SettingDefinition[bool](
    key="jellyfin/sync-videos",
    value_type=bool,
    default=True,
)


def load_jellyfin_config(settings: SettingsService) -> JellyfinConfig:
    """Read the current Jellyfin connection configuration from settings."""
    server_url = settings.get(JELLYFIN_SERVER_URL)
    access_token = settings.get(JELLYFIN_ACCESS_TOKEN)
    user_id = settings.get(JELLYFIN_USER_ID)
    user_name = settings.get(JELLYFIN_USER_NAME)
    server_name = settings.get(JELLYFIN_SERVER_NAME)
    raw_policy = settings.get(JELLYFIN_OVERLAP_POLICY)

    overlap_policy = OverlapPolicy.PREFER_LOCAL
    with contextlib.suppress(ValueError):
        overlap_policy = OverlapPolicy(raw_policy)

    sync_music = settings.get(JELLYFIN_SYNC_MUSIC)
    sync_videos = settings.get(JELLYFIN_SYNC_VIDEOS)

    return JellyfinConfig(
        server_url=server_url,
        access_token=access_token,
        user_id=user_id,
        user_name=user_name,
        server_name=server_name,
        overlap_policy=overlap_policy,
        sync_music=sync_music,
        sync_videos=sync_videos,
    )


def save_jellyfin_config(
    settings: SettingsService,
    config: JellyfinConfig,
) -> None:
    """Persist Jellyfin connection configuration to global settings."""
    settings.set_global(JELLYFIN_SERVER_URL, config.server_url)
    settings.set_global(JELLYFIN_ACCESS_TOKEN, config.access_token)
    settings.set_global(JELLYFIN_USER_ID, config.user_id)
    settings.set_global(JELLYFIN_USER_NAME, config.user_name)
    settings.set_global(JELLYFIN_SERVER_NAME, config.server_name)
    settings.set_global(JELLYFIN_OVERLAP_POLICY, config.overlap_policy.value)
    settings.set_global(JELLYFIN_SYNC_MUSIC, config.sync_music)
    settings.set_global(JELLYFIN_SYNC_VIDEOS, config.sync_videos)
    settings.set_global(JELLYFIN_ENABLED, config.is_authenticated)


def clear_jellyfin_config(settings: SettingsService) -> None:
    """Disconnect Jellyfin and remove stored credentials from settings."""
    settings.set_global(JELLYFIN_ENABLED, False)
    settings.set_global(JELLYFIN_SERVER_URL, "")
    settings.set_global(JELLYFIN_ACCESS_TOKEN, "")
    settings.set_global(JELLYFIN_USER_ID, "")
    settings.set_global(JELLYFIN_USER_NAME, "")
    settings.set_global(JELLYFIN_SERVER_NAME, "")
