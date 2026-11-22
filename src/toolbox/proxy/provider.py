"""提供商配置：本地 YAML 或订阅链接 → 生成内核可用的 runtime.yaml。

「提供商」由用户自己提供（机场订阅、自己写的 Clash/Mihomo 配置都算）。
本模块只做两件事：
1. 把用户给的配置收进 config/proxy/，并校验确实是合法 YAML 映射；
2. 在它基础上覆盖本程序必须的字段（混合端口 / 控制器 / 密钥 / 关闭 TUN），
   保证内核只服务本进程、不接管系统网络。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from . import settings


class ProviderError(RuntimeError):
    """提供商不可用：文案直接面向用户，不含凭据与内部路径。"""


def _load_yaml(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ProviderError(f"读取配置文件失败：{exc.strerror or '文件不可读'}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"第 {mark.line + 1} 行" if mark is not None else "格式"
        raise ProviderError(f"配置不是合法 YAML（{where}有语法错误）") from exc
    if not isinstance(data, dict):
        raise ProviderError("配置内容不是 YAML 映射，无法识别节点")
    return data


def analyze(data: dict) -> dict:
    """统计节点/策略组/提供商数量；全是空则视为无节点。"""
    proxies = [item for item in (data.get("proxies") or [])
               if isinstance(item, dict) and item.get("name")]
    groups = [item for item in (data.get("proxy-groups") or [])
              if isinstance(item, dict) and item.get("name")]
    providers = data.get("proxy-providers") or {}
    if not isinstance(providers, dict):
        providers = {}
    return {
        "nodes": len(proxies),
        "groups": len(groups),
        "providers": len(providers),
        "has_nodes": bool(proxies or providers),
    }


def _selector_group(data: dict) -> str:
    """复用配置里已有的 Selector 组（规则可能正指向它），没有就建一个包全部节点。"""
    groups = data.get("proxy-groups") or []
    for group in groups:
        if not isinstance(group, dict):
            continue
        if str(group.get("type") or "").lower() in ("select", "selector"):
            name = str(group.get("name") or "")
            if name:
                return name
    proxies = [str(item.get("name")) for item in (data.get("proxies") or [])
               if isinstance(item, dict) and item.get("name")]
    providers = list((data.get("proxy-providers") or {}).keys())
    if not proxies and not providers:
        raise ProviderError("配置里没有可用节点（proxies / proxy-providers 都为空）")
    group = {"name": settings.GROUP_NAME, "type": "select"}
    if proxies:
        group["proxies"] = proxies
    if providers:
        group["use"] = [str(name) for name in providers]
    groups = [group] + [g for g in groups if isinstance(g, dict)]
    data["proxy-groups"] = groups
    if not data.get("rules"):
        data["rules"] = [f"MATCH,{settings.GROUP_NAME}"]
    return settings.GROUP_NAME


def _subscription_config(cfg: dict) -> tuple[dict, str]:
    url = str(cfg.get("provider_url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ProviderError("订阅链接需要以 http:// 或 https:// 开头")
    provider_key = "provider"
    target = str(settings.providers_dir() / "subscription.yaml")
    data = {
        "proxy-providers": {
            provider_key: {
                "type": "http",
                "url": url,
                "path": target,
                "interval": 3600,
                "health-check": {
                    "enable": True,
                    "url": settings.DEFAULT_TEST_URL,
                    "interval": 300,
                },
            }
        },
        "proxy-groups": [{
            "name": settings.GROUP_NAME,
            "type": "select",
            "use": [provider_key],
        }],
        "rules": [f"MATCH,{settings.GROUP_NAME}"],
    }
    return data, settings.GROUP_NAME


def _local_config(cfg: dict) -> tuple[dict, str]:
    path = settings.provider_path()
    if not path.is_file():
        raise ProviderError("还没有导入提供商配置：请选择本地 YAML 或填写订阅链接")
    data = _load_yaml(path)
    info = analyze(data)
    if not info["has_nodes"]:
        raise ProviderError("配置里没有可用节点（proxies / proxy-providers 都为空）")
    return data, _selector_group(data)


def build_runtime(cfg: dict | None = None) -> tuple[Path, str]:
    """写 runtime.yaml；返回（配置路径, 用于切换节点的策略组名）。"""
    cfg = cfg or settings.load()
    if str(cfg.get("provider_kind")) == "url":
        data, group = _subscription_config(cfg)
    else:
        data, group = _local_config(cfg)

    # 程序内代理只监听本机回环端口，并关闭 TUN：不接管系统网络。
    for key in ("port", "socks-port", "redir-port", "tproxy-port"):
        data.pop(key, None)
    tun = data.get("tun")
    tun = dict(tun) if isinstance(tun, dict) else {}
    tun["enable"] = False
    data["tun"] = tun
    data["mixed-port"] = int(cfg["mixed_port"])
    data["allow-lan"] = False
    data["bind-address"] = "127.0.0.1"
    data["mode"] = str(cfg.get("mode") or "rule")
    data["log-level"] = "info"
    data["external-controller"] = f"127.0.0.1:{int(cfg['api_port'])}"
    data["secret"] = str(cfg["secret"])
    data.setdefault("ipv6", False)

    path = settings.runtime_path()
    try:
        path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    except OSError as exc:
        raise ProviderError(f"写入运行配置失败：{exc.strerror or '磁盘不可写'}") from exc
    cfg["group"] = group
    settings.save(cfg)
    return path, group


def import_local(source: str | Path, cfg: dict | None = None) -> dict:
    """把用户选的 YAML 复制进 config/proxy/provider.yaml 并校验。"""
    src = Path(source)
    if not src.is_file():
        raise ProviderError("选择的文件不存在")
    if src.suffix.lower() not in (".yaml", ".yml", ".txt"):
        raise ProviderError("请选择 YAML 配置文件（.yaml / .yml）")
    if src.stat().st_size > 8 * 1024 * 1024:
        raise ProviderError("配置文件超过 8MB，请确认选对了文件")
    data = _load_yaml(src)
    info = analyze(data)
    if not info["has_nodes"]:
        raise ProviderError("配置里没有可用节点（proxies / proxy-providers 都为空）")
    target = settings.provider_path()
    try:
        if src.resolve() != target.resolve():
            shutil.copyfile(src, target)
    except OSError as exc:
        raise ProviderError(f"保存配置失败：{exc.strerror or '磁盘不可写'}") from exc
    cfg = settings.load() if cfg is None else cfg
    cfg.update({"provider_kind": "file", "provider_url": ""})
    settings.save(cfg)
    return info


def set_subscription(url: str, cfg: dict | None = None) -> dict:
    url = str(url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ProviderError("订阅链接需要以 http:// 或 https:// 开头")
    cfg = settings.load() if cfg is None else cfg
    cfg.update({"provider_kind": "url", "provider_url": url})
    settings.save(cfg)
    return {"kind": "url", "url": url}


def summary(cfg: dict | None = None) -> dict:
    """页面顶部的提供商概要；失败只返回可读状态，不抛给 UI。"""
    cfg = cfg or settings.load()
    kind = str(cfg.get("provider_kind") or "file")
    if kind == "url":
        url = str(cfg.get("provider_url") or "")
        return {"kind": "url", "ready": bool(url), "url": url,
                "text": f"订阅链接：{url}" if url else "未填写订阅链接"}
    path = settings.provider_path()
    if not path.is_file():
        return {"kind": "file", "ready": False, "path": "",
                "text": "未导入本地配置"}
    try:
        info = analyze(_load_yaml(path))
    except ProviderError as exc:
        return {"kind": "file", "ready": False, "path": str(path), "text": str(exc)}
    parts = []
    if info["nodes"]:
        parts.append(f"{info['nodes']} 个节点")
    if info["groups"]:
        parts.append(f"{info['groups']} 个策略组")
    if info["providers"]:
        parts.append(f"{info['providers']} 个提供商")
    return {"kind": "file", "ready": info["has_nodes"], "path": str(path),
            "text": "本地配置：" + ("、".join(parts) if parts else "空")}