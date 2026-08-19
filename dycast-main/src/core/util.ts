import type { DyLiveInfo } from './dycast';

/**
 * 从 room enter API 的 JSON 响应中提取直播流 URL
 *  - API 返回格式: { data: { data: [{ stream_url: { flv_pull_url: {...}, hls_pull_url_map: {...} } }] } }
 *  - 优先 FLV（延迟低，mpegts.js 支持）
 *  - 备选 HLS/m3u8（hls.js 支持）
 * @param apiResponse room enter API 的完整 JSON 响应
 * @returns { streamUrl, streamType } 或 null
 */
export const extractStreamFromApiResponse = function (
  apiResponse: any
): { streamUrl: string; streamType: 'flv' | 'hls' } | null {
  try {
    const rooms = apiResponse?.data?.data;
    if (!Array.isArray(rooms) || rooms.length === 0) return null;

    // 找到正在直播的房间（status === 2）
    const liveRoom = rooms.find((r: any) => r.status === 2) || rooms[0];
    const streamUrl = liveRoom?.stream_url;
    if (!streamUrl) return null;

    // 1. 优先 FLV
    const flvPullUrl = streamUrl.flv_pull_url;
    if (flvPullUrl && typeof flvPullUrl === 'object') {
      // flv_pull_url 是一个 { "FULL_HD1": "https://...", "HD1": "https://..." } 的对象
      const keys = Object.keys(flvPullUrl);
      for (const key of keys) {
        const url = flvPullUrl[key];
        if (typeof url === 'string' && url.startsWith('http')) {
          return { streamUrl: url, streamType: 'flv' };
        }
      }
    }

    // 2. 备选 HLS map
    const hlsPullUrlMap = streamUrl.hls_pull_url_map;
    if (hlsPullUrlMap && typeof hlsPullUrlMap === 'object') {
      const keys = Object.keys(hlsPullUrlMap);
      for (const key of keys) {
        const url = hlsPullUrlMap[key];
        if (typeof url === 'string' && url.startsWith('http')) {
          return { streamUrl: url, streamType: 'hls' };
        }
      }
    }

    // 3. 再备选 hls_pull_url（单个字符串）
    if (typeof streamUrl.hls_pull_url === 'string' && streamUrl.hls_pull_url.startsWith('http')) {
      return { streamUrl: streamUrl.hls_pull_url, streamType: 'hls' };
    }
  } catch {
    // 解析失败
  }
  return null;
};

/** 画质排序权重（越大越好） */
const QUALITY_ORDER: Record<string, number> = {
  'ORIGIN': 100,
  'FULL_HD1': 90,
  'HD1': 70,
  'SD1': 50,
  'SD2': 30,
  'LD': 10
};

const QUALITY_LABELS: Record<string, string> = {
  'ORIGIN': '原画',
  'FULL_HD1': '蓝光',
  'HD1': '超清',
  'SD1': '高清',
  'SD2': '标清',
  'LD': '流畅'
};

export interface StreamOption {
  quality: string;
  label: string;
  url: string;
  type: 'flv' | 'hls';
}

/**
 * 从 room enter API 响应中提取所有画质的流 URL
 * 按画质从高到低排序
 */
export const extractAllStreamsFromApiResponse = function (
  apiResponse: any
): StreamOption[] {
  const results: StreamOption[] = [];
  try {
    const rooms = apiResponse?.data?.data;
    if (!Array.isArray(rooms) || rooms.length === 0) return results;

    const liveRoom = rooms.find((r: any) => r.status === 2) || rooms[0];
    const streamUrl = liveRoom?.stream_url;
    if (!streamUrl) return results;

    // FLV 流
    const flvPullUrl = streamUrl.flv_pull_url;
    if (flvPullUrl && typeof flvPullUrl === 'object') {
      for (const [key, url] of Object.entries(flvPullUrl)) {
        if (typeof url === 'string' && url.startsWith('http')) {
          results.push({
            quality: key,
            label: QUALITY_LABELS[key] || key,
            url,
            type: 'flv'
          });
        }
      }
    }

    // 如果没有 FLV，尝试 HLS
    if (results.length === 0) {
      const hlsPullUrlMap = streamUrl.hls_pull_url_map;
      if (hlsPullUrlMap && typeof hlsPullUrlMap === 'object') {
        for (const [key, url] of Object.entries(hlsPullUrlMap)) {
          if (typeof url === 'string' && url.startsWith('http')) {
            results.push({
              quality: key,
              label: QUALITY_LABELS[key] || key,
              url,
              type: 'hls'
            });
          }
        }
      }
    }

    // 按画质排序（高到低）
    results.sort((a, b) => (QUALITY_ORDER[b.quality] || 0) - (QUALITY_ORDER[a.quality] || 0));
  } catch {
    // ignore
  }
  return results;
};

/**
 * 从解码后的 JSON 字符串中提取直播流 URL（HTML 页面方式，作为备选）
 *  - 优先 FLV（延迟低，mpegts.js 支持）
 *  - 备选 HLS/m3u8（hls.js 支持）
 * @param json 已做反转义处理的 JSON 字符串
 * @returns { streamUrl, streamType } 或 null
 */
export const extractStreamUrl = function (
  json: string
): { streamUrl: string; streamType: 'flv' | 'hls' } | null {
  try {
    // 1. 尝试提取 FLV 流地址：flv_pull_url 对象中的第一个值
    //    格式示例: "flv_pull_url":{"FULL_HD1":"https://...","HD1":"https://..."}
    const flvMatch = json.match(
      /"flv_pull_url"\s*:\s*\{([^}]+)\}/
    );
    if (flvMatch) {
      // 提取对象中的第一个 URL 值
      const urlMatch = flvMatch[1].match(/"[^"]+"\s*:\s*"(https?:\/\/[^"]+)"/);
      if (urlMatch && urlMatch[1]) {
        const url = urlMatch[1].replace(/\\u0026/g, '&');
        return { streamUrl: url, streamType: 'flv' };
      }
    }

    // 2. 备选：提取 HLS/m3u8 流地址
    //    格式示例: "hls_pull_url_map":{"FULL_HD1":"https://...","HD1":"https://..."}
    const hlsMatch = json.match(
      /"hls_pull_url_map"\s*:\s*\{([^}]+)\}/
    );
    if (hlsMatch) {
      const urlMatch = hlsMatch[1].match(/"[^"]+"\s*:\s*"(https?:\/\/[^"]+)"/);
      if (urlMatch && urlMatch[1]) {
        const url = urlMatch[1].replace(/\\u0026/g, '&');
        return { streamUrl: url, streamType: 'hls' };
      }
    }

    // 3. 再备选：hls_pull_url 字段（单个字符串）
    const hlsSingleMatch = json.match(
      /"hls_pull_url"\s*:\s*"(https?:\/\/[^"]+)"/
    );
    if (hlsSingleMatch && hlsSingleMatch[1]) {
      const url = hlsSingleMatch[1].replace(/\\u0026/g, '&');
      return { streamUrl: url, streamType: 'hls' };
    }
  } catch {
    // 解析失败，忽略
  }
  return null;
};

/**
 * 解析直播间信息
 * @param html
 * @returns
 */
export const parseLiveHtml_old = function (html: string): DyLiveInfo | null {
  try {
    const matchRes = html.match(
      /<script\snonce="\S+?"\s>self\.__pace_f\.push\(\[1,"[a-z]?:\[\\"\$\\",\\"\$L\d+\\",null,([\s\S]+?state[\s\S]+?)\]\\n"\]\)<\/script>/
    );
    const REGLIST = [
      {
        reg: /\\{1,7}"/g,
        str: '"'
      },
      {
        reg: /"\{/g,
        str: '{'
      },
      {
        reg: /\}"/g,
        str: '}'
      },
      {
        reg: /"\[(.*)]\"/g,
        str: '[$1]'
      },
      {
        reg: /([^:,{\[])(")([^:,}\]])/g,
        str: '$1"$3'
      }
    ];
    if (!matchRes) return null;
    let json: string = matchRes[1];
    for (const REG of REGLIST) {
      json = json.replace(REG.reg, REG.str);
    }
    const data = JSON.parse(json);
    return {
      roomId: data['state']['roomStore']['roomInfo']['roomId'],
      uniqueId: data['state']['userStore']['odin']['user_unique_id'],
      avatar: data['state']['roomStore']['roomInfo']['anchor']?.['avatar_thumb']?.['url_list'][0],
      cover: data['state']['roomStore']['roomInfo']['room']?.['cover']?.['url_list'][0],
      nickname: data['state']['roomStore']['roomInfo']['anchor']['nickname'],
      title: data['state']['roomStore']['roomInfo']['room']['title'],
      status: data['state']['roomStore']['roomInfo']['room']['status']
    };
  } catch (err) {
    return null;
  }
};

/**
 * 解析直播间信息
 * @param html
 * @returns
 */
export const parseLiveHtml = function (html: string): DyLiveInfo | null {
  try {
    const matchRes = html.match(
      /<script\snonce="\S+?"\s>self\.__pace_f\.push\(\[1,"[a-z]?:\[\\"\$\\",\\"\$L\d+\\",null,([\s\S]+?state[\s\S]+?)\]\\n"\]\)<\/script>/
    );
    if (!matchRes) return null;
    let json: string = matchRes[1];
    const REGMAP: Record<string, RegExp> = {
      roomId: /{"state":{[\s\S]*?"roomStore":{[\s\S]*?"roomInfo":{[\s\S]*?"roomId":"([0-9]+?)"/,
      uniqueId: /{"state":{[\s\S]*?"userStore":{[\s\S]*?"odin":{[\s\S]*?"user_unique_id":"([0-9]+?)"/,
      avatar:
        /{"state":{[\s\S]*?"roomStore":{[\s\S]*?"roomInfo":{[\s\S]*?"anchor":{[\s\S]*?"avatar_thumb":{[\s\S]*?"url_list":\["([\S]+?)"/,
      cover:
        /{"state":{[\s\S]*?"roomStore":{[\s\S]*?"roomInfo":{[\s\S]*?"room":{[\s\S]*?"cover":{[\s\S]*?"url_list":\["([\S]+?)"/,
      nickname: /{"state":{[\s\S]*?"roomStore":{[\s\S]*?"roomInfo":{[\s\S]*?"anchor":{[\s\S]*?"nickname":"([\s\S]+?)"/,
      title: /{"state":{[\s\S]*?"roomStore":{[\s\S]*?"roomInfo":{[\s\S]*?"room":{[\s\S]*?"title":"([\s\S]+?)"/,
      status: /{"state":{[\s\S]*?"roomStore":{[\s\S]*?"roomInfo":{[\s\S]*?"room":{[\s\S]*?"status":([0-9]{1})/
    };
    function extractJsonField(name: string, json: string) {
      const reg = REGMAP[name];
      let res: string = '';
      if (reg) {
        const exec = reg.exec(json);
        if (exec) res = exec[1];
      }
      return res;
    }
    function decodeUnicodeUrl(url: string) {
      if (url) return url.replace(/\\u0026/g, '&');
      else return url;
    }
    json = json.replace(/\\{1,7}"/g, '"');
    const roomId = extractJsonField('roomId', json);
    const uniqueId = extractJsonField('uniqueId', json);
    const avatar = extractJsonField('avatar', json);
    const cover = extractJsonField('cover', json);
    const nickname = extractJsonField('nickname', json);
    const title = extractJsonField('title', json);
    const status = extractJsonField('status', json);

    // 提取直播流 URL
    const stream = extractStreamUrl(json);

    return {
      roomId,
      uniqueId,
      avatar: decodeUnicodeUrl(avatar),
      cover: decodeUnicodeUrl(cover),
      nickname,
      title,
      status: parseInt(status || '4'),
      streamUrl: stream?.streamUrl,
      streamType: stream?.streamType
    };
  } catch (err) {
    return null;
  }
};

/**
 * 将对象化成请求参数字符串
 *  - 如：item1=value1&item2=value2&...
 * @param params 请求参数对象
 * @returns
 */
export const makeUrlParams = function (params: any): string {
  return Object.keys(params).reduce((t, n) => {
    let r;
    return `${t}${t ? '&' : ''}${n}=${null != (r = params[n]) ? r : ''}`;
  }, '');
};

