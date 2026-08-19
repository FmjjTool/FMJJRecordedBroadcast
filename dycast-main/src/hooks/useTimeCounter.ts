import { ref } from 'vue';

interface TimeCounterOptions {
  /**
   * 初始时间(s)
   */
  initValue?: number;
  /**
   * 计时模式
   *  - 0 => 正计时
   *  - 1 => 倒计时
   */
  mode?: 0 | 1;
  /**
   * 时间格式字符串
   *  - h => 时 | m => 分 | s => 秒
   *  - 默认：hh:mm:ss
   */
  fmt?: string;
}

const S_UNIT = 1000;

/**
 * 将秒数转换为格式字符串
 * @param seconds
 * @param fmt
 * @returns
 */
function convertTime(seconds: number, fmt: string = 'hh:mm:ss') {
  const map: Record<string, number> = {
    'h+': Math.floor(seconds / 3600),
    'm+': Math.floor((seconds % 3600) / 60),
    's+': seconds % 60
  };
  for (const item in map) {
    const reg = new RegExp(`(${item})`);
    if (reg.test(fmt)) {
      const t = reg.exec(fmt)?.[1];
      t && (fmt = fmt.replace(t, t.length === 1 ? `${map[item]}` : `00${map[item]}`.slice(t.length * -1)));
    }
  }
  return fmt;
}

const useTimeCounter = function (opt: TimeCounterOptions = {}) {
  const { initValue = 0, mode = 0, fmt = 'hh:mm:ss' } = opt;

  let seconds = initValue;
  let startTimestamp = 0;
  let elapsedBeforeStart = initValue;

  let timer: number | null = null;
  const text = ref(convertTime(initValue, fmt));

  // 刷新文本
  const refreshText = () => {
    if (timer) {
      const now = Date.now();
      const deltaSeconds = Math.floor((now - startTimestamp) / 1000);
      if (mode === 1) {
        seconds = Math.max(0, elapsedBeforeStart - deltaSeconds);
      } else {
        seconds = elapsedBeforeStart + deltaSeconds;
      }
    }
    text.value = convertTime(seconds, fmt);
  };

  /**
   * 开始计时
   */
  const start = () => {
    if (!timer) {
      startTimestamp = Date.now();
      elapsedBeforeStart = seconds;
      timer = window.setInterval(() => {
        // 每秒刷新一次文本，使用时间戳计算实际过去的时间
        refreshText();
      }, S_UNIT);
    }
  };

  /**
   * 停止计时
   */
  const stop = () => {
    if (timer) {
      window.clearInterval(timer);
      timer = null;
      // 停止时把当前走过的秒数存下来，支持后续再 start 接着计时
      elapsedBeforeStart = seconds;
    }
  };

  /**
   * 重置计时
   */
  const reset = () => {
    if (timer) {
      window.clearInterval(timer);
      timer = null;
    }
    seconds = initValue;
    elapsedBeforeStart = initValue;
    refreshText();
  };

  return {
    text,
    start,
    stop,
    reset
  };
};

export default useTimeCounter;
