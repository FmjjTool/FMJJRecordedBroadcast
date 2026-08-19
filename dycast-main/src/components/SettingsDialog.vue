<template>
  <Teleport to="body">
    <div v-if="visible" class="settings-overlay" @click.self="close">
      <div class="settings-dialog">
        <div class="settings-header">
          <span class="settings-title">设 置</span>
          <span class="settings-close" @click="close">✕</span>
        </div>
        <div class="settings-body">
          <div class="settings-item">
            <label class="settings-label">弹幕监控间隔（秒）</label>
            <input class="settings-input" type="number" min="5" max="3600" v-model.number="localMonitorInterval" />
          </div>
          <div class="settings-item">
            <label class="settings-label">统计数据采集间隔（秒）</label>
            <input class="settings-input" type="number" min="1" max="60" v-model.number="localStatsInterval" />
          </div>
          <div class="settings-item">
            <label class="settings-label">监控房间列表（每行一个房间号）</label>
            <textarea class="settings-textarea" rows="5" v-model="localRoomList" placeholder="每行一个房间号" />
          </div>
          <div class="settings-item settings-item-row">
            <label class="settings-label">启用直播录制</label>
            <label class="settings-switch">
              <input type="checkbox" v-model="localEnableRecording" />
              <span class="settings-switch-slider"></span>
            </label>
            <span class="settings-hint">开启后，直播预览播放时自动录制视频（FLV 原始流）</span>
          </div>
          <div class="settings-item settings-item-row">
            <label class="settings-label">启用自动深色模式</label>
            <label class="settings-switch">
              <input type="checkbox" v-model="localTimedBWEnabled" />
              <span class="settings-switch-slider"></span>
            </label>
            <span class="settings-hint">勾选后根据时间段自动切换深色/浅色；未勾选时可手动设置深色</span>
          </div>
          <div class="settings-item">
            <div style="display:flex;gap:8px;align-items:center;">
              <input type="time" class="settings-input" v-model="localTimedBWStart" />
              <span style="color:var(--text-secondary)">→</span>
              <input type="time" class="settings-input" v-model="localTimedBWEnd" />
            </div>
            <div style="display:flex;gap:8px;align-items:center;">
              <label class="settings-label" style="min-width:96px;">关闭时默认</label>
              <select class="settings-input" v-model="localTimedBWDefault">
                <option value="black">黑色（深色）</option>
                <option value="white">白色（浅色）</option>
              </select>
            </div>
          </div>
          <div class="settings-item settings-item-row">
            <label class="settings-label">手动深色</label>
            <label class="settings-switch">
              <input type="checkbox" v-model="localDarkMode" :disabled="localTimedBWEnabled" />
              <span class="settings-switch-slider"></span>
            </label>
            <span class="settings-hint">当未启用自动深色时，此开关控制界面深色/浅色</span>
          </div>
          <div class="settings-item settings-item-row">
            <label class="settings-label">保持滚动（强制最新）</label>
            <label class="settings-switch">
              <input type="checkbox" v-model="localForceLatestScroll" />
              <span class="settings-switch-slider"></span>
            </label>
            <span class="settings-hint">开启后弹幕列表会始终定位到最新消息，即使手动上滑也会自动回到底部</span>
          </div>
          <div class="settings-item">
            <label class="settings-label">视频显示模式</label>
            <select class="settings-input" v-model="localVideoMode">
              <option value="normal">正常（视频+弹幕）</option>
              <option value="off">关闭视频（仅弹幕/数据）</option>
              <option value="video-only">仅视频（无弹幕，仅视频播放区）</option>
            </select>
            <span class="settings-hint">注意：视频模式优先于录制，需先开启视频模式才能录制</span>
          </div>
        </div>
        <div class="settings-footer">
          <button class="btn btn-cancel" @click="close">取消</button>
          <button class="btn btn-confirm" @click="confirm">确认</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { ref } from 'vue';

interface SettingsProps {
  monitorInterval: number;
  statsInterval: number;
  roomList: string;
  enableRecording: boolean;
  darkMode: boolean;
  // 定时黑白模式
  // 使用 timedBw 前缀，避免在模板 kebab-case 与缩写大写转换时歧义
  timedBwEnabled?: boolean;
  timedBwStart?: string; // HH:mm
  timedBwEnd?: string; // HH:mm
  timedBwDefault?: string; // 'black' | 'white'
  // 是否强制列表始终滚动到最新
  forceLatestScroll?: boolean;
  // 视频显示模式: 'normal' | 'off' | 'video-only'
  videoMode?: string;
}

const props = defineProps<SettingsProps>();

const emit = defineEmits<{
  (e: 'update', monitorInterval: number, statsInterval: number, roomList: string, enableRecording: boolean, darkMode: boolean, timedBWEnabled: boolean, timedBWStart: string, timedBWEnd: string, timedBWDefault: string, videoMode: string, forceLatestScroll: boolean): void;
  (e: 'close'): void;
}>();

const visible = ref(false);
// 本地编辑值，单位：秒
const localMonitorInterval = ref(props.monitorInterval / 1000);
const localStatsInterval = ref(props.statsInterval / 1000);
const localRoomList = ref(props.roomList);
const localEnableRecording = ref(props.enableRecording);
const localDarkMode = ref(props.darkMode);
const localTimedBWEnabled = ref(!!props.timedBwEnabled);
const localTimedBWStart = ref(props.timedBwStart || '07:00');
const localTimedBWEnd = ref(props.timedBwEnd || '19:00');
const localTimedBWDefault = ref(props.timedBwDefault || 'black');
const localForceLatestScroll = ref(!!props.forceLatestScroll);
const localVideoMode = ref(props.videoMode || 'normal');

const open = function () {
  // 同步最新值
  localMonitorInterval.value = props.monitorInterval / 1000;
  localStatsInterval.value = props.statsInterval / 1000;
  localRoomList.value = props.roomList;
  localEnableRecording.value = props.enableRecording;
  localDarkMode.value = props.darkMode;
  localForceLatestScroll.value = !!props.forceLatestScroll;
  // 优先使用 props；若 props 未包含（例如后端未保存），回退到 localStorage
  if (typeof props.timedBwEnabled === 'boolean') {
    localTimedBWEnabled.value = !!props.timedBwEnabled;
    localTimedBWStart.value = props.timedBwStart || '07:00';
    localTimedBWEnd.value = props.timedBwEnd || '19:00';
    localTimedBWDefault.value = props.timedBwDefault || 'black';
  } else {
    try {
      const raw = localStorage.getItem('dycast.settings');
      if (raw) {
        const saved = JSON.parse(raw);
        if (typeof saved.timedBWEnabled === 'boolean') localTimedBWEnabled.value = saved.timedBWEnabled;
        if (typeof saved.timedBWStart === 'string') localTimedBWStart.value = saved.timedBWStart;
        if (typeof saved.timedBWEnd === 'string') localTimedBWEnd.value = saved.timedBWEnd;
        if (typeof saved.timedBWDefault === 'string') localTimedBWDefault.value = saved.timedBWDefault;
      }
    } catch (e) {}
  }
  localVideoMode.value = props.videoMode || (() => { try { const raw = localStorage.getItem('dycast.settings'); if (raw) return JSON.parse(raw).videoMode; } catch(e){} return 'normal'; })();
  visible.value = true;
};

const close = function () {
  visible.value = false;
  emit('close');
};

const confirm = function () {
  // 限制范围
  const monitor = Math.max(5, Math.min(3600, localMonitorInterval.value || 30));
  const stats = Math.max(1, Math.min(60, localStatsInterval.value || 3));
  emit('update', monitor * 1000, stats * 1000, localRoomList.value.trim(), localEnableRecording.value, localDarkMode.value, localTimedBWEnabled.value, localTimedBWStart.value, localTimedBWEnd.value, localTimedBWDefault.value, localVideoMode.value, localForceLatestScroll.value);
  visible.value = false;
};

defineExpose({ open, close });
</script>

<style lang="scss" scoped>
.settings-overlay {
  position: fixed;
  top: 0; left: 0;
  width: 100%; height: 100%;
  background: var(--overlay-dialog);
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  backdrop-filter: blur(2px);
}
.settings-dialog {
  background: var(--bg-card);
  border-radius: 12px;
  width: 380px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.25);
  overflow: hidden;
  animation: dialog-in 0.2s ease-out;
}
@keyframes dialog-in {
  from { opacity: 0; transform: scale(0.9) translateY(-10px); }
  to { opacity: 1; transform: scale(1) translateY(0); }
}
.settings-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--bd-light);
}
.settings-title {
  font-family: 'mkwxy';
  font-size: 1.1rem;
  font-weight: bold;
  color: var(--text-primary);
}
.settings-close {
  cursor: pointer;
  color: var(--text-hint);
  font-size: 14px;
  transition: color 0.2s;
  &:hover { color: var(--text-primary); }
}
.settings-body {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 18px;
  max-height: 60vh;
  overflow-y: auto;
}
.settings-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.settings-label {
  font-family: 'mkwxy';
  font-size: 0.9rem;
  color: var(--text-secondary);
}
.settings-input {
  border: 1.5px solid var(--input-border);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 1rem;
  outline: none;
  transition: border-color 0.2s;
  width: 100%;
  box-sizing: border-box;
  background: var(--input-bg);
  color: var(--text-primary);
  &:focus { border-color: var(--theme); }
}
.settings-textarea {
  border: 1.5px solid var(--input-border);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 0.95rem;
  font-family: monospace;
  outline: none;
  transition: border-color 0.2s;
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  line-height: 1.6;
  background: var(--input-bg);
  color: var(--text-primary);
  &:focus { border-color: var(--theme); }
}
.settings-hint {
  font-size: 0.75rem;
  color: var(--text-hint);
}
.settings-footer {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  padding: 14px 20px;
  border-top: 1px solid var(--bd-light);
}
.btn {
  padding: 8px 20px;
  border: none;
  border-radius: 8px;
  font-size: 0.9rem;
  cursor: pointer;
  transition: all 0.2s;
  font-family: 'mkwxy';
}
.btn-cancel {
  background: var(--bd-light);
  color: var(--text-secondary);
  &:hover { opacity: 0.8; }
}
.btn-confirm {
  background: var(--theme);
  color: #fff;
  &:hover { opacity: 0.85; }
}
.settings-item-row {
  flex-direction: row !important;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px !important;
  .settings-hint { width: 100%; }
}
.settings-switch {
  position: relative;
  display: inline-block;
  width: 40px;
  height: 22px;
  flex-shrink: 0;
  input { opacity: 0; width: 0; height: 0; }
}
.settings-switch-slider {
  position: absolute;
  cursor: pointer;
  top: 0; left: 0; right: 0; bottom: 0;
  background-color: var(--bd);
  border-radius: 22px;
  transition: 0.3s;
  &::before {
    content: '';
    position: absolute;
    height: 16px; width: 16px;
    left: 3px; bottom: 3px;
    background: #fff;
    border-radius: 50%;
    transition: 0.3s;
  }
}
.settings-switch input:checked + .settings-switch-slider {
  background-color: var(--theme);
}
.settings-switch input:checked + .settings-switch-slider::before {
  transform: translateX(18px);
}
</style>

