(function () {
  'use strict';

  window.createVideoCommentsCard = function (options) {
    var h = options.h;
    var post = options.post;
    var toast = options.toast;
    var watchJob = options.watchJob;
    var reload = options.reload;
    var authorMode = false;
    var running = false;
    var platformSel = h('select', { class: 'select', 'aria-label': '平台' },
      h('option', { value: 'douyin' }, '抖音'),
      h('option', { value: 'bili' }, 'B站'),
      h('option', { value: 'weibo' }, '微博'));
    var modeBtn = h('button', {
      class: 'btn', type: 'button', id: 'comments-mode',
      style: 'min-width:76px', 'aria-pressed': 'false'
    }, '视频');
    var urlInput = h('input', {
      class: 'input grow', id: 'comments-url', 'aria-label': '视频链接或 ID'
    });
    var countInput = h('input', {
      class: 'input', id: 'comments-count', style: 'width:76px',
      type: 'number', min: '1', max: '10', value: '3',
      'aria-label': '视频数'
    });
    var pagesInput = h('input', {
      class: 'input', id: 'comments-pages', style: 'width:76px',
      type: 'number', min: '1', max: '50', value: '5',
      'aria-label': '页数'
    });
    var authorRow = h('div', {
      class: 'toolbar hidden', id: 'comments-author-options',
      style: 'margin-top:8px'
    },
      h('span', { class: 'small' }, '视频数'), countInput,
      h('span', { class: 'small' }, '页数'), pagesInput);
    var pngCb = h('input', { type: 'checkbox', checked: true });
    var rawCb = h('input', { type: 'checkbox', checked: true });
    var kwInput = h('input', {
      class: 'input', placeholder: '关键词', 'aria-label': '关键词',
      style: 'width:130px'
    });
    var userInput = h('input', {
      class: 'input', placeholder: '昵称', 'aria-label': '昵称',
      style: 'width:120px'
    });
    var likesInput = h('input', {
      class: 'input', placeholder: '最少点赞', 'aria-label': '最少点赞',
      style: 'width:90px', type: 'number', min: '0'
    });
    var imageSel = h('select', { class: 'select', 'aria-label': '评论类型' },
      h('option', { value: 'all' }, '全部评论'),
      h('option', { value: 'has' }, '仅带图'),
      h('option', { value: 'text' }, '仅纯文字'));
    var sortSel = h('select', { class: 'select', 'aria-label': '排序' },
      h('option', { value: 'default' }, '默认排序'),
      h('option', { value: 'likes' }, '按点赞'),
      h('option', { value: 'time' }, '按时间'));
    var btn = h('button', {
      class: 'btn primary', type: 'button', id: 'comments-fetch'
    }, '抓取评论');
    var jobHost = h('div');

    function syncMode() {
      if (platformSel.value !== 'douyin') authorMode = false;
      modeBtn.textContent = authorMode ? '作者' : '视频';
      modeBtn.disabled = running || platformSel.value !== 'douyin';
      modeBtn.setAttribute('aria-pressed', String(authorMode));
      modeBtn.setAttribute('aria-label', authorMode
        ? '当前为作者模式，点击切换为视频模式'
        : '当前为视频模式，点击切换为作者模式');
      modeBtn.title = platformSel.value === 'douyin'
        ? (authorMode ? '点击切换为视频模式' : '点击切换为作者模式')
        : '当前平台仅支持视频模式';
      modeBtn.style.borderColor = authorMode ? 'var(--accent)' : '';
      modeBtn.style.background = authorMode ? 'var(--panel-3)' : '';
      modeBtn.style.color = authorMode ? 'var(--link)' : '';
      authorRow.classList.toggle('hidden', !authorMode);
      urlInput.placeholder = authorMode
        ? '抖音作者主页链接或 sec_uid'
        : ({ douyin: '抖音视频链接或 aweme_id',
          bili: 'B站视频链接、BV / AV 号或动态链接',
          weibo: '微博链接或 ID' }[platformSel.value]);
      urlInput.setAttribute('aria-label', authorMode
        ? '作者主页链接或 sec_uid' : '视频链接或 ID');
    }

    function setRunning(value) {
      running = value;
      btn.disabled = value;
      btn.textContent = value ? '抓取中…' : '抓取评论';
      platformSel.disabled = value;
      urlInput.disabled = value;
      countInput.disabled = value;
      pagesInput.disabled = value;
      syncMode();
    }

    modeBtn.addEventListener('click', function () {
      if (running || platformSel.value !== 'douyin') return;
      authorMode = !authorMode;
      syncMode();
    });
    platformSel.addEventListener('change', syncMode);
    btn.addEventListener('click', function () {
      if (running) return;
      if (!urlInput.value.trim()) {
        toast(authorMode ? '请输入作者主页链接或 sec_uid'
          : '请输入视频链接或 ID', 'warning');
        urlInput.focus();
        return;
      }
      if (!pngCb.checked && !rawCb.checked) {
        toast('请至少选择一种导出内容', 'warning');
        return;
      }
      if (authorMode && (!countInput.checkValidity()
          || !pagesInput.checkValidity())) {
        toast('视频数需为 1–10，页数需为 1–50', 'warning');
        (!countInput.checkValidity() ? countInput : pagesInput).focus();
        return;
      }
      var payload = {
        platform: platformSel.value,
        mode: authorMode ? 'author' : 'video',
        url: urlInput.value.trim(),
        count: authorMode ? Number(countInput.value) || 3 : 1,
        pages: authorMode ? Number(pagesInput.value) || 5 : 5,
        png: pngCb.checked,
        raw: rawCb.checked,
        keyword: kwInput.value.trim(),
        user: userInput.value.trim(),
        min_likes: Number(likesInput.value) || 0,
        image_mode: imageSel.value,
        sort_by: sortSel.value
      };
      setRunning(true);
      post('/api/tools/artifacts/comments', payload).then(function (result) {
        watchJob(jobHost, result.job, function (job) {
          setRunning(false);
          var outcome = job.result || {};
          toast(outcome.summary || '抓取已完成', 'success');
          reload();
        }, {
          cancelable: true,
          onSettled: function (job) {
            setRunning(false);
            if (job.status === 'error') {
              toast('抓取失败：' + (job.error || '未取得结果，请重试'), 'error');
            }
          }
        });
      }).catch(function (error) {
        setRunning(false);
        toast('抓取失败：' + error.message, 'error');
      });
    });
    syncMode();

    return h('div', { class: 'card', style: 'margin-bottom:12px' },
      h('div', { class: 'row-title' }, '抓取'),
      h('div', { class: 'toolbar', style: 'margin-top:8px' },
        h('span', { class: 'small' }, '平台'), platformSel, modeBtn, urlInput,
        h('label', { class: 'small' }, pngCb, ' 导出长图'),
        h('label', { class: 'small' }, rawCb, ' 保存原始数据'), btn),
      authorRow,
      h('div', { class: 'toolbar', style: 'margin-top:8px' },
        kwInput, userInput, likesInput, imageSel, sortSel),
      h('div', { class: 'muted small', style: 'margin-top:6px' },
        '导出到「评论区/<平台>/<视频ID>/」，可在下方产物列表预览长图或下载；'
        + '作者模式支持抖音主页，按视频数依次抓取并导出。'),
      jobHost);
  };
}());
