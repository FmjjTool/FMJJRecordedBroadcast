"""歌曲合集创建命令行（无界面）。

用法示例：
  python main.py --music-playlist --target netease,qq --name "封茗囧菌歌曲合集" \
      --from-playlist "https://music.163.com/playlist?id=123"
  python main.py --music-playlist --target all --name "封茗囧菌歌曲合集" \
      --from-file 歌曲清单.xlsx
  # 加 --apply --yes 才会真正创建歌单并加歌；默认只预览。

来源优先级：--from-playlist 读取现有歌单，--from-file 读取 txt/json/csv/xlsx。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from music import playlist_service as service
from music.playlist_service import PlaylistWriteError


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.capabilities:
        for row in service.capabilities():
            status = "不支持" if not row["supports_create"] else (
                "实验" if row["experimental"] else ("已核对" if row["verified"] else "待实测")
            )
            print(f"{row['name']}（{row['platform']}）：{status}")
        return 0

    try:
        sources, source_label = _load_sources(args)
    except ValueError as exc:
        print(f"读取歌曲清单失败：{exc}")
        return 1
    if args.limit and args.limit > 0:
        sources = sources[:args.limit]
    if not sources:
        print("歌曲清单为空，未生成任何计划。")
        return 1

    targets = _resolve_targets(args.target)
    if not targets:
        print("没有可执行的目标平台。")
        return 1

    print(f"来源：{source_label}，共 {len(sources)} 首")
    plans = []
    for platform in targets:
        print(f"\n正在匹配 {service.PLATFORM_NAMES.get(platform, platform)}…")
        try:
            plan = service.build_plan(
                platform, sources, args.name,
                min_score=args.min_score,
                uncertain_score=args.min_score - 0.15,
                search_limit=args.search_limit,
                search_interval=args.search_interval,
                progress=_print_match_progress,
            )
        except PlaylistWriteError as exc:
            print(f"  跳过：{exc}")
            continue
        plans.append(plan)

    if not plans:
        print("没有生成任何平台计划。")
        return 1

    for plan in plans:
        _print_plan(plan, show=args.show)

    report_dir = Path(args.report_dir) if args.report_dir else _default_report_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    preview_path = report_dir / f"preview_{stamp}.json"
    _write_report(preview_path, {
        "mode": "preview",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": source_label,
        "plans": [plan.to_dict() for plan in plans],
    })
    print(f"\n预览报告：{preview_path}")

    if not args.apply:
        print("当前为预览模式，未创建任何歌单。确认无误后追加 --apply --yes 执行。")
        return 0

    if not args.yes and not _confirm():
        print("已取消，未创建任何歌单。")
        return 1

    results = []
    for plan in plans:
        print(f"\n开始写入 {plan.platform_name}：{plan.playlist_name}")
        try:
            cookie = service.get_login_jar(plan.platform)
        except PlaylistWriteError as exc:
            print(f"  跳过：{exc}")
            results.append({"platform": plan.platform, "playlist_id": "", "stopped": str(exc)})
            continue
        journal_path = report_dir / f"journal_{plan.platform}.json"
        resume = None
        if args.resume:
            journal = service.load_journal(journal_path)
            if journal.get("playlist_name") == plan.playlist_name and journal.get("playlist_id"):
                resume = journal
                print(f"  使用断点记录：已添加 {len(resume.get('added') or [])} 首")
        try:
            result = service.apply_plan(
                plan, cookie,
                include_uncertain=args.include_uncertain,
                interval=args.add_interval,
                journal_path=journal_path,
                resume=resume,
                on_progress=_print_add_progress,
            )
        except PlaylistWriteError as exc:
            print(f"  失败：{exc}")
            results.append({"platform": plan.platform, "playlist_id": "", "stopped": str(exc)})
            continue
        results.append(result.to_dict())
        print(f"  歌单已创建/继续：{result.playlist_id or '未返回 ID'}")
        print(f"  已添加 {len(result.added)} 首，失败 {len(result.failed)} 首，"
              f"跳过 {len(result.skipped)} 首")
        if result.stopped:
            print(f"  已停止：{result.stopped}")

    result_path = report_dir / f"result_{stamp}.json"
    _write_report(result_path, {
        "mode": "apply",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": source_label,
        "results": results,
    })
    print(f"\n执行报告：{result_path}")
    failed = any(item.get("stopped") or item.get("failed") for item in results)
    return 1 if failed else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python main.py --music-playlist",
        description="创建歌曲合集：默认只预览匹配结果，--apply --yes 才写入平台。",
    )
    parser.add_argument("--target", default="all",
                        help="目标平台：all 或逗号分隔（netease,qq,kugou,kuwo,5sing）")
    parser.add_argument("--name", default="封茗囧菌歌曲合集", help="歌单名称")
    parser.add_argument("--from-playlist", help="来源歌单链接或 ID")
    parser.add_argument("--source-platform", help="来源平台，未填时按链接自动识别")
    parser.add_argument("--from-file", help="歌曲清单文件：txt / json / csv / xlsx")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 首（0 = 全部）")
    parser.add_argument("--min-score", type=float, default=0.72, help="匹配阈值 0-1")
    parser.add_argument("--search-limit", type=int, default=5, help="每首搜索候选数")
    parser.add_argument("--search-interval", type=float, default=0.35, help="搜索间隔秒")
    parser.add_argument("--add-interval", type=float, default=0.8, help="每批加歌间隔秒")
    parser.add_argument("--include-uncertain", action="store_true", help="相似度不足的也加入")
    parser.add_argument("--show", type=int, default=20, help="每平台最多展示多少条明细")
    parser.add_argument("--report-dir", help="预览/执行报告目录")
    parser.add_argument("--resume", action="store_true", help="使用上次 journal 断点继续")
    parser.add_argument("--apply", action="store_true", help="确认执行写入")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认")
    parser.add_argument("--capabilities", action="store_true", help="显示各平台能力")
    return parser


def _load_sources(args) -> tuple[list[service.SourceSong], str]:
    has_playlist = bool(args.from_playlist)
    has_file = bool(args.from_file)
    if has_playlist == has_file:
        raise ValueError("请只提供 --from-playlist 或 --from-file 其中一个")
    if has_file:
        sources = service.read_source_file(args.from_file)
        return sources, f"文件 {args.from_file}"
    platform = (args.source_platform or service.detect_platform(args.from_playlist)).strip().lower()
    if not platform:
        raise ValueError("无法识别来源平台，请补充 --source-platform")
    if platform not in service.SOURCE_SCRAPERS:
        raise ValueError(f"不支持的来源平台：{platform}")
    sources, name = service.read_source_playlist(platform, args.from_playlist)
    return sources, f"{service.PLATFORM_NAMES.get(platform, platform)}歌单「{name}」"


def _resolve_targets(value: str) -> list[str]:
    value = (value or "all").strip().lower()
    if value == "all":
        return service.supported_targets()
    targets = []
    for part in value.replace("，", ",").split(","):
        platform = part.strip()
        if platform and platform not in targets:
            targets.append(platform)
    return [platform for platform in targets if platform in service.WRITERS]


def _print_match_progress(index: int, total: int, source, item) -> None:
    label = item.target.song_name if item.target else "未匹配"
    print(f"  [{index}/{total}] {source.title} - {source.artist or '未知歌手'} -> {label}"
          f"（{item.status} {item.score:.2f}）")


def _print_plan(plan: service.PlatformPlan, show: int) -> None:
    counts = plan.counts()
    mark = " [实验]" if plan.writer.experimental else ""
    print(f"\n{plan.platform_name}{mark}：匹配 {counts['matched']}、"
          f"待确认 {counts['uncertain']}、未找到 {counts['missing']}、"
          f"重复 {counts['duplicate']}")
    shown = 0
    for index, item in enumerate(plan.items, 1):
        if shown >= show:
            break
        if item.status in ("matched",) and shown >= min(show, 10):
            continue
        target = item.target.song_name if item.target else "—"
        singer = item.target.singer if item.target else ""
        print(f"  {index:>3}. {item.source.title} - {item.source.artist or '未知歌手'}"
              f" -> {target} - {singer}（{item.status} {item.score:.2f}）{item.note}")
        shown += 1


def _print_add_progress(done: int, total: int, message: str) -> None:
    print(f"  [{done}/{total}] {message}")


def _confirm() -> bool:
    if not sys.stdin.isatty():
        print("当前不是交互终端，请追加 --yes 明确确认执行。")
        return False
    answer = input("确认创建并写入以上歌单？输入 y 继续：").strip().lower()
    return answer in ("y", "yes", "是")


def _default_report_dir() -> Path:
    try:
        from toolbox.config_manager import get_export_subdir
        path = get_export_subdir("music_info") / "歌曲合集"
    except Exception:
        path = Path.cwd() / "download" / "音乐信息" / "歌曲合集"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_report(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    raise SystemExit(main())
