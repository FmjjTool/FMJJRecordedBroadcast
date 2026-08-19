<template>
  <div :class="['index-view', { 'video-only': videoMode === 'video-only' }]">
    <div class="view-left">
      <LiveInfo
        :cover="cover"
        :title="title"
        :avatar="avatar"
        :nickname="nickname"
        :follow-count="followCount"
        :member-count="memberCount"
        :user-count="userCount"
        :like-count="likeCount" />
      <div class="view-left-bottom">
        <div class="view-left-tools">
          <div class="view-left-tool danger-tool" title="清除当前信息" @click.stop="confirmClearCurrentInfo">
            <i class="ice-close"></i>
          </div>
          <div class="view-left-tool" title="保存弹幕" @click.stop="saveCastToFile">
            <i class="ice-save"></i>
          </div>
          <div class="view-left-tool" title="保存统计数据" @click.stop="saveStatsToFile">
            <i class="ice-stat"></i>
          </div>
          <div
            :class="{ 'view-left-tool': true, 'monitor-active': isMonitoring, 'tool-disabled': isReplaying }"
            :title="isReplaying ? '回放中不可使用监控' : isMonitoring ? '停止监控 (每' + monitorInterval / 1000 + '秒检查)' : '开始监控开播'"
            @click.stop="toggleMonitor">
            <i class="ice-record"></i>
          </div>
          <div
            :class="{ 'view-left-tool': true, 'monitor-active': isReplaying, 'tool-disabled': connectStatus === 1 }"
            :title="connectStatus === 1 ? '录制中不可回放' : isReplaying ? '停止回放' : '导入弹幕回放'"
            @click.stop="isReplaying ? stopReplay() : triggerReplayImport()">
            <i class="ice-live"></i>
          </div>

          <div class="view-left-tool" title="设置" @click.stop="openSettings">
            <i class="ice-setting"></i>
          </div>
        </div>
        <hr class="hr" />
        <LiveStatusPanel ref="panel" :status="connectStatus" />
      </div>
    </div>
    <div :class="['view-center', { 'portrait-layout': isPortraitVideo }]">
      <!-- 仅视频且非竖屏时，在中间顶部保留房间号与 WS 输入 -->
      <div v-if="videoMode === 'video-only' && !isPortraitVideo" class="view-input video-only-input" style="width:100%;padding-bottom:8px;box-sizing:border-box;">
        <ConnectInput
          ref="roomInputVideoOnly"
          label="房间号"
          placeholder="请输入房间号"
          v-model:value="roomNum"
          :test="verifyRoomNumber"
          @confirm="connectLive"
          @cancel="disconnectLive" />
        <ConnectInput
          ref="relayInputVideoOnly"
          label="WS地址"
          placeholder="请输入转发地址"
          confirm-text="转发"
          cancel-text="停止"
          v-model:value="relayUrl"
          :test="verifyWssUrl"
          @confirm="relayCast"
          @cancel="stopRelayCast" />
      </div>
      <div v-if="videoMode !== 'off' && !isReplaying" class="view-live-preview">
        <div class="view-live-preview-header">
          <span>直播预览{{ isRecording ? '（录制中 ●）' : '（仅播放，不录制）' }}</span>
          <div class="view-live-preview-actions">
            <button
              v-if="roomNum.trim()"
              type="button"
              class="view-live-refresh"
              :disabled="livePreviewLoading"
              @click="refreshLivePreview(true)">
              {{ livePreviewLoading ? '抓流中' : '刷新流' }}
            </button>
            <a
              v-if="livePreviewPageUrl"
              :href="livePreviewPageUrl"
              target="_blank"
              rel="noopener noreferrer"
              class="view-live-link">
              新窗口打开
            </a>
            <select
              v-if="availableStreams.length > 1"
              class="view-live-quality"
              :value="selectedStreamQuality"
              @change="onStreamQualityChange($event)">
              <option v-for="s in availableStreams" :key="s.quality" :value="s.quality">
                {{ s.label }} ({{ s.type.toUpperCase() }})
              </option>
            </select>
          </div>
        </div>
        <div class="view-live-preview-main">
          <div v-if="livePreviewStreamUrl" class="view-live-video-shell">
            <video ref="livePreviewVideoRef" class="view-live-video" autoplay playsinline controls @loadedmetadata="onVideoMetadata"></video>
            <div v-if="livePreviewLoading" class="view-live-overlay">正在刷新直播流...</div>
          </div>
          <div v-else class="view-live-empty">
            {{ livePreviewLoading ? '正在抓取直播流...' : livePreviewError || '请输入房间号后显示直播预览' }}
          </div>
        </div>
      </div>
      <div v-if="videoMode !== 'off' && isReplaying && replayVideoUrl" class="view-live-preview">
        <div class="view-live-preview-header">
          <span>回放视频</span>
        </div>
        <div class="view-live-preview-main">
          <div class="view-live-video-shell">
            <video ref="replayVideoRef" class="view-live-video" playsinline controls @loadedmetadata="onVideoMetadata" @seeking="onReplayVideoSeeking"></video>
          </div>
        </div>
      </div>
      <div v-if="videoMode !== 'video-only'" class="view-center-casts">
        <!-- 主要弹幕：聊天、礼物 -->
        <CastList :types="['chat', 'gift']" :force-latest-scroll="forceLatestScroll" ref="castEl" />
      </div>
      <div v-if="isReplaying" class="replay-progress">
        <button class="replay-pause-btn" @click="toggleReplayPause" :title="replayPaused ? '继续' : '暂停'">
          {{ replayPaused ? '▶' : '⏸' }}
        </button>
        <span class="replay-time">{{ formatDuration(replayCurrentMs) }}</span>
        <input
          type="range"
          class="replay-slider"
          min="0"
          :max="replayTotalMs"
          :value="replayCurrentMs"
          @input="onReplaySeek($event)" />
        <span class="replay-time">{{ formatDuration(replayTotalMs) }}</span>
        <input
          class="replay-speed-slider"
          type="range"
          min="1"
          max="20"
          step="0.1"
          v-model.number="replaySpeed"
          @input="onReplaySpeedChange"
          title="播放倍速"
        />
        <span class="replay-speed-label">{{ Number(replaySpeed).toFixed(1) }}x</span>
      </div>
    </div>
      <div v-if="videoMode !== 'video-only' || isPortraitVideo" class="view-right">
      <div v-if="videoMode !== 'video-only' || isPortraitVideo" class="view-input">
        <ConnectInput
          ref="roomInput"
          label="房间号"
          placeholder="请输入房间号"
          v-model:value="roomNum"
          :test="verifyRoomNumber"
          @confirm="connectLive"
          @cancel="disconnectLive" />
        <ConnectInput
          ref="relayInput"
          label="WS地址"
          placeholder="请输入转发地址"
          confirm-text="转发"
          cancel-text="停止"
          v-model:value="relayUrl"
          :test="verifyWssUrl"
          @confirm="relayCast"
          @cancel="stopRelayCast" />
        <!-- SessionID 登录凭证 -->
        <SidTool />
      </div>
      <div v-if="videoMode !== 'video-only'" class="view-rank">
        <div class="gift-rank-title">累计打赏榜</div>
        <div class="gift-rank-head">
          <span>排名</span>
          <span>昵称</span>
          <span>礼物数</span>
          <span>价值(抖币)</span>
          <span>更新时间</span>
        </div>
        <div class="gift-rank-list">
          <div v-if="giftRankList.length === 0" class="gift-rank-empty">暂无礼物数据</div>
          <div v-for="(item, idx) in giftRankList" :key="`${item.name}-${idx}`" class="gift-rank-row">
            <span class="rank">{{ idx + 1 }}</span>
            <span class="name" :title="item.name">{{ item.name }}</span>
            <span class="count">{{ formatRankNumber(item.giftCount) }}</span>
            <span class="value">{{ formatRankNumber(item.totalValue) }}</span>
            <span class="time">{{ formatRankTime(item.lastUpdateTime) }}</span>
          </div>
        </div>
      </div>
      <div v-if="videoMode !== 'video-only'" class="view-other">
        <!-- 其它弹幕：关注、点赞、进入、控制台等 -->
        <CastList ref="otherEl" :types="['social', 'like', 'member']" :force-latest-scroll="forceLatestScroll" pos="left" no-prefix />
      </div>
    </div>
    <SettingsDialog
      ref="settingsRef"
      :monitor-interval="monitorInterval"
      :stats-interval="statsInterval"
      :room-list="monitorRoomList"
      :enable-recording="enableRecording"
      :dark-mode="darkMode"
      :timed-bw-enabled="timedBWEnabled"
      :timed-bw-start="timedBWStart"
      :timed-bw-end="timedBWEnd"
      :timed-bw-default="timedBWDefault"
      :video-mode="videoMode"
      :force-latest-scroll="forceLatestScroll"
      @update="handleSettingsUpdate" />
    <!-- 回放导入文件选择器（隐藏） -->
    <input
      ref="replayFileInput"
      type="file"
      accept=".json,.zip"
      multiple
      style="display: none"
      @change="handleReplayFileSelect" />
  </div>
</template>

<script setup lang="ts">
import ConnectInput from '@/components/ConnectInput.vue';
import LiveInfo from '@/components/LiveInfo.vue';
import LiveStatusPanel from '@/components/LiveStatusPanel.vue';
import CastList from '@/components/CastList.vue';
import SettingsDialog from '@/components/SettingsDialog.vue';
import SidTool from '@/components/SidTool/SidTool.vue';
import {
  CastMethod,
  DyCast,
  DyCastCloseCode,
  RoomStatus,
  type ConnectStatus,
  type DyLiveInfo,
  type DyMessage,
  type LiveRoom
} from '@/core/dycast';
import { verifyRoomNum, verifyWsUrl } from '@/utils/verifyUtil';
import { computed, ref, useTemplateRef, onBeforeUnmount, watch, nextTick, onMounted } from 'vue';
import { CLog } from '@/utils/logUtil';
import { getId } from '@/utils/idUtil';
import { RelayCast } from '@/core/relay';
import SkMessage from '@/components/Message';
import { formatDate } from '@/utils/commonUtil';
import FileSaver from '@/utils/fileUtil';
import JSZip from 'jszip';
import { fetchLiveInfo, fetchRoomStreamInfo } from '@/core/request';
import { extractStreamUrl, extractStreamFromApiResponse, extractAllStreamsFromApiResponse, type StreamOption } from '@/core/util';
import mpegts from 'mpegts.js';
import Hls from 'hls.js';

// 连接状态
const connectStatus = ref<ConnectStatus>(0);
// 转发状态
const relayStatus = ref<ConnectStatus>(0);
// 房间号
const roomNum = ref<string>('');
// 房间号输入框状态
const roomInputRef = useTemplateRef('roomInput');
// 中央仅视频模式的输入引用
const roomInputVideoOnlyRef = useTemplateRef('roomInputVideoOnly');
// 转发地址
const relayUrl = ref<string>('');
const relayInputRef = useTemplateRef('relayInput');
const relayInputVideoOnlyRef = useTemplateRef('relayInputVideoOnly');
// 状态面板
const statusPanelRef = useTemplateRef('panel');

/** 直播间信息 */
const cover = ref<string>('');
const title = ref<string>('*****');
const avatar = ref<string>('');
const nickname = ref<string>('***');
const followCount = ref<string | number>('*****');
const memberCount = ref<string | number>('*****');
const userCount = ref<string | number>('*****');
const likeCount = ref<string | number>('*****');
const livePreviewPageUrl = computed(() => {
  const room = roomNum.value.trim();
  if (!room) return '';
  return `https://live.douyin.com/${room}`;
});
const livePreviewFrameUrl = computed(() => {
  const room = roomNum.value.trim();
  if (!room) return '';
  // 带一个参数避免被某些浏览器复用缓存的空文档
  return `/dylive/${room}?dycast_preview=1`;
});

/** ===== 直播预览流播放 ===== */
const livePreviewStreamUrl = ref('');
const livePreviewLoading = ref(false);
const livePreviewError = ref('');
const livePreviewStreamType = ref<'' | 'flv' | 'hls'>('');
const livePreviewStreamRoom = ref('');
const isPortraitVideo = ref(false);
const availableStreams = ref<StreamOption[]>([]);
const selectedStreamQuality = ref('');

/** 视频元数据加载后检测横竖屏 */
const onVideoMetadata = function (e: Event) {
  const video = e.target as HTMLVideoElement;
  if (video.videoWidth && video.videoHeight) {
    isPortraitVideo.value = video.videoHeight > video.videoWidth;
    CLog.info(`视频尺寸: ${video.videoWidth}x${video.videoHeight}, ${isPortraitVideo.value ? '竖屏' : '横屏'}`);
  }
};

/** 切换画质 */
const onStreamQualityChange = function (e: Event) {
  const target = e.target as HTMLSelectElement;
  selectedStreamQuality.value = target.value;
  addConsoleMessage(`切换画质: ${target.value}`);
  refreshLivePreview(true);
};
const livePreviewVideoRef = useTemplateRef<HTMLVideoElement>('livePreviewVideoRef');
let livePreviewMpegtsPlayer: mpegts.Player | null = null;
let livePreviewHlsInstance: Hls | null = null;

/** 销毁当前直播预览播放器 */
const destroyLivePreviewPlayer = function () {
  if (livePreviewMpegtsPlayer) {
    try {
      livePreviewMpegtsPlayer.pause();
      livePreviewMpegtsPlayer.unload();
      livePreviewMpegtsPlayer.detachMediaElement();
      livePreviewMpegtsPlayer.destroy();
    } catch (e) {
      CLog.warn('销毁 mpegts 播放器出错:', e);
    }
    livePreviewMpegtsPlayer = null;
  }
  if (livePreviewHlsInstance) {
    try {
      livePreviewHlsInstance.destroy();
    } catch (e) {
      CLog.warn('销毁 HLS 播放器出错:', e);
    }
    livePreviewHlsInstance = null;
  }
  isPortraitVideo.value = false;
};

const inferStreamType = function (url: string): 'flv' | 'hls' {
  const low = (url || '').toLowerCase();
  if (low.includes('.m3u8') || low.includes('format=hls') || low.includes('type=m3u8')) return 'hls';
  return 'flv';
};

/** 仅重建播放器（不重新请求流接口） */
const mountLivePreviewPlayer = async function (streamUrl: string, streamType: 'flv' | 'hls') {
  // 先销毁旧播放器
  destroyLivePreviewPlayer();
  livePreviewStreamUrl.value = streamUrl;
  livePreviewError.value = '';

  // 等 DOM 更新后绑定播放器
  await nextTick();
  const videoEl = livePreviewVideoRef.value;
  if (!videoEl) {
    livePreviewError.value = '视频元素未就绪';
    livePreviewStreamUrl.value = '';
    return;
  }
  if (streamType === 'flv') {
    if (!mpegts.isSupported()) {
      livePreviewError.value = '当前浏览器不支持 FLV 播放';
      livePreviewStreamUrl.value = '';
      return;
    }
    const player = mpegts.createPlayer({
      type: 'flv',
      isLive: true,
      url: streamUrl
    }, {
      enableWorker: false,
      enableStashBuffer: false,
      stashInitialSize: 128,
      liveBufferLatencyChasing: true,
      liveBufferLatencyMaxLatency: 1.5,
      liveBufferLatencyMinRemain: 0.3
    });
    player.attachMediaElement(videoEl);
    player.load();
    Promise.resolve(player.play()).catch(() => {});
    livePreviewMpegtsPlayer = player;
    player.on(mpegts.Events.ERROR, (e: any) => {
      CLog.warn('mpegts 播放错误:', e);
    });
  } else {
    // HLS
    if (Hls.isSupported()) {
      const hls = new Hls({
        liveSyncDurationCount: 3,
        liveMaxLatencyDurationCount: 6,
        enableWorker: false
      });
      hls.loadSource(streamUrl);
      hls.attachMedia(videoEl);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        videoEl.play().catch(() => {});
      });
      hls.on(Hls.Events.ERROR, (_event: any, data: any) => {
        if (data.fatal) {
          CLog.warn('HLS 致命错误:', data);
        }
      });
      livePreviewHlsInstance = hls;
    } else if (videoEl.canPlayType('application/vnd.apple.mpegurl')) {
      // Safari 原生 HLS
      videoEl.src = streamUrl;
      videoEl.play().catch(() => {});
    } else {
      livePreviewError.value = '当前浏览器不支持 HLS 播放';
      livePreviewStreamUrl.value = '';
    }
  }
};

/**
 * 刷新直播预览流
 * @param force 是否强制刷新（即使已有流）
 */
const refreshLivePreview = async function (force = false) {
  const room = roomNum.value.trim();
  if (!room) {
    livePreviewStreamUrl.value = '';
    livePreviewStreamType.value = '';
    livePreviewStreamRoom.value = '';
    livePreviewError.value = '请输入房间号后显示直播预览';
    return;
  }
  if (livePreviewLoading.value && !force) return;

  // 同一个直播间优先复用已缓存的流（不重新抓流）
  if (!force && livePreviewStreamUrl.value && livePreviewStreamRoom.value === room) {
    livePreviewLoading.value = true;
    livePreviewError.value = '';
    try {
      const streamUrl = livePreviewStreamUrl.value;
      const streamType = (livePreviewStreamType.value || inferStreamType(streamUrl)) as 'flv' | 'hls';
      CLog.info(`复用缓存直播流 [${streamType}]:`, streamUrl);
      await mountLivePreviewPlayer(streamUrl, streamType);
      return;
    } catch (err: any) {
      CLog.warn('复用缓存流失败，改为重新抓流:', err);
    } finally {
      livePreviewLoading.value = false;
      // 录制：流加载完成后自动开始录制
      if (livePreviewStreamUrl.value && enableRecording.value) {
        setTimeout(() => startRecording(), 500);
      }
    }
  }

  // 刷新前保存当前录制
  await saveCurrentRecording();
  livePreviewLoading.value = true;
  livePreviewError.value = '';
  try {
    // 方法1：通过 room enter API 获取直播流（推荐，返回结构化 JSON）
    //        先请求一次 HTML 页面以获取必要的 cookie（__ac_nonce 等）
    await fetchLiveInfo(room).catch(() => {});
    const apiRes = await fetchRoomStreamInfo(room);
    let stream = extractStreamFromApiResponse(apiRes);

    // 提取所有画质选项
    const allStreams = extractAllStreamsFromApiResponse(apiRes);
    if (allStreams.length > 0) {
      availableStreams.value = allStreams;
      // 默认选最高画质，或保持用户之前的选择
      if (!selectedStreamQuality.value || !allStreams.find(s => s.quality === selectedStreamQuality.value)) {
        selectedStreamQuality.value = allStreams[0].quality;
      }
      // 使用用户选择的画质
      const chosen = allStreams.find(s => s.quality === selectedStreamQuality.value);
      if (chosen) {
        stream = { streamUrl: chosen.url, streamType: chosen.type };
      }
    }

    // 方法2：API 无法获取时，降级从 HTML 页面解析
    if (!stream) {
      CLog.info('API 未返回直播流，尝试从 HTML 页面解析...');
      const html = await fetchLiveInfo(room);
      stream = extractStreamUrl(html);
      if (!stream) {
        const html2 = await fetchLiveInfo(room);
        stream = extractStreamUrl(html2);
      }
    }

    if (!stream) {
      livePreviewStreamUrl.value = '';
      livePreviewStreamType.value = '';
      livePreviewStreamRoom.value = '';
      livePreviewError.value = '未获取到直播流，主播可能未开播';
      livePreviewLoading.value = false;
      return;
    }
    // 通过 Vite 代理转发流地址（避免 CORS）
    // 流地址示例: https://pull-flv-l77.douyincdn.com/third/stream-xxx.flv
    // 我们直接使用原始 URL，因为流 CDN 通常允许跨域
    const streamUrl = stream.streamUrl;
    const streamType = stream.streamType;
    CLog.info(`直播流获取成功 [${streamType}]:`, streamUrl);
    await mountLivePreviewPlayer(streamUrl, streamType);
    if (livePreviewStreamUrl.value) {
      livePreviewStreamType.value = streamType;
      livePreviewStreamRoom.value = room;
    }
  } catch (err: any) {
    CLog.error('直播预览流获取失败:', err);
    // 若当前没有可用流，才清空；避免刷新失败把原本可播的流清掉
    if (!livePreviewStreamUrl.value || livePreviewStreamRoom.value !== room) {
      livePreviewStreamUrl.value = '';
      livePreviewStreamType.value = '';
      livePreviewStreamRoom.value = '';
    }
    livePreviewError.value = `获取直播流失败: ${err?.message || err}`;
  } finally {
    livePreviewLoading.value = false;
    // 录制：流加载完成后自动开始录制
    if (livePreviewStreamUrl.value && enableRecording.value) {
      // 稍微延迟，确保视频已开始播放
      setTimeout(() => startRecording(), 500);
    }
  }
};

// 组件卸载时清理播放器
onBeforeUnmount(() => {
  destroyLivePreviewPlayer();
  releaseWebLock();
  document.removeEventListener('visibilitychange', onVisibilityChange);
  document.removeEventListener('visibilitychange', onThemeVisibilityChange);
  window.removeEventListener('focus', onThemeFocus);
  clearThemeTimer();
});

/**
 * ===== 后台标签页保护 =====
 * 1. Web Lock API：持有锁期间浏览器不会完全节流标签页
 * 2. visibilitychange：切回前台时检查连接状态
 */
let webLockController: AbortController | null = null;

const acquireWebLock = function () {
  if (webLockController || !navigator.locks) return;
  webLockController = new AbortController();
  navigator.locks.request('dycast-active', { signal: webLockController.signal }, () => {
    // 持有锁直到 abort — 返回一个永不 resolve 的 Promise
    return new Promise(() => {});
  }).catch(() => {});
  CLog.info('Web Lock 已获取，后台标签页将减少节流');
};

const releaseWebLock = function () {
  if (webLockController) {
    webLockController.abort();
    webLockController = null;
    CLog.info('Web Lock 已释放');
  }
};

const onVisibilityChange = function () {
  if (document.visibilityState === 'hidden') {
    // 页面进入后台 → 立即保存一次会话数据
    if (connectStatus.value === 1 && allCasts.length > 0) {
      saveSession();
    }
  } else if (document.visibilityState === 'visible') {
    // 页面恢复前台 → 检查连接状态
    if (connectStatus.value === 1 && castWs) {
      // 检查 WebSocket 是否还活着
      const ws = (castWs as any)._ws as WebSocket | undefined;
      if (ws && ws.readyState !== WebSocket.OPEN) {
        addConsoleMessage('⚠️ 检测到后台期间 WebSocket 断开，数据已自动保存，请检查连接');
        CLog.warn('后台期间 WebSocket 连接已断开');
      }
    }
  }
};

document.addEventListener('visibilitychange', onVisibilityChange);

interface GiftRankItem {
  name: string;
  avatar: string;
  giftCount: number;
  totalValue: number;
  lastUpdateTime: number;
}
const GIFT_RANK_LIMIT = 150;
const giftRankList = ref<GiftRankItem[]>([]);
const giftRankMap = new Map<string, GiftRankItem>();

/** 格式化榜单更新时间 */
const formatRankTime = function (ts?: number): string {
  if (!ts) return '--';
  const d = new Date(ts);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
};

// 主要弹幕
const castRef = useTemplateRef('castEl');
// 其它弹幕
const otherRef = useTemplateRef('otherEl');
// 所有弹幕
const allCasts: DyMessage[] = [];
// 记录弹幕
const castSet = new Set<string>();
// 弹幕客户端
let castWs: DyCast | undefined;
// 转发客户端
let relayWs: RelayCast | undefined;

/** ===== 直播间统计数据定时采集 ===== */
interface StatsSnapshot {
  /** 采集时间戳(毫秒) */
  time: number;
  /** 格式化时间 HH:mm:ss.SSS */
  timeStr: string;
  /** 在线观众数 */
  audienceCount: string | number;
  /** 主播粉丝数 */
  followCount: string | number;
  /** 本场点赞数 */
  likeCount: string | number;
  /** 累计观看人数 */
  totalUserCount: string | number;
}
// 统计快照列表
const statsSnapshots: StatsSnapshot[] = [];
// 采集间隔（毫秒），默认 3 秒
const statsInterval = ref(3000);
// 定时器句柄
let statsTimer: ReturnType<typeof setInterval> | null = null;
// 是否启用直播录制
const enableRecording = ref(false);
// 深色模式
const darkMode = ref(false);
// 定时黑白模式设置
const timedBWEnabled = ref(false);
const timedBWStart = ref('07:00');
const timedBWEnd = ref('19:00');
const timedBWDefault = ref('black'); // 'black' | 'white'
// 视频模式: 'normal' | 'off' | 'video-only'
const videoMode = ref('normal');
// 保持滚动：即使手动滚动也强制显示最新
const forceLatestScroll = ref(false);

// 监听深色模式切换
watch(darkMode, (isDark) => {
  document.documentElement.classList.toggle('dark', isDark);
});

// 解析 HH:mm（允许 HH:mm:ss）为当日分钟数
const toMinuteOfDay = function (timeStr: string, fallback: string) {
  const parse = (raw: string) => {
    const m = /^(\d{1,2}):(\d{2})/.exec((raw || '').trim());
    if (!m) return null;
    const h = Number(m[1]);
    const mm = Number(m[2]);
    if (!Number.isFinite(h) || !Number.isFinite(mm)) return null;
    if (h < 0 || h > 23 || mm < 0 || mm > 59) return null;
    return h * 60 + mm;
  };
  return parse(timeStr) ?? parse(fallback) ?? 0;
};

// 根据定时黑白设置评估主题（仅当启用自动深色时覆盖）
const evaluateTheme = function () {
  try {
    if (!timedBWEnabled.value) return; // 未启用自动，不覆盖手动设置
    const now = new Date();
    const cur = now.getHours() * 60 + now.getMinutes();
    const start = toMinuteOfDay(timedBWStart.value, '07:00');
    const end = toMinuteOfDay(timedBWEnd.value, '19:00');
    const defaultIsBlack = timedBWDefault.value !== 'white';

    // 时间段内显示“默认色的反色”，时间段外显示“默认色”
    let inWindow = false;
    if (start === end) {
      inWindow = true;
    } else if (start < end) {
      inWindow = cur >= start && cur < end;
    } else {
      // 跨午夜
      inWindow = cur >= start || cur < end;
    }
    const shouldBlack = inWindow ? !defaultIsBlack : defaultIsBlack;
    darkMode.value = shouldBlack;
  } catch (e) {
    // ignore
  }
};

let themeTimer: ReturnType<typeof setTimeout> | null = null;
const clearThemeTimer = function () {
  if (themeTimer) {
    clearTimeout(themeTimer);
    themeTimer = null;
  }
};

// 对齐到“下一分钟”触发，边界时刻更稳定
const scheduleThemeTimer = function () {
  clearThemeTimer();
  if (!timedBWEnabled.value) return;
  const now = new Date();
  const delay = Math.max(500, (60 - now.getSeconds()) * 1000 - now.getMilliseconds() + 80);
  themeTimer = setTimeout(() => {
    evaluateTheme();
    scheduleThemeTimer();
  }, delay);
};

// 配置变化时立即评估并重新调度
watch([timedBWEnabled, timedBWStart, timedBWEnd, timedBWDefault], () => {
  evaluateTheme();
  scheduleThemeTimer();
}, { immediate: true });

// 页面回前台/聚焦时立刻补偿一次，避免后台节流导致延迟
const onThemeVisibilityChange = function () {
  if (document.visibilityState === 'visible') {
    evaluateTheme();
    scheduleThemeTimer();
  }
};
const onThemeFocus = function () {
  evaluateTheme();
  scheduleThemeTimer();
};
document.addEventListener('visibilitychange', onThemeVisibilityChange);
window.addEventListener('focus', onThemeFocus);

/** ===== 直播录制（直接抓取原始 FLV 流） ===== */
const isRecording = ref(false);
let recordingChunks: Uint8Array[] = [];
let recordingStartTs = 0;
let recordingEndTs = 0;
let recordingAbortCtrl: AbortController | null = null;

/** 录制分段累计（刷新流时保存到此数组，最终导出时打包到同一ZIP） */
interface RecordingSegment {
  blob: Blob;
  startTimestamp: number;
  endTimestamp: number;
  index: number;
}
let recordingSegments: RecordingSegment[] = [];

/** 开始录制 — 直接 fetch 原始 FLV 流并读取 ReadableStream */
const startRecording = function () {
  if (!enableRecording.value) return;
  if (isRecording.value) return;
  const streamUrl = livePreviewStreamUrl.value;
  if (!streamUrl) {
    CLog.warn('录制失败：没有可用的直播流 URL');
    return;
  }
  try {
    recordingChunks = [];
    recordingStartTs = Date.now();
    recordingEndTs = 0;
    isRecording.value = true;
    const abortCtrl = new AbortController();
    recordingAbortCtrl = abortCtrl;

    // 并行 fetch 原始流
    fetch(streamUrl, { signal: abortCtrl.signal })
      .then(async (res) => {
        if (!res.ok || !res.body) {
          CLog.error('录制：获取流失败', res.status);
          isRecording.value = false;
          return;
        }
        const reader = res.body.getReader();
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          if (value) recordingChunks.push(value);
        }
        // 流自然结束（主播下播等）
        recordingEndTs = Date.now();
        isRecording.value = false;
        CLog.info(`录制流自然结束，共 ${recordingChunks.length} 个片段`);
        addConsoleMessage(`录制已结束，时长: ${Math.round((recordingEndTs - recordingStartTs) / 1000)}秒`);
      })
      .catch((err) => {
        if (err?.name === 'AbortError') {
          // 手动停止，正常
          return;
        }
        CLog.error('录制流读取出错:', err);
        isRecording.value = false;
      });

    addConsoleMessage('直播录制已开始（FLV 原始流）');
    CLog.info('录制已开始，流地址:', streamUrl);
  } catch (e) {
    CLog.error('启动录制失败:', e);
    addConsoleMessage('启动录制失败');
    isRecording.value = false;
  }
};

/** 停止录制 */
const stopRecording = function (): Promise<void> {
  return new Promise((resolve) => {
    if (!isRecording.value && recordingChunks.length === 0) {
      resolve();
      return;
    }
    // 中断 fetch 流
    if (recordingAbortCtrl) {
      recordingAbortCtrl.abort();
      recordingAbortCtrl = null;
    }
    recordingEndTs = recordingEndTs || Date.now();
    isRecording.value = false;
    CLog.info(`录制已停止，共 ${recordingChunks.length} 个片段`);
    addConsoleMessage(`录制已停止，时长: ${Math.round((recordingEndTs - recordingStartTs) / 1000)}秒`);
    // 给一点时间让最后的 chunk 写入
    setTimeout(resolve, 200);
  });
};

/** 获取录制的 Blob（FLV 格式） */
const getRecordingBlob = function (): Blob | null {
  if (recordingChunks.length === 0) return null;
  return new Blob(recordingChunks as BlobPart[], { type: 'video/x-flv' });
};

/** 保存当前录制到分段数组（不立即下载，等最终导出时一起打包） */
const saveCurrentRecording = async function () {
  if (!isRecording.value && recordingChunks.length === 0) return;
  await stopRecording();
  const recordingBlob = getRecordingBlob();
  if (!recordingBlob || recordingBlob.size === 0) {
    recordingChunks = [];
    recordingStartTs = 0;
    recordingEndTs = 0;
    return;
  }

  // 累计到分段数组
  recordingSegments.push({
    blob: recordingBlob,
    startTimestamp: recordingStartTs,
    endTimestamp: recordingEndTs || Date.now(),
    index: recordingSegments.length
  });
  addConsoleMessage(`录制分段 #${recordingSegments.length} 已保存（${Math.round(recordingBlob.size / 1024 / 1024 * 10) / 10}MB），将在最终导出时打包`);

  // 清空当前录制状态，准备下次录制
  recordingChunks = [];
  recordingStartTs = 0;
  recordingEndTs = 0;
};

/** 采集一次当前统计快照 */
const captureStatsSnapshot = function () {
  const now = Date.now();
  statsSnapshots.push({
    time: now,
    timeStr: formatDate(new Date(now), 'HH:mm:ss.SSS'),
    audienceCount: memberCount.value,
    followCount: followCount.value,
    likeCount: likeCount.value,
    totalUserCount: userCount.value
  });
};

/** 开始定时采集 */
const startStatsRecording = function () {
  stopStatsRecording();
  // 立即采集一次
  captureStatsSnapshot();
  statsTimer = setInterval(captureStatsSnapshot, statsInterval.value);
};

/** 停止定时采集 */
const stopStatsRecording = function () {
  if (statsTimer) {
    clearInterval(statsTimer);
    statsTimer = null;
  }
};

/** ===== 自动监控开播并连接 ===== */
// 监控状态
const isMonitoring = ref(false);
// 监控间隔（毫秒），默认 30 秒
const monitorInterval = ref(30000);
// 监控定时器句柄
let monitorTimer: ReturnType<typeof setInterval> | null = null;
// 监控房间号列表（换行分隔）
const monitorRoomList = ref('');
// 轮询索引
let monitorRoomIndex = 0;
// 多房间分页面时，主页面固定监控的房间号
let monitorMainRoom = '';
// 由主控页打开的子监控窗口
const monitorChildWindows: Window[] = [];
// URL 参数：用于“单房间子监控页”
const monitorSearch = new URLSearchParams(window.location.search);
const monitorRoomFromQuery = (monitorSearch.get('monitorRoom') || '').trim();
const monitorAutoStart = monitorSearch.get('autoMonitor') === '1';
const isMonitorChildPage = monitorSearch.get('monitorChild') === '1' || !!monitorRoomFromQuery;
let shouldAutoStartMonitorByQuery = isMonitorChildPage && monitorAutoStart;

/** 从配置文件加载设置 */
const loadConfig = async function () {
  try {
    const res = await fetch('/api/config');
    if (res.ok) {
      const cfg = await res.json();
      if (cfg.monitorInterval) monitorInterval.value = cfg.monitorInterval;
      if (cfg.statsInterval) statsInterval.value = cfg.statsInterval;
      if (!isMonitorChildPage && cfg.monitorRoomList != null) {
        monitorRoomList.value = cfg.monitorRoomList;
      }
      if (typeof cfg.enableRecording === 'boolean') {
        enableRecording.value = cfg.enableRecording;
      }
      if (typeof cfg.darkMode === 'boolean') {
        darkMode.value = cfg.darkMode;
      }
      if (typeof cfg.timedBWEnabled === 'boolean') {
        timedBWEnabled.value = cfg.timedBWEnabled;
      }
      if (typeof cfg.timedBWStart === 'string') {
        timedBWStart.value = cfg.timedBWStart;
      }
      if (typeof cfg.timedBWEnd === 'string') {
        timedBWEnd.value = cfg.timedBWEnd;
      }
      if (typeof cfg.timedBWDefault === 'string') {
        timedBWDefault.value = cfg.timedBWDefault;
      }
      if (typeof cfg.videoMode === 'string') {
        videoMode.value = cfg.videoMode;
      }
      if (typeof cfg.forceLatestScroll === 'boolean') {
        forceLatestScroll.value = cfg.forceLatestScroll;
      }
      // merge localStorage fallback
      try {
        const raw = localStorage.getItem('dycast.settings');
        if (raw) {
          const local = JSON.parse(raw);
          if (typeof local.timedBWEnabled === 'boolean' && cfg.timedBWEnabled == null) timedBWEnabled.value = local.timedBWEnabled;
          if (typeof local.timedBWStart === 'string' && cfg.timedBWStart == null) timedBWStart.value = local.timedBWStart;
          if (typeof local.timedBWEnd === 'string' && cfg.timedBWEnd == null) timedBWEnd.value = local.timedBWEnd;
          if (typeof local.timedBWDefault === 'string' && cfg.timedBWDefault == null) timedBWDefault.value = local.timedBWDefault;
          if (typeof local.videoMode === 'string' && cfg.videoMode == null) videoMode.value = local.videoMode;
          if (typeof local.forceLatestScroll === 'boolean' && cfg.forceLatestScroll == null) forceLatestScroll.value = local.forceLatestScroll;
          if (typeof local.darkMode === 'boolean' && cfg.darkMode == null) darkMode.value = local.darkMode;
        }
      } catch (e) {}
    }
  } catch (e) {
    console.warn('加载配置文件失败，使用默认值', e);
  }
};

/** 保存当前配置 */
const persistConfig = async function () {
  await fetch('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      monitorInterval: monitorInterval.value,
      statsInterval: statsInterval.value,
      monitorRoomList: monitorRoomList.value,
      enableRecording: enableRecording.value,
      darkMode: darkMode.value,
      timedBWEnabled: timedBWEnabled.value,
      timedBWStart: timedBWStart.value,
      timedBWEnd: timedBWEnd.value,
      timedBWDefault: timedBWDefault.value,
      videoMode: videoMode.value,
      forceLatestScroll: forceLatestScroll.value
    })
  });
  try {
    const save = {
      monitorInterval: monitorInterval.value,
      statsInterval: statsInterval.value,
      monitorRoomList: monitorRoomList.value,
      enableRecording: enableRecording.value,
      darkMode: darkMode.value,
      timedBWEnabled: timedBWEnabled.value,
      timedBWStart: timedBWStart.value,
      timedBWEnd: timedBWEnd.value,
      timedBWDefault: timedBWDefault.value,
      videoMode: videoMode.value,
      forceLatestScroll: forceLatestScroll.value
    };
    localStorage.setItem('dycast.settings', JSON.stringify(save));
  } catch (e) {}
};
// 根据 URL 初始化单房间监控页（用于多房间分页面监控）
const initMonitorPageByQuery = function () {
  if (!monitorRoomFromQuery) return;
  roomNum.value = monitorRoomFromQuery;
  monitorRoomList.value = monitorRoomFromQuery;
};

// 一启动就加载配置
initMonitorPageByQuery();
loadConfig();

// 启动后自动连接默认房间
const autoConnectOnStart = async function () {
  // 等待配置加载完成
  await loadConfig();
  // 如果 URL 参数指定了房间号，优先使用
  if (monitorRoomFromQuery) {
    roomNum.value = monitorRoomFromQuery;
  } else if (!roomNum.value) {
    // 使用配置文件中的默认房间号
    const rooms = getConfiguredMonitorRooms();
    if (rooms.length > 0) {
      roomNum.value = rooms[0];
    }
  }
  // 延迟一下确保 DOM 已渲染，然后自动连接
  if (roomNum.value) {
    setTimeout(() => {
      connectLive();
    }, 800);
  }
};

// 页面加载完成后自动连接（非子监控页才自动连接）
if (!isMonitorChildPage) {
  onMounted(() => {
    autoConnectOnStart();
  });
}

// 心跳：每 5 秒通知服务端页面还在线
setInterval(() => {
  fetch('/api/heartbeat').catch(() => {});
}, 5000);
// 立即发一次心跳
fetch('/api/heartbeat').catch(() => {});

/** ===== 会话自动恢复 ===== */
let sessionTimer: ReturnType<typeof setInterval> | null = null;
const SESSION_SAVE_INTERVAL = 5000; // 每 5 秒自动保存
const SESSION_RESTORE_MAX_AGE_MS = 24 * 60 * 60 * 1000; // 超过 24 小时不自动恢复
let sessionStartTime = ''; // 本次录制的开始时间，确保同一场写同一个文件

/** 解析会话保存时间（优先 savedAt，其次统计/弹幕末条时间） */
const resolveSessionSavedAtMs = function (session: any): number | null {
  if (session?.savedAt) {
    const savedAtMs = Date.parse(session.savedAt);
    if (!Number.isNaN(savedAtMs)) return savedAtMs;
  }
  if (Array.isArray(session?.stats) && session.stats.length > 0) {
    const last = session.stats[session.stats.length - 1];
    if (typeof last?.time === 'number') return last.time;
  }
  if (Array.isArray(session?.casts) && session.casts.length > 0) {
    const last = session.casts[session.casts.length - 1];
    if (typeof last?.timestamp === 'number') return last.timestamp;
  }
  return null;
};

/** 保存会话到磁盘 */
const saveSession = async function () {
  if (allCasts.length === 0 && statsSnapshots.length === 0) return;
  try {
    const session = {
      startTime: sessionStartTime,
      roomId: roomNum.value,
      nickname: nickname.value,
      avatar: avatar.value,
      cover: cover.value,
      title: title.value,
      followCount: followCount.value,
      memberCount: memberCount.value,
      totalUserCount: userCount.value,
      likeCount: likeCount.value,
      casts: allCasts,
      stats: statsSnapshots,
      savedAt: new Date().toISOString()
    };
    await fetch('/api/session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(session)
    });
  } catch (e) {
    console.warn('保存会话失败', e);
  }
};

/** 清除会话 */
const clearSession = async function () {
  let remoteCleared = true;
  try {
    await fetch('/api/session', { method: 'DELETE' });
  } catch {
    remoteCleared = false;
  }
  sessionStartTime = '';
  clearMessageList(); // 彻底清空本地数据，防止 beforeunload 再次保存
  if (!remoteCleared) {
    addConsoleMessage('会话文件清除失败，但页面信息已清空');
    SkMessage.warning('会话文件清除失败，但页面已清空');
  }
};

/** 二次确认后清除页面与会话 */
const confirmClearCurrentInfo = async function () {
  if (connectStatus.value === 1) {
    SkMessage.warning('录制中请先断开连接后再清除');
    return;
  }
  const ok = window.confirm('确认清除当前页面信息和会话缓存吗？此操作不可恢复。');
  if (!ok) return;
  stopReplay();
  await clearSession();
  addConsoleMessage('已清除当前页面信息和会话缓存');
  SkMessage.success('已清除当前信息');
};

/** 开始定期保存会话 */
const startSessionSave = function () {
  stopSessionSave();
  sessionTimer = setInterval(saveSession, SESSION_SAVE_INTERVAL);
};

/** 停止定期保存会话 */
const stopSessionSave = function () {
  if (sessionTimer) {
    clearInterval(sessionTimer);
    sessionTimer = null;
  }
};

/** 恢复上次的会话数据 */
const restoreSession = async function () {
  try {
    const res = await fetch('/api/session');
    if (!res.ok) return;
    const session = await res.json();
    if (!session || (!session.casts?.length && !session.stats?.length)) return;

    const sessionSavedAtMs = resolveSessionSavedAtMs(session);
    if (sessionSavedAtMs != null && Date.now() - sessionSavedAtMs > SESSION_RESTORE_MAX_AGE_MS) {
      const staleAt = new Date(sessionSavedAtMs).toLocaleString();
      addConsoleMessage(`检测到缓存会话已超过 24 小时（${staleAt}），已跳过自动恢复`);
      return;
    }

    const castCount = session.casts?.length || 0;
    const statsCount = session.stats?.length || 0;
    const savedAt = session.savedAt ? new Date(session.savedAt).toLocaleString() : '未知时间';

    // 恢复直播间信息
    if (session.nickname) nickname.value = session.nickname;
    if (session.avatar) avatar.value = session.avatar;
    if (session.cover) cover.value = session.cover;
    if (session.title) title.value = session.title;
    if (session.roomId) roomNum.value = session.roomId;
    if (session.followCount) followCount.value = `${session.followCount}`;
    if (session.memberCount) memberCount.value = `${session.memberCount}`;
    if (session.totalUserCount) userCount.value = `${session.totalUserCount}`;
    if (session.likeCount) likeCount.value = `${session.likeCount}`;

    // 恢复弹幕数据
    if (session.casts && session.casts.length > 0) {
      allCasts.push(...session.casts);
      const mainCasts: DyMessage[] = [];
      const otherCasts: DyMessage[] = [];
      for (const msg of session.casts) {
        const m = msg.method;
        if (m === CastMethod.CHAT || m === CastMethod.GIFT || m === CastMethod.EMOJI_CHAT) {
          mainCasts.push(msg);
        } else {
          otherCasts.push(msg);
        }
      }
      if (castRef.value) castRef.value.appendCasts(mainCasts);
      if (otherRef.value) otherRef.value.appendCasts(otherCasts);
    }

    // 恢复统计数据
    if (session.stats && session.stats.length > 0) {
      statsSnapshots.push(...session.stats);
    }
    rebuildGiftRankList(allCasts);

    addConsoleMessage(`已恢复上次会话（${savedAt}）：${castCount} 条弹幕，${statsCount} 条统计`);
    SkMessage.success(`已恢复 ${castCount} 条弹幕数据`);
    // 恢复录制开始时间
    if (session.startTime) sessionStartTime = session.startTime;
  } catch (e) {
    console.warn('恢复会话失败', e);
  }
};
// 页面加载时尝试恢复
restoreSession();

// 页面关闭前保存会话
window.addEventListener('beforeunload', () => {
  if (allCasts.length > 0 || statsSnapshots.length > 0) {
    const session = {
      startTime: sessionStartTime,
      roomId: roomNum.value,
      nickname: nickname.value,
      avatar: avatar.value,
      cover: cover.value,
      title: title.value,
      followCount: followCount.value,
      memberCount: memberCount.value,
      totalUserCount: userCount.value,
      likeCount: likeCount.value,
      casts: allCasts,
      stats: statsSnapshots,
      savedAt: new Date().toISOString()
    };
    navigator.sendBeacon('/api/session', JSON.stringify(session));
  }
});

/** 获取有效的房间号列表 */
const getConfiguredMonitorRooms = function (): string[] {
  const list = monitorRoomList.value
    .split('\n')
    .map(s => s.trim())
    .filter(s => s.length > 0);
  // 如果列表为空，使用主界面输入的房间号
  if (list.length === 0 && roomNum.value) {
    return [roomNum.value];
  }
  return Array.from(new Set(list));
};

/** 获取当前页面实际参与监控的房间号列表 */
const getMonitorRooms = function (): string[] {
  if (isMonitorChildPage && monitorRoomFromQuery) {
    return [monitorRoomFromQuery];
  }
  if (monitorMainRoom) return [monitorMainRoom];
  return getConfiguredMonitorRooms();
};

const openMonitorPages = function (rooms: string[]): number {
  let opened = 0;
  // 先清理可能遗留的已关闭窗口引用
  for (let i = monitorChildWindows.length - 1; i >= 0; i--) {
    const w = monitorChildWindows[i];
    if (!w || w.closed) monitorChildWindows.splice(i, 1);
  }
  const baseParams = new URLSearchParams(window.location.search);
  for (const room of rooms) {
    const params = new URLSearchParams(baseParams);
    params.set('monitorRoom', room);
    params.set('autoMonitor', '1');
    params.set('monitorChild', '1');
    const targetUrl = `${window.location.origin}${window.location.pathname}?${params.toString()}`;
    const child = window.open(targetUrl, '_blank');
    if (child) {
      monitorChildWindows.push(child);
      opened++;
    }
  }
  return opened;
};

/** 开始监控 */
const startMonitor = function () {
  const rooms = isMonitorChildPage ? getMonitorRooms() : getConfiguredMonitorRooms();
  if (rooms.length === 0) {
    SkMessage.warning('请先输入房间号或在设置中配置监控房间号');
    return;
  }
  if (!isMonitorChildPage) monitorMainRoom = '';
  if (!isMonitorChildPage && rooms.length > 1) {
    const mainRoom = rooms[0];
    const childRooms = rooms.slice(1);
    monitorMainRoom = mainRoom;
    roomNum.value = mainRoom;
    const opened = openMonitorPages(childRooms);
    if (opened < childRooms.length) {
      addConsoleMessage(`主页面监控[${mainRoom}]，子页面仅打开 ${opened}/${childRooms.length}，其余可能被浏览器拦截`);
      SkMessage.warning(`主页面监控已开启，子页面仅打开 ${opened}/${childRooms.length}`);
    } else {
      addConsoleMessage(`主页面监控[${mainRoom}]，并已打开 ${opened} 个子监控页面`);
      SkMessage.success(`监控已开启：主页面 + ${opened} 个子页面`);
    }
  }
  isMonitoring.value = true;
  monitorRoomIndex = 0;
  const activeRooms = getMonitorRooms();
  const roomsStr = activeRooms.length === 1 ? activeRooms[0] : `${activeRooms.length} 个房间`;
  addConsoleMessage(`已开始监控 [${roomsStr}]，每 ${monitorInterval.value / 1000} 秒检查一次`);
  SkMessage.success(`监控已开启，每 ${monitorInterval.value / 1000} 秒检查`);
  // 立即尝试一次
  tryMonitorConnect();
  monitorTimer = setInterval(tryMonitorConnect, monitorInterval.value);
};

/** 停止监控 */
const stopMonitor = function () {
  isMonitoring.value = false;
  if (monitorTimer) {
    clearInterval(monitorTimer);
    monitorTimer = null;
  }
  if (!isMonitorChildPage) monitorMainRoom = '';
  if (monitorChildWindows.length > 0) {
    for (const child of monitorChildWindows) {
      try {
        if (child && !child.closed) child.close();
      } catch {}
    }
    monitorChildWindows.length = 0;
  }
  addConsoleMessage('监控已停止');
  SkMessage.info('监控已停止');
};

/** 切换监控 */
const toggleMonitor = function () {
  if (isReplaying.value) {
    SkMessage.warning('回放模式下不能开启监控');
    return;
  }
  if (isMonitoring.value) stopMonitor();
  else startMonitor();
};

/** 监控检查：如果未连接则轮询尝试连接 */
const tryMonitorConnect = function () {
  if (connectStatus.value === 1) {
    // 已连接，跳过
    return;
  }
  const rooms = getMonitorRooms();
  if (rooms.length === 0) return;
  // 确保索引在范围内
  if (monitorRoomIndex >= rooms.length) monitorRoomIndex = 0;
  const targetRoom = rooms[monitorRoomIndex];
  // 更新主界面房间号
  roomNum.value = targetRoom;
  addConsoleMessage(`监控轮询 [${monitorRoomIndex + 1}/${rooms.length}]：尝试连接 [${targetRoom}]...`);
  // 移动到下一个房间号
  monitorRoomIndex = (monitorRoomIndex + 1) % rooms.length;
  connectLive();
};

/** 设置弹窗 */
const settingsRef = useTemplateRef('settingsRef');

const openSettings = function () {
  if (settingsRef.value) (settingsRef.value as any).open();
};

const handleSettingsUpdate = function (
  newMonitorInterval: number,
  newStatsInterval: number,
  newRoomList: string,
  newEnableRecording: boolean,
  newDarkMode: boolean,
  newTimedBWEnabled: boolean,
  newTimedBWStart: string,
  newTimedBWEnd: string,
  newTimedBWDefault: string,
  newVideoMode: string,
  newForceLatestScroll: boolean
) {
  monitorInterval.value = newMonitorInterval;
  statsInterval.value = newStatsInterval;
  monitorRoomList.value = newRoomList;
  enableRecording.value = newEnableRecording;
  darkMode.value = newDarkMode;
  timedBWEnabled.value = !!newTimedBWEnabled;
  timedBWStart.value = newTimedBWStart || '07:00';
  timedBWEnd.value = newTimedBWEnd || '19:00';
  timedBWDefault.value = newTimedBWDefault || 'black';
  videoMode.value = newVideoMode || 'normal';
  forceLatestScroll.value = !!newForceLatestScroll;
  // 视频模式优先级高：如果视频被关闭，则不能开启录制
  if (videoMode.value === 'off' && enableRecording.value) {
    enableRecording.value = false;
    addConsoleMessage('已关闭视频模式，录制功能已被自动关闭');
  }
  // 持久化到配置文件
  persistConfig().catch(e => console.warn('保存配置文件失败', e));
  const rooms = getMonitorRooms();
  addConsoleMessage(`设置已更新：监控间隔 ${newMonitorInterval / 1000}秒，采集间隔 ${newStatsInterval / 1000}秒，监控房间 ${rooms.length} 个。`);
  SkMessage.success('设置已保存');
  // 如果监控中，重启定时器
  if (isMonitoring.value) {
    if (!isMonitorChildPage && monitorChildWindows.length > 0) {
      addConsoleMessage('主控页监控配置已更新，请先停止监控再重新开启，以重新分发子监控页面');
    } else {
      if (monitorTimer) clearInterval(monitorTimer);
      monitorRoomIndex = 0;
      monitorTimer = setInterval(tryMonitorConnect, monitorInterval.value);
    }
  }
  // 如果统计采集中，重启定时器
  if (statsTimer) {
    clearInterval(statsTimer);
    statsTimer = setInterval(captureStatsSnapshot, statsInterval.value);
  }
  // 评估主题（定时黑白可能会覆盖深色开关）
  evaluateTheme();
};

/** ===== 弹幕回放模式 ===== */
const isReplaying = ref(false);
const replayFileInput = ref<HTMLInputElement | null>(null);
let replayTimers: ReturnType<typeof setTimeout>[] = [];
/** 回放视频状态 */
const replayVideoUrl = ref('');
const replayVideoRef = useTemplateRef<HTMLVideoElement>('replayVideoRef');
let replayMpegtsPlayer: mpegts.Player | null = null;
let replayRecordingMeta: any = null;
/** 回放进度状态 */
let replaySortedMessages: DyMessage[] = [];
let replayFirstTs = 0;
let replayLastTs = 0;
const replayCurrentMs = ref(0);
const replayTotalMs = ref(0);
let replayStartRealTime = 0; // 回放开始的真实时间
let replayOffsetMs = 0;      // 当前回放偏移量
let replayProgressTimer: ReturnType<typeof setInterval> | null = null;
let isVideoSeekingByCode = false;
const replayPaused = ref(false);
let replayPausedAtMs = 0;
const replaySpeed = ref(1.0);

/** 切换倍速 */
const onReplaySpeedChange = function () {
  if (!isReplaying.value) return;
  // 倍速变化时，视频倍速更新
  if (replayVideoRef.value) {
    replayVideoRef.value.playbackRate = replaySpeed.value;
  }
  // 如果正在播放，重制当前的定时器来反映新的倍速
  if (!replayPaused.value) {
    seekReplay(replayCurrentMs.value);
  }
};

/** 格式化时长 mm:ss */
const formatDuration = function (ms: number): string {
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
};

/** 拖动进度条跳转（防抖 150ms） */
let seekDebounceTimer: ReturnType<typeof setTimeout> | null = null;
let seekVersion = 0;

const onReplaySeek = function (e: Event) {
  const target = e.target as HTMLInputElement;
  const targetMs = parseInt(target.value, 10) || 0;
  // 立即更新进度条显示（视觉反馈）
  replayCurrentMs.value = targetMs;
  // 暂停进度自动更新
  stopReplayProgress();
  // 防抖：只在用户停止拖动后执行
  if (seekDebounceTimer) clearTimeout(seekDebounceTimer);
  seekDebounceTimer = setTimeout(() => {
    seekDebounceTimer = null;
    replayPaused.value = false;
    seekReplay(targetMs);
  }, 150);
};

/** 暂停/继续回放 */
const toggleReplayPause = function () {
  if (!isReplaying.value) return;
  if (replayPaused.value) {
    // 继续：从当前位置重新调度
    replayPaused.value = false;
    seekReplay(replayCurrentMs.value);
    // 恢复视频播放
    if (replayVideoRef.value) {
      replayVideoRef.value.playbackRate = replaySpeed.value;
      replayVideoRef.value.play().catch(() => {});
    }
  } else {
    // 暂停：清除所有定时器
    replayPaused.value = true;
    replayPausedAtMs = replayCurrentMs.value;
    for (const t of replayTimers) clearTimeout(t);
    replayTimers = [];
    stopReplayProgress();
    // 暂停视频
    if (replayVideoRef.value) replayVideoRef.value.pause();
  }
};

/** 回放视频被用户手动拖动时，强制恢复到进度条位置 */
const onReplayVideoSeeking = function () {
  if (isVideoSeekingByCode || !isReplaying.value) return;
  const videoEl = replayVideoRef.value;
  if (!videoEl) return;
  let expectedSec = replayCurrentMs.value / 1000;
  if (replayRecordingMeta && replayRecordingMeta.startTimestamp && replayFirstTs) {
    expectedSec = Math.max(0, (replayFirstTs - replayRecordingMeta.startTimestamp) / 1000) + replayCurrentMs.value / 1000;
  }
  if (Math.abs(videoEl.currentTime - expectedSec) > 1) {
    videoEl.currentTime = expectedSec;
  }
};

/** 跳转回放到指定位置（ms，相对于第一条弹幕） */
const seekReplay = async function (targetMs: number) {
  if (!isReplaying.value || replaySortedMessages.length === 0) return;
  const myVersion = ++seekVersion;

  // 1. 清除现有定时器
  for (const t of replayTimers) clearTimeout(t);
  replayTimers = [];

  // 2. 清空弹幕面板
  allCasts.length = 0;
  giftRankMap.clear();
  giftRankList.value = [];
  if (castRef.value) castRef.value.clearCasts();
  if (otherRef.value) otherRef.value.clearCasts();
  await nextTick();
  // 如果已有更新的 seek，放弃当前
  if (myVersion !== seekVersion) return;

  const sorted = replaySortedMessages;
  const total = sorted.length;
  const firstTs = replayFirstTs;
  const targetAbsTs = firstTs + targetMs;

  // 3. 立即渲染 targetMs 之前的所有弹幕
  const pastChatMsgs: DyMessage[] = [];
  const pastOtherMsgs: DyMessage[] = [];
  let seekIdx = 0;
  for (let i = 0; i < total; i++) {
    const ts = sorted[i].timestamp || 0;
    if (ts > targetAbsTs) {
      seekIdx = i;
      break;
    }
    if (i === total - 1) {
      seekIdx = total;
    }
    const replayMsg: DyMessage = { ...sorted[i], replayMark: true };
    allCasts.push(replayMsg);
    if (replayMsg.room) setRoomCount(replayMsg.room);
    const method = replayMsg.method;
    if (method === CastMethod.GIFT && shouldCountGiftForRank(replayMsg)) {
      addGiftRankByMessage(replayMsg);
    }
    if (method === CastMethod.CHAT || method === CastMethod.GIFT || method === CastMethod.EMOJI_CHAT) {
      pastChatMsgs.push(replayMsg);
    } else {
      pastOtherMsgs.push(replayMsg);
    }
  }
  // 批量渲染（只渲染最近 N 条到面板，避免大量数据卡住虚拟滚动）
  const SEEK_CHAT_DISPLAY_LIMIT = 200;
  const SEEK_OTHER_DISPLAY_LIMIT = 100;
  if (pastChatMsgs.length > 0 && castRef.value) {
    const displayChat = pastChatMsgs.length > SEEK_CHAT_DISPLAY_LIMIT
      ? pastChatMsgs.slice(-SEEK_CHAT_DISPLAY_LIMIT)
      : pastChatMsgs;
    castRef.value.appendCasts(displayChat);
  }
  if (pastOtherMsgs.length > 0 && otherRef.value) {
    const displayOther = pastOtherMsgs.length > SEEK_OTHER_DISPLAY_LIMIT
      ? pastOtherMsgs.slice(-SEEK_OTHER_DISPLAY_LIMIT)
      : pastOtherMsgs;
    otherRef.value.appendCasts(displayOther);
  }
  refreshGiftRankList();
  // 等 DOM 更新后滚动到底部，确保后续弹幕能继续自动滚动
  await nextTick();
  if (castRef.value) castRef.value.scrollToBottom();
  if (otherRef.value) otherRef.value.scrollToBottom();

  // 4. 分块设置后续弹幕定时器以避免卡死
  replayOffsetMs = targetMs;
  replayStartRealTime = Date.now();
  
  let currentIndex = seekIdx;
  const CHUNK_SIZE = 10000;
  
  const scheduleNextChunk = () => {
    if (!isReplaying.value || myVersion !== seekVersion) return;
    
    const endIdx = Math.min(currentIndex + CHUNK_SIZE, total);
    for (let i = currentIndex; i < endIdx; i++) {
      const msg = sorted[i];
      // 根据倍速缩小延迟时间
      const msgDelay = Math.max(0, (msg.timestamp || 0) - targetAbsTs) / replaySpeed.value;
      const idx = i;
      const timer = setTimeout(() => {
        if (!isReplaying.value) return;
        const replayMsg: DyMessage = { ...msg, replayMark: true };
        if (replayMsg.room) setRoomCount(replayMsg.room);
        allCasts.push(replayMsg);
        const method = replayMsg.method;
        if (method === CastMethod.GIFT && shouldCountGiftForRank(replayMsg)) {
          addGiftRankByMessage(replayMsg);
          refreshGiftRankList();
        }
        if (method === CastMethod.CHAT || method === CastMethod.GIFT || method === CastMethod.EMOJI_CHAT) {
          if (castRef.value) castRef.value.appendCasts([replayMsg]);
        } else {
          if (otherRef.value) otherRef.value.appendCasts([replayMsg]);
        }
        if (idx === total - 1) {
          isReplaying.value = false;
          addConsoleMessage(`回放完成，共 ${total} 条`);
          SkMessage.success('回放完成');
          stopReplayProgress();
        }
      }, msgDelay);
      replayTimers.push(timer);
    }
    
    currentIndex = endIdx;
    if (currentIndex < total) {
      setTimeout(scheduleNextChunk, 10);
    }
  };
  
  scheduleNextChunk();

  // 重启进度条自动更新
  startReplayProgress();

  // 5. 同步视频
  if (replayMpegtsPlayer && replayVideoRef.value) {
    const videoEl = replayVideoRef.value;
    let videoSeekSec = targetMs / 1000;
    if (replayRecordingMeta && replayRecordingMeta.startTimestamp && replayFirstTs) {
      const baseOffset = Math.max(0, (replayFirstTs - replayRecordingMeta.startTimestamp) / 1000);
      videoSeekSec = baseOffset + targetMs / 1000;
    }
    if (videoEl.duration && videoSeekSec < videoEl.duration) {
      isVideoSeekingByCode = true;
      videoEl.currentTime = videoSeekSec;
      videoEl.playbackRate = replaySpeed.value;
      setTimeout(() => { isVideoSeekingByCode = false; }, 200);
    }
  }

  // 6. 更新进度
  replayCurrentMs.value = targetMs;
  addConsoleMessage(`跳转到 ${formatDuration(targetMs)}，已渲染 ${seekIdx} 条弹幕`);
};

/** 启动进度条自动更新 */
const startReplayProgress = function () {
  stopReplayProgress();
  replayStartRealTime = Date.now();
  replayProgressTimer = setInterval(() => {
    if (!isReplaying.value) {
      stopReplayProgress();
      return;
    }
    // 根据倍速推进进度条
    const elapsed = (Date.now() - replayStartRealTime) * replaySpeed.value;
    replayCurrentMs.value = Math.min(replayOffsetMs + elapsed, replayTotalMs.value);
  }, 200);
};

const stopReplayProgress = function () {
  if (replayProgressTimer) {
    clearInterval(replayProgressTimer);
    replayProgressTimer = null;
  }
};

/** 触发文件选择 */
const triggerReplayImport = function () {
  if (connectStatus.value === 1) {
    SkMessage.warning('录制中不能开启回放，请先断开连接');
    return;
  }
  if (isMonitoring.value) {
    SkMessage.warning('监控中不能开启回放，请先停止监控');
    return;
  }
  if (replayFileInput.value) {
    replayFileInput.value.value = '';
    replayFileInput.value.click();
  }
};

/** 处理文件选择（支持多文件，JSON 和 ZIP） */
const handleReplayFileSelect = async function (e: Event) {
  const input = e.target as HTMLInputElement;
  const files = input.files;
  if (!files || files.length === 0) return;

  const allMessages: DyMessage[] = [];
  let bundleInfo: any = null;

  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    try {
      if (file.name.endsWith('.zip')) {
        // 解压 ZIP 文件
        const zip = await JSZip.loadAsync(file);
        // 提取 data.json
        const dataFile = zip.file('data.json');
        if (dataFile) {
          const jsonStr = await dataFile.async('string');
          const parsed = JSON.parse(jsonStr);
          if (parsed && parsed._bundle && Array.isArray(parsed.casts)) {
            bundleInfo = parsed;
            allMessages.push(...parsed.casts);
          }
        }
        // 提取封面图片
        const coverFile = zip.file(/^cover\./)?.[0];
        if (coverFile) {
          const blob = await coverFile.async('blob');
          const coverUrl = URL.createObjectURL(blob);
          if (bundleInfo) bundleInfo.cover = coverUrl;
        }
        // 提取录制视频（recording.flv）
        const recFile = zip.file('recording.flv');
        if (recFile) {
          const recBlob = await recFile.async('blob');
          // 释放旧 URL
          if (replayVideoUrl.value) URL.revokeObjectURL(replayVideoUrl.value);
          replayVideoUrl.value = URL.createObjectURL(recBlob);
          addConsoleMessage(`已提取录制视频: recording.flv (${Math.round(recBlob.size / 1024 / 1024 * 10) / 10}MB)`);
        }
        // 提取录制元数据
        const recMetaFile = zip.file('recording_meta.json');
        if (recMetaFile) {
          try {
            const metaStr = await recMetaFile.async('string');
            replayRecordingMeta = JSON.parse(metaStr);
          } catch { /* ignore */ }
        }
        addConsoleMessage(`已解压 ZIP 打包：${file.name}`);
      } else {
        // JSON 文件
        const text = await file.text();
        const parsed = JSON.parse(text);
        if (parsed && parsed._bundle && Array.isArray(parsed.casts)) {
          bundleInfo = parsed;
          allMessages.push(...parsed.casts);
          addConsoleMessage(`已识别打包文件：${file.name}`);
        } else if (parsed && parsed.casts && parsed.savedAt) {
          // 会话文件格式（dycast.session.json）
          bundleInfo = parsed;
          allMessages.push(...parsed.casts);
          addConsoleMessage(`已识别会话文件：${file.name}（${parsed.casts.length} 条弹幕）`);
        } else if (parsed && parsed.snapshots && parsed.nickname) {
          if (!bundleInfo) bundleInfo = parsed;
          addConsoleMessage(`已识别统计文件：${file.name}`);
        } else if (Array.isArray(parsed)) {
          allMessages.push(...parsed);
        }
      }
    } catch (err) {
      CLog.error(`解析文件 ${file.name} 失败:`, err);
    }
  }

  if (allMessages.length === 0) {
    SkMessage.warning('文件为空或格式不正确');
    return;
  }
  // 多文件合并提示
  if (files.length > 1) {
    addConsoleMessage(`已导入 ${files.length} 个文件，合并 ${allMessages.length} 条弹幕`);
  }
  // 保存录制视频数据（防止被 stopReplay 清除）
  const savedVideoUrl = replayVideoUrl.value;
  const savedMeta = replayRecordingMeta;
  replayVideoUrl.value = '';  // 先清空引用，由 startReplay 重新设置
  replayRecordingMeta = null;
  
  // 启动回放，并传入采集到的元数据（主播名、封面等）
  startReplay(allMessages, bundleInfo, savedVideoUrl, savedMeta);
};

/** 开始回放 */
const startReplay = async function (messages: DyMessage[], bundleInfo: any = null, videoUrl = '', recordingMeta: any = null) {
  // 先清理
  stopReplay();
  clearMessageList();

  // 恢复录制视频数据（stopReplay 会清除旧值，这里重新设置）
  if (videoUrl) {
    replayVideoUrl.value = videoUrl;
    replayRecordingMeta = recordingMeta;
  }
  
  // 恢复回放时的直播信息（必须在 clearMessageList 之后设置，否则会被清空）
  if (bundleInfo) {
    if (bundleInfo.nickname) nickname.value = bundleInfo.nickname;
    if (bundleInfo.avatar) avatar.value = bundleInfo.avatar;
    if (bundleInfo.cover) cover.value = bundleInfo.cover;
    if (bundleInfo.title) title.value = bundleInfo.title;
    if (bundleInfo.followCount) followCount.value = `${bundleInfo.followCount}`;
    if (bundleInfo.memberCount) memberCount.value = `${bundleInfo.memberCount}`;
    if (bundleInfo.totalUserCount) userCount.value = `${bundleInfo.totalUserCount}`;
    if (bundleInfo.likeCount) likeCount.value = `${bundleInfo.likeCount}`;
    if (bundleInfo.roomId) roomNum.value = bundleInfo.roomId;
    if (bundleInfo.stats && Array.isArray(bundleInfo.stats)) {
      statsSnapshots.push(...bundleInfo.stats);
    }
  }

  isReplaying.value = true;

  // 按时间戳排序
  const sorted = [...messages].sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
  const total = sorted.length;
  const firstTs = sorted[0]?.timestamp || 0;
  const lastTs = sorted[total - 1]?.timestamp || firstTs;

  // 保存回放状态（用于进度条跳转）
  replaySortedMessages = sorted;
  replayFirstTs = firstTs;
  replayLastTs = lastTs;
  replayTotalMs.value = Math.max(0, lastTs - firstTs);
  replayCurrentMs.value = 0;
  replayOffsetMs = 0;

  // 如果有录制视频，等 DOM 更新后初始化播放器
  if (replayVideoUrl.value) {
    await nextTick();
    const videoEl = replayVideoRef.value;
    if (videoEl) {
      // 先销毁旧播放器
      if (replayMpegtsPlayer) {
        try { replayMpegtsPlayer.destroy(); } catch {}
        replayMpegtsPlayer = null;
      }
      if (mpegts.isSupported()) {
        const player = mpegts.createPlayer({
          type: 'flv',
          isLive: false,
          url: replayVideoUrl.value
        });
        player.attachMediaElement(videoEl);
        player.load();
        replayMpegtsPlayer = player;
        // 计算视频起始偏移：录制开始时间 vs 第一条弹幕时间
        if (replayRecordingMeta && replayRecordingMeta.startTimestamp && firstTs) {
          const videoOffsetSec = Math.max(0, (firstTs - replayRecordingMeta.startTimestamp) / 1000);
          player.on(mpegts.Events.MEDIA_INFO, () => {
            if (videoEl.duration && videoOffsetSec < videoEl.duration) {
              videoEl.currentTime = videoOffsetSec;
            }
            videoEl.playbackRate = replaySpeed.value;
            videoEl.play().catch(() => {});
          });
        } else {
          player.on(mpegts.Events.MEDIA_INFO, () => {
            videoEl.playbackRate = replaySpeed.value;
            videoEl.play().catch(() => {});
          });
        }
        addConsoleMessage('录制视频已加载，与弹幕同步回放');
      } else {
        videoEl.src = replayVideoUrl.value;
        videoEl.playbackRate = replaySpeed.value;
        videoEl.play().catch(() => {});
      }
    }
  }

  addConsoleMessage(`开始回放，共 ${total} 条弹幕`);
  SkMessage.success(`开始回放 ${total} 条弹幕`);

  // 分块计算每条消息相对于第一条的延迟以避免大量数据卡死
  let currentIndex = 0;
  const CHUNK_SIZE = 10000;
  const currentSeekVersion = seekVersion;

  const scheduleNextChunk = () => {
    if (!isReplaying.value || currentSeekVersion !== seekVersion) return;
    
    const endIdx = Math.min(currentIndex + CHUNK_SIZE, total);
    for (let i = currentIndex; i < endIdx; i++) {
        const msg = sorted[i];
        // 按照播放倍速缩小实际等待的延迟时间
        const delay = Math.max(0, (msg.timestamp || 0) - firstTs) / replaySpeed.value;
        const idx = i;

        const timer = setTimeout(() => {
          if (!isReplaying.value) return;
          const replayMsg: DyMessage = { ...msg, replayMark: true };
          // 更新房间统计信息
          if (replayMsg.room) setRoomCount(replayMsg.room);
          // 推入渲染（按类型分派到对应面板）
          allCasts.push(replayMsg);
          const method = replayMsg.method;
        if (method === CastMethod.GIFT && shouldCountGiftForRank(replayMsg)) {
          addGiftRankByMessage(replayMsg);
          refreshGiftRankList();
        }
        if (method === CastMethod.CHAT || method === CastMethod.GIFT || method === CastMethod.EMOJI_CHAT) {
          if (castRef.value) castRef.value.appendCasts([replayMsg]);
        } else {
          if (otherRef.value) otherRef.value.appendCasts([replayMsg]);
        }

        // 最后一条
        if (idx === total - 1) {
          isReplaying.value = false;
          addConsoleMessage(`回放完成，共 ${total} 条`);
          SkMessage.success('回放完成');
          stopReplayProgress();
        }
      }, delay);

      replayTimers.push(timer);
    }
    
    currentIndex = endIdx;
    if (currentIndex < total) {
      setTimeout(scheduleNextChunk, 10);
    }
  };
  
  scheduleNextChunk();

  // 启动进度条自动更新
  startReplayProgress();
};

/** 停止回放 */
const stopReplay = function () {
  if (isReplaying.value) {
    addConsoleMessage('回放已停止');
    SkMessage.info('回放已停止');
  }
  isReplaying.value = false;
  for (const t of replayTimers) clearTimeout(t);
  replayTimers = [];
  // 清理回放视频
  if (replayMpegtsPlayer) {
    try {
      replayMpegtsPlayer.pause();
      replayMpegtsPlayer.unload();
      replayMpegtsPlayer.detachMediaElement();
      replayMpegtsPlayer.destroy();
    } catch {}
    replayMpegtsPlayer = null;
  }
  if (replayVideoUrl.value) {
    URL.revokeObjectURL(replayVideoUrl.value);
    replayVideoUrl.value = '';
  }
  replayRecordingMeta = null;
  isPortraitVideo.value = false;
  // 清理进度条状态
  stopReplayProgress();
  replaySortedMessages = [];
  replayFirstTs = 0;
  replayLastTs = 0;
  replayTotalMs.value = 0;
  replayCurrentMs.value = 0;
  replayOffsetMs = 0;
};

/**
 * 验证房间号
 * @param value
 * @returns
 */
function verifyRoomNumber(value: string) {
  const flag = verifyRoomNum(value);
  if (flag) return { flag, message: '' };
  else {
    return { flag, message: '房间号错误' };
  }
}

/**
 * 验证转发地址 WsUrl
 * @param value
 * @returns
 */
function verifyWssUrl(value: string) {
  const flag = verifyWsUrl(value);
  if (flag) return { flag, message: '' };
  else {
    return { flag, message: '转发地址错误' };
  }
}

/** 设置房间号输入框状态 */
const setRoomInputStatus = function (flag?: boolean) {
  if (roomInputRef.value) roomInputRef.value.setStatus(flag);
  if (roomInputVideoOnlyRef && roomInputVideoOnlyRef.value) roomInputVideoOnlyRef.value.setStatus(flag);
};

/** 设置转发地址输入框状态 */
const setRelayInputStatus = function (flag?: boolean) {
  if (relayInputRef.value) relayInputRef.value.setStatus(flag);
  if (relayInputVideoOnlyRef && relayInputVideoOnlyRef.value) relayInputVideoOnlyRef.value.setStatus(flag);
};

/** 模式切换后重建可见弹幕列表，避免组件销毁重建后“看起来丢失历史弹幕” */
const restoreVisibleCastPanels = function () {
  if (isReplaying.value) return;

  const mainCasts = allCasts.filter(msg =>
    msg.method === CastMethod.CHAT ||
    msg.method === CastMethod.GIFT ||
    msg.method === CastMethod.EMOJI_CHAT
  );
  const otherCasts = allCasts.filter(msg =>
    msg.method === CastMethod.SOCIAL ||
    msg.method === CastMethod.LIKE ||
    msg.method === CastMethod.MEMBER ||
    msg.method === CastMethod.CUSTOM ||
    msg.method === CastMethod.CONTROL
  );

  if (castRef.value) {
    castRef.value.clearCasts();
    if (mainCasts.length > 0) castRef.value.appendCasts(mainCasts);
  }
  if (otherRef.value) {
    otherRef.value.clearCasts();
    if (otherCasts.length > 0) otherRef.value.appendCasts(otherCasts);
  }
};

// 切换视频模式时：
// 1) 右侧输入区与仅视频输入区会销毁/重建，需同步按钮状态
// 2) 处理预览流：同一直播优先复用缓存流，仅在无缓存时重新抓流
watch(videoMode, (mode, prevMode) => {
  nextTick(() => {
    if (connectStatus.value === 1) setRoomInputStatus(true);
    if (relayStatus.value === 1) setRelayInputStatus(true);
    restoreVisibleCastPanels();
  });

  if (mode === prevMode) return;
  if (isReplaying.value) return;

  const room = roomNum.value.trim();

  if (mode === 'off') {
    // 仅释放播放器，保留缓存流信息；切回可见模式时可直接复用
    destroyLivePreviewPlayer();
    livePreviewError.value = '';
    return;
  }

  if (!room) return;

  // 从 off 切回可见视频：优先复用缓存流（refresh(false) 会先走复用逻辑）
  if (prevMode === 'off') {
    refreshLivePreview(false);
    return;
  }

  // 切到仅视频时：
  // - 若已有当前房间流，则不刷新
  // - 若当前无流，再补一次抓流
  if (mode === 'video-only' && (!livePreviewStreamUrl.value || livePreviewStreamRoom.value !== room)) {
    refreshLivePreview(true);
  }
});

/**
 * 设置房间统计信息
 * @param room
 * @returns
 */
const setRoomCount = function (room?: LiveRoom) {
  if (!room) return;
  if (room.audienceCount) memberCount.value = `${room.audienceCount}`;
  if (room.followCount) followCount.value = `${room.followCount}`;
  if (room.likeCount) likeCount.value = `${room.likeCount}`;
  if (room.totalUserCount) userCount.value = `${room.totalUserCount}`;
};
/**
 * 设置直播间信息
 * @param info
 */
const setRoomInfo = function (info?: DyLiveInfo) {
  if (!info) return;
  if (info.cover) cover.value = info.cover;
  if (info.title) title.value = info.title;
  if (info.avatar) avatar.value = info.avatar;
  if (info.nickname) nickname.value = info.nickname;
};

const parsePositiveNumber = function (value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : 0;
};

const shouldCountGiftForRank = function (msg?: DyMessage): boolean {
  if (!msg || msg.method !== CastMethod.GIFT) return false;
  return !!msg.gift;
};

const addGiftRankByMessage = function (msg?: DyMessage) {
  if (!msg || !msg.gift || !shouldCountGiftForRank(msg)) return;
  const name = (msg.user?.name || '').trim() || '未知用户';
  const count = parsePositiveNumber(msg.gift.count) || 1;
  const unitPrice = parsePositiveNumber(msg.gift.price);
  const totalValue = count * unitPrice;
  let current = giftRankMap.get(name);
  if (!current) {
    current = { name, avatar: msg.user?.avatar || '', giftCount: 0, totalValue: 0, lastUpdateTime: 0 };
  }
  current.giftCount += count;
  current.totalValue += totalValue;
  current.lastUpdateTime = msg.timestamp || Date.now();
  if (!current.avatar && msg.user?.avatar) current.avatar = msg.user.avatar;
  giftRankMap.set(name, current);
};

const refreshGiftRankList = function () {
  const sorted = Array.from(giftRankMap.values()).sort((a, b) => {
    if (b.totalValue !== a.totalValue) return b.totalValue - a.totalValue;
    if (b.giftCount !== a.giftCount) return b.giftCount - a.giftCount;
    return a.name.localeCompare(b.name, 'zh-CN');
  });
  giftRankList.value = sorted.slice(0, GIFT_RANK_LIMIT);
};

const rebuildGiftRankList = function (casts: DyMessage[]) {
  giftRankMap.clear();
  for (const msg of casts) {
    addGiftRankByMessage(msg);
  }
  refreshGiftRankList();
};

const formatRankNumber = function (num: number): string {
  return num.toLocaleString('zh-CN');
};

/**
 * 处理消息列表
 */
const handleMessages = function (msgs: DyMessage[]) {
  const newCasts: DyMessage[] = [];
  const mainCasts: DyMessage[] = [];
  const otherCasts: DyMessage[] = [];
  let giftRankChanged = false;
  try {
    for (const msg of msgs) {
      const msgId =
        msg.id ||
        [
          msg.method || 'unknown',
          msg.timestamp || 0,
          msg.user?.id || msg.user?.name || '',
          msg.gift?.id || msg.gift?.name || '',
          msg.gift?.count || msg.content || ''
        ].join('_');
      if (castSet.has(msgId)) continue;
      castSet.add(msgId);
      if (!msg.id) msg.id = msgId;
      switch (msg.method) {
        case CastMethod.CHAT:
          newCasts.push(msg);
          mainCasts.push(msg);
          break;
        case CastMethod.GIFT:
          newCasts.push(msg);
          mainCasts.push(msg);
          if (shouldCountGiftForRank(msg)) {
            addGiftRankByMessage(msg);
            giftRankChanged = true;
          }
          break;
        case CastMethod.LIKE:
          newCasts.push(msg);
          otherCasts.push(msg);
          setRoomCount(msg.room);
          break;
        case CastMethod.MEMBER:
          newCasts.push(msg);
          otherCasts.push(msg);
          setRoomCount(msg.room);
          break;
        case CastMethod.SOCIAL:
          newCasts.push(msg);
          otherCasts.push(msg);
          setRoomCount(msg.room);
          break;
        case CastMethod.EMOJI_CHAT:
          newCasts.push(msg);
          mainCasts.push(msg);
          break;
        case CastMethod.ROOM_USER_SEQ:
          setRoomCount(msg.room);
          break;
        case CastMethod.ROOM_STATS:
          setRoomCount(msg.room);
          break;
        case CastMethod.CONTROL:
          if (msg?.room?.status !== RoomStatus.LIVING) {
            // 已经下播
            newCasts.push(msg);
            otherCasts.push(msg);
            disconnectLive(DyCastCloseCode.LIVE_END, '主播已下播');
          }
          break;
      }
    }
  } catch (err) {}
  // 记录
  allCasts.push(...newCasts);
  if (giftRankChanged) refreshGiftRankList();
  if (castRef.value) castRef.value.appendCasts(mainCasts);
  if (otherRef.value) otherRef.value.appendCasts(otherCasts);
  if (relayWs && relayWs.isConnected()) {
    relayWs.send(JSON.stringify(msgs));
  }
};

/**
 * 添加控制台消息
 * @param msg
 */
const addConsoleMessage = function (content: string) {
  if (otherRef.value)
    otherRef.value.appendCasts([
      {
        id: getId(),
        method: CastMethod.CUSTOM,
        content,
        user: { name: '控制台' }
      }
    ]);
};

/**
 * 清理列表和直播间信息
 */
function clearMessageList() {
  castSet.clear();
  allCasts.length = 0;
  statsSnapshots.length = 0;
  giftRankMap.clear();
  giftRankList.value = [];
  if (castRef.value) castRef.value.clearCasts();
  if (otherRef.value) otherRef.value.clearCasts();

  // 重置直播间信息
  cover.value = '';
  title.value = '*****';
  avatar.value = '';
  nickname.value = '***';
  followCount.value = '*****';
  memberCount.value = '*****';
  userCount.value = '*****';
  likeCount.value = '*****';
  sessionStartTime = '';

  // 清理直播预览
  destroyLivePreviewPlayer();
  livePreviewStreamUrl.value = '';
  livePreviewStreamType.value = '';
  livePreviewStreamRoom.value = '';
  livePreviewError.value = '';
}

/**
 * 连接房间
 */
const connectLive = function (confirmValue?: any, isRetry = false) {
  // 当输入组件状态不同步时，误触“连接”会走到这里；已连接则改为先断开，避免清空当前会话数据
  if (connectStatus.value === 1) {
    addConsoleMessage('检测到当前已连接，先执行断开以防止数据丢失');
    disconnectLive();
    return;
  }
  if (isReplaying.value) {
    SkMessage.warning('回放模式下不能连接直播间');
    return;
  }
  // 如果是不同房间，先清理旧数据（非重连且已有数据）
  if (!isRetry && allCasts.length > 0) {
    clearMessageList();
  }
  try {
    CLog.debug('正在连接:', roomNum.value);
    SkMessage.info(`正在连接：${roomNum.value}`);
    const cast = new DyCast(roomNum.value);
    cast.on('open', (ev, info) => {
      CLog.info('DyCast 房间连接成功');
      SkMessage.success(`房间连接成功[${roomNum.value}]`);
      setRoomInputStatus(true);
      connectStatus.value = 1;
      setRoomInfo(info);
      addConsoleMessage('直播间已连接');
      // 设置本次录制开始时间（如果还没有的话）
      if (!sessionStartTime) {
        const now = new Date();
        const pad = (n: number) => n.toString().padStart(2, '0');
        sessionStartTime = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
      }
      // 开始统计数据采集
      startStatsRecording();
      // 开始定期保存会话
      startSessionSave();
      // 连接成功后自动刷新直播预览
      refreshLivePreview(true);
      // 监控模式下暂停定时器（已连接，无需继续检查）
      if (isMonitoring.value && monitorTimer) {
        clearInterval(monitorTimer);
        monitorTimer = null;
        addConsoleMessage('监控已暂停（已连接直播间）');
      }
    });
    cast.on('error', err => {
      CLog.error('DyCast 连接出错 =>', err);
      SkMessage.error(`连接出错: ${err}`);
      connectStatus.value = 2;
      setRoomInputStatus(false);
    });
    cast.on('close', (code, reason) => {
      CLog.info(`DyCast 房间已关闭[${code}] => ${reason}`);
      connectStatus.value = 3;
      setRoomInputStatus(false);
      switch (code) {
        case DyCastCloseCode.NORMAL:
          SkMessage.success('断开成功');
          break;
        case DyCastCloseCode.LIVE_END:
          SkMessage.info('主播已下播');
          break;
        case DyCastCloseCode.CANNOT_RECEIVE:
          SkMessage.error('无法正常接收信息，已关闭');
          break;
        default:
          SkMessage.info('房间已关闭');
      }
      if (code === DyCastCloseCode.LIVE_END) {
        addConsoleMessage(reason || '主播尚未开播或已下播');
        
        // 下播也尝试保存一次（如果有数据）
        if (allCasts.length > 0) {
          (async () => {
            await stopRecording();
            await autoSaveCastToFile();
            await clearSession();
          })();
        }
      } else {
        if (statusPanelRef.value) addConsoleMessage(`连接已关闭，共持续: ${statusPanelRef.value.getDuration()}`);
        else addConsoleMessage('连接已关闭');
        
        // 断开连接后自动保存数据
        (async () => {
          await stopRecording();
          await autoSaveCastToFile();
          await autoSaveStatsToFile();
          // 导出完成后清除会话文件和本地数据
          await clearSession();
        })();

        // 手动断开时停止监控
        if (isMonitoring.value && code === DyCastCloseCode.NORMAL) {
          stopMonitor();
        }
      }

      // 如果监控模式开启且不是手动停止，恢复定时器继续检查
      if (isMonitoring.value && code !== DyCastCloseCode.NORMAL) {
        if (!monitorTimer) {
          addConsoleMessage(`${monitorInterval.value / 1000} 秒后继续监控轮询...`);
          monitorTimer = setInterval(tryMonitorConnect, monitorInterval.value);
        }
      }

      // 停止统计采集
      stopStatsRecording();
      // 停止会话保存
      stopSessionSave();
    });
    cast.on('message', msgs => {
      handleMessages(msgs);
    });
    cast.on('reconnecting', (count, code, reason) => {
      switch (code) {
        case DyCastCloseCode.CANNOT_RECEIVE:
          // 无法正常接收信息
          SkMessage.warning('无法正常接收弹幕，准备重连中');
          break;
        default:
          CLog.warn('DyCast 重连中 =>', count);
          SkMessage.warning(`正在重连中: ${count}`);
      }
    });
    cast.on('reconnect', ev => {
      CLog.info('DyCast 重连成功');
      SkMessage.success('房间重连完成');
    });
    cast.connect();
    castWs = cast;
    acquireWebLock();
  } catch (err) {
    CLog.error('房间连接过程出错:', err);
    SkMessage.error('房间连接过程出错');
    setRoomInputStatus(false);
    castWs = void 0;
  }
};
/** 断开连接 */
const disconnectLive = function (code?: number | any, reason?: string) {
  const finalCode = typeof code === 'number' ? code : 1000;
  const finalReason = typeof reason === 'string' ? reason : '断开连接';
  if (castWs) castWs.close(finalCode, finalReason);
  releaseWebLock();
};

/** 连接转发房间 */
const relayCast = function () {
  try {
    CLog.info('正在连接转发中 =>', relayUrl.value);
    SkMessage.info(`转发连接中: ${relayUrl.value}`);
    const cast = new RelayCast(relayUrl.value);
    cast.on('open', () => {
      CLog.info(`DyCast 转发连接成功`);
      SkMessage.success(`已开始转发`);
      setRelayInputStatus(true);
      relayStatus.value = 1;
      addConsoleMessage('转发客户端已连接');
      if (castWs) {
        // 发送直播间信息给转发地址
        cast.send(JSON.stringify(castWs.getLiveInfo()));
      }
    });
    cast.on('close', (code, msg) => {
      CLog.info(`(${code})dycast 转发已关闭: ${msg || '未知原因'}`);
      if (code === 1000) SkMessage.info(`已停止转发`);
      else SkMessage.warning(`转发已停止: ${msg || '未知原因'}`);
      setRelayInputStatus(false);
      relayStatus.value = 0;
      addConsoleMessage('转发已关闭');
    });
    cast.on('error', ev => {
      CLog.warn(`dycast 转发出错: ${ev.message}`);
      SkMessage.error(`转发出错了: ${ev.message}`);
      setRelayInputStatus(false);
      relayStatus.value = 2;
    });
    cast.connect();
    relayWs = cast;
  } catch (err) {
    CLog.error('弹幕转发出错:', err);
    SkMessage.error('转发出错: ${err.message}');
    setRelayInputStatus(false);
    relayStatus.value = 2;
    relayWs = void 0;
  }
};
/** 暂停转发 */
const stopRelayCast = function () {
  if (relayWs) relayWs.close(1000);
};

/**
 * 将弹幕转换为纯文本行
 * @param msg 弹幕数据
 * @returns 时间 + 用户名 + 内容
 */
const getMsgLine = function (msg: DyMessage): { timeStr: string; userContent: string } | null {
  if (!msg.content && !msg.gift) return null;
  const timeStr = msg.timestamp
    ? formatDate(new Date(msg.timestamp), 'HH:mm:ss.SSS')
    : '??:??:??.???';
  const name = msg.user?.name || 'unknown';
  let text = msg.content || '';
  if (msg.gift) {
    text = `送出了 ${msg.gift.name} × ${msg.gift.count}`;
  }
  return { timeStr, userContent: `[${name}]：${text}` };
};

/**
 * 将弹幕转换为 TXT 格式
 *  每条弹幕一行：时间|用户：内容
 */
const convertToTxt = function (casts: DyMessage[]): string {
  const lines: string[] = [];
  for (const msg of casts) {
    const line = getMsgLine(msg);
    if (!line) continue;
    lines.push(`${line.timeStr}|${line.userContent}`);
  }
  return lines.join('\n');
};

/**
 * 将统计快照转换为 TXT 格式
 *  每条快照一行：时间|粉丝:x|在线:x|累计:x|点赞:x
 */
const convertStatsToTxt = function (snapshots: StatsSnapshot[]): string {
  const lines: string[] = [];
  for (const s of snapshots) {
    lines.push(`${s.timeStr}|粉丝:${s.followCount}|在线:${s.audienceCount}|累计:${s.totalUserCount}|点赞:${s.likeCount}`);
  }
  return lines.join('\n');
};

/** 将图片 URL 转为 base64（用于封面打包） */
const fetchCoverBase64 = async function (url: string): Promise<string> {
  try {
    const res = await fetch(url);
    const blob = await res.blob();
    return new Promise((resolve) => {
      const reader = new FileReader();
      reader.onloadend = () => resolve(reader.result as string);
      reader.readAsDataURL(blob);
    });
  } catch {
    return url; // 失败则保留原 URL
  }
};

/** 按类型分类并导出的映射表 */
const castTypeMap: { method: CastMethod; label: string; suffix: string }[] = [
  { method: CastMethod.CHAT, label: '弹幕', suffix: '_chat' },
  { method: CastMethod.GIFT, label: '礼物', suffix: '_gift' },
  { method: CastMethod.SOCIAL, label: '关注', suffix: '_social' },
  { method: CastMethod.LIKE, label: '点赞', suffix: '_like' },
  { method: CastMethod.MEMBER, label: '进入', suffix: '_member' }
];

/**
 * 自动保存弹幕到本地文件
 *  - 断开连接后自动触发
 *  - 将所有数据打包到一个 ZIP 文件中
 */
const autoSaveCastToFile = async function () {
  // 先采集一次统计，再做快照，避免异步保存期间被后续清空/覆盖
  captureStatsSnapshot();
  const castsSnapshot = allCasts.slice();
  const statsSnapshot = statsSnapshots.slice();
  const roomIdSnapshot = roomNum.value;
  const streamerSnapshot = nickname.value || '未知主播';
  const avatarSnapshot = avatar.value;
  const coverSnapshot = cover.value;
  const titleSnapshot = title.value;
  const followCountSnapshot = followCount.value;
  const memberCountSnapshot = memberCount.value;
  const userCountSnapshot = userCount.value;
  const likeCountSnapshot = likeCount.value;
  const statsIntervalSnapshot = statsInterval.value;

  const len = castsSnapshot.length;
  if (len <= 0) return;
  const date = formatDate(new Date(), 'yyyy-MM-dd_HHmmss');
  const baseName = `${streamerSnapshot}-${roomIdSnapshot}-${date}-${len}条`;

  try {
    const zip = new JSZip();

    // 1. data.json — 完整打包数据（用于导入恢复）
    const bundle = {
      _bundle: true,
      exportTime: new Date().toISOString(),
      roomId: roomIdSnapshot,
      nickname: streamerSnapshot,
      avatar: avatarSnapshot,
      cover: '',
      title: titleSnapshot,
      followCount: followCountSnapshot,
      memberCount: memberCountSnapshot,
      totalUserCount: userCountSnapshot,
      likeCount: likeCountSnapshot,
      statsInterval: statsIntervalSnapshot,
      casts: castsSnapshot,
      stats: statsSnapshot
    };
    zip.file('data.json', JSON.stringify(bundle, null, 2));

    // 2. 全量弹幕 JSON
    zip.file('全量弹幕.json', JSON.stringify(castsSnapshot, null, 2));

    // 3. 按类型的 TXT 文件
    for (const typeInfo of castTypeMap) {
      const filtered = castsSnapshot.filter(m => m.method === typeInfo.method);
      if (filtered.length === 0) continue;
      zip.file(`${typeInfo.label}.txt`, convertToTxt(filtered));
    }

    // 4. 统计数据
    if (statsSnapshot.length > 0) {
      const statsData = {
        roomId: roomIdSnapshot,
        cover: coverSnapshot,
        title: titleSnapshot,
        avatar: avatarSnapshot,
        nickname: streamerSnapshot,
        interval: statsIntervalSnapshot,
        totalSnapshots: statsSnapshot.length,
        snapshots: statsSnapshot
      };
      zip.file('统计.json', JSON.stringify(statsData, null, 2));
      zip.file('统计.txt', convertStatsToTxt(statsSnapshot));
    }

    // 5. 封面图片
    if (coverSnapshot) {
      try {
        const coverRes = await fetch(coverSnapshot);
        const coverBlob = await coverRes.blob();
        const ext = coverBlob.type.includes('png') ? 'png' : 'jpg';
        zip.file(`cover.${ext}`, coverBlob);
      } catch {
        addConsoleMessage('封面图片下载失败，跳过');
      }
    }

    // 6. 录制视频 — 将当前录制 + 所有分段一起打包到录制 ZIP
    // 将当前正在录制的数据也保存为一个分段
    const currentBlob = getRecordingBlob();
    if (currentBlob && currentBlob.size > 0) {
      recordingSegments.push({
        blob: currentBlob,
        startTimestamp: recordingStartTs,
        endTimestamp: recordingEndTs || Date.now(),
        index: recordingSegments.length
      });
      recordingChunks = [];
    }
    if (recordingSegments.length > 0) {
      const recZip = new JSZip();
      const segMetas: any[] = [];
      for (let si = 0; si < recordingSegments.length; si++) {
        const seg = recordingSegments[si];
        const suffix = recordingSegments.length === 1 ? '' : `_${si + 1}`;
        recZip.file(`recording${suffix}.flv`, seg.blob);
        segMetas.push({
          file: `recording${suffix}.flv`,
          startTimestamp: seg.startTimestamp,
          endTimestamp: seg.endTimestamp,
          startTime: new Date(seg.startTimestamp).toISOString(),
          endTime: new Date(seg.endTimestamp).toISOString(),
          durationMs: seg.endTimestamp - seg.startTimestamp
        });
      }
      recZip.file('recording_meta.json', JSON.stringify({
        segments: segMetas,
        totalSegments: segMetas.length,
        format: 'video/x-flv',
        note: '多段录制，每段对应一次流刷新/画质切换'
      }, null, 2));
      const recZipBlob = await recZip.generateAsync({ type: 'blob' });
      const recName = `${baseName}-录制`;
      await FileSaver.save(recZipBlob, {
        name: recName,
        ext: '.zip',
        mimeType: 'application/zip',
        description: '直播录制压缩包',
        forceFallback: true
      });
      addConsoleMessage(`录制压缩包已保存（${recordingSegments.length}段，${Math.round(recZipBlob.size / 1024 / 1024 * 10) / 10}MB）`);
    }

    // 生成并保存 ZIP
    const zipBlob = await zip.generateAsync({ type: 'blob' });
    const res = await FileSaver.save(zipBlob, {
      name: baseName,
      ext: '.zip',
      mimeType: 'application/zip',
      description: '完整打包数据',
      forceFallback: true
    });
    if (res.success) {
      addConsoleMessage(`完整 ZIP 已保存：${baseName}.zip`);
      SkMessage.success('所有数据已打包保存为 ZIP');
    } else {
      CLog.error('保存 ZIP 失败 =>', res.message);
    }
  } catch (err) {
    CLog.error('保存 ZIP 出错 =>', err);
  }
};

/**
 * 自动保存统计数据到本地文件（JSON + TXT）
 *  - 断开连接后自动触发（如果弹幕保存已包含统计，此函数跳过）
 *  - 全部使用直接下载
 */
const autoSaveStatsToFile = async function () {
  // 统计数据已包含在 ZIP 中，不再单独导出
  addConsoleMessage('统计数据已包含在 ZIP 打包文件中');
};

/** 手动保存统计数据 */
const saveStatsToFile = function () {
  const len = statsSnapshots.length;
  if (len <= 0) {
    SkMessage.warning('暂无统计数据需要保存');
    return;
  }
  const date = formatDate(new Date(), 'yyyy-MM-dd_HHmmss');
  const fileName = `[${roomNum.value}]${date}_stats(${len})`;
  const exportData = {
    roomId: roomNum.value,
    cover: cover.value,
    title: title.value,
    avatar: avatar.value,
    nickname: nickname.value,
    interval: statsInterval.value,
    totalSnapshots: len,
    snapshots: statsSnapshots
  };
  const data = JSON.stringify(exportData, null, 2);
  FileSaver.save(data, {
    name: fileName,
    ext: '.json',
    mimeType: 'application/json',
    description: '直播间统计数据',
    existStrategy: 'new',
    forceFallback: true
  })
    .then(res => {
      if (res.success) {
        SkMessage.success('统计数据保存成功');
      } else {
        SkMessage.error('统计数据保存失败');
        CLog.error('统计数据保存失败 =>', res.message);
      }
    })
    .catch(err => {
      SkMessage.error('统计数据保存出错');
      CLog.error('统计数据保存出错 =>', err);
    });
};

/** 将弹幕手动保存到本地文件 */
const saveCastToFile = function () {
  if (connectStatus.value === 1) {
    SkMessage.warning('请断开连接后再保存');
    return;
  }
  const len = allCasts.length;
  if (len <= 0) {
    SkMessage.warning('暂无弹幕需要保存');
    return;
  }
  const date = formatDate(new Date(), 'yyyy-MM-dd_HHmmss');
  const fileName = `[${roomNum.value}]${date}(${len})`;
  const data = JSON.stringify(allCasts, null, 2);
  FileSaver.save(data, {
    name: fileName,
    ext: '.json',
    mimeType: 'application/json',
    description: '弹幕数据',
    existStrategy: 'new',
    forceFallback: true
  })
    .then(res => {
      if (res.success) {
        SkMessage.success('弹幕保存成功');
      } else {
        SkMessage.error('弹幕保存失败');
        CLog.error('弹幕保存失败 =>', res.message);
      }
    })
    .catch(err => {
      SkMessage.error('弹幕保存出错了');
      CLog.error('弹幕保存出错了 =>', err);
    });
};

// 子监控页自动启动：放在脚本末尾同步触发，避免后台标签页 setTimeout 被节流
if (shouldAutoStartMonitorByQuery) {
  if (!isMonitoring.value) startMonitor();
  shouldAutoStartMonitorByQuery = false;
}
</script>

<style lang="scss" scoped>
$bg: var(--bg);
$bd: var(--bd);
$theme: var(--theme);
$tool: var(--tool);

.index-view {
  position: relative;
  background-color: $bg;
  display: flex;
  width: 100%;
  height: 100%;
  .view-left,
  .view-center,
  .view-right {
    display: flex;
    flex-direction: column;
    height: 100%;
    box-sizing: border-box;
    width: 0;
    flex-shrink: 0;
  }
  .view-left {
    flex-grow: 2.5;
    border-right: 1px solid $bd;
    justify-content: space-between;
  }
  .view-left-bottom {
    width: 100%;
    display: flex;
    flex-direction: column;
    box-sizing: border-box;
    padding: 12px 0;
    .hr {
      height: 0;
      border: 0;
      border-top: 1px solid $bd;
      margin: 5px 0;
    }
  }
  .view-left-tools {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
    box-sizing: border-box;
    padding: 0 12px;
  }
  .view-left-tool {
    font-size: 21px;
    width: 1.2em;
    height: 1.2em;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: $tool;
    transition: color 0.2s ease-in-out, background-color 0.3s ease-in-out, opacity 0.2s ease;
    background-color: transparent;
    border-radius: 0.4em;
    i {
      font-size: 1em;
    }
    &:hover {
      color: #fff;
      background-color: $theme;
    }
    &:active {
      opacity: 0.8;
    }
  }
  .view-left-tool.monitor-active {
    color: #e95464;
    animation: monitor-pulse 1.5s ease-in-out infinite;
    &:hover {
      color: #fff;
      background-color: #e95464;
      animation: none;
    }
  }
  .view-left-tool.tool-disabled {
    opacity: 0.3;
    cursor: not-allowed;
    pointer-events: none;
  }
  .view-left-tool.danger-tool {
    color: #e95464;
    &:hover {
      color: #fff;
      background-color: #e95464;
    }
  }
  @keyframes monitor-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }
  .view-center {
    flex-grow: 4.5;
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 18px 12px;
  }

  /* 仅视频模式：中间占满（并可扩展到右侧），右侧保持仅保留输入 */
  &.video-only {
    .view-center {
      flex-grow: 7;
      padding-right: 12px;
      padding-left: 6px;
      padding-top: 6px;
      padding-bottom: 6px;
      gap: 6px;
    }
    .view-right {
      /* 仅视频 + 竖屏时右侧输入区放大（约为之前 3 倍），保证房间号可读 */
      flex-grow: 2.4;
      min-width: 320px;
      padding-top: 18px;
    }
    .view-rank,
    .view-other {
      display: none !important;
    }
    .view-live-preview {
      /* 仅占用输入区以下的剩余空间，避免超出视口 */
      flex: 1 1 auto;
      height: auto;
      min-height: 0;
      max-height: none;
      border-radius: 6px;
      padding: 0;
      display: flex;
      align-items: stretch;
      justify-content: flex-start;
    }
    .view-live-video-shell {
      /* 直接铺满预览容器，减少黑框 */
      width: 100%;
      height: 100%;
      max-width: none;
      max-height: none;
      box-sizing: border-box;
      margin: 0;
    }
    .view-live-video {
      width: 100%;
      height: 100%;
      // 保持完整画面，避免裁切
      object-fit: contain;
      object-position: center center;
    }
    .view-center-casts {
      display: none !important;
    }
    /* 竖屏下顶格显示 */
    .view-center.portrait-layout {
      flex-direction: column;
      flex-wrap: nowrap;
      .view-live-preview {
        width: 100%;
        align-self: flex-start;
        height: auto;
        flex: 1 1 auto;
      }
    }
    .view-live-preview-header {
      height: 30px;
      padding: 6px 8px;
    }
    .view-live-video {
      object-fit: contain;
      object-position: center center;
    }
  }
  // 竖屏模式：视频和弹幕左右并排
  .view-center.portrait-layout {
    flex-direction: row;
    flex-wrap: nowrap;
    .view-live-preview {
      width: 50%;
      height: 100%;
      max-height: none;
      min-height: 0;
      flex-shrink: 0;
    }
    .view-center-casts {
      width: 50%;
      height: 100%;
      flex-grow: 1;
    }
  }
  .view-center-casts {
    width: 100%;
    min-height: 0;
    height: 0;
    flex-grow: 1;
    display: flex;
  }
  .replay-progress {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 4px;
    box-sizing: border-box;
    background: var(--progress-bg);
    border-radius: 6px;
    flex-shrink: 0;
    .replay-pause-btn {
      width: 26px;
      height: 26px;
      border: none;
      border-radius: 50%;
      background: var(--theme);
      color: #fff;
      font-size: 12px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      transition: opacity 0.2s;
      &:hover { opacity: 0.85; }
    }
    .replay-time {
      font-family: 'dymht', monospace;
      font-size: 0.78rem;
      color: var(--text-rank-head);
      white-space: nowrap;
      min-width: 40px;
      text-align: center;
    }
    .replay-slider {
      flex-grow: 1;
      height: 6px;
      -webkit-appearance: none;
      appearance: none;
      background: var(--progress-track);
      border-radius: 3px;
      outline: none;
      cursor: pointer;
      &::-webkit-slider-thumb {
        -webkit-appearance: none;
        width: 14px;
        height: 14px;
        background: #3b82f6;
        border-radius: 50%;
        cursor: grab;
        border: 2px solid #fff;
        box-shadow: 0 1px 3px rgba(0,0,0,0.2);
      }
      &::-moz-range-thumb {
        width: 14px;
        height: 14px;
        background: #3b82f6;
        border-radius: 50%;
        cursor: grab;
        border: 2px solid #fff;
      }
    }
    .replay-speed-slider {
      width: 60px;
      margin-left: 4px;
      height: 4px;
      -webkit-appearance: none;
      appearance: none;
      background: var(--progress-track);
      border-radius: 2px;
      outline: none;
      cursor: pointer;
      &::-webkit-slider-thumb {
        -webkit-appearance: none;
        width: 10px;
        height: 10px;
        background: #3b82f6;
        border-radius: 50%;
        cursor: grab;
      }
      &::-moz-range-thumb {
        width: 10px;
        height: 10px;
        background: #3b82f6;
        border-radius: 50%;
        cursor: grab;
      }
    }
    .replay-speed-label {
      font-family: 'dymht', monospace;
      font-size: 0.75rem;
      color: var(--cast-time);
      margin-left: 4px;
      min-width: 32px;
    }
  }
  .view-live-preview {
    width: 100%;
    height: 40%;
    min-height: 200px;
    max-height: 380px;
    border: 1px solid $bd;
    border-radius: 8px;
    background-color: var(--bg-card);
    overflow: hidden;
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
  }
  .view-live-preview-header {
    height: 34px;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: space-between;
    box-sizing: border-box;
    padding: 0 10px;
    border-bottom: 1px solid #e3e8eb;
    font-family: 'mkwxy';
    color: #445363;
    font-size: 0.85rem;
    .view-live-link {
      color: var(--link-color);
      text-decoration: none;
      font-size: 0.78rem;
      &:hover {
        text-decoration: underline;
      }
    }
    .view-live-quality {
      font-size: 0.76rem;
      padding: 2px 6px;
      border: 1px solid var(--input-border);
      border-radius: 4px;
      background: var(--input-bg);
      color: var(--text-primary);
      cursor: pointer;
      outline: none;
      &:focus {
        border-color: var(--input-focus);
      }
    }
  }
  .view-live-preview-main {
    width: 100%;
    height: 0;
    flex-grow: 1;
    display: flex;
    position: relative;
    overflow: hidden;
    background: #000;
  }
  .view-live-video-shell {
    width: 100%;
    height: 100%;
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .view-live-video {
    width: 100%;
    height: 100%;
    object-fit: contain;
    background: #000;
  }
  .view-live-overlay {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, 0.55);
    color: #fff;
    font-size: 0.9rem;
    font-family: 'mkwxy';
  }
  .view-live-preview-actions {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .view-live-refresh {
    font-size: 0.76rem;
    padding: 2px 10px;
    border-radius: 4px;
    border: 1px solid $bd;
    background: #fff;
    color: #445363;
    cursor: pointer;
    font-family: 'mkwxy';
    &:hover {
      background: $theme;
      color: #fff;
      border-color: $theme;
    }
    &:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
  }
  .view-live-iframe-shell {
    width: 100%;
    height: 100%;
    position: relative;
    overflow: hidden;
  }
  .view-live-iframe {
    // 把整页直播裁剪为“以视频区域为主”的预览，减少空白浪费
    position: absolute;
    top: -8%;
    left: -20%;
    width: 140%;
    height: 132%;
    border: 0;
    background: #000;
  }
  .view-live-empty {
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #7e8a97;
    font-family: 'mkwxy';
    font-size: 0.9rem;
  }
  .view-right {
    flex-grow: 3;
    border-left: 1px solid $bd;
    padding: 18px 12px;
    gap: 12px;
  }
  .view-input {
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 5px;
  }
  .view-rank {
    width: 100%;
    border: 1px solid $bd;
    border-radius: 8px;
    background-color: var(--bg-card-alt);
    overflow: hidden;
    min-height: 210px;
    max-height: 260px;
    display: flex;
    flex-direction: column;
  }
  .gift-rank-title {
    font-family: 'mkwxy';
    font-size: 0.98rem;
    font-weight: 700;
    color: var(--text-primary);
    box-sizing: border-box;
    padding: 8px 10px 6px;
    border-bottom: 1px solid var(--bd-light);
    background-color: var(--bg-card-alt);
  }
  .gift-rank-head {
    display: grid;
    grid-template-columns: 42px 1.2fr 64px 80px 68px;
    column-gap: 6px;
    align-items: center;
    box-sizing: border-box;
    padding: 6px 10px;
    font-size: 0.73rem;
    color: var(--text-rank-head);
    border-bottom: 1px solid var(--bd-light);
    span {
      text-align: center;
    }
    span:nth-child(3),
    span:nth-child(4) {
      text-align: right;
    }
    span:nth-child(5) {
      text-align: center;
    }
  }
  .gift-rank-list {
    flex-grow: 1;
    overflow: auto;
  }
  .gift-rank-row {
    display: grid;
    grid-template-columns: 42px 1.2fr 64px 80px 68px;
    column-gap: 6px;
    align-items: center;
    box-sizing: border-box;
    padding: 6px 10px;
    font-size: 0.82rem;
    color: var(--text-rank);
    border-bottom: 1px dashed var(--bd-dashed);
    &:last-child {
      border-bottom: none;
    }
    .rank {
      text-align: center;
      font-family: 'dymht';
    }
    .name {
      text-align: center;
      overflow: hidden;
      white-space: nowrap;
      text-overflow: ellipsis;
      padding: 0 4px;
    }
    .count,
    .value {
      text-align: right;
      font-family: 'dymht';
    }
    .time {
      text-align: center;
      font-family: 'dymht';
      font-size: 0.75rem;
      color: var(--text-rank-time);
    }
  }
  .gift-rank-empty {
    color: var(--text-hint);
    font-size: 0.85rem;
    font-family: 'mkwxy';
    text-align: center;
    box-sizing: border-box;
    padding: 20px 12px;
  }
  .view-other {
    display: flex;
    width: 100%;
    height: 0;
    flex-grow: 1;
    min-height: 220px;
    box-sizing: border-box;
  }
}

@media (max-width: 768px) {
  .index-view {
    flex-direction: column;
    height: auto;
    .view-left,
    .view-center,
    .view-right {
      width: 100%;
      flex-grow: 0;
      border: none;
    }
    .view-left {
      margin-top: 250px;
      justify-content: flex-start;
    }
    .view-center {
      height: 100vh;
      gap: 8px;
    }
    .view-center-casts {
      min-height: 420px;
    }
    .view-live-preview {
      min-height: 180px;
      max-height: 280px;
    }
    .view-live-iframe {
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
    }
    .view-right {
      height: 80vh;
    }
    .view-rank {
      max-height: 34vh;
      min-height: 170px;
    }
    .gift-rank-head,
    .gift-rank-row {
      grid-template-columns: 38px 1fr 58px 70px 60px;
      column-gap: 4px;
      font-size: 0.72rem;
    }
    .view-input {
      position: absolute;
      top: 0;
      left: 0;
      box-sizing: border-box;
      padding: 18px 12px;
    }
    .view-left-bottom {
      position: absolute;
      top: 150px;
      left: 0;
    }
  }
}
</style>
