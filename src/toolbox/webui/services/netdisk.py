"""网络库 WebUI 适配（L2）：映射增删改、立即备份、云端浏览与下载任务。"""

from __future__ import annotations

from toolbox import webui_netdisk
from toolbox.webui.jobs import JobCancelled


class NetdiskService:
    def __init__(self, jobs=None):
        self.jobs = jobs

    def overview(self) -> dict:
        return webui_netdisk.overview()

    def save_mapping(self, payload: dict) -> dict:
        return webui_netdisk.save_mapping(payload)

    def set_mapping_enabled(self, payload: dict) -> dict:
        return webui_netdisk.set_mapping_enabled(payload)

    def delete_mapping(self, payload: dict) -> dict:
        return webui_netdisk.delete_mapping(payload)

    def start_backup(self) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        from toolbox.quark import config as qconfig
        enabled = [item for item in qconfig.load_mappings()
                   if item.enabled and item.local_dir]
        if not enabled:
            raise ValueError("没有启用的备份目录，请先新增或启用一条映射")
        job = self.jobs.submit(
            "netdisk_backup",
            lambda task: webui_netdisk.backup(
                on_progress=self._progress(task), on_log=task.log,
                is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}

    def start_cloud_list(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        data = webui_netdisk.ensure_cloud_list(dict(payload))
        job = self.jobs.submit(
            "netdisk_cloud_list",
            lambda task: webui_netdisk.cloud_list(data["provider"], data["fid"]))
        return {"job": job.id}

    def start_cloud_download(self, payload: dict) -> dict:
        if self.jobs is None:
            raise ValueError("任务服务不可用")
        data = webui_netdisk.ensure_cloud_download(dict(payload))
        job = self.jobs.submit(
            "netdisk_download",
            lambda task: webui_netdisk.cloud_download(
                dict(data), on_progress=self._progress(task),
                on_log=task.log,
                is_cancelled=lambda: bool(task.cancel_requested)))
        return {"job": job.id}

    @staticmethod
    def _progress(task):
        def progress(fraction, text) -> None:
            task.update(str(text), fraction)
        return progress
