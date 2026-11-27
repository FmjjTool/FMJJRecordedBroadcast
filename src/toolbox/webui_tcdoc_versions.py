"""Web 采集版本用例：Qt-free、只读、与桌面共用版本文件与校验。"""

from toolbox.tcdoc import version_service


def overview(doc_key: str) -> dict:
    return {"versions": version_service.list_versions(doc_key)}


def open_version(doc_key: str, version_id: str) -> dict:
    return version_service.read_version(doc_key, version_id)


def image(doc_key: str, version_id: str, asset_id: str) -> tuple[bytes, str]:
    return version_service.read_asset(doc_key, version_id, asset_id)
