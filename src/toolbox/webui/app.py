"""WebUI 应用装配：路由与服务实例。"""

from __future__ import annotations

from toolbox.webui.errors import NotFound
from toolbox.webui.jobs import JobManager
from toolbox.webui.server import (AttachmentResponse, BytesResponse,
                                  FileResponse, RangeResponse, WebUIServer)
from toolbox.webui.services.assets import AssetService
from toolbox.webui.services.artifacts import ArtifactService
from toolbox.webui.services.collected import CollectedService
from toolbox.webui.services.cover import CoverService
from toolbox.webui.services.douyin_index import DouyinIndexService
from toolbox.webui.services.fangroups import FanGroupService
from toolbox.webui.services.info_accounts import InfoAccountsService
from toolbox.webui.services.image_sorter import ImageSorterService
from toolbox.webui.services.livestream_summary import LivestreamSummaryService
from toolbox.webui.services.localwiki import LocalWikiService
from toolbox.webui.services.music_playlist import MusicPlaylistService
from toolbox.webui.services.music_download import MusicDownloadService
from toolbox.webui.services.music_tag import MusicTagService
from toolbox.webui.services.performance import PerformanceService
from toolbox.webui.services.rename_batch import RenameBatchService
from toolbox.webui.services.file_time import FileTimeService
from toolbox.webui.services.tickets import TicketsService
from toolbox.webui.services.netdisk import NetdiskService
from toolbox.webui.services.publish import PublishService
from toolbox.webui.services.shopping import ShoppingService
from toolbox.webui.services.modules import ModuleService
from toolbox.webui.services.offline import OfflineService
from toolbox.webui.services.record import RecordService
from toolbox.webui.services.tcdoc import TCDocService
from toolbox.webui.services.tools import ToolService
from toolbox.webui.services.trackers import TrackerService
from toolbox.webui.services.weibo_chaohua import WeiboChaohuaService
from version import APP_VERSION


def build_app(token: str, host: str = "127.0.0.1", port: int = 0) -> WebUIServer:
    jobs = JobManager()
    collected = CollectedService()
    cover = CoverService(jobs)
    assets = AssetService(jobs)
    record = RecordService(jobs)
    modules = ModuleService()
    offline = OfflineService(jobs)
    tool_service = ToolService(jobs)
    tcdoc = TCDocService(jobs)
    trackers = TrackerService(jobs)
    fangroups = FanGroupService(jobs)
    douyin_index = DouyinIndexService(jobs)
    chaohua = WeiboChaohuaService(jobs)
    info_accounts = InfoAccountsService(jobs)
    artifacts = ArtifactService(jobs)
    netdisk = NetdiskService(jobs)
    publish = PublishService(jobs)
    shopping = ShoppingService(jobs)
    localwiki = LocalWikiService(jobs)
    livestream_summary = LivestreamSummaryService(jobs)
    image_sorter = ImageSorterService()
    rename_batch = RenameBatchService()
    file_time = FileTimeService()
    tickets = TicketsService(jobs)
    music_playlist = MusicPlaylistService(jobs)
    music_download = MusicDownloadService(jobs)
    music_tag = MusicTagService(jobs)
    performance = PerformanceService()
    server = WebUIServer(token, host=host, port=port, on_stop=jobs.close)

    server.get("/api/health", lambda req: {
        "ok": True, "name": "录播工具箱 WebUI", "version": APP_VERSION})

    server.get("/api/cover/overview", lambda req: cover.overview())
    server.post("/api/cover/preview", lambda req: cover.preview(req.json))
    server.post("/api/cover/generate", lambda req: cover.start_generate(req.json))

    server.get("/api/collected/overview", lambda req: collected.overview())
    server.get("/api/collected/items", lambda req: collected.items(
        req.q("platform"), req.q_int("page", 1, 1),
        req.q_int("page_size", 30, 1, 200), req.q("q")))
    server.get("/api/collected/keys", lambda req: collected.keys(
        req.q("platform"), req.q("q")))

    def start_export(req):
        payload = dict(req.json)
        job = jobs.submit("collected_export",
                          lambda task: collected.export_job(task, payload))
        return {"job": job.id}
    server.post("/api/collected/export", start_export)

    def start_download(req):
        payload = dict(req.json)
        job = jobs.submit("collected_download",
                          lambda task: collected.download_job(task, payload))
        return {"job": job.id}
    server.post("/api/collected/download", start_download)

    server.get("/api/assets/summary", lambda req: assets.summary())
    server.get("/api/assets/db/tables", lambda req: assets.db_tables())
    server.get("/api/assets/db/rows", lambda req: assets.db_rows(
        req.q("platform"), req.q_int("page", 1, 1),
        req.q_int("page_size", 50, 1, 200), req.q("q"), req.q("author"),
        req.q("order_by"), _truthy(req.q("ascending"))))
    server.post("/api/assets/db/delete", lambda req: assets.db_delete(req.json))
    server.get("/api/assets/local/groups",
               lambda req: assets.local_groups(
                   req.q("cat"), req.q_int("page", 1, 1),
                   req.q_int("page_size", 40, 1, 100)))

    def preview_local(req):
        data, mime = assets.preview_local(
            req.q("cat"), req.q("dir"), req.q("name"))
        return BytesResponse(data, mime)
    server.get("/api/assets/local/preview", preview_local)

    def thumbnail_local(req):
        data, mime = assets.thumbnail_local(
            req.q("cat"), req.q("dir"), req.q("name"))
        return BytesResponse(data, mime)
    server.get("/api/assets/local/thumb", thumbnail_local)

    server.get("/api/assets/entry",
               lambda req: assets.entry_detail(req.q("path")))
    server.get("/api/assets/local/stat",
               lambda req: assets.local_stat(req.q("cat"), req.q("dir"),
                                             req.q("name")))

    def start_local_scan(req):
        payload = dict(req.json or {})
        return assets.start_local_scan(str(payload.get("cat") or ""))
    server.post("/api/assets/local/scan", start_local_scan)

    server.get("/api/assets/entries", lambda req: assets.entries(
        req.q_int("page", 1, 1), req.q_int("page_size", 20, 1, 100),
        req.q("q"), req.q("platform")))

    server.get("/api/assets/entries-status", lambda req: assets.entries_status(
        req.q_int("page", 1, 1), req.q_int("page_size", 20, 1, 100),
        req.q("q"), req.q("platform"), req.q("status")))
    server.post("/api/assets/entries/refresh", lambda req: assets.refresh_entries(req.json))

    def start_assets_download(req):
        return assets.start_download(dict(req.json or {}))
    server.post("/api/assets/download", start_assets_download)

    def set_asset_no_dl(req):
        return assets.set_no_dl(dict(req.json or {}))
    server.post("/api/assets/no-dl", set_asset_no_dl)

    server.get("/api/assets/offline/events", lambda req: offline.overview())

    def start_offline_search(req):
        return offline.start_search(dict(req.json or {}))
    server.post("/api/assets/offline/search", start_offline_search)

    server.get("/api/record/overview", lambda req: record.overview())

    server.get("/api/record/bridge", lambda req: record.bridge_status())

    def record_control(req):
        return record.control(dict(req.json or {}))
    server.post("/api/record/control", record_control)

    def record_inspect(req):
        return record.inspect(dict(req.json or {}))
    server.post("/api/record/inspect", record_inspect)

    def record_add(req):
        return record.add_anchor(dict(req.json or {}))
    server.post("/api/record/add", record_add)

    def record_import(req):
        return record.import_anchors(dict(req.json or {}))
    server.post("/api/record/import", record_import)

    server.get("/api/record/repair/sources",
               lambda req: record.repair_sources(req.q("platform")))
    server.post("/api/record/repair",
                lambda req: record.start_repair(dict(req.json or {})))

    server.get("/api/modules", lambda req: modules.list())
    server.get_re(r"/api/modules/(?P<key>[a-z_]+)",
                  lambda req, key: modules.detail(key))
    server.get("/api/tasks", lambda req: modules.tasks())
    server.get("/api/tasks/schedule",
               lambda req: modules.task_schedule(req.q("id")))

    def install_task(req):
        return modules.install_task(dict(req.json or {}))
    server.post("/api/tasks/install", install_task)

    def uninstall_task(req):
        return modules.uninstall_task(dict(req.json or {}))
    server.post("/api/tasks/uninstall", uninstall_task)

    server.get("/api/system", lambda req: modules.system())
    server.get("/api/system/settings", lambda req: modules.system_settings())

    def save_system_dirs(req):
        return modules.save_system_dirs(dict(req.json or {}))
    server.post("/api/system/dirs", save_system_dirs)

    def save_system_theme(req):
        return modules.save_system_theme(dict(req.json or {}))
    server.post("/api/system/theme", save_system_theme)

    server.get("/api/system/deps", lambda req: modules.dependencies())

    def start_tracker_collect(req):
        return trackers.start_collect(dict(req.json or {}))
    server.post("/api/trackers/collect", start_tracker_collect)

    def export_tracker_png(req):
        data = trackers.export_png(dict(req.json or {}))
        return AttachmentResponse(data["data"], data["filename"], "image/png")
    server.post("/api/trackers/export", export_tracker_png)

    server.get("/api/trackers/groups",
               lambda req: trackers.groups(req.q("kind")))

    def export_tracker_group(req):
        data = trackers.export_group_png(dict(req.json or {}))
        return AttachmentResponse(data["data"], data["filename"], "image/png")
    server.post("/api/trackers/group-export", export_tracker_group)

    server.get("/api/trackers/music", lambda req: trackers.music())
    server.get("/api/trackers/media", lambda req: trackers.media())
    server.get("/api/trackers/music/history", lambda req: trackers.music_history(
        req.q("name"), req.q_int("limit", 50, 1, 200)))
    server.get("/api/trackers/media/history", lambda req: trackers.media_history(
        req.q("name"), req.q_int("limit", 50, 1, 200)))

    server.get("/api/fangroups", lambda req: fangroups.overview())
    server.get("/api/fangroups/history", lambda req: fangroups.history(
        req.q("no"), req.q_int("limit", 200, 1, 500)))

    def fangroup_avatar(req):
        path = fangroups.avatar(req.q("no"))
        if path is None:
            raise NotFound("群头像不存在")
        suffix = path.suffix.lower()
        mime = {".png": "image/png", ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg", ".webp": "image/webp",
                ".gif": "image/gif"}.get(suffix, "application/octet-stream")
        return BytesResponse(path.read_bytes(), mime)
    server.get("/api/fangroups/avatar", fangroup_avatar)

    def start_fangroups_query(req):
        return fangroups.start_query(dict(req.json or {}))
    server.post("/api/fangroups/query", start_fangroups_query)

    def export_qq_groups(req):
        data = fangroups.qq_export()
        return AttachmentResponse(data["data"], data["filename"],
                                  "application/vnd.openxmlformats-officedocument"
                                  ".spreadsheetml.sheet")
    server.post("/api/tools/fangroups/qq/export", export_qq_groups)

    def start_qq_query(req):
        return fangroups.start_qq_query(dict(req.json or {}))
    server.post("/api/tools/fangroups/qq/query", start_qq_query)

    def start_qq_avatars(req):
        return fangroups.start_qq_avatars(dict(req.json or {}))
    server.post("/api/tools/fangroups/qq/avatars", start_qq_avatars)

    def start_qq_refresh(req):
        return fangroups.start_qq_refresh(dict(req.json or {}))
    server.post("/api/tools/fangroups/qq/refresh", start_qq_refresh)

    def remove_qq_subscription(req):
        return fangroups.qq_remove(dict(req.json or {}))
    server.post("/api/tools/fangroups/qq/remove", remove_qq_subscription)


    server.get("/api/douyin-index", lambda req: douyin_index.overview())
    server.get("/api/douyin-index/report", lambda req: douyin_index.report(
        req.q("name")))

    def search_douyin_index(req):
        return douyin_index.search(dict(req.json or {}))
    server.post("/api/douyin-index/search", search_douyin_index)

    def start_douyin_index_fetch(req):
        """复用已确认的抓取/资产回写任务；确认参数由服务统一校验。"""
        return douyin_index.start_fetch(dict(req.json or {}))
    server.post("/api/douyin-index/fetch", start_douyin_index_fetch)

    server.get("/api/weibo-chaohua", lambda req: chaohua.overview())
    server.get("/api/weibo-chaohua/analysis", lambda req: chaohua.analysis(
        req.q("topic_id")))

    def start_chaohua_parse(req):
        return chaohua.start_parse(dict(req.json or {}))
    server.post("/api/tools/weibo-chaohua/parse", start_chaohua_parse)

    def start_chaohua_collect(req):
        return chaohua.start_collect(dict(req.json or {}))
    server.post("/api/tools/weibo-chaohua/collect", start_chaohua_collect)

    def export_chaohua(req):
        data = chaohua.export(dict(req.json or {}))
        return AttachmentResponse(data["data"], data["filename"],
                                  "application/vnd.openxmlformats-officedocument"
                                  ".spreadsheetml.sheet")
    server.post("/api/tools/weibo-chaohua/export", export_chaohua)

    server.get("/api/info-accounts", lambda req: info_accounts.overview())

    def save_info_accounts_settings(req):
        return info_accounts.save_settings(dict(req.json or {}))
    server.post("/api/info-accounts/settings", save_info_accounts_settings)

    def start_info_parse(req):
        return info_accounts.start_parse(dict(req.json or {}))
    server.post("/api/info-accounts/parse", start_info_parse)

    def start_info_collect(req):
        return info_accounts.start_collect(dict(req.json or {}))
    server.post("/api/info-accounts/collect", start_info_collect)

    def start_comments_fetch(req):
        return artifacts.start_comments(dict(req.json or {}))
    server.post("/api/tools/artifacts/comments", start_comments_fetch)

    def start_wechat_parse(req):
        return artifacts.start_wechat(dict(req.json or {}))
    server.post("/api/tools/artifacts/wechat", start_wechat_parse)

    def bili_emote_search(req):
        return artifacts.bili_emote_search(dict(req.json or {}))
    server.post("/api/tools/bili/emote/search", bili_emote_search)

    def start_bili_emote_download(req):
        return artifacts.start_bili_emote_download(dict(req.json or {}))
    server.post("/api/tools/bili/emote/download", start_bili_emote_download)

    def start_bili_garb_download(req):
        return artifacts.start_bili_garb_download(dict(req.json or {}))
    server.post("/api/tools/bili/garb/download", start_bili_garb_download)

    def start_bili_up_download(req):
        return artifacts.start_bili_up_download(dict(req.json or {}))
    server.post("/api/tools/bili/up/download", start_bili_up_download)

    def douyin_emoji_fetch(req):
        return artifacts.douyin_emoji_fetch(dict(req.json or {}))
    server.post("/api/tools/douyin-emoji/fetch", douyin_emoji_fetch)

    def start_douyin_emoji_download(req):
        return artifacts.start_douyin_emoji_download(dict(req.json or {}))
    server.post("/api/tools/douyin-emoji/download", start_douyin_emoji_download)

    server.get("/api/tools/artifacts", lambda req: artifacts.list(
        req.q("kind"), req.q_int("group_limit", 200, 1, 500),
        req.q_int("file_limit", 200, 1, 1000)))

    def artifact_preview(req):
        path = artifacts.file(req.q("kind"), req.q("rel"), preview=True)
        suffix = path.suffix.lower()
        mime = {".png": "image/png", ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg", ".webp": "image/webp",
                ".gif": "image/gif", ".bmp": "image/bmp"}.get(
            suffix, "application/octet-stream")
        return BytesResponse(path.read_bytes(), mime)
    server.get("/api/tools/artifacts/preview", artifact_preview)

    def artifact_download(req):
        path = artifacts.file(req.q("kind"), req.q("rel"))
        return FileResponse(path, path.name, "application/octet-stream")
    server.get("/api/tools/artifacts/file", artifact_download)

    server.get("/api/netdisk", lambda req: netdisk.overview())

    def save_netdisk_mapping(req):
        return netdisk.save_mapping(dict(req.json or {}))
    server.post("/api/netdisk/mappings", save_netdisk_mapping)

    def toggle_netdisk_mapping(req):
        return netdisk.set_mapping_enabled(dict(req.json or {}))
    server.post("/api/netdisk/mappings/enabled", toggle_netdisk_mapping)

    def delete_netdisk_mapping(req):
        return netdisk.delete_mapping(dict(req.json or {}))
    server.post("/api/netdisk/mappings/delete", delete_netdisk_mapping)

    def start_netdisk_backup(req):
        return netdisk.start_backup()
    server.post("/api/netdisk/backup", start_netdisk_backup)

    def start_netdisk_cloud_list(req):
        return netdisk.start_cloud_list(dict(req.json or {}))
    server.post("/api/netdisk/cloud/list", start_netdisk_cloud_list)

    def start_netdisk_cloud_download(req):
        return netdisk.start_cloud_download(dict(req.json or {}))
    server.post("/api/netdisk/cloud/download", start_netdisk_cloud_download)

    server.get("/api/localwiki", lambda req: localwiki.overview())

    def start_wiki_deploy(req):
        return localwiki.start_deploy(dict(req.json or {}))
    server.post("/api/tools/localwiki/deploy", start_wiki_deploy)

    def start_wiki_rebuild(req):
        return localwiki.start_rebuild(dict(req.json or {}))
    server.post("/api/tools/localwiki/rebuild", start_wiki_rebuild)

    def start_wiki_collect(req):
        return localwiki.start_collect(dict(req.json or {}))
    server.post("/api/tools/localwiki/collect", start_wiki_collect)

    server.post("/api/tools/localwiki/service/start",
                lambda req: localwiki.start_service())
    server.post("/api/tools/localwiki/service/stop",
                lambda req: localwiki.stop_service())

    def save_wiki_password(req):
        return localwiki.save_password(dict(req.json or {}))
    server.post("/api/tools/localwiki/password", save_wiki_password)

    server.get("/api/livestream-summary",
               lambda req: livestream_summary.overview())
    server.get("/api/livestream-summary/saved",
               lambda req: livestream_summary.saved_text(req.q("name")))

    def save_livestream_settings(req):
        return livestream_summary.save_settings(dict(req.json or {}))
    server.post("/api/tools/livestream-summary/settings", save_livestream_settings)

    def test_livestream_connection(req):
        return livestream_summary.test_connection()
    server.post("/api/tools/livestream-summary/test", test_livestream_connection)

    def start_livestream_summary(req):
        return livestream_summary.start_summary(dict(req.json or {}))
    server.post("/api/tools/livestream-summary/run", start_livestream_summary)

    server.get("/api/image-sorter", lambda req: image_sorter.overview())
    server.get("/api/image-sorter/scan", lambda req: image_sorter.scan(
        req.q("kind") or "all", req.q("q"),
        req.q_int("limit", 500, 1, 2000)))

    def image_sorter_preview(req):
        path = image_sorter.file(req.q("ref"), preview=True)
        suffix = path.suffix.lower()
        mime = {".png": "image/png", ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg", ".webp": "image/webp",
                ".gif": "image/gif", ".bmp": "image/bmp"}.get(
            suffix, "application/octet-stream")
        return BytesResponse(path.read_bytes(), mime)
    server.get("/api/image-sorter/preview", image_sorter_preview)

    def image_sorter_transfer(req):
        return image_sorter.transfer(dict(req.json or {}))
    server.post("/api/image-sorter/transfer", image_sorter_transfer)

    def image_sorter_delete(req):
        return image_sorter.delete(dict(req.json or {}))
    server.post("/api/image-sorter/delete", image_sorter_delete)

    def image_sorter_rename(req):
        return image_sorter.rename(dict(req.json or {}))
    server.post("/api/image-sorter/rename", image_sorter_rename)

    server.get("/api/rename-batch", lambda req: rename_batch.overview())

    def rename_batch_preview(req):
        return rename_batch.preview(dict(req.json or {}))
    server.post("/api/rename-batch/preview", rename_batch_preview)

    def rename_batch_execute(req):
        return rename_batch.execute(dict(req.json or {}))
    server.post("/api/rename-batch/execute", rename_batch_execute)

    server.get("/api/file-time", lambda req: file_time.overview())

    def file_time_preview(req):
        return file_time.preview(dict(req.json or {}))
    server.post("/api/file-time/preview", file_time_preview)

    def file_time_apply(req):
        return file_time.apply(dict(req.json or {}))
    server.post("/api/file-time/apply", file_time_apply)

    server.get("/api/tickets", lambda req: tickets.overview())

    def start_tickets_parse(req):
        return tickets.start_parse(dict(req.json or {}))
    server.post("/api/tickets/parse", start_tickets_parse)

    def start_tickets_export(req):
        return tickets.start_export(dict(req.json or {}))
    server.post("/api/tickets/export", start_tickets_export)

    server.get("/api/tools/shopping/options", lambda req: shopping.options())

    def start_shopping_parse(req):
        return shopping.start_parse(dict(req.json or {}))
    server.post("/api/tools/shopping/parse", start_shopping_parse)

    def search_xianyu_shopping(req):
        return shopping.start_xianyu_search(dict(req.json or {}))
    server.post("/api/tools/shopping/xianyu/search", search_xianyu_shopping)

    def save_xianyu_shopping(req):
        return shopping.start_xianyu_save(dict(req.json or {}))
    server.post("/api/tools/shopping/xianyu/save", save_xianyu_shopping)



    server.get("/api/music-playlist", lambda req: music_playlist.overview())
    server.get("/api/music-playlist/report",
               lambda req: music_playlist.report_text(req.q("name")))
    server.get("/api/music-playlist/platforms",
               lambda req: music_playlist.capabilities())

    def start_playlist_preview(req):
        return music_playlist.start_preview(dict(req.json or {}))
    server.post("/api/tools/music-playlist/preview", start_playlist_preview)

    def start_playlist_apply(req):
        return music_playlist.start_apply(dict(req.json or {}))
    server.post("/api/tools/music-playlist/apply", start_playlist_apply)

    server.get("/api/music-download", lambda req: music_download.overview())
    server.get("/api/music-download/qualities", lambda req: music_download.qualities())

    def start_music_search(req):
        return music_download.start_search(dict(req.json or {}))
    server.post("/api/tools/music/search", start_music_search)

    def start_music_download(req):
        return music_download.start_download(dict(req.json or {}))
    server.post("/api/tools/music/download", start_music_download)
    server.get("/api/music-tag", lambda req: music_tag.overview(
        req.q_int("limit", 120, 1, 300)))

    def tag_scan(req):
        return music_tag.scan(dict(req.json or {}))
    server.post("/api/tools/music-tag/scan", tag_scan)

    server.get("/api/music-tag/detail",
               lambda req: music_tag.detail(req.q("rel", "")))

    def start_tag_write(req):
        return music_tag.start_write(dict(req.json or {}))
    server.post("/api/tools/music-tag/write", start_tag_write)

    def start_tag_lyric(req):
        return music_tag.start_lyric_search(dict(req.json or {}))
    server.post("/api/tools/music-tag/lyric/search", start_tag_lyric)

    def start_tag_lyric_apply(req):
        return music_tag.start_lyric_apply(dict(req.json or {}))
    server.post("/api/tools/music-tag/lyric/apply", start_tag_lyric_apply)

    server.get("/api/performance", lambda req: performance.overview())
    server.post("/api/performance/settings",
                lambda req: performance.save_settings(dict(req.json or {})))

    server.get("/api/tools/organize/overview",
               lambda req: tool_service.organize_overview())
    server.get("/api/tools/organize/preview",
               lambda req: tool_service.organize_preview(
                   req.q_int("limit", 100, 1, 500)))

    def start_organize(req):
        return tool_service.start_organize(dict(req.json or {}))
    server.post("/api/tools/organize/run", start_organize)

    def save_organize_sources(req):
        return tool_service.organize_save_sources(dict(req.json or {}))
    server.post("/api/tools/organize/sources", save_organize_sources)

    server.get("/api/tools/organize/sources/scan",
               lambda req: tool_service.organize_scan_sources())

    def start_organize_migrate(req):
        return tool_service.start_organize_migrate(dict(req.json or {}))
    server.post("/api/tools/organize/sources/migrate", start_organize_migrate)

    server.get("/api/tools/rename/overview",
               lambda req: tool_service.rename_overview())
    server.get("/api/tools/rename/preview", lambda req: tool_service.rename_preview(
        req.q_int("limit", 200, 1, 500)))

    def start_rename_execute(req):
        return tool_service.start_rename_execute(dict(req.json or {}))
    server.post("/api/tools/rename/execute", start_rename_execute)

    server.get("/api/publish/overview", lambda req: publish.overview())

    def publish_precheck(req):
        return publish.precheck(dict(req.json or {}))
    server.post("/api/publish/precheck", publish_precheck)

    def start_publish_upload(req):
        return publish.start_upload(dict(req.json or {}))
    server.post("/api/publish/upload", start_publish_upload)

    server.get("/api/publish/categories", lambda req: publish.categories())

    server.get("/api/publish/templates", lambda req: publish.templates())

    def save_publish_template(req):
        return publish.save_template(dict(req.json or {}))
    server.post("/api/publish/templates", save_publish_template)

    server.get("/api/publish/history", lambda req: publish.history())

    def publish_allow_retry(req):
        return publish.allow_retry(dict(req.json or {}))
    server.post("/api/publish/history/retry", publish_allow_retry)



    server.get("/api/tools/video/overview",
               lambda req: tool_service.video_overview())
    server.get("/api/tools/video/sources", lambda req: tool_service.video_sources(
        req.q_int("limit", 200, 1, 500)))

    def start_video_convert(req):
        return tool_service.start_video_convert(dict(req.json or {}))
    server.post("/api/tools/video/convert", start_video_convert)

    def start_video_compress(req):
        return tool_service.start_video_compress(dict(req.json or {}))
    server.post("/api/tools/video/compress", start_video_compress)

    def start_video_merge(req):
        return tool_service.start_video_merge(dict(req.json or {}))
    server.post("/api/tools/video/merge", start_video_merge)

    def start_video_cut(req):
        return tool_service.start_video_cut(dict(req.json or {}))
    server.post("/api/tools/video/cut", start_video_cut)

    def start_video_snapshot(req):
        return tool_service.start_video_snapshot(dict(req.json or {}))
    server.post("/api/tools/video/snapshot", start_video_snapshot)

    server.get("/api/tools/video/tracks",
               lambda req: tool_service.video_tracks(req.q("path")))
    server.get("/api/tools/video/metadata",
               lambda req: tool_service.video_metadata(req.q("path")))
    server.get("/api/tools/video/extras", lambda req: tool_service.video_extras(
        req.q("kind"), req.q_int("limit", 200, 1, 500)))

    def start_video_tracks_extract(req):
        return tool_service.start_video_tracks_extract(dict(req.json or {}))
    server.post("/api/tools/video/tracks/extract", start_video_tracks_extract)

    def start_video_tracks_remove(req):
        return tool_service.start_video_tracks_remove(dict(req.json or {}))
    server.post("/api/tools/video/tracks/remove", start_video_tracks_remove)

    def start_video_audio_add(req):
        return tool_service.start_video_audio_add(dict(req.json or {}))
    server.post("/api/tools/video/audio/add", start_video_audio_add)

    def start_video_subtitle_add(req):
        return tool_service.start_video_subtitle_add(dict(req.json or {}))
    server.post("/api/tools/video/subtitle/add", start_video_subtitle_add)

    def start_video_metadata_write(req):
        return tool_service.start_video_metadata_write(dict(req.json or {}))
    server.post("/api/tools/video/metadata/write", start_video_metadata_write)

    def start_video_chapters_write(req):
        return tool_service.start_video_chapters_write(dict(req.json or {}))
    server.post("/api/tools/video/chapters/write", start_video_chapters_write)

    def start_video_detect(req):
        return tool_service.start_video_detect(dict(req.json or {}))
    server.post("/api/tools/video/detect", start_video_detect)

    def video_stream(req):
        data = tool_service.video_preview(req.q("path"))
        return RangeResponse(data["path"], data["content_type"])

    server.get("/api/tools/video/stream", video_stream)

    def export_video_segments(req):
        data = tool_service.video_segments_export(dict(req.json or {}))
        return AttachmentResponse(
            data["text"].encode("utf-8"), data["filename"],
            data["content_type"])

    server.post("/api/tools/video/segments/export", export_video_segments)

    def import_video_segments(req):
        return tool_service.video_segments_import(dict(req.json or {}))

    server.post("/api/tools/video/segments/import", import_video_segments)

    server.get("/api/tools/audio/overview",
               lambda req: tool_service.audio_overview())
    server.get("/api/tools/audio/sources", lambda req: tool_service.audio_sources(
        req.q_int("limit", 200, 1, 500)))

    def start_audio_extract(req):
        return tool_service.start_audio_extract(dict(req.json or {}))
    server.post("/api/tools/audio/extract", start_audio_extract)

    server.get("/api/tools/subtitle/overview",
               lambda req: tool_service.subtitle_overview())
    server.get("/api/tools/subtitle/sources",
               lambda req: tool_service.subtitle_sources(
                   req.q_int("limit", 200, 1, 500)))

    def start_subtitle_convert(req):
        return tool_service.start_subtitle_convert(dict(req.json or {}))
    server.post("/api/tools/subtitle/convert", start_subtitle_convert)

    server.get("/api/tools/calendar/days",
               lambda req: tool_service.calendar_days())
    server.get("/api/tools/calendar/day",
               lambda req: tool_service.calendar_day_files(
                   req.q("folder"), req.q_int("limit", 500, 1, 1000)))
    server.get("/api/tools/launcher/overview",
               lambda req: tool_service.launcher_overview())

    def launch_launcher(req):
        return tool_service.launcher_launch(dict(req.json or {}))
    server.post("/api/tools/launcher/launch", launch_launcher)

    server.get("/api/tools/migrate/overview",
               lambda req: tool_service.migrate_overview())
    server.get("/api/tools/migrate/scan",
               lambda req: tool_service.migrate_scan())

    def start_migrate(req):
        return tool_service.start_migrate(dict(req.json or {}))
    server.post("/api/tools/migrate/run", start_migrate)

    server.get("/api/tcdoc/overview",
               lambda req: tcdoc.overview(req.q("doc_key")))

    def start_collect(req):
        job = tcdoc.start_collect()
        return {"job": job.id}

    def start_sync(req):
        job = tcdoc.start_sync()
        return {"job": job.id}

    server.post("/api/tcdoc/collect", start_collect)
    server.post("/api/tcdoc/sync", start_sync)
    server.get("/api/tcdoc/table-rows", lambda req: tcdoc.table_rows(
        req.q("doc_key"), req.q("tab_id"), req.q_int("page", 1, 1),
        req.q_int("page_size", 50, 1, 200)))
    server.get("/api/tcdoc/snapshots", lambda req: tcdoc.snapshots(
        req.q("doc_key"), req.q_int("limit", 500, 1, 5000)))
    server.get("/api/tcdoc/revisions", lambda req: tcdoc.revisions(
        req.q("doc_key"), req.q_int("limit", 200, 1, 2000)))
    server.get("/api/tcdoc/members", lambda req: tcdoc.members(
        req.q("doc_key"), req.q_int("limit", 500, 1, 5000)))

    server.get("/api/tcdoc/body", lambda req: tcdoc.body(req.q("doc_key")))

    def tcdoc_body_fetch(req):
        if not isinstance(req.json, dict):
            raise ValueError("抓取参数格式错误，请刷新页面后重试")
        return tcdoc.start_body_fetch(dict(req.json or {}))
    server.post("/api/tcdoc/body/fetch", tcdoc_body_fetch)

    def tcdoc_body_image(req):
        data, mime = tcdoc.body_image(
            req.q("doc_key"), req.q("page_id"), req.q("image_key"))
        return BytesResponse(data, mime)
    server.get("/api/tcdoc/body/image", tcdoc_body_image)

    server.get("/api/tcdoc/writeback/status",
               lambda req: tcdoc.writeback_status())
    server.get("/api/tcdoc/writeback/preview",
               lambda req: tcdoc.writeback_preview(
                   req.q("tab"), req.q_int("days", 30, 1, 3650),
                   req.q_int("limit", 100, 1, 500)))

    def start_tcdoc_writeback(req):
        return tcdoc.start_writeback_upload(dict(req.json or {}))
    server.post("/api/tcdoc/writeback/upload", start_tcdoc_writeback)

    server.get("/api/tcdoc/edit/status", lambda req: tcdoc.edit_status())

    def tcdoc_edit_preview(req):
        return tcdoc.edit_preview(dict(req.json or {}))
    server.post("/api/tcdoc/edit/preview", tcdoc_edit_preview)

    def tcdoc_edit_update(req):
        return tcdoc.edit_update(dict(req.json or {}))
    server.post("/api/tcdoc/edit/update", tcdoc_edit_update)

    def tcdoc_edit_delete(req):
        return tcdoc.edit_delete(dict(req.json or {}))
    server.post("/api/tcdoc/edit/delete", tcdoc_edit_delete)

    server.get("/api/tcdoc/promo/status", lambda req: tcdoc.promo_status())

    def tcdoc_promo_preview(req):
        return tcdoc.promo_preview(dict(req.json or {}))
    server.post("/api/tcdoc/promo/preview", tcdoc_promo_preview)

    def tcdoc_promo_apply(req):
        return tcdoc.promo_apply(dict(req.json or {}))
    server.post("/api/tcdoc/promo/apply", tcdoc_promo_apply)

    server.get("/api/tcdoc/bridge", lambda req: tcdoc.bridge_overview())

    def tcdoc_bridge_preview(req):
        return tcdoc.bridge_preview(dict(req.json or {}))
    server.post("/api/tcdoc/bridge/preview", tcdoc_bridge_preview)

    def tcdoc_bridge_options(req):
        return tcdoc.bridge_option_preview(dict(req.json or {}))
    server.post("/api/tcdoc/bridge/options", tcdoc_bridge_options)

    def tcdoc_bridge_manual_schema(req):
        return tcdoc.bridge_manual_schema(dict(req.json or {}))
    server.post("/api/tcdoc/bridge/manual/schema", tcdoc_bridge_manual_schema)

    def tcdoc_bridge_manual_tsv(req):
        return tcdoc.bridge_manual_tsv(dict(req.json or {}))
    server.post("/api/tcdoc/bridge/manual/tsv", tcdoc_bridge_manual_tsv)

    def start_tcdoc_bridge_refresh(req):
        job = tcdoc.start_bridge_refresh()
        return {"job": job.id}
    server.post("/api/tcdoc/bridge/refresh", start_tcdoc_bridge_refresh)

    def start_tcdoc_bridge_upload(req):
        return tcdoc.start_bridge_upload(dict(req.json or {}))
    server.post("/api/tcdoc/bridge/upload", start_tcdoc_bridge_upload)

    server.get("/api/tcdoc/bridge/events", lambda req: tcdoc.bridge_events({
        "doc_key": req.q("doc_key"), "tab_id": req.q("tab_id")}))

    def start_tcdoc_bridge_playlist(req):
        return tcdoc.start_bridge_playlist(dict(req.json or {}))
    server.post("/api/tcdoc/bridge/playlist", start_tcdoc_bridge_playlist)

    def get_job(req, job_id):
        job = jobs.get(job_id)
        if job is None:
            raise NotFound("任务不存在")
        return job.public()

    def download_job(req, job_id):
        path = jobs.download(job_id)
        if path is None:
            raise NotFound("任务文件不存在")
        return FileResponse(path, path.name, "application/zip")

    def cancel_job(req, job_id):
        if not jobs.cancel(job_id):
            raise NotFound("任务不存在或已结束")
        return {"ok": True}

    server.get_re(r"/api/jobs/(?P<job_id>[0-9a-f]{12})", get_job)
    server.get_re(r"/api/jobs/(?P<job_id>[0-9a-f]{12})/download", download_job)
    server.post_re(r"/api/jobs/(?P<job_id>[0-9a-f]{12})/cancel", cancel_job)
    return server


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")
