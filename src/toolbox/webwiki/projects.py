"""资料站项目的本地运行编排（微信小程序开发工具与 Wiki 网站）。"""

from __future__ import annotations

from pathlib import Path

from toolbox.webwiki import config, deploy, devtools

MINIAPP = "miniapp"
WIKI = "wiki"
ALL = "all"
ALLOWED_SCOPES = (MINIAPP, WIKI, ALL)
PROJECT_OPTIONS = (
    {"value": MINIAPP, "label": "仅微信小程序",
     "description": "只在本机微信开发者工具打开小程序工程；不会上传或发布"},
    {"value": WIKI, "label": "仅 Wiki 网站",
     "description": "只启动 Wiki 网页和 API 服务"},
    {"value": ALL, "label": "全部（小程序 + Wiki 网站）",
     "description": "打开小程序工程，并启动 Wiki 网页和 API 服务"},
)


class ProjectStartError(RuntimeError):
    """所选项目部分或全部启动失败，消息保留已成功的项目。"""

    def __init__(self, completed: list[str], errors: list[str]):
        self.completed = tuple(completed)
        self.errors = tuple(errors)
        prefix = "已完成：" + "、".join(completed) + "；" if completed else ""
        super().__init__(prefix + "未能启动：" + "；".join(errors))


def start_selected(scope: str, root: str | Path | None = None, log=print,
                   service=None, open_miniapp=None) -> str:
    """打开小程序开发工程、启动 Wiki 网站，或依次执行两项。"""
    selected = str(scope or "").strip()
    if selected not in ALLOWED_SCOPES:
        raise ValueError("运行范围无效，请重新选择项目。")

    base = Path(root or config.root_dir())
    start_miniapp = selected in (MINIAPP, ALL)
    start_wiki = selected in (WIKI, ALL)
    completed: list[str] = []
    errors: list[str] = []

    if start_miniapp:
        try:
            opener = open_miniapp or devtools.open_project
            message = opener(config.miniapp_dir(base))
            log(message)
            completed.append("微信小程序")
        except Exception as exc:  # noqa: BLE001 - 继续尝试另一个所选项目
            errors.append(f"微信小程序：{exc}")
            log(errors[-1])

    if start_wiki:
        try:
            try:
                deploy.warm_covers(base, log)
            except Exception as exc:  # noqa: BLE001 - 预热失败不影响服务启动
                log(f"封面预热跳过：{exc}")
            if service is None:
                from toolbox.webwiki.server import SiteService

                service = SiteService(base)
            service.start(log)
            completed.append("Wiki 网站（网页 + API）")
        except Exception as exc:  # noqa: BLE001 - 保留小程序启动结果
            errors.append(f"Wiki 网站（网页 + API）：{exc}")
            log(errors[-1])

    if errors:
        raise ProjectStartError(completed, errors)

    if start_miniapp and start_wiki:
        return "已打开微信小程序开发工程；已启动 Wiki 网站（网页 + API）"
    if start_miniapp:
        return "已在微信开发者工具中打开小程序工程（未上传或发布）"
    return "已启动 Wiki 网站（网页 + API）"
