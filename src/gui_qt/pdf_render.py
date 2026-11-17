"""PDF 渲染（GUI 层）：QTextDocument + QPdfWriter，必须在 Qt 主线程调用。"""

from __future__ import annotations


def render_pdf(html: str, out_path: str) -> str:
    from PySide6.QtCore import QMarginsF, QSizeF
    from PySide6.QtGui import QFont, QPageLayout, QPageSize, QPdfWriter, QTextDocument

    doc = QTextDocument()
    doc.setDefaultFont(QFont("Microsoft YaHei", 11))
    doc.setHtml(html)

    writer = QPdfWriter(out_path)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(16, 14, 14, 14), QPageLayout.Unit.Millimeter)
    writer.setResolution(96)
    doc.setPageSize(QSizeF(writer.width(), writer.height()))
    doc.print_(writer)
    return out_path
