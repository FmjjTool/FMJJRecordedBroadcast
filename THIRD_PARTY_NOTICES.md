# 第三方组件与来源声明

FengMingToolbox（封茗囧菌数据汇总库 · 统一维护工具箱）在开发与分发过程中使用了下列第三方组件。
各组件的著作权归其各自作者所有，本项目按其原始许可证使用，不改变其授权条件。

本文件随 `LICENSE`（GPL-3.0）一并生效，是分发本项目的必要组成部分。

---

## 一、运行时依赖（随发布包分发）

| 组件 | 版本要求 | 许可证 | 项目地址 |
|---|---|---|---|
| PySide6（含 Qt 6 运行库） | >= 6.5.0 | LGPL-3.0（Qt 为 LGPL-3.0 / GPL-3.0 双授权） | <https://code.qt.io/cgit/pyside/pyside-setup.git> |
| mutagen | >= 1.46.0 | **GPL-2.0-or-later** | <https://github.com/quodlibet/mutagen> |
| httpx | >= 0.27.0 | BSD-3-Clause | <https://github.com/encode/httpx> |
| requests | >= 2.28.0 | Apache-2.0 | <https://github.com/psf/requests> |
| pydantic / pydantic-core | >= 2.0.0 | MIT | <https://github.com/pydantic/pydantic> |
| cryptography | >= 40.0.0 | Apache-2.0 OR BSD-3-Clause | <https://github.com/pyca/cryptography> |
| pycryptodome | 最新 | BSD-2-Clause（部分为公有领域） | <https://github.com/Legrandin/pycryptodome> |
| openpyxl | >= 3.1.0 | MIT | <https://foss.heptapod.net/openpyxl/openpyxl> |
| Pillow | >= 10.0.0 | MIT-CMU (HPND) | <https://github.com/python-pillow/Pillow> |
| pillow-heif | >= 0.13.0 | BSD-3-Clause | <https://github.com/bigcat88/pillow_heif> |
| numpy | >= 1.24.0 | BSD-3-Clause | <https://github.com/numpy/numpy> |
| qrcode | >= 8.0 | BSD-3-Clause | <https://github.com/lincolnloop/python-qrcode> |
| rich | >= 13.0.0 | MIT | <https://github.com/Textualize/rich> |
| beautifulsoup4 | 最新 | MIT | <https://code.launchpad.net/beautifulsoup> |
| lxml | 最新 | BSD-3-Clause | <https://github.com/lxml/lxml> |
| playwright | >= 1.40.0（可选） | Apache-2.0 | <https://github.com/microsoft/playwright-python> |
| 7-Zip（内置 `7z.exe` / `7z.dll`） | 26.06 | LGPL-2.1-or-later（含 unRAR 限制条款） | <https://www.7-zip.org/> |

### 关于 mutagen 与 GPL-3.0

`mutagen` 以 GPL-2.0-or-later 授权，是**本项目整体采用 GPL-3.0 的直接原因**。两者兼容
（GPL-2.0-or-later 允许升级至 GPL-3.0），因此本项目可以在 GPL-3.0 下合法分发。

### 关于 PySide6 与 LGPL-3.0

PySide6 采用 LGPL-3.0。本项目以 GPL-3.0 分发，其要求严于 LGPL-3.0，已完整覆盖 LGPL 的
全部义务（提供完整对应源码、保留声明、允许替换库文件）。发布包中的 Qt 运行库未作任何修改。

### 关于内置 7-Zip

- 文件：`src/toolbox/bin/7z.exe`、`src/toolbox/bin/7z.dll`，许可证原文见同目录 `7-Zip-License.txt`。
- 版本与来源：7-Zip 26.06 x64 官方发行版，未修改二进制内容。
- 许可证：主要部分按 **GNU LGPL-2.1-or-later** 分发；其中 unRAR 代码带有额外限制
  （仅可用于解压 RAR，不得用于开发 RAR 压缩程序），本项目只调用其解压能力。
- 用途：网盘下载完成后的可选解压，以及项目内的压缩包处理；发布包通过 `build.py`
  的 `ADD_DATA` 一并携带，未改变其授权条件。

---

## 二、外部程序（不在本仓库内分发）

| 程序 | 说明 |
|---|---|
| **FFmpeg / ffprobe** | 视频转换、切割、合并、抽帧、元数据写入均调用用户本机已有的 FFmpeg。本项目不打包、不附带其二进制，用户需自行安装并遵守其许可证。若使用 `--enable-gpl` 构建版本，其 GPL 义务由使用者自行承担；建议使用 LGPL 构建版。 |
| **faster-whisper** | 可选的字幕转写后端，由用户自行安装，本项目不随包分发。 |
| **必剪 / 剪映** | 字幕转换通过其公开接口调用，不包含任何其客户端代码或资源。 |

---

## 三、已并入本项目的第三方代码

### Unlock Music（音乐解锁）

- 项目地址：<https://git.unlock-music.dev/um/web>（原 GitHub 仓库已于 2022 年因 DMCA 投诉下架）
- 许可证：**MIT**
- 许可证原文：<https://git.unlock-music.dev/um/web/src/branch/master/LICENSE>
- 引用范围：`src/music_unlock.py`（全格式解密算法移植）与 `src/toolbox/_unlock_keys.json`（密钥表）
- 说明：本项目将 Unlock Music 的 TypeScript 解密算法移植为 Python，覆盖 ncm / qmc / mflac / mgg / kgm / kwm / xm / tm / x2m 等格式。MIT 与 GPL-3.0 兼容，此处保留其原始版权声明。

```
MIT License

Copyright (c) Unlock Music contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

> 提示：该算法的用途受《中华人民共和国著作权法》关于「规避技术措施」条款的约束。本项目仅在
> 用户对**自己合法持有**的音频文件做格式恢复时提供便利，不提供任何内容分发能力。

---

## 四、参考与致谢（未复制源代码）

| 来源 | 用途 | 说明 |
|---|---|---|
| **dycast**（<https://github.com/skmcj/dycast>） | 抖音直播弹幕 | 仅参考其记录的 protobuf **字段号**（通信协议的事实性信息），解析逻辑为独立实现，未复制其源代码。原仓库未声明许可证，本项目与其无代码层面的派生关系。 |
| **BililiveRecorder**（<https://github.com/Bililive/BililiveRecorder>，GPL-3.0） | 录播录制 | 参考其 FLV 流式转存与弹幕 XML 格式的设计思路，未复制其 C# 源代码。 |
| **video-parser** | 多平台视频解析 | 参考多平台解析的整体思路，具体实现为本项目独立编写。 |

如后续引入其他开源实现，须在此登记来源、许可证与引用范围。

---

## 五、构建工具链

| 工具 | 许可证 | 说明 |
|---|---|---|
| PyInstaller | GPL-2.0-with-exception | 其许可证附带例外条款，明确允许用其打包任意许可的应用程序，不影响本项目的 GPL-3.0 授权。 |

---

## 六、本项目自身的许可

- 许可证：**GNU General Public License v3.0**，全文见根目录 `LICENSE`。
- 著作权人：歆慕（XINMU）及本项目贡献者。
- 项目地址：<https://github.com/FmjjTool/FMJJRecordedBroadcast>

在遵守 GPL-3.0 的前提下，你可以自由使用、修改和再分发本项目。再分发时须保留本文件与
`LICENSE`，并在修改过的文件中注明修改内容与日期。

---

## 七、免责声明

本项目为粉丝向的数据归档与录播后期辅助工具，仅供个人学习、研究与内容整理使用。

- 使用者应遵守所访问平台的用户协议、robots 协议及所在地法律法规，不得用于批量抓取、
  商业分发或其他侵害第三方权益的用途。
- 本项目不提供、不存储任何账号凭据；所有登录态由使用者在本地自行配置并保管。
- 因使用本项目产生的任何法律风险与后果，由使用者自行承担。

---

最后更新：2026-11-17
