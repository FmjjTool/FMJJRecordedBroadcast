"""腾讯文档官方 OpenAPI 凭证卡片。

只保留原「映射回写」里真正需要的登录信息：Client-Id / Access-Token / Open-Id
三个 ID 的填写、加密保存与清除。字段映射、写回预览等界面已移除。
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QCheckBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from toolbox.tcdoc import openapi


class OpenApiCredentialsBox(QGroupBox):
    """默认收起的凭证卡片，展开后是一行三个 ID + 保存 / 清除。"""

    def __init__(self, parent=None, log: Callable[[str], None] | None = None):
        super().__init__(parent)
        self._log = log or (lambda _text: None)
        self.setCheckable(True)
        self.setChecked(False)
        self._build()
        self.body.setVisible(False)  # 默认收起，不占中间层表格高度
        self.toggled.connect(self._on_toggled)
        self._fill(openapi.load_credentials())
        self._sync_title()

    # ==================== 界面 ====================

    def _build(self) -> None:
        self.body = QWidget(self)
        grid = QGridLayout(self.body)
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        self.client_id_edit = QLineEdit()
        self.client_id_edit.setPlaceholderText("Client-Id / APPid")
        self.client_id_edit.setToolTip("腾讯文档开放平台应用的 Client-Id")
        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("Access-Token")
        self.token_edit.setToolTip("OAuth2.0 的 Access-Token，约 30 天过期；到期后重新获取")
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.open_id_edit = QLineEdit()
        self.open_id_edit.setPlaceholderText("Open-Id")
        self.open_id_edit.setToolTip("OAuth2.0 授权返回的 Open-Id")
        self.show_token_cb = QCheckBox("显示")
        self.show_token_cb.toggled.connect(
            lambda on: self.token_edit.setEchoMode(
                QLineEdit.Normal if on else QLineEdit.Password))
        self.save_btn = QPushButton("💾 保存凭证")
        self.save_btn.setToolTip("保存到 Cookie 管理器的加密文件（本机可解密）")
        self.save_btn.clicked.connect(self.save_credentials)
        self.clear_btn = QPushButton("清除")
        self.clear_btn.setToolTip("清除本机保存的官方 OpenAPI 凭证")
        self.clear_btn.clicked.connect(self.clear_credentials)
        self.status = QLabel("")
        self.status.setStyleSheet("color: #888;")

        grid.addWidget(QLabel("Client-Id"), 0, 0)
        grid.addWidget(self.client_id_edit, 0, 1)
        grid.addWidget(QLabel("Access-Token"), 0, 2)
        grid.addWidget(self.token_edit, 0, 3)
        grid.addWidget(self.show_token_cb, 0, 4)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(6)
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.clear_btn)
        buttons.addStretch(1)
        grid.addWidget(QLabel("Open-Id"), 1, 0)
        grid.addWidget(self.open_id_edit, 1, 1)
        grid.addLayout(buttons, 1, 2, 1, 2)
        grid.addWidget(self.status, 1, 4)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 8)
        layout.addWidget(self.body)

    def _on_toggled(self, checked: bool) -> None:
        self.body.setVisible(checked)
        if checked:
            self.status.setText(self.status.text() or "三个 ID 加密保存在本机 Cookie 管理器")

    def _sync_title(self) -> None:
        saved = bool(self.token_edit.text().strip())
        self.setTitle("🔐 官方 OpenAPI 凭证（已保存 · 点击展开）" if saved
                      else "🔐 官方 OpenAPI 凭证（未填写 · 点击展开）")

    # ==================== 凭证读写 ====================

    def _fill(self, creds: dict) -> None:
        self.client_id_edit.setText(str(creds.get("client_id") or ""))
        self.token_edit.setText(str(creds.get("access_token") or ""))
        self.open_id_edit.setText(str(creds.get("open_id") or ""))
        self.status.setText("已载入凭证" if creds.get("access_token") else "")

    def values(self) -> tuple[str, str, str]:
        return (self.client_id_edit.text().strip(), self.token_edit.text().strip(),
                self.open_id_edit.text().strip())

    def reload(self) -> None:
        """从管理器重新读取，用于页面刷新或别处改过凭据后同步。"""
        self._fill(openapi.load_credentials())
        self._sync_title()

    def save_credentials(self) -> None:
        client_id, token, open_id = self.values()
        if not (client_id and token and open_id):
            QMessageBox.warning(self, "凭证不完整", "请填写 Client-Id、Access-Token、Open-Id")
            return
        try:
            openapi.save_credentials(client_id, token, open_id)
        except Exception as exc:  # noqa: BLE001 - 保存失败要原样告诉用户
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.status.setText("已加密保存到本机")
        self._sync_title()
        self._log("凭证已加密保存；新的 OpenAPI 调用会使用它")

    def clear_credentials(self) -> None:
        openapi.clear_credentials()
        self.client_id_edit.clear()
        self.token_edit.clear()
        self.open_id_edit.clear()
        self.status.setText("已清除")
        self._sync_title()
        self._log("已清除本机保存的官方 OpenAPI 凭证")
