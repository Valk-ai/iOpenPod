"""Unit tests for the JellyfinSettings widget."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit

if TYPE_CHECKING:
    import pytest

from iOpenPod.app.core.settings.service import SettingsService
from iOpenPod.app.core.settings.stores import DeviceSettingsStore, GlobalSettingsStore
from iOpenPod.app.jellyfin.client import JellyfinClient, Transport
from iOpenPod.app.jellyfin.models import JellyfinConfig, JellyfinResponse
from iOpenPod.app.jellyfin.settings import (
    JELLYFIN_ENABLED,
    JELLYFIN_SERVER_URL,
    save_jellyfin_config,
)
from iOpenPod.GUI.widgets.jellyfin_settings import JellyfinSettings
from iOpenPod.GUI.widgets.themed_buttons import ActionButton


def _application() -> QApplication:
    existing = QApplication.instance()
    if isinstance(existing, QApplication):
        return existing
    return QApplication([])


class MockTransport:
    def __init__(self, *responses: JellyfinResponse | Exception) -> None:
        self.responses = list(responses)
        self.history: list[str] = []

    def request(
        self,
        url: str,
        method: str = "GET",
        data: bytes | None = None,
        headers: object = None,
    ) -> JellyfinResponse:
        self.history.append(f"{method} {url}")
        if not self.responses:
            raise RuntimeError(f"Unexpected request: {method} {url}")
        next_resp = self.responses.pop(0)
        if isinstance(next_resp, Exception):
            raise next_resp
        return next_resp


def test_jellyfin_settings_initial_state() -> None:
    _application()
    settings = SettingsService(GlobalSettingsStore(), DeviceSettingsStore())
    widget = JellyfinSettings(settings)

    url_edit = widget.findChild(QLineEdit, "jellyfinServerUrl")
    qc_btn = widget.findChild(ActionButton, "jellyfinQuickConnectButton")
    status = widget.findChild(QLabel, "jellyfinStatus")

    assert url_edit is not None
    assert qc_btn is not None
    assert status is not None
    assert url_edit.text() == ""
    assert "Not connected" in status.text()


def test_jellyfin_settings_connected_state() -> None:
    _application()
    settings = SettingsService(GlobalSettingsStore(), DeviceSettingsStore())
    save_jellyfin_config(
        settings,
        JellyfinConfig(
            server_url="http://media.home:8096",
            access_token="tok_123",
            user_id="usr_456",
            user_name="Valkai",
            server_name="Home Server",
        ),
    )

    widget = JellyfinSettings(settings)

    url_edit = widget.findChild(QLineEdit, "jellyfinServerUrl")
    qc_btn = widget.findChild(ActionButton, "jellyfinQuickConnectButton")
    disconnect_btn = widget.findChild(ActionButton, "jellyfinDisconnectButton")

    assert url_edit is not None
    assert qc_btn is not None
    assert disconnect_btn is not None

    widget.show()
    # When connected, input fields and quick connect button are hidden
    assert widget._server_url_row.isVisible() is False
    assert disconnect_btn.isVisible() is True

    # Clicking disconnect resets back
    disconnect_btn.click()
    assert settings.get(JELLYFIN_ENABLED) is False
    assert settings.get(JELLYFIN_SERVER_URL) == ""


def test_jellyfin_settings_quick_connect_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    _application()
    settings = SettingsService(GlobalSettingsStore(), DeviceSettingsStore())
    widget = JellyfinSettings(settings)

    url_edit = widget.findChild(QLineEdit, "jellyfinServerUrl")
    qc_btn = widget.findChild(ActionButton, "jellyfinQuickConnectButton")
    qc_code = widget.findChild(QLabel, "quickConnectCode")

    assert url_edit is not None
    assert qc_btn is not None
    assert qc_code is not None

    # Set mock client
    test_transport = MockTransport(
        JellyfinResponse(200, {"ServerName": "TestServer"}, {}),  # ping
        JellyfinResponse(200, {"Code": "999888", "Secret": "sec-abc"}, {}),  # initiate
        JellyfinResponse(200, {"Authenticated": True}, {}),  # check
        JellyfinResponse(
            200,
            {
                "AccessToken": "qc-jwt",
                "ServerId": "srv-1",
                "User": {"Id": "u-1", "Name": "Alice"},
            },
            {},
        ),  # auth
        JellyfinResponse(200, {"ServerName": "TestServer"}, {}),  # ping
    )

    original_client_init = JellyfinClient.__init__

    def mock_client_init(
        self: JellyfinClient,
        server_url: str,
        token: str = "",
        *,
        device_id: str = "iopenpod-desktop",
        transport: Transport | None = None,
    ) -> None:
        original_client_init(
            self,
            server_url,
            token=token,
            device_id=device_id,
            transport=test_transport,
        )

    monkeypatch.setattr(JellyfinClient, "__init__", mock_client_init)

    url_edit.setText("http://jellyfin.lan:8096")
    qc_btn.click()

    assert qc_code.text() == "999888"
    assert widget._poll_timer.isActive() is True

    # Simulate timer poll
    widget._poll_quick_connect()

    assert widget._poll_timer.isActive() is False
    assert settings.get(JELLYFIN_ENABLED) is True
    assert settings.get(JELLYFIN_SERVER_URL) == "http://jellyfin.lan:8096"
