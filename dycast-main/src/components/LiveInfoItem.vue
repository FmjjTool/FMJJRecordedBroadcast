<template>
  <div class="live-info-item">
    <label class="title">{{ title }}：</label>
    <div class="live-info-item-main">
      <img v-if="cover" :src="cover" alt="avatar" class="avatar" />
      <span class="text" title="{text}">{{ text }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
interface LiveInfoItemProps {
  title?: string;
  text?: string | number;
  cover?: string;
}

const props = withDefaults(defineProps<LiveInfoItemProps>(), {
  text: '*****'
});
</script>

<style lang="scss" scoped>
$titleColor: var(--text-primary);
$textColor: var(--text-secondary);

.live-info-item {
  width: 100%;
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 10px;
  box-sizing: border-box;
  .title {
    font-family: 'mkwxy';
    font-size: 1.05rem;
    font-weight: bold;
    color: $titleColor;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex-shrink: 0;
  }
}

.live-info-item-main {
  width: 100%;
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 5px;
  .text {
    user-select: none;
    width: fit-content;
    font-size: 1rem;
    line-height: 1.6;
    color: $textColor;
    font-family: 'dymht';
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .avatar {
    width: 2rem;
    height: 2rem;
    object-fit: cover;
    border-radius: 0.7rem;
  }
}
</style>
