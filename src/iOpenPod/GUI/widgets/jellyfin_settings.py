"""Jellyfin media server connection controls within Settings → Sync."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from iOpenPod.app.jellyfin.client import JellyfinClient
from iOpenPod.app.jellyfin.models import (
    JellyfinAuthError,
    JellyfinConfig,
    JellyfinConnectionError,
    JellyfinError,
    OverlapPolicy,
)
from iOpenPod.app.jellyfin.settings import (
    JELLYFIN_OVERLAP_POLICY,
    JELLYFIN_SYNC_MUSIC,
    JELLYFIN_SYNC_VIDEOS,
    clear_jellyfin_config,
    load_jellyfin_config,
    save_jellyfin_config,
)
from iOpenPod.GUI.presentation.theme.tokens import LAYOUT
from iOpenPod.GUI.widgets.app_combo_box import AppComboBox
from iOpenPod.GUI.widgets.setting_group import SettingGroup, SettingRow
from iOpenPod.GUI.widgets.settings_editor import ScopedSettingBinding, SettingsEditor
from iOpenPod.GUI.widgets.themed_buttons import ActionButton, ActionButtonKind

if TYPE_CHECKING:
    from collections.abc import Sequence

    from iOpenPod.app.core.settings.definitions import SettingDefinition
    from iOpenPod.app.core.settings.service import SettingsService

logger = logging.getLogger(__name__)

POLL_INTERVAL_MS = 2500


class _Choice[Value](SettingRow):
    """Bind a dropdown choice to one typed setting."""

    def __init__(
        self,
        settings: SettingsEditor,
        definition: SettingDefinition[Value],
        name: str,
        parent: QWidget,
    ) -> None:
        self.combo = AppComboBox(parent)
        self.combo.setObjectName(name)
        super().__init__("", "", self.combo, parent)
        self._binding = ScopedSettingBinding(settings, definition, self, self.combo)

    def configure(
        self,
        choices: Sequence[tuple[str, Value]],
        definition: SettingDefinition[Value] | None = None,
    ) -> None:
        self._binding.configure(choices, definition)


class JellyfinSettings(QWidget):
    """Jellyfin authentication and sync configuration for Settings -> Sync tab."""

    def __init__(
        self,
        settings: SettingsService,
        parent: QWidget | None = None,
        *,
        editor: SettingsEditor | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("jellyfinSettings")
        self._settings = settings
        scope = editor or SettingsEditor(settings, self)

        self._pending_client: JellyfinClient | None = None
        self._pending_secret: str = ""
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_quick_connect)

        self._title = QLabel(self)
        self._title.setObjectName("sectionTitle")

        self._status = QLabel(self)
        self._status.setObjectName("jellyfinStatus")
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._status.setWordWrap(True)

        # Quick connect active display banner
        self._quick_connect_panel = QFrame(self)
        self._quick_connect_panel.setObjectName("quickConnectBanner")
        self._quick_connect_code = QLabel(self._quick_connect_panel)
        self._quick_connect_code.setObjectName("quickConnectCode")
        self._quick_connect_code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._quick_connect_hint = QLabel(self._quick_connect_panel)
        self._quick_connect_hint.setObjectName("quickConnectHint")
        self._quick_connect_hint.setWordWrap(True)
        self._quick_connect_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        qc_layout = QVBoxLayout(self._quick_connect_panel)
        qc_layout.setContentsMargins(
            LAYOUT.space_md,
            LAYOUT.space_md,
            LAYOUT.space_md,
            LAYOUT.space_md,
        )
        qc_layout.setSpacing(LAYOUT.space_xs)
        qc_layout.addWidget(self._quick_connect_code)
        qc_layout.addWidget(self._quick_connect_hint)
        self._quick_connect_panel.hide()

        # Input fields
        self._server_url = QLineEdit(self)
        self._server_url.setObjectName("jellyfinServerUrl")
        self._server_url.setPlaceholderText("http://localhost:8096")
        self._server_url_row = SettingRow("", "", self._server_url, self)

        # Quick connect action
        self._quick_connect_btn = ActionButton(
            parent=self, kind=ActionButtonKind.PRIMARY
        )
        self._quick_connect_btn.setObjectName("jellyfinQuickConnectButton")
        self._quick_connect_btn.clicked.connect(self._begin_quick_connect)

        self._cancel_qc_btn = ActionButton(parent=self, kind=ActionButtonKind.SECONDARY)
        self._cancel_qc_btn.setObjectName("jellyfinCancelQuickConnectButton")
        self._cancel_qc_btn.clicked.connect(self._cancel_quick_connect)
        self._cancel_qc_btn.hide()

        qc_btn_container = QWidget(self)
        qc_btn_layout = QHBoxLayout(qc_btn_container)
        qc_btn_layout.setContentsMargins(0, 0, 0, 0)
        qc_btn_layout.setSpacing(LAYOUT.space_xs)
        qc_btn_layout.addWidget(self._quick_connect_btn)
        qc_btn_layout.addWidget(self._cancel_qc_btn)

        self._quick_connect_row = SettingRow("", "", qc_btn_container, self)

        # Password login inputs
        self._username = QLineEdit(self)
        self._username.setObjectName("jellyfinUsername")
        self._username_row = SettingRow("", "", self._username, self)

        self._password = QLineEdit(self)
        self._password.setObjectName("jellyfinPassword")
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password_row = SettingRow("", "", self._password, self)

        self._login_btn = ActionButton(parent=self, kind=ActionButtonKind.SECONDARY)
        self._login_btn.setObjectName("jellyfinLoginButton")
        self._login_btn.clicked.connect(self._login_with_password)
        self._login_row = SettingRow("", "", self._login_btn, self)

        # Disconnect action
        self._disconnect_btn = ActionButton(parent=self, kind=ActionButtonKind.DANGER)
        self._disconnect_btn.setObjectName("jellyfinDisconnectButton")
        self._disconnect_btn.clicked.connect(self._disconnect)
        self._connected_info_row = SettingRow("", "", self._disconnect_btn, self)

        # Connection Setting Group
        self._auth_group = SettingGroup(self)
        self._auth_group.setMaximumWidth(960)
        self._auth_group.add_row(self._server_url_row)
        self._auth_group.add_row(self._quick_connect_row)
        self._auth_group.add_row(self._username_row)
        self._auth_group.add_row(self._password_row)
        self._auth_group.add_row(self._login_row)
        self._auth_group.add_row(self._connected_info_row)

        # Sync Preferences Setting Group
        self._overlap_policy = _Choice(
            scope,
            JELLYFIN_OVERLAP_POLICY,
            "jellyfinOverlapPolicy",
            self,
        )
        self._sync_music = _Choice(
            scope,
            JELLYFIN_SYNC_MUSIC,
            "jellyfinSyncMusic",
            self,
        )
        self._sync_videos = _Choice(
            scope,
            JELLYFIN_SYNC_VIDEOS,
            "jellyfinSyncVideos",
            self,
        )

        self._options_group = SettingGroup(self)
        self._options_group.setMaximumWidth(960)
        self._options_group.add_row(self._overlap_policy)
        self._options_group.add_row(self._sync_music)
        self._options_group.add_row(self._sync_videos)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(LAYOUT.space_xs)
        layout.addWidget(self._title)
        layout.addWidget(self._quick_connect_panel)
        layout.addWidget(self._auth_group)
        layout.addWidget(self._options_group)
        layout.addWidget(self._status)

        settings.settingChanged.connect(self._on_setting_changed)
        self._refresh()
        self.retranslate_ui()

    def _normalize_server_url(self, raw: str) -> str:
        url = raw.strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            url = f"http://{url}"
        return url

    def _begin_quick_connect(self) -> None:
        url = self._normalize_server_url(self._server_url.text())
        if not url:
            self._status.setText(self.tr("Please enter your Jellyfin server URL."))
            return

        self._status.setText(self.tr("Contacting Jellyfin server..."))
        client = JellyfinClient(url)
        try:
            client.ping()
            result = client.initiate_quick_connect()
        except JellyfinConnectionError as exc:
            self._status.setText(
                self.tr("Cannot reach server: {error}").format(error=exc)
            )
            return
        except JellyfinError as exc:
            self._status.setText(
                self.tr("Quick Connect error: {error}").format(error=exc)
            )
            return

        self._pending_client = client
        self._pending_secret = result.secret

        # Show code in prominent panel
        self._quick_connect_code.setText(f"{result.code}")
        self._quick_connect_hint.setText(
            self.tr(
                "Enter this code in your Jellyfin server dashboard under "
                "Settings → Quick Connect to authorize iOpenPod."
            )
        )
        self._quick_connect_panel.show()
        self._quick_connect_btn.hide()
        self._cancel_qc_btn.show()
        self._status.setText(
            self.tr("Awaiting authentication on your Jellyfin server...")
        )
        self._poll_timer.start()

    def _cancel_quick_connect(self) -> None:
        self._poll_timer.stop()
        self._pending_client = None
        self._pending_secret = ""
        self._quick_connect_panel.hide()
        self._cancel_qc_btn.hide()
        self._quick_connect_btn.show()
        self._status.setText(self.tr("Quick Connect cancelled."))

    def _poll_quick_connect(self) -> None:
        if not self._pending_client or not self._pending_secret:
            self._poll_timer.stop()
            return

        try:
            authenticated = self._pending_client.check_quick_connect(
                self._pending_secret
            )
            if authenticated:
                self._poll_timer.stop()
                auth = self._pending_client.authenticate_quick_connect(
                    self._pending_secret
                )
                self._pending_client.token = auth.access_token
                info = self._pending_client.ping()
                server_name = str(info.get("ServerName", "Jellyfin Server"))

                config = JellyfinConfig(
                    server_url=self._pending_client.server_url,
                    access_token=auth.access_token,
                    user_id=auth.user_id,
                    user_name=auth.user_name,
                    server_name=server_name,
                )
                save_jellyfin_config(self._settings, config)
                self._cancel_quick_connect()
                self._refresh()
                self._status.setText(
                    self.tr("Successfully connected to {server} as {user}!").format(
                        server=server_name,
                        user=auth.user_name,
                    )
                )
        except Exception as exc:
            logger.debug("Quick Connect polling check: %s", exc)

    def _login_with_password(self) -> None:
        url = self._normalize_server_url(self._server_url.text())
        username = self._username.text().strip()
        password = self._password.text()

        if not url:
            self._status.setText(self.tr("Please enter your Jellyfin server URL."))
            return
        if not username:
            self._status.setText(self.tr("Please enter your username."))
            return

        self._status.setText(self.tr("Signing in..."))
        client = JellyfinClient(url)
        try:
            client.ping()
            auth = client.authenticate_by_name(username, password)
            client.token = auth.access_token
            info = client.ping()
            server_name = str(info.get("ServerName", "Jellyfin Server"))

            config = JellyfinConfig(
                server_url=url,
                access_token=auth.access_token,
                user_id=auth.user_id,
                user_name=auth.user_name,
                server_name=server_name,
            )
            save_jellyfin_config(self._settings, config)
            self._password.clear()
            self._refresh()
            self._status.setText(
                self.tr("Successfully connected to {server} as {user}!").format(
                    server=server_name,
                    user=auth.user_name,
                )
            )
        except JellyfinAuthError as exc:
            self._status.setText(
                self.tr("Authentication failed: {error}").format(error=exc)
            )
        except JellyfinConnectionError as exc:
            self._status.setText(
                self.tr("Cannot reach server: {error}").format(error=exc)
            )
        except JellyfinError as exc:
            self._status.setText(self.tr("Sign-in error: {error}").format(error=exc))

    def _disconnect(self) -> None:
        self._poll_timer.stop()
        clear_jellyfin_config(self._settings)
        self._refresh()
        self._status.setText(self.tr("Disconnected from Jellyfin."))

    def _on_setting_changed(self, key: str, _value: object) -> None:
        if key.startswith("jellyfin/"):
            self._refresh()

    def _refresh(self) -> None:
        config = load_jellyfin_config(self._settings)
        is_connected = config.is_authenticated

        self._server_url.setText(config.server_url)
        self._server_url_row.setVisible(not is_connected)
        self._quick_connect_row.setVisible(not is_connected)
        self._username_row.setVisible(not is_connected)
        self._password_row.setVisible(not is_connected)
        self._login_row.setVisible(not is_connected)

        self._connected_info_row.setVisible(is_connected)
        self._options_group.setVisible(is_connected)

        if is_connected:
            self._connected_info_row.set_copy(
                config.server_name or config.server_url,
                self.tr("Connected as {user}").format(
                    user=config.user_name or config.user_id
                ),
            )
        elif not self._poll_timer.isActive():
            self._status.setText(self.tr("Not connected to Jellyfin."))

    def retranslate_ui(self) -> None:
        self._title.setText(self.tr("Jellyfin Media Server"))
        self._server_url_row.set_copy(
            self.tr("Server Address"),
            self.tr("HTTP or HTTPS address of your Jellyfin server."),
        )
        self._quick_connect_row.set_copy(
            self.tr("Quick Connect"),
            self.tr(
                "Generate a temporary 6-digit code to authorize via your server dashboard without typing your password."
            ),
        )
        self._quick_connect_btn.setText(self.tr("Use Quick Connect"))
        self._cancel_qc_btn.setText(self.tr("Cancel"))
        self._username_row.set_copy(
            self.tr("Username"),
            self.tr("Your Jellyfin account username."),
        )
        self._password_row.set_copy(
            self.tr("Password"),
            self.tr("Your Jellyfin account password."),
        )
        self._login_btn.setText(self.tr("Sign In"))
        self._disconnect_btn.setText(self.tr("Disconnect"))

        self._overlap_policy.configure(
            (
                (
                    self.tr("Prefer Local Files (Recommended)"),
                    OverlapPolicy.PREFER_LOCAL.value,
                ),
                (
                    self.tr("Prefer Jellyfin Server"),
                    OverlapPolicy.PREFER_JELLYFIN.value,
                ),
                (
                    self.tr("Always Ask / Prompt"),
                    OverlapPolicy.PROMPT.value,
                ),
            )
        )
        self._overlap_policy.set_copy(
            self.tr("Duplicate Resolution Preference"),
            self.tr(
                "When identical media exists both locally and on Jellyfin, choose which source to prioritize."
            ),
        )

        self._sync_music.configure(((self.tr("Off"), False), (self.tr("On"), True)))
        self._sync_music.set_copy(
            self.tr("Sync Music Library"),
            self.tr("Include music albums and tracks from Jellyfin during Sync."),
        )

        self._sync_videos.configure(((self.tr("Off"), False), (self.tr("On"), True)))
        self._sync_videos.set_copy(
            self.tr("Sync Video Library"),
            self.tr(
                "Include movies and TV shows from Jellyfin (only synced if Active iPod supports video)."
            ),
        )

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate_ui()
        super().changeEvent(event)
