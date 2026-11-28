# 腾讯文档历史实现背景（非操作规则）

本页只用于理解旧实现；当前操作以 [tcdoc-sync-scope](../SKILL.md) 唯一两表白名单为准。以下功能不提供当前写入授权，不得因历史同意、已有按钮、字段正确或只追加而执行。

| 历史目标 | tab_id | 旧能力 |
| --- | --- | --- |
| 完整歌曲 | ss_ktwk0t | 仅新增，歌曲 B站链接匹配 |
| 线下活动 | ss_e1kkm2 | 仅新增、宣传图新增 / 追加、文本地址、活动歌单回填 |
| 周边图鉴 | ss_ekd4cz | 仅新增 |

## 旧新增与图片流程

旧流程只调用 addRecords、逐条读回，线下活动按活动名 / 周边按周边名去重，备注信息追加“自动推送程序上传+北京时间（到秒）”，无列则跳过。曾有单独扩展图片能力；这些实现不改变当前禁写状态。

宣传图旧来源仅事件目录 cover/ 与 images/，排除座位图、嘉宾 / 场馆等图片。旧实现按文件名和云端图片标题去重，缺图片 ID 则停止，不得整列重写。对应 webui_tcdoc_image.py、import_pending.append_candidates、/api/tcdoc/promo/{status,preview,apply}，旧上限 50 场。当前不得上传或追加。

完整歌曲“B站链接”旧字段 fhDmMB、type 8，由 src/toolbox/tcdoc/song_bili.py 匹配；历史开关 SONG_BILI_WRITE_ENABLED 默认 False，过去要求单条试写读回。本页不授权试写、开关变更或 Agent 用人工粘贴绕行。

## 地址技术背景

活动“场馆地址”为 type 22 地理位置字段，历史 OpenAPI 尝试返回 ret=22037；部分 dict 形态虽报成功，读回为空。不要再把该列当普通文本或为此申请地图 Key。历史替代“场馆地址（文本）”字段 fDmLFb、type 1；旧 geocode.py / tcdoc_map_key 方案已下线。正确解析也不代表当前可写入。

## 歌单回写背景

历史列为 活动歌单_网易云 / 活动歌单_酷狗 / 活动歌单_QQ，均 type 8，旧单列“活动歌单”不写。相关链路在 src/toolbox/tcdoc/event_playlist.py、import_local.py::update_playlist_link、src/music/playlist_link.py、桌面 / WebUI 歌单页；历史手工补链复用中间层“手动录入”。

旧流程只改指定记录的平台单列，不新增 / 删行，不覆盖已有其它链接；曾另有当次手工覆盖、确认失效链接后替换等例外。它们全部不构成当前 Agent 操作许可。未知写入结果不得重试。

QQ dirId 需换成 GetPlaylistByUin 的 tid 才能生成公开链接；酷狗曾由 specialid 过渡为 global_collection_id 的 yy/special/single/collection_3_<uid>_<listid>_0.html 链接，需校验页面与歌单名。网络错误不等于链接失效。旧回执包括已写入、已是该链接、已有其它链接、缺列、校验失败、无网页链接与写入失败；这些状态仅帮助识别旧代码。

本次技能整并没有测试线上能力、改变已有实现或恢复历史许可；未来涉及这些链路时，须先核对当前用户范围、主规则及源码。
