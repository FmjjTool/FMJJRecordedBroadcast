"""微信公众号文章解析引擎。

输入文章链接（mp.weixin.qq.com/s/xxx 或 /s?__biz=... 长链），抓取并解析：
- 标题（h1#activity-name，正文在 span.js_title_inner 里）
- 公众号名（a#js_name）+ 作者（meta author / rich_media_meta_text）
- 发布时间（em#publish_time 初始为空，回退 var ct = "unix时间戳"）
- 正文（div#js_content）：data-src → src（微信图片懒加载）、视频 iframe → 占位、
  移除脚本/样式/二维码浮层
- 图片按出现顺序收集（去重），供多格式导出复用
"""
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup, Comment

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

WX_URL_RE = re.compile(r"https?://mp\.weixin\.qq\.com/\S+", re.I)


def extract_urls(text: str) -> List[str]:
    """从多行输入里提取所有公众号文章链接（自动去重、保序）。"""
    seen, out = set(), []
    for m in WX_URL_RE.finditer(text or ""):
        u = m.group(0).rstrip('），。;]')
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


@dataclass
class WechatImage:
    url: str
    fmt: str = "jpeg"          # jpeg/png/gif/webp
    data: bytes = b""          # 下载后填充
    width: int = 0             # 下载后按图片实际尺寸填充
    height: int = 0


@dataclass
class WechatArticle:
    url: str
    title: str = ""
    account: str = ""          # 公众号名
    author: str = ""           # 作者（可为空）
    publish_time: str = ""     # 2026-09-23 18:05
    content_html: str = ""     # 清洗后的正文 HTML（img 已换 src）
    images: List[WechatImage] = field(default_factory=list)

    def image_by_url(self, url: str) -> Optional[WechatImage]:
        for img in self.images:
            if img.url == url:
                return img
        return None


def _detect_fmt(url: str, data: bytes) -> str:
    """按 magic bytes 判断图片真实格式（微信可能返回 webp）。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    m = re.search(r"wx_fmt=(\w+)", url)
    return (m.group(1).lower() if m else "jpeg") or "jpeg"


def _convert_webp(data: bytes) -> Tuple[bytes, str]:
    """webp 统一转 jpeg（Word 不支持 webp）。"""
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    if im.mode in ("RGBA", "P", "LA"):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        if im.mode == "P":
            im = im.convert("RGBA")
        bg.paste(im, mask=im.split()[-1] if im.mode in ("RGBA", "LA") else None)
        im = bg
    else:
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue(), "jpeg"


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
    return s


# ==================== 解析 ====================

def fetch_article(url: str, session: Optional[requests.Session] = None) -> WechatArticle:
    """抓取并解析单篇文章，失败抛 ValueError（界面显示错误信息）。"""
    s = session or _make_session()
    resp = s.get(url, timeout=20)
    resp.raise_for_status()
    html = resp.text
    soup = BeautifulSoup(html, "html.parser")

    # 已删除 / 环境异常 等错误页
    err_title = soup.find("div", class_="weui-msg__title")
    if err_title and not soup.find(id="js_content"):
        raise ValueError(f"无法解析：{err_title.get_text(strip=True) or '页面异常（可能已删除或需验证）'}")
    if "操作频繁" in html or "环境异常" in html:
        raise ValueError("微信风控拦截（操作频繁/环境异常），请稍后再试或降低批量速度")

    art = WechatArticle(url=url)

    h1 = soup.find(id="activity-name")
    if h1:
        art.title = h1.get_text(strip=True)
    if not art.title:
        og = soup.find("meta", property="og:title")
        art.title = (og["content"].strip() if og and og.get("content") else "") or "未命名文章"

    js_name = soup.find(id="js_name")
    if js_name:
        art.account = js_name.get_text(strip=True)

    meta_author = soup.find("meta", attrs={"name": "author"})
    if meta_author and meta_author.get("content"):
        art.author = meta_author["content"].strip()

    em = soup.find(id="publish_time")
    if em:
        art.publish_time = em.get_text(strip=True)
    if not art.publish_time:
        m = re.search(r'var\s+ct\s*=\s*"?(\d{10})"?', html)
        if m:
            art.publish_time = datetime.fromtimestamp(int(m.group(1))).strftime("%Y-%m-%d %H:%M")

    content = soup.find(id="js_content")
    if content is None:
        raise ValueError("未找到正文（js_content），链接可能不是图文消息")

    _clean_content(soup, content, art)
    art.content_html = content.decode_contents()
    return art


def _clean_content(soup, content, art: WechatArticle):
    """就地清洗正文：移除噪音节点、懒加载图片转真实 src、视频/语音占位。"""
    # 注释 / 脚本 / 样式
    for node in content.find_all(string=lambda t: isinstance(t, Comment)):
        node.extract()
    for tag in content.find_all(["script", "style"]):
        tag.decompose()
    # 二维码浮层 / 账号名片 / 打赏等运营区块
    for sel in (".qr_code_pc", ".qr_code_pc_outer", "mp-common-profile",
                "mp-common-product", ".reward_area", "#js_sponsor_ad_area"):
        for tag in content.select(sel):
            tag.decompose()

    # 视频占位（iframe data-src）
    for ifr in content.find_all("iframe"):
        src = ifr.get("data-src") or ifr.get("src") or ""
        text = f"▶ 视频：{src.split('?')[0]}" if src else "▶ 视频"
        new_p = soup.new_tag("p")
        new_p.string = text
        ifr.replace_with(new_p)

    # 语音占位（mpvoice）
    for v in content.find_all("mpvoice"):
        new_p = soup.new_tag("p")
        new_p.string = "🎙 语音消息"
        v.replace_with(new_p)

    # 图片：data-src → src，收集图片清单（按出现顺序去重）
    seen = set()
    for img in content.find_all("img"):
        src = img.get("data-src") or img.get("src") or ""
        if not src:
            img.decompose()
            continue
        if src not in seen:
            seen.add(src)
            art.images.append(WechatImage(url=src))
        img["src"] = src
        for k in ("data-src", "data-type", "data-ratio", "data-w", "crossorigin"):
            if k in img.attrs:
                del img[k]

    # 图片外链防盗链处理：微信图床直链可访问，无需 referer
    # 移除影响导出的内联定位样式
    for tag in content.find_all(True):
        style = tag.get("style") or ""
        if "visibility" in style or "position:absolute" in style.replace(" ", ""):
            tag["style"] = re.sub(r"visibility\s*:[^;]+;?", "", style)


# ==================== 图片下载 ====================

def download_images(art: WechatArticle, session: Optional[requests.Session] = None,
                    progress: Optional[Callable[[int, int, str], None]] = None,
                    should_cancel: Optional[Callable[[], bool]] = None) -> Tuple[int, int]:
    """下载文章全部图片到内存。返回 (成功数, 失败数)。"""
    s = session or _make_session()
    total = len(art.images)
    ok = fail = 0
    for i, img in enumerate(art.images):
        if should_cancel and should_cancel():
            break
        if progress:
            progress(i + 1, total, art.title)
        try:
            resp = s.get(img.url, timeout=20)
            resp.raise_for_status()
            data = resp.content
            fmt = _detect_fmt(img.url, data)
            if fmt == "webp":
                data, fmt = _convert_webp(data)
            img.data, img.fmt = data, fmt
            try:
                import io
                from PIL import Image
                with Image.open(io.BytesIO(data)) as im:
                    img.width, img.height = im.size
            except Exception:
                pass
            ok += 1
        except Exception:
            fail += 1
    return ok, fail


# ==================== 测试入口 ====================

if __name__ == "__main__":
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    if not url:
        print("用法: python wechat_article.py <文章链接>")
        sys.exit(1)
    t0 = time.time()
    a = fetch_article(url)
    print(f"标题: {a.title}")
    print(f"公众号: {a.account}  作者: {a.author}  发布: {a.publish_time}")
    print(f"图片: {len(a.images)} 张  正文长度: {len(a.content_html)}")
    ok, fail = download_images(a)
    print(f"图片下载: 成功 {ok} 失败 {fail}  耗时 {time.time()-t0:.1f}s")
