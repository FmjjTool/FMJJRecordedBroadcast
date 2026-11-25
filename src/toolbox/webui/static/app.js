(function () {
  'use strict';

  var params = new URLSearchParams(location.search);
  var urlToken = params.get('token') || '';
  var token = localStorage.getItem('webui-ticket') || '';
  var gate = document.getElementById('gate');
  var gateToken = document.getElementById('gateToken');
  var gateError = document.getElementById('gateError');
  var content = document.getElementById('content');
  var pageTitle = document.getElementById('pageTitle');
  var toastBox = document.getElementById('toast');
  var sidebar = document.getElementById('sidebar');
  var scrim = document.getElementById('scrim');
  var state = {
    route: 'home',
    theme: localStorage.getItem('webui-theme') || 'auto',
    collected: { q: '', pageSize: 100, formats: new Set(), overview: null, cols: {} },
    assets: { tab: 'entries', page: 1, q: '', platform: '', dbPlatform: '', dbPage: 1, dbQ: '', dbOrder: '', dbAsc: false, cat: '', localPage: 1,
      offline: { year: '', selected: null, order: '', asc: true, platforms: new Set(['bilibili', 'douyin_archive']) } },
    record: { platform: '', order: '', asc: true, selected: new Set() },
    trackers: { platform: '', open: '' },
    fangroups: { open: '' },
    douyin: { name: '', tab: 'core' },
    chaohua: { topic: '' },
    info_accounts: { platform: '' },
    artifacts: { tab: 'comments' },
    netdisk: { tab: 'backup' },
    audio: { order: '', asc: true, selected: new Set() },
    organize: { order: '', asc: true, selected: new Set() },
    rename: { order: '', asc: true },
    video: { tab: 'convert', order: '', asc: true, selected: new Set() },
    calendar: { day: '', order: '', asc: true },
    migrate: { order: '', asc: true, selected: new Set(), mode: 'copy' },
    subtitle: { order: '', asc: true, selected: new Set(), formats: new Set() },
    tasks: { order: '', asc: true },
    tcdoc: { docKey: '', tab: 'sync', rowsTab: '', rowsPage: 1, writeTab: 'official', writeDays: 30, writeKey: '' }
  };
  var routes = {
    home: { title: '工作台', load: renderHome },
    info_accounts: { title: '采集任务', load: renderInfoAccounts },
    info_tools: { title: '采集工具', load: renderInfoTools },
    collected: { title: '采集结果', load: renderCollected },
    assets: { title: '资产库', load: renderAssets },
    netdisk: { title: '网络库', load: renderNetdisk },
    organize: { title: '自动整理', load: renderOrganize },
    editor: { title: '视频操作', load: renderVideo },
    rename: { title: '命名投稿', load: renderRename },
    audio: { title: '音频提取', load: renderAudio },
    subtitle: { title: '字幕转换', load: renderSubtitle },
    launcher: { title: '继续操作', load: renderLauncher },
    calendar: { title: '日历浏览', load: renderCalendar },
    migrate: { title: '文件迁移', load: renderMigrate },
    record: { title: '直播录制', load: renderRecord },
    music_tracker: { title: '音乐账号', load: renderMusicTracker },
    media_tracker: { title: '网媒账号', load: renderMediaTracker },
    douyin_index: { title: '抖音指数', load: renderDouyinIndex },
    weibo_chaohua: { title: '微博超话', load: renderWeiboChaohua },
    tcdoc: { title: '腾讯文档', load: renderTcdoc },
    fan_groups: { title: '粉丝群', load: renderFanGroups },
    tasks: { title: '定时任务', load: renderTasks },
    system: { title: '全局设置', load: renderSystem }
  };
  var searchTimer = null;

  function login(master, onDone, onFail) {
    fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-WebUI-Token': master },
      body: '{}', cache: 'no-store'
    }).then(function (response) {
      return response.json().then(function (data) {
        if (response.status === 403) {
          onFail('访问令牌无效，请核对后重试。');
          return;
        }
        if (!response.ok || !data || !data.ticket) {
          onFail('登录失败（' + response.status + '）。');
          return;
        }
        token = data.ticket;
        localStorage.setItem('webui-ticket', token);
        onDone();
      });
    }).catch(function () {
      onFail('无法连接服务，请确认 WebUI 正在运行。');
    });
  }

  function h(tag, props) {
    var node = document.createElement(tag);
    props = props || {};
    Object.keys(props).forEach(function (key) {
      var value = props[key];
      if (value === null || value === undefined || value === false) return;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key === 'value') node.value = value;
      else if (key === 'checked') node.checked = !!value;
      else if (key === 'selected') node.selected = !!value;
      else if (key === 'dataset') Object.assign(node.dataset, value);
      else if (key.indexOf('on') === 0 && typeof value === 'function') {
        node.addEventListener(key.slice(2).toLowerCase(), value);
      } else {
        node.setAttribute(key, value === true ? '' : value);
      }
    });
    for (var i = 2; i < arguments.length; i++) appendChild(node, arguments[i]);
    return node;
  }

  function appendChild(node, child) {
    if (child === null || child === undefined || child === false) return;
    if (Array.isArray(child)) {
      child.forEach(function (item) { appendChild(node, item); });
      return;
    }
    node.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }

  function setLoading(host, text) {
    host.replaceChildren(h('div', { class: 'loading', text: text || '加载中…' }));
  }

  function setError(host, error, retry) {
    host.replaceChildren(h('div', { class: 'error' },
      '加载失败：' + (error && error.message ? error.message : '未知错误'),
      h('div', { class: 'small', style: 'margin-top:8px;color:var(--muted)' }, '请确认服务端仍在运行并重试。'),
      retry ? h('button', { class: 'btn', type: 'button', style: 'margin-top:10px', onclick: retry }, '重试') : null));
  }

  function toast(text, type) {
    toastBox.textContent = text;
    toastBox.className = 'toast' + (type ? ' ' + type : '');
    clearTimeout(toastBox._timer);
    toastBox._timer = setTimeout(function () {
      toastBox.className = 'toast hidden';
    }, 3200);
  }

  function showGate(message) {
    gate.classList.remove('hidden');
    gateError.textContent = message || '';
    setTimeout(function () { gateToken.focus(); }, 30);
  }

  function api(path, options) {
    options = options || {};
    var headers = Object.assign({}, options.headers || {});
    if (token) headers['X-WebUI-Token'] = token;
    return fetch(path, Object.assign({}, options, { headers: headers, cache: 'no-store' }))
      .then(function (response) {
        return response.text().then(function (text) {
          var data = null;
          try { data = text ? JSON.parse(text) : null; } catch (e) { data = text; }
          if (response.status === 403) {
            clearStaleSession();
            throw new Error('需要访问令牌');
          }
          if (!response.ok) {
            var message = data && data.error ? data.error : ('请求失败（' + response.status + '）');
            throw new Error(message);
          }
          return data;
        });
      });
  }

  function post(path, body) {
    return api(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    });
  }

  function withToken(path) {
    return path + (path.indexOf('?') >= 0 ? '&' : '?') + 'token=' + encodeURIComponent(token);
  }

  function pager(host, page, pages, total, onChange) {
    host.replaceChildren(
      h('span', { class: 'page-text' }, '第 ' + page + ' / ' + pages + ' 页 · 共 ' + total + ' 条'),
      h('button', { class: 'btn', type: 'button', disabled: page <= 1, onclick: function () { onChange(1); } }, '首页'),
      h('button', { class: 'btn', type: 'button', disabled: page <= 1, onclick: function () { onChange(page - 1); } }, '上一页'),
      h('button', { class: 'btn', type: 'button', disabled: page >= pages, onclick: function () { onChange(page + 1); } }, '下一页'),
      h('button', { class: 'btn', type: 'button', disabled: page >= pages, onclick: function () { onChange(pages); } }, '末页'));
  }

  function empty(text) {
    return h('div', { class: 'empty' }, text);
  }

  function statCard(label, value, note) {
    return h('div', { class: 'stat' },
      h('div', { class: 'label' }, label),
      h('div', { class: 'value' }, value == null ? '-' : String(value)),
      note ? h('div', { class: 'note' }, note) : null);
  }

  function fmtTime(value) {
    return value ? String(value).replace('T', ' ') : '-';
  }

  function fmtNumber(value) {
    var number = Number(value || 0);
    return Number.isFinite(number) ? number.toLocaleString('zh-CN') : '0';
  }

  function selectedCountText(count) {
    return count ? ('已选 ' + count + ' 条') : '未选择条目';
  }

  function applyTheme() {
    document.documentElement.dataset.theme = state.theme;
    var btn = document.getElementById('themeBtn');
    var effectiveDark = state.theme === 'dark' ||
      (state.theme === 'auto' && window.matchMedia &&
        window.matchMedia('(prefers-color-scheme: dark)').matches);
    btn.textContent = effectiveDark ? '浅色' : '深色';
  }

  function toggleTheme() {
    var effectiveDark = state.theme === 'dark' ||
      (state.theme === 'auto' && window.matchMedia &&
        window.matchMedia('(prefers-color-scheme: dark)').matches);
    state.theme = effectiveDark ? 'light' : 'dark';
    localStorage.setItem('webui-theme', state.theme);
    applyTheme();
  }

  function navigate(route) {
    if (!routes[route]) route = 'home';
    state.route = route;
    location.hash = '#/' + route;
    document.querySelectorAll('.nav-item').forEach(function (button) {
      button.classList.toggle('active', button.dataset.route === route);
    });
    pageTitle.textContent = routes[route].title;
    sidebar.classList.remove('open');
    scrim.classList.remove('show');
    content.replaceChildren();
    try {
      routes[route].load(content);
    } catch (error) {
      setError(content, error, function () { navigate(route); });
      if (window.console && console.error) {
        console.error('页面渲染失败', route, error);
      }
    }
  }

  function watchJob(host, jobId, onDone, options) {
    options = options || {};
    host.replaceChildren();
    var box = h('div', { class: 'job' });
    var head = h('div', { class: 'job-head' });
    var status = h('span', { class: 'job-status' }, '任务运行中');
    var link = h('span', { class: 'job-done' });
    var cancelBtn = options.cancelable
      ? h('button', { class: 'btn', type: 'button' }, '取消任务')
      : null;
    var bar = h('div', { class: 'progress' }, h('i'));
    var resultLine = h('div', { class: 'muted small', style: 'margin:2px 0 6px' });
    var log = h('pre', { class: 'job-log' });
    head.appendChild(status);
    head.appendChild(link);
    if (cancelBtn) {
      cancelBtn.addEventListener('click', function () {
        cancelBtn.disabled = true;
        cancelBtn.textContent = '取消中…';
        post('/api/jobs/' + jobId + '/cancel', {}).catch(function (error) {
          toast('取消失败：' + error.message, 'error');
          cancelBtn.disabled = false;
          cancelBtn.textContent = '取消任务';
        });
      });
      head.appendChild(cancelBtn);
    }
    box.appendChild(head);
    box.appendChild(bar);
    box.appendChild(resultLine);
    box.appendChild(log);
    host.appendChild(box);

    function settle(job) {
      if (cancelBtn) cancelBtn.remove();
      if (options.onSettled) options.onSettled(job);
    }

    function tick() {
      if (!box.isConnected) return;
      api('/api/jobs/' + jobId).then(function (job) {
        status.textContent = job.status === 'done' ? '已完成' : (job.status === 'error' ? '失败' : (job.status === 'cancelled' ? '已取消' : (job.message || '运行中')));
        bar.firstChild.style.width = (job.progress == null ? 30 : Math.round(job.progress * 100)) + '%';
        log.textContent = (job.logs || []).join('\n');
        if (job.status === 'done') {
          if (job.result) {
            if (job.result.summary) resultLine.textContent = job.result.summary;
            else if (job.result.exported !== undefined) {
              resultLine.textContent = '导出 ' + job.result.exported + ' 个文件' +
                (job.result.skipped ? '，跳过已存在 ' + job.result.skipped + ' 个' : '');
            }
          }
          if (job.download) {
            link.replaceChildren(h('a', {
              href: withToken(job.download), class: 'btn primary', download: ''
            }, '下载结果'));
          }
          if (onDone) onDone(job);
          settle(job);
          return;
        }
        if (job.status === 'cancelled') {
          bar.firstChild.style.width = '0';
          settle(job);
          return;
        }
        if (job.status === 'error') {
          link.replaceChildren(h('span', { class: 'badge warn' }, job.error || '任务失败'));
          settle(job);
          return;
        }
        setTimeout(tick, 700);
      }).catch(function (error) {
        status.textContent = '任务状态读取失败';
        log.textContent = error.message;
        link.replaceChildren(h('span', { class: 'badge warn' }, error.message));
      });
    }
    tick();
  }

  function renderHome(host) {
    host.replaceChildren();
    host.appendChild(h('div', { class: 'card' },
      h('div', { class: 'row-title' }, '录播工具箱 Web 服务端'),
      h('div', { class: 'muted small', style: 'margin-top:6px' },
        '分组与命名与桌面端一致；这里只列出已接入 Web 的页面，'
        + '桌面端的采集任务、网络库、账户追踪、音乐工具、本地工具等页面尚未接入。')));
    [
      ['常用工具', [
        ['info_accounts', '采集任务', '账号清单与批量解析'],
        ['collected', '采集结果', '结果库、下载与导出'],
        ['info_tools', '采集工具', '评论区 / B站资源 / 公众号 / 表情包'],
        ['netdisk', '网络库', '网盘备份状态与运行记录'],
        ['assets', '资产库', '已获取资源与本地扫描'],
        ['editor', '视频操作', '转换 / 合并 / 切割 / 压缩 / 检测'],
        ['rename', '命名投稿', '标准命名与版权标签'],
        ['subtitle', '字幕转换', '字幕与歌词格式转换'],
        ['organize', '自动整理', '按日期整理录播文件']
      ]],
      ['更多工具', [
        ['audio', '音频提取', '按音轨提取音频'],
        ['launcher', '继续操作', '打开链接或启动本机软件'],
        ['calendar', '日历浏览', '按日期文件夹浏览'],
        ['migrate', '文件迁移', '复制 / 剪切到目标目录'],
        ['record', '直播录制', '监控与录制控制（需桌面运行）'],
        ['music_tracker', '音乐账号', '五平台账号指标与历史'],
        ['media_tracker', '网媒账号', '网媒账号指标与历史'],
        ['douyin_index', '抖音指数', '达人核心指标与粉丝画像'],
        ['weibo_chaohua', '微博超话', '本地累计与增长分析'],
        ['fan_groups', '粉丝群', 'QQ 群订阅与人数历史'],
        ['tcdoc', '腾讯文档', '浏览量 / 成员数每日记录']
      ]],
      ['全局', [
        ['tasks', '任务', '定时任务与安装'],
        ['system', '设置', '目录、主题与依赖']
      ]]
    ].forEach(function (group) {
      host.appendChild(h('div', {
        class: 'row-title', style: 'margin:16px 0 8px'
      }, group[0]));
      var grid = h('div', { class: 'grid cols-3' });
      group[1].forEach(function (item) {
        grid.appendChild(h('button', {
          class: 'card tool-card', type: 'button',
          onclick: function () { navigate(item[0]); }
        },
          h('div', { class: 'tool-card-title' }, item[1]),
          h('div', { class: 'muted small' }, item[2])));
      });
      host.appendChild(grid);
    });
  }

  function renderInfoAccounts(root) {
    setLoading(root, '正在读取账号清单…');
    api('/api/info-accounts').then(function (data) {
      root.replaceChildren();
      var accounts = data.accounts || [];
      var platformSel = h('select', { class: 'select' });
      platformSel.appendChild(h('option', { value: '' }, '全部平台'));
      (data.by_platform || []).forEach(function (item) {
        platformSel.appendChild(h('option', { value: item.key },
          item.name + '（' + item.count + '）'));
      });
      platformSel.value = state.info_accounts.platform || '';
      var tableHost = h('div');

      function draw() {
        var rows = accounts.filter(function (row) {
          return !state.info_accounts.platform
            || row.platform === state.info_accounts.platform;
        });
        if (!rows.length) {
          tableHost.replaceChildren(empty(
            '暂无账号。桌面端「采集任务」批量添加账号主页链接后刷新。'));
          return;
        }
        var head = h('tr');
        ['平台', '账号', '链接', '分组', '备注', '类型']
          .forEach(function (title) {
            head.appendChild(h('th', null, title));
          });
        var body = h('tbody');
        rows.forEach(function (row) {
          var kindLabels = { auto: '自动', video: '只视频',
            dynamic: '只动态', all: '全部' };
          body.appendChild(h('tr', null,
            h('td', null, h('span', { class: 'badge' },
              row.platform_name || '未知')),
            h('td', null, row.name || '-'),
            h('td', { class: 'ellipsis' }, row.url
              ? h('a', { href: row.url, target: '_blank',
                rel: 'noopener noreferrer' }, row.url)
              : '-'),
            h('td', { class: 'muted small' }, row.groups || '-'),
            h('td', { class: 'muted small' }, row.note || '-'),
            h('td', null, kindLabels[row.kind] || row.kind || '-')));
        });
        tableHost.replaceChildren(h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' },
            h('thead', null, head), body)));
      }

      platformSel.addEventListener('change', function () {
        state.info_accounts.platform = platformSel.value;
        draw();
      });

      var s = data.settings || {};
      var dateText = (s.date_start || s.date_end)
        ? ((s.date_start || '不限') + ' ~ ' + (s.date_end || '现在'))
        : '不限';
      function setting(label, value) {
        return h('div', { class: 'metric' },
          h('div', { class: 'label' }, label),
          h('div', { class: 'value' }, value));
      }

      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'row-title' }, '批量添加 / 解析'),
        h('div', { class: 'muted small', style: 'margin:4px 0 6px' },
          '一行一个，支持整段分享文案：账号主页链接 → 添加账号；'
          + '作品 / 视频链接 → 解析到采集结果。'),
        h('textarea', {
          class: 'textarea', rows: '3', disabled: true,
          placeholder: '一行一个链接（批量处理在桌面端）'
        }),
        h('div', { class: 'toolbar', style: 'margin-top:8px' },
          h('button', {
            class: 'btn primary', type: 'button', disabled: true
          }, '开始处理'),
          h('span', { class: 'muted small' }, '批量解析在桌面端'))));
      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'toolbar' },
          h('span', { class: 'row-title' }, '账号列表'),
          h('span', { class: 'muted small' },
            '共 ' + fmtNumber(data.total) + ' 个账号'),
          h('span', { style: 'flex:1' }),
          h('span', { class: 'muted small' }, '平台'), platformSel),
        tableHost));
      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'row-title' }, '采集设置'),
        h('div', { class: 'metric-grid', style: 'margin-top:8px' },
          setting('采集范围', '全部账号'),
          setting('每账号条数', String(s.count || 10)),
          setting('限流', '每 ' + (s.rate_count || 10) + ' 个停 '
            + (s.rate_interval || 10) + ' 秒'),
          setting('增量模式', s.incremental ? '开' : '关'),
          setting('获取后自动下载', s.auto_download ? '开' : '关'),
          setting('日期段', dateText)),
        h('div', { class: 'muted small', style: 'margin-top:8px' },
          '以上设置与账号配置在桌面端修改。')));
      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'toolbar' },
          h('button', {
            class: 'btn primary', type: 'button', disabled: true
          }, '获取最新'),
          h('button', {
            class: 'btn', type: 'button', disabled: true
          }, '会话定时'),
          h('span', { class: 'muted small' }, '空闲（采集在桌面端）'))));
      root.appendChild(h('div', { class: 'card' },
        h('div', { class: 'card-title' }, '运行日志'),
        h('div', { class: 'muted small' },
          '运行日志由桌面端采集任务实时输出；Web 端暂不提供。')));
      draw();
    }).catch(function (error) {
      setError(root, error, function () { renderInfoAccounts(root); });
    });
  }

  function renderInfoTools(root) {
    root.replaceChildren();
    var TABS = [['comments', '视频评论区'], ['bili', 'B站资源'],
      ['wechat', '公众号解析'], ['emoji', '抖音表情包']];
    var tabs = h('div', { class: 'tabs' });
    var host = h('div');
    TABS.forEach(function (item) {
      tabs.appendChild(h('button', {
        class: 'tab' + (state.artifacts.tab === item[0] ? ' active' : ''),
        type: 'button',
        onclick: function () {
          state.artifacts.tab = item[0];
          tabs.querySelectorAll('.tab').forEach(function (button, index) {
            button.classList.toggle('active', TABS[index][0] === item[0]);
          });
          load();
        }
      }, item[1]));
    });
    root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
      h('div', { class: 'muted small' },
        '浏览桌面端导出的本地产物：评论区长图与原始数据、B站资源、公众号文章、抖音表情包；'
        + '抓取与导出仍在桌面端。')));
    root.appendChild(tabs);
    root.appendChild(host);

    function preview(kind, rel) {
      var overlay = h('div', { class: 'preview-overlay' });
      overlay.addEventListener('click', function (event) {
        if (event.target === overlay) overlay.remove();
      });
      overlay.appendChild(h('div', { class: 'preview-box' },
        h('div', { class: 'preview-caption' },
          h('span', null, rel),
          h('button', {
            class: 'btn mini', type: 'button',
            onclick: function () { overlay.remove(); }
          }, '关闭')),
        h('div', { class: 'preview-body' },
          h('img', {
            alt: '',
            src: '/api/tools/artifacts/preview?kind='
              + encodeURIComponent(kind) + '&rel=' + encodeURIComponent(rel)
              + '&token=' + encodeURIComponent(token)
          }))));
      document.body.appendChild(overlay);
    }

    function fileRow(kind, file) {
      var actions = [];
      if (file.is_image) {
        actions.push(h('button', {
          class: 'btn mini', type: 'button',
          onclick: function () { preview(kind, file.rel); }
        }, '预览'));
      }
      actions.push(h('a', {
        class: 'btn mini',
        href: '/api/tools/artifacts/file?kind=' + encodeURIComponent(kind)
          + '&rel=' + encodeURIComponent(file.rel)
          + '&token=' + encodeURIComponent(token)
      }, '下载'));
      return h('div', { class: 'artifact-row' },
        h('span', { class: 'artifact-name', title: file.rel }, file.name),
        h('span', { class: 'muted small' }, fmtBytes(file.size)),
        actions);
    }

    function load() {
      setLoading(host, '正在读取本地产物…');
      api('/api/tools/artifacts?kind=' + encodeURIComponent(state.artifacts.tab))
        .then(function (data) {
          host.replaceChildren();
          var groups = data.groups || [];
          var images = groups.reduce(function (sum, group) {
            return sum + (group.images || 0);
          }, 0);
          host.appendChild(h('div', {
            class: 'muted small', style: 'margin-bottom:8px'
          }, '共 ' + groups.length + ' 组 · ' + fmtNumber(data.total)
            + ' 个文件 · 图片 ' + fmtNumber(images)
            + ' · 只读，抓取 / 导出仍在桌面端'));
          if (!groups.length) {
            host.appendChild(empty(data.note || '暂无产物。'));
            return;
          }
          groups.forEach(function (group) {
            var card = h('div', { class: 'card', style: 'margin-bottom:12px' });
            card.appendChild(h('div', { class: 'toolbar' },
              h('span', { class: 'row-title' }, group.name),
              h('span', { class: 'muted small' },
                fmtNumber(group.total) + ' 个文件 · 图片 '
                + fmtNumber(group.images))));
            var list = h('div', { class: 'artifact-list' });
            (group.files || []).slice(0, 30).forEach(function (file) {
              list.appendChild(fileRow(data.kind, file));
            });
            card.appendChild(list);
            if (group.total > 30) {
              card.appendChild(h('div', {
                class: 'muted small', style: 'margin-top:6px'
              }, '仅显示前 30 个文件，共 ' + fmtNumber(group.total) + ' 个。'));
            }
            host.appendChild(card);
          });
        }).catch(function (error) {
          setError(host, error, load);
        });
    }
    load();
  }

  function renderCollected(root) {
    setLoading(root, '正在读取采集结果概览…');
    api('/api/collected/overview').then(function (overview) {
      state.collected.overview = overview;
      if (!state.collected.formats.size) {
        overview.formats.forEach(function (fmt) { state.collected.formats.add(fmt); });
      }
      var jobHost = h('div');
      var info = h('span', { class: 'muted small' });
      var search = h('input', {
        class: 'input grow', placeholder: '搜索标题、作者或作品 ID',
        value: state.collected.q
      });
      var comment = h('input', {
        class: 'input', type: 'number', min: '0', max: '50',
        value: overview.comment_pages, style: 'width:76px'
      });
      var exportSelected = h('button', {
        class: 'btn primary', type: 'button', disabled: true
      }, '导出所有勾选');
      var downloadSelected = h('button', {
        class: 'btn', type: 'button', disabled: true
      }, '下载所有勾选');

      var formatBoxes = h('div', { class: 'toolbar', style: 'margin:0' });
      overview.formats.forEach(function (fmt) {
        formatBoxes.appendChild(h('label', { class: 'check' },
          h('input', {
            type: 'checkbox', checked: state.collected.formats.has(fmt),
            onchange: function (event) {
              if (event.target.checked) state.collected.formats.add(fmt);
              else state.collected.formats.delete(fmt);
              updateInfo();
            }
          }), fmt.toUpperCase()));
      });
      formatBoxes.appendChild(h('label', {
        class: 'check', style: 'margin-left:8px'
      }, '微博评论页', comment));

      var grid = h('div', { class: 'grid cols-4 collected-grid' });
      var cols = {};

      function column(key) {
        return state.collected.cols[key];
      }

      function totalSelected() {
        var total = 0;
        Object.keys(cols).forEach(function (key) {
          total += column(key).selected.size;
        });
        return total;
      }

      function allSelectedKeys() {
        var keys = [];
        Object.keys(cols).forEach(function (key) {
          column(key).selected.forEach(function (item) { keys.push(item); });
        });
        return keys;
      }

      function updateInfo() {
        var total = totalSelected();
        var noFormats = state.collected.formats.size === 0;
        info.textContent = selectedCountText(total) + ' · 导出格式 '
          + (noFormats ? '未选择'
            : Array.from(state.collected.formats).join(' / '));
        exportSelected.disabled = total === 0 || noFormats;
        downloadSelected.disabled = total === 0;
        Object.keys(cols).forEach(updateColumnButtons);
      }

      function updateColumnButtons(key) {
        var col = column(key);
        var count = col.selected.size;
        col.refs.exportBtn.disabled = count === 0
          || state.collected.formats.size === 0;
        col.refs.downloadBtn.disabled = count === 0;
      }

      function drawColumn(key) {
        var col = column(key);
        var data = col.data;
        if (!data) return;
        col.refs.count.textContent = fmtNumber(data.total);
        var host = col.refs.list;
        host.replaceChildren();
        if (!data.items.length) {
          host.appendChild(empty('该栏暂无条目。可在桌面端「采集任务」解析后刷新。'));
        } else {
          data.items.forEach(function (item) {
            var checked = col.selected.has(item.key);
            var kind = item.kind === 'video' ? 'video'
              : (item.kind === 'dynamic' ? 'dynamic' : 'image');
            var checkbox = h('input', { type: 'checkbox', checked: checked });
            var row = h('label', {
              class: 'col-item' + (checked ? ' checked' : '')
            }, checkbox, h('span', { class: 'col-item-main' },
              h('span', { class: 'col-item-title' },
                h('span', { class: 'kind kind-' + kind },
                  '[' + kindLabel(item.kind) + '] '),
                item.url
                  ? h('a', {
                    href: item.url, target: '_blank',
                    rel: 'noopener noreferrer'
                  }, item.title)
                  : item.title),
              h('span', { class: 'col-item-meta' },
                (item.author || '-') + ' · ' + fmtTime(item.publish_time)
                + ' · 播放 ' + fmtNumber(item.play)
                + ' · 赞 ' + fmtNumber(item.like))));
            checkbox.addEventListener('change', function () {
              if (checkbox.checked) col.selected.add(item.key);
              else col.selected.delete(item.key);
              row.classList.toggle('checked', checkbox.checked);
              updateInfo();
            });
            host.appendChild(row);
          });
        }
        drawColumnPager(key);
        updateColumnButtons(key);
      }

      function drawColumnPager(key) {
        var col = column(key);
        var data = col.data;
        if (!data) return;
        col.refs.pager.replaceChildren(
          h('button', {
            class: 'btn ghost mini', type: 'button',
            disabled: data.page <= 1,
            onclick: function () {
              col.page = data.page - 1;
              loadColumn(key);
            }
          }, '上一页'),
          h('span', { class: 'muted small' }, data.page + ' / ' + data.pages),
          h('button', {
            class: 'btn ghost mini', type: 'button',
            disabled: data.page >= data.pages,
            onclick: function () {
              col.page = data.page + 1;
              loadColumn(key);
            }
          }, '下一页'));
      }

      function loadColumn(key) {
        var col = column(key);
        setLoading(col.refs.list, '正在读取条目…');
        api('/api/collected/items?platform=' + encodeURIComponent(key)
          + '&page=' + col.page
          + '&page_size=' + state.collected.pageSize
          + '&q=' + encodeURIComponent(state.collected.q))
          .then(function (data) {
            col.data = data;
            drawColumn(key);
          }).catch(function (error) {
            setError(col.refs.list, error, function () { loadColumn(key); });
          });
      }

      function loadAll() {
        Object.keys(cols).forEach(function (key) {
          column(key).page = 1;
          loadColumn(key);
        });
      }

      function columnSelect(key, mode) {
        api('/api/collected/keys?platform=' + encodeURIComponent(key)
          + '&q=' + encodeURIComponent(state.collected.q))
          .then(function (data) {
            var col = column(key);
            var keys = data.keys || [];
            if (mode === 'all') {
              keys.forEach(function (item) { col.selected.add(item); });
            } else if (mode === 'none') {
              col.selected.clear();
            } else {
              keys.forEach(function (item) {
                if (col.selected.has(item)) col.selected.delete(item);
                else col.selected.add(item);
              });
            }
            if (col.data) drawColumn(key);
            updateInfo();
          }).catch(function (error) {
            toast('选择失败：' + error.message, 'error');
          });
      }

      function startExport(keys, platform) {
        var formats = Array.from(state.collected.formats);
        if (!formats.length) {
          toast('请至少选择一种导出格式', 'error');
          return;
        }
        if (!keys.length) {
          toast('请先勾选要导出的条目', 'warning');
          return;
        }
        exportSelected.disabled = true;
        downloadSelected.disabled = true;
        post('/api/collected/export', {
          keys: keys, platform: platform || '', keyword: '',
          formats: formats, comment_pages: Number(comment.value || 0)
        }).then(function (data) {
          watchJob(jobHost, data.job, function () {
            toast('导出完成', 'success');
            updateInfo();
            loadAll();
          });
        }).catch(function (error) {
          updateInfo();
          toast(error.message, 'error');
        });
      }

      function startDownload(keys) {
        if (!keys.length) {
          toast('请先勾选要下载的条目', 'warning');
          return;
        }
        if (!confirm('将按最高画质、4 线程把勾选内容下载到服务端导出目录，继续？')) {
          return;
        }
        exportSelected.disabled = true;
        downloadSelected.disabled = true;
        post('/api/collected/download', { keys: keys }).then(function (data) {
          watchJob(jobHost, data.job, function () {
            updateInfo();
          });
        }).catch(function (error) {
          updateInfo();
          toast(error.message, 'error');
        });
      }

      overview.platforms.forEach(function (platform) {
        var col = state.collected.cols[platform.key];
        if (!col) {
          col = state.collected.cols[platform.key] = {
            page: 1, data: null, selected: new Set()
          };
        }
        cols[platform.key] = col;
        var listHost = h('div', { class: 'col-list' });
        var pagerHost = h('div', { class: 'col-pager' });
        var exportBtn = h('button', {
          class: 'btn mini', type: 'button', disabled: true
        }, '导出');
        var downloadBtn = h('button', {
          class: 'btn mini', type: 'button', disabled: true
        }, '下载');
        var selectAll = h('button', {
          class: 'btn ghost mini', type: 'button',
          onclick: function () { columnSelect(platform.key, 'all'); }
        }, '全选');
        var invert = h('button', {
          class: 'btn ghost mini', type: 'button',
          onclick: function () { columnSelect(platform.key, 'invert'); }
        }, '反选');
        var selectNone = h('button', {
          class: 'btn ghost mini', type: 'button',
          onclick: function () { columnSelect(platform.key, 'none'); }
        }, '全不选');
        exportBtn.addEventListener('click', function () {
          startExport(Array.from(col.selected), platform.key);
        });
        downloadBtn.addEventListener('click', function () {
          startDownload(Array.from(col.selected));
        });
        col.refs = {
          count: h('span', { class: 'muted small' }, fmtNumber(platform.total)),
          list: listHost, pager: pagerHost,
          exportBtn: exportBtn, downloadBtn: downloadBtn
        };
        grid.appendChild(h('div', { class: 'card platform-col' },
          h('div', { class: 'col-head' },
            h('span', { class: 'row-title' }, platform.name),
            col.refs.count),
          h('div', { class: 'col-tools' }, selectAll, invert, selectNone),
          h('div', { class: 'col-tools' }, exportBtn, downloadBtn),
          listHost,
          pagerHost));
      });

      exportSelected.addEventListener('click', function () {
        startExport(allSelectedKeys(), '');
      });
      downloadSelected.addEventListener('click', function () {
        startDownload(allSelectedKeys());
      });

      search.addEventListener('input', function () {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(function () {
          state.collected.q = search.value.trim();
          loadAll();
          updateInfo();
        }, 320);
      });

      root.replaceChildren(
        h('div', { class: 'card', style: 'margin-bottom:12px' },
          h('div', { class: 'toolbar' },
            exportSelected, downloadSelected,
            h('span', { class: 'spacer' }), info),
          h('div', { class: 'toolbar', style: 'margin-top:8px' },
            search, formatBoxes)),
        grid,
        jobHost);
      updateInfo();
      loadAll();
    }).catch(function (error) {
      setError(root, error, function () { renderCollected(root); });
    });
  }

  function renderNetdisk(root) {
    setLoading(root, '正在读取网盘备份状态…');
    api('/api/netdisk').then(function (data) {
      root.replaceChildren();
      var tabs = h('div', { class: 'tabs' });
      var host = h('div');
      var TABS = [['backup', '备份到网盘'], ['download', '文件下载']];
      TABS.forEach(function (item) {
        tabs.appendChild(h('button', {
          class: 'tab' + (state.netdisk.tab === item[0] ? ' active' : ''),
          type: 'button',
          onclick: function () {
            state.netdisk.tab = item[0];
            tabs.querySelectorAll('.tab').forEach(function (button, index) {
              button.classList.toggle('active', TABS[index][0] === item[0]);
            });
            renderTab();
          }
        }, item[1]));
      });
      root.appendChild(tabs);
      root.appendChild(host);

      function renderTab() {
        host.replaceChildren();
        if (state.netdisk.tab === 'backup') renderBackup();
        else renderDownload();
      }

      function renderBackup() {
        var mappings = data.mappings || [];
        var schedule = data.schedule || {};
        var files = mappings.reduce(function (sum, item) {
          return sum + (item.files || 0);
        }, 0);
        var bytes = mappings.reduce(function (sum, item) {
          return sum + (item.bytes || 0);
        }, 0);
        var schedText = schedule.mode === 'interval'
          ? ('每 ' + (schedule.interval_minutes || 120) + ' 分钟')
          : ('每日 ' + (schedule.time || '02:30'));
        host.appendChild(h('div', {
          class: 'muted small', style: 'margin-bottom:8px'
        }, '共 ' + mappings.length + ' 组映射 · 定时 ' + schedText
          + ' · 已备份 ' + fmtNumber(files) + ' 个文件 · ' + fmtBytes(bytes)
          + ' · 只读，备份 / 下载仍在桌面端'));
        if (!mappings.length) {
          host.appendChild(empty(
            '还没有备份目录。桌面端「网络库 → 备份到网盘」新增一组映射。'));
        }
        mappings.forEach(function (mapping) {
          var extras = [];
          if (mapping.exclude) extras.push('排除：' + mapping.exclude);
          if (mapping.max_file_mb) {
            extras.push('单文件上限 ' + mapping.max_file_mb + 'MB');
          }
          if (mapping.share) {
            extras.push('生成分享链接'
              + (mapping.last_share_url
                ? '：' + mapping.last_share_url : ''));
          }
          host.appendChild(h('div', {
            class: 'card', style: 'margin-bottom:12px'
          },
            h('div', { class: 'toolbar' },
              h('span', { class: 'badge' }, mapping.provider_name),
              h('span', {
                class: 'row-title',
                style: 'white-space:normal;overflow-wrap:anywhere',
                title: mapping.local_dir + ' → ' + mapping.remote_dir
              }, (mapping.local_dir || '（未选本地目录）') + ' → '
                + (mapping.remote_dir || '（未设云端目录）')),
              h('span', { class: 'badge ' + (mapping.enabled ? 'ok' : '') },
                mapping.enabled ? '启用' : '停用')),
            h('div', { class: 'muted small', style: 'margin-top:4px' },
              '已备份 ' + fmtNumber(mapping.files) + ' 个 · '
              + fmtBytes(mapping.bytes)
              + (mapping.last_run
                ? ' · 最近 ' + mapping.last_run
                  + (mapping.last_result ? ' · ' + mapping.last_result : '')
                : ' · 尚未运行')),
            extras.length ? h('div', {
              class: 'muted small', style: 'margin-top:4px'
            }, extras.join(' ｜ ')) : null));
        });

        var runs = data.runs || [];
        var runsCard = h('div', {
          class: 'card', style: 'margin-bottom:12px'
        }, h('div', { class: 'row-title', style: 'margin-bottom:8px' },
          '最近运行'));
        if (!runs.length) {
          runsCard.appendChild(empty('暂无运行记录。'));
        } else {
          var head = h('tr');
          ['完成时间', '本地目录', '扫描', '上传', '跳过', '失败', '大小', '状态']
            .forEach(function (title, index) {
              head.appendChild(h('th', {
                class: index >= 2 && index <= 6 ? 'num' : ''
              }, title));
            });
          var body = h('tbody');
          runs.forEach(function (row) {
            body.appendChild(h('tr', null,
              h('td', null, row.finished_at || '-'),
              h('td', { class: 'ellipsis' }, row.local_dir || '-'),
              h('td', { class: 'num' }, fmtNumber(row.scanned)),
              h('td', { class: 'num' }, fmtNumber(row.uploaded)),
              h('td', { class: 'num' }, fmtNumber(row.skipped)),
              h('td', { class: 'num' }, fmtNumber(row.failed)),
              h('td', { class: 'num' }, fmtBytes(row.bytes)),
              h('td', null, h('span', {
                class: 'badge ' + (row.ok ? 'ok' : 'warn')
              }, row.ok ? '成功' : '有失败'))));
          });
          runsCard.appendChild(h('div', { class: 'table-wrap' },
            h('table', { class: 'db-table' },
              h('thead', null, head), body)));
        }
        host.appendChild(runsCard);

        var log = data.log || [];
        host.appendChild(h('div', { class: 'card' },
          h('div', { class: 'row-title', style: 'margin-bottom:8px' },
            '任务日志（config/quark_backup_task.log）'),
          log.length
            ? h('pre', { class: 'log-box' }, log.join('\n'))
            : empty('暂无任务日志。')));
      }

      function renderDownload() {
        host.appendChild(h('div', { class: 'card' },
          h('div', { class: 'card-title' }, '文件下载'),
          h('div', { class: 'muted small' },
            '浏览四大网盘目录、断点续传下载与自动解压在桌面端；'
            + 'Web 端只读展示备份状态与运行记录。')));
      }

      renderTab();
    }).catch(function (error) {
      setError(root, error, function () { renderNetdisk(root); });
    });
  }

  function renderAssets(root) {
    setLoading(root, '正在读取资产概览…');
    api('/api/assets/summary').then(function (summary) {
      var tabs = h('div', { class: 'tabs' });
      var host = h('div');
      [['entries', '已获取资源'], ['db', '数据库浏览'], ['local', '本地资源'], ['offline', '线下现场视频']].forEach(function (pair) {
        tabs.appendChild(h('button', {
          class: 'tab' + (state.assets.tab === pair[0] ? ' active' : ''),
          type: 'button', dataset: { tab: pair[0] },
          onclick: function () {
            state.assets.tab = pair[0];
            tabs.querySelectorAll('.tab').forEach(function (button) {
              button.classList.toggle('active', button.dataset.tab === pair[0]);
            });
            renderTab();
          }
        }, pair[1]));
      });

      function renderTab() {
        if (state.assets.tab === 'entries') renderEntries(host);
        else if (state.assets.tab === 'db') renderDb(host);
        else if (state.assets.tab === 'offline') renderOffline(host);
        else renderLocal(host, summary);
      }

      root.replaceChildren(
        h('div', { class: 'grid cols-4' },
          statCard('导出 JSON 缓存', fmtNumber(summary.index.json_rows), '索引库记录'),
          statCard('媒体目录缓存', fmtNumber(summary.index.media_rows), '索引库记录'),
          statCard('本地分类', fmtNumber(summary.categories.length), '可浏览的导出目录'),
          statCard('导出根目录', summary.export_root_name || '-', '仅显示目录名')),
        h('div', { class: 'card', style: 'margin-top:12px' }, tabs, host));
      renderTab();
    }).catch(function (error) {
      setError(root, error, function () { renderAssets(root); });
    });
  }

  function renderEntries(host) {
    host.replaceChildren();
    var tableHost = h('div');
    var jobHost = h('div');
    var resultHost = h('div');
    var pagerHost = h('div', { class: 'pager' });
    var search = h('input', { class: 'input grow', placeholder: '搜索标题或作者', value: state.assets.q });
    var platform = h('select', { class: 'select' },
      h('option', { value: '' }, '全部平台'),
      h('option', { value: 'douyin' }, '抖音'),
      h('option', { value: 'bilibili' }, 'B站'),
      h('option', { value: 'weibo' }, '微博'),
      h('option', { value: 'kuaishou' }, '快手'),
      h('option', { value: 'xiaohongshu' }, '小红书'));
    platform.value = state.assets.platform;
    var quality = h('select', { class: 'select', title: 'B站视频画质' },
      h('option', { value: '最高画质' }, '最高画质'),
      h('option', { value: '4K' }, '4K'),
      h('option', { value: '1080P60' }, '1080P60'),
      h('option', { value: '1080P' }, '1080P'),
      h('option', { value: '720P' }, '720P'));
    var statusSel = h('select', { class: 'select', title: '媒体完整度筛选' },
      h('option', { value: '' }, '全部状态'),
      h('option', { value: 'missing' }, '该下未下'),
      h('option', { value: 'partial' }, '部分下载'),
      h('option', { value: 'ok' }, '已完整'),
      h('option', { value: 'text' }, '纯文本'),
      h('option', { value: 'nodl' }, '不可下载'));
    statusSel.value = state.assets.status || '';
    var dlBtn = h('button', { class: 'btn primary', type: 'button' }, '下载选中');
    var selectAll = h('input', { type: 'checkbox', title: '全选本页' });
    var selectedLabel = h('span', { class: 'muted small' });
    var countsLabel = h('span', { class: 'muted small' });
    var selected = state.assets.dlSelected || (state.assets.dlSelected = new Set());
    var running = false;
    var STATUS_BADGE = {
      ok: ['已完整', 'ok'], partial: ['部分下载', 'warn'],
      missing: ['该下未下', 'danger'], text: ['纯文本', ''],
      nodl: ['不可下载', 'warn']
    };

    function updateButtons() {
      dlBtn.disabled = running || selected.size === 0;
      dlBtn.textContent = running ? '下载中…' : '下载选中';
      selectedLabel.textContent = selected.size ? '已选 ' + selected.size + ' 条' : '';
    }

    function load() {
      setLoading(tableHost, '正在读取已获取资源…');
      var url = '/api/assets/entries-status?page=' + state.assets.page
        + '&page_size=20&q=' + encodeURIComponent(state.assets.q)
        + '&platform=' + encodeURIComponent(state.assets.platform)
        + '&status=' + encodeURIComponent(state.assets.status || '');
      api(url)
        .then(function (data) {
          if (data.counts) {
            var parts = [];
            ['ok', 'partial', 'missing', 'text', 'nodl'].forEach(function (key) {
              if (data.counts[key]) {
                parts.push((data.labels[key] || key) + ' ' + data.counts[key]);
              }
            });
            countsLabel.textContent = parts.join(' · ');
          } else {
            countsLabel.textContent = '';
          }
          if (!data.items.length) {
            tableHost.replaceChildren(empty('没有匹配的导出 JSON。可清空搜索词或切换平台。'));
            pagerHost.replaceChildren();
            selected.clear();
            updateButtons();
            return;
          }
          var known = new Set(data.items.map(function (item) { return item.path; }));
          Array.from(selected).forEach(function (path) {
            if (!known.has(path) && selected.size > data.items.length) {
              if (data.items.every(function (item) { return !known.has(path) || selected.has(item.path); })) {
                selected.delete(path);
              }
            }
          });
          var body = h('tbody');
          data.items.forEach(function (item) {
            var check = h('input', {
              type: 'checkbox', checked: selected.has(item.path)
            });
            check.addEventListener('change', function () {
              if (check.checked) selected.add(item.path);
              else selected.delete(item.path);
              updateButtons();
            });
            var title = item.url
              ? h('a', { href: item.url, target: '_blank', rel: 'noopener noreferrer' }, item.title)
              : item.title;
            var badge = STATUS_BADGE[item.status];
            var statusCell = badge
              ? h('span', { class: badge[1] ? ('badge ' + badge[1]) : 'muted small' }, badge[0])
              : '-';
            var mediaInfo = item.n_img || item.n_vid
              ? (item.n_img ? '图 ' + item.n_img : '') +
                (item.n_img && item.n_vid ? ' / ' : '') +
                (item.n_vid ? '视 ' + item.n_vid : '')
              : '';
            var noDlBtn = h('button', {
              class: 'btn', type: 'button', title: item.no_dl
                ? '解除后重新计入媒体完整度并可下载'
                : '标记后不计入「该下未下」，下载时跳过'
            }, item.no_dl ? '解除' : '标记');
            noDlBtn.addEventListener('click', function () {
              if (!window.confirm(item.no_dl
                ? '解除「' + (item.title || '该条目') + '」的不可下载标记？'
                : '将「' + (item.title || '该条目') + '」标记为不可下载？\n'
                  + '标记后不计入「该下未下」，下载时会被跳过。')) {
                return;
              }
              noDlBtn.disabled = true;
              post('/api/assets/no-dl', {
                path: item.path, marked: !item.no_dl
              }).then(function (result) {
                toast((result && result.message) || '已保存', 'success');
                load();
              }).catch(function (error) {
                noDlBtn.disabled = false;
                toast('操作失败：' + error.message, 'error');
              });
            });
            body.appendChild(h('tr', null,
              h('td', null, check),
              h('td', { class: 'ellipsis' }, title),
              h('td', null, item.author || '-'),
              h('td', null, platformLabel(item.platform)),
              h('td', null, statusCell),
              h('td', { class: 'muted small' }, mediaInfo || '-'),
              h('td', null, fmtTime(item.publish_time)),
              h('td', { class: 'num' }, fmtNumber(item.play)),
              h('td', { class: 'num' }, fmtNumber(item.like)),
              h('td', { class: 'ellipsis' }, item.file || '-'),
              h('td', null, noDlBtn)));
          });
          tableHost.replaceChildren();
          if (data.truncated) {
            tableHost.appendChild(h('div', { class: 'muted small', style: 'margin:6px 2px' },
              '已获取资源仅展示前 2000 条 JSON 概览。'));
          }
          tableHost.appendChild(h('div', { class: 'table-wrap' },
            h('table', null,
              h('thead', null, h('tr', null,
                h('th', { style: 'width:36px' }, ''),
                h('th', null, '标题'), h('th', null, '作者'), h('th', null, '平台'),
                h('th', null, '状态'), h('th', null, '本地媒体'),
                h('th', null, '发布时间'), h('th', { class: 'num' }, '播放'),
                h('th', { class: 'num' }, '点赞'), h('th', null, '导出文件'),
                h('th', null, '操作'))),
              body)));
          selectAll.checked = data.items.length > 0
            && data.items.every(function (item) { return selected.has(item.path); });
          selectAll.indeterminate = selected.size > 0 && !selectAll.checked;
          updateButtons();
          pager(pagerHost, data.page, data.pages, data.total, function (page) {
            state.assets.page = page;
            load();
          });
        }).catch(function (error) { setError(tableHost, error, load); });
    }

    selectAll.addEventListener('change', function () {
      var url = '/api/assets/entries-status?page=' + state.assets.page
        + '&page_size=20&q=' + encodeURIComponent(state.assets.q)
        + '&platform=' + encodeURIComponent(state.assets.platform)
        + '&status=' + encodeURIComponent(state.assets.status || '');
      api(url)
        .then(function (data) {
          data.items.forEach(function (item) {
            if (selectAll.checked) selected.add(item.path);
            else selected.delete(item.path);
          });
          load();
        });
    });

    statusSel.addEventListener('change', function () {
      state.assets.status = statusSel.value;
      state.assets.page = 1;
      load();
    });

    dlBtn.addEventListener('click', function () {
      if (!selected.size || running) return;
      var paths = Array.from(selected);
      running = true;
      updateButtons();
      post('/api/assets/download', {
        paths: paths, quality: quality.value
      }).then(function (data) {
        watchJob(jobHost, data.job, function (job) {
          running = false;
          updateButtons();
          var summary = job.result && job.result.summary
            ? job.result.summary : '下载完成';
          toast(summary, 'success');
          showResult(job);
          selected.clear();
          load();
        }, {
          cancelable: true,
          onSettled: function (job) {
            running = false;
            updateButtons();
            if (job.status === 'cancelled') {
              toast('下载已取消', 'info');
            }
          }
        });
      }).catch(function (error) {
        running = false;
        updateButtons();
        toast('下载失败：' + error.message, 'error');
      });
    });

    function showResult(job) {
      var result = job.result || {};
      var rows = result.results || [];
      if (!rows.length) return;
      var head = h('tr', null,
        h('th', null, '条目'), h('th', null, '结果'), h('th', null, '产出'));
      var body = h('tbody');
      rows.forEach(function (row) {
        var parts = [];
        if (row.kind === 'video') {
          parts.push(row.path ? '视频 ' + row.path : '视频 ×' + (row.videos || 1));
          if (row.skipped) parts.push('跳过已有 ' + row.skipped);
          if (row.covers) parts.push('封面 ' + row.covers);
        } else if (row.kind === 'images') {
          var segs = [];
          if (row.images) segs.push('图片 ' + row.images);
          if (row.live) segs.push('Live图 ' + row.live);
          if (row.covers) segs.push('封面 ' + row.covers);
          if (row.skipped) segs.push('跳过已有 ' + row.skipped);
          parts.push(segs.join(' / ') || '无新文件');
        }
        body.appendChild(h('tr', null,
          h('td', { class: 'ellipsis' }, row.title || row.file),
          h('td', null, row.ok ? '成功' : '失败'),
          h('td', { class: 'muted small' }, row.ok ? (parts.join('；') || '-') : (row.error || '-'))));
      });
      resultHost.replaceChildren(h('div', { class: 'card', style: 'margin:12px 0' },
        h('div', { class: 'row-title', style: 'margin-bottom:8px' },
          result.summary || '下载结果'),
        h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' }, h('thead', null, head), body))));
    }

    search.addEventListener('input', function () {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(function () {
        state.assets.q = search.value.trim();
        state.assets.page = 1;
        load();
      }, 320);
    });
    platform.addEventListener('change', function () {
      state.assets.platform = platform.value;
      state.assets.page = 1;
      load();
    });
    host.appendChild(h('div', { class: 'toolbar' },
      search, platform, statusSel, h('span', { style: 'flex:1' }),
      h('label', { class: 'muted small' }, selectAll, ' 本页'),
      selectedLabel, countsLabel, quality, dlBtn));
    host.appendChild(jobHost);
    host.appendChild(tableHost);
    host.appendChild(resultHost);
    host.appendChild(pagerHost);
    load();
  }

  function renderDb(host) {
    host.replaceChildren();
    var controls = h('div', { class: 'toolbar' });
    var tableHost = h('div');
    var pagerHost = h('div', { class: 'pager' });
    var platform = h('select', { class: 'select' });
    var search = h('input', { class: 'input grow', placeholder: '搜索标题、作者、正文或作品 ID', value: state.assets.dbQ });
    var columns = null;

    setLoading(tableHost, '正在读取数据库表…');
    api('/api/assets/db/tables').then(function (data) {
      if (!data.tables.length) {
        tableHost.replaceChildren(empty('暂无增量采集数据库。先在桌面端完成一次账号增量采集。'));
        return;
      }
      data.tables.forEach(function (table) {
        platform.appendChild(h('option', { value: table.name }, table.name + '（' + fmtNumber(table.rows) + ' 行）'));
      });
      var names = data.tables.map(function (table) { return table.name; });
      if (!state.assets.dbPlatform || names.indexOf(state.assets.dbPlatform) < 0) {
        state.assets.dbPlatform = names.indexOf('douyin') >= 0 ? 'douyin' : names[0];
      }
      platform.value = state.assets.dbPlatform;
      platform.addEventListener('change', function () {
        state.assets.dbPlatform = platform.value;
        state.assets.dbPage = 1;
        load();
      });
      search.addEventListener('input', function () {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(function () {
          state.assets.dbQ = search.value.trim();
          state.assets.dbPage = 1;
          load();
        }, 320);
      });
      controls.appendChild(platform);
      controls.appendChild(search);
      host.appendChild(controls);
      host.appendChild(tableHost);
      host.appendChild(pagerHost);
      load();
    }).catch(function (error) { setError(tableHost, error); });

    function load() {
      setLoading(tableHost, '正在查询…');
      api('/api/assets/db/rows?platform=' + encodeURIComponent(state.assets.dbPlatform) +
        '&page=' + state.assets.dbPage + '&page_size=50&q=' + encodeURIComponent(state.assets.dbQ) +
        '&order_by=' + encodeURIComponent(state.assets.dbOrder) + '&ascending=' + (state.assets.dbAsc ? '1' : '0'))
        .then(function (data) {
          columns = data.columns;
          if (!data.rows.length) {
            tableHost.replaceChildren(empty('没有匹配的数据。可清空搜索词或切换平台。'));
            pagerHost.replaceChildren();
            return;
          }
          var head = h('tr');
          columns.forEach(function (column) {
            head.appendChild(h('th', {
              class: 'sortable', dataset: { order: column },
              onclick: function () {
                if (state.assets.dbOrder === column) state.assets.dbAsc = !state.assets.dbAsc;
                else { state.assets.dbOrder = column; state.assets.dbAsc = false; }
                load();
              }
            }, dbColumnLabel(column), state.assets.dbOrder === column ? (state.assets.dbAsc ? ' ↑' : ' ↓') : ''));
          });
          var body = h('tbody');
          data.rows.forEach(function (row) {
            var tr = h('tr');
            columns.forEach(function (column) {
              var value = row[column];
              if (value === null || value === undefined) value = '';
              var text = String(value);
              if (text.length > 300) text = text.slice(0, 300) + '…';
              tr.appendChild(h('td', { class: column === 'description' ? 'ellipsis' : '' }, text));
            });
            body.appendChild(tr);
          });
          tableHost.replaceChildren(h('div', { class: 'table-wrap' },
            h('table', { class: 'db-table' }, h('thead', null, head), body)));
          pager(pagerHost, data.page, data.pages, data.total, function (page) {
            state.assets.dbPage = page;
            load();
          });
        }).catch(function (error) { setError(tableHost, error, load); });
    }
  }

  function renderAudio(host) {
    host.replaceChildren();
    setLoading(host, '正在读取工作目录…');
    Promise.all([
      api('/api/tools/audio/overview'),
      api('/api/tools/audio/sources?limit=300')
    ]).then(function (results) {
      var overview = results[0];
      var sources = results[1];
      host.replaceChildren();
      if (!overview.has_work_dir) {
        host.appendChild(empty('尚未设置工作目录。请先在桌面端「全局设置」中配置工作目录。'));
        return;
      }
      var stats = h('div', { class: 'grid cols-4', style: 'margin-bottom:12px' });
      stats.appendChild(statCard('视频', fmtNumber(sources.total), '工作目录内'));
      stats.appendChild(statCard('提取模式', overview.mode, '与桌面端一致'));
      stats.appendChild(statCard('已列出', fmtNumber(sources.items.length),
        sources.truncated ? '仅前 300 个' : '全部'));
      stats.appendChild(statCard('输出', '同目录', '按音轨编码'));
      host.appendChild(stats);
      host.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'muted small' }, overview.note || '')));
      if (!sources.items.length) {
        host.appendChild(empty('工作目录下没有可提取的视频。'));
        return;
      }

      var selected = state.audio.selected;
      var known = new Set(sources.items.map(function (item) { return item.path; }));
      Array.from(selected).forEach(function (path) {
        if (!known.has(path)) selected.delete(path);
      });

      var selectedLabel = h('span', { class: 'muted small' });
      var selectAll = h('input', { type: 'checkbox' });
      var extract = h('button', { class: 'btn primary', type: 'button' }, '提取选中音频');
      var rescan = h('button', { class: 'btn', type: 'button' }, '刷新');
      var toolbar = h('div', { class: 'toolbar' },
        h('label', { class: 'muted small' }, selectAll, ' 全选'),
        selectedLabel,
        h('span', { style: 'flex:1' }),
        rescan, extract);
      host.appendChild(toolbar);
      var jobHost = h('div');
      var listHost = h('div');
      var resultHost = h('div');
      host.appendChild(jobHost);
      host.appendChild(listHost);
      host.appendChild(resultHost);

      var running = false;
      var columns = [
        { key: 'name', label: '文件名' },
        { key: 'dir', label: '目录' },
        { key: 'size', label: '大小' },
        { key: 'mtime', label: '修改时间' }
      ];

      function updateButtons() {
        extract.disabled = running || selected.size === 0;
        extract.textContent = running ? '提取中…' : '提取选中音频';
        selectedLabel.textContent = '已选 ' + selected.size + ' 个';
        selectAll.checked = sources.items.length > 0
          && selected.size === sources.items.length;
        selectAll.indeterminate = selected.size > 0
          && selected.size < sources.items.length;
      }

      function draw() {
        var rows = sortRows(sources.items, columns, state.audio);
        var head = h('tr', null, h('th', { style: 'width:36px' }, ''));
        columns.forEach(function (column) {
          var mark = state.audio.order === column.key
            ? (state.audio.asc ? ' ↑' : ' ↓') : '';
          head.appendChild(h('th', {
            class: 'sortable',
            onclick: function () {
              cycleSort(state.audio, column.key);
              draw();
            }
          }, column.label + mark));
        });
        var body = h('tbody');
        rows.forEach(function (item) {
          var check = h('input', {
            type: 'checkbox', checked: selected.has(item.path)
          });
          check.addEventListener('change', function () {
            if (check.checked) selected.add(item.path);
            else selected.delete(item.path);
            updateButtons();
          });
          body.appendChild(h('tr', null,
            h('td', null, check),
            h('td', null, item.name || '-'),
            h('td', { class: 'muted small' }, item.dir || '-'),
            h('td', { class: 'num' }, fmtBytes(item.size)),
            h('td', null, item.mtime || '-')));
        });
        listHost.replaceChildren(h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' }, h('thead', null, head), body)));
        updateButtons();
      }

      selectAll.addEventListener('change', function () {
        selected.clear();
        if (selectAll.checked) {
          sources.items.forEach(function (item) { selected.add(item.path); });
        }
        draw();
      });
      rescan.addEventListener('click', function () {
        selected.clear();
        renderAudio(host);
      });
      extract.addEventListener('click', function () {
        if (!selected.size || running) return;
        var paths = Array.from(selected);
        running = true;
        updateButtons();
        post('/api/tools/audio/extract', {
          paths: paths,
          all_tracks: !!overview.all_tracks
        }).then(function (data) {
          watchJob(jobHost, data.job, function (job) {
            running = false;
            updateButtons();
            var summary = job.result && job.result.summary
              ? job.result.summary : '提取完成';
            toast(summary, 'success');
            showResult(job);
            selected.clear();
            draw();
          }, {
            cancelable: true,
            onSettled: function (job) {
              running = false;
              updateButtons();
              if (job.status === 'cancelled') {
                toast('音频提取已取消', 'info');
              }
            }
          });
        }).catch(function (error) {
          running = false;
          updateButtons();
          toast('提取失败：' + error.message, 'error');
        });
      });
      draw();
    }).catch(function (error) {
      setError(host, error, function () { renderAudio(host); });
    });

    function showResult(job) {
      var result = job.result || {};
      var rows = result.results || [];
      if (!rows.length) return;
      var head = h('tr', null,
        h('th', null, '文件'), h('th', null, '结果'),
        h('th', null, '输出 / 错误'));
      var body = h('tbody');
      rows.forEach(function (row) {
        body.appendChild(h('tr', null,
          h('td', null, row.file || '-'),
          h('td', null, row.ok ? '成功' : '失败'),
          h('td', { class: 'muted small' },
            row.ok ? (row.outputs || []).join('、') : (row.error || ''))));
      });
      resultHost.replaceChildren(h('div', {
        class: 'card', style: 'margin-top:12px'
      },
        h('div', { class: 'row-title', style: 'margin-bottom:8px' },
          result.summary || '提取结果'),
        h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' }, h('thead', null, head), body))));
    }
  }

  function renderOrganize(host) {
    host.replaceChildren();
    setLoading(host, '正在扫描工作目录…');
    Promise.all([
      api('/api/tools/organize/overview'),
      api('/api/tools/organize/preview?limit=100')
    ]).then(function (results) {
      var overview = results[0];
      var preview = results[1];
      host.replaceChildren();
      if (!overview.has_work_dir) {
        host.appendChild(empty('尚未设置工作目录。请先在桌面端「全局设置」中配置工作目录。'));
        return;
      }
      var stats = h('div', { class: 'grid cols-4', style: 'margin-bottom:12px' });
      stats.appendChild(statCard('视频', fmtNumber(overview.files), '工作目录顶层'));
      stats.appendChild(statCard('日期分组', fmtNumber(overview.groups), '按文件名日期'));
      stats.appendChild(statCard('无日期', fmtNumber(overview.no_date), '有视频但无日期'));
      stats.appendChild(statCard('工作目录', overview.work_dir || '-', '仅目录名'));
      host.appendChild(stats);
      host.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'muted small' }, overview.note || '')));

      var tableHost = h('div');
      var jobHost = h('div');
      host.appendChild(jobHost);
      host.appendChild(tableHost);
      var groups = preview.groups || [];
      var selected = state.organize.selected;
      var known = new Set(groups.map(function (item) { return item.date; }));
      Array.from(selected).forEach(function (date) {
        if (!known.has(date)) selected.delete(date);
      });
      var runBtn = h('button', {
        class: 'btn primary', type: 'button', disabled: true
      }, '整理选中分组');
      var selectAll = h('input', { type: 'checkbox', title: '全部分组' });
      var selectedLabel = h('span', { class: 'muted small' }, '');
      var running = false;

      function updateButtons() {
        runBtn.disabled = running || selected.size === 0;
        runBtn.textContent = running ? '整理中…' : '整理选中分组';
        selectedLabel.textContent = selected.size
          ? '已选 ' + selected.size + ' 组 · '
            + groups.filter(function (item) {
              return selected.has(item.date);
            }).reduce(function (sum, item) {
              return sum + item.count;
            }, 0) + ' 个视频'
          : '未选择分组';
        selectAll.checked = groups.length > 0
          && selected.size === groups.length;
        selectAll.indeterminate = selected.size > 0
          && selected.size < groups.length;
      }

      if (!groups.length) {
        tableHost.appendChild(empty('工作目录顶层没有可整理的视频。'));
      } else {
        host.appendChild(h('div', { class: 'toolbar' },
          h('label', { class: 'muted small' }, selectAll, ' 全选'),
          selectedLabel, h('span', { style: 'flex:1' }), runBtn));
        var columns = [
          { key: 'date', label: '日期分组' },
          { key: 'count', label: '文件数' },
          { key: 'size', label: '大小' }
        ];
        var rows = sortRows(groups, columns, state.organize);
        var head = h('tr', null, h('th', { style: 'width:36px' }, ''));
        columns.forEach(function (column) {
          var mark = state.organize.order === column.key
            ? (state.organize.asc ? ' ↑' : ' ↓') : '';
          head.appendChild(h('th', {
            class: 'sortable',
            onclick: function () {
              cycleSort(state.organize, column.key);
              renderOrganize(host);
            }
          }, column.label + mark));
        });
        head.appendChild(h('th', null, '文件预览'));
        var body = h('tbody');
        rows.forEach(function (item) {
          var check = h('input', {
            type: 'checkbox', checked: selected.has(item.date)
          });
          check.addEventListener('change', function () {
            if (check.checked) selected.add(item.date);
            else selected.delete(item.date);
            updateButtons();
          });
          body.appendChild(h('tr', null,
            h('td', null, check),
            h('td', null, item.date || '-'),
            h('td', { class: 'num' }, fmtNumber(item.count)),
            h('td', { class: 'num' }, fmtBytes(item.size)),
            h('td', { class: 'muted small' },
              (item.files || []).slice(0, 4).join('、')
              + ((item.files || []).length > 4 ? ' …' : ''))));
        });
        tableHost.appendChild(h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' }, h('thead', null, head), body)));
      }
      updateButtons();

      selectAll.addEventListener('change', function () {
        selected.clear();
        if (selectAll.checked) {
          groups.forEach(function (item) { selected.add(item.date); });
        }
        renderOrganize(host);
      });
      runBtn.addEventListener('click', function () {
        if (!selected.size || running) return;
        var dates = Array.from(selected);
        var count = groups.filter(function (item) {
          return selected.has(item.date);
        }).reduce(function (sum, item) {
          return sum + item.count;
        }, 0);
        if (!window.confirm(
          '确认整理\n\n将把 ' + dates.length + ' 个日期分组（共 ' + count
          + ' 个视频）移动到对应的日期文件夹。\n附属文件随视频一起移动；'
          + '无日期的视频不会被移动。\n\n确认继续？')) {
          return;
        }
        running = true;
        updateButtons();
        post('/api/tools/organize/run', {
          dates: dates, confirmed: true
        }).then(function (data) {
          watchJob(jobHost, data.job, function (job) {
            running = false;
            updateButtons();
            var summary = job.result && job.result.summary
              ? job.result.summary : '整理完成';
            toast(summary, 'success');
            selected.clear();
            renderOrganize(host);
          }, {
            onSettled: function (job) {
              running = false;
              updateButtons();
            }
          });
        }).catch(function (error) {
          running = false;
          updateButtons();
          toast('整理失败：' + error.message, 'error');
        });
      });
      if (preview.no_date && preview.no_date.length) {
        var card = h('div', { class: 'card', style: 'margin-top:12px' },
          h('div', { class: 'row-title', style: 'margin-bottom:8px' }, '无日期文件名'));
        var badges = h('div', { class: 'days' });
        preview.no_date.forEach(function (name) {
          badges.appendChild(h('span', { class: 'badge' }, name));
        });
        card.appendChild(badges);
        host.appendChild(card);
      }
      host.appendChild(h('div', { class: 'muted small', style: 'margin-top:8px' },
        preview.note || '只读预览，不会移动或改名。'));
    }).catch(function (error) {
      setError(host, error, function () { renderOrganize(host); });
    });
  }

  function renderRename(host) {
    host.replaceChildren();
    setLoading(host, '正在读取工作目录…');
    Promise.all([
      api('/api/tools/rename/overview'),
      api('/api/tools/rename/preview?limit=300')
    ]).then(function (results) {
      var overview = results[0];
      var preview = results[1];
      host.replaceChildren();
      if (!overview.has_work_dir) {
        host.appendChild(empty('尚未设置工作目录。请先在桌面端「全局设置」中配置工作目录。'));
        return;
      }
      var stats = h('div', { class: 'grid cols-4', style: 'margin-bottom:12px' });
      stats.appendChild(statCard('视频', fmtNumber(overview.files), '工作目录内'));
      stats.appendChild(statCard('已标准命名', fmtNumber(overview.standard), '日期_时间_前缀'));
      stats.appendChild(statCard('待命名', fmtNumber(overview.nonstandard), '点击右侧「命名」'));
      stats.appendChild(statCard('已列出', fmtNumber(preview.items.length),
        preview.truncated ? '仅前 300 个' : '全部'));
      host.appendChild(stats);
      host.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'muted small' }, overview.note || '')));
      if (!preview.items.length) {
        host.appendChild(empty('工作目录下没有可预览的视频。'));
        return;
      }

      var jobHost = h('div');
      host.appendChild(jobHost);

      function openRenameForm(item) {
        var overlay = h('div', { class: 'preview-overlay' });
        var box = h('div', { class: 'preview-box', style: 'max-width:520px' });
        var caption = h('div', { class: 'preview-caption' },
          h('span', { class: 'ellipsis' }, item.name),
          h('button', { class: 'btn', type: 'button' }, '关闭'));
        var closeBtn = caption.lastChild;
        var form = h('div', { style: 'padding:14px; overflow:auto' });
        var running = false;

        var accountSel = h('select', { class: 'select', style: 'width:100%' });
        (overview.accounts || []).forEach(function (name) {
          accountSel.appendChild(h('option', { value: name }, name));
        });
        var typeBoxes = [];
        var typeRow = h('div', { class: 'row-files', style: 'margin-top:4px' });
        (overview.types || []).forEach(function (name) {
          var box = h('input', { type: 'checkbox' });
          typeBoxes.push({ name: name, box: box });
          typeRow.appendChild(h('label', {
            class: 'muted small', style: 'margin:2px 6px'
          }, box, ' ' + name));
        });
        var danmuBox = h('input', { type: 'checkbox' });
        var platformSel = h('select', { class: 'select' });
        platformSel.appendChild(h('option', { value: '' }, '不标平台'));
        (overview.platforms || []).forEach(function (name) {
          platformSel.appendChild(h('option', { value: name }, name));
        });
        if (item.platform) {
          var label = { douyin: '抖音', bilibili: 'B站', weibo: '微博',
            kuaishou: '快手', xiaohongshu: '小红书' }[item.platform];
          if (label) platformSel.value = label;
        }
        var titleEntry = h('input', {
          class: 'input', style: 'width:100%',
          value: item.title && item.title !== '无标题' ? item.title : ''
        });
        var copyrightText = String(overview.copyright_text || '');
        var copyrightBox = h('input', {
          type: 'checkbox',
          checked: !!overview.copyright_enabled && !!copyrightText
        });
        if (!copyrightText) copyrightBox.disabled = true;
        var resultLine = h('div', { class: 'muted small', style: 'margin-top:8px' });
        var submit = h('button', { class: 'btn primary', type: 'button' }, '确认改名');

        function chosenTypes() {
          return typeBoxes.filter(function (entry) {
            return entry.box.checked;
          }).map(function (entry) { return entry.name; });
        }

        submit.addEventListener('click', function () {
          if (running) return;
          var account = accountSel.value;
          var types = chosenTypes();
          if (!account) { resultLine.textContent = '请选择直播账户'; return; }
          if (!types.length) { resultLine.textContent = '请至少选择一种直播类型'; return; }
          var title = titleEntry.value.trim();
          var previewName = (item.date || '0000-00-00') + '_' + item.time + '_'
            + account + '_' + types.join('+') + '_'
            + (danmuBox.checked ? '有弹幕' : '无弹幕')
            + (platformSel.value ? '_' + platformSel.value : '')
            + (title ? '_' + title : '');
          var copyrightNote = copyrightBox.checked
            ? ('将写入版权标签（' + (copyrightText.length > 60
                ? copyrightText.slice(0, 60) + '…' : copyrightText) + '）。\n')
            : '不写入版权标签。\n';
          if (!window.confirm(
            '确认改名\n\n' + item.name + ' →\n' + previewName + '\n\n'
            + '附属文件会同步改名；' + copyrightNote + '确认继续？')) {
            return;
          }
          running = true;
          submit.disabled = true;
          submit.textContent = '处理中…';
          post('/api/tools/rename/execute', {
            path: item.path, account: account, types: types,
            danmu: danmuBox.checked, platform: platformSel.value,
            title: title, copyright: copyrightBox.checked, confirmed: true
          }).then(function (data) {
            watchJob(jobHost, data.job, function (job) {
              var result = job.result || {};
              var message = result.message || '改名完成';
              var copyright = result.copyright || {};
              var warn = copyright.requested && copyright.ok === false;
              resultLine.textContent = message;
              toast(message, warn ? 'warning' : 'success');
              submit.textContent = '完成';
              setTimeout(function () { overlay.remove(); renderRename(host); }, 900);
            }, {
              onSettled: function (job) {
                if (job.status === 'error') {
                  running = false;
                  submit.disabled = false;
                  submit.textContent = '确认改名';
                  resultLine.textContent = job.error || '改名失败';
                }
              }
            });
          }).catch(function (error) {
            running = false;
            submit.disabled = false;
            submit.textContent = '确认改名';
            resultLine.textContent = '改名失败：' + error.message;
          });
        });

        closeBtn.addEventListener('click', function () { overlay.remove(); });
        overlay.addEventListener('click', function (event) {
          if (event.target === overlay) overlay.remove();
        });

        form.appendChild(h('div', { class: 'muted small' }, '直播账户'));
        form.appendChild(accountSel);
        form.appendChild(h('div', { class: 'muted small', style: 'margin-top:10px' }, '直播类型（可多选）'));
        form.appendChild(typeRow);
        form.appendChild(h('label', { class: 'muted small', style: 'margin-top:10px; display:block' },
          danmuBox, ' 有弹幕'));
        form.appendChild(h('div', { class: 'muted small', style: 'margin-top:10px' }, '平台'));
        form.appendChild(platformSel);
        form.appendChild(h('div', { class: 'muted small', style: 'margin-top:10px' }, '标题（留空用提取标题）'));
        form.appendChild(titleEntry);
        form.appendChild(h('label', {
          class: 'muted small', style: 'margin-top:10px; display:block'
        }, copyrightBox, ' 写入版权标签'));
        form.appendChild(h('div', { class: 'muted small' },
          copyrightText
            ? ('附加信息：' + copyrightText)
            : '桌面端未配置版权附加信息，无法写入；可在桌面端命名投稿设置中填写。'));
        form.appendChild(resultLine);
        var footer = h('div', { style: 'margin-top:12px; text-align:right' });
        footer.appendChild(submit);
        form.appendChild(footer);
        box.appendChild(caption);
        box.appendChild(form);
        overlay.appendChild(box);
        document.body.appendChild(overlay);
      }

      var columns = [
        { key: 'name', label: '文件名' },
        { key: 'platform', label: '平台' },
        { key: 'date', label: '日期' },
        { key: 'time', label: '时间' },
        { key: 'title', label: '提取标题' },
        { key: 'standard', label: '已标准命名', bool: true }
      ];
      var rows = sortRows(preview.items, columns, state.rename);
      var head = h('tr');
      columns.forEach(function (column) {
        var mark = state.rename.order === column.key
          ? (state.rename.asc ? ' ↑' : ' ↓') : '';
        head.appendChild(h('th', {
          class: 'sortable',
          onclick: function () {
            cycleSort(state.rename, column.key);
            renderRename(host);
          }
        }, column.label + mark));
      });
      head.appendChild(h('th', null, '操作'));
      var body = h('tbody');
      rows.forEach(function (item) {
        var actionCell = h('td', null);
        if (!item.standard) {
          var renameBtn = h('button', { class: 'btn', type: 'button' }, '命名');
          renameBtn.addEventListener('click', function () {
            openRenameForm(item);
          });
          actionCell.appendChild(renameBtn);
        } else {
          actionCell.appendChild(h('span', { class: 'muted small' }, '—'));
        }
        body.appendChild(h('tr', null,
          h('td', { class: 'ellipsis' }, item.name || '-'),
          h('td', null, item.platform ? platformLabel(item.platform) : '-'),
          h('td', null, item.date || '-'),
          h('td', null,
            item.time && item.time !== '00-00-00' ? item.time : '-'),
          h('td', { class: 'ellipsis', title: item.title || '' },
            item.title || '-'),
          h('td', null, item.standard ? '是' : '否'),
          actionCell));
      });
      host.appendChild(h('div', { class: 'table-wrap' },
        h('table', { class: 'db-table' }, h('thead', null, head), body)));
      host.appendChild(h('div', { class: 'muted small', style: 'margin-top:8px' },
        preview.note || '点击「命名」生成标准文件名。'));
    }).catch(function (error) {
      setError(host, error, function () { renderRename(host); });
    });
  }

  function renderVideo(host) {
    host.replaceChildren();
    setLoading(host, '正在读取工作目录…');
    api('/api/tools/video/overview').then(function (overview) {
      if (!overview.has_work_dir) {
        host.replaceChildren();
        host.appendChild(empty('尚未设置工作目录。请先在桌面端「全局设置」中配置工作目录。'));
        return null;
      }
      return api('/api/tools/video/sources?limit=300').then(function (sources) {
        return { overview: overview, sources: sources };
      });
    }).then(function (results) {
      if (!results) return;
      var overview = results.overview;
      var sources = results.sources;
      host.replaceChildren();
      var stats = h('div', { class: 'grid cols-4', style: 'margin-bottom:12px' });
      stats.appendChild(statCard('视频', fmtNumber(sources.total), '工作目录内'));
      stats.appendChild(statCard('输出格式', overview.output_extension || '-', '桌面端配置'));
      stats.appendChild(statCard('已列出', fmtNumber(sources.items.length),
        sources.truncated ? '仅前 300 个' : '全部'));
      stats.appendChild(statCard('处理方式', '只生成新文件', '不覆盖已有文件'));
      host.appendChild(stats);
      host.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'muted small' }, overview.note || '')));
      if (!sources.items.length) {
        host.appendChild(empty('工作目录下没有可操作的视频。'));
        return;
      }

      var selected = state.video.selected;
      var known = new Set(sources.items.map(function (item) { return item.path; }));
      Array.from(selected).forEach(function (path) {
        if (!known.has(path)) selected.delete(path);
      });

      var selectedLabel = h('span', { class: 'muted small' });
      var selectAll = h('input', { type: 'checkbox' });
      var rescan = h('button', { class: 'btn', type: 'button' }, '刷新');
      host.appendChild(h('div', { class: 'toolbar' },
        h('label', { class: 'muted small' }, selectAll, ' 全选'),
        selectedLabel, h('span', { style: 'flex:1' }), rescan));

      var listHost = h('div');
      var opHost = h('div', { class: 'card', style: 'margin-top:12px' });
      var jobHost = h('div');
      var resultHost = h('div');
      host.appendChild(listHost);
      host.appendChild(opHost);
      host.appendChild(jobHost);
      host.appendChild(resultHost);

      var running = false;
      var actionRefreshers = [];
      var columns = [
        { key: 'name', label: '文件名' },
        { key: 'dir', label: '目录' },
        { key: 'size', label: '大小' },
        { key: 'mtime', label: '修改时间' }
      ];

      var tabs = h('div', { class: 'tabs' });
      var tabHost = h('div');
      [['convert', '转换'], ['compress', '压缩'], ['merge', '合并'],
       ['cut', '切割'], ['snapshot', '截图'], ['tracks', '轨道'],
       ['metadata', '元数据']].forEach(function (pair) {
        tabs.appendChild(h('button', {
          class: 'tab' + (state.video.tab === pair[0] ? ' active' : ''),
          type: 'button', dataset: { tab: pair[0] },
          onclick: function () {
            state.video.tab = pair[0];
            tabs.querySelectorAll('.tab').forEach(function (button) {
              button.classList.toggle('active', button.dataset.tab === pair[0]);
            });
            renderTab();
          }
        }, pair[1]));
      });
      opHost.appendChild(tabs);
      opHost.appendChild(tabHost);

      selectAll.addEventListener('change', function () {
        selected.clear();
        if (selectAll.checked) {
          sources.items.forEach(function (item) { selected.add(item.path); });
        }
        draw();
      });
      rescan.addEventListener('click', function () {
        selected.clear();
        renderVideo(host);
      });

      function updateButtons() {
        selectedLabel.textContent = '已选 ' + selected.size + ' 个';
        selectAll.checked = sources.items.length > 0
          && selected.size === sources.items.length;
        selectAll.indeterminate = selected.size > 0
          && selected.size < sources.items.length;
        actionRefreshers.forEach(function (refresh) { refresh(); });
      }

      function draw() {
        var rows = sortRows(sources.items, columns, state.video);
        var head = h('tr', null, h('th', { style: 'width:36px' }, ''));
        columns.forEach(function (column) {
          var mark = state.video.order === column.key
            ? (state.video.asc ? ' ↑' : ' ↓') : '';
          head.appendChild(h('th', {
            class: 'sortable',
            onclick: function () {
              cycleSort(state.video, column.key);
              draw();
            }
          }, column.label + mark));
        });
        var body = h('tbody');
        rows.forEach(function (item) {
          var check = h('input', {
            type: 'checkbox', checked: selected.has(item.path)
          });
          check.addEventListener('change', function () {
            if (check.checked) selected.add(item.path);
            else selected.delete(item.path);
            updateButtons();
          });
          body.appendChild(h('tr', null,
            h('td', null, check),
            h('td', null, item.name || '-'),
            h('td', { class: 'muted small' }, item.dir || '-'),
            h('td', { class: 'num' }, fmtBytes(item.size)),
            h('td', null, item.mtime || '-')));
        });
        listHost.replaceChildren(h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' }, h('thead', null, head), body)));
        updateButtons();
      }

      function selectedPaths() {
        return sortRows(sources.items, columns, state.video)
          .filter(function (item) { return selected.has(item.path); })
          .map(function (item) { return item.path; });
      }

      function makeSelect(items, selectedValue) {
        return h('select', { class: 'select' }, items.map(function (item) {
          return h('option', {
            value: item.value,
            selected: item.value === selectedValue
          }, item.label);
        }));
      }

      function runOp(url, body, label) {
        if (running) return;
        running = true;
        updateButtons();
        post(url, body).then(function (data) {
          watchJob(jobHost, data.job, function (job) {
            running = false;
            updateButtons();
            var summary = job.result && job.result.summary
              ? job.result.summary : '已完成';
            toast(summary, 'success');
            showOpResult(job);
            draw();
          }, {
            cancelable: true,
            onSettled: function (job) {
              running = false;
              updateButtons();
              if (job.status === 'cancelled') {
                toast(label + '已取消', 'info');
              }
            }
          });
        }).catch(function (error) {
          running = false;
          updateButtons();
          toast(label + '失败：' + error.message, 'error');
        });
      }

      function renderConvertPanel() {
        var btn = h('button', { class: 'btn primary', type: 'button' }, '转换选中视频');
        tabHost.appendChild(h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' },
            '按桌面默认输出格式做无损优先的格式转换；输出到源视频同目录，目标格式相同时跳过。'),
          h('span', { style: 'flex:1' }), btn));
        var sync = function () {
          btn.disabled = running || selected.size === 0;
          btn.textContent = running ? '处理中…' : '转换选中视频';
        };
        actionRefreshers.push(sync);
        sync();
        btn.addEventListener('click', function () {
          var paths = selectedPaths();
          if (!paths.length) return;
          runOp('/api/tools/video/convert', { paths: paths }, '转换');
        });
      }

      function renderCompressPanel() {
        var preset = makeSelect(overview.compress_presets || [], 'normal');
        var resolution = makeSelect(overview.resolutions || [], 'keep');
        var clean = h('input', { type: 'checkbox', checked: true });
        var btn = h('button', { class: 'btn primary', type: 'button' }, '压缩选中视频');
        tabHost.appendChild(h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '压缩强度'), preset,
          h('span', { class: 'muted small' }, '分辨率'), resolution,
          h('label', { class: 'check small' }, clean, ' 清除元数据'),
          h('span', { style: 'flex:1' }), btn));
        tabHost.appendChild(h('div', { class: 'muted small' },
          '重新编码输出到源视频同目录（<文件名>_compressed_video），源文件不动，同名自动加序号；视频较大时耗时较长。'));
        var sync = function () {
          btn.disabled = running || selected.size === 0;
          btn.textContent = running ? '处理中…' : '压缩选中视频';
        };
        actionRefreshers.push(sync);
        sync();
        btn.addEventListener('click', function () {
          var paths = selectedPaths();
          if (!paths.length) return;
          runOp('/api/tools/video/compress', {
            paths: paths,
            preset: preset.value,
            resolution: resolution.value,
            clear_metadata: clean.checked
          }, '压缩');
        });
      }

      function renderMergePanel() {
        var mode = makeSelect(overview.merge_modes || [], 'copy');
        var ext = makeSelect((overview.merge_exts || ['.mp4']).map(function (value) {
          return { value: value, label: value };
        }), '.mp4');
        var name = h('input', {
          class: 'input grow', placeholder: '留空自动命名 合并_日期_时间'
        });
        var btn = h('button', { class: 'btn primary', type: 'button' }, '合并选中视频');
        tabHost.appendChild(h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '方式'), mode,
          h('span', { class: 'muted small' }, '格式'), ext,
          h('span', { class: 'muted small' }, '文件名'), name,
          h('span', { style: 'flex:1' }), btn));
        tabHost.appendChild(h('div', { class: 'muted small' },
          '勾选 2 个以上视频，按上方表格当前排序顺序合并（可点表头调整顺序）；输出到工作目录根，不覆盖已有文件。'));
        var sync = function () {
          btn.disabled = running || selected.size < 2;
          btn.textContent = running ? '处理中…' : '合并选中视频';
        };
        actionRefreshers.push(sync);
        sync();
        btn.addEventListener('click', function () {
          var paths = selectedPaths();
          if (paths.length < 2) {
            toast('至少选择 2 个视频才能合并', 'warning');
            return;
          }
          runOp('/api/tools/video/merge', {
            paths: paths,
            mode: mode.value,
            output_name: name.value.trim(),
            output_ext: ext.value
          }, '合并');
        });
      }

      function parseTimeMs(value) {
        var parts = String(value || '').trim().split(':');
        var seconds = null;
        if (parts.length === 1) {
          seconds = Number(parts[0]);
        } else if (parts.length === 2) {
          seconds = Number(parts[0]) * 60 + Number(parts[1]);
        } else if (parts.length === 3) {
          seconds = Number(parts[0]) * 3600 + Number(parts[1]) * 60 + Number(parts[2]);
        }
        if (seconds == null || !isFinite(seconds) || seconds < 0) return null;
        return Math.round(seconds * 1000);
      }

      function parseSegments(text) {
        var rows = [];
        String(text || '').split(/\n+/).forEach(function (line) {
          var trimmed = line.trim();
          if (!trimmed) return;
          var parts = trimmed.split('-');
          if (parts.length !== 2) {
            throw new Error('片段格式不正确：' + trimmed);
          }
          var start = parseTimeMs(parts[0]);
          var end = parseTimeMs(parts[1]);
          if (start == null || end == null) {
            throw new Error('时间格式不正确：' + trimmed);
          }
          if (end <= start) {
            throw new Error('结束时间必须大于开始时间：' + trimmed);
          }
          rows.push([start, end]);
        });
        if (!rows.length) throw new Error('请至少填写一个片段');
        return rows;
      }

      function renderCutPanel() {
        var exportSel = makeSelect(overview.cut_exports || [], 'each');
        var modeSel = makeSelect(overview.cut_modes || [], 'copy');
        var nameInput = h('input', {
          class: 'input grow', placeholder: '合并文件名，留空自动命名'
        });
        var nameRow = h('div', { class: 'toolbar', style: 'display:none' },
          h('span', { class: 'muted small' }, '文件名'), nameInput);
        var segments = h('textarea', {
          class: 'textarea', rows: '4',
          placeholder: '每行一个片段：开始-结束，例如\n00:10-01:20\n01:45-02:30'
        });
        var btn = h('button', { class: 'btn primary', type: 'button' }, '切割选中视频');
        var chapterBtn = h('button', { class: 'btn', type: 'button' }, '写入章节');
        var detectKind = makeSelect([
          { value: 'silence', label: '静音' },
          { value: 'black', label: '黑帧' },
          { value: 'scene', label: '场景切换' }
        ], 'silence');
        var detThreshold = h('input', {
          class: 'input', style: 'width:92px', value: '-30'
        });
        var detUnit = h('span', { class: 'muted small' }, 'dB');
        var detMin = h('input', {
          class: 'input', style: 'width:72px', value: '0.5'
        });
        var detBtn = h('button', { class: 'btn', type: 'button' }, '检测');
        var fillBtn = h('button', {
          class: 'btn', type: 'button', disabled: true
        }, '填入片段');
        var clearBtn = h('button', {
          class: 'btn ghost', type: 'button', disabled: true
        }, '清空切点');
        var detInfo = h('div', { class: 'muted small', style: 'margin:6px 0' },
          '选择 1 个视频后检测；结果可一键填入下方片段。');
        var detectPoints = [];
        var detectDuration = 0;
        var detectPath = '';
        tabHost.appendChild(h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '导出'), exportSel,
          h('span', { class: 'muted small' }, '方式'), modeSel,
          h('span', { style: 'flex:1' }), chapterBtn, btn));
        tabHost.appendChild(h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '自动检测'), detectKind,
          h('span', { class: 'muted small' }, '阈值'),
          detThreshold, detUnit,
          h('span', { class: 'muted small' }, '最短'), detMin,
          h('span', { class: 'muted small' }, '秒'), detBtn,
          fillBtn, clearBtn));
        tabHost.appendChild(detInfo);
        tabHost.appendChild(segments);
        tabHost.appendChild(nameRow);
        tabHost.appendChild(h('div', { class: 'muted small', style: 'margin-top:8px' },
          '先在表格勾选 1 个视频；时间可用秒（90）或时间码（01:30 / 00:01:30）。输出到工作目录根，不覆盖已有文件。'
          + '检测只读视频不生成文件：静音阈值用 dB（-60 到 0），黑帧与场景用 0.01 到 1；'
          + '「写入章节」会把上方片段写成章节并封装为新视频（不转码）。'));
        var detectDefaults = {
          silence: { value: '-30', unit: 'dB' },
          black: { value: '0.10', unit: '' },
          scene: { value: '0.40', unit: '' }
        };
        detectKind.addEventListener('change', function () {
          var preset = detectDefaults[detectKind.value];
          detThreshold.value = preset.value;
          detUnit.textContent = preset.unit;
          detMin.disabled = detectKind.value === 'scene';
        });
        detMin.disabled = detectKind.value === 'scene';

        function msClock(ms) {
          var total = Math.max(0, Math.round(ms / 100) / 10);
          var hours = Math.floor(total / 3600);
          var minutes = Math.floor((total % 3600) / 60);
          var seconds = (total % 60).toFixed(1);
          return (hours < 10 ? '0' : '') + hours + ':'
            + (minutes < 10 ? '0' : '') + minutes + ':'
            + (Number(seconds) < 10 ? '0' : '') + seconds;
        }

        function resetDetect() {
          detectPoints = [];
          detectDuration = 0;
          detectPath = '';
          fillBtn.disabled = true;
          clearBtn.disabled = true;
        }
        exportSel.addEventListener('change', function () {
          nameRow.style.display = exportSel.value === 'each' ? 'none' : '';
        });
        var sync = function () {
          var disabled = running || selected.size !== 1;
          btn.disabled = disabled;
          chapterBtn.disabled = disabled;
          detBtn.disabled = disabled;
          fillBtn.disabled = running || !detectPoints.length || !detectDuration;
          clearBtn.disabled = running || !detectPoints.length;
          btn.textContent = running ? '处理中…' : '切割选中视频';
          chapterBtn.textContent = running ? '处理中…' : '写入章节';
          detBtn.textContent = running ? '处理中…' : '检测';
        };
        actionRefreshers.push(sync);
        sync();
        detBtn.addEventListener('click', function () {
          var paths = selectedPaths();
          if (paths.length !== 1) {
            toast('请先在表格勾选 1 个视频', 'warning');
            return;
          }
          var threshold = Number(detThreshold.value);
          if (!isFinite(threshold)) {
            toast('请填写检测阈值', 'warning');
            return;
          }
          if (detectKind.value === 'silence') {
            if (threshold < -60 || threshold > 0) {
              toast('静音阈值需在 -60 到 0 dB 之间', 'warning');
              return;
            }
          } else if (threshold < 0.01 || threshold > 1) {
            toast('阈值需在 0.01 到 1 之间', 'warning');
            return;
          }
          var minSec = detectKind.value === 'scene' ? 0.5 : Number(detMin.value);
          if (detectKind.value !== 'scene'
              && (!isFinite(minSec) || minSec < 0.1 || minSec > 10)) {
            toast('最短时长需在 0.1 到 10 秒之间', 'warning');
            return;
          }
          running = true;
          updateButtons();
          post('/api/tools/video/detect', {
            path: paths[0], kind: detectKind.value,
            threshold: threshold, min_sec: minSec
          }).then(function (data) {
            watchJob(jobHost, data.job, function (job) {
              running = false;
              updateButtons();
              var result = job.result || {};
              detectPoints = (result.points || []).slice();
              detectDuration = Number(result.duration_ms || 0);
              detectPath = String(result.path || '');
              var found = result.summary || '检测完成';
              detInfo.textContent = found
                + (detectDuration ? ' · 时长 ' + msClock(detectDuration) : '')
                + (detectPoints.length ? '，可点「填入片段」' : '，没有可用切点');
              toast(found, 'success');
            }, {
              cancelable: true,
              onSettled: function (job) {
                running = false;
                updateButtons();
                if (job.status === 'cancelled') {
                  detInfo.textContent = '检测已取消。';
                  toast('检测已取消', 'info');
                }
              }
            });
          }).catch(function (error) {
            running = false;
            updateButtons();
            toast('检测失败：' + error.message, 'error');
          });
        });
        fillBtn.addEventListener('click', function () {
          if (!detectPoints.length || !detectDuration) {
            toast('请先执行检测', 'warning');
            return;
          }
          var paths = selectedPaths();
          if (paths.length !== 1 || (detectPath && paths[0] !== detectPath)) {
            toast('当前选中的视频已变化，请重新检测', 'warning');
            return;
          }
          var bounds = [0];
          detectPoints.forEach(function (point) {
            if (point > 0 && point < detectDuration) bounds.push(point);
          });
          bounds.push(detectDuration);
          var rows = [];
          for (var index = 1; index < bounds.length; index += 1) {
            if (bounds[index] - bounds[index - 1] >= 400) {
              rows.push(msClock(bounds[index - 1]) + '-'
                + msClock(bounds[index]));
            }
          }
          if (!rows.length) {
            toast('切点间隔太短，未生成片段', 'warning');
            return;
          }
          segments.value = rows.join('\n');
          toast('已按切点填入 ' + rows.length + ' 个片段', 'success');
        });
        clearBtn.addEventListener('click', function () {
          resetDetect();
          detInfo.textContent = '已清空切点。';
        });
        chapterBtn.addEventListener('click', function () {
          var paths = selectedPaths();
          if (paths.length !== 1) {
            toast('请先在表格勾选 1 个视频', 'warning');
            return;
          }
          var parsed;
          try {
            parsed = parseSegments(segments.value);
          } catch (error) {
            toast(error.message, 'warning');
            return;
          }
          runOp('/api/tools/video/chapters/write', {
            path: paths[0], segments: parsed
          }, '写入章节');
        });
        btn.addEventListener('click', function () {
          var paths = selectedPaths();
          if (paths.length !== 1) {
            toast('请先在表格勾选 1 个视频', 'warning');
            return;
          }
          var parsed;
          try {
            parsed = parseSegments(segments.value);
          } catch (error) {
            toast(error.message, 'warning');
            return;
          }
          runOp('/api/tools/video/cut', {
            path: paths[0],
            segments: parsed,
            mode: modeSel.value,
            export: exportSel.value,
            merged_name: nameInput.value.trim()
          }, '切割');
        });
      }

      function renderSnapshotPanel() {
        var fmt = makeSelect(overview.snapshot_formats || [], 'jpg');
        var position = h('input', {
          class: 'input', placeholder: '时间点：90 或 01:30 或 00:01:30'
        });
        var singleBtn = h('button', { class: 'btn primary', type: 'button' }, '截取单帧');
        var interval = h('input', {
          class: 'input', placeholder: '间隔秒数，如 10'
        });
        var everyBtn = h('button', { class: 'btn', type: 'button' }, '按间隔抽帧');
        tabHost.appendChild(h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '格式'), fmt,
          position, singleBtn));
        tabHost.appendChild(h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '抽帧间隔'), interval, everyBtn));
        tabHost.appendChild(h('div', { class: 'muted small', style: 'margin-top:8px' },
          '先在表格勾选 1 个视频；单帧输出到视频同目录，按间隔抽帧输出到「<文件名>_frames」文件夹（JPG）。'));
        var sync = function () {
          var disabled = running || selected.size !== 1;
          singleBtn.disabled = disabled;
          everyBtn.disabled = disabled;
          singleBtn.textContent = running ? '处理中…' : '截取单帧';
          everyBtn.textContent = running ? '处理中…' : '按间隔抽帧';
        };
        actionRefreshers.push(sync);
        sync();
        singleBtn.addEventListener('click', function () {
          var paths = selectedPaths();
          if (paths.length !== 1) {
            toast('请先在表格勾选 1 个视频', 'warning');
            return;
          }
          if (!position.value.trim()) {
            toast('请填写截图时间点', 'warning');
            return;
          }
          runOp('/api/tools/video/snapshot', {
            path: paths[0], mode: 'single',
            position: position.value.trim(), format: fmt.value
          }, '截图');
        });
        everyBtn.addEventListener('click', function () {
          var paths = selectedPaths();
          if (paths.length !== 1) {
            toast('请先在表格勾选 1 个视频', 'warning');
            return;
          }
          var seconds = Number(interval.value);
          if (!isFinite(seconds) || seconds < 0.1) {
            toast('抽帧间隔至少 0.1 秒', 'warning');
            return;
          }
          runOp('/api/tools/video/snapshot', {
            path: paths[0], mode: 'interval', interval_sec: seconds,
            format: 'jpg'
          }, '抽帧');
        });
      }

      function renderTracksPanel() {
        var infoHost = h('div');
        var tableHost = h('div');
        var trackBoxes = [];
        var reloadBtn = h('button', { class: 'btn', type: 'button' }, '刷新轨道');
        var extractBtn = h('button', { class: 'btn primary', type: 'button' }, '提取全部轨道');
        var removeBtn = h('button', { class: 'btn danger', type: 'button' }, '删除选中轨道');
        var audioSel = h('select', { class: 'select grow' });
        var subSel = h('select', { class: 'select grow' });
        var audioAppendBtn = h('button', { class: 'btn', type: 'button' }, '追加音轨');
        var audioReplaceBtn = h('button', { class: 'btn', type: 'button' }, '替换音轨');
        var subtitleBtn = h('button', { class: 'btn', type: 'button' }, '添加字幕');
        tabHost.appendChild(h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '先在表格勾选 1 个视频；提取与删除都只生成新文件。'),
          h('span', { style: 'flex:1' }), reloadBtn, extractBtn));
        tabHost.appendChild(infoHost);
        tabHost.appendChild(tableHost);
        tabHost.appendChild(h('div', { class: 'toolbar', style: 'margin-top:8px' },
          h('span', { style: 'flex:1' }), removeBtn));
        tabHost.appendChild(h('div', { class: 'card', style: 'margin-top:12px' },
          h('div', { class: 'row-title', style: 'margin-bottom:8px' }, '外部音轨 / 字幕'),
          h('div', { class: 'toolbar' },
            h('span', { class: 'muted small' }, '音轨文件'), audioSel,
            audioAppendBtn, audioReplaceBtn),
          h('div', { class: 'toolbar' },
            h('span', { class: 'muted small' }, '字幕文件'), subSel, subtitleBtn),
          h('div', { class: 'muted small' },
            '只列出工作目录内的文件；输出到工作目录根，源视频不变；'
            + '外部音轨编码不适合 mp4 时会自动改用 mkv。')));

        function currentPath() {
          var paths = selectedPaths();
          return paths.length === 1 ? paths[0] : '';
        }

        function sync() {
          var one = !!currentPath();
          reloadBtn.disabled = running || !one;
          extractBtn.disabled = running || !one;
          removeBtn.disabled = running || !one
            || !trackBoxes.some(function (entry) { return entry.box.checked; });
          audioAppendBtn.disabled = running || !one || !audioSel.value;
          audioReplaceBtn.disabled = running || !one || !audioSel.value;
          subtitleBtn.disabled = running || !one || !subSel.value;
          extractBtn.textContent = running ? '处理中…' : '提取全部轨道';
          removeBtn.textContent = running ? '处理中…' : '删除选中轨道';
        }
        actionRefreshers.push(sync);

        function fillSelect(select, items, emptyText) {
          select.replaceChildren();
          if (!items.length) {
            select.appendChild(h('option', { value: '' }, emptyText));
            select.disabled = true;
            return;
          }
          select.disabled = false;
          items.forEach(function (item) {
            select.appendChild(h('option', {
              value: item.path, title: item.name
            }, item.name));
          });
        }

        function loadExtras() {
          Promise.all([
            api('/api/tools/video/extras?kind=audio'),
            api('/api/tools/video/extras?kind=subtitle')
          ]).then(function (results) {
            fillSelect(audioSel, results[0].items || [],
              '（工作目录内无音频文件）');
            fillSelect(subSel, results[1].items || [],
              '（工作目录内无字幕文件）');
            sync();
          }).catch(function () { sync(); });
        }

        function loadTracks() {
          var path = currentPath();
          trackBoxes = [];
          if (!path) {
            infoHost.replaceChildren(
              h('div', { class: 'muted small' }, '先在表格勾选 1 个视频。'));
            tableHost.replaceChildren();
            sync();
            return;
          }
          setLoading(infoHost, '正在读取轨道…');
          tableHost.replaceChildren();
          api('/api/tools/video/tracks?path=' + encodeURIComponent(path))
            .then(function (data) {
              infoHost.replaceChildren(
                h('div', { class: 'muted small' }, data.note || ''));
              var tracks = data.tracks || [];
              if (!tracks.length) {
                tableHost.replaceChildren(
                  empty('该视频没有可提取的音轨或字幕轨。'));
                sync();
                return;
              }
              var head = h('tr', null,
                h('th', { style: 'width:36px' }, ''),
                h('th', null, '序号'), h('th', null, '类型'),
                h('th', null, '编码'), h('th', null, '语言'),
                h('th', null, '标题'));
              var body = h('tbody');
              tracks.forEach(function (track) {
                var box = h('input', { type: 'checkbox' });
                box.addEventListener('change', sync);
                trackBoxes.push({ box: box, index: track.index });
                body.appendChild(h('tr', null,
                  h('td', null, box),
                  h('td', { class: 'num' }, track.index),
                  h('td', null, track.kind === 'audio' ? '音轨' : '字幕'),
                  h('td', null, track.codec || '-'),
                  h('td', null, track.language || '-'),
                  h('td', { class: 'ellipsis' }, track.title || '-')));
              });
              tableHost.replaceChildren(h('div', { class: 'table-wrap' },
                h('table', { class: 'db-table' },
                  h('thead', null, head), body)));
              sync();
            }).catch(function (error) {
              setError(infoHost, error, loadTracks);
            });
        }

        reloadBtn.addEventListener('click', function () {
          loadExtras();
          loadTracks();
        });
        extractBtn.addEventListener('click', function () {
          var path = currentPath();
          if (!path) return;
          runOp('/api/tools/video/tracks/extract', { path: path }, '提取轨道');
        });
        removeBtn.addEventListener('click', function () {
          var path = currentPath();
          if (!path) return;
          var indices = trackBoxes.filter(function (entry) {
            return entry.box.checked;
          }).map(function (entry) { return entry.index; });
          if (!indices.length) {
            toast('请先勾选要删除的轨道', 'warning');
            return;
          }
          runOp('/api/tools/video/tracks/remove',
            { path: path, indices: indices }, '删除轨道');
        });
        audioAppendBtn.addEventListener('click', function () {
          var path = currentPath();
          if (!path || !audioSel.value) return;
          runOp('/api/tools/video/audio/add', {
            path: path, audio: audioSel.value, replace: false
          }, '追加音轨');
        });
        audioReplaceBtn.addEventListener('click', function () {
          var path = currentPath();
          if (!path || !audioSel.value) return;
          runOp('/api/tools/video/audio/add', {
            path: path, audio: audioSel.value, replace: true
          }, '替换音轨');
        });
        subtitleBtn.addEventListener('click', function () {
          var path = currentPath();
          if (!path || !subSel.value) return;
          runOp('/api/tools/video/subtitle/add', {
            path: path, subtitle: subSel.value
          }, '添加字幕');
        });

        loadTracks();
        loadExtras();
        sync();
      }

      function renderMetadataPanel() {
        var infoHost = h('div');
        var inputs = {};
        var rotationSel = h('select', { class: 'select' },
          h('option', { value: '0' }, '0°'),
          h('option', { value: '90' }, '90°'),
          h('option', { value: '180' }, '180°'),
          h('option', { value: '270' }, '270°'));
        var reloadBtn = h('button', { class: 'btn', type: 'button' }, '重新读取');
        var writeBtn = h('button', { class: 'btn primary', type: 'button' }, '写入元数据');
        tabHost.appendChild(h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '先在表格勾选 1 个视频；写入生成 <文件名>_meta 新文件，源视频保留。'),
          h('span', { style: 'flex:1' }), reloadBtn, writeBtn));
        var form = h('div', { style: 'display:grid; gap:10px; max-width:560px' });
        [['title', '标题'], ['artist', '作者'], ['comment', '备注'],
         ['date', '日期']].forEach(function (pair) {
          inputs[pair[0]] = h('input', { class: 'input', style: 'width:100%' });
          form.appendChild(h('div', null,
            h('div', { class: 'muted small', style: 'margin-bottom:4px' }, pair[1]),
            inputs[pair[0]]));
        });
        form.appendChild(h('div', null,
          h('div', { class: 'muted small', style: 'margin-bottom:4px' }, '旋转'),
          rotationSel));
        tabHost.appendChild(infoHost);
        tabHost.appendChild(form);

        function currentPath() {
          var paths = selectedPaths();
          return paths.length === 1 ? paths[0] : '';
        }

        function sync() {
          var one = !!currentPath();
          reloadBtn.disabled = running || !one;
          writeBtn.disabled = running || !one;
          writeBtn.textContent = running ? '处理中…' : '写入元数据';
        }
        actionRefreshers.push(sync);

        function loadMeta() {
          var path = currentPath();
          if (!path) {
            infoHost.replaceChildren(
              h('div', { class: 'muted small' }, '先在表格勾选 1 个视频。'));
            sync();
            return;
          }
          setLoading(infoHost, '正在读取元数据…');
          api('/api/tools/video/metadata?path=' + encodeURIComponent(path))
            .then(function (data) {
              var tags = data.tags || {};
              Object.keys(inputs).forEach(function (key) {
                inputs[key].value = tags[key] || '';
              });
              rotationSel.value = String(data.rotation || 0);
              infoHost.replaceChildren(
                h('div', { class: 'muted small' }, data.note || ''));
              sync();
            }).catch(function (error) {
              setError(infoHost, error, loadMeta);
            });
        }

        reloadBtn.addEventListener('click', loadMeta);
        writeBtn.addEventListener('click', function () {
          var path = currentPath();
          if (!path) return;
          var tags = {};
          Object.keys(inputs).forEach(function (key) {
            tags[key] = inputs[key].value.trim();
          });
          runOp('/api/tools/video/metadata/write', {
            path: path, tags: tags, rotation: Number(rotationSel.value)
          }, '写入元数据');
        });

        loadMeta();
        sync();
      }

      function renderTab() {
        tabHost.replaceChildren();
        actionRefreshers = [];
        if (state.video.tab === 'compress') renderCompressPanel();
        else if (state.video.tab === 'merge') renderMergePanel();
        else if (state.video.tab === 'cut') renderCutPanel();
        else if (state.video.tab === 'snapshot') renderSnapshotPanel();
        else if (state.video.tab === 'tracks') renderTracksPanel();
        else if (state.video.tab === 'metadata') renderMetadataPanel();
        else renderConvertPanel();
        updateButtons();
      }

      function showOpResult(job) {
        var result = job.result || {};
        var rows = result.results || [];
        resultHost.replaceChildren();
        if (rows.length) {
          var labels = {
            lossless: '无损封装', skip: '已存在跳过',
            encode: '重新编码', encode_fallback: '重编码'
          };
          var head = h('tr', null,
            h('th', null, '文件'), h('th', null, '结果'),
            h('th', null, '输出'), h('th', null, '说明'));
          var body = h('tbody');
          rows.forEach(function (row) {
            var note = row.error || '';
            if (!note && row.before && row.after) {
              note = fmtBytes(row.before) + ' → ' + fmtBytes(row.after);
            }
            if (!note) note = labels[row.mode] || row.mode || '-';
            body.appendChild(h('tr', null,
              h('td', null, row.file || '-'),
              h('td', null, row.ok ? '成功' : '失败'),
              h('td', { class: 'muted small' }, row.output || '-'),
              h('td', { class: 'muted small' }, note)));
          });
          resultHost.appendChild(h('div', {
            class: 'card', style: 'margin-top:12px'
          },
            h('div', { class: 'row-title', style: 'margin-bottom:8px' },
              result.summary || '处理结果'),
            h('div', { class: 'table-wrap' },
              h('table', { class: 'db-table' }, h('thead', null, head), body))));
          return;
        }
        var lines = [];
        if (result.output) lines.push('输出：' + result.output);
        if (result.folder) lines.push('输出文件夹：' + result.folder);
        if (result.outputs && result.outputs.length) {
          lines.push('输出：' + result.outputs.slice(0, 30).join('、') +
            (result.outputs.length > 30
              ? ' 等 ' + result.outputs.length + ' 个文件' : ''));
        }
        if (result.problems && result.problems.length) {
          lines.push('参数差异提示：' + result.problems.slice(0, 5).join('；'));
        }
        if (!lines.length) return;
        resultHost.appendChild(h('div', {
          class: 'card', style: 'margin-top:12px'
        },
          h('div', { class: 'row-title', style: 'margin-bottom:8px' },
            result.summary || '处理结果'),
          h('div', { class: 'muted small' }, lines.join('；'))));
      }

      draw();
      renderTab();
    }).catch(function (error) {
      setError(host, error, function () { renderVideo(host); });
    });
  }

  function renderSubtitle(host) {
    host.replaceChildren();
    setLoading(host, '正在读取字幕文件…');
    Promise.all([
      api('/api/tools/subtitle/overview'),
      api('/api/tools/subtitle/sources?limit=300')
    ]).then(function (results) {
      var overview = results[0];
      var sources = results[1];
      host.replaceChildren();
      if (!overview.has_work_dir) {
        host.appendChild(empty('尚未设置工作目录。请先在桌面端「全局设置」中配置工作目录。'));
        return;
      }
      var stats = h('div', { class: 'grid cols-4', style: 'margin-bottom:12px' });
      stats.appendChild(statCard('字幕文件', fmtNumber(sources.total), '工作目录内'));
      stats.appendChild(statCard('输出位置', overview.output || '-', '与桌面端一致'));
      stats.appendChild(statCard('已列出', fmtNumber(sources.items.length),
        sources.truncated ? '仅前 300 个' : '全部'));
      stats.appendChild(statCard('转换', '只生成新文件', '同名输出会跳过'));
      host.appendChild(stats);
      host.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'muted small' }, overview.note || '')));
      if (!sources.items.length) {
        host.appendChild(empty('工作目录下没有支持的字幕文件。'));
        return;
      }

      var selected = state.subtitle.selected;
      var chosen = state.subtitle.formats;
      var available = (overview.formats || []).map(function (item) { return item.key; });
      Array.from(chosen).forEach(function (key) {
        if (available.indexOf(key) < 0) chosen.delete(key);
      });
      if (!chosen.size) {
        (overview.default_formats || []).forEach(function (key) { chosen.add(key); });
      }
      if (!chosen.size) available.forEach(function (key) { chosen.add(key); });
      var known = new Set(sources.items.map(function (item) { return item.path; }));
      Array.from(selected).forEach(function (path) {
        if (!known.has(path)) selected.delete(path);
      });

      var formatCard = h('div', { class: 'card', style: 'margin-bottom:12px' });
      formatCard.appendChild(h('div', { class: 'row-title', style: 'margin-bottom:8px' }, '输出格式'));
      (overview.formats || []).forEach(function (item) {
        var box = h('input', { type: 'checkbox', checked: chosen.has(item.key) });
        box.addEventListener('change', function () {
          if (box.checked) chosen.add(item.key);
          else chosen.delete(item.key);
          updateButtons();
        });
        formatCard.appendChild(h('label', {
          class: 'muted small', style: 'margin-right:14px'
        }, box, ' ' + item.label));
      });
      host.appendChild(formatCard);

      var selectedLabel = h('span', { class: 'muted small' });
      var selectAll = h('input', { type: 'checkbox' });
      var convert = h('button', { class: 'btn primary', type: 'button' }, '转换选中字幕');
      var rescan = h('button', { class: 'btn', type: 'button' }, '刷新');
      host.appendChild(h('div', { class: 'toolbar' },
        h('label', { class: 'muted small' }, selectAll, ' 全选'),
        selectedLabel, h('span', { style: 'flex:1' }), rescan, convert));

      var jobHost = h('div');
      var listHost = h('div');
      var resultHost = h('div');
      host.appendChild(jobHost);
      host.appendChild(listHost);
      host.appendChild(resultHost);

      var running = false;
      var columns = [
        { key: 'name', label: '文件名' },
        { key: 'dir', label: '目录' },
        { key: 'size', label: '大小' },
        { key: 'mtime', label: '修改时间' }
      ];

      function updateButtons() {
        convert.disabled = running || selected.size === 0 || chosen.size === 0;
        convert.textContent = running ? '转换中…' : '转换选中字幕';
        selectedLabel.textContent = '已选 ' + selected.size + ' 个 · '
          + chosen.size + ' 种格式';
        selectAll.checked = sources.items.length > 0
          && selected.size === sources.items.length;
        selectAll.indeterminate = selected.size > 0
          && selected.size < sources.items.length;
      }

      function draw() {
        var rows = sortRows(sources.items, columns, state.subtitle);
        var head = h('tr', null, h('th', { style: 'width:36px' }, ''));
        columns.forEach(function (column) {
          var mark = state.subtitle.order === column.key
            ? (state.subtitle.asc ? ' ↑' : ' ↓') : '';
          head.appendChild(h('th', {
            class: 'sortable',
            onclick: function () {
              cycleSort(state.subtitle, column.key);
              draw();
            }
          }, column.label + mark));
        });
        var body = h('tbody');
        rows.forEach(function (item) {
          var check = h('input', {
            type: 'checkbox', checked: selected.has(item.path)
          });
          check.addEventListener('change', function () {
            if (check.checked) selected.add(item.path);
            else selected.delete(item.path);
            updateButtons();
          });
          body.appendChild(h('tr', null,
            h('td', null, check),
            h('td', null, item.name || '-'),
            h('td', { class: 'muted small' }, item.dir || '-'),
            h('td', { class: 'num' }, fmtBytes(item.size)),
            h('td', null, item.mtime || '-')));
        });
        listHost.replaceChildren(h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' }, h('thead', null, head), body)));
        updateButtons();
      }

      selectAll.addEventListener('change', function () {
        selected.clear();
        if (selectAll.checked) {
          sources.items.forEach(function (item) { selected.add(item.path); });
        }
        draw();
      });
      rescan.addEventListener('click', function () {
        selected.clear();
        renderSubtitle(host);
      });
      convert.addEventListener('click', function () {
        if (!selected.size || !chosen.size || running) return;
        var paths = Array.from(selected);
        var formats = Array.from(chosen);
        running = true;
        updateButtons();
        post('/api/tools/subtitle/convert', {
          paths: paths, formats: formats
        }).then(function (data) {
          watchJob(jobHost, data.job, function (job) {
            running = false;
            updateButtons();
            var summary = job.result && job.result.summary
              ? job.result.summary : '转换完成';
            toast(summary, 'success');
            showResult(job);
            selected.clear();
            draw();
          }, {
            cancelable: true,
            onSettled: function (job) {
              running = false;
              updateButtons();
              if (job.status === 'cancelled') {
                toast('字幕转换已取消', 'info');
              }
            }
          });
        }).catch(function (error) {
          running = false;
          updateButtons();
          toast('转换失败：' + error.message, 'error');
        });
      });
      draw();
    }).catch(function (error) {
      setError(host, error, function () { renderSubtitle(host); });
    });

    function showResult(job) {
      var result = job.result || {};
      var rows = result.results || [];
      if (!rows.length) return;
      var head = h('tr', null,
        h('th', null, '文件'), h('th', null, '结果'),
        h('th', null, '输出 / 跳过 / 错误'));
      var body = h('tbody');
      rows.forEach(function (row) {
        var extra = [];
        if (row.outputs && row.outputs.length) {
          extra.push('输出：' + row.outputs.join('、'));
        }
        if (row.skipped && row.skipped.length) {
          extra.push('跳过同名：' + row.skipped.join('、'));
        }
        if (row.error) extra.push(row.error);
        body.appendChild(h('tr', null,
          h('td', null, row.file || '-'),
          h('td', null, row.ok ? '成功' : '失败'),
          h('td', { class: 'muted small' }, extra.join('；') || '-')));
      });
      resultHost.replaceChildren(h('div', {
        class: 'card', style: 'margin-top:12px'
      },
        h('div', { class: 'row-title', style: 'margin-bottom:8px' },
          result.summary || '转换结果'),
        h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' }, h('thead', null, head), body))));
    }
  }

  function renderCalendar(host) {
    host.replaceChildren();
    setLoading(host, '正在扫描日期文件夹…');
    api('/api/tools/calendar/days').then(function (data) {
      host.replaceChildren();
      if (!data.total) {
        host.appendChild(empty('工作目录下没有日期文件夹。'));
        return;
      }
      var stats = h('div', { class: 'grid cols-3', style: 'margin-bottom:12px' });
      stats.appendChild(statCard('日期', fmtNumber(data.total), '工作目录内'));
      var files = (data.days || []).reduce(function (sum, day) {
        return sum + day.files;
      }, 0);
      stats.appendChild(statCard('文件', fmtNumber(files), '全部日期合计'));
      var size = (data.days || []).reduce(function (sum, day) {
        return sum + day.size;
      }, 0);
      stats.appendChild(statCard('总大小', fmtBytes(size), '全部日期合计'));
      host.appendChild(stats);
      host.appendChild(h('div', { class: 'muted small', style: 'margin:0 2px 8px' },
        data.note || ''));

      var daysHost = h('div');
      var filesHost = h('div');
      host.appendChild(daysHost);
      host.appendChild(filesHost);

      function drawDays() {
        var head = h('tr',
          null,
          h('th', { class: 'sortable', onclick: function () { cycleSort(state.calendar, 'date'); drawDays(); } },
            '日期' + (state.calendar.order === 'date' ? (state.calendar.asc ? ' ↑' : ' ↓') : '')),
          h('th', { class: 'num' }, '文件'),
          h('th', { class: 'num' }, '大小'));
        var rows = (data.days || []).slice();
        if (state.calendar.order === 'date' && !state.calendar.asc) rows.reverse();
        var body = h('tbody');
        rows.forEach(function (day) {
          var link = h('a', {
            href: '#', text: day.date || day.folder
          }, day.date || day.folder);
          link.addEventListener('click', function (event) {
            event.preventDefault();
            state.calendar.day = day.folder;
            loadDay();
          });
          body.appendChild(h('tr', { class: state.calendar.day === day.folder ? 'active' : null },
            h('td', null, link),
            h('td', { class: 'num' }, fmtNumber(day.files)),
            h('td', { class: 'num' }, fmtBytes(day.size))));
        });
        daysHost.replaceChildren(h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' }, h('thead', null, head), body)));
      }

      function loadDay() {
        drawDays();
        if (!state.calendar.day) { filesHost.replaceChildren(); return; }
        setLoading(filesHost, '正在读取当天文件…');
        api('/api/tools/calendar/day?folder=' + encodeURIComponent(state.calendar.day))
          .then(function (day) {
            if (!day.files.length) {
              filesHost.replaceChildren(h('div', { class: 'card', style: 'margin-top:12px' },
                h('div', { class: 'row-title' }, day.folder),
                empty('该日期文件夹内没有文件。')));
              return;
            }
            var head = h('tr', null, h('th', null, '文件名'), h('th', { class: 'num' }, '大小'),
              h('th', null, '修改时间'));
            var body = h('tbody');
            day.files.forEach(function (file) {
              body.appendChild(h('tr', null,
                h('td', null, file.name),
                h('td', { class: 'num' }, fmtBytes(file.size)),
                h('td', null, file.mtime || '-')));
            });
            var title = day.folder + ' · ' + fmtNumber(day.total) + ' 个文件'
              + (day.truncated ? '（仅列前 ' + day.files.length + ' 个）' : '');
            filesHost.replaceChildren(h('div', { class: 'card', style: 'margin-top:12px' },
              h('div', { class: 'row-title', style: 'margin-bottom:8px' }, title),
              h('div', { class: 'table-wrap' },
                h('table', { class: 'db-table' }, h('thead', null, head), body))));
          })
          .catch(function (error) { setError(filesHost, error, loadDay); });
      }

      drawDays();
    }).catch(function (error) {
      setError(host, error, function () { renderCalendar(host); });
    });
  }

  function renderLauncher(host) {
    host.replaceChildren();
    setLoading(host, '正在读取快捷项…');
    api('/api/tools/launcher/overview').then(function (data) {
      host.replaceChildren();
      if (!data.total) {
        host.appendChild(empty('尚未配置快捷项。请在桌面端「继续操作」页添加。'));
        return;
      }
      host.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'muted small' }, data.note || '')));
      var head = h('tr', null,
        h('th', { class: 'num' }, '#'), h('th', null, '名称'),
        h('th', null, '类型'), h('th', null, '路径状态'),
        h('th', null, '操作'));
      var body = h('tbody');
      (data.items || []).forEach(function (item) {
        var actionCell = h('td', null);
        if (item.type === 'link') {
          var openBtn = h('button', {
            class: 'btn', type: 'button'
          }, '打开链接');
          openBtn.disabled = !item.exists || !item.url;
          openBtn.addEventListener('click', function () {
            window.open(item.url, '_blank', 'noopener');
            toast('已在新标签打开：' + (item.name || ''), 'success');
          });
          actionCell.appendChild(openBtn);
        } else {
          var launchBtn = h('button', {
            class: 'btn', type: 'button'
          }, '启动');
          launchBtn.disabled = !item.exists;
          launchBtn.addEventListener('click', function () {
            if (!window.confirm(
              '将在运行 WebUI 的电脑上启动「' + (item.name || '') + '」。\n'
              + '不会在你当前设备的浏览器中打开。\n确定吗？')) {
              return;
            }
            launchBtn.disabled = true;
            post('/api/tools/launcher/launch', { index: item.index })
              .then(function (result) {
                toast((result && result.message) || '已启动', 'success');
              })
              .catch(function (error) {
                toast('启动失败：' + error.message, 'error');
              })
              .then(function () { launchBtn.disabled = false; });
          });
          actionCell.appendChild(launchBtn);
        }
        body.appendChild(h('tr', null,
          h('td', { class: 'num' }, item.index),
          h('td', null, item.name || '-'),
          h('td', null, item.type === 'link' ? '链接' : '软件'),
          h('td', null, item.exists ? '可用' : '未找到'),
          actionCell));
      });
      host.appendChild(h('div', { class: 'table-wrap' },
        h('table', { class: 'db-table' }, h('thead', null, head), body)));
    }).catch(function (error) {
      setError(host, error, function () { renderLauncher(host); });
    });
  }

  function renderMigrate(host) {
    host.replaceChildren();
    setLoading(host, '正在读取迁移配置…');
    Promise.all([
      api('/api/tools/migrate/overview'),
      api('/api/tools/migrate/scan')
    ]).then(function (results) {
      var overview = results[0];
      var scan = results[1];
      host.replaceChildren();
      if (!overview.has_work_dir) {
        host.appendChild(empty('尚未设置工作目录。请先在桌面端「全局设置」中配置工作目录。'));
        return;
      }
      if (!overview.has_target_dir) {
        host.appendChild(empty('尚未设置迁移目标目录。请先在桌面端「文件迁移」页选择目标目录。'));
        return;
      }
      var stats = h('div', { class: 'grid cols-3', style: 'margin-bottom:12px' });
      stats.appendChild(statCard('日期文件夹', fmtNumber(scan.total), '工作目录内'));
      var selectedCount = 0;
      var selectedSize = 0;
      scan.folders.forEach(function (item) {
        if (item.is_today) { selectedCount++; selectedSize += item.size; }
      });
      stats.appendChild(statCard('默认勾选（当天）', fmtNumber(selectedCount),
        fmtBytes(selectedSize)));
      stats.appendChild(statCard('目标目录', overview.target_dir || '-', '桌面端设置'));
      host.appendChild(stats);
      host.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'muted small' }, overview.note || '')));
      if (!scan.folders.length) {
        host.appendChild(empty('工作目录下没有日期文件夹。'));
        return;
      }

      var selected = state.migrate.selected;
      var mode = state.migrate.mode;
      var known = new Set(scan.folders.map(function (item) { return item.folder; }));
      Array.from(selected).forEach(function (name) {
        if (!known.has(name)) selected.delete(name);
      });
      if (!selected.size) {
        scan.folders.forEach(function (item) {
          if (item.is_today) selected.add(item.folder);
        });
      }

      var copyRadio = h('input', { type: 'radio', name: 'migrate-mode', checked: mode === 'copy' });
      var moveRadio = h('input', { type: 'radio', name: 'migrate-mode', checked: mode === 'move' });
      copyRadio.addEventListener('change', function () { if (copyRadio.checked) { state.migrate.mode = 'copy'; updateButtons(); } });
      moveRadio.addEventListener('change', function () { if (moveRadio.checked) { state.migrate.mode = 'move'; updateButtons(); } });
      var selectedLabel = h('span', { class: 'muted small' });
      var selectAll = h('input', { type: 'checkbox' });
      var runBtn = h('button', { class: 'btn primary', type: 'button' }, '开始迁移');
      var rescan = h('button', { class: 'btn', type: 'button' }, '刷新');
      host.appendChild(h('div', { class: 'toolbar' },
        h('label', { class: 'muted small' }, copyRadio, ' 复制（保留源）'),
        h('label', { class: 'muted small' }, moveRadio, ' 剪切（移动）'),
        h('span', { style: 'flex:1' }),
        h('label', { class: 'muted small' }, selectAll, ' 全选'),
        selectedLabel, rescan, runBtn));

      var jobHost = h('div');
      var listHost = h('div');
      var resultHost = h('div');
      host.appendChild(jobHost);
      host.appendChild(listHost);
      host.appendChild(resultHost);

      var running = false;
      var columns = [
        { key: 'date', label: '日期' },
        { key: 'files', label: '文件' },
        { key: 'size', label: '大小' }
      ];

      function updateButtons() {
        runBtn.disabled = running || selected.size === 0;
        runBtn.textContent = running ? '迁移中…' : '开始迁移';
        selectedLabel.textContent = '已选 ' + selected.size + ' 个文件夹';
        selectAll.checked = scan.folders.length > 0
          && selected.size === scan.folders.length;
        selectAll.indeterminate = selected.size > 0
          && selected.size < scan.folders.length;
        runBtn.classList.toggle('danger', state.migrate.mode === 'move');
      }

      function draw() {
        var rows = sortRows(scan.folders, columns, state.migrate);
        var head = h('tr', null, h('th', { style: 'width:36px' }, ''));
        columns.forEach(function (column) {
          var mark = state.migrate.order === column.key
            ? (state.migrate.asc ? ' ↑' : ' ↓') : '';
          head.appendChild(h('th', {
            class: 'sortable',
            onclick: function () {
              cycleSort(state.migrate, column.key);
              draw();
            }
          }, column.label + mark));
        });
        var body = h('tbody');
        rows.forEach(function (item) {
          var check = h('input', {
            type: 'checkbox', checked: selected.has(item.folder)
          });
          check.addEventListener('change', function () {
            if (check.checked) selected.add(item.folder);
            else selected.delete(item.folder);
            updateButtons();
          });
          body.appendChild(h('tr', null,
            h('td', null, check),
            h('td', null, (item.date || item.folder) + (item.is_today ? '（今天）' : '')),
            h('td', { class: 'num' }, fmtNumber(item.files)),
            h('td', { class: 'num' }, fmtBytes(item.size))));
        });
        listHost.replaceChildren(h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' }, h('thead', null, head), body)));
        updateButtons();
      }

      selectAll.addEventListener('change', function () {
        selected.clear();
        if (selectAll.checked) {
          scan.folders.forEach(function (item) { selected.add(item.folder); });
        }
        draw();
      });
      rescan.addEventListener('click', function () {
        selected.clear();
        renderMigrate(host);
      });
      runBtn.addEventListener('click', function () {
        if (!selected.size || running) return;
        var modeText = state.migrate.mode === 'copy' ? '复制（保留源）' : '剪切（移动）';
        var folders = Array.from(selected);
        if (!window.confirm(
          '确认迁移\n\n操作模式: ' + modeText +
          '\n将迁移 ' + folders.length + ' 个日期文件夹到「' +
          (overview.target_dir || '目标目录') + '」。\n同名文件会跳过并计入重复，不会覆盖目标。\n\n确认继续？')) {
          return;
        }
        running = true;
        updateButtons();
        post('/api/tools/migrate/run', {
          mode: state.migrate.mode, folders: folders, confirmed: true
        }).then(function (data) {
          watchJob(jobHost, data.job, function (job) {
            running = false;
            updateButtons();
            var summary = job.result && job.result.summary
              ? job.result.summary : '迁移完成';
            toast(summary, 'success');
            showResult(job);
          }, {
            cancelable: true,
            onSettled: function (job) {
              running = false;
              updateButtons();
              if (job.status === 'cancelled') {
                toast('文件迁移已取消', 'info');
              }
            }
          });
        }).catch(function (error) {
          running = false;
          updateButtons();
          toast('迁移失败：' + error.message, 'error');
        });
      });
      draw();
    }).catch(function (error) {
      setError(host, error, function () { renderMigrate(host); });
    });

    function showResult(job) {
      var result = job.result || {};
      var rows = result.results || [];
      if (!rows.length) return;
      var head = h('tr', null,
        h('th', null, '文件夹'), h('th', null, '结果'),
        h('th', { class: 'num' }, '迁移文件'), h('th', { class: 'num' }, '重复跳过'),
        h('th', null, '错误'));
      var body = h('tbody');
      rows.forEach(function (row) {
        body.appendChild(h('tr', null,
          h('td', null, row.folder),
          h('td', null, row.ok ? '成功' : '失败'),
          h('td', { class: 'num' }, fmtNumber(row.moved || 0)),
          h('td', { class: 'num' }, fmtNumber(row.duplicates || 0)),
          h('td', { class: 'muted small' }, row.error || '-')));
      });
      resultHost.replaceChildren(h('div', {
        class: 'card', style: 'margin-top:12px'
      },
        h('div', { class: 'row-title', style: 'margin-bottom:8px' },
          result.summary || '迁移结果'),
        h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' }, h('thead', null, head), body))));
    }
  }

  function renderModule(host, key) {
    host.replaceChildren();
    setLoading(host, '正在读取模块信息…');
    api('/api/modules/' + encodeURIComponent(key)).then(function (data) {
      host.replaceChildren();
      host.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'row-title' }, data.name),
        h('div', { class: 'muted small', style: 'margin-top:4px' }, data.desc || ''),
        h('div', { class: 'muted small', style: 'margin-top:6px' }, data.note || '')));

      var settings = data.settings || [];
      if (settings.length) {
        var card = h('div', { class: 'card', style: 'margin-bottom:12px' });
        card.appendChild(h('div', { class: 'row-title', style: 'margin-bottom:8px' }, '当前配置'));
        settings.forEach(function (item) {
          card.appendChild(h('div', { class: 'kv' },
            h('span', { class: 'muted' }, item.label),
            h('span', null, item.value || '-')));
        });
        host.appendChild(card);
      }

      var actions = data.actions || [];
      if (actions.length) {
        var actionCard = h('div', { class: 'card' });
        actionCard.appendChild(h('div', { class: 'row-title', style: 'margin-bottom:8px' }, '桌面操作'));
        actions.forEach(function (name) {
          actionCard.appendChild(h('button', {
            class: 'btn', type: 'button', disabled: true,
            title: '该操作暂未在 Web 端开放，请在桌面端使用',
            style: 'margin:4px'
          }, name));
        });
        actionCard.appendChild(h('div', { class: 'muted small', style: 'margin-top:8px' },
          '入口已预留；当前版本只提供只读概览。'));
        host.appendChild(actionCard);
      }

      if (data.days && data.days.length) {
        var daysCard = h('div', { class: 'card', style: 'margin-top:12px' });
        daysCard.appendChild(h('div', { class: 'row-title', style: 'margin-bottom:8px' }, '最近日期'));
        var days = h('div', { class: 'days' });
        data.days.forEach(function (day) {
          days.appendChild(h('span', { class: 'badge' }, day));
        });
        daysCard.appendChild(days);
        host.appendChild(daysCard);
      }
    }).catch(function (error) {
      setError(host, error, function () { renderModule(host, key); });
    });
  }

  function renderTasks(host) {
    host.replaceChildren();
    setLoading(host, '正在读取计划任务…');
    api('/api/tasks').then(function (data) {
      host.replaceChildren();
      var items = data.tasks || [];
      if (!items.length) {
        host.appendChild(empty('暂无计划任务。'));
        return;
      }
      var columns = [
        { key: 'label', label: '任务' },
        { key: 'usage', label: '用途' },
        { key: 'schedule', label: '计划' },
        { key: 'installed', label: '状态', bool: true },
        { key: 'next_run', label: '下次运行' }
      ];
      var rows = sortRows(items, columns, state.tasks);
      var head = h('tr');
      columns.forEach(function (column) {
        var mark = state.tasks.order === column.key
          ? (state.tasks.asc ? ' ↑' : ' ↓') : '';
        head.appendChild(h('th', {
          class: 'sortable',
          onclick: function () {
            cycleSort(state.tasks, column.key);
            renderTasks(host);
          }
        }, column.label + mark));
      });
      head.appendChild(h('th', null, '操作'));
      var body = h('tbody');
      rows.forEach(function (item) {
        var action = h('button', { class: 'btn', type: 'button' }, '设置');
        action.addEventListener('click', function () {
          openTaskForm(item);
        });
        body.appendChild(h('tr', null,
          h('td', null, item.label || '-'),
          h('td', { class: 'muted small' }, item.usage || '-'),
          h('td', null, item.schedule || '-'),
          h('td', null, item.installed ? '已安装' : '未安装'),
          h('td', null, item.next_run || '-'),
          h('td', null, action)));
      });
      host.appendChild(h('div', { class: 'table-wrap' },
        h('table', { class: 'db-table' }, h('thead', null, head), body)));
      host.appendChild(h('div', { class: 'muted small', style: 'margin-top:8px' },
        '安装 / 更新 / 删除会操作运行 WebUI 电脑上的 Windows 计划任务；'
        + '计划设置与桌面端「任务」页共用。'));

      function openTaskForm(item) {
        var overlay = h('div', { class: 'preview-overlay' });
        var box = h('div', { class: 'preview-box', style: 'max-width:460px' });
        var caption = h('div', { class: 'preview-caption' },
          h('span', { class: 'ellipsis' }, item.label || '定时任务'),
          h('button', { class: 'btn', type: 'button' }, '关闭'));
        var closeBtn = caption.lastChild;
        var form = h('div', { style: 'padding:14px; overflow:auto' },
          h('div', { class: 'loading', text: '正在读取计划设置…' }));
        box.appendChild(caption);
        box.appendChild(form);
        overlay.appendChild(box);
        document.body.appendChild(overlay);
        closeBtn.addEventListener('click', function () { overlay.remove(); });
        overlay.addEventListener('click', function (event) {
          if (event.target === overlay) overlay.remove();
        });

        api('/api/tasks/schedule?id=' + encodeURIComponent(item.id))
          .then(function (schedule) {
            var modeSel = h('select', { class: 'select', style: 'width:100%' },
              h('option', { value: 'daily', selected: schedule.mode === 'daily' }, '每天'),
              h('option', {
                value: 'interval',
                selected: schedule.mode === 'interval',
                disabled: !schedule.interval_supported
              }, '按间隔'));
            var timeInput = h('input', {
              class: 'input', style: 'width:100%', value: schedule.time || '09:00'
            });
            var intervalInput = h('input', {
              class: 'input', style: 'width:100%', type: 'number',
              min: '1', max: '1439', value: String(schedule.interval || 30)
            });
            var timeRow = h('div', null,
              h('div', { class: 'muted small', style: 'margin-top:10px' }, '每天时间（HH:MM）'),
              timeInput);
            var intervalRow = h('div', null,
              h('div', { class: 'muted small', style: 'margin-top:10px' }, '间隔（分钟，1–1439）'),
              intervalInput);
            function syncMode() {
              timeRow.style.display = modeSel.value === 'daily' ? '' : 'none';
              intervalRow.style.display = modeSel.value === 'interval' ? '' : 'none';
            }
            modeSel.addEventListener('change', syncMode);
            var installBtn = h('button', {
              class: 'btn primary', type: 'button'
            }, item.installed ? '更新计划' : '安装计划');
            var deleteBtn = item.installed
              ? h('button', { class: 'btn danger', type: 'button' }, '删除任务')
              : null;
            var status = h('div', { class: 'muted small', style: 'margin-top:8px' });
            var busy = false;

            function submitInstall() {
              if (busy) return;
              var mode = modeSel.value;
              var timeText = String(timeInput.value || '').trim();
              if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(timeText)) {
                status.textContent = '时间格式应为 HH:MM，例如 09:30';
                return;
              }
              var interval = Number(intervalInput.value);
              if (mode === 'interval' &&
                  (!isFinite(interval) || interval < 1 || interval > 1439)) {
                status.textContent = '间隔需在 1–1439 分钟之间';
                return;
              }
              var planText = mode === 'daily'
                ? ('每天 ' + timeText)
                : ('每 ' + interval + ' 分钟');
              if (!window.confirm(
                '将在运行 WebUI 的电脑上' + (item.installed ? '更新' : '安装') +
                '计划任务「' + (item.label || '') + '」：' + planText + '。\n'
                + '与桌面端共用同一 Windows 计划任务。确定吗？')) {
                return;
              }
              busy = true;
              installBtn.disabled = true;
              if (deleteBtn) deleteBtn.disabled = true;
              post('/api/tasks/install', {
                id: item.id, mode: mode, time: timeText,
                interval: interval, confirmed: true
              }).then(function (result) {
                toast((result && result.message) || '计划已保存', 'success');
                overlay.remove();
                renderTasks(host);
              }).catch(function (error) {
                busy = false;
                installBtn.disabled = false;
                if (deleteBtn) deleteBtn.disabled = false;
                status.textContent = '操作失败：' + error.message;
              });
            }

            function submitUninstall() {
              if (busy) return;
              if (!window.confirm(
                '将删除运行 WebUI 电脑上的计划任务「' + (item.label || '') +
                '」，之后不再自动执行。\n确定吗？')) {
                return;
              }
              busy = true;
              installBtn.disabled = true;
              if (deleteBtn) deleteBtn.disabled = true;
              post('/api/tasks/uninstall', {
                id: item.id, confirmed: true
              }).then(function (result) {
                toast((result && result.message) || '已删除', 'success');
                overlay.remove();
                renderTasks(host);
              }).catch(function (error) {
                busy = false;
                installBtn.disabled = false;
                if (deleteBtn) deleteBtn.disabled = false;
                status.textContent = '操作失败：' + error.message;
              });
            }

            installBtn.addEventListener('click', submitInstall);
            if (deleteBtn) deleteBtn.addEventListener('click', submitUninstall);
            syncMode();
            form.replaceChildren(
              h('div', { class: 'muted small' }, item.usage || ''),
              modeSel,
              timeRow,
              intervalRow,
              h('div', { class: 'muted small', style: 'margin-top:10px' },
                '操作在运行 WebUI 的电脑上执行；安装 / 更新会同时保存计划设置。'),
              status,
              h('div', { style: 'margin-top:12px; display:flex; gap:8px; justify-content:flex-end' },
                deleteBtn, h('span', { style: 'flex:1' }), installBtn));
          })
          .catch(function (error) {
            form.replaceChildren(h('div', { class: 'error' },
              '读取计划设置失败：' + error.message));
          });
      }
    }).catch(function (error) {
      setError(host, error, function () { renderTasks(host); });
    });
  }

  function renderSystem(host) {
    host.replaceChildren();
    setLoading(host, '正在读取系统信息…');
    Promise.all([
      api('/api/system'),
      api('/api/system/settings')
    ]).then(function (results) {
      var data = results[0];
      var settings = results[1];
      host.replaceChildren();
      var info = h('div', { class: 'card', style: 'margin-bottom:12px' });
      info.appendChild(h('div', { class: 'row-title', style: 'margin-bottom:8px' }, '版本与目录'));
      [
        ['版本', data.version],
        ['程序目录', data.app_root],
        ['配置目录', data.config_dir],
        ['导出目录', data.export_dir],
        ['工作目录', data.work_dir]
      ].forEach(function (pair) {
        info.appendChild(h('div', { class: 'kv' },
          h('span', { class: 'muted' }, pair[0]),
          h('span', null, pair[1] || '-')));
      });
      host.appendChild(info);

      var webui = h('div', { class: 'card', style: 'margin-bottom:12px' });
      webui.appendChild(h('div', { class: 'row-title', style: 'margin-bottom:8px' }, 'WebUI'));
      var cfg = data.webui || {};
      var tunnelRows = [
        ['端口', cfg.port],
        ['局域网访问', cfg.lan ? '已开启' : '关闭'],
        ['公网隧道', cfg.tunnel_name || cfg.tunnel || '未启用']
      ];
      if (cfg.tunnel_public_url) {
        tunnelRows.push(['公网地址', cfg.tunnel_public_url]);
      }
      tunnelRows.forEach(function (pair) {
        webui.appendChild(h('div', { class: 'kv' },
          h('span', { class: 'muted' }, pair[0]),
          h('span', null, pair[1])));
      });
      var clients = cfg.tunnel_clients || [];
      if (clients.length) {
        var clientRow = h('div', { class: 'muted small', style: 'margin-top:8px' },
          '隧道客户端：');
        clients.forEach(function (item) {
          clientRow.appendChild(h('span', {
            class: 'badge ' + (item.ready ? 'ok' : 'warn'),
            style: 'margin-left:6px'
          }, item.name + (item.ready ? ' 已安装' : ' 未安装')));
        });
        webui.appendChild(clientRow);
      }
      webui.appendChild(h('div', { class: 'muted small', style: 'margin-top:6px' },
        '隧道由桌面端「WebUI 公网访问」启动 / 停止，本页只读展示。'));
      host.appendChild(webui);

      var dirs = h('div', { class: 'card', style: 'margin-bottom:12px' });
      dirs.appendChild(h('div', { class: 'row-title', style: 'margin-bottom:8px' }, '目录设置'));
      var workInput = h('input', {
        class: 'input', style: 'width:100%',
        value: settings.work_dir || '',
        placeholder: '工作目录完整路径；留空 = 不修改'
      });
      var exportInput = h('input', {
        class: 'input', style: 'width:100%',
        value: settings.export_dir || '',
        placeholder: '导出目录完整路径；留空 = 不修改'
      });
      workInput.disabled = !!settings.work_dir_locked;
      exportInput.disabled = !!settings.export_dir_locked;
      var saveDirs = h('button', { class: 'btn primary', type: 'button' },
        '保存目录');
      dirs.appendChild(h('div', { class: 'muted small', style: 'margin-bottom:4px' },
        '工作目录' + (settings.work_dir_locked ? '（已锁定）' : '')));
      dirs.appendChild(workInput);
      dirs.appendChild(h('div', { class: 'muted small', style: 'margin:8px 0 4px' },
        '导出目录' + (settings.export_dir_locked ? '（已锁定）' : '')));
      dirs.appendChild(exportInput);
      dirs.appendChild(h('div', { class: 'toolbar', style: 'margin-top:8px' },
        h('span', { class: 'muted small' },
          '目录必须已存在；修改后立即影响扫描与导出路径，并与桌面端共用。'),
        h('span', { style: 'flex:1' }), saveDirs));
      saveDirs.addEventListener('click', function () {
        if (!window.confirm('保存后会立即影响扫描与导出路径，确定保存目录设置？')) {
          return;
        }
        saveDirs.disabled = true;
        post('/api/system/dirs', {
          work_dir: workInput.value.trim(),
          export_dir: exportInput.value.trim(),
          confirmed: true
        }).then(function (result) {
          saveDirs.disabled = false;
          toast('目录设置已保存', 'success');
          renderSystem(host);
        }).catch(function (error) {
          saveDirs.disabled = false;
          toast('保存失败：' + error.message, 'error');
        });
      });
      host.appendChild(dirs);

      var themeCard = h('div', { class: 'card' });
      themeCard.appendChild(h('div', { class: 'row-title', style: 'margin-bottom:8px' }, '外观主题'));
      var themeCurrent = settings.theme_follow_system ? 'auto' : settings.theme;
      var themeSel = h('select', { class: 'select' },
        [['light', '浅色'], ['dark', '深色'], ['auto', '跟随系统']].map(
          function (pair) {
            return h('option', {
              value: pair[0], selected: pair[0] === themeCurrent
            }, pair[1]);
          }));
      var saveTheme = h('button', { class: 'btn primary', type: 'button' },
        '保存主题');
      themeCard.appendChild(h('div', { class: 'toolbar' },
        themeSel, saveTheme,
        h('span', { class: 'muted small' },
          '主题写入桌面端配置；桌面端若启用定时切换，以其设置为准。')));
      saveTheme.addEventListener('click', function () {
        saveTheme.disabled = true;
        post('/api/system/theme', { theme: themeSel.value })
          .then(function () {
            saveTheme.disabled = false;
            state.theme = themeSel.value;
            localStorage.setItem('webui-theme', state.theme);
            applyTheme();
            toast('主题已保存', 'success');
          }).catch(function (error) {
            saveTheme.disabled = false;
            toast('保存失败：' + error.message, 'error');
          });
      });
      host.appendChild(themeCard);

      var deps = h('div', { class: 'card', style: 'margin-top:12px' });
      deps.appendChild(h('div', { class: 'row-title', style: 'margin-bottom:8px' }, '依赖与组件'));
      var depHost = h('div');
      deps.appendChild(depHost);
      host.appendChild(deps);
      depHost.replaceChildren(h('div', { class: 'loading' }, '正在检测依赖…'));
      api('/api/system/deps').then(function (result) {
        depHost.replaceChildren();
        (result.items || []).forEach(function (item) {
          depHost.appendChild(h('div', {
            style: 'display:flex;flex-wrap:wrap;gap:8px;align-items:baseline;'
              + 'padding:6px 0;border-bottom:1px solid var(--border)'
          },
            h('span', { style: 'min-width:110px;font-weight:bold' }, item.name),
            h('span', { class: 'badge ' + (item.ok ? 'ok' : 'warn') },
              item.ok ? '已就绪' : '未就绪'),
            h('span', { class: 'muted small' }, item.usage || ''),
            h('span', { style: 'flex:1' }),
            h('span', { class: 'muted small' }, item.detail || '')));
        });
        depHost.appendChild(h('div', {
          class: 'muted small', style: 'margin-top:8px'
        }, result.note || ''));
      }).catch(function (error) {
        depHost.replaceChildren(h('div', { class: 'error' },
          '依赖检测失败：' + error.message));
      });

      host.appendChild(h('div', { class: 'muted small' }, settings.note || ''));
    }).catch(function (error) {
      setError(host, error, function () { renderSystem(host); });
    });
  }

  function cycleSort(state, key) {
    if (state.order !== key) {
      state.order = key;
      state.asc = true;
    } else if (state.asc) {
      state.asc = false;
    } else {
      state.order = '';
      state.asc = true;
    }
  }

  function sortRows(items, columns, order) {
    if (!order.order) return items;
    var column = null;
    columns.forEach(function (item) {
      if (item.key === order.order) column = item;
    });
    if (!column) return items;
    var sorted = items.slice();
    sorted.sort(function (left, right) {
      var av = left[column.key];
      var bv = right[column.key];
      if (column.bool) {
        av = av ? 1 : 0;
        bv = bv ? 1 : 0;
      }
      var aempty = av === '' || av === null || av === undefined;
      var bempty = bv === '' || bv === null || bv === undefined;
      if (aempty && bempty) return 0;
      if (aempty) return 1;
      if (bempty) return -1;
      var result;
      if (typeof av === 'number' && typeof bv === 'number') result = av - bv;
      else result = String(av).localeCompare(String(bv), 'zh-CN');
      return order.asc ? result : -result;
    });
    return sorted;
  }

  function renderMusicTracker(root) {
    renderTrackerPage(root, 'music');
  }

  function renderMediaTracker(root) {
    renderTrackerPage(root, 'media');
  }

  function renderTrackerPage(root, kind) {
    var label = kind === 'music' ? '音乐账号' : '网媒账号';
    setLoading(root, '正在读取' + label + '与历史缓存…');
    api(kind === 'music' ? '/api/trackers/music' : '/api/trackers/media')
      .then(function (data) {
        root.replaceChildren();
        var platformSel = h('select', { class: 'select' });
        platformSel.appendChild(h('option', { value: '' }, '全部平台'));
        (data.platforms || []).forEach(function (item) {
          platformSel.appendChild(h('option', { value: item.key },
            item.name));
        });
        platformSel.value = state.trackers.platform || '';
        var grid = h('div', { class: 'account-grid' });

        function draw() {
          grid.replaceChildren();
          var rows = (data.accounts || []).filter(function (row) {
            return !state.trackers.platform
              || row.platform === state.trackers.platform;
          });
          if (!rows.length) {
            grid.appendChild(empty('该平台暂无账号。账号配置在桌面端'
              + '「Cookie 管理 → 账号配置」维护。'));
            return;
          }
          rows.forEach(function (row) {
            grid.appendChild(buildAccountCard(row, kind));
          });
        }

        platformSel.addEventListener('change', function () {
          state.trackers.platform = platformSel.value;
          draw();
        });
        var refreshBtn = h('button', {
          class: 'btn', type: 'button'
        }, '刷新');
        refreshBtn.addEventListener('click', function () {
          renderTrackerPage(root, kind);
        });
        var exportBtn = h('button', {
          class: 'btn', type: 'button', disabled: true,
          title: '按组导出在桌面端'
        }, '按组导出');
        var collectBtn = h('button', {
          class: 'btn primary', type: 'button', disabled: true,
          title: '采集需要桌面端登录态与采集引擎'
        }, '获取全部');
        root.appendChild(h('div', { class: 'toolbar' },
          refreshBtn, exportBtn, collectBtn,
          h('span', { class: 'muted small' }, '平台'), platformSel,
          h('span', { class: 'spacer' }),
          h('span', { class: 'muted small' },
            '共 ' + fmtNumber(data.total) + ' 个账号；采集与导出仍在桌面端。')));
        root.appendChild(grid);
        draw();
      }).catch(function (error) {
        setError(root, error, function () { renderTrackerPage(root, kind); });
      });
  }

  function buildAccountCard(row, kind) {
    var historyHost = h('div', { style: 'margin-top:8px' });
    var historyBtn = h('button', {
      class: 'btn mini', type: 'button'
    }, '历史');
    historyBtn.addEventListener('click', function () {
      loadTrackerHistory(historyHost, kind, row.name);
    });
    var metrics = h('div', { class: 'metric-grid' });
    (row.metrics || []).forEach(function (metric) {
      metrics.appendChild(h('div', { class: 'metric' },
        h('div', { class: 'label' }, metric.label),
        h('div', { class: 'value', title: String(metric.value) },
          metric.value)));
    });
    return h('div', { class: 'card account-card' },
      h('div', { class: 'account-head' },
        h('div', { style: 'min-width:0' },
          h('div', { class: 'row-title' }, row.name),
          h('div', { class: 'muted small' },
            row.platform_name + (row.group ? ' · ' + row.group : ''))),
        h('div', { class: 'muted small', style: 'white-space:nowrap' },
          row.last_time ? '最近 ' + row.last_time
            : (row.records ? row.records + ' 条记录' : '暂无采集'))),
      metrics,
      h('div', { class: 'col-tools' }, historyBtn),
      historyHost);
  }

  function loadTrackerHistory(host, kind, name) {
    if (host.dataset.open === '1') {
      host.replaceChildren();
      host.dataset.open = '';
      return;
    }
    host.dataset.open = '1';
    setLoading(host, '正在读取历史…');
    api('/api/trackers/' + kind + '/history?name='
      + encodeURIComponent(name) + '&limit=50')
      .then(function (result) {
        if (!(result.rows || []).length) {
          host.replaceChildren(empty(
            '暂无历史缓存。桌面端点「采集」后生成。'));
          return;
        }
        var head = h('tr');
        result.columns.forEach(function (column) {
          head.appendChild(h('th', null, column.label));
        });
        var body = h('tbody');
        result.rows.forEach(function (cells) {
          var tr = h('tr');
          result.columns.forEach(function (column) {
            tr.appendChild(h('td', { class: 'ellipsis' },
              cells[column.key] == null ? '-' : cells[column.key]));
          });
          body.appendChild(tr);
        });
        host.replaceChildren(h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' },
            h('thead', null, head), body)));
      }).catch(function (error) {
        setError(host, error, function () {
          host.dataset.open = '';
          loadTrackerHistory(host, kind, name);
        });
      });
  }

  function renderFanGroups(root) {
    setLoading(root, '正在读取 QQ 群订阅列表…');
    api('/api/fangroups').then(function (data) {
      root.replaceChildren();
      var groups = data.groups || [];
      var tableHost = h('div');
      var historyHost = h('div', { style: 'margin-top:12px' });
      var refreshBtn = h('button', {
        class: 'btn', type: 'button'
      }, '刷新');
      refreshBtn.addEventListener('click', function () {
        renderFanGroups(root);
      });

      function draw() {
        if (!groups.length) {
          tableHost.replaceChildren(empty(
            '暂无已订阅 QQ 群。桌面端「订阅列表」添加群号或分享链接后刷新。'));
          return;
        }
        var head = h('tr');
        ['', '群名', '群号', '人数', '变化', '状态', '来源']
          .forEach(function (title) {
            head.appendChild(h('th', null, title));
          });
        head.appendChild(h('th', null, ''));
        var body = h('tbody');
        groups.forEach(function (row) {
          var fallback = h('span', {
            class: 'fg-avatar fg-avatar-fallback'
          }, (row.name || '?').slice(0, 1));
          var avatar = fallback;
          if (row.has_avatar) {
            avatar = h('img', {
              class: 'fg-avatar', alt: '',
              src: '/api/fangroups/avatar?no='
                + encodeURIComponent(row.no) + '&token='
                + encodeURIComponent(token)
            });
            avatar.addEventListener('error', function () {
              avatar.replaceWith(fallback);
            });
          }
          var active = state.fangroups.open === row.no;
          body.appendChild(h('tr', null,
            h('td', null, avatar),
            h('td', null, row.name || '-'),
            h('td', null, row.no || '-'),
            h('td', { class: 'num' }, row.members == null
              ? '-' : fmtNumber(row.members)),
            h('td', {
              class: 'num' + (String(row.change || '').charAt(0) === '+'
                ? ' delta-up'
                : (String(row.change || '').charAt(0) === '-'
                  && row.change !== '-' ? ' delta-down' : ''))
            }, row.change || '-'),
            h('td', { class: 'muted small' }, row.status || '-'),
            h('td', { class: 'muted small' }, row.source || '-'),
            h('td', null, h('button', {
              class: 'btn mini', type: 'button',
              onclick: function () {
                if (state.fangroups.open === row.no) {
                  state.fangroups.open = '';
                  historyHost.replaceChildren();
                } else {
                  state.fangroups.open = row.no;
                  loadHistory(row);
                }
                draw();
              }
            }, active ? '收起' : '历史'))));
        });
        tableHost.replaceChildren(h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' },
            h('thead', null, head), body)));
      }

      function loadHistory(row) {
        setLoading(historyHost, '正在读取人数历史…');
        api('/api/fangroups/history?no=' + encodeURIComponent(row.no)
          + '&limit=200')
          .then(function (result) {
            if (!(result.rows || []).length) {
              historyHost.replaceChildren(empty(
                '暂无人数历史。桌面端刷新群信息后生成。'));
              return;
            }
            var head = h('tr', null,
              h('th', null, '采集时间'), h('th', { class: 'num' }, '人数'),
              h('th', null, '群名'));
            var body = h('tbody');
            result.rows.forEach(function (item) {
              body.appendChild(h('tr', null,
                h('td', null, item.time || '-'),
                h('td', { class: 'num' }, item.members == null
                  ? '-' : fmtNumber(item.members)),
                h('td', null, item.name || '-')));
            });
            historyHost.replaceChildren(
              h('div', { class: 'row-title', style: 'margin-bottom:8px' },
                row.name + ' · 人数历史（最新在前）'),
              h('div', { class: 'table-wrap' },
                h('table', { class: 'db-table' },
                  h('thead', null, head), body)));
          }).catch(function (error) {
            setError(historyHost, error, function () { loadHistory(row); });
          });
      }

      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '平台'),
          h('select', { class: 'select' },
            h('option', { value: 'qqgroup' }, 'QQ群')),
          h('input', {
            class: 'input grow', disabled: true,
            placeholder: '粘贴群号或分享链接（订阅在桌面端）'
          }),
          h('button', {
            class: 'btn primary', type: 'button', disabled: true
          }, '查询')),
        h('div', { class: 'toolbar', style: 'margin-top:8px' },
          h('button', {
            class: 'btn', type: 'button', disabled: true
          }, '导出 Excel'),
          h('button', {
            class: 'btn', type: 'button', disabled: true
          }, '下载头像'),
          h('span', { style: 'flex:1' }), refreshBtn),
        h('div', { class: 'muted small', style: 'margin-top:6px' },
          '可粘贴群号或群资料分享链接（免登录）；订阅、刷新、导出与头像下载仍在桌面端。')));
      root.appendChild(tableHost);
      root.appendChild(historyHost);
      draw();
    }).catch(function (error) {
      setError(root, error, function () { renderFanGroups(root); });
    });
  }

  function renderDouyinIndex(root) {
    setLoading(root, '正在读取抖音指数快照…');
    api('/api/douyin-index').then(function (data) {
      root.replaceChildren();
      var reports = data.reports || [];
      if (!reports.length) {
        root.appendChild(empty(
          '暂无本地快照。桌面端「抖音指数」搜索达人并抓取后会保存到本地，刷新本页即可查看。'));
        return;
      }
      var names = reports.map(function (item) { return item.name; });
      if (names.indexOf(state.douyin.name) < 0) {
        state.douyin.name = names[0];
      }
      var select = h('select', { class: 'select' });
      reports.forEach(function (item) {
        select.appendChild(h('option', {
          value: item.name,
          selected: item.name === state.douyin.name
        }, item.nick + (item.fetched_at ? '（' + item.fetched_at + '）' : '')));
      });
      select.value = state.douyin.name;
      select.addEventListener('change', function () {
        state.douyin.name = select.value;
        loadReport();
      });
      var refreshBtn = h('button', {
        class: 'btn', type: 'button'
      }, '刷新');
      refreshBtn.addEventListener('click', function () {
        renderDouyinIndex(root);
      });
      var summary = h('div', {
        class: 'muted small', style: 'margin:8px 0'
      });
      var tabs = h('div', { class: 'tabs' });
      var tabHost = h('div');
      var TABS = [['core', '核心指标'], ['trend', '每日趋势'],
        ['videos', '达人视频'], ['fans', '粉丝分析'],
        ['similar', '相似达人']];
      TABS.forEach(function (item) {
        tabs.appendChild(h('button', {
          class: 'tab' + (state.douyin.tab === item[0] ? ' active' : ''),
          type: 'button',
          onclick: function () {
            state.douyin.tab = item[0];
            tabs.querySelectorAll('.tab').forEach(function (button, index) {
              button.classList.toggle('active', TABS[index][0] === item[0]);
            });
            renderTab();
          }
        }, item[1]));
      });
      root.appendChild(h('div', { class: 'card' },
        h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '达人'), select,
          h('span', { class: 'muted small', style: 'margin-left:8px' },
            '搜索与抓取仍在桌面端'),
          h('span', { style: 'flex:1' }), refreshBtn)));
      root.appendChild(summary);
      root.appendChild(tabs);
      root.appendChild(tabHost);

      var report = null;

      function deltaClass(raw) {
        if (typeof raw !== 'number') return '';
        if (raw > 0) return ' delta-up';
        if (raw < 0) return ' delta-down';
        return '';
      }

      function tableOf(head, body) {
        return h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' }, h('thead', null, head), body));
      }

      function loadReport() {
        setLoading(tabHost, '正在读取达人报表…');
        api('/api/douyin-index/report?name='
          + encodeURIComponent(state.douyin.name))
          .then(function (result) {
            report = result;
            var author = result.author || {};
            var meta = result.meta || {};
            summary.textContent = author.name + '（抖音号 '
              + (author.uid || '-') + '）'
              + (author.tags ? ' · ' + author.tags : '')
              + ' · 粉丝 ' + author.fans + ' · 总获赞 ' + author.likes
              + ' · 作品 ' + (author.items == null ? '-' : author.items)
              + (meta.fetched_at ? ' · 采集于 ' + meta.fetched_at : '')
              + (meta.days ? ' · 统计 ' + meta.days + ' 天' : '');
            renderTab();
          }).catch(function (error) {
            setError(tabHost, error, loadReport);
          });
      }

      function renderTab() {
        if (!report) return;
        tabHost.replaceChildren();
        if (state.douyin.tab === 'core') renderCore();
        else if (state.douyin.tab === 'trend') renderTrend();
        else if (state.douyin.tab === 'videos') renderVideos();
        else if (state.douyin.tab === 'fans') renderFans();
        else renderSimilar();
      }

      function renderCore() {
        var head = h('tr', null, h('th', null, '指标'));
        ['昨日', '近7天', '近15天', '近30天'].forEach(function (title) {
          head.appendChild(h('th', { class: 'num' }, title));
          head.appendChild(h('th', { class: 'num' }, title + '环比'));
        });
        var body = h('tbody');
        (report.core || []).forEach(function (row) {
          var tr = h('tr', null, h('td', null, row.label));
          row.cells.forEach(function (cell) {
            tr.appendChild(h('td', { class: 'num' }, cell.value));
            tr.appendChild(h('td', {
              class: 'num' + deltaClass(cell.exchange_raw)
            }, cell.exchange));
          });
          body.appendChild(tr);
        });
        var avgHead = h('tr', null,
          h('th', null, '稿均指标'), h('th', { class: 'num' }, '近7天'),
          h('th', { class: 'num' }, '环比'), h('th', { class: 'num' }, '近30天'),
          h('th', { class: 'num' }, '环比'));
        var avgBody = h('tbody');
        (report.avg || []).forEach(function (row) {
          avgBody.appendChild(h('tr', null,
            h('td', null, row.label),
            h('td', { class: 'num' }, row.week),
            h('td', {
              class: 'num' + deltaClass(row.week_exchange_raw)
            }, row.week_exchange),
            h('td', { class: 'num' }, row.month),
            h('td', {
              class: 'num' + deltaClass(row.month_exchange_raw)
            }, row.month_exchange)));
        });
        tabHost.appendChild(h('div', { class: 'row-title', style: 'margin-bottom:8px' },
          '核心指标（昨日 / 近7天 / 近15天 / 近30天）'));
        tabHost.appendChild(tableOf(head, body));
        tabHost.appendChild(h('div', {
          class: 'row-title', style: 'margin:14px 0 8px'
        }, '稿均指标'));
        tabHost.appendChild(tableOf(avgHead, avgBody));
      }

      function renderTrend() {
        if (!(report.trend || []).length) {
          tabHost.appendChild(empty('暂无趋势数据。'));
          return;
        }
        var head = h('tr', null, h('th', null, '日期'),
          h('th', { class: 'num' }, '新增点赞'),
          h('th', { class: 'num' }, '净增粉丝'),
          h('th', { class: 'num' }, '新增分享'),
          h('th', { class: 'num' }, '新增评论'),
          h('th', { class: 'num' }, '新增作品'));
        var body = h('tbody');
        report.trend.forEach(function (row) {
          body.appendChild(h('tr', null,
            h('td', null, row.date),
            h('td', { class: 'num' }, row.like),
            h('td', { class: 'num' }, row.fans),
            h('td', { class: 'num' }, row.share),
            h('td', { class: 'num' }, row.comment),
            h('td', { class: 'num' }, row.item)));
        });
        tabHost.appendChild(h('div', { class: 'row-title', style: 'margin-bottom:8px' },
          '每日趋势 · 共 ' + report.trend.length + ' 天'));
        tabHost.appendChild(tableOf(head, body));
      }

      function renderVideos() {
        if (!(report.videos || []).length) {
          tabHost.appendChild(empty('暂无作品数据。'));
          return;
        }
        var head = h('tr', null, h('th', null, '发布日期'), h('th', null, '内容'),
          h('th', { class: 'num' }, '点赞量'), h('th', { class: 'num' }, '评论量'),
          h('th', { class: 'num' }, '分享量'), h('th', { class: 'num' }, '收藏量'),
          h('th', { class: 'num' }, '视频指数'));
        var body = h('tbody');
        report.videos.forEach(function (row) {
          body.appendChild(h('tr', null,
            h('td', null, row.date),
            h('td', { class: 'ellipsis' }, row.url
              ? h('a', { href: row.url, target: '_blank',
                rel: 'noopener noreferrer' }, row.text || '-')
              : (row.text || '-')),
            h('td', { class: 'num' }, row.like),
            h('td', { class: 'num' }, row.comment),
            h('td', { class: 'num' }, row.share),
            h('td', { class: 'num' }, row.favorite),
            h('td', { class: 'num' }, row.index)));
        });
        tabHost.appendChild(h('div', { class: 'row-title', style: 'margin-bottom:8px' },
          '达人视频 · 共 ' + report.videos.length + ' 条'));
        tabHost.appendChild(tableOf(head, body));
      }

      function renderFans() {
        if (!(report.fans || []).length) {
          tabHost.appendChild(empty('暂无粉丝画像。'));
          return;
        }
        var head = h('tr', null, h('th', null, '维度'), h('th', null, '项目'),
          h('th', { class: 'num' }, '占比'), h('th', { class: 'num' }, 'TGI'));
        var body = h('tbody');
        report.fans.forEach(function (row) {
          body.appendChild(h('tr', null,
            h('td', null, row.dimension),
            h('td', null, row.name),
            h('td', { class: 'num' }, row.pct),
            h('td', { class: 'num' }, row.tgi)));
        });
        tabHost.appendChild(h('div', { class: 'row-title', style: 'margin-bottom:8px' },
          '粉丝画像 · 共 ' + report.fans.length + ' 项（TGI>100 表示偏好度高于大盘）'));
        tabHost.appendChild(tableOf(head, body));
      }

      function renderSimilar() {
        if (!(report.similar || []).length) {
          tabHost.appendChild(empty('暂无相似达人。'));
          return;
        }
        var head = h('tr', null, h('th', { class: 'num' }, '排名'),
          h('th', null, '达人'), h('th', null, '抖音号'),
          h('th', { class: 'num' }, '粉丝'), h('th', { class: 'num' }, '总获赞'),
          h('th', { class: 'num' }, '作品'), h('th', null, '标签'));
        var body = h('tbody');
        report.similar.forEach(function (row) {
          body.appendChild(h('tr', null,
            h('td', { class: 'num' }, row.rank == null ? '-' : row.rank),
            h('td', null, row.name || '-'),
            h('td', null, row.uid || '-'),
            h('td', { class: 'num' }, row.fans),
            h('td', { class: 'num' }, row.likes),
            h('td', { class: 'num' }, row.items == null ? '-' : row.items),
            h('td', null, row.tags || '-')));
        });
        tabHost.appendChild(h('div', { class: 'row-title', style: 'margin-bottom:8px' },
          '相似达人 · 共 ' + report.similar.length + ' 位'));
        tabHost.appendChild(tableOf(head, body));
      }

      loadReport();
    }).catch(function (error) {
      setError(root, error, function () { renderDouyinIndex(root); });
    });
  }

  function renderWeiboChaohua(root) {
    setLoading(root, '正在读取超话本地数据…');
    api('/api/weibo-chaohua').then(function (data) {
      root.replaceChildren();
      var topics = data.topics || [];
      if (!topics.length) {
        root.appendChild(empty(data.note || '本地暂无超话数据。'));
        return;
      }
      if (!topics.some(function (item) {
        return item.topic_id === state.chaohua.topic;
      })) {
        state.chaohua.topic = topics[0].topic_id;
      }
      var select = h('select', { class: 'select' });
      topics.forEach(function (item) {
        select.appendChild(h('option', {
          value: item.topic_id,
          selected: item.topic_id === state.chaohua.topic
        }, item.nick + '（' + item.topic_id + '）'));
      });
      select.value = state.chaohua.topic;
      select.addEventListener('change', function () {
        state.chaohua.topic = select.value;
        load();
      });
      var refreshBtn = h('button', {
        class: 'btn', type: 'button'
      }, '刷新');
      refreshBtn.addEventListener('click', function () {
        renderWeiboChaohua(root);
      });
      var summary = h('div', {
        class: 'muted small', style: 'margin:8px 0'
      });
      var contentHost = h('div');
      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '超话'), select,
          h('span', { class: 'muted small', style: 'margin-left:8px' },
            '解析与采集仍在桌面端'),
          h('span', { style: 'flex:1' }), refreshBtn),
        h('div', { class: 'muted small', style: 'margin-top:6px' },
          '输入超话链接或 ID 解析、频道采集与导出 Excel 仍在桌面端。')));
      root.appendChild(summary);
      root.appendChild(contentHost);

      function section(title, labels, rows, emptyText, buildCells, numeric) {
        var card = h('div', { class: 'card', style: 'margin-bottom:12px' });
        card.appendChild(h('div', {
          class: 'row-title', style: 'margin-bottom:8px'
        }, title));
        if (!rows.length) {
          card.appendChild(empty(emptyText));
          return card;
        }
        var head = h('tr');
        labels.forEach(function (label, index) {
          head.appendChild(h('th', {
            class: numeric.indexOf(index) >= 0 ? 'num' : ''
          }, label));
        });
        var body = h('tbody');
        rows.forEach(function (row) {
          var tr = h('tr');
          buildCells(row).forEach(function (cell, index) {
            if (cell && cell.nodeType) {
              tr.appendChild(h('td', {
                class: numeric.indexOf(index) >= 0 ? 'num' : ''
              }, cell));
            } else {
              tr.appendChild(h('td', {
                class: (numeric.indexOf(index) >= 0 ? 'num ' : '')
                  + (index === 2 ? 'ellipsis' : '')
              }, cell));
            }
          });
          body.appendChild(tr);
        });
        card.appendChild(h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' },
            h('thead', null, head), body)));
        return card;
      }

      function num(value) {
        return value == null ? '-' : fmtNumber(value);
      }

      function load() {
        setLoading(contentHost, '正在读取历史分析…');
        api('/api/weibo-chaohua/analysis?topic_id='
          + encodeURIComponent(state.chaohua.topic))
          .then(function (result) {
            var topic = result.topic || {};
            var lastRun = result.last_run || {};
            summary.textContent = topic.nick + '（' + topic.topic_id + '）'
              + ' · 累计 ' + fmtNumber(result.total_posts) + ' 帖'
              + ' · ' + fmtNumber(result.total_runs) + ' 次采集'
              + (result.first_seen ? ' · 首次 ' + result.first_seen : '')
              + (result.last_seen ? ' · 最近 ' + result.last_seen : '')
              + (lastRun.finished_at
                ? ' · 最近一次新增 ' + fmtNumber(lastRun.new_posts)
                  + ' / 状态 ' + (lastRun.status || '-') : '');
            contentHost.replaceChildren();
            contentHost.appendChild(section(
              '最近采集批次', ['完成时间', '频道', '抓取', '去重', '新增', '状态'],
              result.runs || [], '暂无采集批次。',
              function (row) {
                return [row.finished_at, row.channels, num(row.raw_total),
                  num(row.unique_total), num(row.new_posts), row.status || '-'];
              }, [2, 3, 4]));
            contentHost.appendChild(section(
              '超话指标快照', ['时间', '签到', '分类排名', '帖子数', '粉丝数'],
              result.snapshots || [], '暂无指标快照。',
              function (row) {
                return [row.captured_at, row.checkin, row.category,
                  row.post_count, row.fans];
              }, []));
            contentHost.appendChild(section(
              '每日新增', ['日期', '新增帖子'], result.daily_new || [],
              '暂无每日新增数据。',
              function (row) { return [row.day, num(row.count)]; }, [1]));
            contentHost.appendChild(section(
              '热门帖子（按点赞）', ['作者', '时间', '内容', '赞', '评论', '转发', '链接'],
              result.top_posts || [], '暂无帖子数据。',
              function (row) {
                return [row.author, row.dt, row.text, num(row.likes),
                  num(row.comments), num(row.reposts),
                  row.url ? h('a', { href: row.url, target: '_blank',
                    rel: 'noopener noreferrer' }, '打开') : '-'];
              }, [3, 4, 5]));
            contentHost.appendChild(section(
              '增长榜（多次采集的点赞增幅）',
              ['作者', '时间', '内容', '最少赞', '最多赞', '采集次数'],
              result.growth || [], '暂无多次采集的帖子。',
              function (row) {
                return [row.author, row.dt, row.text, num(row.min_likes),
                  num(row.max_likes), num(row.captures)];
              }, [3, 4, 5]));
          }).catch(function (error) {
            setError(contentHost, error, load);
          });
      }

      load();
    }).catch(function (error) {
      setError(root, error, function () { renderWeiboChaohua(root); });
    });
  }

  function renderRecord(host) {
    host.replaceChildren();
    setLoading(host, '正在读取录制状态…');
    Promise.all([
      api('/api/record/overview'),
      api('/api/record/bridge')
    ]).then(function (results) {
      var data = results[0];
      var bridge = results[1] || {};
      host.replaceChildren();
      var connected = !!bridge.connected;
      var platforms = (connected && bridge.platforms
        && bridge.platforms.length) ? bridge.platforms : (data.platforms || []);
      if (!platforms.length) {
        host.appendChild(empty(
          '尚未配置录制主播。请先在桌面端「直播录制」页添加主播。'));
        return;
      }
      var keys = platforms.map(function (item) { return item.key; });
      if (keys.indexOf(state.record.platform) < 0) {
        state.record.platform = keys[0];
      }

      var allAnchors = [];
      platforms.forEach(function (platform) {
        platform.anchors.forEach(function (anchor) {
          allAnchors.push(Object.assign({ platform_key: platform.key }, anchor));
        });
      });
      var infoBar = h('div', { class: 'record-info muted small' });
      var tableHost = h('div');
      var busy = false;
      var selected = state.record.selected;

      function currentPlatform() {
        return platforms.filter(function (item) {
          return item.key === state.record.platform;
        })[0] || { anchors: [], name: '' };
      }

      function visibleRows() {
        return currentPlatform().anchors.map(function (anchor) {
          return Object.assign({ platform_key: state.record.platform }, anchor);
        });
      }

      function updateInfo() {
        var rows = visibleRows();
        var monitoring = rows.filter(function (item) {
          return item.monitoring;
        }).length;
        var recording = rows.filter(function (item) {
          return item.recording;
        }).length;
        infoBar.textContent = '共 ' + rows.length + ' 个主播 · 监控 '
          + monitoring + ' · 录制中 ' + recording + ' · '
          + (connected ? '桌面控制通道已连接' : '只读：桌面程序未运行');
      }

      function control(item, action, text, after) {
        if (busy) return;
        if (!window.confirm('确认对「' + (item.name || item.room) + '」执行「'
          + text + '」？')) {
          return;
        }
        busy = true;
        post('/api/record/control', {
          platform: item.platform_key, room: item.room, action: action
        }).then(function () {
          toast('操作成功', 'success');
          busy = false;
          if (after) after();
          else renderRecord(host);
        }).catch(function (error) {
          busy = false;
          toast('操作失败：' + error.message, 'error');
        });
      }

      function targetsFor(action) {
        var rows = visibleRows();
        if (selected.size) {
          rows = rows.filter(function (item) {
            return selected.has(item.platform_key + '|' + item.room);
          });
        }
        return rows.filter(function (item) {
          if (action === 'start_monitor') return !item.monitoring;
          if (action === 'stop_monitor') return item.monitoring;
          return item.recording;
        });
      }

      function bulkAction(action, label) {
        if (!connected || busy) return;
        var targets = targetsFor(action);
        if (!targets.length) {
          toast('没有可' + label + '的主播', 'warning');
          return;
        }
        if (!window.confirm('将对 ' + targets.length + ' 个主播执行「' + label
          + '」（未勾选时作用于本平台全部主播），继续？')) {
          return;
        }
        busy = true;
        var index = 0;
        var ok = 0;
        var fail = 0;
        function next() {
          if (index >= targets.length) {
            busy = false;
            toast('完成：成功 ' + ok + '，失败 ' + fail,
              fail ? 'warning' : 'success');
            renderRecord(host);
            return;
          }
          var item = targets[index++];
          post('/api/record/control', {
            platform: item.platform_key, room: item.room, action: action
          }).then(function () { ok += 1; next(); })
            .catch(function () { fail += 1; next(); });
        }
        next();
      }

      var monitorBtn = h('button', {
        class: 'btn primary', type: 'button', disabled: !connected,
        onclick: function () { bulkAction('start_monitor', '开启监控'); }
      }, '开启监控录制');
      var stopRecordBtn = h('button', {
        class: 'btn', type: 'button', disabled: !connected,
        onclick: function () { bulkAction('stop_record', '停止录制'); }
      }, '一键停止录制');
      var stopMonitorBtn = h('button', {
        class: 'btn', type: 'button', disabled: !connected,
        onclick: function () { bulkAction('stop_monitor', '关闭监控'); }
      }, '关闭监控');
      var selectAllBtn = h('button', {
        class: 'btn ghost mini', type: 'button'
      }, '全选');
      var selectNoneBtn = h('button', {
        class: 'btn ghost mini', type: 'button'
      }, '取消');

      selectAllBtn.addEventListener('click', function () {
        visibleRows().forEach(function (item) {
          selected.add(item.platform_key + '|' + item.room);
        });
        draw();
      });
      selectNoneBtn.addEventListener('click', function () {
        selected.clear();
        draw();
      });

      var refreshBtn = h('button', {
        class: 'btn', type: 'button'
      }, '刷新状态');
      refreshBtn.addEventListener('click', function () {
        renderRecord(host);
      });

      function draw() {
        var rows = visibleRows();
        updateInfo();
        if (!rows.length) {
          tableHost.replaceChildren(empty(
            '该平台暂无主播。请先在桌面端「直播录制」添加。'));
          return;
        }
        var columns = [
          { key: 'name', label: '主播名称' },
          { key: 'monitoring', label: '监控状态' },
          { key: 'want_record', label: '是否录制' },
          { key: 'recording', label: '录制中' },
          { key: 'remark', label: '备注' }
        ];
        var sorted = sortRows(rows, columns, state.record);
        var pageKeys = sorted.map(function (item) {
          return item.platform_key + '|' + item.room;
        });
        var allChecked = pageKeys.every(function (key) {
          return selected.has(key);
        });
        var headBox = h('input', { type: 'checkbox', checked: allChecked });
        headBox.addEventListener('change', function () {
          pageKeys.forEach(function (key) {
            if (headBox.checked) selected.add(key);
            else selected.delete(key);
          });
          draw();
        });
        var head = h('tr', null,
          h('th', { style: 'width:36px' }, headBox),
          h('th', { style: 'width:48px' }, '序号'));
        columns.forEach(function (column) {
          var mark = state.record.order === column.key
            ? (state.record.asc ? ' ↑' : ' ↓') : '';
          head.appendChild(h('th', {
            class: 'sortable',
            onclick: function () {
              cycleSort(state.record, column.key);
              draw();
            }
          }, column.label + mark));
        });
        head.appendChild(h('th', null, '操作'));
        var body = h('tbody');
        sorted.forEach(function (item, index) {
          var key = item.platform_key + '|' + item.room;
          var box = h('input', {
            type: 'checkbox', checked: selected.has(key)
          });
          box.addEventListener('change', function () {
            if (box.checked) selected.add(key);
            else selected.delete(key);
          });
          var actionCell = h('td', { style: 'white-space:nowrap' });
          if (connected) {
            actionCell.appendChild(h('button', {
              class: 'btn mini', type: 'button', style: 'margin-right:4px',
              onclick: function () {
                control(item, item.recording ? 'stop_record' : 'start_record',
                  item.recording ? '停止录制' : '开始录制');
              }
            }, item.recording ? '停止录制' : '开始录制'));
            actionCell.appendChild(h('button', {
              class: 'btn mini', type: 'button',
              onclick: function () {
                control(item, item.monitoring ? 'stop_monitor' : 'start_monitor',
                  item.monitoring ? '关闭监控' : '开启监控');
              }
            }, item.monitoring ? '关闭监控' : '开启监控'));
          } else {
            actionCell.appendChild(h('span', { class: 'muted small' }, '只读'));
          }
          body.appendChild(h('tr', null,
            h('td', null, box),
            h('td', { class: 'num' }, index + 1),
            h('td', { title: item.title || '' }, item.name || '-'),
            h('td', null, h('span', {
              class: 'badge ' + (item.monitoring ? 'ok' : '')
            }, item.monitoring ? '监控中' : '未监控')),
            h('td', null, item.want_record ? '是' : '否'),
            h('td', null, item.recording
              ? h('span', { class: 'badge ok' }, '录制中')
              : h('span', { class: 'muted small' }, '否')),
            h('td', { class: 'ellipsis' }, item.remark || '-'),
            actionCell));
        });
        tableHost.replaceChildren(h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' }, h('thead', null, head), body)));
      }

      var platBar = h('div', { class: 'record-platforms' });
      platforms.forEach(function (platform) {
        platBar.appendChild(h('button', {
          class: 'btn' + (platform.key === state.record.platform ? ' active' : ''),
          type: 'button',
          onclick: function () {
            state.record.platform = platform.key;
            selected.clear();
            renderRecord(host);
          }
        }, platform.name + '（' + platform.anchors.length + '）'));
      });
      platBar.appendChild(h('span', { class: 'muted small' },
        '各平台的列表与录制任务互相独立，切换不会中断录制'));

      host.appendChild(h('div', { class: 'toolbar' },
        monitorBtn, stopRecordBtn, stopMonitorBtn,
        h('span', { class: 'spacer' }), selectAllBtn, selectNoneBtn));
      host.appendChild(h('div', { class: 'toolbar' },
        h('span', { class: 'muted small' },
          '未勾选时批量操作作用于本平台全部主播；添加 / 批量修改主播、录制设置、推送设置、FLV 修复、写时间码与弹幕姬在桌面端。'),
        h('span', { style: 'flex:1' }), refreshBtn));
      host.appendChild(infoBar);
      host.appendChild(tableHost);
      host.appendChild(h('div', { class: 'card', style: 'margin-top:12px' },
        h('div', { class: 'card-title' }, '运行日志'),
        h('div', { class: 'muted small' },
          '运行日志由桌面端录制页实时输出；Web 端暂不提供，可在桌面端查看。')));
      host.appendChild(platBar);
      draw();
    }).catch(function (error) {
      setError(host, error, function () { renderRecord(host); });
    });
  }
  function kindLabel(kind) {
    var labels = { video: '视频', note: '图文', image: '图文', dynamic: '动态' };
    return labels[kind] || (kind || '-');
  }

  function platformLabel(platform) {
    var labels = { douyin: '抖音', bilibili: 'B站', weibo: '微博',
      kuaishou: '快手', xiaohongshu: '小红书', acfun: 'A站',
      tieba: '贴吧', zhihu: '知乎' };
    return labels[platform] || (platform || '-');
  }

  function dbColumnLabel(name) {
    var labels = {
      dedup_key: '去重键', fetched_at: '获取时间', kind: '类型', video_id: '作品 ID',
      title: '标题', author: '作者', publish_time: '发布时间', play_count: '播放',
      like_count: '点赞', comment_count: '评论', share_count: '分享',
      favorite_count: '收藏', danmaku_count: '弹幕', coin_count: '投币',
      duration: '时长', description: '正文', cover_url: '封面',
      video_url: '链接', tags: '标签', category: '分类', music: '音乐'
    };
    return labels[name] || name;
  }

  function renderLocal(host, summary) {
    host.replaceChildren();
    var listHost = h('div');
    var pagerHost = h('div', { class: 'pager' });
    var jobHost = h('div', { class: 'hidden' });
    var select = h('select', { class: 'select' });
    summary.categories.forEach(function (name) {
      select.appendChild(h('option', { value: name }, name));
    });
    if (!summary.categories.length) {
      host.appendChild(empty('暂无可浏览的本地分类目录。先在桌面端导出资源，或检查导出目录。'));
      return;
    }
    if (!state.assets.cat || summary.categories.indexOf(state.assets.cat) < 0) {
      state.assets.cat = summary.categories.indexOf('已获取数据') >= 0
        ? '已获取数据' : summary.categories[0];
      state.assets.localPage = 1;
    }
    select.value = state.assets.cat;
    var rescan = h('button', { class: 'btn', type: 'button' }, '重新扫描');
    var scanInfo = h('span', { class: 'muted small' });
    var scanning = false;
    var tracking = '';
    var autoTried = false;
    var cancelledOnce = false;

    function setScanning(value) {
      scanning = value;
      rescan.disabled = value;
      rescan.textContent = value ? '扫描中…' : '重新扫描';
    }

    function renderInfo(data) {
      if (!data.scanned_at) { scanInfo.textContent = ''; return; }
      var time = new Date(data.scanned_at * 1000);
      scanInfo.textContent = '扫描于 ' + time.toLocaleTimeString('zh-CN', {
        hour: '2-digit', minute: '2-digit'
      }) + ' · ' + fmtNumber(data.files) + ' 个文件';
    }

    function renderGroups(data) {
      state.assets.localPage = data.page;
      renderInfo(data);
      if (!data.groups.length) {
        listHost.replaceChildren(empty('该分类下暂无文件。可切换其他分类，或先在桌面端导出资源。'));
        pagerHost.replaceChildren();
        return;
      }
      var list = h('div', { class: 'list' });
      data.groups.forEach(function (group) {
        var files = h('ul', { class: 'row-files' });
        (group.files || []).slice(0, 12).forEach(function (file) {
          var previewable = /\.(png|jpe?g|gif|webp|bmp|mp4|flv|mkv|ts|mov|webm)$/i.test(file.name || '');
          var node;
          if (previewable) {
            var link = h('a', {
              href: '#', title: '点击预览' + (/^(mp4|flv|mkv|ts|mov|webm)$/i.test((file.name.split('.').pop() || '')) ? '（视频首帧）' : '')
            }, file.name + (file.size ? ' · ' + fmtBytes(file.size) : ''));
            link.addEventListener('click', function (event) {
              event.preventDefault();
              openPreview(data.category, file);
            });
            node = h('li', null, link);
          } else {
            node = h('li', null, file.name + (file.size ? ' · ' + fmtBytes(file.size) : ''));
          }
          files.appendChild(node);
        });
        if ((group.files || []).length > 12) {
          files.appendChild(h('li', null, '…共 ' + group.files.length + ' 个文件'));
        }
        var card = h('div', { class: 'row-card' });
        if (group.cover && group.cover.name) {
          var thumb = h('img', {
            class: 'row-thumb', loading: 'lazy', alt: '',
            title: '点击预览'
          });
          thumb.src = withToken('/api/assets/local/thumb?cat='
            + encodeURIComponent(data.category)
            + '&dir=' + encodeURIComponent(group.cover.dir || '')
            + '&name=' + encodeURIComponent(group.cover.name));
          thumb.addEventListener('click', function () {
            openPreview(data.category, {
              name: group.cover.name, rel_dir: group.cover.dir || ''
            });
          });
          card.appendChild(h('div', { class: 'row-thumb-wrap' }, thumb));
        }
        var main = h('div', { class: 'row-main' },
          h('div', { class: 'row-head' }, h('div', { class: 'row-title' }, group.title || '-')),
          h('div', { class: 'row-meta' },
            h('span', null, group.date || '无日期'),
            h('span', null, '图片 ' + group.n_img),
            h('span', null, '视频 ' + group.n_vid),
            h('span', null, '音频 ' + group.n_aud),
            h('span', null, '文档 ' + group.n_doc),
            h('span', null, fmtBytes(group.size)),
            h('span', null, group.mtime || '')),
          files);
        card.appendChild(main);
        list.appendChild(card);
      });
      listHost.replaceChildren();
      if (data.truncated) {
        listHost.appendChild(h('div', { class: 'muted small', style: 'margin:0 2px 8px' },
          '目录文件超过 50000 个，当前仅展示已扫描部分。'));
      }
      listHost.appendChild(list);
      pager(pagerHost, data.page, data.pages, data.total, function (page) {
        state.assets.localPage = page;
        load();
      });
    }

    function trackScan(jobId) {
      if (!jobId || tracking === jobId) return;
      tracking = jobId;
      setScanning(true);
      jobHost.classList.remove('hidden');
      watchJob(jobHost, jobId, function (job) {
        tracking = '';
        setScanning(false);
        jobHost.classList.add('hidden');
        toast(job.result && job.result.summary ? job.result.summary : '扫描完成', 'success');
        load();
      }, {
        cancelable: true,
        onSettled: function (job) {
          tracking = '';
          setScanning(false);
          if (job.status === 'cancelled') {
            cancelledOnce = true;
            jobHost.replaceChildren();
            jobHost.classList.add('hidden');
            toast('扫描已取消，保留上次结果。', 'info');
            load();
          }
        }
      });
    }

    function startScan(quiet) {
      if (scanning) return;
      setScanning(true);
      if (!quiet) listHost.replaceChildren(h('div', { class: 'loading', text: '正在启动后台扫描…' }));
      post('/api/assets/local/scan', { cat: state.assets.cat }).then(function (data) {
        trackScan(data.job);
      }).catch(function (error) {
        setScanning(false);
        setError(listHost, error, load);
      });
    }

    function load() {
      setLoading(listHost, '正在读取本地资源…');
      api('/api/assets/local/groups?cat=' + encodeURIComponent(state.assets.cat) +
        '&page=' + state.assets.localPage + '&page_size=40')
        .then(function (data) {
          if (data.status === 'idle') {
            renderInfo(data);
            if (!autoTried && !cancelledOnce) {
              autoTried = true;
              startScan();
            } else {
              listHost.replaceChildren(empty('尚未扫描该分类。点右上角「重新扫描」开始。'));
              pagerHost.replaceChildren();
            }
            return;
          }
          renderGroups(data);
          if (data.status === 'scanning' && data.job) trackScan(data.job);
          else if (data.stale && !scanning) startScan(true);
        }).catch(function (error) { setError(listHost, error, load); });
    }

    select.addEventListener('change', function () {
      state.assets.cat = select.value;
      state.assets.localPage = 1;
      tracking = '';
      cancelledOnce = false;
      setScanning(false);
      jobHost.replaceChildren();
      jobHost.classList.add('hidden');
      load();
    });
    rescan.addEventListener('click', function () {
      state.assets.localPage = 1;
      cancelledOnce = false;
      startScan();
    });

    host.appendChild(h('div', { class: 'toolbar' },
      h('span', { class: 'muted small' }, '分类'), select,
      h('span', { style: 'flex:1' }), scanInfo, rescan));
    host.appendChild(jobHost);
    host.appendChild(listHost);
    host.appendChild(pagerHost);
    load();

    function openPreview(category, file) {
      var overlay = h('div', { class: 'preview-overlay' });
      var box = h('div', { class: 'preview-box' });
      var caption = h('div', { class: 'preview-caption' },
        h('span', { class: 'ellipsis' }, file.name),
        h('button', { class: 'btn', type: 'button' }, '关闭'));
      var closeBtn = caption.lastChild;
      var body = h('div', { class: 'preview-body' }, '正在加载预览…');
      box.appendChild(caption);
      box.appendChild(body);
      overlay.appendChild(box);
      document.body.appendChild(overlay);

      function close() {
        overlay.remove();
      }
      closeBtn.addEventListener('click', close);
      overlay.addEventListener('click', function (event) {
        if (event.target === overlay) close();
      });
      document.addEventListener('keydown', function onKey(event) {
        if (event.key === 'Escape') {
          close();
          document.removeEventListener('keydown', onKey);
        }
      });

      var url = '/api/assets/local/preview?cat=' + encodeURIComponent(category)
        + '&dir=' + encodeURIComponent(file.rel_dir || '')
        + '&name=' + encodeURIComponent(file.name);
      var image = new Image();
      image.onload = function () {
        body.replaceChildren(image);
      };
      image.onerror = function () {
        api(url).catch(function (error) {
          body.replaceChildren(h('div', { class: 'muted' },
            '预览失败：' + error.message));
        });
        body.replaceChildren(h('div', { class: 'muted' }, '该文件无法预览。'));
      };
      image.src = withToken(url);
    }
  }

  function fmtBytes(value) {
    var size = Number(value || 0);
    if (size < 1024) return size + ' B';
    if (size < 1024 * 1024) return (size / 1024).toFixed(1) + ' KB';
    if (size < 1024 * 1024 * 1024) return (size / 1024 / 1024).toFixed(1) + ' MB';
    return (size / 1024 / 1024 / 1024).toFixed(2) + ' GB';
  }

  function renderOffline(host) {
    host.replaceChildren();
    setLoading(host, '正在读取线下活动清单…');
    api('/api/assets/offline/events').then(function (data) {
      host.replaceChildren();
      if (!data.available) {
        host.appendChild(empty(data.error || '本地快照不可用，请先在「腾讯文档」页同步表格。'));
        return;
      }
      if (!data.events.length) {
        host.appendChild(empty('线下活动表为空；请先在「腾讯文档」页同步表格。'));
        return;
      }
      var offline = state.assets.offline;
      var years = data.years || [];
      if (offline.year && years.indexOf(offline.year) < 0) offline.year = '';
      var yearSel = h('select', { class: 'select' });
      yearSel.appendChild(h('option', { value: '' }, '全部年份'));
      years.forEach(function (year) {
        yearSel.appendChild(h('option', { value: year }, year + ' 年'));
      });
      yearSel.value = offline.year;
      var biliBox = h('input', {
        type: 'checkbox', checked: offline.platforms.has('bilibili')
      });
      var douyinBox = h('input', {
        type: 'checkbox', checked: offline.platforms.has('douyin_archive')
      });
      var searchBtn = h('button', {
        class: 'btn primary', type: 'button', disabled: true
      }, '搜索现场视频');
      var selectionLabel = h('span', { class: 'muted small' }, '未选择活动');
      var toolbar = h('div', { class: 'toolbar' },
        h('span', { class: 'muted small' }, '年份'), yearSel,
        h('label', { class: 'muted small' }, biliBox, ' B站实时'),
        h('label', { class: 'muted small' }, douyinBox, ' 抖音归档'),
        h('span', { style: 'flex:1' }), selectionLabel, searchBtn);
      host.appendChild(toolbar);
      var jobHost = h('div');
      var tableHost = h('div');
      var resultHost = h('div');
      host.appendChild(jobHost);
      host.appendChild(tableHost);
      host.appendChild(resultHost);

      var running = false;

      function visibleRows() {
        var rows = data.events.filter(function (item) {
          return !offline.year || item.year === offline.year;
        });
        if (offline.order) {
          rows = sortRows(rows, [{ key: 'date' }, { key: 'name' }], offline);
        }
        return rows;
      }

      function selectedEvent() {
        if (!offline.selected) return null;
        return data.events.find(function (item) {
          return item.date + '|' + item.name === offline.selected;
        }) || null;
      }

      function updateButtons() {
        var event = selectedEvent();
        selectionLabel.textContent = event
          ? (event.date + ' · ' + event.name)
          : '未选择活动';
        searchBtn.disabled = running || !event || offline.platforms.size === 0;
        searchBtn.textContent = running ? '搜索中…' : '搜索现场视频';
      }

      function updatePlatforms() {
        offline.platforms.clear();
        if (biliBox.checked) offline.platforms.add('bilibili');
        if (douyinBox.checked) offline.platforms.add('douyin_archive');
        updateButtons();
      }
      biliBox.addEventListener('change', updatePlatforms);
      douyinBox.addEventListener('change', updatePlatforms);
      yearSel.addEventListener('change', function () {
        offline.year = yearSel.value;
        offline.selected = null;
        draw();
      });

      function draw() {
        var rows = visibleRows();
        var head = h('tr', null, h('th', { style: 'width:36px' }, ''));
        ['date', 'name'].forEach(function (key) {
          var label = key === 'date' ? '日期' : '活动';
          var mark = offline.order === key
            ? (offline.asc ? ' ↑' : ' ↓') : '';
          head.appendChild(h('th', {
            class: 'sortable',
            onclick: function () {
              cycleSort(offline, key);
              draw();
            }
          }, label + mark));
        });
        ['城市', '场馆', '状态', '已登记'].forEach(function (label) {
          head.appendChild(h('th', null, label));
        });
        var body = h('tbody');
        rows.forEach(function (item) {
          var key = item.date + '|' + item.name;
          var radio = h('input', {
            type: 'radio', name: 'offline-event',
            checked: offline.selected === key
          });
          radio.addEventListener('change', function () {
            offline.selected = key;
            updateButtons();
          });
          body.appendChild(h('tr', null,
            h('td', null, radio),
            h('td', null, item.date || '-'),
            h('td', null, item.name || '-'),
            h('td', null, item.city || '-'),
            h('td', null, item.venue || '-'),
            h('td', null, item.status || '-'),
            h('td', { class: 'num' }, fmtNumber(item.existing || 0))));
        });
        tableHost.replaceChildren(h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' }, h('thead', null, head), body)));
        updateButtons();
      }

      searchBtn.addEventListener('click', function () {
        var event = selectedEvent();
        if (!event || running) return;
        running = true;
        updateButtons();
        post('/api/assets/offline/search', {
          date: event.date, name: event.name,
          platforms: Array.from(offline.platforms)
        }).then(function (data) {
          watchJob(jobHost, data.job, function (job) {
            running = false;
            updateButtons();
            var result = job.result || {};
            var errors = result.errors || [];
            toast((result.summary || '搜索完成')
              + (errors.length ? '（部分请求失败）' : ''), 'success');
            showResult(result);
          }, {
            cancelable: true,
            onSettled: function (job) {
              running = false;
              updateButtons();
              if (job.status === 'cancelled') toast('搜索已取消', 'info');
            }
          });
        }).catch(function (error) {
          running = false;
          updateButtons();
          toast('搜索失败：' + error.message, 'error');
        });
      });

      function showResult(result) {
        var rows = result.results || [];
        var errors = result.errors || [];
        if (!rows.length) {
          resultHost.replaceChildren(h('div', {
            class: 'card', style: 'margin-top:12px'
          },
            h('div', { class: 'row-title' }, '搜索结果'),
            h('div', { class: 'muted small', style: 'margin-top:6px' },
              errors.length
                ? '没有找到结果；部分请求失败：' + errors.join('；')
                : '没有找到达到阈值的现场视频。')));
          return;
        }
        var head = h('tr', null,
          h('th', null, '标题'), h('th', null, '平台'),
          h('th', null, '作者'), h('th', null, '时长'),
          h('th', null, '发布时间'), h('th', { class: 'num' }, '评分'));
        var body = h('tbody');
        rows.forEach(function (item) {
          var title = item.url
            ? h('a', {
                href: item.url, target: '_blank', rel: 'noopener noreferrer'
              }, item.title || '-')
            : (item.title || '-');
          body.appendChild(h('tr', null,
            h('td', { class: 'ellipsis' }, title),
            h('td', null, item.platform || '-'),
            h('td', null, item.author || '-'),
            h('td', null, item.duration || '-'),
            h('td', null, item.publish_time || '-'),
            h('td', { class: 'num' }, item.score || 0)));
        });
        resultHost.replaceChildren(h('div', {
          class: 'card', style: 'margin-top:12px'
        },
          h('div', { class: 'row-title', style: 'margin-bottom:8px' },
            (result.summary || '搜索结果')
            + (errors.length ? '（部分请求失败）' : '')),
          h('div', { class: 'table-wrap' },
            h('table', { class: 'db-table' },
              h('thead', null, head), body))));
      }

      draw();
    }).catch(function (error) {
      setError(host, error, function () { renderOffline(host); });
    });
  }

  function renderTcdoc(root) {
    setLoading(root, '正在读取腾讯文档概览…');
    api('/api/tcdoc/overview?doc_key=' + encodeURIComponent(state.tcdoc.docKey))
      .then(function (data) {
        state.tcdoc.docKey = data.selected;
        if (!state.tcdoc.tab) state.tcdoc.tab = 'sync';
        var docSelect = h('select', { class: 'select' });
        data.docs.forEach(function (doc) {
          docSelect.appendChild(h('option', { value: doc.key }, doc.name));
        });
        docSelect.value = data.selected || '';
        docSelect.addEventListener('change', function () {
          state.tcdoc.docKey = docSelect.value;
          state.tcdoc.rowsTab = '';
          state.tcdoc.rowsPage = 1;
          renderTcdoc(root);
        });
        var current = (data.docs || []).filter(function (doc) {
          return doc.key === data.selected;
        })[0] || {};
        var jobHost = h('div');
        var latest = data.latest || {};
        var latestText = latest.ts
          ? '最近采集 ' + fmtTime(latest.ts)
            + ' · 浏览 ' + fmtNumber(latest.views)
            + ' · 成员 ' + fmtNumber(latest.members)
            + ' · 浏览人 ' + fmtNumber(latest.readers)
          : '暂无采集记录。';
        var collectBtn = h('button', {
          class: 'btn primary', type: 'button'
        }, '立即采集');
        var syncBtn = h('button', { class: 'btn', type: 'button' }, '表格同步');
        var openBtn = h('button', { class: 'btn', type: 'button' }, '打开链接');
        openBtn.disabled = !current.url;
        openBtn.addEventListener('click', function () {
          if (current.url) window.open(current.url, '_blank', 'noopener');
        });

        function start(url, label) {
          collectBtn.disabled = true;
          syncBtn.disabled = true;
          post(url, {}).then(function (job) {
            watchJob(jobHost, job.job, function () {
              collectBtn.disabled = false;
              syncBtn.disabled = false;
              toast(label + '完成', 'success');
              renderTcdoc(root);
            }, {
              cancelable: true,
              onSettled: function (job2) {
                collectBtn.disabled = false;
                syncBtn.disabled = false;
                if (job2.status === 'cancelled') toast(label + '已取消', 'info');
              }
            });
          }).catch(function (error) {
            collectBtn.disabled = false;
            syncBtn.disabled = false;
            toast(error.message, 'error');
          });
        }
        collectBtn.addEventListener('click', function () {
          start('/api/tcdoc/collect', '采集');
        });
        syncBtn.addEventListener('click', function () {
          start('/api/tcdoc/sync', '表格同步');
        });

        var tabs = h('div', { class: 'tabs' });
        var tabHost = h('div');
        var TABS = [
          ['bridge', '中间层同步'], ['sync', '表格同步'],
          ['daily', '历史记录'], ['snapshot', '采集明细'],
          ['revision', '修订记录'], ['members', '成员名单'],
          ['docs', '文档管理'], ['writeback', '映射回写']
        ];
        TABS.forEach(function (item) {
          tabs.appendChild(h('button', {
            class: 'tab' + (state.tcdoc.tab === item[0] ? ' active' : ''),
            type: 'button',
            onclick: function () {
              state.tcdoc.tab = item[0];
              tabs.querySelectorAll('.tab').forEach(function (button, index) {
                button.classList.toggle('active', TABS[index][0] === item[0]);
              });
              renderTab();
            }
          }, item[1]));
        });

        root.replaceChildren(
          h('div', { class: 'card', style: 'margin-bottom:12px' },
            h('div', { class: 'toolbar' },
              h('span', { class: 'muted small' }, '文档'), docSelect,
              openBtn, collectBtn, syncBtn,
              h('span', { class: 'spacer' }),
              data.auto ? h('span', { class: 'badge ok' }, '自动维护已开启')
                : h('span', { class: 'badge' }, '自动维护已关闭')),
            h('div', { class: 'muted small', style: 'margin-top:6px' },
              latestText),
            h('div', { class: 'muted small', style: 'margin-top:4px' },
              '定时采集已统一到「任务」页面，可在那里修改计划并安装 / 删除计划任务。')),
          tabs,
          tabHost,
          jobHost);
        renderTab();

        function renderTab() {
          tabHost.replaceChildren();
          if (state.tcdoc.tab === 'sync') renderSyncTab();
          else if (state.tcdoc.tab === 'daily') renderDailyTab();
          else if (state.tcdoc.tab === 'snapshot') renderSnapshotTab();
          else if (state.tcdoc.tab === 'revision') renderRevisionTab();
          else if (state.tcdoc.tab === 'members') renderMembersTab();
          else if (state.tcdoc.tab === 'docs') renderDocsTab();
          else if (state.tcdoc.tab === 'writeback') renderWritebackTab();
          else {
            tabHost.appendChild(empty(
              '中间层同步（选择目标表格、时间范围与账号后写入）仅在桌面端提供。'));
          }
        }

        function renderSyncTab() {
          var sheets = data.tables.sheets || [];
          var sheetSelect = h('select', { class: 'select' });
          if (!sheets.length) {
            sheetSelect.appendChild(h('option', { value: '' }, '（暂无子表）'));
            sheetSelect.disabled = true;
          }
          sheets.forEach(function (sheet) {
            sheetSelect.appendChild(h('option', {
              value: sheet.tab_id,
              selected: state.tcdoc.rowsTab === sheet.tab_id
            }, sheet.tab_name || sheet.tab_id));
          });
          if (!state.tcdoc.rowsTab && sheets.length) {
            state.tcdoc.rowsTab = sheets[0].tab_id;
            sheetSelect.value = sheets[0].tab_id;
          }
          var rowsHost = h('div');
          var pagerHost = h('div', { class: 'col-pager' });
          var syncNow = h('button', {
            class: 'btn', type: 'button'
          }, '同步表格');
          syncNow.addEventListener('click', function () {
            start('/api/tcdoc/sync', '表格同步');
          });
          tabHost.appendChild(h('div', { class: 'toolbar' },
            h('span', { class: 'muted small' }, '工作表'), sheetSelect,
            h('span', { style: 'flex:1' }), syncNow));
          tabHost.appendChild(rowsHost);
          tabHost.appendChild(pagerHost);
          sheetSelect.addEventListener('change', function () {
            state.tcdoc.rowsTab = sheetSelect.value;
            state.tcdoc.rowsPage = 1;
            loadRows();
          });

          function loadRows() {
            var tabId = state.tcdoc.rowsTab;
            if (!tabId) {
              rowsHost.replaceChildren(empty(
                '暂无表格同步数据。点「同步表格」抓取当前文档。'));
              pagerHost.replaceChildren();
              return;
            }
            setLoading(rowsHost, '正在读取表格行…');
            api('/api/tcdoc/table-rows?doc_key='
              + encodeURIComponent(state.tcdoc.docKey)
              + '&tab_id=' + encodeURIComponent(tabId)
              + '&page=' + state.tcdoc.rowsPage + '&page_size=50')
              .then(function (rows) {
                var sheet = sheets.filter(function (item) {
                  return item.tab_id === tabId;
                })[0] || {};
                var columns = (sheet.columns || []).map(function (column) {
                  return typeof column === 'string' ? column
                    : (column && column.name) || '';
                }).filter(Boolean);
                if (!columns.length && rows.rows.length
                    && rows.rows[0].values) {
                  columns = Object.keys(rows.rows[0].values);
                }
                var head = h('tr', null, h('th', null, '行号'));
                columns.forEach(function (column) {
                  head.appendChild(h('th', null, column));
                });
                var body = h('tbody');
                rows.rows.forEach(function (row) {
                  var tr = h('tr', null,
                    h('td', { class: 'num' }, row.row_no));
                  columns.forEach(function (column) {
                    var value = row.values ? row.values[column] : '';
                    if (value === null || value === undefined) value = '';
                    tr.appendChild(h('td', { class: 'ellipsis' },
                      String(value)));
                  });
                  body.appendChild(tr);
                });
                rowsHost.replaceChildren(
                  h('div', { class: 'muted small', style: 'margin-bottom:8px' },
                    (sheet.tab_name || tabId) + ' · ' + rows.total + ' 行'),
                  h('div', { class: 'table-wrap' },
                    h('table', null, h('thead', null, head), body)));
                pagerHost.replaceChildren();
                if (rows.pages > 1) {
                  pagerHost.appendChild(h('button', {
                    class: 'btn ghost mini', type: 'button',
                    disabled: rows.page <= 1,
                    onclick: function () {
                      state.tcdoc.rowsPage = rows.page - 1;
                      loadRows();
                    }
                  }, '上一页'));
                  pagerHost.appendChild(h('span', { class: 'muted small' },
                    rows.page + ' / ' + rows.pages));
                  pagerHost.appendChild(h('button', {
                    class: 'btn ghost mini', type: 'button',
                    disabled: rows.page >= rows.pages,
                    onclick: function () {
                      state.tcdoc.rowsPage = rows.page + 1;
                      loadRows();
                    }
                  }, '下一页'));
                }
              }).catch(function (error) {
                setError(rowsHost, error, loadRows);
              });
          }
          loadRows();
        }

        function renderDailyTab() {
          var daily = (data.daily || []).slice().reverse();
          if (!daily.length) {
            tabHost.appendChild(empty(
              '暂无采集历史。点「立即采集」开始第一次采集。'));
            return;
          }
          var body = h('tbody');
          daily.forEach(function (row) {
            body.appendChild(h('tr', null,
              h('td', null, fmtTime(row.ts)),
              h('td', { class: 'num' }, fmtNumber(row.views)),
              h('td', { class: 'num' }, row.views_delta == null
                ? '-' : fmtNumber(row.views_delta)),
              h('td', { class: 'num' }, fmtNumber(row.members)),
              h('td', { class: 'num' }, row.members_delta == null
                ? '-' : fmtNumber(row.members_delta)),
              h('td', { class: 'num' }, fmtNumber(row.readers))));
          });
          tabHost.appendChild(h('div', { class: 'table-wrap' },
            h('table', null, h('thead', null, h('tr', null,
              h('th', null, '采集时间'), h('th', { class: 'num' }, '浏览量'),
              h('th', { class: 'num' }, '浏览增量'),
              h('th', { class: 'num' }, '成员数'),
              h('th', { class: 'num' }, '成员增量'),
              h('th', { class: 'num' }, '已查看人数'))), body)));
        }

        function renderSnapshotTab() {
          setLoading(tabHost, '正在读取采集明细…');
          api('/api/tcdoc/snapshots?doc_key='
            + encodeURIComponent(state.tcdoc.docKey) + '&limit=2000')
            .then(function (result) {
              var rows = (result.rows || []).slice().reverse();
              if (!rows.length) {
                tabHost.replaceChildren(empty('暂无采集明细。'));
                return;
              }
              var body = h('tbody');
              rows.forEach(function (row) {
                body.appendChild(h('tr', null,
                  h('td', null, fmtTime(row.ts)),
                  h('td', null, h('span', {
                    class: 'badge ' + (row.ok ? 'ok' : 'warn')
                  }, row.ok ? '成功' : '失败')),
                  h('td', { class: 'num' }, row.views == null
                    ? '-' : fmtNumber(row.views)),
                  h('td', { class: 'num' }, row.members == null
                    ? '-' : fmtNumber(row.members)),
                  h('td', { class: 'ellipsis' }, row.message || '')));
              });
              tabHost.replaceChildren(
                h('div', { class: 'muted small', style: 'margin-bottom:8px' },
                  '共 ' + fmtNumber(result.total) + ' 条（最新在前）'),
                h('div', { class: 'table-wrap' },
                  h('table', null, h('thead', null, h('tr', null,
                    h('th', null, '采集时间'), h('th', null, '状态'),
                    h('th', { class: 'num' }, '浏览量'),
                    h('th', { class: 'num' }, '成员数'),
                    h('th', null, '说明'))), body)));
            }).catch(function (error) {
              setError(tabHost, error, renderSnapshotTab);
            });
        }

        function renderRevisionTab() {
          setLoading(tabHost, '正在读取修订记录…');
          api('/api/tcdoc/revisions?doc_key='
            + encodeURIComponent(state.tcdoc.docKey) + '&limit=500')
            .then(function (result) {
              var meta = result.meta || {};
              var info = '共 ' + fmtNumber(result.total) + ' 条'
                + (meta.fetched_at ? ' · 拉取于 ' + fmtTime(meta.fetched_at) : '')
                + (meta.complete ? '' : ' · 未完整拉取');
              if (!(result.rows || []).length) {
                tabHost.replaceChildren(
                  h('div', { class: 'muted small', style: 'margin-bottom:8px' },
                    info),
                  empty('暂无本地修订记录。桌面端「拉取修订记录」后可在此查看。'));
                return;
              }
              var body = h('tbody');
              result.rows.forEach(function (row) {
                body.appendChild(h('tr', null,
                  h('td', null, row.ts || '-'),
                  h('td', null, row.version || '-'),
                  h('td', null, row.author || '-'),
                  h('td', { class: 'num' }, row.rev == null ? '-' : row.rev),
                  h('td', null, row.type || '-')));
              });
              tabHost.replaceChildren(
                h('div', { class: 'muted small', style: 'margin-bottom:8px' },
                  info),
                h('div', { class: 'table-wrap' },
                  h('table', null, h('thead', null, h('tr', null,
                    h('th', null, '时间'), h('th', null, '版本'),
                    h('th', null, '编辑人'), h('th', { class: 'num' }, '修订号'),
                    h('th', null, '类型'))), body)));
            }).catch(function (error) {
              setError(tabHost, error, renderRevisionTab);
            });
        }

        function renderMembersTab() {
          setLoading(tabHost, '正在读取成员名单…');
          api('/api/tcdoc/members?doc_key='
            + encodeURIComponent(state.tcdoc.docKey) + '&limit=2000')
            .then(function (result) {
              var meta = result.meta || {};
              var info = '共 ' + fmtNumber(result.total) + ' 人'
                + (meta.fetched_at ? ' · 拉取于 ' + fmtTime(meta.fetched_at) : '')
                + (meta.unique != null ? ' · 累计 ' + fmtNumber(meta.unique) + ' 人'
                  : '');
              if (!(result.users || []).length) {
                tabHost.replaceChildren(
                  h('div', { class: 'muted small', style: 'margin-bottom:8px' },
                    info),
                  empty('暂无本地成员名单。桌面端「拉取全部成员」后可在此查看。'));
                return;
              }
              var body = h('tbody');
              result.users.forEach(function (user) {
                body.appendChild(h('tr', null,
                  h('td', null, user.name || '-'),
                  h('td', null, user.role || '-'),
                  h('td', null, user.source || '-'),
                  h('td', { class: 'ellipsis' }, user.uid || '-'),
                  h('td', null, user.corp_name || '-')));
              });
              tabHost.replaceChildren(
                h('div', { class: 'muted small', style: 'margin-bottom:8px' },
                  info),
                h('div', { class: 'table-wrap' },
                  h('table', null, h('thead', null, h('tr', null,
                    h('th', null, '昵称'), h('th', null, '权限等级'),
                    h('th', null, '账号来源'), h('th', null, 'UID'),
                    h('th', null, '企业'))), body)));
            }).catch(function (error) {
              setError(tabHost, error, renderMembersTab);
            });
        }

        function renderDocsTab() {
          var docs = data.docs || [];
          var body = h('tbody');
          docs.forEach(function (doc) {
            body.appendChild(h('tr', null,
              h('td', null, doc.name || '-'),
              h('td', null, doc.kind_label || doc.kind || '-'),
              h('td', { class: 'num' }, fmtNumber(doc.records)),
              h('td', null, doc.last ? fmtTime(doc.last) : '-'),
              h('td', { class: 'ellipsis' }, doc.url
                ? h('a', {
                  href: doc.url, target: '_blank',
                  rel: 'noopener noreferrer'
                }, doc.url)
                : '-')));
          });
          tabHost.appendChild(h('div', { class: 'table-wrap' },
            h('table', null, h('thead', null, h('tr', null,
              h('th', null, '名称'), h('th', null, '类型'),
              h('th', { class: 'num' }, '记录数'),
              h('th', null, '最近采集'), h('th', null, '链接'))), body)));
          tabHost.appendChild(h('div', {
            class: 'muted small', style: 'margin-top:8px'
          }, '添加 / 编辑 / 删除文档、生成诊断包与重建报表仅在桌面端提供。'));
        }

        function renderWritebackTab() {
          var writeHost = h('div');
          var pushHost = h('div', { style: 'margin-top:12px' });
          tabHost.appendChild(writeHost);
          tabHost.appendChild(pushHost);
          renderTcdocWriteback(writeHost);
          renderTcdocPushHistory(pushHost, data.push_history || []);
        }
      }).catch(function (error) {
        setError(root, error, function () { renderTcdoc(root); });
      });
  }

  function renderTcdocPushHistory(host, entries) {
    if (!entries.length) {
      host.appendChild(empty('暂无推送记录。'));
      return;
    }
    var body = h('tbody');
    entries.forEach(function (entry) {
      body.appendChild(h('tr', null,
        h('td', null, fmtTime(entry.time)),
        h('td', null, entry.tab_name || entry.tab_id || '-'),
        h('td', null, entry.source || '-'),
        h('td', null, h('span', {
          class: 'badge ' + (entry.status === '成功' ? 'ok' : 'warn')
        }, entry.status || '-')),
        h('td', { class: 'num' }, fmtNumber(entry.added)),
        h('td', { class: 'num' }, fmtNumber(entry.images)),
        h('td', { class: 'num' }, fmtNumber(entry.failed))));
    });
    host.appendChild(h('div', { class: 'card-title' }, '推送历史'));
    host.appendChild(h('div', { class: 'table-wrap' },
      h('table', null, h('thead', null, h('tr', null,
        h('th', null, '时间'), h('th', null, '子表'), h('th', null, '来源'),
        h('th', null, '状态'), h('th', { class: 'num' }, '新增'),
        h('th', { class: 'num' }, '图片'),
        h('th', { class: 'num' }, '失败'))), body)));
  }

  function renderTcdocWriteback(host) {
    host.replaceChildren(
      h('div', { class: 'card-title' }, '写回（白名单两表 · 单条新增）'),
      h('div', { class: 'loading' }, '正在读取写回状态…'));
    var writeJobHost = h('div');
    var writeResultHost = h('div');
    Promise.all([
      api('/api/tcdoc/writeback/status'),
      api('/api/tcdoc/writeback/preview?tab='
        + encodeURIComponent(state.tcdoc.writeTab)
        + '&days=' + state.tcdoc.writeDays + '&limit=100')
    ]).then(function (results) {
      var status = results[0] || {};
      var preview = results[1] || {};
      var refresh = preview.refresh || status.refresh || {};
      var allowed = !!refresh.allowed;
      var tabs = h('div', { class: 'tabs' });
      (status.targets || []).forEach(function (target) {
        tabs.appendChild(h('button', {
          class: 'tab' + (state.tcdoc.writeTab === target.key ? ' active' : ''),
          type: 'button',
          onclick: function () {
            state.tcdoc.writeTab = target.key;
            state.tcdoc.writeKey = '';
            renderTcdocWriteback(host);
          }
        }, target.name));
      });
      var daysSelect = h('select', { class: 'select' },
        [30, 90, 180, 365].map(function (value) {
          return h('option', {
            value: String(value),
            selected: value === state.tcdoc.writeDays
          }, value + ' 天');
        }));
      daysSelect.addEventListener('change', function () {
        state.tcdoc.writeDays = Number(daysSelect.value);
        state.tcdoc.writeKey = '';
        renderTcdocWriteback(host);
      });
      var rows = preview.rows || [];
      var known = new Set(rows.map(function (row) { return row.key; }));
      if (!known.has(state.tcdoc.writeKey)) state.tcdoc.writeKey = '';
      var body = h('tbody');
      rows.forEach(function (row) {
        var radio = h('input', {
          type: 'radio', name: 'tcdocWriteKey',
          checked: state.tcdoc.writeKey === row.key
        });
        radio.addEventListener('change', function () {
          state.tcdoc.writeKey = row.key;
          updateWriteButton();
        });
        var link = row.link
          ? h('a', { href: row.link, target: '_blank',
            rel: 'noopener noreferrer' }, '打开')
          : '-';
        body.appendChild(h('tr', null,
          h('td', null, radio),
          h('td', null, row.time || '-'),
          h('td', { class: 'ellipsis', title: row.title || '' },
            row.title || '-'),
          h('td', null, (row.account || '-')
            + (row.platform ? ' / ' + row.platform : '')),
          h('td', { class: 'muted small' }, link),
          h('td', { class: 'num' }, row.images || 0),
          h('td', { class: 'muted small' }, row.flag || '')));
      });
      var table = rows.length
        ? h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' },
            h('thead', null, h('tr', null,
              h('th', { style: 'width:36px' }, ''),
              h('th', null, '时间'), h('th', null, '标题'),
              h('th', null, '账号 / 平台'), h('th', null, '链接'),
              h('th', { class: 'num' }, '图片'),
              h('th', null, '标记'))), body))
        : empty('当前范围内没有待写记录。可切换表或调整时间范围后重新加载。');
      var summary = h('span', { class: 'muted small' },
        '待写 ' + fmtNumber(preview.total || 0) + ' 条'
        + (preview.truncated ? '（仅显示前 100 条）' : '')
        + (preview.stats && preview.stats.since
          ? ' · 自 ' + preview.stats.since : ''));
      var writeBtn = h('button', {
        class: 'btn primary', type: 'button'
      }, '写入选中 1 条');

      function updateWriteButton() {
        writeBtn.disabled = !state.tcdoc.writeKey || !allowed;
      }

      writeBtn.addEventListener('click', function () {
        var row = rows.filter(function (item) {
          return item.key === state.tcdoc.writeKey;
        })[0];
        if (!row) {
          toast('请先选择一条待写记录', 'warning');
          return;
        }
        if (!allowed) {
          toast('数据未刷新：请先在桌面端完成同步前刷新或开启免校对模式',
            'warning');
          return;
        }
        var lines = '将真实新增 1 条到「' + (preview.name || '') +
          '」（《' + (preview.doc_name || status.doc_name || '封茗囧菌数据汇总库') +
          '》）\n\n'
          + (row.title ? '标题：' + row.title + '\n' : '')
          + (row.time ? '时间：' + row.time + '\n' : '')
          + (row.account ? '账号：' + row.account + '\n' : '')
          + '\n只新增，不修改、不删除；写入后立即读回校验并记录推送历史。\n'
          + '确定写入这 1 条吗？';
        if (!window.confirm(lines)) return;
        writeBtn.disabled = true;
        post('/api/tcdoc/writeback/upload', {
          tab: state.tcdoc.writeTab,
          key: row.key,
          days: state.tcdoc.writeDays,
          confirmed: true
        }).then(function (data2) {
          watchJob(writeJobHost, data2.job, function (job) {
            writeBtn.disabled = false;
            var result = job.result || {};
            toast(result.summary || '写回完成',
              result.verified ? 'success' : 'info');
            var notes = [];
            if (result.summary) notes.push(result.summary);
            if (result.record_ids && result.record_ids.length) {
              notes.push('record_id：' + result.record_ids.join('、'));
            }
            if (result.error) notes.push('原因：' + result.error);
            if (result.note) notes.push(result.note);
            writeResultHost.replaceChildren(h('div', {
              class: 'muted small', style: 'margin-top:12px'
            }, notes.join('；') || '写回完成'));
            state.tcdoc.writeKey = '';
            renderTcdocWriteback(host);
          }, {
            onSettled: function (job) {
              writeBtn.disabled = false;
              if (job.status === 'cancelled') toast('写回已取消', 'info');
              if (job.status === 'error') {
                toast('写回失败：' + job.error, 'error');
              }
            }
          });
        }).catch(function (error) {
          writeBtn.disabled = false;
          toast('写回失败：' + error.message, 'error');
        });
      });

      updateWriteButton();
      host.replaceChildren(
        h('div', { class: 'card-title' }, '写回（白名单两表 · 单条新增）'),
        h('div', { class: 'muted small' }, status.note || ''),
        h('div', { class: 'muted small', style: 'margin-top:4px' },
          '目标固定为《' + (status.doc_name || '封茗囧菌数据汇总库')
          + '》，与上方文档选择无关。'),
        h('div', { class: 'toolbar', style: 'margin:8px 0' },
          h('span', { class: 'badge ' + (allowed ? 'ok' : 'warn') },
            allowed ? '数据已刷新' : '需要先刷新'),
          h('span', { class: 'muted small' }, refresh.message || ''),
          h('span', { style: 'flex:1' }),
          h('button', {
            class: 'btn', type: 'button',
            onclick: function () { renderTcdocWriteback(host); }
          }, '重新加载')),
        tabs,
        h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '时间范围'), daysSelect,
          summary, h('span', { style: 'flex:1' }), writeBtn),
        table,
        writeJobHost,
        writeResultHost);
    }).catch(function (error) {
      host.replaceChildren(
        h('div', { class: 'card-title' }, '写回（白名单两表 · 单条新增）'),
        h('div', { class: 'error' }, '写回状态加载失败：' + error.message,
          h('div', { class: 'small', style: 'margin-top:8px' },
            '请确认本地腾讯文档数据与登录状态后重试。'),
          h('button', {
            class: 'btn', type: 'button', style: 'margin-top:8px',
            onclick: function () { renderTcdocWriteback(host); }
          }, '重试')));
    });
  }

  function init() {
    document.querySelectorAll('.nav-item').forEach(function (button) {
      button.addEventListener('click', function () { navigate(button.dataset.route); });
    });
    document.getElementById('themeBtn').addEventListener('click', toggleTheme);
    document.getElementById('refreshBtn').addEventListener('click', function () {
      navigate(state.route);
    });
    document.getElementById('menuBtn').addEventListener('click', function () {
      sidebar.classList.toggle('open');
      scrim.classList.toggle('show', sidebar.classList.contains('open'));
    });
    scrim.addEventListener('click', function () {
      sidebar.classList.remove('open');
      scrim.classList.remove('show');
    });
    applyTheme();
    var hash = (location.hash || '').replace(/^#\/?/, '');
    navigate(routes[hash] ? hash : 'home');
    api('/api/health').then(function () {
      document.getElementById('serverDot').className = 'dot ok';
      document.getElementById('serverState').textContent = '服务已连接';
    }).catch(function () {
      document.getElementById('serverDot').className = 'dot bad';
      document.getElementById('serverState').textContent = '服务未连接';
    });
  }

  document.getElementById('gateSubmit').addEventListener('click', function () {
    var value = gateToken.value.trim();
    if (!value) { gateError.textContent = '请输入访问令牌'; return; }
    gateError.textContent = '';
    login(value, function () {
      gate.classList.add('hidden');
      init();
    }, function (message) {
      gateError.textContent = message;
    });
  });
  gateToken.addEventListener('keydown', function (event) {
    if (event.key === 'Enter') document.getElementById('gateSubmit').click();
  });

  function clearStaleSession() {
    localStorage.removeItem('webui-ticket');
    token = '';
    showGate('会话已过期或失效，请重新输入访问令牌。');
  }

  if (urlToken) {
    history.replaceState(null, '', location.pathname + (location.hash || ''));
    login(urlToken, function () {
      init();
    }, function (message) {
      showGate(message);
    });
  } else if (!token) {
    showGate('请从 config/webui_url.txt 打开完整地址，或粘贴令牌。');
  } else {
    init();
  }
})();
