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
    fangroups: { open: '', tables: {} },
    douyin: { name: '', tab: 'core' },
    chaohua: { topic: '' },
    image_sorter: { kind: 'all', q: '' },
    rename_batch: {
      path: '', recursive: false, filter: 'all', tab: 'rename',
      dirRename: false
    },
    shopping: { tab: 'taobao' },
    info_accounts: { platform: '', scope: 'all' },
    artifacts: { tab: 'comments' },
    audio: { order: '', asc: true, selected: new Set() },
    organize: { order: '', asc: true, selected: new Set() },
    rename: { order: '', asc: true },
    video: { tab: 'convert', order: '', asc: true, selected: new Set() },
    calendar: { day: '', order: '', asc: true },
    migrate: { order: '', asc: true, selected: new Set(), mode: 'copy' },
    subtitle: { order: '', asc: true, selected: new Set(), formats: new Set() },
    tasks: { order: '', asc: true },
    tcdoc: { docKey: '', tab: 'sync', rowsTab: '', rowsPage: 1, writeTab: 'official', writeDays: 30, writeKey: '',
      bridge: { docKey: '', tabId: '', days: 30, accounts: 'default', dupMode: 'skip' } }
  };
  var routes = {
    home: { title: '工作台', load: renderHome },
    info_accounts: { title: '采集任务', load: renderInfoAccounts },
    info_tools: { title: '采集工具', load: renderInfoTools },
    collected: { title: '采集结果', load: renderCollected },
    assets: { title: '资产库', load: renderAssets },
    netdisk: { title: '网络库', load: renderNetdisk },
    localwiki: { title: '资料站', load: renderLocalWiki },
    livestream_summary: { title: '直播总结', load: renderLivestreamSummary },
    image_sorter: { title: '媒体筛选', load: renderImageSorter },
    rename_batch: { title: '批量改名', load: renderRenameBatch },
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
    tickets: { title: '票务解析', load: renderTickets },
    shopping: { title: '购物解析', load: renderShoppingHub },
    music_playlist: { title: '歌单', load: renderMusicPlaylist },
    music_download: { title: '下载', load: renderMusicDownload },
    music_tag: { title: '标签', load: renderMusicTag },
    performance: { title: '性能诊断', load: renderPerformance },
    image_crop: { title: '图片裁切', load: function (host) {
      renderDesktopOnly(host, '图片裁切',
        '图片裁切是自绘画布（选区、旋转、缩放），Web 端不提供编辑，请在桌面端使用。');
    } },
    cover_maker: { title: '封面制作', load: function (host) {
      window.renderCoverAutomation(host, {
        api: api, post: post, watchJob: watchJob, toast: toast,
        cycleSort: cycleSort, sortRows: sortRows
      });
    } },
    certificate_maker: { title: '证书制作', load: function (host) {
      renderDesktopOnly(host, '证书制作',
        '证书制作使用桌面端自绘模板与图片合成，Web 端不提供编辑，请在桌面端使用。');
    } },
    proxy: { title: '网络代理', load: function (host) {
      renderDesktopOnly(host, '网络代理',
        '网络代理需要在本机启动代理内核进程，Web 端不提供启停，请在桌面端或系统代理中设置。');
    } },
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

  function downloadPost(path, body, fallbackName) {
    var headers = { 'Content-Type': 'application/json' };
    if (token) headers['X-WebUI-Token'] = token;
    return fetch(path, {
      method: 'POST', headers: headers, body: JSON.stringify(body || {})
    }).then(function (response) {
      if (!response.ok) {
        return response.text().then(function (text) {
          var data = null;
          try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
          throw new Error(data && data.error ? data.error : ('导出失败（' + response.status + '）'));
        });
      }
      return response.blob().then(function (blob) {
        var url = URL.createObjectURL(blob);
        var link = document.createElement('a');
        link.href = url;
        link.download = fallbackName || 'download';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
      });
    });
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
        var indeterminate = options.indeterminate && job.progress == null;
        bar.classList.toggle('is-indeterminate', !!indeterminate);
        bar.firstChild.style.width = indeterminate ? '35%'
          : (job.progress == null ? 30 : Math.round(job.progress * 100)) + '%';
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
        ['netdisk', '网络库', '网盘文件浏览与下载'],
        ['localwiki', '资料站', '部署状态与站点访问统计'],
        ['livestream_summary', '直播总结', '转录清单与已保存总结'],
        ['image_sorter', '媒体筛选', '源文件夹扫描与图片预览'],
        ['rename_batch', '批量改名', '规则预览与冲突检查'],
        ['tickets', '票务解析', '演出缓存与导出目录'],
        ['shopping', '购物解析', '淘宝 / 抖音 / B站小店产物'],
        ['music_playlist', '歌单', '本地预览与执行报告'],
        ['music_download', '下载', '本地音乐文件列表'],
        ['music_tag', '标签', '标签快照与历史'],
        ['assets', '资产库', '资源归档与本地文件'],
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
        ['system', '设置', '目录、主题与依赖'],
        ['performance', '性能诊断', '本机 CPU / 内存 / 磁盘读数']
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
      var settings = data.settings || {};
      var selected = new Set();
      var platformSel = h('select', { class: 'select' });
      platformSel.appendChild(h('option', { value: '' }, '全部平台'));
      (data.by_platform || []).forEach(function (item) {
        platformSel.appendChild(h('option', { value: item.key },
          item.name + '（' + item.count + '）'));
      });
      platformSel.value = state.info_accounts.platform || '';
      var tableHost = h('div');
      var jobHost = h('div');
      var resultHost = h('div');
      var selectedLabel = h('span', { class: 'muted small' }, '已选 0 个');
      var scopeSel = h('select', { class: 'select' },
        h('option', { value: 'all' }, '全部账号'),
        h('option', { value: 'selected' }, '选中账号'));
      scopeSel.value = state.info_accounts.scope || 'all';
      var countInput = h('input', {
        class: 'input', style: 'width:80px', value: String(settings.count || 10)
      });
      var incCheck = h('input', {
        type: 'checkbox', checked: !!settings.incremental
      });
      var dateStart = h('input', {
        class: 'input', style: 'width:130px', type: 'date',
        value: settings.date_start || ''
      });
      var dateEnd = h('input', {
        class: 'input', style: 'width:130px', type: 'date',
        value: settings.date_end || ''
      });
      var parseText = h('textarea', {
        class: 'textarea', rows: '3',
        placeholder: '一行一个链接或整段分享文案（最多 200 行）'
      });
      var parseBtn = h('button', { class: 'btn primary', type: 'button' }, '开始处理');
      var fetchBtn = h('button', { class: 'btn primary', type: 'button' }, '获取最新');
      var busy = false;

      function visibleRows() {
        return accounts.filter(function (row) {
          return !state.info_accounts.platform
            || row.platform === state.info_accounts.platform;
        });
      }

      function syncRun() {
        selectedLabel.textContent = '已选 ' + selected.size + ' 个';
        var target = scopeSel.value === 'selected' ? selected.size : accounts.length;
        scopeSel.options[1].textContent = '选中账号（' + selected.size + '）';
        fetchBtn.disabled = busy || target === 0;
        fetchBtn.textContent = busy ? '处理中…' : '获取最新';
        parseBtn.disabled = busy;
        parseBtn.textContent = busy ? '处理中…' : '开始处理';
        scopeSel.disabled = busy;
      }

      function draw() {
        var rows = visibleRows();
        if (!rows.length) {
          tableHost.replaceChildren(empty(
            accounts.length
              ? '当前平台没有账号。'
              : '暂无账号。可在上方批量粘贴账号主页链接后点「开始处理」。'));
          syncRun();
          return;
        }
        var head = h('tr');
        ['', '平台', '账号', '链接', '分组', '备注', '类型']
          .forEach(function (title) {
            head.appendChild(h('th', null, title));
          });
        var body = h('tbody');
        rows.forEach(function (row) {
          var kindLabels = { auto: '自动', video: '只视频',
            dynamic: '只动态', all: '全部' };
          var check = h('input', {
            type: 'checkbox', checked: selected.has(row.url)
          });
          check.addEventListener('change', function () {
            if (check.checked) selected.add(row.url);
            else selected.delete(row.url);
            syncRun();
          });
          body.appendChild(h('tr', null,
            h('td', { style: 'width:32px' }, check),
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
        syncRun();
      }

      platformSel.addEventListener('change', function () {
        state.info_accounts.platform = platformSel.value;
        draw();
      });
      scopeSel.addEventListener('change', function () {
        state.info_accounts.scope = scopeSel.value;
        syncRun();
      });
      incCheck.addEventListener('change', function () {
        post('/api/info-accounts/settings', {
          incremental: incCheck.checked
        }).catch(function (error) {
          toast('增量模式保存失败：' + error.message, 'error');
        });
      });
      function saveDates() {
        post('/api/info-accounts/settings', {
          date_start: dateStart.value, date_end: dateEnd.value
        }).then(function () {
          toast('日期段已保存', 'success');
        }).catch(function (error) {
          toast('日期段保存失败：' + error.message, 'error');
        });
      }
      dateStart.addEventListener('change', saveDates);
      dateEnd.addEventListener('change', saveDates);

      function runJob(path, body) {
        busy = true;
        syncRun();
        post(path, body).then(function (data) {
          watchJob(jobHost, data.job, function (job) {
            busy = false;
            var result = job.result || {};
            toast(result.summary || '完成',
              result.cancelled ? 'info' : 'success');
            resultHost.replaceChildren();
            if (result.counts) {
              var c = result.counts;
              resultHost.appendChild(h('div', {
                class: 'muted small', style: 'margin-top:6px'
              }, '新增账号 ' + c.accounts + '，重复账号 '
                + c.duplicate_accounts + '，作品 ' + c.posts
                + ' 条（新入库 ' + c.new_posts + '），失败 ' + c.failed));
            }
            syncRun();
            if (path.indexOf('parse') >= 0) {
              setTimeout(function () { renderInfoAccounts(root); }, 600);
            }
          }, {
            cancelable: true,
            onSettled: function (job) {
              busy = false;
              syncRun();
              if (job.status === 'error') {
                toast('任务失败：' + (job.error || '未知错误'), 'error');
              }
            }
          });
        }).catch(function (error) {
          busy = false;
          syncRun();
          toast('任务失败：' + error.message, 'error');
        });
      }

      parseBtn.addEventListener('click', function () {
        if (!parseText.value.trim()) {
          toast('请粘贴账号主页或作品链接', 'warning');
          return;
        }
        runJob('/api/info-accounts/parse', { text: parseText.value });
      });
      fetchBtn.addEventListener('click', function () {
        var urls = scopeSel.value === 'selected'
          ? Array.from(selected) : [];
        if (scopeSel.value === 'selected' && !urls.length) {
          toast('请勾选要采集的账号，或把采集范围改为「全部账号」', 'warning');
          return;
        }
        runJob('/api/info-accounts/collect', {
          urls: urls,
          count: Number(countInput.value) || 10,
          incremental: incCheck.checked,
          date_start: dateStart.value,
          date_end: dateEnd.value
        });
      });

      function setting(label, value) {
        return h('div', { class: 'metric' },
          h('div', { class: 'label' }, label),
          h('div', { class: 'value' }, value));
      }

      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'row-title' }, '批量添加 / 解析'),
        h('div', { class: 'muted small', style: 'margin:4px 0 6px' },
          '一行一个，支持整段分享文案：账号主页链接 → 添加账号；'
          + '作品 / 视频链接 → 解析并存入采集结果。'),
        parseText,
        h('div', { class: 'toolbar', style: 'margin-top:8px' },
          parseBtn,
          h('span', { class: 'muted small' },
            '处理过程与结果见下方运行日志；最多 200 行。'))));
      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'toolbar' },
          h('span', { class: 'row-title' }, '账号列表'),
          h('span', { class: 'muted small' },
            '共 ' + fmtNumber(data.total) + ' 个账号'),
          h('button', {
            class: 'btn', type: 'button',
            onclick: function () {
              visibleRows().forEach(function (row) { selected.add(row.url); });
              draw();
            }
          }, '全选本页'),
          h('button', {
            class: 'btn', type: 'button',
            onclick: function () {
              visibleRows().forEach(function (row) { selected.delete(row.url); });
              draw();
            }
          }, '全不选'),
          h('span', { style: 'flex:1' }),
          selectedLabel,
          h('span', { class: 'muted small' }, '平台'), platformSel),
        tableHost));
      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'row-title' }, '采集设置'),
        h('div', { class: 'toolbar', style: 'margin-top:8px' },
          h('span', { class: 'muted small' }, '采集范围'), scopeSel,
          h('span', { class: 'muted small' }, '每账号条数'), countInput,
          h('label', { class: 'muted small' }, incCheck, ' 增量模式'),
          h('span', { class: 'muted small' }, '日期段'), dateStart,
          h('span', { class: 'muted small' }, '~'), dateEnd),
        h('div', { class: 'metric-grid', style: 'margin-top:8px' },
          setting('平台数', String((data.by_platform || []).length)),
          setting('限流', '每 ' + (settings.rate_count || 10) + ' 个停 '
            + (settings.rate_interval || 10) + ' 秒'),
          setting('获取后自动下载', settings.auto_download ? '开' : '关'),
          setting('账号配置', '桌面端维护')),
        h('div', { class: 'muted small', style: 'margin-top:8px' },
          '增量模式表示只把新内容投影到增量库；关闭时按作品导出文件。'
          + '账号增删改仍在桌面端。')));
      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'toolbar' },
          fetchBtn,
          h('span', { class: 'muted small' },
            scopeSel.value === 'selected'
              ? '只采集勾选的账号' : '采集全部账号的最新内容'),
          h('span', { class: 'spacer' }),
          h('span', { class: 'muted small' }, '会话定时仍在桌面端'))));
      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'card-title' }, '运行'),
        jobHost,
        resultHost));
      draw();
    }).catch(function (error) {
      setError(root, error, function () { renderInfoAccounts(root); });
    });
  }

  function renderInfoTools(root) {
    root.replaceChildren();
    var TABS = [['comments', '视频评论'], ['bili', 'B站资源'],
      ['wechat', '公众号文章'], ['emoji', '抖音表情']];
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
        '视频评论与公众号文章可直接抓取并导出到本地产物目录；'
        + 'B站资源（表情包 / 装扮 / 用户空间）与抖音表情抓取仍在桌面端。'
        + '下方列表读取桌面端与 Web 端导出的本地产物。')));
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

    function commentsCard(reload) {
      var platformSel = h('select', { class: 'select' },
        h('option', { value: 'douyin' }, '抖音'),
        h('option', { value: 'bili' }, 'B站'),
        h('option', { value: 'weibo' }, '微博'));
      var urlInput = h('input', {
        class: 'input grow',
        placeholder: '视频链接或 ID；抖音支持作者主页（抓最新 N 个）'
      });
      var pagesInput = h('input', {
        class: 'input', style: 'width:70px', type: 'number',
        min: '1', max: '50', value: '5'
      });
      var pngCb = h('input', { type: 'checkbox', checked: true });
      var rawCb = h('input', { type: 'checkbox', checked: true });
      var kwInput = h('input', {
        class: 'input', placeholder: '关键词', style: 'width:130px'
      });
      var userInput = h('input', {
        class: 'input', placeholder: '昵称', style: 'width:120px'
      });
      var likesInput = h('input', {
        class: 'input', placeholder: '最少点赞', style: 'width:90px',
        type: 'number', min: '0'
      });
      var imageSel = h('select', { class: 'select' },
        h('option', { value: 'all' }, '全部评论'),
        h('option', { value: 'has' }, '仅带图'),
        h('option', { value: 'text' }, '仅纯文字'));
      var sortSel = h('select', { class: 'select' },
        h('option', { value: 'default' }, '默认排序'),
        h('option', { value: 'likes' }, '按点赞'),
        h('option', { value: 'time' }, '按时间'));
      var btn = h('button', { class: 'btn primary', type: 'button' }, '开始抓取');
      var jobHost = h('div');
      var running = false;
      btn.addEventListener('click', function () {
        if (running) return;
        if (!urlInput.value.trim()) {
          toast('请输入视频链接或 ID', 'warning');
          return;
        }
        if (!pngCb.checked && !rawCb.checked) {
          toast('请至少选择一种导出内容', 'warning');
          return;
        }
        running = true;
        btn.disabled = true;
        btn.textContent = '抓取中…';
        post('/api/tools/artifacts/comments', {
          platform: platformSel.value,
          url: urlInput.value.trim(),
          pages: Number(pagesInput.value) || 5,
          png: pngCb.checked,
          raw: rawCb.checked,
          keyword: kwInput.value.trim(),
          user: userInput.value.trim(),
          min_likes: Number(likesInput.value) || 0,
          image_mode: imageSel.value,
          sort_by: sortSel.value
        }).then(function (result) {
          watchJob(jobHost, result.job, function (job) {
            running = false;
            btn.disabled = false;
            btn.textContent = '开始抓取';
            var outcome = job.result || {};
            toast(outcome.summary || '抓取完成', 'success');
            reload();
          }, {
            cancelable: true,
            onSettled: function (job) {
              running = false;
              btn.disabled = false;
              btn.textContent = '开始抓取';
              if (job.status === 'error') {
                toast('抓取失败：' + (job.error || '未知错误'), 'error');
              }
            }
          });
        }).catch(function (error) {
          running = false;
          btn.disabled = false;
          btn.textContent = '开始抓取';
          toast('抓取失败：' + error.message, 'error');
        });
      });
      return h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'row-title' }, '抓取评论区'),
        h('div', { class: 'toolbar', style: 'margin-top:8px' },
          h('span', { class: 'muted small' }, '平台'), platformSel,
          urlInput),
        h('div', { class: 'toolbar', style: 'margin-top:8px' },
          h('span', { class: 'muted small' }, '页数'), pagesInput,
          h('label', { class: 'muted small' }, pngCb, ' 评论区长图'),
          h('label', { class: 'muted small' }, rawCb, ' 原始数据'),
          kwInput, userInput, likesInput, imageSel, sortSel,
          h('span', { style: 'flex:1' }), btn),
        h('div', { class: 'muted small', style: 'margin-top:6px' },
          '导出到「评论区/<平台>/<视频ID>/」，可在下方产物列表预览长图或下载；'
          + '抓取依赖登录态与网络，作者主页模式对抖音有效。'),
        jobHost);
    }

    function wechatCard(reload) {
      var textarea = h('textarea', {
        class: 'textarea', rows: '3',
        placeholder: '粘贴 mp.weixin.qq.com 文章链接，一行一个（最多 30 篇）'
      });
      var checks = {};
      var row = h('div', { class: 'toolbar', style: 'margin-top:8px' });
      [['html', 'HTML'], ['docx', 'Word'], ['md', 'Markdown']]
        .forEach(function (pair) {
          checks[pair[0]] = h('input', { type: 'checkbox', checked: true });
          row.appendChild(h('label', { class: 'muted small' },
            checks[pair[0]], ' ' + pair[1]));
        });
      var btn = h('button', { class: 'btn primary', type: 'button' }, '开始解析');
      var jobHost = h('div');
      var running = false;
      btn.addEventListener('click', function () {
        if (running) return;
        if (!textarea.value.trim()) {
          toast('请粘贴公众号文章链接', 'warning');
          return;
        }
        var formats = Object.keys(checks).filter(function (key) {
          return checks[key].checked;
        });
        if (!formats.length) {
          toast('请至少选择一种导出格式', 'warning');
          return;
        }
        running = true;
        btn.disabled = true;
        btn.textContent = '解析中…';
        post('/api/tools/artifacts/wechat', {
          text: textarea.value, formats: formats
        }).then(function (result) {
          watchJob(jobHost, result.job, function (job) {
            running = false;
            btn.disabled = false;
            btn.textContent = '开始解析';
            var outcome = job.result || {};
            toast(outcome.summary || '解析完成',
              outcome.failed ? 'warning' : 'success');
            reload();
          }, {
            cancelable: true,
            onSettled: function (job) {
              running = false;
              btn.disabled = false;
              btn.textContent = '开始解析';
              if (job.status === 'error') {
                toast('解析失败：' + (job.error || '未知错误'), 'error');
              }
            }
          });
        }).catch(function (error) {
          running = false;
          btn.disabled = false;
          btn.textContent = '开始解析';
          toast('解析失败：' + error.message, 'error');
        });
      });
      return h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'row-title' }, '解析公众号文章'),
        textarea, row,
        h('div', { class: 'toolbar', style: 'margin-top:8px' },
          h('span', { class: 'muted small' },
            '导出到公众号分类目录（或桌面设置的自定义目录）；'
            + 'PDF 需要桌面端渲染，Web 端不提供。'),
          h('span', { style: 'flex:1' }), btn),
        jobHost);
    }

    function biliCard(reload) {
      var modeSel = h('select', { class: 'select' },
        h('option', { value: 'emote' }, '表情包'),
        h('option', { value: 'garb' }, '装扮'),
        h('option', { value: 'up' }, '用户空间'));
      var modeHost = h('div', { style: 'margin-top:8px' });
      var jobHost = h('div');

      function jobRun(path, body, label) {
        post(path, body).then(function (result) {
          watchJob(jobHost, result.job, function (job) {
            var outcome = job.result || {};
            toast(outcome.summary || (label + '完成'), 'success');
            reload();
          }, {
            cancelable: true,
            onSettled: function (job) {
              if (job.status === 'error') {
                toast(label + '失败：' + (job.error || '未知错误'), 'error');
              }
            }
          });
        }).catch(function (error) {
          toast(label + '失败：' + error.message, 'error');
        });
      }

      function renderEmote() {
        var keyword = h('input', {
          class: 'input grow', placeholder: '包名关键字，留空列出全部'
        });
        var searchBtn = h('button', { class: 'btn', type: 'button' }, '搜索');
        var listHost = h('div', { style: 'margin-top:8px' });
        var selected = new Set();
        var dlBtn = h('button', {
          class: 'btn primary', type: 'button', disabled: true
        }, '下载选中');
        var info = h('div', { class: 'muted small', style: 'margin-top:6px' });
        function syncDl() {
          dlBtn.disabled = selected.size === 0;
          dlBtn.textContent = '下载选中' + (selected.size
            ? '（' + selected.size + ' 个包）' : '');
        }
        function search() {
          searchBtn.disabled = true;
          info.textContent = '正在读取表情包列表…';
          post('/api/tools/bili/emote/search', { keyword: keyword.value.trim() })
            .then(function (data) {
              searchBtn.disabled = false;
              selected.clear();
              syncDl();
              var items = data.items || [];
              info.textContent = '共 ' + fmtNumber(data.total) + ' 个表情包'
                + (data.truncated ? '（仅列前 ' + items.length + ' 个）' : '')
                + ' · 勾选后下载到「B站资源/表情包」';
              if (!items.length) {
                listHost.replaceChildren(empty('没有匹配的表情包。'));
                return;
              }
              var head = h('tr', null, h('th', { style: 'width:32px' }, ''),
                h('th', null, '表情包'), h('th', null, '更新时间'));
              var body = h('tbody');
              items.forEach(function (item) {
                var check = h('input', { type: 'checkbox' });
                check.addEventListener('change', function () {
                  if (check.checked) selected.add(item.id);
                  else selected.delete(item.id);
                  syncDl();
                });
                body.appendChild(h('tr', null,
                  h('td', null, check),
                  h('td', { class: 'ellipsis', title: item.id }, item.name || item.id),
                  h('td', { class: 'muted small' }, item.mtime || '-')));
              });
              listHost.replaceChildren(h('div', { class: 'table-wrap' },
                h('table', { class: 'db-table' }, h('thead', null, head), body)));
            }).catch(function (error) {
              searchBtn.disabled = false;
              info.textContent = '';
              toast('搜索失败：' + error.message, 'error');
            });
        }
        searchBtn.addEventListener('click', search);
        dlBtn.addEventListener('click', function () {
          if (!selected.size) return;
          var ids = Array.from(selected);
          dlBtn.disabled = true;
          jobRun('/api/tools/bili/emote/download', { ids: ids }, '表情包下载');
        });
        modeHost.replaceChildren(
          h('div', { class: 'toolbar' }, keyword, searchBtn, dlBtn),
          info, listHost);
        search();
      }

      function renderIdInput(mode) {
        var isGarb = mode === 'garb';
        var input = h('input', {
          class: 'input grow',
          placeholder: isGarb
            ? '装扮 item_id 或装扮链接（如 107423）'
            : 'UP 主 UID 或空间链接（如 470880931）'
        });
        var btn = h('button', { class: 'btn primary', type: 'button' },
          isGarb ? '下载装扮素材' : '下载空间资源');
        btn.addEventListener('click', function () {
          var value = input.value.trim();
          if (!value) {
            toast(isGarb ? '请填写装扮 ID' : '请填写 UP 主 UID', 'warning');
            return;
          }
          btn.disabled = true;
          jobRun(isGarb ? '/api/tools/bili/garb/download'
                        : '/api/tools/bili/up/download',
            isGarb ? { item_id: value } : { uid: value },
            isGarb ? '装扮下载' : '空间资源下载');
          setTimeout(function () { btn.disabled = false; }, 1500);
        });
        modeHost.replaceChildren(
          h('div', { class: 'toolbar' }, input, btn),
          h('div', { class: 'muted small', style: 'margin-top:6px' },
            isGarb
              ? '读取装扮详情后按桌面端同一份清单下载（背景 / 表情包 / 皮肤 / 头像框等）。'
              : '下载头像、空间横幅、App 端背景、投稿封面、充电表情、收藏集封面等。'));
      }

      modeSel.addEventListener('change', function () {
        if (modeSel.value === 'emote') renderEmote();
        else renderIdInput(modeSel.value);
      });
      renderEmote();
      return h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'row-title' }, 'B站资源下载'),
        h('div', { class: 'toolbar', style: 'margin-top:8px' },
          h('span', { class: 'muted small' }, '类型'), modeSel,
          h('span', { class: 'muted small' },
            '需要 B站 登录；输出到「B站资源/表情包 | 装扮 | 空间资源」。')),
        modeHost, jobHost);
    }

    function emojiCard(reload) {
      var room = h('input', {
        class: 'input grow',
        placeholder: '直播间号或 live.douyin.com 链接'
      });
      var fetchBtn = h('button', { class: 'btn', type: 'button' }, '拉取表情');
      var dlBtn = h('button', {
        class: 'btn primary', type: 'button', disabled: true
      }, '下载全部');
      var info = h('div', { class: 'muted small', style: 'margin-top:6px' });
      var listHost = h('div');
      var jobHost = h('div');
      var items = [];
      fetchBtn.addEventListener('click', function () {
        if (!room.value.trim()) {
          toast('请填写直播间号或链接', 'warning');
          return;
        }
        fetchBtn.disabled = true;
        info.textContent = '正在拉取会员表情栏…';
        post('/api/tools/douyin-emoji/fetch', { room: room.value.trim() })
          .then(function (data) {
            fetchBtn.disabled = false;
            items = data.items || [];
            dlBtn.disabled = items.length === 0;
            info.textContent = '共 ' + fmtNumber(data.total) + ' 个会员表情'
              + (data.describe ? ' · ' + data.describe : '')
              + (data.error ? ' · ' + data.error : '');
            if (!items.length) {
              listHost.replaceChildren(empty('没有拉到会员表情。'));
              return;
            }
            var list = h('div', { class: 'artifact-list' });
            items.slice(0, 40).forEach(function (item) {
              list.appendChild(h('div', { class: 'artifact-row' },
                h('span', { class: 'artifact-name', title: item.url },
                  item.name)));
            });
            listHost.replaceChildren(list,
              items.length > 40
                ? h('div', { class: 'muted small', style: 'margin-top:6px' },
                  '仅列前 40 个，共 ' + fmtNumber(items.length) + ' 个。')
                : null);
          }).catch(function (error) {
            fetchBtn.disabled = false;
            info.textContent = '';
            toast('拉取失败：' + error.message, 'error');
          });
      });
      dlBtn.addEventListener('click', function () {
        if (!room.value.trim()) return;
        dlBtn.disabled = true;
        post('/api/tools/douyin-emoji/download', { room: room.value.trim() })
          .then(function (result) {
            watchJob(jobHost, result.job, function (job) {
              var outcome = job.result || {};
              toast(outcome.summary || '下载完成', 'success');
              reload();
            }, {
              cancelable: true,
              onSettled: function (job) {
                if (job.status === 'error') {
                  dlBtn.disabled = false;
                  toast('下载失败：' + (job.error || '未知错误'), 'error');
                }
              }
            });
          }).catch(function (error) {
            dlBtn.disabled = false;
            toast('下载失败：' + error.message, 'error');
          });
      });
      return h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'row-title' }, '抖音会员表情'),
        h('div', { class: 'toolbar', style: 'margin-top:8px' },
          room, fetchBtn, dlBtn),
        h('div', { class: 'muted small', style: 'margin-top:6px' },
          '抓取直播间的「会员表情栏」（需要登录），下载到「抖音表情包」导出目录。'),
        info, listHost, jobHost);
    }

    function load() {
      host.replaceChildren();
      if (state.artifacts.tab === 'comments') host.appendChild(commentsCard(load));
      else if (state.artifacts.tab === 'wechat') host.appendChild(wechatCard(load));
      else if (state.artifacts.tab === 'bili') host.appendChild(biliCard(load));
      else if (state.artifacts.tab === 'emoji') host.appendChild(emojiCard(load));
      var listHost = h('div');
      host.appendChild(listHost);
      setLoading(listHost, '正在读取本地产物…');
      api('/api/tools/artifacts?kind=' + encodeURIComponent(state.artifacts.tab))
        .then(function (data) {
          listHost.replaceChildren();
          var groups = data.groups || [];
          var images = groups.reduce(function (sum, group) {
            return sum + (group.images || 0);
          }, 0);
          listHost.appendChild(h('div', {
            class: 'muted small', style: 'margin-bottom:8px'
          }, '共 ' + groups.length + ' 组 · ' + fmtNumber(data.total)
            + ' 个文件 · 图片 ' + fmtNumber(images)));
          if (!groups.length) {
            listHost.appendChild(empty(data.note || '暂无产物。'));
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
            listHost.appendChild(card);
          });
        }).catch(function (error) {
          setError(listHost, error, load);
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
    return renderNetdiskPanel(root, 'download');
  }

  function renderNetdiskPanel(root, mode) {
    setLoading(root, mode === 'backup' ? '正在读取网盘备份状态…' : '正在读取网盘目录…');
    return api('/api/netdisk').then(function (data) {
      root.replaceChildren();
      var host = h('div');
      var providers = data.providers || [];
      var providerLabel = {};
      providers.forEach(function (item) { providerLabel[item.value] = item.label; });
      root.appendChild(host);

      function editMapping(mapping) {
        var form = {
          id: mapping ? mapping.id : '',
          provider: h('select', { class: 'select' },
            providers.map(function (item) {
              return h('option', {
                value: item.value,
                selected: mapping
                  ? item.value === mapping.provider
                  : item.value === 'quark'
              }, item.label);
            })),
          local: h('input', {
            class: 'input', style: 'width:100%',
            placeholder: '本地目录完整路径，如 D:\\WindowsDocs\\视频',
            value: mapping ? mapping.local_dir : ''
          }),
          remote: h('input', {
            class: 'input', style: 'width:100%',
            placeholder: '云端目录，如 封茗囧菌直播录屏2017年起',
            value: mapping ? mapping.remote_dir : ''
          }),
          exclude: h('input', {
            class: 'input', style: 'width:100%',
            placeholder: '排除规则，分号分隔，如 cache/*; *.tmp',
            value: mapping ? mapping.exclude : ''
          }),
          maxMb: h('input', {
            class: 'input', style: 'width:110px', type: 'number', min: '0',
            value: String(mapping ? mapping.max_file_mb : 0)
          }),
          enabled: h('input', {
            type: 'checkbox', checked: mapping ? !!mapping.enabled : true
          }),
          replace: h('input', {
            type: 'checkbox',
            checked: mapping ? mapping.replace_changed !== false : true
          }),
          share: h('input', {
            type: 'checkbox', checked: mapping ? !!mapping.share : false
          }),
          expire: h('select', { class: 'select' },
            ['永久', '7天', '30天', '1天'].map(function (value) {
              return h('option', {
                value: value,
                selected: mapping ? mapping.share_expire === value : value === '永久'
              }, value);
            })),
          passcode: h('input', {
            type: 'checkbox', checked: mapping ? !!mapping.share_passcode : false
          })
        };
        var errLine = h('div', { class: 'muted small', style: 'color:var(--danger)' });
        var overlay = h('div', { class: 'preview-overlay' });
        overlay.addEventListener('click', function (event) {
          if (event.target === overlay) overlay.remove();
        });
        var saveBtn = h('button', { class: 'btn primary', type: 'button' },
          mapping ? '保存修改' : '新增映射');
        overlay.appendChild(h('div', {
          class: 'preview-box', style: 'max-width:640px'
        },
          h('div', { class: 'preview-caption' },
            h('span', null, mapping ? '编辑备份映射' : '新增备份映射'),
            h('button', {
              class: 'btn', type: 'button',
              onclick: function () { overlay.remove(); }
            }, '取消')),
          h('div', { style: 'padding:12px 14px' },
            h('div', { class: 'kv' }, h('span', { class: 'muted' }, '网盘'),
              form.provider),
            h('div', { class: 'kv', style: 'margin-top:8px' },
              h('span', { class: 'muted' }, '本地目录'), form.local),
            h('div', { class: 'kv', style: 'margin-top:8px' },
              h('span', { class: 'muted' }, '云端目录'), form.remote),
            h('div', { class: 'kv', style: 'margin-top:8px' },
              h('span', { class: 'muted' }, '排除规则'), form.exclude),
            h('div', { class: 'toolbar', style: 'margin-top:8px' },
              h('span', { class: 'muted small' }, '单文件上限 MB'),
              form.maxMb,
              h('label', { class: 'muted small' }, form.enabled, ' 启用'),
              h('label', { class: 'muted small' }, form.replace, ' 替换已变更文件'),
              h('label', { class: 'muted small' }, form.share, ' 完成后生成分享'),
              form.expire,
              h('label', { class: 'muted small' }, form.passcode, ' 提取码')),
            errLine,
            h('div', { class: 'toolbar', style: 'margin-top:10px' },
              h('span', { class: 'muted small' },
                '本地目录必须已存在；保存后立即生效，下一次备份按新规则执行。'),
              h('span', { style: 'flex:1' }), saveBtn))));
        document.body.appendChild(overlay);
        saveBtn.addEventListener('click', function () {
          saveBtn.disabled = true;
          post('/api/netdisk/mappings', {
            id: form.id,
            provider: form.provider.value,
            local_dir: form.local.value.trim(),
            remote_dir: form.remote.value.trim(),
            exclude: form.exclude.value.trim(),
            max_file_mb: Number(form.maxMb.value) || 0,
            enabled: form.enabled.checked,
            replace_changed: form.replace.checked,
            share: form.share.checked,
            share_expire: form.expire.value,
            share_passcode: form.passcode.checked
          }).then(function (result) {
            toast(result.summary || '已保存', 'success');
            overlay.remove();
            renderNetdiskPanel(root, mode);
          }).catch(function (error) {
            saveBtn.disabled = false;
            errLine.textContent = '保存失败：' + error.message;
          });
        });
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
        var jobHost = h('div');
        var running = false;
        var addBtn = h('button', { class: 'btn', type: 'button' }, '新增映射');
        var backupBtn = h('button', {
          class: 'btn primary', type: 'button'
        }, '立即备份');
        addBtn.addEventListener('click', function () { editMapping(null); });
        backupBtn.addEventListener('click', function () {
          if (running) return;
          var enabled = mappings.filter(function (item) {
            return item.enabled && item.local_dir;
          });
          if (!enabled.length) {
            toast('没有启用的备份目录，请先新增或启用一条映射', 'warning');
            return;
          }
          if (!window.confirm('立即备份\n\n将按顺序备份 ' + enabled.length
            + ' 组启用的映射；已有且未变化的文件自动跳过，'
            + '上传完成后回写每组最近运行结果。\n\n确认开始？')) {
            return;
          }
          running = true;
          backupBtn.disabled = true;
          addBtn.disabled = true;
          post('/api/netdisk/backup', {}).then(function (result) {
            watchJob(jobHost, result.job, function (job) {
              running = false;
              backupBtn.disabled = false;
              addBtn.disabled = false;
              var outcome = job.result || {};
              toast(outcome.summary || '备份完成',
                outcome.failed ? 'warning' : 'success');
              var shareHost = h('div');
              (outcome.shares || []).forEach(function (item) {
                shareHost.appendChild(h('div', { class: 'muted small' },
                  '分享：' + item.url
                  + (item.passcode ? '  提取码：' + item.passcode : '')));
              });
              if ((outcome.shares || []).length) {
                jobHost.appendChild(h('div', { class: 'card' },
                  h('div', { class: 'row-title' }, '本次分享链接'),
                  shareHost));
              }
              setTimeout(function () { renderNetdiskPanel(root, mode); }, 1200);
            }, {
              cancelable: true,
              onSettled: function (job) {
                running = false;
                backupBtn.disabled = false;
                addBtn.disabled = false;
                if (job.status === 'error') {
                  toast('备份失败：' + (job.error || '未知错误'), 'error');
                }
              }
            });
          }).catch(function (error) {
            running = false;
            backupBtn.disabled = false;
            addBtn.disabled = false;
            toast('备份失败：' + error.message, 'error');
          });
        });

        host.appendChild(h('div', {
          class: 'muted small', style: 'margin-bottom:8px'
        }, '共 ' + mappings.length + ' 组映射 · 定时 ' + schedText
          + ' · 已备份 ' + fmtNumber(files) + ' 个文件 · ' + fmtBytes(bytes)));
        host.appendChild(h('div', { class: 'toolbar', style: 'margin-bottom:8px' },
          addBtn, backupBtn,
          h('span', { class: 'muted small' },
            '立即备份与桌面端同一条引擎；定时任务仍在「任务」页面管理。')));
        host.appendChild(jobHost);
        if (!mappings.length) {
          host.appendChild(empty(
            '还没有备份目录。点「新增映射」选择本地目录与云端目录。'));
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
          var editBtn = h('button', { class: 'btn', type: 'button' }, '编辑');
          editBtn.addEventListener('click', function () { editMapping(mapping); });
          var toggleBtn = h('button', { class: 'btn', type: 'button' },
            mapping.enabled ? '停用' : '启用');
          toggleBtn.addEventListener('click', function () {
            toggleBtn.disabled = true;
            post('/api/netdisk/mappings/enabled', {
              id: mapping.id, enabled: !mapping.enabled
            }).then(function (result) {
              toast(result.summary || '已保存', 'success');
              renderNetdiskPanel(root, mode);
            }).catch(function (error) {
              toggleBtn.disabled = false;
              toast('操作失败：' + error.message, 'error');
            });
          });
          var delBtn = h('button', {
            class: 'btn danger', type: 'button'
          }, '删除');
          delBtn.addEventListener('click', function () {
            if (!window.confirm('删除备份映射\n\n将删除「'
              + (mapping.local_dir || '') + ' → ' + (mapping.remote_dir || '')
              + '」这条映射。\n只删除映射配置，不会删除本地文件或云端文件。\n\n确认删除？')) {
              return;
            }
            delBtn.disabled = true;
            post('/api/netdisk/mappings/delete', {
              id: mapping.id, confirmed: true
            }).then(function (result) {
              toast(result.summary || '已删除', 'success');
              renderNetdiskPanel(root, mode);
            }).catch(function (error) {
              delBtn.disabled = false;
              toast('删除失败：' + error.message, 'error');
            });
          });
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
                mapping.enabled ? '启用' : '停用'),
              h('span', { style: 'flex:1' }),
              editBtn, toggleBtn, delBtn),
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
        var rootId = (data.root_ids || {})[providers.length ? providers[0].value : 'quark'] || '0';
        var providerSel = h('select', { class: 'select' },
          providers.map(function (item) {
            return h('option', { value: item.value }, item.label);
          }));
        var crumbs = [{ fid: (data.root_ids || {})[providerSel.value] || rootId,
                        name: '根目录' }];
        var selected = new Map();
        var listHost = h('div');
        var listJobHost = h('div');
        var dlJobHost = h('div');
        var pathLabel = h('span', { class: 'muted small' });
        var saveInput = h('input', {
          class: 'input grow', value: data.default_save_dir || '',
          placeholder: '保存到（导出目录或工作目录内）'
        });
        var upBtn = h('button', { class: 'btn', type: 'button' }, '上一级');
        var refreshBtn = h('button', { class: 'btn', type: 'button' }, '刷新');
        var dlBtn = h('button', {
          class: 'btn primary', type: 'button', disabled: true
        }, '下载选中');
        var folderBtn = h('button', {
          class: 'btn', type: 'button'
        }, '下载当前文件夹');
        var selLabel = h('span', { class: 'muted small' }, '');
        var busy = false;

        function currentFid() { return crumbs[crumbs.length - 1].fid; }

        function syncPath() {
          pathLabel.textContent = ' / ' + crumbs.map(function (item) {
            return item.name;
          }).join(' / ');
          upBtn.disabled = busy || crumbs.length <= 1;
          dlBtn.disabled = busy || selected.size === 0;
          dlBtn.textContent = busy ? '处理中…' : '下载选中';
          folderBtn.disabled = busy;
          selLabel.textContent = selected.size
            ? '已选 ' + selected.size + ' 个文件' : '';
        }

        function enter(fid, name) {
          crumbs.push({ fid: fid, name: name });
          selected.clear();
          loadDir();
        }

        function loadDir() {
          busy = true;
          syncPath();
          setLoading(listHost, '正在读取云端目录…');
          post('/api/netdisk/cloud/list', {
            provider: providerSel.value, fid: currentFid()
          }).then(function (result) {
            watchJob(listJobHost, result.job, function (job) {
              busy = false;
              syncPath();
              var outcome = job.result || {};
              var entries = outcome.entries || [];
              if (!entries.length) {
                listHost.replaceChildren(empty('该目录为空。'));
                return;
              }
              var head = h('tr', null,
                h('th', { style: 'width:32px' }, ''),
                h('th', null, '名称'), h('th', null, '类型'),
                h('th', { class: 'num' }, '大小'),
                h('th', null, '修改时间'));
              var body = h('tbody');
              entries.forEach(function (entry) {
                var check = h('input', { type: 'checkbox' });
                if (!entry.dir) {
                  check.addEventListener('change', function () {
                    if (check.checked) selected.set(entry.fid, entry);
                    else selected.delete(entry.fid);
                    syncPath();
                  });
                } else {
                  check.disabled = true;
                }
                var nameNode = entry.dir
                  ? h('a', {
                    href: '#', title: '进入文件夹', onclick: function (event) {
                      event.preventDefault();
                      enter(entry.fid, entry.name);
                    }
                  }, entry.name + '/')
                  : h('span', null, entry.name);
                body.appendChild(h('tr', null,
                  h('td', null, check),
                  h('td', { class: 'ellipsis' }, nameNode),
                  h('td', { class: 'muted small' },
                    entry.dir ? '文件夹' : '文件'),
                  h('td', { class: 'num' },
                    entry.dir ? '-' : fmtBytes(entry.size)),
                  h('td', { class: 'muted small' }, entry.updated || '-')));
              });
              var parts = [];
              if (outcome.truncated) {
                parts.push(h('div', {
                  class: 'muted small', style: 'margin:6px 2px'
                }, '目录超过 ' + fmtNumber(outcome.total)
                  + ' 项，仅显示已加载部分。'));
              }
              parts.push(h('div', { class: 'table-wrap' },
                h('table', { class: 'db-table' },
                  h('thead', null, head), body)));
              listHost.replaceChildren.apply(listHost, parts);
            }, {
              onSettled: function (job) {
                busy = false;
                syncPath();
                if (job.status === 'error') {
                  setError(listHost, new Error(job.error || '列目录失败'),
                    loadDir);
                }
              }
            });
          }).catch(function (error) {
            busy = false;
            syncPath();
            setError(listHost, error, loadDir);
          });
        }

        function doDownload(payload, title) {
          if (busy) return;
          if (!window.confirm(title + '\n\n保存到：' + saveInput.value.trim()
            + '\n\n已存在的同名同大小文件会跳过；中断后可重新下载续传。\n\n确认下载？')) {
            return;
          }
          busy = true;
          syncPath();
          payload.provider = providerSel.value;
          payload.save_dir = saveInput.value.trim();
          post('/api/netdisk/cloud/download', payload).then(function (result) {
            watchJob(dlJobHost, result.job, function (job) {
              busy = false;
              syncPath();
              var outcome = job.result || {};
              toast(outcome.summary || '下载完成',
                outcome.failed ? 'warning' : 'success');
              if (outcome.failures && outcome.failures.length) {
                dlJobHost.appendChild(h('div', {
                  class: 'card', style: 'margin-top:8px'
                }, h('div', { class: 'muted small' },
                  '失败文件：' + outcome.failures.map(function (item) {
                    return item.name + '（' + (item.error || '未知') + '）';
                  }).join('；'))));
              }
            }, {
              cancelable: true,
              onSettled: function (job) {
                busy = false;
                syncPath();
                if (job.status === 'error') {
                  toast('下载失败：' + (job.error || '未知错误'), 'error');
                }
              }
            });
          }).catch(function (error) {
            busy = false;
            syncPath();
            toast('下载失败：' + error.message, 'error');
          });
        }

        upBtn.addEventListener('click', function () {
          if (crumbs.length > 1) {
            crumbs.pop();
            selected.clear();
            loadDir();
          }
        });
        refreshBtn.addEventListener('click', function () {
          selected.clear();
          loadDir();
        });
        providerSel.addEventListener('change', function () {
          crumbs = [{ fid: (data.root_ids || {})[providerSel.value] || '0',
                      name: '根目录' }];
          selected.clear();
          loadDir();
        });
        dlBtn.addEventListener('click', function () {
          var files = Array.from(selected.values()).map(function (entry) {
            return { fid: entry.fid, name: entry.name, size: entry.size,
                     download_id: entry.download_id };
          });
          if (!files.length) return;
          doDownload({ files: files, folders: [] },
            '下载选中 ' + files.length + ' 个文件');
        });
        folderBtn.addEventListener('click', function () {
          var folder = crumbs[crumbs.length - 1];
          doDownload({ files: [], folders: [
            { fid: folder.fid, name: folder.name }
          ] }, '下载文件夹：' + folder.name);
        });

        host.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
          h('div', { class: 'toolbar' },
            h('span', { class: 'muted small' }, '网盘'), providerSel,
            upBtn, refreshBtn, pathLabel,
            h('span', { style: 'flex:1' }), selLabel, dlBtn, folderBtn),
          h('div', { class: 'toolbar', style: 'margin-top:8px' },
            h('span', { class: 'muted small' }, '保存到'), saveInput),
          h('div', { class: 'muted small', style: 'margin-top:6px' },
            '浏览云端目录并下载到导出目录或工作目录内；文件夹会递归下载并保留层级，'
            + '已存在的同名同大小文件自动跳过。自动解压仍在桌面端。')));
        host.appendChild(listJobHost);
        host.appendChild(dlJobHost);
        host.appendChild(listHost);
        loadDir();
      }

      if (mode === 'backup') renderBackup();
      else renderDownload();
    }).catch(function (error) {
      setError(root, error, function () { renderNetdiskPanel(root, mode); });
    });
  }

  function renderRenameBatch(root) {
    setLoading(root, '正在读取改名设置…');
    api('/api/rename-batch').then(function (data) {
      root.replaceChildren();
      if (!state.rename_batch.path) {
        state.rename_batch.path = data.work_dir || '';
      }
      var pathInput = h('input', {
        class: 'input grow',
        placeholder: '要扫描的文件夹完整路径',
        value: state.rename_batch.path || ''
      });
      var recursiveCb = h('input', {
        type: 'checkbox', checked: !!state.rename_batch.recursive
      });
      var dirRenameCb = h('input', {
        type: 'checkbox', checked: !!state.rename_batch.dirRename
      });
      var filterSel = h('select', { class: 'select' });
      (data.filters || []).forEach(function (item) {
        filterSel.appendChild(h('option', {
          value: item.key,
          selected: item.key === state.rename_batch.filter
        }, item.name));
      });
      var scanBtn = h('button', {
        class: 'btn primary', type: 'button'
      }, '扫描预览');
      var template = h('input', {
        class: 'input', placeholder: '如 {名}_{序号}，留空不启用'
      });
      var prefix = h('input', {
        class: 'input', placeholder: '前缀', style: 'width:120px'
      });
      var suffix = h('input', {
        class: 'input', placeholder: '后缀', style: 'width:120px'
      });
      var find = h('input', {
        class: 'input', placeholder: '查找', style: 'width:120px'
      });
      var replaceTo = h('input', {
        class: 'input', placeholder: '替换为', style: 'width:120px'
      });
      var caseSel = h('select', { class: 'select' });
      [['', '大小写不变'], ['upper', '全部大写'], ['lower', '全部小写'],
        ['capital', '首字母大写']].forEach(function (pair) {
        caseSel.appendChild(h('option', { value: pair[0] }, pair[1]));
      });
      var convertSel = h('select', { class: 'select' });
      [['', '全半角不变'], ['full2half', '全角转半角'],
        ['half2full', '半角转全角']].forEach(function (pair) {
        convertSel.appendChild(h('option', { value: pair[0] }, pair[1]));
      });
      var extCaseSel = h('select', { class: 'select' });
      [['', '扩展名不变'], ['upper', '扩展名大写'], ['lower', '扩展名小写'],
        ['delete', '删除扩展名']].forEach(function (pair) {
        extCaseSel.appendChild(h('option', { value: pair[0] }, pair[1]));
      });
      var newExt = h('input', {
        class: 'input', placeholder: '如 png（留空不改）', style: 'width:120px'
      });
      var kwInput = h('input', {
        class: 'input', placeholder: '定位关键词', style: 'width:120px'
      });
      var kwText = h('input', {
        class: 'input', placeholder: '插入文本', style: 'width:120px'
      });
      var kwAfterCb = h('input', { type: 'checkbox', checked: true });
      var posText = h('input', {
        class: 'input', placeholder: '插入文本', style: 'width:120px'
      });
      var posIdx = h('input', {
        class: 'input', type: 'number', value: '0', min: '0',
        style: 'width:72px'
      });
      var fromRightCb = h('input', { type: 'checkbox' });
      var textmode = h('textarea', {
        class: 'textarea',
        rows: 4,
        placeholder: '每行一个完整新文件名（含扩展名）；行顺序与预览一致，'
          + '留空表示沿用上面的规则结果'
      });
      var numberingCb = h('input', { type: 'checkbox' });
      var randomCb = h('input', { type: 'checkbox' });
      var startInput = h('input', {
        class: 'input', type: 'number', value: '1', style: 'width:72px'
      });
      var widthInput = h('input', {
        class: 'input', type: 'number', value: '2', style: 'width:72px'
      });
      var ignoreExtCb = h('input', { type: 'checkbox', checked: true });
      var conflictCb = h('input', { type: 'checkbox', checked: true });
      var stats = h('div', {
        class: 'muted small', style: 'margin:8px 0'
      });
      var tableHost = h('div');
      var resultHost = h('div');
      var timer = null;
      var lastRows = [];
      var execBtn = h('button', {
        class: 'btn primary', type: 'button', disabled: true
      }, '执行改名');
      var fillTmBtn = h('button', {
        class: 'btn mini', type: 'button'
      }, '填入当前预览');
      fillTmBtn.addEventListener('click', function () {
        if (!lastRows.length) {
          toast('请先扫描预览', 'warning');
          return;
        }
        textmode.value = lastRows.map(function (row) {
          return row.new;
        }).join('\n');
        schedule();
      });

      var delTypeBoxes = {};
      var delTypeRow = h('div', { class: 'toolbar' });
      [['space', '空格'], ['digit', '数字'], ['lower', '小写'],
        ['upper', '大写'], ['chinese', '中文'], ['special', '特殊字符']
      ].forEach(function (pair) {
        var box = h('input', { type: 'checkbox' });
        delTypeBoxes[pair[0]] = box;
        delTypeRow.appendChild(h('label', { class: 'check' }, box,
          ' ' + pair[1]));
      });
      var delPos = h('input', {
        class: 'input', type: 'number', value: '0', min: '0',
        style: 'width:72px'
      });
      var delCnt = h('input', {
        class: 'input', type: 'number', value: '0', min: '0',
        style: 'width:72px'
      });
      var delRight = h('input', { type: 'checkbox' });
      var delKw = h('input', {
        class: 'input', type: 'text', placeholder: '定位关键词',
        style: 'min-width:120px'
      });
      var delKwCnt = h('input', {
        class: 'input', type: 'number', value: '0', min: '0',
        style: 'width:72px'
      });
      var delDir = h('select', { class: 'select' },
        h('option', { value: 'right' }, '向右删'),
        h('option', { value: 'left' }, '向左删'));
      var delIncl = h('input', { type: 'checkbox' });
      var delPosRow = h('div', { class: 'toolbar' },
        h('span', { class: 'muted small' }, '从第'), delPos,
        h('span', { class: 'muted small' }, '个字符起删除'), delCnt,
        h('span', { class: 'muted small' }, '个字符'),
        h('label', { class: 'check' }, delRight, ' 从右往左'),
        h('span', { class: 'muted small' }, '（数量 0 表示不按位置删）'));
      var delKwRow = h('div', { class: 'toolbar' },
        h('span', { class: 'muted small' }, '关键词'), delKw, delDir,
        delKwCnt, h('span', { class: 'muted small' }, '个字符'),
        h('label', { class: 'check' }, delIncl, ' 包含关键词本身'));

      function collect() {
        return {
          path: pathInput.value.trim(),
          recursive: recursiveCb.checked,
          filter: filterSel.value,
          dir_rename: dirRenameCb.checked,
          template: template.value.trim(),
          prefix: prefix.value,
          suffix: suffix.value,
          find: find.value,
          to: replaceTo.value,
          case: caseSel.value,
          convert: convertSel.value,
          ext_case: extCaseSel.value,
          new_ext: newExt.value,
          kw: kwInput.value,
          kw_text: kwText.value,
          kw_after: kwAfterCb.checked,
          pos_idx: posIdx.value,
          pos_text: posText.value,
          from_right: fromRightCb.checked,
          del_types: Object.keys(delTypeBoxes).filter(function (key) {
            return delTypeBoxes[key].checked;
          }),
          del_pos: delPos.value,
          del_cnt: delCnt.value,
          del_from_right: delRight.checked,
          del_kw: delKw.value,
          del_kw_cnt: delKwCnt.value,
          del_dir: delDir.value,
          del_incl: delIncl.checked,
          textmode: textmode.value,
          numbering: numberingCb.checked,
          random: randomCb.checked,
          start: startInput.value,
          width: widthInput.value,
          ignore_ext: ignoreExtCb.checked,
          conflict: conflictCb.checked
        };
      }

      function load() {
        var payload = collect();
        state.rename_batch.path = payload.path;
        state.rename_batch.recursive = payload.recursive;
        state.rename_batch.filter = payload.filter;
        if (!payload.path) {
          stats.textContent = '请填写要扫描的文件夹。';
          tableHost.replaceChildren();
          return;
        }
        setLoading(tableHost, '正在生成预览…');
        post('/api/rename-batch/preview', payload).then(function (result) {
          lastRows = result.rows || [];
          execBtn.disabled = !result.changed;
          stats.textContent = '共 ' + fmtNumber(result.total) + ' 个文件 · 将改名 '
            + fmtNumber(result.changed) + ' 个'
            + (result.truncated ? '（超过 2000 个，已截断）' : '')
            + ' · 执行前会二次确认，只改名称不改内容';
          if (!lastRows.length) {
            tableHost.replaceChildren(empty('文件夹内没有符合条件的文件。'));
            return;
          }
          var head = h('tr');
          ['序号', '文件名', '新文件名', '状态', '扩展名', '大小']
            .forEach(function (title, index) {
              head.appendChild(h('th', {
                class: index === 0 || index === 5 ? 'num' : ''
              }, title));
            });
          var body = h('tbody');
          lastRows.forEach(function (row) {
            body.appendChild(h('tr', null,
              h('td', { class: 'num' }, row.index),
              h('td', { class: 'ellipsis' },
                (row.kind === 'dir' ? '［目录］' : '') + row.old),
              h('td', {
                class: 'ellipsis',
                style: row.status === '不变' ? '' : 'color: var(--link)'
              }, row.new),
              h('td', null, row.status),
              h('td', null, row.ext),
              h('td', { class: 'num' }, fmtBytes(row.size))));
          });
          tableHost.replaceChildren(h('div', { class: 'table-wrap' },
            h('table', { class: 'db-table' },
              h('thead', null, head), body)));
        }).catch(function (error) {
          setError(tableHost, error, load);
        });
      }

      function schedule() {
        clearTimeout(timer);
        timer = setTimeout(load, 450);
      }
      [template, prefix, suffix, find, replaceTo, startInput, widthInput,
        posText, posIdx, textmode, newExt, kwInput, kwText]
        .forEach(function (input) {
          input.addEventListener('input', schedule);
        });
      [recursiveCb, numberingCb, randomCb, ignoreExtCb, conflictCb, filterSel,
        caseSel, convertSel, fromRightCb, extCaseSel, kwAfterCb, dirRenameCb]
        .forEach(function (input) {
          input.addEventListener('change', schedule);
        });
      // 扩展名规则只在「忽略扩展名」开启时才作用到主名之外，切换时自动打开。
      function extRuleChanged() {
        if (extCaseSel.value || newExt.value.trim()) {
          ignoreExtCb.checked = true;
        }
      }
      extCaseSel.addEventListener('change', extRuleChanged);
      newExt.addEventListener('input', extRuleChanged);
      Object.keys(delTypeBoxes).forEach(function (key) {
        delTypeBoxes[key].addEventListener('change', schedule);
      });
      [delPos, delCnt, delKw, delKwCnt]
        .forEach(function (input) {
          input.addEventListener('input', schedule);
        });
      [delRight, delIncl, delDir]
        .forEach(function (input) {
          input.addEventListener('change', schedule);
        });
      scanBtn.addEventListener('click', load);
      execBtn.addEventListener('click', function () {
        var changed = lastRows.filter(function (row) {
          return row.new !== row.old;
        }).length;
        if (!changed) {
          toast('没有需要改名的文件', 'warning');
          return;
        }
        var payload = collect();
        if (!window.confirm('将对「' + payload.path + '」的 ' + changed
          + ' 个文件 / 文件夹执行改名。\n\n按当前预览执行（随机后缀不会再变）；'
          + '只修改名称，不改内容；'
          + '目标已存在或文件名非法的会跳过。\n\n确定继续？')) {
          return;
        }
        var plan = lastRows.filter(function (row) {
          return row.new !== row.old;
        }).map(function (row) {
          return { rel: row.rel, new: row.new };
        });
        execBtn.disabled = true;
        resultHost.replaceChildren();
        post('/api/rename-batch/execute', Object.assign(payload,
          { confirmed: true, plan: plan })).then(function (result) {
          var failed = result.failed || [];
          toast('改名完成：成功 ' + result.renamed
            + (failed.length ? '，失败 ' + failed.length : ''),
            failed.length ? 'warning' : 'success');
          if (failed.length) {
            var body = h('tbody');
            failed.forEach(function (item) {
              body.appendChild(h('tr', null,
                h('td', { class: 'ellipsis' }, item.old),
                h('td', null, item.error)));
            });
            resultHost.replaceChildren(h('div', {
              class: 'card', style: 'margin-bottom:12px'
            }, h('div', { class: 'row-title', style: 'margin-bottom:8px' },
              '未改名 · ' + failed.length + ' 个'),
            h('div', { class: 'table-wrap' },
              h('table', { class: 'db-table' }, h('thead', null, h('tr', null,
                h('th', null, '文件'), h('th', null, '原因'))), body))));
          } else if (textmode.value.trim()) {
            textmode.value = '';
          }
          load();
        }).catch(function (error) {
          execBtn.disabled = false;
          toast('改名失败：' + error.message, 'error');
        });
      });

      function buildFileTimeCard(host) {
        var modeSel = h('select', { class: 'select' },
          h('option', { value: 'set' }, '指定时间'),
          h('option', { value: 'offset' }, '偏移量'),
          h('option', { value: 'name' }, '按文件名时间'));
        var valueInput = h('input', {
          class: 'input', type: 'datetime-local', style: 'width:200px'
        });
        var offsetInput = h('input', {
          class: 'input', value: '+0h', placeholder: '如 +2h / -210m / -30s',
          style: 'width:140px'
        });
        var modeHint = h('span', { class: 'muted small' });
        var prevBtn = h('button', { class: 'btn', type: 'button' }, '预览');
        var applyBtn = h('button', {
          class: 'btn primary', type: 'button', disabled: true
        }, '应用');
        var statLine = h('div', {
          class: 'muted small', style: 'margin:8px 0'
        }, '只改文件的修改 / 创建 / 访问时间，不改文件名与内容；'
          + '范围沿用上方「文件夹 / 子文件夹 / 类型」。');
        var tableHost = h('div');

        function payload() {
          return {
            path: pathInput.value.trim(),
            recursive: recursiveCb.checked,
            filter: filterSel.value,
            mode: modeSel.value,
            value: valueInput.value,
            offset: offsetInput.value
          };
        }

        function syncMode() {
          var mode = modeSel.value;
          valueInput.disabled = mode !== 'set';
          offsetInput.disabled = mode !== 'offset';
          modeHint.textContent = mode === 'set'
            ? '按指定时间写入修改 / 创建 / 访问时间'
            : (mode === 'offset' ? '在当前时间上整体加减'
              : '从文件名里的日期推断，识别不到会跳过');
          applyBtn.disabled = true;
        }

        function renderRows(data) {
          var ready = data.ready || 0;
          statLine.textContent = '共 ' + fmtNumber(data.total) + ' 个文件 · 可应用 '
            + fmtNumber(ready)
            + (data.truncated ? '（超过 2000 个，已截断）' : '')
            + ' · 应用前会再次计算并二次确认';
          var rows = data.rows || [];
          if (!rows.length) {
            tableHost.replaceChildren(empty('文件夹内没有符合条件的文件。'));
            applyBtn.disabled = true;
            return;
          }
          var head = h('tr');
          ['序号', '文件', '当前修改时间', '当前创建时间', '新修改时间',
            '新创建时间', '状态'].forEach(function (title, index) {
            head.appendChild(h('th', {
              class: index === 0 ? 'num' : ''
            }, title));
          });
          var body = h('tbody');
          rows.forEach(function (row) {
            body.appendChild(h('tr', null,
              h('td', { class: 'num' }, row.index),
              h('td', { class: 'ellipsis' }, row.name),
              h('td', null, row.mtime),
              h('td', null, row.ctime),
              h('td', {
                style: row.status === '跳过' ? 'color: var(--danger)' : ''
              }, row.new_mtime),
              h('td', null, row.new_ctime),
              h('td', null, row.status)));
          });
          tableHost.replaceChildren(h('div', { class: 'table-wrap' },
            h('table', { class: 'db-table' },
              h('thead', null, head), body)));
          applyBtn.disabled = !ready;
        }

        function preview() {
          var data = payload();
          if (!data.path) {
            toast('请先在上方填写文件夹', 'warning');
            return;
          }
          setLoading(tableHost, '正在计算新时间…');
          applyBtn.disabled = true;
          post('/api/file-time/preview', data).then(renderRows)
            .catch(function (error) {
              setError(tableHost, error, preview);
            });
        }

        applyBtn.addEventListener('click', function () {
          var data = payload();
          if (!window.confirm('将修改「' + data.path + '」下文件的时间。\n\n'
            + '只改时间元数据，不改文件名与内容；无法计算的文件会跳过。\n\n'
            + '确定继续？')) {
            return;
          }
          applyBtn.disabled = true;
          post('/api/file-time/apply', Object.assign(data, { confirmed: true }))
            .then(function (result) {
              var failed = result.failed || [];
              toast('时间修改完成：成功 ' + result.applied
                + (failed.length ? '，跳过 ' + failed.length : ''),
                failed.length ? 'warning' : 'success');
              preview();
            }).catch(function (error) {
              applyBtn.disabled = false;
              toast('时间修改失败：' + error.message, 'error');
            });
        });
        prevBtn.addEventListener('click', preview);
        modeSel.addEventListener('change', syncMode);
        valueInput.addEventListener('input', function () {
          applyBtn.disabled = true;
        });
        offsetInput.addEventListener('input', function () {
          applyBtn.disabled = true;
        });
        syncMode();

        host.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
          h('div', { class: 'row-title', style: 'margin-bottom:8px' },
            '时间修改'),
          h('div', { class: 'toolbar' },
            h('span', { class: 'muted small' }, '方式'), modeSel,
            valueInput, offsetInput, modeHint,
            h('span', { style: 'flex:1' }), prevBtn, applyBtn),
          statLine,
          tableHost));
      }

      var renameHost = h('div');
      var timeHost = h('div');
      var tabRenameBtn = h('button', {
        class: 'btn primary', type: 'button'
      }, '批量改名');
      var tabTimeBtn = h('button', { class: 'btn', type: 'button' }, '时间修改');

      function showTab(which) {
        renameHost.style.display = which === 'time' ? 'none' : '';
        timeHost.style.display = which === 'time' ? '' : 'none';
        tabRenameBtn.className = which === 'time' ? 'btn' : 'btn primary';
        tabTimeBtn.className = which === 'time' ? 'btn primary' : 'btn';
        state.rename_batch.tab = which === 'time' ? 'time' : 'rename';
      }
      tabRenameBtn.addEventListener('click', function () { showTab('rename'); });
      tabTimeBtn.addEventListener('click', function () { showTab('time'); });

      root.appendChild(h('div', {
        class: 'toolbar', style: 'margin-bottom:12px'
      }, tabRenameBtn, tabTimeBtn));
      root.appendChild(renameHost);
      root.appendChild(timeHost);
      buildFileTimeCard(timeHost);
      showTab(state.rename_batch.tab === 'time' ? 'time' : 'rename');

      renameHost.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '文件夹'), pathInput,
          h('label', { class: 'check' }, recursiveCb, ' 包含子文件夹'),
          h('label', { class: 'check' }, dirRenameCb, ' 文件夹改名'),
          h('span', { class: 'muted small' }, '类型'), filterSel,
          scanBtn)));
      renameHost.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'row-title', style: 'margin-bottom:8px' }, '改名规则'),
        h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '模板'), template),
        h('div', { class: 'toolbar' },
          prefix, suffix,
          h('span', { class: 'muted small' }, '替换'), find,
          h('span', { class: 'muted small' }, '为'), replaceTo),
        h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '大小写'), caseSel,
          h('span', { class: 'muted small' }, '全半角'), convertSel),
        h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '扩展名'), extCaseSel,
          h('span', { class: 'muted small' }, '改为'), newExt,
          h('span', { class: 'muted small' },
            '（扩展名规则生效时会自动勾选「忽略扩展名」）')),
        h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '关键词插入'), kwInput,
          h('span', { class: 'muted small' }, '插入'), kwText,
          h('label', { class: 'check' }, kwAfterCb, ' 关键词后面')),
        h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '插入'), posText,
          h('span', { class: 'muted small' }, '位置'), posIdx,
          h('label', { class: 'check' }, fromRightCb, ' 从右往左'),
          h('span', { class: 'muted small' }, '（位置 0 表示开头 / 末尾）')),
        h('div', { class: 'toolbar' },
          h('label', { class: 'check' }, numberingCb, ' 序号'),
          h('span', { class: 'muted small' }, '起始'), startInput,
          h('span', { class: 'muted small' }, '位数'), widthInput,
          h('label', { class: 'check' }, randomCb, ' 随机数'),
          h('label', { class: 'check' }, ignoreExtCb, ' 忽略扩展名'),
          h('label', { class: 'check' }, conflictCb, ' 冲突加序号')),
        h('div', {
          class: 'toolbar', style: 'margin-top:10px'
        },
          h('span', { class: 'row-title' }, '按类型删除'),
          h('span', { class: 'muted small' },
            '勾选后从文件名里删掉这类字符；只改文件名，不删文件')),
        delTypeRow,
        delPosRow,
        delKwRow,
        h('div', {
          class: 'toolbar', style: 'margin-top:8px;margin-bottom:6px'
        },
          h('span', { class: 'row-title' }, '逐行命名'),
          h('span', { class: 'muted small' },
            '每行一个完整新文件名，优先于上面的规则'),
          h('span', { style: 'flex:1' }),
          fillTmBtn),
        textmode,
        h('div', { class: 'muted small', style: 'margin-top:6px' },
          '随机数、改名时间与目录改名仍在桌面端。')));
      renameHost.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'toolbar' },
          h('span', { class: 'row-title' }, '预览'),
          h('span', { style: 'flex:1' }),
          execBtn,
          h('span', { class: 'muted small' },
            '只改文件 / 文件夹名，不改内容；执行前二次确认'))));
      renameHost.appendChild(stats);
      renameHost.appendChild(tableHost);
      renameHost.appendChild(resultHost);
      load();
    }).catch(function (error) {
      setError(root, error, function () { renderRenameBatch(root); });
    });
  }

  function renderImageSorter(root) {
    setLoading(root, '正在读取源文件夹…');
    api('/api/image-sorter').then(function (data) {
      root.replaceChildren();
      var folders = data.folders || [];
      var targets = data.targets || [];
      var canWrite = !!data.can_write;
      var workDir = data.work_dir || '';
      var selected = new Set();
      var kindSel = h('select', { class: 'select' });
      [['all', '全部'], ['image', '图片'], ['video', '视频']]
        .forEach(function (pair) {
          kindSel.appendChild(h('option', {
            value: pair[0],
            selected: pair[0] === state.image_sorter.kind
          }, pair[1]));
        });
      var search = h('input', {
        class: 'input grow', placeholder: '搜索文件名',
        value: state.image_sorter.q || ''
      });
      var refreshBtn = h('button', {
        class: 'btn', type: 'button'
      }, '刷新扫描');
      var stats = h('div', {
        class: 'muted small', style: 'margin:8px 0'
      });
      var listHost = h('div');
      var failHost = h('div');
      var timer = null;

      var targetSel = h('select', { class: 'select' });
      if (!targets.length) {
        targetSel.appendChild(h('option', { value: '' },
          canWrite ? '工作目录下暂无子文件夹' : '未设置分类工作目录'));
        targetSel.disabled = true;
      } else {
        targets.forEach(function (item) {
          targetSel.appendChild(h('option', { value: item.name }, item.name));
        });
      }
      var moveBtn = h('button', { class: 'btn', type: 'button', disabled: true }, '移动');
      var copyBtn = h('button', { class: 'btn', type: 'button', disabled: true }, '复制');
      var renameBtn = h('button', { class: 'btn', type: 'button', disabled: true }, '重命名');
      var deleteBtn = h('button', {
        class: 'btn danger', type: 'button', disabled: true
      }, '移入删除');
      var selectedLabel = h('span', { class: 'muted small' }, '已选 0 个');

      function syncWrite() {
        selectedLabel.textContent = '已选 ' + selected.size + ' 个';
        var ready = canWrite && targets.length > 0;
        moveBtn.disabled = !ready || selected.size === 0;
        copyBtn.disabled = !ready || selected.size === 0;
        deleteBtn.disabled = !ready || selected.size === 0;
        renameBtn.disabled = selected.size !== 1;
      }

      function runWrite(path, body) {
        post(path, body).then(function (result) {
          toast(result.summary || '已完成', 'success');
          if (result.failed && result.failed.length) {
            failHost.replaceChildren(h('div', {
              class: 'muted small', style: 'margin-top:6px'
            }, '未处理 ' + result.failed.length + ' 个：'
              + result.failed.map(function (item) {
                return (item.name || item.ref) + '（' + item.reason + '）';
              }).join('；')));
          } else {
            failHost.replaceChildren();
          }
          selected.clear();
          load();
        }).catch(function (error) {
          toast('操作失败：' + error.message, 'error');
        });
      }

      moveBtn.addEventListener('click', function () {
        if (!selected.size || !targetSel.value) return;
        var ok = window.confirm('确定把选中的 ' + selected.size + ' 个文件移动到「'
          + targetSel.value + '」？\n源文件会离开当前文件夹，可在分类工作目录找回。');
        if (!ok) return;
        runWrite('/api/image-sorter/transfer', {
          refs: Array.from(selected), mode: 'move',
          target: targetSel.value, confirmed: true
        });
      });
      copyBtn.addEventListener('click', function () {
        if (!selected.size || !targetSel.value) return;
        var ok = window.confirm('确定把选中的 ' + selected.size + ' 个文件复制到「'
          + targetSel.value + '」？\n源文件保留在原处；同名文件会自动加 (1)(2) 后缀。');
        if (!ok) return;
        runWrite('/api/image-sorter/transfer', {
          refs: Array.from(selected), mode: 'copy',
          target: targetSel.value, confirmed: true
        });
      });
      deleteBtn.addEventListener('click', function () {
        if (!selected.size) return;
        var ok = window.confirm('确定把选中的 ' + selected.size
          + ' 个文件移入「删除」文件夹？\n不会永久删除，可在分类工作目录的「删除」文件夹找回。');
        if (!ok) return;
        runWrite('/api/image-sorter/delete', {
          refs: Array.from(selected), confirmed: true
        });
      });
      renameBtn.addEventListener('click', function () {
        if (selected.size !== 1) {
          toast('请先勾选 1 个文件', 'warning');
          return;
        }
        var ref = Array.from(selected)[0];
        var base = ref.split(':').slice(1).join(':').split('/').pop();
        var name = window.prompt('新文件名（不填扩展名则沿用原扩展名）', base);
        if (!name || !name.trim() || name.trim() === base) return;
        runWrite('/api/image-sorter/rename', {
          ref: ref, name: name.trim(), confirmed: true
        });
      });

      function previewItem(item) {
        var overlay = h('div', { class: 'preview-overlay' });
        overlay.addEventListener('click', function (event) {
          if (event.target === overlay) overlay.remove();
        });
        overlay.appendChild(h('div', { class: 'preview-box' },
          h('div', { class: 'preview-caption' },
            h('span', null, item.name),
            h('button', {
              class: 'btn mini', type: 'button',
              onclick: function () { overlay.remove(); }
            }, '关闭')),
          h('div', { class: 'preview-body' },
            h('img', {
              alt: '',
              src: '/api/image-sorter/preview?ref='
                + encodeURIComponent(item.ref) + '&token='
                + encodeURIComponent(token)
            }))));
        document.body.appendChild(overlay);
      }

      function load() {
        setLoading(listHost, '正在扫描源文件夹…');
        api('/api/image-sorter/scan?kind=' + encodeURIComponent(kindSel.value)
          + '&q=' + encodeURIComponent(state.image_sorter.q)
          + '&limit=500')
          .then(function (result) {
            stats.textContent = '共 ' + fmtNumber(result.total_files)
              + ' 个文件（图片 ' + fmtNumber(result.images) + ' / 视频 '
              + fmtNumber(result.videos) + '）· ' + fmtBytes(result.bytes)
              + ' · 显示 ' + fmtNumber(result.shown)
              + (result.truncated ? '（结果已截断，可缩小范围）' : '');
            if (!result.items.length) {
              listHost.replaceChildren(empty(
                folders.length
                  ? '没有符合条件的文件。'
                  : '未设置源文件夹。桌面端「媒体筛选」里添加后刷新。'));
              syncWrite();
              return;
            }
            var known = new Set(result.items.map(function (item) {
              return item.ref;
            }));
            Array.from(selected).forEach(function (ref) {
              if (!known.has(ref)) selected.delete(ref);
            });
            var list = h('div', { class: 'artifact-list' });
            result.items.forEach(function (item) {
              var check = h('input', {
                type: 'checkbox', checked: selected.has(item.ref)
              });
              check.addEventListener('change', function () {
                if (check.checked) selected.add(item.ref);
                else selected.delete(item.ref);
                syncWrite();
              });
              var actions = [];
              if (item.is_image) {
                actions.push(h('button', {
                  class: 'btn mini', type: 'button',
                  onclick: function () { previewItem(item); }
                }, '预览'));
              }
              list.appendChild(h('div', { class: 'artifact-row' },
                h('span', { style: 'width:22px' }, check),
                h('span', {
                  class: 'badge' + (item.is_image ? ' ok' : '')
                }, item.is_image ? '图片' : '视频'),
                h('span', {
                  class: 'artifact-name',
                  title: item.folder + '/' + item.name
                }, item.name),
                h('span', { class: 'muted small' }, item.folder),
                h('span', { class: 'muted small' }, fmtBytes(item.size)),
                h('span', { class: 'muted small' }, item.mtime),
                actions));
            });
            listHost.replaceChildren(list);
            syncWrite();
          }).catch(function (error) {
            setError(listHost, error, load);
          });
      }

      kindSel.addEventListener('change', function () {
        state.image_sorter.kind = kindSel.value;
        load();
      });
      refreshBtn.addEventListener('click', load);
      search.addEventListener('input', function () {
        clearTimeout(timer);
        timer = setTimeout(function () {
          state.image_sorter.q = search.value.trim();
          load();
        }, 320);
      });

      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '类型'), kindSel,
          search, refreshBtn),
        h('div', { class: 'muted small', style: 'margin-top:6px' },
          '源文件夹：' + (folders.length
            ? folders.map(function (item) { return item.path; }).join('　')
            : '未设置（桌面端「媒体筛选」添加）'))));
      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '目标文件夹'), targetSel,
          moveBtn, copyBtn, renameBtn, deleteBtn,
          h('span', { style: 'flex:1' }), selectedLabel),
        h('div', { class: 'muted small', style: 'margin-top:6px' },
          canWrite
            ? '分类工作目录：' + workDir
              + '　·　删除 = 移入「删除」文件夹（可找回）；单次最多 200 个文件；MD5 去重仍在桌面端。'
            : '复制 / 移动 / 删除需要先在桌面端「媒体筛选」设置分类工作目录；重命名可直接使用。'),
        failHost));
      root.appendChild(stats);
      root.appendChild(listHost);
      load();
    }).catch(function (error) {
      setError(root, error, function () { renderImageSorter(root); });
    });
  }

function renderLivestreamSummary(root) {
    setLoading(root, '正在读取转录文件与总结设置…');
    api('/api/livestream-summary').then(function (data) {
      root.replaceChildren();
      var settings = data.settings || {};
      var transcripts = data.transcripts || [];
      var saved = data.saved || [];

      var fileSelect = h('select', { class: 'select grow' });
      if (!transcripts.length) {
        fileSelect.appendChild(h('option', { value: '' },
          data.work_dir ? '工作文件夹内没有转录文件（TXT / SRT）'
            : '未设置工作文件夹'));
        fileSelect.disabled = true;
      } else {
        transcripts.forEach(function (item) {
          fileSelect.appendChild(h('option', { value: item.rel },
            item.name + '（' + item.mtime + '）'));
        });
      }
      var modeSel = h('select', { class: 'select' },
        h('option', { value: 'ai' }, '模型接口'),
        h('option', { value: 'local' }, '本地模型（CPU / GPU）'),
        h('option', { value: 'rule' }, '本地规则保底'));
      modeSel.value = ['ai', 'local', 'rule'].indexOf(settings.mode) >= 0
        ? settings.mode : 'ai';
      var startBtn = h('button', { class: 'btn primary', type: 'button' },
        '开始总结');
      var statusText = h('span', { class: 'muted small' }, '空闲');
      var jobHost = h('div');
      var resultHost = h('div');
      var running = false;

      startBtn.addEventListener('click', function () {
        if (running) return;
        if (!fileSelect.value) {
          toast('请先选择转录文件', 'warning');
          return;
        }
        running = true;
        startBtn.disabled = true;
        startBtn.textContent = '总结中…';
        statusText.textContent = modeSel.value === 'rule'
          ? '本地规则总结中…' : '调用模型总结中…';
        resultHost.replaceChildren();
        modeSel.disabled = true;
        saveBtn.disabled = true;
        testBtn.disabled = true;
        localForm.setDisabled(true);
        persistSettings().then(function () {
          return post('/api/tools/livestream-summary/run', {
            rel: fileSelect.value, mode: modeSel.value
          });
        }).then(function (result) {
          watchJob(jobHost, result.job, function (job) {
            var outcome = job.result || {};
            statusText.textContent = outcome.summary || '已完成';
            toast(outcome.summary || '总结完成',
              outcome.problems && outcome.problems.length ? 'warning' : 'success');
            var stats = outcome.stats || {};
            var info = h('div', { class: 'muted small', style: 'margin:8px 0' });
            info.textContent = [
              outcome.name,
              stats.chunks ? stats.chunks + ' 块' : '',
              stats.requests ? stats.requests + ' 次请求' : '',
              stats.chars ? stats.chars + ' 字' : '',
              stats.date ? '日期 ' + stats.date : ''
            ].filter(Boolean).join(' · ');
            resultHost.replaceChildren(
              h('div', { class: 'card' },
                h('div', { class: 'row-title', style: 'margin-bottom:6px' },
                  '总结结果'),
                info,
                h('pre', {
                  class: 'log-box', style: 'max-height:420px'
                }, outcome.text || ''),
                (outcome.problems && outcome.problems.length)
                  ? h('div', { class: 'muted small',
                    style: 'margin-top:6px;color:var(--danger,#c62828)' },
                    '格式校验：' + outcome.problems.join('；'))
                  : null));
          }, {
            cancelable: true,
            onSettled: function (job) {
              running = false;
              startBtn.disabled = false;
              startBtn.textContent = '开始总结';
              modeSel.disabled = false;
              saveBtn.disabled = false;
              syncMode();
              if (job.status === 'error') {
                statusText.textContent = '失败';
                toast('总结失败：' + (job.error || '未知错误'), 'error');
              } else if (job.status === 'cancelled') {
                statusText.textContent = '已取消';
              }
            }
          });
        }).catch(function (error) {
          running = false;
          startBtn.disabled = false;
          startBtn.textContent = '开始总结';
          statusText.textContent = '失败';
          modeSel.disabled = false;
          saveBtn.disabled = false;
          syncMode();
          toast('总结失败：' + error.message, 'error');
        });
      });

      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '转录文件'), fileSelect,
          h('span', { class: 'muted small' }, '模式'), modeSel,
          startBtn, statusText),
        h('div', { class: 'muted small', style: 'margin-top:6px' },
          '工作目录：' + (data.work_dir || '未设置')
          + ' · 输出：' + (data.output_dir || '-')
          + ' · 结果会自动保存到输出目录。'),
        jobHost, resultHost));

      var baseInput = h('input', {
        class: 'input', type: 'text', value: settings.base_url || '',
        placeholder: 'https://api.openai.com/v1'
      });
      var modelInput = h('input', {
        class: 'input', type: 'text', value: settings.model || '',
        placeholder: '模型名称'
      });
      var chunkInput = h('input', {
        class: 'input', type: 'number', value: String(settings.chunk_chars || 6000),
        min: '2000', max: '20000', step: '500'
      });
      var keyInput = h('input', {
        class: 'input', type: 'password',
        placeholder: settings.has_key ? '已保存（留空不修改）' : 'API Key'
      });
      var saveBtn = h('button', { class: 'btn', type: 'button' }, '保存设置');
      var testBtn = h('button', { class: 'btn', type: 'button' }, '测试连接');
      var settingsHost = h('div', { class: 'muted small',
        style: 'margin-top:8px' }, settings.has_key
        ? 'API Key 已保存（本机加密，不回显）。' : '尚未保存 API Key。');
      var localForm = window.FMJJLocalLLM.createOptionsForm(
        h, data.local_fields || [], settings.local || {}, data.local_runtime || {});
      var localBox = h('div', { style: 'margin-top:12px' },
        h('div', { class: 'row-title', style: 'margin-bottom:8px' }, '本地推理配置'),
        localForm.element,
        h('div', { class: 'muted small', style: 'margin-top:8px' },
          '模块和模型请在全局设置中按需准备；CPU 线程数填 0 表示自动，GPU 层数只在 GPU 模式生效。'));
      var cloudFields = h('div', { class: 'form-grid' },
        h('label', null, h('span', { class: 'muted small' }, '接口地址'), baseInput),
        h('label', null, h('span', { class: 'muted small' }, '模型名称'), modelInput),
        h('label', null, h('span', { class: 'muted small' }, 'API Key'), keyInput));

      function persistSettings() {
        return post('/api/tools/livestream-summary/settings', {
          base_url: baseInput.value, model: modelInput.value,
          chunk_chars: Number(chunkInput.value) || 6000,
          mode: modeSel.value, api_key: modeSel.value === 'ai' ? keyInput.value : '',
          local: localForm.read()
        }).then(function (result) {
          keyInput.value = '';
          settingsHost.textContent = result.summary;
          return result;
        });
      }
      function syncMode() {
        cloudFields.hidden = modeSel.value !== 'ai';
        localBox.hidden = modeSel.value !== 'local';
        localForm.setDisabled(modeSel.value !== 'local' || running);
        chunkInput.disabled = modeSel.value === 'rule' || running;
        testBtn.disabled = modeSel.value === 'rule' || running;
      }
      modeSel.addEventListener('change', syncMode);

      saveBtn.addEventListener('click', function () {
        saveBtn.disabled = true;
        persistSettings().then(function (result) {
          toast(result.summary, 'success');
        }).catch(function (error) {
          toast('保存失败：' + error.message, 'error');
        }).then(function () { saveBtn.disabled = false; });
      });

      testBtn.addEventListener('click', function () {
        if (running) return;
        running = true;
        startBtn.disabled = true;
        modeSel.disabled = true;
        saveBtn.disabled = true;
        testBtn.disabled = true;
        testBtn.textContent = '测试中…';
        localForm.setDisabled(true);
        persistSettings().then(function () {
          return post('/api/tools/livestream-summary/test', {});
        }).then(function (result) {
          watchJob(jobHost, result.job, function (job) {
            var outcome = job.result || {};
            toast(outcome.summary || outcome.message, 'success');
            settingsHost.textContent = outcome.message || outcome.summary;
          }, { cancelable: true, indeterminate: true,
            onSettled: function (job) {
              running = false;
              startBtn.disabled = false;
              modeSel.disabled = false;
              saveBtn.disabled = false;
              testBtn.textContent = '测试连接';
              syncMode();
              if (job.status === 'error') settingsHost.textContent = '连接失败：' + job.error;
              if (job.status === 'cancelled') settingsHost.textContent = '已取消';
            }
          });
        }).catch(function (error) {
          running = false;
          startBtn.disabled = false;
          modeSel.disabled = false;
          saveBtn.disabled = false;
          settingsHost.textContent = '连接失败：' + error.message;
          toast('连接失败：' + error.message, 'error');
          testBtn.textContent = '测试连接';
          syncMode();
        });
      });

      function kv(label, value) {
        return h('div', { class: 'kv' },
          h('span', { class: 'muted' }, label),
          h('span', { class: 'ellipsis', title: String(value) }, value));
      }

      var savedHost = h('div');
      var savedSelect = h('select', { class: 'select grow' });
      if (!saved.length) {
        savedSelect.appendChild(h('option', { value: '' }, '暂无已保存总结'));
        savedSelect.disabled = true;
      } else {
        saved.forEach(function (item) {
          savedSelect.appendChild(h('option', { value: item.name },
            item.name + '（' + item.mtime + '）'));
        });
      }

      var grid = h('div', { class: 'grid cols-2' });
      grid.appendChild(h('div', { class: 'card' },
        h('div', { class: 'row-title', style: 'margin-bottom:8px' },
          '模型设置'),
        cloudFields,
        h('div', { class: 'form-grid', style: 'margin-top:8px' },
          h('label', null, h('span', { class: 'muted small' }, '每块字数'),
            chunkInput)),
        localBox,
        h('div', { class: 'toolbar', style: 'margin-top:8px' },
          saveBtn, testBtn),
        kv('已保存模式', settings.mode === 'rule' ? '本地规则（不调用模型）'
          : settings.mode === 'local' ? '本地模型（CPU / GPU）' : '模型接口'),
        settingsHost));
      syncMode();
      grid.appendChild(h('div', { class: 'card' },
        h('div', { class: 'row-title', style: 'margin-bottom:8px' },
          '已保存总结'),
        h('div', { class: 'toolbar' }, savedSelect,
          h('span', { class: 'muted small' }, '只读预览')),
        savedHost));

      function loadSaved(name) {
        if (!name) return;
        setLoading(savedHost, '正在读取总结…');
        api('/api/livestream-summary/saved?name=' + encodeURIComponent(name))
          .then(function (result) {
            savedHost.replaceChildren(
              h('div', { class: 'muted small', style: 'margin:8px 0 6px' },
                result.name + ' · ' + fmtNumber(result.text.length) + ' 字'),
              h('pre', {
                class: 'log-box', style: 'max-height:420px'
              }, result.text));
          }).catch(function (error) {
            setError(savedHost, error, function () { loadSaved(name); });
          });
      }
      savedSelect.addEventListener('change', function () {
        loadSaved(savedSelect.value);
      });
      root.appendChild(grid);
    }).catch(function (error) {
      setError(root, error, function () { renderLivestreamSummary(root); });
    });
  }

  function renderLocalWiki(root) {
    setLoading(root, '正在读取资料站状态…');
    api('/api/localwiki').then(function (data) {
      root.replaceChildren();
      var d = data.data || {};
      var service = data.service || {};
      var a = data.analytics || {};
      var latest = a.latest || {};
      function metric(label, value) {
        return h('div', { class: 'metric' },
          h('div', { class: 'label' }, label),
          h('div', { class: 'value', title: String(value) }, value));
      }
      function num(value) {
        return value == null ? '-' : fmtNumber(value);
      }
      var stats = h('div', { class: 'grid cols-4', style: 'margin-bottom:12px' });
      stats.appendChild(statCard('GitHub 登录',
        data.github.logged_in ? '已登录' : '未登录',
        '登录后可部署'));
      stats.appendChild(statCard('服务状态',
        service.site_running ? '运行中'
          : (service.api_running ? 'API 运行中' : '未运行'),
        service.url || ('站点端口 ' + (service.site_port || '-'))));
      stats.appendChild(statCard('站点数据',
        d.exists ? '已重建' : '未重建',
        d.exists ? d.mtime : '桌面端重建'));
      stats.appendChild(statCard('本地图片', fmtNumber(d.local_images),
        '优先使用本地图'));
      root.appendChild(stats);

      var wikiJobHost = h('div', { style: 'margin-bottom:12px' });
      var deployBtn = h('button', { class: 'btn primary', type: 'button' },
        '一键部署并启动');
      var rebuildBtn = h('button', { class: 'btn', type: 'button' },
        '重建站点数据');
      var startBtn = h('button', { class: 'btn', type: 'button' },
        '启动服务');
      var stopBtn = h('button', { class: 'btn', type: 'button' },
        '停止服务');
      var collectBtn = h('button', { class: 'btn', type: 'button' },
        '采集今日');
      var openSiteBtn = h('button', { class: 'btn', type: 'button' },
        '打开站点');
      var wikiBusy = false;

      function wikiAction(button, url, body, confirmText) {
        if (wikiBusy) return;
        if (confirmText && !window.confirm(confirmText)) return;
        wikiBusy = true;
        var label = button.textContent;
        button.disabled = true;
        button.textContent = '执行中…';
        post(url, body || {}).then(function (result) {
          if (!result || !result.job) {
            toast((result && result.summary) || '已完成', 'success');
            wikiBusy = false;
            button.disabled = false;
            button.textContent = label;
            renderLocalWiki(root);
            return;
          }
          watchJob(wikiJobHost, result.job, function (job) {
            var outcome = job.result || {};
            toast(outcome.summary || '已完成', 'success');
            renderLocalWiki(root);
          }, {
            cancelable: true,
            onSettled: function (job) {
              wikiBusy = false;
              button.disabled = false;
              button.textContent = label;
              if (job.status === 'error') {
                toast('执行失败：' + (job.error || '未知错误'), 'error');
              }
            }
          });
        }).catch(function (error) {
          wikiBusy = false;
          button.disabled = false;
          button.textContent = label;
          toast('执行失败：' + error.message, 'error');
        });
      }

      deployBtn.addEventListener('click', function () {
        wikiAction(deployBtn, '/api/tools/localwiki/deploy', { confirmed: true },
          '一键部署会：拉取 webwiki / webwiki-api 最新代码 → 安装 Python 与'
          + ' Node 依赖 → 重建站点数据 → 构建前端 → 重启本地站点服务。'
          + '需要桌面端已登录 GitHub。确认继续？');
      });
      rebuildBtn.addEventListener('click', function () {
        wikiAction(rebuildBtn, '/api/tools/localwiki/rebuild',
          { confirmed: true },
          '将重新读取本地腾讯文档快照并重建站点数据库、重新构建前端。确认继续？');
      });
      startBtn.addEventListener('click', function () {
        wikiAction(startBtn, '/api/tools/localwiki/service/start', {});
      });
      stopBtn.addEventListener('click', function () {
        if (!window.confirm('停止本地站点服务？WebUI 本身不受影响。')) return;
        wikiAction(stopBtn, '/api/tools/localwiki/service/stop', {});
      });
      collectBtn.addEventListener('click', function () {
        wikiAction(collectBtn, '/api/tools/localwiki/collect', {});
      });
      openSiteBtn.addEventListener('click', function () {
        if (service.url) window.open(service.url, '_blank', 'noopener');
      });

      var repoText = (data.repos || []).map(function (repo) {
        return repo.text;
      }).join('　');
      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'row-title', style: 'margin-bottom:8px' },
          '代码与目录'),
        h('div', { class: 'kv' },
          h('span', { class: 'muted' }, '仓库'),
          h('span', { style: 'text-align:right' }, repoText || '-')),
        h('div', { class: 'kv' },
          h('span', { class: 'muted' }, '资料站目录'),
          h('span', { class: 'ellipsis', title: data.paths.root || '' },
            data.paths.root || '-')),
        h('div', { class: 'kv' },
          h('span', { class: 'muted' }, '端口'),
          h('span', null, 'API ' + (service.api_port || '-')
            + ' / 站点 ' + (service.site_port || '-'))),
        h('div', { class: 'kv' },
          h('span', { class: 'muted' }, '数据量'),
          h('span', null, d.exists
            ? ('歌曲 ' + num(d.songs) + ' / 活动 ' + num(d.events)
              + ' / 周边 ' + num(d.goods) + ' / 动态 ' + num(d.posts)
              + (d.live ? ' / 直播记录 ' + num(d.live) : ''))
            : '-')),
        h('div', { class: 'toolbar', style: 'margin-top:8px' },
          deployBtn, rebuildBtn, startBtn, stopBtn, collectBtn, openSiteBtn,
          h('span', { class: 'muted small' }, '改端口仍在桌面端')),
        wikiJobHost));

      var analyticsCard = h('div', {
        class: 'card', style: 'margin-bottom:12px'
      }, h('div', { class: 'row-title', style: 'margin-bottom:8px' },
        '站点访问统计'));
      analyticsCard.appendChild(h('div', {
        class: 'metric-grid', style: 'margin-bottom:8px'
      },
        metric('统计口径', a.scope_label || '-'),
        metric('最近日期', latest.date || '-'),
        metric('访客', num(latest.visitors)),
        metric('打开', num(latest.sessions)),
        metric('浏览量', num(latest.pageviews)),
        metric('在线', num(latest.online))));
      var pwdInput = h('input', {
        class: 'input', type: 'password', placeholder: '后台访问密码',
        style: 'flex:1;min-width:140px'
      });
      var pwdBtn = h('button', { class: 'btn', type: 'button' },
        data.has_password ? '更新密码' : '保存密码');
      pwdBtn.addEventListener('click', function () {
        if (!pwdInput.value.trim()) {
          toast('请填写后台密码', 'warning');
          return;
        }
        pwdBtn.disabled = true;
        post('/api/tools/localwiki/password',
          { password: pwdInput.value }).then(function (result) {
            pwdInput.value = '';
            toast(result.summary || '已保存', 'success');
          }).catch(function (error) {
            toast('保存失败：' + error.message, 'error');
          }).then(function () { pwdBtn.disabled = false; });
      });
      var collectBtn2 = h('button', { class: 'btn primary', type: 'button' },
        '采集今日');
      collectBtn2.addEventListener('click', function () {
        wikiAction(collectBtn2, '/api/tools/localwiki/collect', {});
      });
      analyticsCard.appendChild(h('div', { class: 'toolbar',
        style: 'margin-bottom:8px' },
        h('span', { class: 'muted small' },
          data.has_password ? '后台密码已保存（加密，不回显）' : '尚未保存后台密码'),
        pwdInput, pwdBtn, collectBtn2));
      analyticsCard.appendChild(h('div', {
        class: 'muted small', style: 'margin-bottom:8px'
      }, a.has_today ? '今日已采集'
        : '今日未采集（点「采集今日」立即采集）'));
      var history = a.history || [];
      if (history.length) {
        var head = h('tr', null,
          h('th', null, '日期'), h('th', null, '口径'),
          h('th', { class: 'num' }, '访客'), h('th', { class: 'num' }, '打开'),
          h('th', { class: 'num' }, '浏览量'));
        var body = h('tbody');
        history.forEach(function (row) {
          body.appendChild(h('tr', null,
            h('td', null, row.date || '-'),
            h('td', null, row.scope || '-'),
            h('td', { class: 'num' }, num(row.visitors)),
            h('td', { class: 'num' }, num(row.opens)),
            h('td', { class: 'num' }, num(row.views))));
        });
        analyticsCard.appendChild(h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' },
            h('thead', null, head), body)));
      } else {
        analyticsCard.appendChild(empty('暂无访问统计。桌面端采集后生成。'));
      }
      var fetchLog = a.fetch_log || [];
      if (fetchLog.length) {
        analyticsCard.appendChild(h('div', {
          class: 'row-title', style: 'margin:12px 0 6px'
        }, '采集日志'));
        var logBody = h('tbody');
        fetchLog.slice(0, 5).forEach(function (row) {
          logBody.appendChild(h('tr', null,
            h('td', null, row.time || '-'),
            h('td', null, h('span', {
              class: 'badge ' + (row.ok ? 'ok' : 'warn')
            }, row.ok ? '成功' : '失败')),
            h('td', { class: 'ellipsis' }, row.message || '')));
        });
        analyticsCard.appendChild(h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' },
            h('thead', null, h('tr', null,
              h('th', null, '时间'), h('th', null, '结果'),
              h('th', null, '说明'))), logBody)));
      }
      root.appendChild(analyticsCard);

      var docs = data.docs || [];
      var docSelect = h('select', { class: 'select grow' });
      docs.forEach(function (doc) {
        docSelect.appendChild(h('option', { value: doc.url || '' },
          doc.name + (doc.url ? '' : '（无链接）')));
      });
      var openDoc = h('button', {
        class: 'btn', type: 'button', disabled: !docs.some(function (doc) {
          return doc.url;
        })
      }, '打开预览');
      openDoc.addEventListener('click', function () {
        if (docSelect.value) {
          window.open(docSelect.value, '_blank', 'noopener');
        }
      });
      root.appendChild(h('div', { class: 'card' },
        h('div', { class: 'row-title', style: 'margin-bottom:8px' },
          '腾讯文档预览'),
        h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '源文档'), docSelect, openDoc),
        h('div', { class: 'muted small', style: 'margin-top:6px' },
          '站点数据直接读取「腾讯文档」已同步的本地表格；'
          + '改端口仍在桌面端。')));
    }).catch(function (error) {
      setError(root, error, function () { renderLocalWiki(root); });
    });
  }

  function openEntryDetail(item) {
      var overlay = h('div', { class: 'preview-overlay' });
      var box = h('div', { class: 'preview-box' });
      var caption = h('div', { class: 'preview-caption' },
        h('span', { class: 'ellipsis' }, item.title || item.file || '条目详情'),
        h('button', { class: 'btn', type: 'button' }, '关闭'));
      var closeBtn = caption.lastChild;
      var body = h('div', {
        class: 'preview-body', style: 'padding:12px 14px'
      }, '正在读取导出文件…');
      box.appendChild(caption);
      box.appendChild(body);
      overlay.appendChild(box);
      document.body.appendChild(overlay);

      function close() { overlay.remove(); }
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

      api('/api/assets/entry?path=' + encodeURIComponent(item.path))
        .then(function (data) {
          var grid = h('div');
          (data.fields || []).forEach(function (field) {
            grid.appendChild(h('div', { class: 'kv' },
              h('span', { class: 'muted' }, field.label),
              h('span', { class: 'ellipsis', title: field.value }, field.value)));
          });
          if (!(data.fields || []).length) {
            grid.appendChild(h('div', { class: 'muted small' },
              '导出 JSON 里没有可识别的字段。'));
          }
          var pre = h('pre', {
            class: 'job-log',
            style: 'max-height:300px;overflow:auto;white-space:pre-wrap;'
              + 'word-break:break-all;margin-top:8px'
          });
          pre.textContent = data.text || '';
          var actions = h('div', { class: 'toolbar', style: 'margin-top:8px' });
          if (item.url) {
            actions.appendChild(h('a', {
              class: 'btn', href: item.url, target: '_blank',
              rel: 'noopener noreferrer'
            }, '打开原链接'));
          }
          actions.appendChild(h('span', { class: 'muted small' },
            data.file + ' · ' + fmtBytes(data.size) + ' · ' + data.mtime));
          body.replaceChildren(
            grid, actions,
            h('div', { class: 'muted small', style: 'margin-top:8px' },
              '导出 JSON 原文' + (data.truncated ? '（已截断，最多 200KB）' : '')),
            pre);
        }).catch(function (error) {
          body.replaceChildren(h('div', { class: 'muted' },
            '详情读取失败：' + error.message));
        });
    }

  function renderAssets(root) {
    setLoading(root, '正在读取资产概览…');
    api('/api/assets/summary').then(function (summary) {
      var tabs = h('div', { class: 'tabs' });
      var host = h('div');
      [['entries', '资源归档'], ['db', '采集记录'], ['local', '本地文件'], ['offline', '活动视频']].forEach(function (pair) {
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
    var pageItems = [];
    var pageMeta = null;
    var rowNodes = new Map();
    var knownItems = state.assets.entryKnown || (state.assets.entryKnown = new Map());
    var tableBody = null;
    var requestSeq = 0;
    var loading = false;
    var STATUS_BADGE = {
      ok: ['已完整', 'ok'], partial: ['部分下载', 'warn'],
      missing: ['该下未下', 'danger'], text: ['纯文本', ''],
      nodl: ['不可下载', 'warn']
    };

    function updateButtons() {
      dlBtn.disabled = running || selected.size === 0;
      dlBtn.textContent = running ? '下载中…' : '下载选中';
      selectedLabel.textContent = selected.size ? '已选 ' + selected.size + ' 条' : '';
      var checked = 0;
      pageItems.forEach(function (item) {
        var node = rowNodes.get(item.path);
        var isChecked = selected.has(item.path);
        if (node) node.element.querySelector('input[type="checkbox"]').checked = isChecked;
        if (isChecked) checked += 1;
      });
      selectAll.disabled = loading || !pageItems.length;
      selectAll.checked = !!pageItems.length && checked === pageItems.length;
      selectAll.indeterminate = checked > 0 && checked < pageItems.length;
    }

    function scopeKey() {
      return JSON.stringify([state.assets.page, state.assets.q,
        state.assets.platform, state.assets.status || '']);
    }

    function updateCounts() {
      var parts = [];
      if (pageMeta && pageMeta.counts) {
        ['ok', 'partial', 'missing', 'text', 'nodl'].forEach(function (key) {
          if (pageMeta.counts[key]) {
            parts.push(((pageMeta.labels || {})[key] || key) + ' ' + pageMeta.counts[key]);
          }
        });
      }
      countsLabel.textContent = parts.join(' · ');
    }

    function matchesView(item) {
      if (!item) return false;
      var keyword = (state.assets.q || '').toLowerCase();
      return (!state.assets.platform || item.platform === state.assets.platform)
        && (!keyword || ((item.title || '') + ' ' + (item.author || '')).toLowerCase().indexOf(keyword) >= 0)
        && (!state.assets.status || item.status === state.assets.status);
    }

    function refreshRows(paths) {
      var scope = scopeKey();
      var seq = requestSeq;
      return post('/api/assets/entries/refresh', { paths: paths }).then(function (data) {
        if (!host.isConnected || seq !== requestSeq || scope !== scopeKey()) return;
        var updates = new Map((data.items || []).map(function (item) { return [item.path, item]; }));
        var removed = new Set(data.removed || []);
        var membershipChanged = false;
        paths.forEach(function (path) {
          var previous = knownItems.get(path);
          var next = updates.get(path);
          if (removed.has(path)) {
            selected.delete(path);
            knownItems.delete(path);
          } else if (next) {
            knownItems.set(path, next);
          } else {
            return;
          }
          var wasVisible = matchesView(previous);
          var isVisible = matchesView(next);
          if (wasVisible !== isVisible || removed.has(path)) membershipChanged = true;
          if (pageMeta && pageMeta.counts && previous && next && wasVisible && isVisible
              && previous.status !== next.status) {
            pageMeta.counts[previous.status] = Math.max(0, (pageMeta.counts[previous.status] || 0) - 1);
            pageMeta.counts[next.status] = (pageMeta.counts[next.status] || 0) + 1;
          }
        });
        if (membershipChanged) return load();
        if (!pageMeta) return;
        pageMeta.items = pageItems.map(function (item) { return updates.get(item.path) || item; });
        renderPage(pageMeta);
      }).catch(function (error) {
        if (host.isConnected && scope === scopeKey()) {
          toast('资源状态刷新失败：' + error.message, 'error');
        }
      });
    }

    function load() {
      var seq = ++requestSeq;
      var scope = scopeKey();
      loading = true;
      updateButtons();
      tableHost.setAttribute('aria-busy', 'true');
      if (!tableBody) setLoading(tableHost, '正在读取资源归档…');
      var url = '/api/assets/entries-status?page=' + state.assets.page
        + '&page_size=20&q=' + encodeURIComponent(state.assets.q)
        + '&platform=' + encodeURIComponent(state.assets.platform)
        + '&status=' + encodeURIComponent(state.assets.status || '');
      return api(url)
        .then(function (data) {
          if (!host.isConnected || seq !== requestSeq || scope !== scopeKey()) return;
          renderPage(data);
        }).catch(function (error) {
          if (!host.isConnected || seq !== requestSeq) return;
          if (tableBody) toast('资源清单刷新失败：' + error.message, 'error');
          else setError(tableHost, error, load);
        }).finally(function () {
          if (seq !== requestSeq) return;
          loading = false;
          tableHost.removeAttribute('aria-busy');
          updateButtons();
        });
    }

    function makeRow(item) {
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
            var detailBtn = h('button', {
              class: 'btn', type: 'button', title: '查看导出 JSON 字段与原文'
            }, '详情');
            detailBtn.addEventListener('click', function () {
              openEntryDetail(item);
            });
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
                return refreshRows([item.path]);
              }).catch(function (error) {
                toast('操作失败：' + error.message, 'error');
              }).finally(function () {
                noDlBtn.disabled = false;
              });
            });
            return h('tr', { dataset: { entryPath: item.path } },
              h('td', null, check),
              h('td', { class: 'ellipsis' }, title),
              h('td', null, item.author || '-'),
              h('td', null, platformLabel(item.platform)),
              h('td', null, statusCell),
              h('td', { class: 'muted small' }, mediaInfo || '-'),
              h('td', null, fmtTime(item.publish_time)),
              h('td', { class: 'num' }, fmtNumber(item.play)),
              h('td', { class: 'num' }, fmtNumber(item.like)),
              h('td', { class: 'ellipsis' },
                h('a', {
                  href: '#', title: '查看详情', onclick: function (event) {
                    event.preventDefault();
                    openEntryDetail(item);
                  }
                }, item.file || '-')),
              h('td', null, h('div', { class: 'toolbar' }, detailBtn, noDlBtn)));
    }

    function renderPage(data) {
      var scrollX = window.scrollX, scrollY = window.scrollY;
      pageMeta = data;
      pageItems = data.items || [];
      state.assets.page = data.page;
      updateCounts();
      if (!pageItems.length) {
        tableHost.replaceChildren(empty('没有匹配的导出 JSON。可清空搜索词或切换平台。'));
        tableBody = null;
        rowNodes.clear();
        pagerHost.replaceChildren();
        updateButtons();
        return;
      }
      if (!tableBody) {
        tableBody = h('tbody');
        tableHost.replaceChildren();
        if (data.truncated) {
          tableHost.appendChild(h('div', { class: 'muted small', style: 'margin:6px 2px' },
            '资源归档仅展示前 2000 条 JSON 概览。'));
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
              tableBody)));
      }
      var desired = new Set(pageItems.map(function (item) { return item.path; }));
      rowNodes.forEach(function (entry, path) {
        if (!desired.has(path)) {
          entry.element.remove();
          rowNodes.delete(path);
        }
      });
      var cursor = tableBody.firstChild;
      pageItems.forEach(function (item) {
        knownItems.set(item.path, item);
        var fingerprint = JSON.stringify(item);
        var entry = rowNodes.get(item.path);
        if (!entry || entry.fingerprint !== fingerprint) {
          var node = makeRow(item);
          if (entry) {
            if (cursor === entry.element) cursor = node;
            entry.element.replaceWith(node);
          }
          entry = { element: node, fingerprint: fingerprint };
          rowNodes.set(item.path, entry);
        }
        if (entry.element === cursor) cursor = cursor.nextSibling;
        else tableBody.insertBefore(entry.element, cursor);
      });
      updateButtons();
      pager(pagerHost, data.page, data.pages, data.total, function (page) {
        state.assets.page = page;
        load();
      });
      window.scrollTo(scrollX, scrollY);
    }

    selectAll.addEventListener('change', function () {
      pageItems.forEach(function (item) {
        if (selectAll.checked) selected.add(item.path);
        else selected.delete(item.path);
      });
      updateButtons();
    });

    statusSel.addEventListener('change', function () {
      state.assets.status = statusSel.value;
      state.assets.page = 1;
      load();
    });

    dlBtn.addEventListener('click', function () {
      if (!selected.size || running) return;
      var paths = Array.from(selected).slice(0, 50);
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
        }, {
          cancelable: true,
          onSettled: function (job) {
            running = false;
            ((job.result || {}).results || []).forEach(function (result) {
              if (result.ok) selected.delete(result.file);
            });
            updateButtons();
            if (job.status !== 'done') showResult(job);
            refreshRows(paths);
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
    var actions = h('div', { class: 'toolbar' });
    var tableHost = h('div');
    var pagerHost = h('div', { class: 'pager' });
    var platform = h('select', { class: 'select' });
    var search = h('input', { class: 'input grow', placeholder: '搜索标题、作者、正文或作品 ID', value: state.assets.dbQ });
    var columns = null;
    var selected = new Set();
    var rows = [];
    var busy = false;
    var loading = false;
    var queryPending = false;
    var requestSeq = 0;
    var selectAll = h('button', { class: 'btn', type: 'button', onclick: function () {
      rows.forEach(function (row) { selected.add(row.dedup_key); });
      syncChecks();
    } }, '全选');
    var clear = h('button', { class: 'btn', type: 'button', onclick: function () {
      selected.clear();
      syncChecks();
    } }, '清除');
    var remove = h('button', { class: 'btn danger', type: 'button', onclick: function () {
      deleteRows(Array.from(selected));
    } }, '删除所选');
    var count = h('span', { class: 'muted small' }, '已勾选 0 条');
    var refresh = h('button', { class: 'btn', type: 'button', onclick: function () {
      api('/api/assets/db/tables').then(function (data) {
        updateTables(data.tables);
        return load(true);
      }).catch(function (error) { toast(error.message, 'error'); });
    } }, '刷新');
    actions.append(selectAll, clear, remove, count);
    controls.append(platform, search, refresh);
    host.append(controls, actions, tableHost, pagerHost);

    function syncChecks() {
      tableHost.querySelectorAll('input[data-db-key]').forEach(function (box) {
        box.checked = selected.has(box.dataset.dbKey);
      });
      syncControls();
    }

    function syncControls() {
      var disabled = busy || loading;
      platform.disabled = search.disabled = refresh.disabled = disabled;
      selectAll.disabled = disabled || queryPending || !rows.length;
      clear.disabled = remove.disabled = disabled || queryPending || !selected.size;
      count.textContent = '已勾选 ' + selected.size + ' 条';
      tableHost.querySelectorAll('input, button').forEach(function (control) {
        control.disabled = disabled || queryPending;
      });
      pagerHost.querySelectorAll('button').forEach(function (button) {
        button.disabled = disabled || queryPending || button.dataset.boundaryDisabled === '1';
      });
    }

    function updateTables(tables) {
      platform.replaceChildren();
      tables.forEach(function (table) {
        platform.appendChild(h('option', { value: table.name }, table.name + '（' + fmtNumber(table.rows) + ' 行）'));
      });
      var names = tables.map(function (table) { return table.name; });
      if (names.indexOf(state.assets.dbPlatform) < 0) {
        state.assets.dbPlatform = names.indexOf('douyin') >= 0 ? 'douyin' : (names[0] || '');
        state.assets.dbPage = 1;
      }
      platform.value = state.assets.dbPlatform;
    }

    function deleteRows(keys) {
      if (busy || loading || queryPending || !keys.length) return;
      if (!window.confirm('删除「' + state.assets.dbPlatform + '」中的 ' + keys.length
        + ' 条记录？\n\n仅删除数据库记录，不删除已导出的文件。此操作不可恢复。')) return;
      busy = true;
      syncControls();
      post('/api/assets/db/delete', {
        platform: state.assets.dbPlatform, keys: keys, confirmed: true
      }).then(function (data) {
        keys.forEach(function (key) { selected.delete(key); });
        updateTables(data.tables);
        toast('已删除 ' + data.deleted + ' 条', 'success');
        return load(true);
      }).catch(function (error) {
        toast('删除失败：' + error.message, 'error');
      }).finally(function () {
        busy = false;
        syncControls();
      });
    }

    setLoading(tableHost, '正在读取数据库表…');
    api('/api/assets/db/tables').then(function (data) {
      updateTables(data.tables);
      if (!data.tables.length) {
        tableHost.replaceChildren(empty('暂无增量采集数据库。先在桌面端完成一次账号增量采集。'));
        syncControls();
        return;
      }
      platform.addEventListener('change', function () {
        state.assets.dbPlatform = platform.value;
        state.assets.dbPage = 1;
        selected.clear();
        load();
      });
      search.addEventListener('input', function () {
        clearTimeout(searchTimer);
        queryPending = true;
        syncControls();
        searchTimer = setTimeout(function () {
          queryPending = false;
          state.assets.dbQ = search.value.trim();
          state.assets.dbPage = 1;
          selected.clear();
          load();
        }, 320);
      });
      load();
    }).catch(function (error) { setError(tableHost, error); });

    function load(preserveScroll) {
      var seq = ++requestSeq;
      var scrollX = window.scrollX, scrollY = window.scrollY;
      loading = true;
      syncControls();
      if (!preserveScroll) setLoading(tableHost, '正在查询…');
      return api('/api/assets/db/rows?platform=' + encodeURIComponent(state.assets.dbPlatform) +
        '&page=' + state.assets.dbPage + '&page_size=50&q=' + encodeURIComponent(state.assets.dbQ) +
        '&order_by=' + encodeURIComponent(state.assets.dbOrder) + '&ascending=' + (state.assets.dbAsc ? '1' : '0'))
        .then(function (data) {
          if (seq !== requestSeq || !host.isConnected) return;
          state.assets.dbPage = data.page;
          columns = data.columns;
          rows = data.rows;
          var visible = new Set(rows.map(function (row) { return row.dedup_key; }));
          selected.forEach(function (key) { if (!visible.has(key)) selected.delete(key); });
          if (!data.rows.length) {
            tableHost.replaceChildren(empty('没有匹配的数据。可清空搜索词或切换平台。'));
            pagerHost.replaceChildren();
            return;
          }
          var head = h('tr', null, h('th', null, ''));
          columns.forEach(function (column) {
            head.appendChild(h('th', {
              class: 'sortable', dataset: { order: column },
              onclick: function () {
                if (busy || loading) return;
                if (state.assets.dbOrder === column) state.assets.dbAsc = !state.assets.dbAsc;
                else { state.assets.dbOrder = column; state.assets.dbAsc = false; }
                state.assets.dbPage = 1;
                selected.clear();
                load();
              }
            }, dbColumnLabel(column), state.assets.dbOrder === column ? (state.assets.dbAsc ? ' ↑' : ' ↓') : ''));
          });
          head.appendChild(h('th', null, '操作'));
          var body = h('tbody');
          data.rows.forEach(function (row) {
            var box = h('input', { type: 'checkbox', checked: selected.has(row.dedup_key),
              dataset: { dbKey: row.dedup_key }, 'aria-label': '勾选记录',
              onchange: function () {
                if (box.checked) selected.add(row.dedup_key);
                else selected.delete(row.dedup_key);
                syncControls();
              } });
            var tr = h('tr', null, h('td', null, box));
            columns.forEach(function (column) {
              var value = row[column];
              if (value === null || value === undefined) value = '';
              var text = String(value);
              if (text.length > 300) text = text.slice(0, 300) + '…';
              tr.appendChild(h('td', { class: column === 'description' ? 'ellipsis' : '' }, text));
            });
            tr.appendChild(h('td', null, h('button', {
              class: 'btn danger', type: 'button',
              onclick: function () { deleteRows([row.dedup_key]); }
            }, '删除')));
            body.appendChild(tr);
          });
          tableHost.replaceChildren(h('div', { class: 'table-wrap' },
            h('table', { class: 'db-table' }, h('thead', null, head), body)));
          pager(pagerHost, data.page, data.pages, data.total, function (page) {
            if (busy || loading) return;
            state.assets.dbPage = page;
            selected.clear();
            load();
          });
          pagerHost.querySelectorAll('button').forEach(function (button) {
            button.dataset.boundaryDisabled = button.disabled ? '1' : '0';
          });
          if (preserveScroll) window.scrollTo(scrollX, scrollY);
        }).catch(function (error) {
          if (seq === requestSeq) setError(tableHost, error, function () { load(); });
        }).finally(function () {
          if (seq === requestSeq) {
            loading = false;
            syncControls();
          }
        });
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

      var sources = (overview.sources || []).map(function (item) {
        return item.path;
      });
      var recursive = h('input', {
        type: 'checkbox', checked: !!overview.recursive
      });
      var addInput = h('input', {
        class: 'input grow', placeholder: 'A 端来源文件夹完整路径'
      });
      var addBtn = h('button', { class: 'btn', type: 'button' }, '添加');
      var sourceList = h('div', { class: 'days', style: 'margin-top:8px' });
      var scanBtn = h('button', { class: 'btn', type: 'button' }, '扫描来源');
      var migrateBtn = h('button', {
        class: 'btn primary', type: 'button', disabled: true
      }, '迁移来源视频');
      var scanHost = h('div', { class: 'muted small', style: 'margin-top:6px' });
      var migrateHost = h('div');
      var lastScan = null;
      var migrating = false;

      function drawSources() {
        sourceList.replaceChildren();
        if (!sources.length) {
          sourceList.appendChild(h('span', { class: 'muted small' },
            '还没有来源文件夹；只整理工作目录时可不填。'));
          migrateBtn.disabled = true;
          return;
        }
        sources.forEach(function (path) {
          sourceList.appendChild(h('span', {
            class: 'badge', title: path, style: 'margin:0 6px 6px 0'
          }, path, h('button', {
            class: 'btn mini', type: 'button', style: 'margin-left:6px',
            onclick: function () {
              sources = sources.filter(function (item) { return item !== path; });
              saveSources();
            }
          }, '移除')));
        });
      }

      function saveSources() {
        post('/api/tools/organize/sources', {
          sources: sources, recursive: recursive.checked
        }).then(function (result) {
          sources = result.sources || sources;
          drawSources();
          toast('来源设置已保存', 'success');
        }).catch(function (error) {
          toast('来源保存失败：' + error.message, 'error');
        });
      }

      addBtn.addEventListener('click', function () {
        var path = addInput.value.trim();
        if (!path) {
          toast('请填写来源文件夹完整路径', 'warning');
          return;
        }
        if (sources.indexOf(path) >= 0) {
          toast('该来源已存在', 'info');
          return;
        }
        sources.push(path);
        addInput.value = '';
        saveSources();
      });
      recursive.addEventListener('change', saveSources);
      scanBtn.addEventListener('click', function () {
        if (!sources.length) {
          toast('请先添加 A 端来源文件夹', 'warning');
          return;
        }
        scanBtn.disabled = true;
        scanHost.textContent = '正在扫描来源文件夹…';
        api('/api/tools/organize/sources/scan').then(function (result) {
          scanBtn.disabled = false;
          lastScan = result;
          var dates = (result.by_date || []).map(function (item) {
            return item.date + '（' + item.count + '）';
          }).slice(0, 8).join('、');
          scanHost.textContent = '来源可迁移 ' + fmtNumber(result.total)
            + ' 个视频' + (dates ? '：' + dates : '')
            + (result.no_date ? '；无日期 ' + result.no_date + ' 个按修改时间归组' : '')
            + (result.skipped_total
              ? '；跳过 ' + fmtNumber(result.skipped_total) + ' 个（空文件 / 正在写入 / 已在工作目录）'
              : '');
          migrateBtn.disabled = migrating || !result.total;
        }).catch(function (error) {
          scanBtn.disabled = false;
          lastScan = null;
          migrateBtn.disabled = true;
          scanHost.textContent = '';
          toast('扫描失败：' + error.message, 'error');
        });
      });
      migrateBtn.addEventListener('click', function () {
        if (migrating) return;
        var total = lastScan ? lastScan.total : 0;
        if (!total) {
          toast('请先扫描来源', 'warning');
          return;
        }
        if (!window.confirm('确认从来源迁移视频\n\n将把 ' + total
          + ' 个视频移动到工作目录的对应日期文件夹（无日期按修改时间归组）。\n'
          + '同名自动编号，校验成功后删除来源文件；附属文件一起带出。\n\n确认执行？')) {
          return;
        }
        migrating = true;
        migrateBtn.disabled = true;
        post('/api/tools/organize/sources/migrate', { confirmed: true })
          .then(function (data) {
            watchJob(migrateHost, data.job, function (job) {
              migrating = false;
              var result = job.result || {};
              toast(result.summary || '迁移完成', 'success');
              renderOrganize(host);
            }, {
              onSettled: function (job) {
                migrating = false;
                if (job.status === 'error') {
                  migrateBtn.disabled = false;
                  toast('迁移失败：' + (job.error || '未知错误'), 'error');
                }
              }
            });
          }).catch(function (error) {
            migrating = false;
            migrateBtn.disabled = false;
            toast('迁移失败：' + error.message, 'error');
          });
      });
      drawSources();
      host.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'row-title' }, 'A 端来源（可选）'),
        h('div', { class: 'muted small', style: 'margin:4px 0 6px' },
          '从其它文件夹（如另一台机器 / 采集盘）把视频迁移进工作目录；'
          + '与桌面端「自动整理」共用同一份来源设置。'),
        h('div', { class: 'toolbar' },
          addInput, addBtn,
          h('label', { class: 'muted small' }, recursive, ' 含子文件夹'),
          h('span', { style: 'flex:1' }), scanBtn, migrateBtn),
        sourceList, scanHost, migrateHost));

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
    api('/api/tools/rename/overview').then(function (overview) {
      return Promise.all([
        Promise.resolve(overview),
        overview.has_work_dir
          ? api('/api/tools/rename/preview?limit=300')
          : Promise.resolve({ items: [], total: 0, truncated: false }),
        api('/api/publish/overview').catch(function () { return null; }),
        api('/api/publish/templates').catch(function () { return null; }),
        api('/api/publish/history').catch(function () { return null; }),
        api('/api/publish/categories').catch(function () { return null; })
      ]);
    }).then(function (results) {
      var overview = results[0];
      var preview = results[1];
      var publishInfo = results[2] || {};
      var templateInfo = results[3] || {};
      var historyInfo = results[4] || {};
      var categoryInfo = results[5] || null;
      host.replaceChildren();
      if (!overview.has_work_dir) {
        host.appendChild(empty('尚未设置工作目录，仍可先配置并保存投稿模板；视频准备好后再设置工作目录。'));
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
      if (overview.has_work_dir && !preview.items.length) {
        host.appendChild(empty('工作目录下没有可预览的视频，仍可先配置并保存投稿模板。'));
      }

      var jobHost = h('div');
      host.appendChild(jobHost);

      var selected = new Set();
      var precheckResult = null;
      var uploading = false;
      var publishJobHost = h('div');
      var checksHost = h('div');
      var pset = publishInfo.settings || {};
      var activeTpl = (templateInfo.items || []).filter(function (item) {
        return item.name === (templateInfo.active || '');
      })[0];
      if (activeTpl && activeTpl.settings) {
        pset = Object.assign({}, pset, activeTpl.settings);
      }
      var titleInput = h('input', {
        class: 'input', style: 'width:100%',
        value: pset.title || '', placeholder: '标题模板'
      });
      var descInput = h('textarea', {
        class: 'textarea', rows: '2',
        placeholder: '简介模板（留空 = 不填）'
      });
      descInput.value = pset.desc || '';
      var tagsInput = h('input', {
        class: 'input', style: 'width:100%', value: pset.tags || '',
        placeholder: '标签，逗号分隔（1–12 个）'
      });
      var tidInput = h('input', {
        class: 'input', style: 'width:100px', type: 'number', min: '1',
        value: String(pset.tid || ''), placeholder: '分区 ID'
      });
      var statementSel = h('select', { class: 'select' });
      (publishInfo.statements || []).forEach(function (item) {
        statementSel.appendChild(h('option', {
          value: item.value,
          selected: item.value === (pset.creation_statement || 'self')
        }, item.label));
      });
      var seasonInput = h('input', {
        class: 'input', style: 'width:200px', value: pset.season || '',
        placeholder: '合集名（留空不加）'
      });
      var sourceInput = h('input', {
        class: 'input grow', value: pset.source || '',
        placeholder: '创作声明为转载时必填原直播来源链接'
      });
      var dateInput = h('input', {
        class: 'input', style: 'width:150px', value: pset.date || '',
        placeholder: '日期 YYYYMMDD（可留空）'
      });
      var coverInput = h('input', {
        class: 'input grow', value: pset.cover || '',
        placeholder: '封面图片完整路径（留空自动提取首帧，需要 FFmpeg）'
      });
      var templateSel = h('select', { class: 'select' });
      var categorySel = h('select', { class: 'select' });
      var categoryItems = (categoryInfo && categoryInfo.items) || [];
      if (!categoryItems.length) {
        categorySel.appendChild(h('option', { value: '' },
          categoryInfo === null ? '分区读取失败，请手填分区 ID'
            : '账号没有可用分区'));
        categorySel.disabled = true;
      } else {
        categorySel.appendChild(h('option', { value: '' }, '选择分区'));
        categoryItems.forEach(function (item) {
          categorySel.appendChild(h('option', {
            value: String(item.id),
            selected: String(item.id) === String(pset.tid || '')
          }, item.name + '（' + item.id + '）'));
        });
      }
      categorySel.addEventListener('change', function () {
        if (!categorySel.value) return;
        tidInput.value = categorySel.value;
        precheckResult = null;
        checksHost.replaceChildren();
        syncPublish();
      });
      var tplItems = templateInfo.items || [];
      if (!tplItems.length) {
        templateSel.appendChild(h('option', { value: '' }, '（暂无模板）'));
        templateSel.disabled = true;
      } else {
        tplItems.forEach(function (item) {
          templateSel.appendChild(h('option', {
            value: item.name,
            selected: item.name === (templateInfo.active || '')
          }, item.name));
        });
      }
      function applyTemplate(name) {
        var found = tplItems.filter(function (item) {
          return item.name === name;
        })[0];
        if (!found) return;
        var conf = found.settings || {};
        if (conf.title != null) titleInput.value = conf.title;
        if (conf.desc != null) descInput.value = conf.desc;
        if (conf.tags != null) tagsInput.value = conf.tags;
        if (conf.tid != null) tidInput.value = String(conf.tid);
        if (conf.creation_statement) statementSel.value = conf.creation_statement;
        if (conf.season != null) seasonInput.value = conf.season;
        if (conf.source != null) sourceInput.value = conf.source;
        if (conf.cover != null) coverInput.value = conf.cover;
        if (conf.auto_publish != null) autoCheck.checked = !!conf.auto_publish;
        precheckResult = null;
        checksHost.replaceChildren();
        syncPublish();
        toast('已载入模板：' + name, 'info');
      }
      templateSel.addEventListener('change', function () {
        applyTemplate(templateSel.value);
      });
      var saveTplBtn = h('button', { class: 'btn', type: 'button' }, '另存为模板');
      saveTplBtn.addEventListener('click', function () {
        var name = window.prompt('模板名称（同名为覆盖）', templateSel.value || '');
        if (!name || !name.trim()) return;
        post('/api/publish/templates', {
          name: name.trim(), action: 'save', settings: currentSettings()
        }).then(function (result) {
          toast(result.summary || '模板已保存', 'success');
          renderRename(host);
        }).catch(function (error) {
          toast('模板保存失败：' + error.message, 'error');
        });
      });
      var delTplBtn = h('button', { class: 'btn ghost', type: 'button' }, '删除模板');
      var saveTplQuickBtn = h('button', {
        class: 'btn', type: 'button', title: '保存当前表单到选中的模板'
      }, '保存设置');
      saveTplQuickBtn.addEventListener('click', function () {
        var name = templateSel.value || '默认模板';
        post('/api/publish/templates', {
          name: name, action: 'save', settings: currentSettings()
        }).then(function (result) {
          toast(result.summary || '模板已保存', 'success');
          renderRename(host);
        }).catch(function (error) {
          toast('模板保存失败：' + error.message, 'error');
        });
      });
      tidInput.addEventListener('change', function () {
        var value = String(Number(tidInput.value) || '');
        var matched = categoryItems.some(function (item) {
          return String(item.id) === value;
        });
        categorySel.value = matched ? value : '';
      });
      delTplBtn.addEventListener('click', function () {
        var name = templateSel.value;
        if (!name) return;
        if (!window.confirm('删除投稿模板「' + name + '」？')) return;
        post('/api/publish/templates', { name: name, action: 'delete' })
          .then(function (result) {
            toast(result.summary || '模板已删除', 'success');
            renderRename(host);
          }).catch(function (error) {
            toast('模板删除失败：' + error.message, 'error');
          });
      });
      var autoCheck = h('input', {
        type: 'checkbox', checked: pset.auto_publish !== false
      });
      var selLabel = h('span', { class: 'muted small' }, '已选 0 个');
      var precheckBtn = h('button', { class: 'btn', type: 'button' }, '预检');
      var uploadBtn = h('button', {
        class: 'btn primary', type: 'button', disabled: true
      }, '确认投稿');

      function currentSettings() {
        return {
          title: titleInput.value.trim(),
          desc: descInput.value,
          tags: tagsInput.value.trim(),
          tid: Number(tidInput.value) || 0,
          creation_statement: statementSel.value,
          season: seasonInput.value.trim(),
          source: sourceInput.value.trim(),
          date: dateInput.value.trim(),
          cover: coverInput.value.trim(),
          auto_publish: autoCheck.checked
        };
      }

      function signature() {
        return JSON.stringify({
          paths: Array.from(selected).sort(),
          settings: currentSettings()
        });
      }

      function syncPublish() {
        selLabel.textContent = '已选 ' + selected.size + ' 个';
        var ready = selected.size > 0;
        precheckBtn.disabled = uploading || !ready;
        uploadBtn.disabled = uploading || !ready || !precheckResult
          || precheckResult.signature !== signature();
        uploadBtn.textContent = uploading ? '上传中…'
          : (precheckResult && precheckResult.signature === signature()
            ? '确认投稿' : '请先预检');
      }

      function renderChecks(result) {
        checksHost.replaceChildren();
        var list = h('div', { class: 'muted small', style: 'margin-top:6px' });
        (result.checks || []).forEach(function (check) {
          list.appendChild(h('div', null,
            (check.ok ? '✓ ' : (check.fatal ? '✕ ' : '! '))
            + check.label + '：' + (check.detail || '')));
        });
        checksHost.appendChild(list);
        var meta = result.metadata || {};
        if (result.ok) {
          checksHost.appendChild(h('div', {
            class: 'card', style: 'margin-top:8px'
          },
            h('div', { class: 'row-title' }, meta.title || '（无标题）'),
            h('div', { class: 'muted small', style: 'margin-top:4px' },
              result.parts + ' P · 分区 ' + meta.tid
              + (meta.tid_name ? '「' + meta.tid_name + '」' : '')
              + ' · 标签 ' + meta.tag + ' · 共 '
              + fmtBytes(result.total_bytes)
              + (result.account && result.account.name
                ? ' · 账号 ' + result.account.name : '')),
            meta.desc ? h('div', { class: 'muted small', style: 'margin-top:4px' },
              meta.desc) : null));
        } else if (result.blocking) {
          checksHost.appendChild(h('div', {
            class: 'muted small', style: 'margin-top:6px;color:var(--danger)'
          }, result.blocking));
        }
      }

      precheckBtn.addEventListener('click', function () {
        if (!selected.size) return;
        precheckBtn.disabled = true;
        precheckBtn.textContent = '预检中…';
        post('/api/publish/precheck', {
          paths: Array.from(selected), settings: currentSettings()
        }).then(function (result) {
          precheckBtn.disabled = false;
          precheckBtn.textContent = '预检';
          result.signature = signature();
          precheckResult = result;
          renderChecks(result);
          uploadBtn.disabled = !result.ok;
          uploadBtn.textContent = result.ok ? '确认投稿' : '预检未通过';
          toast(result.ok ? '预检通过，可确认投稿'
            : ('预检未通过：' + (result.blocking || '')), result.ok ? 'success' : 'warning');
        }).catch(function (error) {
          precheckBtn.disabled = false;
          precheckBtn.textContent = '预检';
          toast('预检失败：' + error.message, 'error');
        });
      });

      uploadBtn.addEventListener('click', function () {
        if (!precheckResult || precheckResult.signature !== signature()) {
          toast('请先预检当前选择与设置', 'warning');
          return;
        }
        var meta = precheckResult.metadata || {};
        var action = autoCheck.checked ? '上传并自动投稿' : '仅上传，不投稿';
        if (!window.confirm('确认' + action + '\n\n'
          + (meta.title || '') + '\n'
          + precheckResult.parts + ' P · 分区 ' + meta.tid + '\n'
          + '账号：' + ((precheckResult.account || {}).name || '-') + '\n'
          + '合集：' + (seasonInput.value.trim() || '不加入合集') + '\n'
          + '标签：' + (meta.tag || '') + '\n\n'
          + '上传会真实发布到 B站，确认开始？')) {
          return;
        }
        uploading = true;
        syncPublish();
        post('/api/publish/upload', {
          paths: Array.from(selected), settings: currentSettings(), confirmed: true
        }).then(function (result) {
          watchJob(publishJobHost, result.job, function (job) {
            uploading = false;
            var outcome = job.result || {};
            toast(outcome.summary || '上传完成',
              outcome.state === 'uncertain' ? 'warning' : 'success');
            precheckResult = null;
            syncPublish();
            setTimeout(function () { renderRename(host); }, 1200);
          }, {
            cancelable: true,
            onSettled: function (job) {
              uploading = false;
              syncPublish();
              if (job.status === 'error') {
                toast('上传失败：' + (job.error || '未知错误'), 'error');
              }
            }
          });
        }).catch(function (error) {
          uploading = false;
          syncPublish();
          toast('上传失败：' + error.message, 'error');
        });
      });

      host.appendChild(h('div', {
        class: 'card', style: 'margin-bottom:12px'
      },
        h('div', { class: 'row-title' }, 'B站投稿上传'),
        h('div', { class: 'muted small', style: 'margin:4px 0 6px' },
          '先在下方表格勾选已标准命名的视频（可多选成分 P），填好投稿信息后「预检」，'
          + '通过后「确认投稿」才会真实上传。'
          + (publishInfo.logged_in ? '' : ' 当前未登录 B站。')),
        h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '标题模板'), titleInput),
        h('div', { style: 'margin-top:6px' }, descInput),
        h('div', { class: 'toolbar', style: 'margin-top:6px' },
          h('span', { class: 'muted small' }, '标签'), tagsInput),
        h('div', { class: 'toolbar', style: 'margin-top:6px' },
          h('span', { class: 'muted small' }, '账号可用分区'), categorySel,
          h('span', { class: 'muted small' }, '分区 ID'), tidInput,
          h('span', { class: 'muted small' }, '创作声明'), statementSel,
          h('label', { class: 'muted small' }, autoCheck, ' 上传后自动投稿')),
        h('div', { class: 'toolbar', style: 'margin-top:6px' },
          h('span', { class: 'muted small' }, '合集'), seasonInput,
          h('span', { class: 'muted small' }, '日期'), dateInput,
          selLabel,
          h('span', { style: 'flex:1' }), precheckBtn, uploadBtn),
        h('div', { class: 'toolbar', style: 'margin-top:6px' },
          h('span', { class: 'muted small' }, '原直播来源'), sourceInput),
        h('div', { class: 'toolbar', style: 'margin-top:6px' },
          h('span', { class: 'muted small' }, '封面'), coverInput),
        h('div', { class: 'toolbar', style: 'margin-top:6px' },
          h('span', { class: 'muted small' }, '投稿模板'), templateSel,
          saveTplQuickBtn, saveTplBtn, delTplBtn),
        checksHost, publishJobHost));

      var STATE_LABELS = {
        published: ['已投稿', 'ok'], uploaded: ['已上传', ''],
        uploading: ['上传中', 'warn'], submitting: ['提交中', 'warn'],
        uncertain: ['结果不明', 'warn'], failed: ['失败', 'danger']
      };
      var historyHost = h('div');
      var historyRows = historyInfo.items || [];
      if (historyRows.length) {
        var hHead = h('tr', null, h('th', null, '任务'), h('th', null, '状态'),
          h('th', null, '标题'), h('th', null, '账号'), h('th', null, '操作'));
        var hBody = h('tbody');
        historyRows.forEach(function (row) {
          var badge = STATE_LABELS[row.state]
            || [row.state || '-', ''];
          var actionCell = h('td', null);
          if (row.state !== 'published' && row.state !== 'uploading') {
            var retryBtn = h('button', {
              class: 'btn mini', type: 'button'
            }, '允许重试');
            retryBtn.addEventListener('click', function () {
              if (!window.confirm('允许重试该投稿任务？\n\n请先确认 B站投稿管理里'
                + '没有相同稿件，否则可能重复投稿。')) {
                return;
              }
              retryBtn.disabled = true;
              post('/api/publish/history/retry', {
                task: row.task, confirmed: true
              }).then(function (result) {
                toast(result.summary || '已允许重试', 'success');
                renderRename(host);
              }).catch(function (error) {
                retryBtn.disabled = false;
                toast('操作失败：' + error.message, 'error');
              });
            });
            actionCell.appendChild(retryBtn);
          }
          hBody.appendChild(h('tr', null,
            h('td', { class: 'muted small' }, row.task),
            h('td', null, h('span', { class: 'badge ' + badge[1] }, badge[0])),
            h('td', { class: 'ellipsis' }, row.title || '-'),
            h('td', { class: 'muted small' }, row.account || '-'),
            actionCell));
        });
        historyHost.appendChild(h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' },
            h('thead', null, hHead), hBody)));
      } else {
        historyHost.appendChild(empty('暂无投稿历史。'));
      }
      host.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'row-title' }, '投稿历史'),
        h('div', { class: 'muted small', style: 'margin:4px 0 6px' },
          historyInfo.note || '已投稿 / 结果不明的任务不能重复提交。'),
        historyHost));

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
      var head = h('tr', null, h('th', { style: 'width:32px' }, ''));
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
        var check = h('input', {
          type: 'checkbox', disabled: !item.standard,
          title: item.standard ? '' : '先标准命名后才能投稿'
        });
        check.addEventListener('change', function () {
          if (!item.path) return;
          if (check.checked) selected.add(item.path);
          else selected.delete(item.path);
          precheckResult = null;
          checksHost.replaceChildren();
          syncPublish();
        });
        body.appendChild(h('tr', null,
          h('td', null, check),
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
      if (preview.items.length) {
        host.appendChild(h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' }, h('thead', null, head), body)));
        host.appendChild(h('div', { class: 'muted small', style: 'margin-top:8px' },
          preview.note || '点击「命名」生成标准文件名。'));
      }
      syncPublish();
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
      var previewVideo = h('video', {
        controls: true, preload: 'metadata', class: 'video-preview'
      });
      var previewNote = h('div', { class: 'muted small', style: 'margin-top:6px' },
        '勾选 1 个视频即可预览（拖动进度按需加载）。MKV / AVI 等浏览器不支持的格式可能无法播放，请用桌面端。');
      var previewHost = h('div', {
        class: 'card video-preview-card', style: 'margin-top:12px;display:none', tabindex: '0',
        'aria-label': '视频预览与片段标记'
      }, previewVideo, previewNote);
      var opHost = h('div', { class: 'card video-operations', style: 'margin-top:12px' });
      var jobHost = h('div');
      var resultHost = h('div');
      host.appendChild(listHost);
      host.appendChild(previewHost);
      host.appendChild(opHost);
      host.appendChild(jobHost);
      host.appendChild(resultHost);
      previewVideo.addEventListener('error', function () {
        if (!previewVideo.getAttribute('src')) return;
        previewNote.textContent = '该视频格式浏览器无法播放，请在桌面端预览。';
      });

      var running = false;
      var actionRefreshers = [];
      var cutDrafts = {};
      var emptyCutDraft = { text: '', pendingStart: null, selected: -1,
        drafts: [], selectedDraft: -1 };
      var cutEditorRefresh = null;
      var previewDurationMs = 0;
      var rangeLayer = h('div', { class: 'video-time-ranges', 'aria-hidden': 'true' });
      var seek = h('input', {
        class: 'video-seek', type: 'range', min: '0', max: '0', step: '1',
        value: '0', disabled: true, 'aria-label': '视频时间轴'
      });
      var currentTimeLabel = h('span', { class: 'video-time-label' }, '00:00:00.000');
      var durationLabel = h('span', { class: 'video-time-label' }, '00:00:00.000');
      var markStartBtn = h('button', { class: 'btn', type: 'button' }, '标记开始');
      var markEndBtn = h('button', { class: 'btn', type: 'button' }, '标记结束');
      var removeSegmentBtn = h('button', { class: 'btn', type: 'button' }, '删除选中片段');
      var deleteStartBtn = h('button', { class: 'btn', type: 'button' }, '删除起点');
      var deleteEndBtn = h('button', { class: 'btn', type: 'button' }, '删除终点');
      var clearSegmentsBtn = h('button', { class: 'btn ghost', type: 'button' }, '清除标记');
      var markHint = h('span', { class: 'muted small video-mark-hint' });
      var pointMenu = h('div', { class: 'video-point-menu', role: 'menu', hidden: true });
      previewHost.insertBefore(h('div', { class: 'video-timebar' },
        currentTimeLabel, h('div', { class: 'video-time-track' }, rangeLayer, seek),
        durationLabel), previewNote);
      previewHost.insertBefore(h('div', { class: 'toolbar video-mark-actions' },
        markStartBtn, markEndBtn), previewNote);
      previewHost.insertBefore(h('div', { class: 'toolbar video-point-actions' },
        deleteStartBtn, deleteEndBtn, removeSegmentBtn, clearSegmentsBtn), previewNote);
      previewHost.insertBefore(markHint, previewNote);
      previewHost.appendChild(pointMenu);

      function videoClock(ms) {
        var total = Math.max(0, Math.round(Number(ms) || 0));
        var hours = Math.floor(total / 3600000);
        var minutes = Math.floor((total % 3600000) / 60000);
        var seconds = ((total % 60000) / 1000).toFixed(3);
        return String(hours).padStart(2, '0') + ':'
          + String(minutes).padStart(2, '0') + ':'
          + (Number(seconds) < 10 ? '0' : '') + seconds;
      }

      function currentCutDraft() {
        var paths = selectedPaths();
        if (paths.length !== 1) return emptyCutDraft;
        if (!cutDrafts[paths[0]]) {
          cutDrafts[paths[0]] = { text: '', pendingStart: null, selected: -1,
            drafts: [], selectedDraft: -1 };
        }
        return cutDrafts[paths[0]];
      }

      function currentCutRows() {
        var text = currentCutDraft().text;
        if (!text.trim()) return [];
        try { return parseSegments(text); } catch (_error) { return []; }
      }

      function refreshMarkers() {
        var draft = currentCutDraft();
        var rows = currentCutRows();
        var one = selected.size === 1;
        var hasDuration = one && previewDurationMs > 0;
        var position = Math.max(0, Number(seek.value) || 0);
        currentTimeLabel.textContent = videoClock(position);
        durationLabel.textContent = videoClock(previewDurationMs);
        seek.disabled = !hasDuration;
        markStartBtn.disabled = running || !hasDuration;
        markEndBtn.disabled = running || !hasDuration;
        var partial = draft.drafts[draft.selectedDraft];
        var complete = rows[draft.selected];
        removeSegmentBtn.disabled = running || !one || (!partial && !complete);
        deleteStartBtn.disabled = running || !one
          || (partial ? partial.start === null : !complete);
        deleteEndBtn.disabled = running || !one
          || (partial ? partial.end === null : !complete);
        clearSegmentsBtn.disabled = running || !one
          || (!draft.text && draft.pendingStart === null && !draft.drafts.length);
        markHint.textContent = draft.pendingStart === null
          ? '按当前播放指针标记；未标记开始时，从 00:00:00.000 开始。'
          : '开始 ' + videoClock(draft.pendingStart) + ' · 移动指针后标记结束';
        rangeLayer.replaceChildren();
        if (!hasDuration) return;
        rows.forEach(function (row, index) {
          var start = Math.min(100, Math.max(0, row[0] / previewDurationMs * 100));
          var end = Math.min(100, Math.max(0, row[1] / previewDurationMs * 100));
          rangeLayer.appendChild(h('span', {
            class: 'video-time-range' + (index === draft.selected ? ' is-selected' : ''),
            style: 'left:' + start + '%;width:' + Math.max(0, end - start) + '%'
          }));
        });
        draft.drafts.forEach(function (item, index) {
          var point = item.start !== null ? item.start : item.end;
          if (point === null) return;
          rangeLayer.appendChild(h('span', {
            class: 'video-draft-point' + (index === draft.selectedDraft ? ' is-selected' : ''),
            style: 'left:' + Math.min(100, Math.max(0, point / previewDurationMs * 100)) + '%'
          }));
        });
        if (draft.pendingStart !== null) {
          rangeLayer.appendChild(h('span', {
            class: 'video-time-pending',
            style: 'left:' + Math.min(100, Math.max(0,
              draft.pendingStart / previewDurationMs * 100)) + '%'
          }));
        }
      }

      function refreshCutDraft() {
        pointMenu.hidden = true;
        refreshMarkers();
        if (cutEditorRefresh) cutEditorRefresh();
      }

      function writeCutRows(draft, rows, selectedRow) {
        rows.sort(function (a, b) { return a[0] - b[0] || a[1] - b[1]; });
        draft.text = rows.map(function (row) {
          return videoClock(row[0]) + '-' + videoClock(row[1]);
        }).join('\n');
        draft.selected = selectedRow
          ? rows.findIndex(function (row) { return row[0] === selectedRow[0] && row[1] === selectedRow[1]; })
          : -1;
      }

      function promoteCutDraft(draft, index) {
        var item = draft.drafts[index];
        if (!item || item.start === null || item.end === null) return;
        if (item.end <= item.start) {
          toast('结束时间必须大于开始时间，请调整端点后重试', 'warning');
          return;
        }
        var row = [item.start, item.end];
        var rows = currentCutRows();
        rows.push(row);
        draft.drafts.splice(index, 1);
        draft.selectedDraft = -1;
        writeCutRows(draft, rows, row);
      }

      function markEndpoint(endpoint) {
        var draft = currentCutDraft();
        var position = Number(seek.value) || 0;
        if (draft.selectedDraft >= 0) {
          var item = draft.drafts[draft.selectedDraft];
          if (!item) { draft.selectedDraft = -1; return; }
          var previous = item[endpoint];
          item[endpoint] = position;
          if (item.start !== null && item.end !== null && item.end <= item.start) {
            item[endpoint] = previous;
            toast('结束时间必须大于开始时间，请调整端点后重试', 'warning');
            return;
          }
          promoteCutDraft(draft, draft.selectedDraft);
        } else if (draft.selected >= 0) {
          var rows = currentCutRows();
          var selected = rows[draft.selected];
          if (!selected) { draft.selected = -1; return; }
          var original = selected.slice();
          selected[endpoint === 'start' ? 0 : 1] = position;
          if (selected[1] <= selected[0]) {
            rows[draft.selected] = original;
            toast('结束时间必须大于开始时间，请调整端点后重试', 'warning');
            return;
          }
          writeCutRows(draft, rows, selected);
        } else if (endpoint === 'start') {
          draft.pendingStart = position;
        } else {
          var start = draft.pendingStart === null ? 0 : draft.pendingStart;
          if (position <= start) {
            toast('结束时间必须大于开始时间，请移动播放指针后重试', 'warning');
            return;
          }
          var nextRows = currentCutRows();
          var newRow = [start, position];
          nextRows.push(newRow);
          draft.pendingStart = null;
          writeCutRows(draft, nextRows, newRow);
        }
        refreshCutDraft();
      }

      function deleteEndpoint(endpoint) {
        var draft = currentCutDraft();
        if (draft.selectedDraft >= 0) {
          var partial = draft.drafts[draft.selectedDraft];
          if (!partial || partial[endpoint] === null) return;
          partial[endpoint] = null;
          if (partial.start === null && partial.end === null) {
            draft.drafts.splice(draft.selectedDraft, 1);
            draft.selectedDraft = -1;
          }
        } else {
          var rows = currentCutRows();
          if (!rows[draft.selected]) return;
          var row = rows.splice(draft.selected, 1)[0];
          var fragment = { start: row[0], end: row[1] };
          fragment[endpoint] = null;
          draft.drafts.push(fragment);
          draft.selectedDraft = draft.drafts.length - 1;
          draft.selected = -1;
          writeCutRows(draft, rows, null);
        }
        refreshCutDraft();
      }

      function clearCutMarks() {
        var draft = currentCutDraft();
        draft.text = '';
        draft.pendingStart = null;
        draft.selected = -1;
        draft.drafts = [];
        draft.selectedDraft = -1;
        refreshCutDraft();
      }

      function deleteAt(endpoint, index, draftIndex) {
        var draft = currentCutDraft();
        if (draftIndex >= 0) {
          draft.selectedDraft = draftIndex;
          draft.selected = -1;
        } else {
          draft.selected = index;
          draft.selectedDraft = -1;
        }
        deleteEndpoint(endpoint);
      }

      function openPointMenu(event) {
        event.preventDefault();
        var draft = currentCutDraft();
        var rect = rangeLayer.getBoundingClientRect();
        var ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / Math.max(1, rect.width)));
        var at = ratio * previewDurationMs;
        var tolerance = Math.max(350, Math.min(1500, previewDurationMs * 0.005));
        var entries = [];
        currentCutRows().forEach(function (row, index) {
          ['start', 'end'].forEach(function (endpoint, endIndex) {
            var value = row[endIndex];
            if (Math.abs(value - at) <= tolerance) entries.push({
              label: '片段 ' + (index + 1) + ' · 删除' + (endpoint === 'start' ? '起点' : '终点') + ' · ' + videoClock(value),
              endpoint: endpoint, index: index, draftIndex: -1, value: value
            });
          });
        });
        draft.drafts.forEach(function (item, index) {
          ['start', 'end'].forEach(function (endpoint) {
            var value = item[endpoint];
            if (value !== null && Math.abs(value - at) <= tolerance) entries.push({
              label: '待补 ' + (index + 1) + ' · 删除' + (endpoint === 'start' ? '起点' : '终点') + ' · ' + videoClock(value),
              endpoint: endpoint, index: -1, draftIndex: index, value: value
            });
          });
        });
        if (!entries.length) { pointMenu.hidden = true; return; }
        pointMenu.replaceChildren();
        entries.forEach(function (entry) {
          pointMenu.appendChild(h('button', {
            class: 'btn mini', type: 'button', role: 'menuitem',
            onclick: function () { deleteAt(entry.endpoint, entry.index, entry.draftIndex); }
          }, entry.label));
        });
        var hostRect = previewHost.getBoundingClientRect();
        pointMenu.style.left = Math.max(8, event.clientX - hostRect.left) + 'px';
        pointMenu.style.top = Math.max(8, event.clientY - hostRect.top) + 'px';
        pointMenu.hidden = false;
        pointMenu.firstChild.focus();
      }

      rangeLayer.addEventListener('contextmenu', openPointMenu);
      previewHost.addEventListener('click', function (event) {
        if (!pointMenu.contains(event.target) && event.target !== rangeLayer) pointMenu.hidden = true;
      });

      seek.addEventListener('input', function () {
        previewVideo.currentTime = Number(seek.value) / 1000;
        refreshMarkers();
      });
      previewVideo.addEventListener('timeupdate', function () {
        seek.value = Math.round((previewVideo.currentTime || 0) * 1000);
        refreshMarkers();
      });
      function updatePreviewDuration() {
        var duration = Number(previewVideo.duration);
        previewDurationMs = selected.size === 1 && isFinite(duration) && duration > 0
          ? Math.round(duration * 1000) : 0;
        seek.max = previewDurationMs;
        refreshMarkers();
      }
      previewVideo.addEventListener('loadedmetadata', updatePreviewDuration);
      previewVideo.addEventListener('durationchange', updatePreviewDuration);
      markStartBtn.addEventListener('click', function () {
        markEndpoint('start');
      });
      markEndBtn.addEventListener('click', function () {
        markEndpoint('end');
      });
      removeSegmentBtn.addEventListener('click', function () {
        var draft = currentCutDraft();
        if (draft.selectedDraft >= 0) {
          draft.drafts.splice(draft.selectedDraft, 1);
          draft.selectedDraft = -1;
          refreshCutDraft();
          return;
        }
        var rows = currentCutRows();
        if (!rows[draft.selected]) return;
        rows.splice(draft.selected, 1);
        writeCutRows(draft, rows, null);
        refreshCutDraft();
      });
      deleteStartBtn.addEventListener('click', function () { deleteEndpoint('start'); });
      deleteEndBtn.addEventListener('click', function () { deleteEndpoint('end'); });
      clearSegmentsBtn.addEventListener('click', function () {
        clearCutMarks();
      });
      previewHost.addEventListener('keydown', function (event) {
        if (event.ctrlKey || event.altKey || event.metaKey || event.repeat
            || event.target.closest('textarea,select,input:not([type=range]),[contenteditable=true]')) return;
        var key = event.key.toLowerCase();
        var button = key === 'i' ? markStartBtn : (key === 'o' ? markEndBtn : null);
        if (!button || button.disabled) return;
        event.preventDefault();
        button.click();
      });
      var columns = [
        { key: 'name', label: '文件名' },
        { key: 'dir', label: '目录' },
        { key: 'size', label: '大小' },
        { key: 'mtime', label: '修改时间' }
      ];

      var tabs = h('div', { class: 'tabs' });
      var tabHost = h('div');
      [['convert', '转换'], ['merge', '合并'], ['cut', '切割'],
       ['compress', '压缩'], ['tracks', '轨道'], ['snapshot', '截图'],
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
        syncPreview();
        refreshMarkers();
        actionRefreshers.forEach(function (refresh) { refresh(); });
      }

      var previewPath = '';

      function syncPreview() {
        var paths = selectedPaths();
        if (paths.length !== 1) {
          previewHost.style.display = 'none';
          if (previewPath) {
            previewPath = '';
            previewVideo.removeAttribute('src');
            previewVideo.load();
          }
          previewDurationMs = 0;
          seek.max = '0';
          seek.value = '0';
          return;
        }
        previewHost.style.display = '';
        if (paths[0] === previewPath) return;
        previewPath = paths[0];
        previewDurationMs = 0;
        seek.max = '0';
        seek.value = '0';
        previewNote.textContent = '勾选 1 个视频即可预览（拖动进度按需加载）。'
          + 'MKV / AVI 等浏览器不支持的格式可能无法播放，请用桌面端。';
        previewVideo.src = withToken('/api/tools/video/stream?path='
          + encodeURIComponent(paths[0]));
        previewVideo.load();
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
        var rotationSel = makeSelect([
          { value: '0', label: '0°' }, { value: '90', label: '90°' },
          { value: '180', label: '180°' }, { value: '270', label: '270°' }
        ], '0');
        rotationSel.setAttribute('aria-label', '旋转角度');
        var rotateBtn = h('button', { class: 'btn primary', type: 'button' }, '导出旋转');
        tabHost.appendChild(h('div', { class: 'toolbar video-rotation-row' },
          h('span', { class: 'muted small' }, '旋转'), rotationSel,
          h('span', { class: 'grow' }), rotateBtn));
        tabHost.appendChild(h('div', { class: 'muted small' },
          '旋转单独导出为新文件（不转码），保留原标签；MP4 显示矩阵可生效，部分播放器可能忽略 MKV 的旋转信息。'));
        var syncRotation = function () {
          rotateBtn.disabled = running || selected.size !== 1;
          rotationSel.disabled = running || selected.size !== 1;
        };
        actionRefreshers.push(syncRotation);
        syncRotation();
        rotateBtn.addEventListener('click', function () {
          var paths = selectedPaths();
          if (paths.length !== 1 || running) return;
          var path = paths[0];
          var rotation = Number(rotationSel.value);
          running = true;
          updateButtons();
          api('/api/tools/video/metadata?path=' + encodeURIComponent(path))
            .then(function (data) {
              running = false;
              runOp('/api/tools/video/metadata/write', {
                path: path, tags: data.tags || {}, rotation: rotation
              }, '旋转');
            }).catch(function (error) {
              running = false;
              updateButtons();
              toast('读取原标签失败：' + error.message + '，请重试', 'error');
            });
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
          class: 'input grow', placeholder: '合并文件名，留空自动命名',
          'aria-label': '合并文件名', title: '合并文件名，留空自动命名 合并_日期_时间'
        });
        var btn = h('button', { class: 'btn primary', type: 'button' }, '合并选中视频');
        tabHost.appendChild(h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '方式'), mode,
          h('span', { class: 'muted small' }, '格式'), ext,
          name,
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
          class: 'input grow', placeholder: '合并文件名，留空自动命名',
          'aria-label': '切割合并文件名', title: '切割合并文件名，留空自动命名'
        });
        var nameRow = h('div', { class: 'toolbar', style: 'display:none' },
          nameInput);
        var segments = h('textarea', {
          class: 'textarea', rows: '4', value: currentCutDraft().text,
          'aria-label': '切割片段',
          placeholder: '每行一个片段：开始-结束，例如\n00:10-01:20\n01:45-02:30'
        });
        var segmentList = h('div', { class: 'video-segment-list' });
        var segmentError = h('div', { class: 'video-segment-error small', role: 'status' });
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
        var expCsvBtn = h('button', { class: 'btn', type: 'button' }, '导出 CSV');
        var expEdlBtn = h('button', { class: 'btn', type: 'button' }, '导出 EDL');
        var expChapBtn = h('button', { class: 'btn', type: 'button' }, '导出章节文件');
        var impCsvBtn = h('button', { class: 'btn', type: 'button' }, '导入 CSV');
        var csvInput = h('input', {
          type: 'file', accept: '.csv,text/csv', style: 'display:none'
        });
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
        tabHost.appendChild(segmentList);
        tabHost.appendChild(segments);
        tabHost.appendChild(segmentError);
        tabHost.appendChild(h('div', { class: 'toolbar', style: 'margin-top:8px' },
          h('span', { class: 'muted small' }, '片段清单'),
          expCsvBtn, expEdlBtn, expChapBtn, impCsvBtn, csvInput));
        tabHost.appendChild(nameRow);
        tabHost.appendChild(h('div', { class: 'muted small', style: 'margin-top:8px' },
          '先在表格勾选 1 个视频；时间可用秒（90）或时间码（01:30 / 00:01:30）。输出到工作目录根，不覆盖已有文件。'
          + '检测只读视频不生成文件：静音阈值用 dB（-60 到 0），黑帧与场景用 0.01 到 1；'
          + '「写入章节」会把上方片段写成章节并封装为新视频（不转码）。'
          + '片段清单导出可交给剪辑软件（EDL / 章节），导入 CSV 会覆盖上方片段。'));
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
          return videoClock(ms);
        }

        function refreshCutEditor() {
          var draft = currentCutDraft();
          if (segments.value !== draft.text) segments.value = draft.text;
          segments.disabled = running || selected.size !== 1;
          segmentList.replaceChildren();
          segmentError.textContent = '';
          var rows = [];
          if (draft.text.trim()) {
            try { rows = parseSegments(draft.text); }
            catch (error) { segmentError.textContent = error.message; }
          }
          rows.forEach(function (row, index) {
            var select = h('button', {
              class: 'btn mini video-segment' + (index === draft.selected ? ' active' : ''),
              type: 'button', disabled: running,
              'aria-pressed': index === draft.selected ? 'true' : 'false',
              title: '单击选中片段，双击定位到开始'
            }, String(index + 1) + '. ' + videoClock(row[0]) + '–' + videoClock(row[1]));
            select.addEventListener('click', function () {
              draft.selected = index;
              draft.selectedDraft = -1;
              segmentList.querySelectorAll('.video-segment, .video-segment-draft').forEach(function (button) {
                button.classList.toggle('active', button === select);
                button.setAttribute('aria-pressed', button === select ? 'true' : 'false');
              });
              refreshMarkers();
            });
            select.addEventListener('dblclick', function () {
              seek.value = row[0];
              previewVideo.currentTime = row[0] / 1000;
              refreshMarkers();
            });
            segmentList.appendChild(select);
          });
          draft.drafts.forEach(function (item, index) {
            var missing = item.start === null ? '起点' : '终点';
            var fixed = item.start === null ? item.end : item.start;
            var select = h('button', {
              class: 'btn mini video-segment-draft'
                + (index === draft.selectedDraft ? ' active' : ''),
              type: 'button', disabled: running,
              'aria-pressed': index === draft.selectedDraft ? 'true' : 'false',
              title: '选择后移动指针并标记缺少的端点'
            }, '待补 ' + (index + 1) + ' · 缺少' + missing
              + (fixed === null ? '' : ' · ' + videoClock(fixed)));
            select.addEventListener('click', function () {
              draft.selected = -1;
              draft.selectedDraft = index;
              segmentList.querySelectorAll('.video-segment, .video-segment-draft')
                .forEach(function (button) {
                  button.classList.toggle('active', button === select);
                  button.setAttribute('aria-pressed', button.classList.contains('active') ? 'true' : 'false');
                });
              refreshMarkers();
            });
            select.addEventListener('dblclick', function () {
              if (fixed === null) return;
              seek.value = fixed;
              previewVideo.currentTime = fixed / 1000;
              refreshMarkers();
            });
            segmentList.appendChild(select);
          });
        }
        cutEditorRefresh = refreshCutEditor;
        segments.addEventListener('input', function () {
          var draft = currentCutDraft();
          draft.text = segments.value;
          draft.selected = -1;
          draft.selectedDraft = -1;
          refreshCutDraft();
        });

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
          expCsvBtn.disabled = running;
          expEdlBtn.disabled = running;
          expChapBtn.disabled = running;
          impCsvBtn.disabled = running;
          fillBtn.disabled = running || !detectPoints.length || !detectDuration;
          clearBtn.disabled = running || !detectPoints.length;
          btn.textContent = running ? '处理中…' : '切割选中视频';
          chapterBtn.textContent = running ? '处理中…' : '写入章节';
          detBtn.textContent = running ? '处理中…' : '检测';
          refreshCutEditor();
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
          var draft = currentCutDraft();
          draft.text = rows.join('\n');
          draft.pendingStart = null;
          draft.selected = rows.length ? 0 : -1;
          draft.selectedDraft = -1;
          refreshCutDraft();
          toast('已按切点填入 ' + rows.length + ' 个片段', 'success');
        });
        clearBtn.addEventListener('click', function () {
          resetDetect();
          detInfo.textContent = '已清空切点。';
        });

        function segmentDocName() {
          var paths = selectedPaths();
          if (paths.length !== 1) return '';
          var name = String(paths[0]).split('/').pop();
          return name.replace(/\.[^.]+$/, '');
        }

        function exportSegmentsDoc(fmt, label) {
          var parsed;
          try {
            parsed = parseSegments(segments.value);
          } catch (error) {
            toast(error.message, 'warning');
            return;
          }
          expCsvBtn.disabled = true;
          expEdlBtn.disabled = true;
          expChapBtn.disabled = true;
          var stem = segmentDocName();
          downloadPost('/api/tools/video/segments/export', {
            segments: parsed, format: fmt, name: stem ? stem + '_片段' : '片段清单'
          }, (stem || '片段') + '.' + (fmt === 'chapters' ? 'txt' : fmt)).then(function () {
            toast('已导出 ' + label + '：' + parsed.length + ' 个片段', 'success');
          }).catch(function (error) {
            toast(error.message, 'error');
          }).then(function () {
            sync();
          });
        }

        expCsvBtn.addEventListener('click', function () {
          exportSegmentsDoc('csv', 'CSV');
        });
        expEdlBtn.addEventListener('click', function () {
          exportSegmentsDoc('edl', 'EDL');
        });
        expChapBtn.addEventListener('click', function () {
          exportSegmentsDoc('chapters', '章节文件');
        });
        impCsvBtn.addEventListener('click', function () {
          csvInput.value = '';
          csvInput.click();
        });
        csvInput.addEventListener('change', function () {
          var file = csvInput.files && csvInput.files[0];
          if (!file) return;
          if (file.size > 1024 * 512) {
            toast('CSV 文件过大（上限 512KB）', 'warning');
            return;
          }
          file.text().then(function (text) {
            return post('/api/tools/video/segments/import', { csv: text });
          }).then(function (data) {
            var draft = currentCutDraft();
            draft.text = data.text || '';
            draft.pendingStart = null;
            draft.selected = data.count ? 0 : -1;
            draft.selectedDraft = -1;
            refreshCutDraft();
            toast('已导入 ' + data.count + ' 个片段', 'success');
          }).catch(function (error) {
            toast('导入失败：' + error.message, 'error');
          });
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
        var originalRotation = 0;
        var metadataPath = '';
        var loadedPath = '';
        var metadataLoading = false;
        var reloadBtn = h('button', { class: 'btn', type: 'button' }, '重新读取');
        var writeBtn = h('button', { class: 'btn primary', type: 'button' }, '写入元数据');
        tabHost.appendChild(h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '先在表格勾选 1 个视频；写入生成 <文件名>_meta 新文件，源视频保留。'),
          h('span', { style: 'flex:1' }), reloadBtn, writeBtn));
        var form = h('div', { class: 'video-metadata-form' });
        [['title', '标题'], ['comment', '备注'], ['artist', '作者'],
         ['date', '日期']].forEach(function (pair) {
          inputs[pair[0]] = h('input', {
            class: 'input', placeholder: pair[1], title: pair[1],
            'aria-label': pair[1], maxlength: '500'
          });
          form.appendChild(inputs[pair[0]]);
        });
        tabHost.appendChild(infoHost);
        tabHost.appendChild(form);

        function currentPath() {
          var paths = selectedPaths();
          return paths.length === 1 ? paths[0] : '';
        }

        function sync() {
          var path = currentPath();
          if (path !== metadataPath) loadMeta();
          reloadBtn.disabled = running || metadataLoading || !path;
          writeBtn.disabled = running || metadataLoading || !path || loadedPath !== path;
          Object.keys(inputs).forEach(function (key) {
            inputs[key].disabled = running || metadataLoading || !path;
          });
          writeBtn.textContent = running ? '处理中…' : '写入元数据';
        }
        actionRefreshers.push(sync);

        function loadMeta() {
          var path = currentPath();
          metadataPath = path;
          loadedPath = '';
          if (!path) {
            metadataLoading = false;
            Object.keys(inputs).forEach(function (key) { inputs[key].value = ''; });
            infoHost.replaceChildren(
              h('div', { class: 'muted small' }, '先在表格勾选 1 个视频。'));
            sync();
            return;
          }
          metadataLoading = true;
          sync();
          setLoading(infoHost, '正在读取元数据…');
          api('/api/tools/video/metadata?path=' + encodeURIComponent(path))
            .then(function (data) {
              if (currentPath() !== path || metadataPath !== path) return;
              var tags = data.tags || {};
              Object.keys(inputs).forEach(function (key) {
                inputs[key].value = tags[key] || '';
              });
              originalRotation = Number(data.rotation || 0);
              loadedPath = path;
              metadataLoading = false;
              infoHost.replaceChildren(
                h('div', { class: 'muted small' }, data.note || ''));
              sync();
            }).catch(function (error) {
              if (currentPath() !== path || metadataPath !== path) return;
              metadataLoading = false;
              setError(infoHost, error, loadMeta);
              sync();
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
            path: path, tags: tags, rotation: originalRotation
          }, '写入元数据');
        });

        loadMeta();
        sync();
      }

      function renderTab() {
        tabHost.replaceChildren();
        actionRefreshers = [];
        cutEditorRefresh = null;
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
            href: '#', title: day.folder
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
        var overlay = h('div', {
          class: 'preview-overlay', role: 'dialog', 'aria-modal': 'true',
          'aria-label': item.label || '定时任务'
        });
        var box = h('div', {
          class: 'preview-box', style: 'width:560px;max-width:100%;min-width:0'
        });
        var caption = h('div', { class: 'preview-caption' },
          h('span', { class: 'ellipsis' }, item.label || '定时任务'),
          h('button', { class: 'btn', type: 'button' }, '关闭'));
        var closeBtn = caption.lastChild;
        var form = h('div', { style: 'padding:12px;overflow:auto;min-height:0' },
          h('div', { class: 'loading', text: '正在读取计划设置…' }));
        var footer = h('div', {
          style: 'padding:12px;display:flex;gap:8px;justify-content:flex-end;flex-shrink:0;flex-wrap:wrap'
        });
        var busy = false;
        var dirty = false;
        var originalFocus = document.activeElement;
        function close(force) {
          if (busy && !force) return;
          if (!force && dirty && !window.confirm('放弃尚未保存的计划设置？')) return;
          document.removeEventListener('keydown', onKey);
          overlay.remove();
          if (originalFocus && originalFocus.isConnected) originalFocus.focus();
        }
        function onKey(event) {
          if (event.key === 'Escape') { event.preventDefault(); close(false); }
          if (event.key === 'Tab') {
            var focusable = Array.from(box.querySelectorAll('button,input,select,[tabindex="0"]'))
              .filter(function (element) { return !element.disabled && element.getClientRects().length; });
            var first = focusable[0], last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
              event.preventDefault(); last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
              event.preventDefault(); first.focus();
            }
          }
        }
        box.appendChild(caption);
        box.appendChild(form);
        box.appendChild(footer);
        overlay.appendChild(box);
        document.body.appendChild(overlay);
        closeBtn.addEventListener('click', function () { close(false); });
        overlay.addEventListener('click', function (event) {
          if (event.target === overlay) close(false);
        });
        document.addEventListener('keydown', onKey);

        api('/api/tasks/schedule?id=' + encodeURIComponent(item.id))
          .then(function (schedule) {
            if (!overlay.isConnected) return;
            var modeSel = h('select', {
              class: 'select', style: 'width:100%', 'aria-label': '执行方式'
            },
              h('option', { value: 'daily', selected: schedule.mode === 'daily' }, '每天'),
              h('option', {
                value: 'interval',
                selected: schedule.mode === 'interval',
                disabled: !schedule.interval_supported
              }, '按间隔'));
            var timeInput = h('input', {
              class: 'input', style: 'width:100%', value: schedule.time || '09:00',
              'aria-label': '每天时间'
            });
            var intervalInput = h('input', {
              class: 'input', style: 'width:100%', type: 'number',
              min: '1', max: '1439', value: String(schedule.interval || 30),
              'aria-label': '间隔（分钟）'
            });
            var timeRow = h('div', null,
              h('div', { class: 'muted small', style: 'margin-top:10px' }, '每天时间（HH:MM）'),
              timeInput);
            var intervalRow = h('div', null,
              h('div', { class: 'muted small', style: 'margin-top:10px' }, '间隔（分钟，1–1439）'),
              intervalInput);
            var collection = schedule.collection;
            var collectionRows = h('div', { style: 'min-width:0' });
            var collectionInputs = [];
            var collectionStatus = h('div', {
              class: 'muted small', role: 'status', style: 'margin-top:8px'
            });
            if (collection) {
              (collection.fields || []).forEach(function (field) {
                var current = Object.prototype.hasOwnProperty.call(collection.values || {}, field.name)
                  ? collection.values[field.name] : field.default;
                var row = h('div', { style: 'margin-top:12px;min-width:0' });
                var label = h('div', { class: 'small', style: 'margin-bottom:4px' },
                  (field.label || field.name) + (field.required ? ' *' : ''));
                row.appendChild(label);
                if (field.type === 'multiselect') {
                  var selected = new Set((Array.isArray(current) ? current : []).map(String));
                  var choices = h('div', {
                    style: 'max-height:192px;overflow:auto;display:grid;gap:6px;min-width:0',
                    role: 'group', 'aria-label': field.label || field.name
                  });
                  var inputs = (field.choices || []).map(function (choice) {
                    var input = h('input', {
                      type: 'checkbox', value: String(choice.value),
                      checked: selected.has(String(choice.value)), name: field.name
                    });
                    choices.appendChild(h('label', {
                      style: 'display:flex;align-items:flex-start;gap:8px;min-width:0'
                    }, input, h('span', { style: 'overflow-wrap:anywhere;min-width:0' },
                      choice.label || String(choice.value))));
                    return input;
                  });
                  row.appendChild(choices);
                  if (!inputs.length) row.appendChild(h('div', { class: 'muted small' },
                    field.empty_hint || '尚无可选对象，请先在对应采集页保存一次结果。'));
                  collectionInputs.push({ field: field, inputs: inputs });
                } else if (field.type === 'int' || field.type === 'select') {
                  var control = field.type === 'int'
                    ? h('input', {
                      class: 'input', type: 'number', step: '1', min: field.min,
                      max: field.max, value: current == null ? '' : String(current)
                    })
                    : h('select', { class: 'select' });
                  if (field.type === 'select') {
                    (field.choices || []).forEach(function (choice) {
                      control.appendChild(h('option', {
                        value: String(choice.value),
                        selected: String(choice.value) === String(current)
                      }, choice.label || String(choice.value)));
                    });
                  }
                  control.name = field.name;
                  control.setAttribute('aria-label', field.label || field.name);
                  control.style.width = '100%';
                  row.appendChild(control);
                  collectionInputs.push({ field: field, control: control });
                } else {
                  collectionInputs.push({ field: field, unsupported: true });
                }
                collectionRows.appendChild(row);
              });
              collectionRows.appendChild(collectionStatus);
            }
            function readCollection() {
              var result = {};
              var error = '';
              collectionInputs.forEach(function (entry) {
                var field = entry.field;
                var value;
                if (entry.unsupported) {
                  error = '采集设置无法显示，请刷新页面后重试';
                  return;
                }
                if (entry.inputs) {
                  value = entry.inputs.filter(function (input) { return input.checked; })
                    .map(function (input) { return input.value; });
                  if (field.required && !value.length) {
                    error = '请至少勾选一个' + (field.label || '采集对象');
                  }
                } else if (field.type === 'int') {
                  value = Number(entry.control.value);
                  if (!entry.control.value || !Number.isInteger(value) ||
                      value < field.min || value > field.max) {
                    error = (field.label || field.name) + '需在 ' + field.min + '–' + field.max + ' 之间';
                  }
                } else {
                  var choice = (field.choices || []).find(function (option) {
                    return String(option.value) === entry.control.value;
                  });
                  if (!choice) error = '请选择' + (field.label || field.name);
                  value = choice ? choice.value : null;
                }
                result[field.name] = value;
              });
              return { values: result, error: error };
            }
            var extraInputs = [];
            var extraRows = h('div');
            (schedule.extra_times || []).forEach(function (value, index) {
              var enabled = h('input', { type: 'checkbox', checked: !!value });
              var input = h('input', {
                class: 'input', type: 'time', value: value || '', disabled: !value,
                'aria-label': '补采 ' + (index + 1) + ' 时间',
                style: 'flex:1;min-width:0'
              });
              enabled.addEventListener('change', function () { input.disabled = !enabled.checked; });
              extraRows.appendChild(h('div', {
                style: 'display:flex;align-items:center;gap:8px;margin-top:8px;min-width:0'
              }, h('label', null, enabled, '补采 ' + (index + 1)), input));
              extraInputs.push({ enabled: enabled, input: input });
            });
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
            var cancelBtn = h('button', { class: 'btn', type: 'button' }, '取消');
            cancelBtn.addEventListener('click', function () { close(false); });
            var status = h('div', {
              class: 'small', role: 'alert', style: 'margin-top:8px;color:var(--danger)'
            });
            function syncControls() {
              var reading = readCollection();
              var count = (reading.values.targets || []).length;
              if (collection) {
                collectionStatus.textContent = reading.error ||
                  ('已勾选 ' + count + ' 个采集对象 · * 为必填');
              }
              installBtn.disabled = busy || !!reading.error;
              installBtn.title = reading.error || '';
              if (deleteBtn) deleteBtn.disabled = busy;
              cancelBtn.disabled = busy;
              closeBtn.disabled = busy;
              modeSel.disabled = busy || !schedule.interval_supported;
              timeInput.disabled = busy || modeSel.value !== 'daily';
              intervalInput.disabled = busy || modeSel.value !== 'interval';
              collectionInputs.forEach(function (entry) {
                (entry.inputs || (entry.control ? [entry.control] : [])).forEach(function (input) {
                  input.disabled = busy;
                });
              });
              extraInputs.forEach(function (entry) {
                entry.enabled.disabled = busy;
                entry.input.disabled = busy || !entry.enabled.checked;
              });
            }
            function setBusy(value) {
              busy = value;
              installBtn.textContent = value ? '保存中…' : (item.installed ? '更新计划' : '安装计划');
              syncControls();
            }
            function edited() {
              dirty = true;
              status.textContent = '';
              syncControls();
            }
            form.addEventListener('input', edited);
            form.addEventListener('change', edited);

            function submitInstall() {
              if (busy) return;
              var reading = readCollection();
              if (reading.error) {
                status.textContent = reading.error;
                syncControls();
                return;
              }
              var mode = modeSel.value;
              var timeText = String(timeInput.value || '').trim();
              if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(timeText)) {
                status.textContent = '时间格式应为 HH:MM，例如 09:30';
                return;
              }
              var interval = Number(intervalInput.value);
              if (mode === 'interval' &&
                  (!Number.isInteger(interval) || interval < 1 || interval > 1439)) {
                status.textContent = '间隔需在 1–1439 分钟之间';
                return;
              }
              var extraTimes = extraInputs.map(function (entry) {
                return entry.enabled.checked ? String(entry.input.value || '').trim() : '';
              });
              if (extraTimes.some(function (value, index) {
                return extraInputs[index].enabled.checked &&
                  !/^([01]\d|2[0-3]):[0-5]\d$/.test(value);
              })) {
                status.textContent = '请填写已启用补采的时间，例如 13:00';
                return;
              }
              var planText = mode === 'daily'
                ? ('每天 ' + timeText)
                : ('每 ' + interval + ' 分钟');
              if (!window.confirm(
                '将在运行 WebUI 的电脑上' + (item.installed ? '更新' : '安装') +
                '计划任务「' + (item.label || '') + '」：' + planText + '。\n'
                + (collection ? ('采集对象：' + (reading.values.targets || []).length + ' 个。\n') : '')
                + '与桌面端共用同一 Windows 计划任务。确定吗？')) {
                return;
              }
              var payload = {
                id: item.id, mode: mode, time: timeText,
                interval: interval, confirmed: true
              };
              if (collection) payload.collection = reading.values;
              if (extraInputs.length) payload.extra_times = extraTimes;
              setBusy(true);
              post('/api/tasks/install', payload).then(function (result) {
                toast((result && result.message) || '计划已保存', 'success');
                close(true);
                renderTasks(host);
              }).catch(function (error) {
                setBusy(false);
                status.textContent = '保存计划失败：' + error.message + '，请检查设置后重试';
              });
            }

            function submitUninstall() {
              if (busy) return;
              if (!window.confirm(
                '将删除运行 WebUI 电脑上的计划任务「' + (item.label || '') +
                '」，之后不再自动执行。\n确定吗？')) {
                return;
              }
              setBusy(true);
              post('/api/tasks/uninstall', {
                id: item.id, confirmed: true
              }).then(function (result) {
                toast((result && result.message) || '已删除', 'success');
                close(true);
                renderTasks(host);
              }).catch(function (error) {
                setBusy(false);
                status.textContent = '删除计划失败：' + error.message + '，请重试';
              });
            }

            installBtn.addEventListener('click', submitInstall);
            if (deleteBtn) deleteBtn.addEventListener('click', submitUninstall);
            syncMode();
            form.replaceChildren(
              h('div', { class: 'muted small' }, item.usage || ''),
              h('div', { class: 'muted small', style: 'margin-top:8px' }, '执行方式'),
              modeSel,
              timeRow,
              intervalRow,
              extraRows,
              collectionRows,
              h('div', { class: 'muted small', style: 'margin-top:10px' },
                '操作在运行 WebUI 的电脑上执行；安装 / 更新会同时保存计划设置。'),
              status);
            footer.replaceChildren.apply(footer,
              [deleteBtn, h('span', { style: 'flex:1' }), cancelBtn, installBtn].filter(Boolean));
            syncControls();
            modeSel.focus();
          })
          .catch(function (error) {
            if (!overlay.isConnected) return;
            form.replaceChildren(h('div', { class: 'error' },
              '读取计划设置失败：' + error.message),
            h('button', {
              class: 'btn', type: 'button', onclick: function () { close(true); openTaskForm(item); }
            }, '重试'));
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
      var migrateInput = h('input', {
        class: 'input', style: 'width:100%',
        value: settings.migrate_target_dir || '',
        placeholder: '迁移目标目录完整路径；留空 = 不修改'
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
      dirs.appendChild(h('div', { class: 'muted small', style: 'margin:8px 0 4px' },
        '迁移目标目录（文件迁移）'));
      dirs.appendChild(migrateInput);
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
          migrate_target_dir: migrateInput.value.trim(),
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

      var backupHost = h('div');
      host.appendChild(h('div', { class: 'card', style: 'margin-top:12px' },
        h('div', { class: 'row-title', style: 'margin-bottom:8px' }, '备份与恢复'),
        h('div', { class: 'muted small', style: 'margin-bottom:8px' },
          '备份到网盘；计划时间仍在「任务」页管理。配置与资产备份沿用桌面端全局设置。'),
        backupHost));
      renderNetdiskPanel(backupHost, 'backup');

      var deps = h('details', { class: 'card', style: 'margin-top:12px' });
      deps.appendChild(h('summary', { class: 'row-title', style: 'cursor:pointer' }, '依赖与组件'));
      var depHost = h('div', { style: 'margin-top:8px' });
      deps.appendChild(depHost);
      host.appendChild(deps);
      var localLLMHost = h('div');
      host.appendChild(localLLMHost);
      window.FMJJLocalLLM.mountSettings(localLLMHost, {
        h: h, api: api, post: post, watchJob: watchJob, toast: toast
      });
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
        var groupSel = h('select', { class: 'select' },
          h('option', { value: '' }, '选择分组'));
        var exportBtn = h('button', {
          class: 'btn', type: 'button', disabled: true,
          title: '选择分组后导出该组全部账号的变化对比 PNG'
        }, '按组导出');
        api('/api/trackers/groups?kind=' + encodeURIComponent(kind))
          .then(function (result) {
            (result.groups || []).forEach(function (group) {
              groupSel.appendChild(h('option', { value: group.id },
                group.name + '（' + group.id + '）'));
            });
            if (!(result.groups || []).length) {
              groupSel.disabled = true;
              groupSel.firstChild.textContent = '暂无分组（桌面端账号配置）';
            }
          }).catch(function () {
            groupSel.disabled = true;
            groupSel.firstChild.textContent = '分组读取失败';
          });
        groupSel.addEventListener('change', function () {
          exportBtn.disabled = !groupSel.value;
        });
        exportBtn.addEventListener('click', function () {
          if (!groupSel.value) return;
          exportBtn.disabled = true;
          downloadPost('/api/trackers/group-export', {
            kind: kind, group_id: groupSel.value
          }, '分组变化对比.png').then(function () {
            toast('已导出分组变化对比图', 'success');
          }).catch(function (error) {
            toast('导出失败：' + error.message, 'error');
          }).then(function () {
            exportBtn.disabled = !groupSel.value;
          });
        });
        var collectBtn = h('button', {
          class: 'btn primary', type: 'button'
        }, '获取全部');
        var jobHost = h('div');
        var running = false;
        collectBtn.addEventListener('click', function () {
          if (running) return;
          running = true;
          collectBtn.disabled = true;
          collectBtn.textContent = '采集中…';
          post('/api/trackers/collect', {
            kind: kind, platform: platformSel.value
          }).then(function (result) {
            watchJob(jobHost, result.job, function (job) {
              running = false;
              collectBtn.disabled = false;
              collectBtn.textContent = '获取全部';
              var outcome = job.result || {};
              toast(outcome.summary || '采集完成',
                outcome.failed ? 'warning' : 'success');
              renderTrackerPage(root, kind);
            }, {
              cancelable: true,
              onSettled: function (job) {
                running = false;
                collectBtn.disabled = false;
                collectBtn.textContent = '获取全部';
                if (job.status === 'error') {
                  toast('采集失败：' + (job.error || '未知错误'), 'error');
                }
              }
            });
          }).catch(function (error) {
            running = false;
            collectBtn.disabled = false;
            collectBtn.textContent = '获取全部';
            toast('采集失败：' + error.message, 'error');
          });
        });
        root.appendChild(h('div', { class: 'toolbar' },
          refreshBtn, groupSel, exportBtn, collectBtn,
          h('span', { class: 'muted small' }, '平台'), platformSel,
          h('span', { class: 'spacer' }),
          h('span', { class: 'muted small' },
            '共 ' + fmtNumber(data.total) + ' 个账号；'
            + '「获取全部」按当前平台筛选采集；单账号可导出变化对比图，选分组可整组合成一张 PNG。')));
        root.appendChild(jobHost);
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
    var exportBtn = h('button', {
      class: 'btn mini', type: 'button', title: '导出最近两次变化对比 PNG'
    }, '导出图片');
    exportBtn.addEventListener('click', function () {
      exportBtn.disabled = true;
      downloadPost('/api/trackers/export', {
        kind: kind, name: row.name, platform: row.platform || ''
      }, (row.name || '账号') + '.png').then(function () {
        exportBtn.disabled = false;
        toast('已导出变化对比图', 'success');
      }).catch(function (error) {
        exportBtn.disabled = false;
        toast('导出失败：' + error.message, 'error');
      });
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
      h('div', { class: 'col-tools' }, historyBtn, exportBtn),
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
      // Table state belongs to this page; shared sorting keeps three-state semantics.
      function groupTable(key, rows, columns, className) {
        var sort = state.fangroups.tables[key] || { order: '', asc: true };
        state.fangroups.tables[key] = sort;
        var holder = h('div');
        var wrap = h('div', { class: 'table-wrap' });
        var table = h('table', { class: 'db-table fg-table ' + (className || '') });
        var controls = h('div', { class: 'toolbar fg-table-controls' });
        var resetWidths = columns.map(function (column) { return column.width || 100; });
        var currentWidths = resetWidths.slice();
        var headers = [];
        function sizeColumns(values) {
          var total = values.reduce(function (sum, value) { return sum + value; }, 0);
          headers.forEach(function (header, index) {
            header.style.width = (values[index] / total * 100) + '%';
          });
        }
        function fitColumns() {
          var canvas = document.createElement('canvas');
          var measure = canvas.getContext('2d');
          measure.font = getComputedStyle(table).font;
          var values = columns.map(function (column, index) {
            if (!column.key) return resetWidths[index];
            var longest = measure.measureText(column.label).width;
            rows.forEach(function (row) {
              var value = row[column.key];
              longest = Math.max(longest, measure.measureText(value == null ? '' : String(value)).width);
            });
            return Math.max(48, Math.min(280, longest + 24));
          });
          currentWidths = values;
          sizeColumns(currentWidths);
        }
        function drawTable() {
          var head = h('tr');
          headers = [];
          columns.forEach(function (column) {
            var active = sort.order === column.key;
            var header = h('th', {
              class: (column.extra ? 'fg-extra ' : '')
                + (column.numeric ? 'num ' : '') + (column.key ? 'sortable' : ''),
              'data-key': column.key || '',
              title: column.label,
              'aria-sort': active ? (sort.asc ? 'ascending' : 'descending') : 'none'
            }, column.label + (active ? (sort.asc ? ' ↑' : ' ↓') : ''));
            if (column.key) {
              header.tabIndex = 0;
              function changeSort() { cycleSort(sort, column.key); drawTable(); }
              header.addEventListener('click', changeSort);
              header.addEventListener('keydown', function (event) {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault(); changeSort();
                }
              });
            }
            headers.push(header);
            head.appendChild(header);
          });
          var body = h('tbody');
          var sortableRows = rows.filter(function (row) {
            var value = row[sort.order];
            return value !== '' && value !== null && value !== undefined;
          });
          var emptyRows = rows.filter(function (row) {
            var value = row[sort.order];
            return value === '' || value === null || value === undefined;
          });
          sortRows(sortableRows, columns, sort).concat(emptyRows).forEach(function (row) {
            var tr = h('tr');
            columns.forEach(function (column) {
              var value = column.key ? row[column.key] : null;
              tr.appendChild(h('td', {
                class: (column.extra ? 'fg-extra ' : '') + (column.numeric ? 'num' : ''),
                title: column.key && value != null ? String(value) : null
              }, column.render ? column.render(row) : (value == null || value === '' ? '-' : value)));
            });
            body.appendChild(tr);
          });
          table.replaceChildren(h('thead', null, head), body);
          sizeColumns(currentWidths);
        }
        controls.appendChild(h('span', { class: 'grow' }));
        controls.appendChild(h('button', { class: 'btn mini', type: 'button', onclick: fitColumns }, '自适应'));
        controls.appendChild(h('button', { class: 'btn mini', type: 'button', onclick: function () {
          currentWidths = resetWidths.slice(); sizeColumns(currentWidths);
        } }, '重置'));
        table.addEventListener('contextmenu', function (event) {
          if (!event.target.closest('th')) return;
          event.preventDefault();
          var previous = holder.querySelector('.fg-column-menu');
          if (previous) previous.remove();
          var menu = h('div', { class: 'fg-column-menu', role: 'menu' });
          [['列宽自适应', fitColumns], ['重置列宽', function () {
            currentWidths = resetWidths.slice(); sizeColumns(currentWidths);
          }]]
            .forEach(function (action) {
              menu.appendChild(h('button', {
                class: 'btn', type: 'button', role: 'menuitem',
                onclick: function () { action[1](); menu.remove(); }
              }, action[0]));
            });
          menu.style.left = Math.max(8, Math.min(event.clientX, innerWidth - 180)) + 'px';
          menu.style.top = Math.max(8, Math.min(event.clientY, innerHeight - 90)) + 'px';
          holder.appendChild(menu);
          menu.firstChild.focus();
          menu.addEventListener('keydown', function (keyEvent) {
            if (keyEvent.key === 'Escape') menu.remove();
          });
          menu.addEventListener('focusout', function (focusEvent) {
            if (!menu.contains(focusEvent.relatedTarget)) menu.remove();
          });
        });
        wrap.appendChild(table);
        holder.appendChild(wrap);
        holder.appendChild(controls);
        drawTable();
        return holder;
      }
      var QUERY_PLATFORMS = {
        douyin: ['抖音', 'https://www.douyin.com/user/... 或 sec_uid'],
        weibo: ['微博', 'https://weibo.com/u/3056354293 或 UID'],
        bilibili: ['B站', 'https://space.bilibili.com/1467772 或 UID']
      };
      var platformSel = h('select', { class: 'select' },
        Object.keys(QUERY_PLATFORMS).map(function (key) {
          return h('option', { value: key }, QUERY_PLATFORMS[key][0]);
        }));
      var queryInput = h('input', {
        class: 'input grow', placeholder: QUERY_PLATFORMS.douyin[1]
      });
      var platformQueryBtn = h('button', { class: 'btn primary', type: 'button' },
        '查询粉丝群');
      var queryHost = h('div', { style: 'margin-top:8px' });
      var queryJobHost = h('div');
      var querying = false;
      platformSel.addEventListener('change', function () {
        queryInput.placeholder = QUERY_PLATFORMS[platformSel.value][1];
        queryHost.replaceChildren();
      });
      platformQueryBtn.addEventListener('click', function () {
        if (querying) return;
        if (!queryInput.value.trim()) {
          toast('请粘贴主页链接或输入 UID', 'warning');
          return;
        }
        querying = true;
        platformQueryBtn.disabled = true;
        platformQueryBtn.textContent = '查询中…';
        post('/api/fangroups/query', {
          platform: platformSel.value, text: queryInput.value.trim()
        }).then(function (result) {
          watchJob(queryJobHost, result.job, function (job) {
            querying = false;
            platformQueryBtn.disabled = false;
            platformQueryBtn.textContent = '查询粉丝群';
            var outcome = job.result || {};
            toast(outcome.summary || '查询完成',
              (outcome.groups || []).length ? 'success' : 'info');
            var groups = outcome.groups || [];
            if (!groups.length) {
              queryHost.replaceChildren(empty('没有查到公开粉丝群。'));
              return;
            }
            groups.forEach(function (row) {
              var changeText = '-';
              if (row.change === null) changeText = '新群';
              else if (typeof row.change === 'number' && row.change !== 0) {
                changeText = (row.change > 0 ? '+' : '') + row.change;
              } else if (typeof row.change === 'number') changeText = '0';
              row.change_text = changeText;
            });
            queryHost.replaceChildren(
              h('div', { class: 'muted small', style: 'margin-bottom:6px' },
                (outcome.platform_label || '') + ' ' + (outcome.owner_id || '')
                + ' · ' + groups.length + ' 个群'
                + (outcome.previous_at
                  ? ' · 较 ' + outcome.previous_at : '')
                + (outcome.avatars && outcome.avatars.total
                  ? ' · 头像 ' + outcome.avatars.ok + '/'
                    + outcome.avatars.total : '')),
              groupTable('public', groups, [
                { key: 'name', label: '群名', width: 180 },
                { key: 'group_no', label: '群号', extra: true },
                { key: 'members', label: '人数', numeric: true, width: 70,
                  render: function (row) { return row.member_text || row.members || '-'; } },
                { key: 'delta', label: '变化', numeric: true, width: 65,
                  render: function (row) { return row.change_text; } },
                { key: 'tags', label: '标签', extra: true },
                { key: 'url', label: '链接', width: 70,
                  render: function (row) { return row.url ? h('a', {
                    href: row.url, target: '_blank', rel: 'noopener noreferrer'
                  }, '打开') : '-'; } }
              ], 'fg-query-table'));
          }, {
            cancelable: true,
            onSettled: function (job) {
              querying = false;
              platformQueryBtn.disabled = false;
              platformQueryBtn.textContent = '查询粉丝群';
              if (job.status === 'error') {
                toast('查询失败：' + (job.error || '未知错误'), 'error');
              }
            }
          });
        }).catch(function (error) {
          querying = false;
          platformQueryBtn.disabled = false;
          platformQueryBtn.textContent = '查询粉丝群';
          toast('查询失败：' + error.message, 'error');
        });
      });
      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'row-title' }, '查询粉丝群'),
        h('div', { class: 'toolbar', style: 'margin-top:8px' },
          h('span', { class: 'muted small' }, '平台'), platformSel,
          queryInput, platformQueryBtn),
        h('div', { class: 'muted small', style: 'margin-top:6px' },
          '抖音 / 微博 / B站 需要对应平台登录态；B站网页端只能列出当前账号'
          + '已加入的应援团。查询会保存快照并加入定时快照名单。'),
        queryHost, queryJobHost));
      var groups = data.groups || [];
      var tableHost = h('div');
      var historyHost = h('div', { style: 'margin-top:12px' });
      var refreshListBtn = h('button', {
        class: 'btn', type: 'button'
      }, '刷新列表');
      refreshListBtn.addEventListener('click', function () {
        renderFanGroups(root);
      });
      var qqRefreshBtn = h('button', { class: 'btn', type: 'button' }, '刷新全部');
      var qqRefreshJobHost = h('div');
      var qqRefreshing = false;
      var qqRefreshErrorsHost = h('div', { class: 'fg-qq-result' });

      function updateQqGroup(row) {
        var index = groups.findIndex(function (item) { return item.no === row.no; });
        if (index < 0) groups.push(row);
        else groups[index] = Object.assign({}, groups[index], row);
      }

      function displayRefresh(job) {
        var outcome = job && job.result || {};
        (outcome.results || []).forEach(updateQqGroup);
        draw();
        var failures = outcome.errors || [];
        var partial = outcome.cancelled || failures.length;
        toast(outcome.summary || (partial ? '刷新未全部完成' : '已刷新群人数'),
          failures.length ? 'warning' : (outcome.cancelled ? 'info' : 'success'));
        if (partial) {
          var rows = failures.map(function (item) {
            return { name: item.name || '-', error: item.error || '刷新失败' };
          });
          if (outcome.cancelled) {
            rows.unshift({ name: '任务状态', error: '已取消，已完成项目保留' });
          }
          qqRefreshErrorsHost.replaceChildren(groupTable('refreshErrors', rows, [
            { key: 'name', label: '群名', width: 150 },
            { key: 'error', label: '结果', width: 240 }
          ], 'fg-refresh-errors-table'));
        } else {
          qqRefreshErrorsHost.replaceChildren();
        }
      }

      qqRefreshBtn.addEventListener('click', function () {
        if (qqRefreshing) return;
        qqRefreshing = true;
        qqRefreshBtn.disabled = true;
        qqRefreshBtn.textContent = '刷新中…';
        post('/api/tools/fangroups/qq/refresh', {}).then(function (result) {
          watchJob(qqRefreshJobHost, result.job, displayRefresh, {
            cancelable: true,
            onSettled: function (job) {
              qqRefreshing = false;
              qqRefreshBtn.disabled = false;
              qqRefreshBtn.textContent = '刷新全部';
              if (job.status === 'cancelled') displayRefresh(job);
              else if (job.status === 'error') {
                toast('刷新失败：' + (job.error || '请稍后重试'), 'error');
                qqRefreshErrorsHost.replaceChildren(h('div', { class: 'error' },
                  '刷新失败：' + (job.error || '请稍后重试')));
              }
            }
          });
        }).catch(function (error) {
          qqRefreshing = false;
          qqRefreshBtn.disabled = false;
          qqRefreshBtn.textContent = '刷新全部';
          toast('刷新失败：' + error.message, 'error');
        });
      });

      function draw() {
        var subscribedGroups = groups.filter(function (row) { return row.subscribed; });
        qqRefreshBtn.disabled = qqRefreshing || subscribedGroups.length === 0;
        if (!subscribedGroups.length) {
          qqRefreshBtn.title = '请先查询并订阅 QQ 群';
        } else {
          qqRefreshBtn.removeAttribute('title');
        }
        if (!groups.length) {
          tableHost.replaceChildren(empty(
            '暂无已订阅 QQ 群。桌面端「订阅列表」添加群号或分享链接后刷新。'));
          return;
        }
        var tableRows = groups.map(function (row) {
          var item = Object.assign({}, row);
          item.change_value = row.delta;
          item.source = row.subscribed ? row.source : '未订阅';
          return item;
        });
        var card = h('div', { class: 'card fg-qq-card' },
          h('div', { class: 'fg-table-heading' },
            h('div', { class: 'row-title' }, 'QQ群订阅列表'),
            h('span', { class: 'muted small' },
              '共 ' + fmtNumber(groups.length) + ' 个群 · 已订阅 '
              + fmtNumber(data.subscribed_count || 0) + ' 个')));
        card.appendChild(groupTable('qq', tableRows, [
            { key: '', label: '', width: 48,
              render: function (row) {
                var fallback = h('span', {
                  class: 'fg-avatar fg-avatar-fallback'
                }, (row.name || '?').slice(0, 1));
                if (!row.has_avatar) return fallback;
                var avatar = h('img', {
                  class: 'fg-avatar', alt: '',
                  src: '/api/fangroups/avatar?no=' + encodeURIComponent(row.no)
                    + '&token=' + encodeURIComponent(token)
                });
                avatar.addEventListener('error', function () { avatar.replaceWith(fallback); });
                return avatar;
              } },
            { key: 'name', label: '群名', width: 150 },
            { key: 'no', label: '群号', extra: true, width: 90 },
            { key: 'members', label: '人数', numeric: true, width: 80,
              render: function (row) { return row.members == null ? '-' : fmtNumber(row.members); } },
            { key: 'change_value', label: '变化', numeric: true, extra: true, width: 70,
              render: function (row) {
                var change = row.change || '-';
                var kind = typeof row.change_value === 'number'
                  ? (row.change_value > 0 ? 'delta-up'
                    : (row.change_value < 0 ? 'delta-down' : '')) : '';
                return h('span', { class: kind }, change);
              } },
            { key: 'status', label: '状态', extra: true, width: 150 },
            { key: 'source', label: '来源', extra: true, width: 90 },
            { key: '', label: '操作', width: 88,
              render: function (row) {
                var actions = h('div', { class: 'fg-row-actions' });
                var active = state.fangroups.open === row.no;
                actions.appendChild(h('button', {
                  class: 'btn mini', type: 'button',
                  onclick: function () {
                    if (active) {
                      state.fangroups.open = '';
                      historyHost.replaceChildren();
                    } else {
                      state.fangroups.open = row.no;
                      loadHistory(row);
                    }
                    draw();
                  }
                }, active ? '收起' : '历史'));
                if (!row.subscribed) {
                  actions.appendChild(h('span', { class: 'muted small' }, '未订阅'));
                  return actions;
                }
                var button = h('button', {
                  class: 'btn mini danger', type: 'button',
                  'aria-label': '取消订阅',
                  title: '取消「' + (row.name || row.no) + '」订阅；保留人数历史和头像'
                }, '取消订阅');
                button.addEventListener('click', function () {
                  if (!window.confirm('取消订阅「' + (row.name || row.no)
                    + '」？\n\n将停止后续刷新人数，已有历史和头像保留。')) return;
                  button.disabled = true;
                  post('/api/tools/fangroups/qq/remove', {
                    no: row.no, confirmed: true
                  }).then(function (result) {
                    var sourceRow = groups.find(function (item) { return item.no === row.no; });
                    if (sourceRow) {
                      sourceRow.subscribed = false;
                      sourceRow.source = '未订阅';
                    }
                    data.subscribed_count = Math.max(0, Number(data.subscribed_count || 0) - 1);
                    qqRefreshErrorsHost.replaceChildren();
                    draw();
                    toast(result.summary || '已取消订阅，历史和头像保留', 'success');
                  }).catch(function (error) {
                    button.disabled = false;
                    toast('取消订阅失败：' + error.message, 'error');
                  });
                });
                actions.appendChild(button);
                return actions;
              } }
          ], 'fg-qq-table'));
        tableHost.replaceChildren(card);
        if (!subscribedGroups.length) {
          card.insertBefore(h('div', { class: 'muted small fg-no-subscriptions' },
            '当前没有已订阅的群。查询并订阅后可刷新人数。'), card.children[1]);
        }
        if (state.fangroups.open && !groups.some(function (row) { return row.no === state.fangroups.open; })) {
          state.fangroups.open = '';
          historyHost.replaceChildren();
        }
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
            historyHost.replaceChildren(
              h('div', { class: 'row-title', style: 'margin-bottom:8px' },
                row.name + ' · 人数历史（最新在前）'),
              groupTable('history', result.rows, [
                { key: 'time', label: '采集时间', width: 180 },
                { key: 'members', label: '人数', numeric: true, width: 90,
                  render: function (item) { return item.members == null ? '-' : fmtNumber(item.members); } },
                { key: 'name', label: '群名', width: 160 }
              ], 'fg-history-table'));
          }).catch(function (error) {
            setError(historyHost, error, function () { loadHistory(row); });
          });
      }

      var qqInput = h('input', {
        class: 'input grow',
        placeholder: '粘贴 QQ 群号（需桌面端已登录 QQ 群）或群资料分享链接'
      });
      var queryBtn = h('button', { class: 'btn primary', type: 'button' },
        '查询 / 订阅');
      var exportBtn = h('button', { class: 'btn', type: 'button' },
        '导出 Excel');
      var avatarsBtn = h('button', { class: 'btn', type: 'button' },
        '下载头像');
      var qqJobHost = h('div');
      var qqBusy = false;

      queryBtn.addEventListener('click', function () {
        if (qqBusy) return;
        if (!qqInput.value.trim()) {
          toast('请粘贴群号或分享链接', 'warning');
          return;
        }
        qqBusy = true;
        queryBtn.disabled = true;
        queryBtn.textContent = '查询中…';
        post('/api/tools/fangroups/qq/query', { text: qqInput.value })
          .then(function (result) {
            watchJob(qqJobHost, result.job, function (job) {
              var outcome = job.result || {};
              toast(outcome.summary || '已查询', 'success');
              qqInput.value = '';
              renderFanGroups(root);
            }, {
              cancelable: true,
              onSettled: function (job) {
                qqBusy = false;
                queryBtn.disabled = false;
                queryBtn.textContent = '查询 / 订阅';
                if (job.status === 'error') {
                  toast('查询失败：' + (job.error || '未知错误'), 'error');
                }
              }
            });
          }).catch(function (error) {
            qqBusy = false;
            queryBtn.disabled = false;
            queryBtn.textContent = '查询 / 订阅';
            toast('查询失败：' + error.message, 'error');
          });
      });

      exportBtn.addEventListener('click', function () {
        exportBtn.disabled = true;
        downloadPost('/api/tools/fangroups/qq/export', {},
          'QQ群人数.xlsx').then(function () {
          toast('已导出 QQ 群人数 Excel', 'success');
        }).catch(function (error) {
          toast('导出失败：' + error.message, 'error');
        }).then(function () { exportBtn.disabled = false; });
      });

      avatarsBtn.addEventListener('click', function () {
        if (qqBusy) return;
        qqBusy = true;
        avatarsBtn.disabled = true;
        avatarsBtn.textContent = '下载中…';
        post('/api/tools/fangroups/qq/avatars', {}).then(function (result) {
          watchJob(qqJobHost, result.job, function (job) {
            var outcome = job.result || {};
            toast(outcome.summary || '头像已下载', 'success');
            renderFanGroups(root);
          }, {
            cancelable: true,
            onSettled: function (job) {
              qqBusy = false;
              avatarsBtn.disabled = false;
              avatarsBtn.textContent = '下载头像';
              if (job.status === 'error') {
                toast('下载失败：' + (job.error || '未知错误'), 'error');
              }
            }
          });
        }).catch(function (error) {
          qqBusy = false;
          avatarsBtn.disabled = false;
          avatarsBtn.textContent = '下载头像';
          toast('下载失败：' + error.message, 'error');
        });
      });

      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '平台'),
          h('select', { class: 'select' },
            h('option', { value: 'qqgroup' }, 'QQ群')),
          qqInput, queryBtn),
        h('div', { class: 'toolbar', style: 'margin-top:8px' },
          exportBtn, avatarsBtn,
          h('span', { style: 'flex:1' }), refreshListBtn),
        h('div', { class: 'muted small', style: 'margin-top:6px' },
          '分享链接免登录；按群号查询需桌面端已登录 QQ 群。导出为桌面同格式 Excel，'
          + '头像按群目录保存、已有文件复用。'),
        qqJobHost));
      root.appendChild(h('div', { class: 'card fg-qq-actions' },
        h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, 'QQ群订阅与人数'),
          h('span', { class: 'grow' }), qqRefreshBtn, refreshListBtn,
          exportBtn, avatarsBtn),
        h('div', { class: 'muted small' },
          '刷新人数会为全部已订阅群保存新记录；取消订阅会保留既有人数历史和头像。'),
        qqRefreshJobHost, qqRefreshErrorsHost));
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
      var keyword = h('input', {
        class: 'input grow', placeholder: '达人名称或抖音号'
      });
      var daysSel = h('select', { class: 'select' },
        h('option', { value: '30' }, '近 30 天'),
        h('option', { value: '90' }, '近 90 天'),
        h('option', { value: '180' }, '近 180 天'),
        h('option', { value: '365' }, '近 1 年'));
      var searchBtn = h('button', { class: 'btn', type: 'button' }, '搜索达人');
      var searchHost = h('div', { style: 'margin-top:8px' });
      var fetchJobHost = h('div');
      var fetchResultHost = h('div', { class: 'muted small', style: 'margin-top:8px' });
      if (state.douyin.lastResult) {
        fetchResultHost.textContent = state.douyin.lastResult.summary || '';
        var lastErrors = state.douyin.lastResult.writeback
          && state.douyin.lastResult.writeback.errors || [];
        lastErrors.forEach(function (message) {
          fetchResultHost.appendChild(h('div', { class: 'error' }, message));
        });
      }
      var searching = false;
      var fetching = false;
      function setFetching(busy) {
        fetching = busy;
        keyword.disabled = busy;
        daysSel.disabled = busy;
        searchBtn.disabled = busy;
        searchHost.querySelectorAll('button').forEach(function (button) {
          button.disabled = busy;
          button.textContent = busy ? '抓取中…' : '抓取并回写';
        });
      }
      searchBtn.addEventListener('click', function () {
        if (searching) return;
        if (!keyword.value.trim()) {
          toast('请输入达人名称或抖音号', 'warning');
          return;
        }
        searching = true;
        searchBtn.disabled = true;
        searchBtn.textContent = '搜索中…';
        searchHost.replaceChildren(h('div', { class: 'muted small' },
          '正在搜索…'));
        post('/api/douyin-index/search', { keyword: keyword.value.trim() })
          .then(function (result) {
            searching = false;
            searchBtn.disabled = false;
            searchBtn.textContent = '搜索达人';
            var items = result.items || [];
            if (!items.length) {
              searchHost.replaceChildren(empty('没有匹配的达人。'));
              return;
            }
            var head = h('tr', null, h('th', null, '达人'),
              h('th', null, '抖音号'), h('th', { class: 'num' }, '粉丝'),
              h('th', null, '操作'));
            var body = h('tbody');
            items.forEach(function (item) {
              var btn = h('button', {
                class: 'btn mini primary', type: 'button'
              }, '抓取并回写');
              btn.addEventListener('click', function () {
                if (fetching) return;
                if (!window.confirm('抓取「' + (item.name || item.uid) + '」并回写已有资产？\n\n'
                  + '将保存指数快照，并按视频 ID 更新本次结果中已有抖音条目的点赞、评论、分享、收藏与指数来源信息。'
                  + '\n不改标题、文件和人工信息；未匹配的作品不新建，旧快照不覆盖较新数据。')) return;
                setFetching(true);
                fetchResultHost.textContent = '';
                post('/api/douyin-index/fetch', {
                  uid: item.uid, days: Number(daysSel.value) || 30, confirmed: true
                }).then(function (job) {
                  watchJob(fetchJobHost, job.job, function (done) {
                    var outcome = done.result || {};
                    state.douyin.lastResult = outcome;
                    renderDouyinIndex(root);
                  }, {
                    cancelable: true,
                    onSettled: function (done) {
                      setFetching(false);
                      if (done.status === 'error') {
                        fetchResultHost.textContent = '抓取失败：'
                          + (done.error || '请检查登录状态、网络与目录权限后重试');
                      } else if (done.status === 'cancelled') {
                        state.douyin.lastResult = done.result || {
                          summary: '已取消，已保存的快照与已完成的回写保留；可在资产库刷新查看。'
                        };
                        renderDouyinIndex(root);
                      }
                    }
                  });
                }).catch(function (error) {
                  setFetching(false);
                  fetchResultHost.textContent = '抓取失败：' + error.message;
                });
              });
              body.appendChild(h('tr', null,
                h('td', { class: 'ellipsis' }, item.name || item.uid),
                h('td', { class: 'muted small' }, item.douyin_id || '-'),
                h('td', { class: 'num' }, fmtNumber(item.fans)),
                h('td', null, btn)));
            });
            searchHost.replaceChildren(h('div', { class: 'table-wrap' },
              h('table', { class: 'db-table' },
                h('thead', null, head), body)));
          }).catch(function (error) {
            searching = false;
            searchBtn.disabled = false;
            searchBtn.textContent = '搜索达人';
            searchHost.replaceChildren(h('div', {
              class: 'muted small', style: 'color:var(--danger)'
            }, '搜索失败：' + error.message));
          });
      });
      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'row-title' }, '搜索达人并回写资产'),
        h('div', { class: 'toolbar', style: 'margin-top:8px' },
          keyword, daysSel, searchBtn,
          h('span', { class: 'muted small' },
            '搜索需要登录；抓取后保存指数快照，并按视频 ID 回写已有抖音资产。')),
        searchHost, fetchJobHost, fetchResultHost));
      if (!reports.length) {
        root.appendChild(empty(
          '暂无本地快照。可搜索达人后抓取，或到桌面端「抖音指数」抓取。'));
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
            '上方搜索达人后可抓取并回写已有资产'),
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

      var topicInput = h('input', {
        class: 'input grow',
        placeholder: '超话链接或 100808 开头的超话 ID'
      });
      var parseBtn = h('button', { class: 'btn primary', type: 'button' },
        '解析超话');
      var collectBtn = h('button', {
        class: 'btn primary', type: 'button', disabled: true
      }, '开始采集');
      var exportBtn = h('button', {
        class: 'btn', type: 'button', disabled: true
      }, '导出 Excel');
      var parsedHost = h('div');
      var jobHost = h('div');
      var activeTopic = '';
      var channels = [];
      var busy = false;

      function renderChannels() {
        if (!channels.length) {
          parsedHost.replaceChildren(empty('解析后在这里选择要采集的频道。'));
          return;
        }
        var body = h('tbody');
        channels.forEach(function (channel, index) {
          var box = h('input', { type: 'checkbox', checked: true });
          var pages = h('input', {
            class: 'input', type: 'number', value: String(channel.max_pages || 10),
            min: '1', max: '500', style: 'width:68px'
          });
          channels[index].checked = true;
          box.addEventListener('change', function () {
            channels[index].checked = box.checked;
          });
          pages.addEventListener('input', function () {
            channels[index].max_pages = Number(pages.value) || 10;
          });
          body.appendChild(h('tr', null,
            h('td', null, box),
            h('td', { class: 'ellipsis' }, channel.name || '频道'),
            h('td', { class: 'ellipsis', title: channel.flow_id,
              style: 'max-width:110px' }, channel.flow_id || '-'),
            h('td', null, pages)));
        });
        parsedHost.replaceChildren(
          h('div', { class: 'muted small', style: 'margin:8px 0' },
            '已解析「' + (data2.nick || activeTopic) + '」 · ' + channels.length
            + ' 个频道 · 每页约 15 条，1 秒 / 页'),
          h('div', { class: 'table-wrap' },
            h('table', { class: 'db-table' },
              h('thead', null, h('tr', null,
                h('th', { style: 'width:36px' }, '采集'),
                h('th', null, '频道'), h('th', null, 'flowId'),
                h('th', null, '最大页数'))), body)));
        collectBtn.disabled = false;
      }

      var data2 = {};

      parseBtn.addEventListener('click', function () {
        if (busy) return;
        if (!topicInput.value.trim()) {
          toast('请输入超话链接或 ID', 'warning');
          return;
        }
        busy = true;
        parseBtn.disabled = true;
        parseBtn.textContent = '解析中…';
        post('/api/tools/weibo-chaohua/parse', { text: topicInput.value })
          .then(function (result) {
            watchJob(jobHost, result.job, function (job) {
              var outcome = job.result || {};
              data2 = outcome;
              activeTopic = outcome.topic_id || '';
              channels = (outcome.channels || []).map(function (channel) {
                return {
                  name: channel.name, flow_id: channel.flow_id,
                  max_pages: channel.max_pages || 10, checked: true
                };
              });
              renderChannels();
              toast(outcome.summary || '解析完成', 'success');
            }, {
              cancelable: true,
              onSettled: function (job) {
                busy = false;
                parseBtn.disabled = false;
                parseBtn.textContent = '解析超话';
                if (job.status === 'error') {
                  toast('解析失败：' + (job.error || '未知错误'), 'error');
                }
              }
            });
          }).catch(function (error) {
            busy = false;
            parseBtn.disabled = false;
            parseBtn.textContent = '解析超话';
            toast('解析失败：' + error.message, 'error');
          });
      });

      collectBtn.addEventListener('click', function () {
        if (busy) return;
        var selected = channels.filter(function (channel) {
          return channel.checked;
        }).map(function (channel) {
          return { name: channel.name, flow_id: channel.flow_id,
            max_pages: channel.max_pages };
        });
        if (!selected.length) {
          toast('请至少勾选一个频道', 'warning');
          return;
        }
        busy = true;
        collectBtn.disabled = true;
        collectBtn.textContent = '采集中…';
        post('/api/tools/weibo-chaohua/collect', {
          topic_id: activeTopic, channels: selected
        }).then(function (result) {
          watchJob(jobHost, result.job, function (job) {
            var outcome = job.result || {};
            toast(outcome.summary || '采集完成',
              outcome.db_error ? 'warning' : 'success');
            if (outcome.db_error) {
              jobHost.appendChild(h('div', { class: 'muted small' },
                '入库失败：' + outcome.db_error));
            }
            exportBtn.disabled = !(outcome.total > 0);
          }, {
            cancelable: true,
            onSettled: function (job) {
              busy = false;
              collectBtn.disabled = false;
              collectBtn.textContent = '开始采集';
              if (job.status === 'error') {
                toast('采集失败：' + (job.error || '未知错误'), 'error');
              }
            }
          });
        }).catch(function (error) {
          busy = false;
          collectBtn.disabled = false;
          collectBtn.textContent = '开始采集';
          toast('采集失败：' + error.message, 'error');
        });
      });

      exportBtn.addEventListener('click', function () {
        if (!activeTopic) return;
        exportBtn.disabled = true;
        downloadPost('/api/tools/weibo-chaohua/export',
          { topic_id: activeTopic }, '超话数据.xlsx').then(function () {
          toast('超话数据已导出', 'success');
        }).catch(function (error) {
          toast('导出失败：' + error.message, 'error');
        }).then(function () { exportBtn.disabled = false; });
      });

      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'row-title', style: 'margin-bottom:8px' },
          '解析与采集'),
        h('div', { class: 'toolbar' },
          topicInput, parseBtn),
        h('div', { class: 'toolbar', style: 'margin-top:8px' },
          collectBtn, exportBtn,
          h('span', { style: 'flex:1' })),
        parsedHost, jobHost));

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
            '查看本地累计'),
          h('span', { style: 'flex:1' }), refreshBtn),
        h('div', { class: 'muted small', style: 'margin-top:6px' },
          '本地累计数据来自超话数据库；采集结果入库后可导出与桌面同格式的 Excel。')));
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

  function buildParserPlatformRange(kind, options, changed) {
    state.parserPlatforms = state.parserPlatforms || {};
    var saved = state.parserPlatforms[kind];
    var checks = [];
    var row = h('div', { class: 'toolbar', style: 'margin-top:8px' },
      h('span', { class: 'muted small' }, '平台范围'));
    (options || []).forEach(function (item) {
      var check = h('input', { type: 'checkbox', value: item.key,
        checked: !Array.isArray(saved) || saved.indexOf(item.key) >= 0 });
      checks.push(check);
      check.addEventListener('change', function () {
        state.parserPlatforms[kind] = selected();
        changed();
      });
      row.appendChild(h('label', { class: 'check' }, check, item.label));
    });
    function selected() {
      return checks.filter(function (check) { return check.checked; })
        .map(function (check) { return check.value; });
    }
    return { element: row, selected: selected,
      setBusy: function (busy) {
        checks.forEach(function (check) { check.disabled = busy; });
      } };
  }

  function renderTickets(root) {
    setLoading(root, '正在读取票务缓存…');
    api('/api/tickets').then(function (data) {
      root.replaceChildren();
      var parseInput = h('input', {
        class: 'input grow',
        placeholder: '票务链接或关键词（留空搜索封茗囧菌）'
      });
      var scopeSel = h('select', { class: 'select' },
        h('option', { value: 'recent' }, '近期（在售）'),
        h('option', { value: 'all', selected: true }, '所有（含历史）'));
      var parseBtn = h('button', { class: 'btn primary', type: 'button' }, '解析演出');
      var exportBtn = h('button', { class: 'btn', type: 'button' }, '导出 JSON');
      var resultHost = h('div', { style: 'margin-top:8px' });
      var jobHost = h('div');
      var running = false;
      var platformHint = h('div', { class: 'muted small', style: 'margin-top:6px' });
      var platformPicker = buildParserPlatformRange(
        'tickets', data.platform_options, sync);

      function sync() {
        var emptySelection = !platformPicker.selected().length;
        parseBtn.disabled = running || emptySelection;
        exportBtn.disabled = running || emptySelection;
        scopeSel.disabled = running;
        platformPicker.setBusy(running);
        platformHint.textContent = emptySelection ? '请至少勾选一个平台' : '';
        parseBtn.textContent = running ? '解析中…' : '解析演出';
      }
      function drawEvents(rows) {
        if (!rows.length) {
          resultHost.replaceChildren(empty('没有解析到演出。'));
          return;
        }
        var head = h('tr', null, h('th', null, '标题'), h('th', null, '平台'),
          h('th', null, '城市'), h('th', null, '场馆'), h('th', null, '时间'),
          h('th', null, '票价'), h('th', null, '状态'));
        var body = h('tbody');
        rows.forEach(function (row) {
          body.appendChild(h('tr', null,
            h('td', { class: 'ellipsis' }, row.url
              ? h('a', { href: row.url, target: '_blank',
                rel: 'noopener noreferrer' }, row.title || '-')
              : (row.title || '-')),
            h('td', null, row.platform || '-'),
            h('td', null, row.city || '-'),
            h('td', { class: 'ellipsis' }, row.venue || '-'),
            h('td', null, row.time || '-'),
            h('td', null, row.price || '-'),
            h('td', null, row.status || '-')));
        });
        resultHost.replaceChildren(h('div', { class: 'muted small',
          style: 'margin-bottom:6px' }, '解析结果 ' + rows.length + ' 场'),
          h('div', { class: 'table-wrap' },
            h('table', { class: 'db-table' },
              h('thead', null, head), body)));
      }
      parseBtn.addEventListener('click', function () {
        if (running) return;
        if (!platformPicker.selected().length) { sync(); return; }
        running = true;
        sync();
        post('/api/tickets/parse', {
          text: parseInput.value.trim(), scope: scopeSel.value,
          platforms: platformPicker.selected()
        }).then(function (result) {
          watchJob(jobHost, result.job, function (job) {
            running = false;
            sync();
            var outcome = job.result || {};
            toast(outcome.summary || '解析完成',
              (outcome.events || []).length ? 'success' : 'info');
            drawEvents(outcome.events || []);
          }, {
            cancelable: true,
            onSettled: function (job) {
              running = false;
              sync();
              if (job.status === 'error') {
                toast('解析失败：' + (job.error || '未知错误'), 'error');
              }
            }
          });
        }).catch(function (error) {
          running = false;
          sync();
          toast('解析失败：' + error.message, 'error');
        });
      });
      exportBtn.addEventListener('click', function () {
        if (running) return;
        if (!platformPicker.selected().length) { sync(); return; }
        running = true;
        sync();
        post('/api/tickets/export', {
          text: parseInput.value.trim(), scope: scopeSel.value,
          platforms: platformPicker.selected()
        }).then(function (result) {
          watchJob(jobHost, result.job, function (job) {
            running = false;
            sync();
            var outcome = job.result || {};
            toast(outcome.summary || '导出完成',
              outcome.failed ? 'warning' : 'success');
          }, {
            cancelable: true,
            onSettled: function (job) {
              running = false;
              sync();
              if (job.status === 'error') {
                toast('导出失败：' + (job.error || '未知错误'), 'error');
              }
            }
          });
        }).catch(function (error) {
          running = false;
          sync();
          toast('导出失败：' + error.message, 'error');
        });
      });
      parseInput.addEventListener('input', sync);
      sync();
      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '链接 / 关键词'), parseInput,
          scopeSel, parseBtn, exportBtn,
          h('span', { style: 'flex:1' }),
          h('span', { class: 'muted small' },
            data.saved_at ? '最近解析 ' + data.saved_at : '尚未解析')),
        platformPicker.element, platformHint,
        h('div', { class: 'muted small', style: 'margin-top:6px' },
          '支持大麦艺人/演出、秀动艺人/事件、B站会员购搜索或项目；'
          + '关键词与链接仅请求勾选的平台。导出按相同平台范围写入全量 JSON 与图片到「票务演出」目录。'),
        resultHost, jobHost));
      var events = data.events || [];
      if (!events.length) {
        root.appendChild(empty(
          '暂无解析缓存。桌面端「票务解析」搜索一次后会写入本地缓存。'));
      } else {
        var body = h('tbody');
        events.forEach(function (row) {
          body.appendChild(h('tr', null,
            h('td', { class: 'ellipsis' }, row.url
              ? h('a', { href: row.url, target: '_blank',
                rel: 'noopener noreferrer' }, row.title)
              : row.title),
            h('td', null, row.platform),
            h('td', null, row.city),
            h('td', { class: 'ellipsis' }, row.venue),
            h('td', null, row.show_time),
            h('td', null, row.price),
            h('td', null, row.status)));
        });
        root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
          h('div', { class: 'row-title', style: 'margin-bottom:8px' },
            '最近解析 · ' + fmtNumber(data.total) + ' 场'),
          h('div', { class: 'table-wrap' },
            h('table', { class: 'db-table' }, h('thead', null, h('tr', null,
              h('th', null, '标题'), h('th', null, '平台'),
              h('th', null, '城市'), h('th', null, '场馆'),
              h('th', null, '演出时间'), h('th', null, '票价'),
              h('th', null, '状态'))), body))));
      }
      var folders = data.folders || [];
      var folderCard = h('div', { class: 'card' },
        h('div', { class: 'row-title', style: 'margin-bottom:8px' },
          '导出目录 · ' + fmtNumber(data.folder_total) + ' 场'));
      if (!folders.length) {
        folderCard.appendChild(empty('暂无导出的演出目录。'));
      } else {
        var fbody = h('tbody');
        folders.forEach(function (row) {
          fbody.appendChild(h('tr', null,
            h('td', { class: 'ellipsis' }, row.name),
            h('td', { class: 'num' }, fmtNumber(row.images)),
            h('td', null, row.mtime)));
        });
        folderCard.appendChild(h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' }, h('thead', null, h('tr', null,
            h('th', null, '目录'), h('th', { class: 'num' }, '图片数'),
            h('th', null, '时间'))), fbody)));
      }
      root.appendChild(folderCard);
    }).catch(function (error) {
      setError(root, error, function () { renderTickets(root); });
    });
  }

  function renderShoppingHub(root) {
    root.replaceChildren();
    var TABS = [['taobao', '淘宝商品'], ['douyin_shop', '抖音商城'],
      ['bili_shop', 'B站小店'], ['xianyu', '闲鱼商品']];
    var tabs = h('div', { class: 'tabs' });
    var host = h('div');
    TABS.forEach(function (item) {
      tabs.appendChild(h('button', {
        class: 'tab' + (state.shopping.tab === item[0] ? ' active' : ''),
        type: 'button',
        onclick: function () {
          state.shopping.tab = item[0];
          tabs.querySelectorAll('.tab').forEach(function (button, index) {
            button.classList.toggle('active', TABS[index][0] === item[0]);
          });
          load();
        }
      }, item[1]));
    });
    root.appendChild(buildShoppingParse(load));
    root.appendChild(tabs);
    root.appendChild(host);

    function buildShoppingParse(reload) {
      var platformPicker = null;
      var platformHost = h('div');
      var platformHint = h('div', { class: 'muted small', style: 'margin-top:6px' },
        '正在读取平台范围…');
      var input = h('textarea', {
        class: 'textarea', rows: '3',
        id: 'shopping-input',
        placeholder: '店铺 / 商品链接或分享文案；搜索闲鱼时输入关键词，如「封茗囧菌 周边」'
      });
      var btn = h('button', { class: 'btn primary', type: 'button' }, '开始解析');
      var searchBtn = h('button', { class: 'btn', type: 'button' }, '搜索闲鱼');
      var jobHost = h('div');
      var resultHost = h('div', { style: 'margin-top:8px' });
      var running = false;
      var saveBtn = null;
      var selectionCount = null;
      var xianyuRows = [];
      var selected = new Set();
      var rowBoxes = [];
      var selectionButtons = [];
      var resultSort = { order: '', asc: true };
      function sync() {
        var platforms = platformPicker ? platformPicker.selected() : [];
        var hasSelection = platforms.length;
        var hasXianyu = platforms.indexOf('xianyu') !== -1;
        btn.disabled = running || !hasSelection;
        searchBtn.disabled = running || !hasXianyu;
        btn.textContent = running ? '解析中…' : '开始解析';
        input.disabled = running;
        if (saveBtn) saveBtn.disabled = running || !hasXianyu || !selected.size;
        if (selectionCount) selectionCount.textContent = '已勾选 ' + selected.size + ' / ' + xianyuRows.length + ' 件';
        rowBoxes.forEach(function (entry) { entry.disabled = running || !hasXianyu; });
        selectionButtons.forEach(function (button) { button.disabled = running || !hasXianyu; });
        if (platformPicker) {
          platformPicker.setBusy(running);
          platformHint.textContent = hasSelection
            ? '链接按勾选平台自动识别；关键词可点「搜索闲鱼」'
            : '请至少勾选一个平台';
        }
      }
      api('/api/tools/shopping/options').then(function (data) {
        platformPicker = buildParserPlatformRange(
          'shopping', data.platform_options, sync);
        platformHost.replaceChildren(platformPicker.element);
        sync();
      }).catch(function (error) {
        platformHint.textContent = '读取平台范围失败：' + error.message + '，请刷新后重试';
      });
      sync();

      function runShoppingJob(path, payload, action, done) {
        running = true;
        sync();
        post(path, payload).then(function (result) {
          watchJob(jobHost, result.job, function (job) {
            running = false;
            var outcome = job.result || {};
            done(outcome);
            sync();
          }, {
            cancelable: true,
            indeterminate: true,
            onSettled: function (job) {
              running = false;
              sync();
              if (job.status === 'error') {
                toast(action + '失败：' + (job.error || '请重试'), 'error');
              }
            }
          });
        }).catch(function (error) {
          running = false;
          sync();
          toast(action + '失败：' + error.message, 'error');
        });
      }

      function showResult(outcome) {
        var items = outcome.items || [];
        selected.clear();
        rowBoxes = [];
        selectionButtons = [];
        saveBtn = null;
        selectionCount = null;
        toast(outcome.summary || '已完成',
          outcome.failures && outcome.failures.length ? 'warning' : 'success');
        if (outcome.platform === 'xianyu') {
          xianyuRows = items.map(function (item) {
            var match = String(item.price || '').replace(/,/g, '').match(/\d+(?:\.\d+)?/);
            var price = match ? Number(match[0]) : null;
            return Object.assign({}, item, {
              _price: price !== null && Number.isFinite(price) ? price : null
            });
          });
          drawXianyuResults();
        } else {
          xianyuRows = [];
          if (!items.length) {
            resultHost.replaceChildren(empty('没有解析到商品，请检查链接后重试。'));
          } else {
            var body = h('tbody');
            items.forEach(function (item) {
              body.appendChild(h('tr', null,
                h('td', { class: 'ellipsis' }, item.title || '-'),
                h('td', null, item.price || '-'),
                h('td', { class: 'num' }, item.sales || '-'),
                h('td', { class: 'ellipsis' }, item.url
                  ? h('a', { href: item.url, target: '_blank',
                    rel: 'noopener noreferrer' }, item.url) : '-')));
            });
            resultHost.replaceChildren(
              h('div', { class: 'muted small', style: 'margin-bottom:6px' }, outcome.selection_note || ''),
              h('div', { class: 'table-wrap' },
                h('table', { class: 'db-table' }, h('thead', null,
                  h('tr', null, h('th', null, '商品'), h('th', null, '价格'),
                    h('th', null, '销量'), h('th', null, '链接'))), body)));
          }
        }
        reload();
      }

      function drawXianyuResults() {
        rowBoxes = [];
        selectionButtons = [];
        if (!xianyuRows.length) {
          resultHost.replaceChildren(empty('闲鱼没有匹配商品，可换关键词重新搜索。'));
          return;
        }
        var columns = [
          { label: '选择', width: 48 }, { key: 'title', label: '商品', width: 300 },
          { key: '_price', label: '价格', width: 85 },
          { key: 'seller', label: '卖家', width: 110 },
          { key: 'location', label: '地区', width: 100 },
          { label: '资料', width: 72 }, { label: '链接', width: 70 }
        ];
        var defaults = columns.map(function (column) { return column.width; });
        var widths = defaults.slice();
        var table = h('table', { class: 'db-table fg-table' });
        var tableHost = h('div');
        var headers = [];
        function setWidths(values) {
          var total = values.reduce(function (sum, value) { return sum + value; }, 0);
          headers.forEach(function (header, index) { header.style.width = (values[index] / total * 100) + '%'; });
        }
        function fitWidths() {
          var canvas = document.createElement('canvas');
          var context = canvas.getContext('2d');
          context.font = getComputedStyle(table).font;
          widths = columns.map(function (column) {
            if (!column.key) return column.width;
            var longest = context.measureText(column.label).width;
            xianyuRows.forEach(function (item) {
              var value = column.key === '_price' ? item.price : item[column.key];
              longest = Math.max(longest, context.measureText(String(value || '')).width);
            });
            return Math.max(60, Math.min(360, longest + 24));
          });
          setWidths(widths);
        }
        function resetWidths() { widths = defaults.slice(); setWidths(widths); }
        function drawTable() {
          rowBoxes = [];
          headers = [];
          var head = h('tr');
          columns.forEach(function (column) {
            var active = column.key && resultSort.order === column.key;
            var header = h('th', {
              class: column.key ? 'sortable' : '',
              'aria-sort': active ? (resultSort.asc ? 'ascending' : 'descending') : 'none'
            }, column.label + (active ? (resultSort.asc ? ' ↑' : ' ↓') : ''));
            if (column.key) {
              header.tabIndex = 0;
              function changeSort() { cycleSort(resultSort, column.key); drawTable(); sync(); }
              header.addEventListener('click', changeSort);
              header.addEventListener('keydown', function (event) {
                if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); changeSort(); }
              });
            }
            headers.push(header);
            head.appendChild(header);
          });
          var body = h('tbody');
          sortRows(xianyuRows, columns, resultSort).forEach(function (item) {
            var key = String(item.item_id || item.url);
            var box = h('input', { type: 'checkbox', 'aria-label': '勾选 ' + (item.title || '商品') });
            box.checked = selected.has(key);
            box.addEventListener('change', function () {
              if (box.checked) selected.add(key); else selected.delete(key);
              sync();
            });
            rowBoxes.push(box);
            body.appendChild(h('tr', null, h('td', null, box),
              h('td', { title: item.title || '' }, item.title || '-'),
              h('td', null, item.price || '-'),
              h('td', { title: item.seller || '' }, item.seller || '-'),
              h('td', null, item.location || '-'),
              h('td', null, item.saved ? '已入库' : '待入库'),
              h('td', null, item.url ? h('a', { href: item.url, target: '_blank',
                rel: 'noopener noreferrer' }, '打开') : '-')));
          });
          table.replaceChildren(h('thead', null, head), body);
          setWidths(widths);
        }
        var allBtn = h('button', { class: 'btn mini', type: 'button', onclick: function () {
          xianyuRows.forEach(function (item) { selected.add(String(item.item_id || item.url)); });
          drawTable(); sync();
        } }, '全选');
        var clearBtn = h('button', { class: 'btn mini', type: 'button', onclick: function () {
          selected.clear(); drawTable(); sync();
        } }, '清除');
        selectionButtons = [allBtn, clearBtn];
        selectionCount = h('span', { class: 'muted small' });
        saveBtn = h('button', { class: 'btn primary', type: 'button' }, '入库并下载');
        saveBtn.addEventListener('click', function () {
          if (running || saveBtn.disabled) return;
          var items = xianyuRows.filter(function (item) { return selected.has(String(item.item_id || item.url)); });
          if (!window.confirm('入库并下载 ' + items.length + ' 件闲鱼商品？\n\n将保存商品资料与图片到「闲鱼商品」目录；相同商品按编号去重。')) return;
          runShoppingJob('/api/tools/shopping/xianyu/save', {
            platforms: platformPicker.selected(),
            items: items.map(function (item) { return { item_id: item.item_id || '', url: item.url || '' }; })
          }, '入库下载', function (outcome) {
            (outcome.items || []).forEach(function (saved) {
              xianyuRows.forEach(function (item) {
                if (item.item_id === saved.item_id && saved.saved !== false) {
                  item.saved = true;
                  selected.delete(String(item.item_id || item.url));
                }
              });
            });
            drawTable();
            toast(outcome.summary || '已保存到闲鱼商品目录', outcome.failed || outcome.cancelled ? 'warning' : 'success');
            state.shopping.tab = 'xianyu';
            tabs.querySelectorAll('.tab').forEach(function (button, index) { button.classList.toggle('active', TABS[index][0] === 'xianyu'); });
            reload();
          });
        });
        table.addEventListener('contextmenu', function (event) {
          if (!event.target.closest('th')) return;
          event.preventDefault();
          var old = tableHost.querySelector('.fg-column-menu');
          if (old) old.remove();
          var menu = h('div', { class: 'fg-column-menu', role: 'menu' });
          [['列宽自适应', fitWidths], ['重置列宽', resetWidths]].forEach(function (action) {
            menu.appendChild(h('button', { class: 'btn', type: 'button', role: 'menuitem',
              onclick: function () { action[1](); menu.remove(); } }, action[0]));
          });
          menu.style.left = Math.max(8, Math.min(event.clientX, innerWidth - 180)) + 'px';
          menu.style.top = Math.max(8, Math.min(event.clientY, innerHeight - 90)) + 'px';
          tableHost.appendChild(menu); menu.firstChild.focus();
          menu.addEventListener('keydown', function (keyEvent) { if (keyEvent.key === 'Escape') menu.remove(); });
          menu.addEventListener('focusout', function (focusEvent) { if (!menu.contains(focusEvent.relatedTarget)) menu.remove(); });
        });
        drawTable();
        tableHost.appendChild(h('div', { class: 'table-wrap' }, table));
        resultHost.replaceChildren(
          h('div', { class: 'row-title' }, '闲鱼商品 · ' + xianyuRows.length + ' 件'), tableHost,
          h('div', { class: 'toolbar', style: 'margin-top:8px' }, allBtn, clearBtn, selectionCount,
            h('span', { style: 'flex:1' }),
            h('button', { class: 'btn mini', type: 'button', onclick: fitWidths }, '自适应'),
            h('button', { class: 'btn mini', type: 'button', onclick: resetWidths }, '重置'), saveBtn));
        sync();
      }

      btn.addEventListener('click', function () {
        if (running || !platformPicker || !platformPicker.selected().length) return;
        if (!input.value.trim()) { toast('请粘贴链接或分享文案', 'warning'); return; }
        runShoppingJob('/api/tools/shopping/parse', {
          platforms: platformPicker.selected(), text: input.value
        }, '解析', showResult);
      });
      searchBtn.addEventListener('click', function () {
        if (running || searchBtn.disabled) return;
        if (!input.value.trim()) { toast('请先输入闲鱼搜索关键词', 'warning'); return; }
        runShoppingJob('/api/tools/shopping/xianyu/search', {
          platforms: platformPicker.selected(), text: input.value, limit: 30
        }, '搜索闲鱼', showResult);
      });
      return h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('label', { class: 'row-title', for: 'shopping-input' }, '链接 / 闲鱼关键词'),
        input, platformHost, platformHint,
        h('div', { class: 'toolbar', style: 'margin-top:8px' },
          h('span', { style: 'flex:1' }), searchBtn, btn),
        resultHost, jobHost);
    }

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

    function load() {
      var kind = state.shopping.tab;
      setLoading(host, '正在读取商品目录…');
      api('/api/tools/artifacts?kind=' + encodeURIComponent(kind))
        .then(function (data) {
          host.replaceChildren();
          var groups = data.groups || [];
          host.appendChild(h('div', {
            class: 'muted small', style: 'margin-bottom:8px'
          }, '共 ' + groups.length + ' 组 · ' + fmtNumber(data.total)
            + ' 个文件 · 只读'));
          if (!groups.length) {
            host.appendChild(empty(data.note || '暂无商品目录。'));
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
              var actions = [];
              if (file.is_image) {
                actions.push(h('button', {
                  class: 'btn mini', type: 'button',
                  onclick: function () { preview(kind, file.rel); }
                }, '预览'));
              }
              actions.push(h('a', {
                class: 'btn mini',
                href: '/api/tools/artifacts/file?kind='
                  + encodeURIComponent(kind) + '&rel='
                  + encodeURIComponent(file.rel) + '&token='
                  + encodeURIComponent(token)
              }, '下载'));
              list.appendChild(h('div', { class: 'artifact-row' },
                h('span', { class: 'artifact-name', title: file.rel },
                  file.name),
                h('span', { class: 'muted small' }, fmtBytes(file.size)),
                actions));
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

  function buildPlaylistTools(reportDir) {
    var kindSel = h('select', { class: 'select' },
      h('option', { value: 'snapshot' }, '本地「完整歌曲」快照'),
      h('option', { value: 'event' }, '线下活动'),
      h('option', { value: 'playlist' }, '现有歌单链接'),
      h('option', { value: 'text' }, '粘贴歌曲清单'));
    var valueInput = h('input', {
      class: 'input', type: 'text', placeholder: '歌单链接或 ID',
      style: 'flex:1;min-width:180px'
    });
    var textArea = h('textarea', {
      class: 'textarea', rows: '5',
      placeholder: '每行一首：歌名 - 歌手（或制表符分隔）'
    });
    var eventSel = h('select', { class: 'select', style: 'flex:1' });
    var nameInput = h('input', {
      class: 'input', type: 'text', value: '封茗囧菌歌曲合集',
      placeholder: '歌单名称', style: 'flex:1;min-width:160px', maxlength: '60'
    });
    var platformHost = h('div', { class: 'toolbar' });
    var includeUncertain = h('input', { type: 'checkbox' });
    var previewBtn = h('button', { class: 'btn primary', type: 'button' }, '预览匹配');
    var applyBtn = h('button', {
      class: 'btn', type: 'button', disabled: true
    }, '创建歌单');
    var jobHost = h('div');
    var resultHost = h('div');
    var boxes = [];
    var events = [];
    var previewId = '';
    var previewData = null;
    var busy = false;
    // 预览结果表的逐首勾选（行对象 + 复选框），写入时换算成 平台 → 序号列表
    var selectionCells = [];
    var selectionLabel = null;

    function selectionCount() {
      return selectionCells.filter(function (entry) {
        return entry.row.selectable && entry.box.checked;
      }).length;
    }

    function collectSelection() {
      var out = {};
      selectionCells.forEach(function (entry) {
        var row = entry.row;
        if (!(row.platform in out)) out[row.platform] = [];
        if (row.selectable && entry.box.checked) out[row.platform].push(row.index);
      });
      return out;
    }

    function refreshSelectionCount() {
      var count = selectionCount();
      if (selectionLabel) {
        selectionLabel.textContent = '已勾选 ' + count
          + ' 首写入（「写入」列可逐首勾选）';
      }
      applyBtn.disabled = !previewId || count <= 0;
      return count;
    }

    function syncSource() {
      var kind = kindSel.value;
      valueInput.style.display = kind === 'playlist' ? '' : 'none';
      textArea.style.display = kind === 'text' ? '' : 'none';
      eventSel.style.display = kind === 'event' ? '' : 'none';
    }
    kindSel.addEventListener('change', syncSource);
    syncSource();

    api('/api/music-playlist/platforms').then(function (data) {
      events = data.events || [];
      events.forEach(function (event) {
        eventSel.appendChild(h('option', { value: String(event.row_no) },
          [event.name, event.date, event.city,
            event.song_count + ' 首'].filter(Boolean).join(' · ')));
      });
      if (!events.length) {
        eventSel.appendChild(h('option', { value: '' }, '本地暂无线下活动'));
      }
      (data.platforms || []).forEach(function (row) {
        var box = h('input', { type: 'checkbox' });
        var ok = row.supports_create && row.available;
        box.disabled = !ok;
        if (ok && (row.platform === 'netease' || row.platform === 'qq'
                   || row.platform === 'kugou')) {
          box.checked = true;
        }
        var label = h('label', {
          class: 'muted small', title: row.note || ''
        }, box, ' ' + row.name);
        boxes.push({ row: row, box: box });
        platformHost.appendChild(label);
      });
    }).catch(function (error) {
      platformHost.appendChild(h('span', { class: 'muted small' },
        '平台能力读取失败：' + error.message));
    });

    function payload() {
      var kind = kindSel.value;
      var source = { kind: kind };
      if (kind === 'playlist') source.value = valueInput.value;
      else if (kind === 'text') source.value = textArea.value;
      else if (kind === 'event') source.row_no = Number(eventSel.value) || null;
      return {
        source: source,
        name: nameInput.value,
        platforms: boxes.filter(function (item) {
          return item.box.checked && !item.box.disabled;
        }).map(function (item) { return item.row.platform; })
      };
    }

    function renderPreview(data) {
      previewData = data;
      previewId = data.preview_id || '';
      applyBtn.disabled = !previewId || !(data.chosen > 0);
      var planRows = (data.plans || []).map(function (plan) {
        var counts = plan.counts || {};
        return h('div', { class: 'muted small' },
          plan.platform_name + '：可写入 ' + (counts.matched || 0) + ' 首'
          + '（待确认 ' + (counts.uncertain || 0) + '、未找到 '
          + (counts.missing || 0) + '、重复 ' + (counts.duplicate || 0) + '）');
      });
      selectionCells = [];
      selectionLabel = h('span', { class: 'muted small' });
      function setAll(mode) {
        selectionCells.forEach(function (entry) {
          if (!entry.row.selectable) return;
          if (mode === 'all') entry.box.checked = true;
          else if (mode === 'matched') {
            entry.box.checked = entry.row.status === 'matched';
          } else entry.box.checked = false;
        });
        refreshSelectionCount();
      }
      includeUncertain.onchange = function () {
        // 顶部开关同步到结果表：勾上＝待确认整批写入，取消＝回到只写已匹配
        setAll(includeUncertain.checked ? 'all' : 'matched');
      };
      var body = h('tbody');
      (data.rows || []).forEach(function (row) {
        var box = h('input', {
          type: 'checkbox',
          checked: !!row.selected,
          disabled: !row.selectable,
          title: row.selectable
            ? '勾选＝写入这首歌，取消勾选＝这次跳过'
            : '未找到平台曲目或重复，无法写入'
        });
        box.addEventListener('change', refreshSelectionCount);
        if (row.selectable) selectionCells.push({ row: row, box: box });
        body.appendChild(h('tr', null,
          h('td', { class: 'num' }, box),
          h('td', null, h('span', { class: 'badge' }, row.platform_name)),
          h('td', { class: 'num' }, row.index),
          h('td', { class: 'ellipsis' }, row.source_title || '-'),
          h('td', { class: 'ellipsis' }, row.source_artist || '-'),
          h('td', null, row.state),
          h('td', { class: 'ellipsis' }, row.target_name || '-'),
          h('td', { class: 'ellipsis' }, row.target_artist || '-'),
          h('td', { class: 'num' }, row.score === null ? '-' : row.score)));
      });
      refreshSelectionCount();
      resultHost.replaceChildren(
        h('div', { style: 'margin-top:10px' },
          h('div', { class: 'row-title', style: 'margin-bottom:6px' },
            data.source + ' · ' + data.matched + '/' + data.total + ' 首可写入'),
          planRows,
          h('div', { class: 'toolbar', style: 'margin-top:8px' },
            selectionLabel,
            h('span', { style: 'flex:1' }),
            h('button', {
              class: 'btn small', type: 'button',
              onclick: function () { setAll('all'); }
            }, '全选'),
            h('button', {
              class: 'btn small', type: 'button',
              onclick: function () { setAll('matched'); }
            }, '只选已匹配'),
            h('button', {
              class: 'btn small', type: 'button',
              onclick: function () { setAll('none'); }
            }, '全不选')),
          h('div', { class: 'table-wrap', style: 'margin-top:8px' },
            h('table', { class: 'db-table' }, h('thead', null, h('tr', null,
              h('th', { class: 'num' }, '写入'),
              h('th', null, '平台'), h('th', { class: 'num' }, '序号'),
              h('th', null, '来源歌曲'), h('th', null, '来源歌手'),
              h('th', null, '状态'), h('th', null, '匹配歌曲'),
              h('th', null, '匹配歌手'), h('th', { class: 'num' }, '相似度'))),
            body)),
          data.truncated ? h('div', { class: 'muted small' },
            '结果过多，仅显示前 ' + (data.rows || []).length
            + ' 行；未显示的行不会被勾选写入。') : null));
    }

    previewBtn.addEventListener('click', function () {
      if (busy) return;
      busy = true;
      previewBtn.disabled = true;
      previewBtn.textContent = '匹配中…';
      applyBtn.disabled = true;
      previewId = '';
      post('/api/tools/music-playlist/preview', payload()).then(function (result) {
        watchJob(jobHost, result.job, function (job) {
          var data = job.result || {};
          toast(data.summary || '预览完成', 'success');
          if (data.report) {
            jobHost.appendChild(h('div', { class: 'muted small' },
              '预览报告：' + data.report));
          }
          renderPreview(data);
        }, {
          cancelable: true,
          onSettled: function (job) {
            busy = false;
            previewBtn.disabled = false;
            previewBtn.textContent = '预览匹配';
            if (job.status === 'error') {
              toast('预览失败：' + (job.error || '未知错误'), 'error');
            }
          }
        });
      }).catch(function (error) {
        busy = false;
        previewBtn.disabled = false;
        previewBtn.textContent = '预览匹配';
        toast('预览失败：' + error.message, 'error');
      });
    });

    applyBtn.addEventListener('click', function () {
      if (busy || !previewId) return;
      var selection = collectSelection();
      if (selectionCount() <= 0) {
        toast('还没有勾选任何要写入的歌曲', 'warning');
        return;
      }
      var lines = (previewData.plans || []).map(function (plan) {
        var picked = (selection[plan.platform] || []).length;
        return plan.platform_name + '：勾选写入 ' + picked + ' 首';
      }).join('\n');
      var detail = '歌单名称：' + (previewData.name || '') + '\n' + lines
        + (boxes.some(function (item) {
          return item.row.platform === 'kugou' && item.box.checked;
        }) ? '\n\n酷狗云歌单需先登录酷狗 App（账号与登录 → 酷狗 → App 扫码）。' : '')
        + (kindSel.value === 'event'
          ? '\n\n写完会把各平台歌单链接回填到腾讯文档「活动歌单_*」三列。' : '')
        + '\n\n写入会在各平台创建同名歌单并逐首添加，确认继续？';
      if (!window.confirm(detail)) return;
      busy = true;
      applyBtn.disabled = true;
      applyBtn.textContent = '写入中…';
      post('/api/tools/music-playlist/apply', {
        preview_id: previewId,
        platforms: payload().platforms,
        include_uncertain: includeUncertain.checked,
        selection: collectSelection()
      }).then(function (result) {
        watchJob(jobHost, result.job, function (job) {
          var data = job.result || {};
          toast(data.summary || '写入完成', 'success');
          var list = h('div');
          (data.results || []).forEach(function (item) {
            list.appendChild(h('div', { class: 'muted small' },
              '· ' + (item.platform_name || item.platform) + '：'
              + (item.stopped
                ? item.stopped
                : '已添加 ' + (item.added || []).length + ' 首，失败 '
                  + (item.failed || []).length + ' 首，跳过 '
                  + (item.skipped || []).length + ' 首')));
          });
          var wbLabels = {
            written: '已写入', unchanged: '已是该链接',
            conflict: '已有其它链接', missing_column: '缺少目标列',
            unverified: '链接校验未通过', failed: '写入失败'
          };
          if (data.writeback) {
            var wb = data.writeback;
            if ((wb.items || []).length) {
              wb.items.forEach(function (item) {
                list.appendChild(h('div', { class: 'muted small' },
                  '· 活动歌单 ' + (item.column || item.platform) + '：'
                  + (wbLabels[item.status] || item.status || '')
                  + (item.message ? '（' + item.message + '）' : '')));
              });
            } else {
              list.appendChild(h('div', { class: 'muted small' },
                '· 活动歌单：' + (wb.message || '未回写')));
            }
          }
          if (data.report) {
            list.appendChild(h('div', { class: 'muted small' },
              '执行报告：' + data.report));
          }
          resultHost.replaceChildren(list);
          previewId = '';
        }, {
          cancelable: true,
          onSettled: function (job) {
            busy = false;
            applyBtn.disabled = true;
            applyBtn.textContent = '创建歌单';
            if (job.status === 'error') {
              toast('写入失败：' + (job.error || '未知错误'), 'error');
            }
          }
        });
      }).catch(function (error) {
        busy = false;
        applyBtn.disabled = false;
        applyBtn.textContent = '创建歌单';
        toast('写入失败：' + error.message, 'error');
      });
    });

    return h('div', { class: 'card', style: 'margin-bottom:12px' },
      h('div', { class: 'row-title' }, '生成预览与创建歌单'),
      h('div', { class: 'toolbar', style: 'margin-top:8px' },
        h('span', { class: 'muted small' }, '来源'), kindSel, valueInput,
        eventSel),
      textArea,
      h('div', { class: 'toolbar', style: 'margin-top:8px' },
        h('span', { class: 'muted small' }, '歌单名称'), nameInput),
      h('div', { class: 'toolbar', style: 'margin-top:8px' },
        h('span', { class: 'muted small' }, '目标平台'), platformHost),
      h('div', { class: 'toolbar', style: 'margin-top:8px' },
        h('label', { class: 'muted small' }, includeUncertain,
          ' 同时加入待确认歌曲'),
        h('span', { style: 'flex:1' }),
        h('span', { class: 'muted small ellipsis', style: 'max-width:100%',
          title: reportDir || '' }, '报告目录：' + (reportDir || '-')),
        previewBtn, applyBtn),
      resultHost, jobHost);
  }

  function renderMusicPlaylist(root) {
    setLoading(root, '正在读取歌单报告…');
    api('/api/music-playlist').then(function (data) {
      root.replaceChildren();
      root.appendChild(buildPlaylistTools(data.report_dir));
      var reports = data.reports || [];
      var previewHost = h('div', { style: 'margin-top:12px' });
      var card = h('div', { class: 'card' },
        h('div', { class: 'row-title', style: 'margin-bottom:8px' },
          '本地报告 · ' + fmtNumber(data.total) + ' 个'));
      if (!reports.length) {
        card.appendChild(empty('暂无报告。桌面端生成预览后会写入报告目录。'));
      } else {
        var body = h('tbody');
        reports.forEach(function (row) {
          body.appendChild(h('tr', null,
            h('td', { class: 'ellipsis' }, row.name),
            h('td', null, row.kind),
            h('td', { class: 'num' }, fmtBytes(row.size)),
            h('td', null, row.mtime),
            h('td', null, h('button', {
              class: 'btn mini', type: 'button',
              onclick: function () { loadReport(row.name); }
            }, '查看'))));
        });
        card.appendChild(h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' }, h('thead', null, h('tr', null,
            h('th', null, '文件'), h('th', null, '类型'),
            h('th', { class: 'num' }, '大小'), h('th', null, '时间'),
            h('th', null, ''))), body)));
      }
      root.appendChild(card);
      root.appendChild(previewHost);

      function loadReport(name) {
        setLoading(previewHost, '正在读取报告…');
        api('/api/music-playlist/report?name=' + encodeURIComponent(name))
          .then(function (result) {
            previewHost.replaceChildren(
              h('div', { class: 'card' },
                h('div', { class: 'row-title', style: 'margin-bottom:8px' },
                  result.name),
                h('pre', {
                  class: 'log-box', style: 'max-height:520px'
                }, result.text)));
          }).catch(function (error) {
            setError(previewHost, error, function () { loadReport(name); });
          });
      }
    }).catch(function (error) {
      setError(root, error, function () { renderMusicPlaylist(root); });
    });
  }

  function buildMusicSearch() {
    var platformSel = h('select', { class: 'select' },
      h('option', { value: 'all' }, '五平台对比'),
      h('option', { value: 'netease' }, '网易云音乐'),
      h('option', { value: 'qq' }, 'QQ 音乐'),
      h('option', { value: 'kugou' }, '酷狗音乐'),
      h('option', { value: 'kuwo' }, '酷我音乐'),
      h('option', { value: '5sing' }, '5sing 音乐'),
      h('option', { value: 'kugou_lite' }, '酷狗概念版'));
    var limitSel = h('select', { class: 'select' },
      h('option', { value: '10' }, '每个平台 10 首'),
      h('option', { value: '20', selected: 'selected' }, '每个平台 20 首'),
      h('option', { value: '30' }, '每个平台 30 首'));
    var keyword = h('input', {
      class: 'input', type: 'text', placeholder: '歌曲名 / 歌手 / 专辑关键词',
      style: 'flex:1;min-width:160px'
    });
    var searchBtn = h('button', { class: 'btn primary', type: 'button' }, '搜索');
    var qualitySel = h('select', { class: 'select', 'aria-label': '下载音质' },
      h('option', { value: 'highest' }, '最高可用'));
    var qualityCatalog = {};
    function refreshQualities() {
      var choices = qualityCatalog[platformSel.value];
      if (!choices || !choices.length) return;
      var previous = qualitySel.value;
      qualitySel.replaceChildren();
      choices.forEach(function (choice) {
        qualitySel.appendChild(h('option', { value: choice.value }, choice.label));
      });
      if (choices.some(function (choice) { return choice.value === previous; })) {
        qualitySel.value = previous;
      }
    }
    platformSel.addEventListener('change', refreshQualities);
    api('/api/music-download/qualities').then(function (result) {
      qualityCatalog = result.platforms || {};
      refreshQualities();
    }).catch(function () {
      toast('音质列表读取失败，当前使用最高可用', 'warning');
    });
    var fallbackBox = h('input', { type: 'checkbox', checked: 'checked' });
    var downloadBtn = h('button', {
      class: 'btn', type: 'button', disabled: true
    }, '下载选中（0）');
    var jobHost = h('div');
    var resultHost = h('div');
    var rows = [];
    var busy = false;

    function syncSelection() {
      var count = rows.filter(function (row) { return row.checked; }).length;
      downloadBtn.textContent = '下载选中（' + count + '）';
      downloadBtn.disabled = busy || !count;
    }

    function renderRows() {
      if (!rows.length) {
        resultHost.replaceChildren(empty('输入关键词后点「搜索」，勾选结果即可下载。'));
        syncSelection();
        return;
      }
      var body = h('tbody');
      rows.forEach(function (item, index) {
        var box = h('input', { type: 'checkbox' });
        box.checked = item.checked;
        box.addEventListener('change', function () {
          rows[index].checked = box.checked;
          syncSelection();
        });
        body.appendChild(h('tr', null,
          h('td', null, box),
          h('td', null, h('span', { class: 'badge' }, item.row.platform_name)),
          h('td', { class: 'ellipsis', title: item.row.name }, item.row.name),
          h('td', { class: 'ellipsis' }, item.row.singer || '-'),
          h('td', { class: 'ellipsis' }, item.row.album || '-'),
          h('td', { class: 'num' }, item.row.duration_text || '-')));
      });
      var allBtn = h('button', { class: 'btn small', type: 'button' }, '全选');
      allBtn.addEventListener('click', function () {
        rows.forEach(function (row) { row.checked = true; });
        renderRows();
      });
      var noneBtn = h('button', { class: 'btn small', type: 'button' }, '反选');
      noneBtn.addEventListener('click', function () {
        rows.forEach(function (row) { row.checked = !row.checked; });
        renderRows();
      });
      resultHost.replaceChildren(
        h('div', { class: 'toolbar', style: 'margin:8px 0' },
          allBtn, noneBtn,
          h('span', { class: 'muted small' }, rows.length + ' 条结果')),
        h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' },
            h('thead', null, h('tr', null,
              h('th', { style: 'width:32px' }),
              h('th', null, '平台'), h('th', null, '歌名'),
              h('th', null, '歌手'), h('th', null, '专辑'),
              h('th', { class: 'num' }, '时长'))),
            body)));
      syncSelection();
    }

    searchBtn.addEventListener('click', function () {
      if (busy) return;
      if (!keyword.value.trim()) {
        toast('请输入歌曲关键词', 'warning');
        return;
      }
      busy = true;
      searchBtn.disabled = true;
      searchBtn.textContent = '搜索中…';
      post('/api/tools/music/search', {
        platform: platformSel.value, keyword: keyword.value,
        limit: limitSel.value
      }).then(function (result) {
        watchJob(jobHost, result.job, function (job) {
          busy = false;
          searchBtn.disabled = false;
          searchBtn.textContent = '搜索';
          var outcome = job.result || {};
          toast(outcome.summary || '搜索完成',
            outcome.failures && outcome.failures.length ? 'warning' : 'success');
          rows = (outcome.items || []).map(function (row) {
            return { row: row, checked: false };
          });
          renderRows();
        }, {
          cancelable: true,
          onSettled: function (job) {
            busy = false;
            searchBtn.disabled = false;
            searchBtn.textContent = '搜索';
            if (job.status === 'error') {
              toast('搜索失败：' + (job.error || '未知错误'), 'error');
            }
            syncSelection();
          }
        });
      }).catch(function (error) {
        busy = false;
        searchBtn.disabled = false;
        searchBtn.textContent = '搜索';
        toast('搜索失败：' + error.message, 'error');
      });
    });

    downloadBtn.addEventListener('click', function () {
      if (busy) return;
      var picked = rows.filter(function (row) { return row.checked; })
        .map(function (row) { return row.row; });
      if (!picked.length) {
        toast('请先勾选歌曲', 'warning');
        return;
      }
      busy = true;
      downloadBtn.disabled = true;
      downloadBtn.textContent = '下载中…';
      post('/api/tools/music/download', {
        items: picked, quality: qualitySel.value, fallback: fallbackBox.checked
      }).then(function (result) {
        watchJob(jobHost, result.job, function (job) {
          var outcome = job.result || {};
          toast(outcome.summary || '下载完成',
            outcome.failures && outcome.failures.length ? 'warning' : 'success');
          var files = outcome.files || [];
          var list = h('div');
          if (files.length) {
            list.appendChild(h('div', { class: 'muted small',
              style: 'margin:6px 0' }, '下载目录：' + (outcome.out_dir || '')));
            files.forEach(function (file) {
              var name = (file.file || '').split(/[\\/]/).pop();
              list.appendChild(h('div', { class: 'muted small' },
                '✓ ' + name + (file.preview ? '（试听）' : '')
                + (file.quality ? ' · ' + file.quality : '')
                + (file.via ? ' · ' + file.via : '')));
            });
          }
          if ((outcome.failures || []).length) {
            list.appendChild(h('div', { class: 'muted small',
              style: 'margin-top:6px;color:var(--danger,#c62828)' },
              outcome.failures.map(function (fail) {
                return '× ' + (fail.title || '') + '：' + fail.error;
              }).join('；')));
          }
          if (list.childNodes.length) {
            jobHost.appendChild(list);
          }
        }, {
          cancelable: true,
          onSettled: function (job) {
            busy = false;
            syncSelection();
            if (job.status === 'error') {
              toast('下载失败：' + (job.error || '未知错误'), 'error');
            }
          }
        });
      }).catch(function (error) {
        busy = false;
        syncSelection();
        toast('下载失败：' + error.message, 'error');
      });
    });

    keyword.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') searchBtn.click();
    });
    renderRows();
    return h('div', { class: 'card', style: 'margin-bottom:12px' },
      h('div', { class: 'row-title' }, '搜索并下载'),
      h('div', { class: 'toolbar', style: 'margin-top:8px' },
        platformSel, keyword, limitSel, searchBtn),
      h('div', { class: 'toolbar', style: 'margin-top:8px' },
        qualitySel,
        h('label', { class: 'muted small' },
          fallbackBox, ' 失败 / 试听时自动换源'),
        h('span', { style: 'flex:1' }), downloadBtn),
      resultHost, jobHost);
  }

  function renderMusicDownload(root) {
    setLoading(root, '正在读取下载目录…');
    api('/api/music-download').then(function (data) {
      root.replaceChildren();
      var formats = (data.by_ext || []).map(function (item) {
        return item.ext + ' ' + fmtNumber(item.count);
      }).join(' · ');
      root.appendChild(buildMusicSearch());
      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' },
            '共 ' + fmtNumber(data.total) + ' 首 · ' + fmtBytes(data.bytes)),
          h('span', { style: 'flex:1' }),
          h('span', { class: 'muted small', title: data.root },
            '下载目录：' + (data.root || '-'))),
        h('div', { class: 'muted small', style: 'margin-top:6px' },
          (formats ? formats + ' · ' : '')
          + (data.truncated ? '（超过 3000 个，已截断）' : '已下载文件如下'))));
      var files = data.files || [];
      if (!files.length) {
        root.appendChild(empty(
          '下载目录内暂无音频文件。桌面端「下载」页搜索并下载后会出现在这里。'));
        return;
      }
      var body = h('tbody');
      files.forEach(function (row) {
        body.appendChild(h('tr', null,
          h('td', { class: 'ellipsis', title: row.rel }, row.name),
          h('td', null, h('span', { class: 'badge' }, row.ext)),
          h('td', { class: 'num' }, fmtBytes(row.size)),
          h('td', null, row.mtime)));
      });
      root.appendChild(h('div', { class: 'card' },
        h('div', { class: 'row-title', style: 'margin-bottom:8px' },
          '本地音乐文件'),
        h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' }, h('thead', null, h('tr', null,
            h('th', null, '文件名'), h('th', null, '格式'),
            h('th', { class: 'num' }, '大小'), h('th', null, '时间'))), body))));
    }).catch(function (error) {
      setError(root, error, function () { renderMusicDownload(root); });
    });
  }

function renderMusicTag(root) {
    setLoading(root, '正在读取音乐目录与标签…');
    api('/api/music-tag?limit=120').then(function (data) {
      root.replaceChildren();
      var files = (data.files || []).slice();
      var selected = '';
      var editorHost = h('div');
      var jobHost = h('div');
      var tableHost = h('div');

      var relInput = h('input', {
        class: 'input', type: 'text', placeholder: '相对音乐目录的路径，留空 = 目录本身',
        style: 'flex:1;min-width:160px'
      });
      var addFileBtn = h('button', { class: 'btn', type: 'button' }, '添加文件');
      var addFolderBtn = h('button', { class: 'btn', type: 'button' }, '添加文件夹');

      function mergeFiles(rows) {
        rows.forEach(function (row) {
          var index = files.findIndex(function (item) { return item.rel === row.rel; });
          if (index >= 0) files[index] = Object.assign({}, files[index], row);
          else files.unshift(row);
        });
      }

      function scan(folder) {
        addFileBtn.disabled = true;
        addFolderBtn.disabled = true;
        post('/api/tools/music-tag/scan', { path: relInput.value, folder: folder })
          .then(function (result) {
            mergeFiles(result.files || []);
            renderFiles();
            toast('已加入 ' + (result.files || []).length + ' 个文件', 'success');
          }).catch(function (error) {
            toast('扫描失败：' + error.message, 'error');
          }).then(function () {
            addFileBtn.disabled = false;
            addFolderBtn.disabled = false;
          });
      }
      addFileBtn.addEventListener('click', function () { scan(false); });
      addFolderBtn.addEventListener('click', function () { scan(true); });

      function renderFiles() {
        tableHost.replaceChildren();
        if (!files.length) {
          tableHost.appendChild(empty('音乐目录内暂无音频文件。'));
          return;
        }
        var body = h('tbody');
        files.forEach(function (row) {
          var tr = h('tr', {
            style: 'cursor:pointer',
            class: row.rel === selected ? 'selected' : ''
          });
          tr.appendChild(h('td', { class: 'ellipsis', title: row.rel }, row.name));
          tr.appendChild(h('td', { class: 'ellipsis' }, row.title || '-'));
          tr.appendChild(h('td', null, row.artist || '-'));
          tr.appendChild(h('td', null, row.album || '-'));
          tr.appendChild(h('td', null, row.year || '-'));
          tr.appendChild(h('td', null, row.genre || '-'));
          tr.addEventListener('click', function () {
            selected = row.rel;
            renderFiles();
            openEditor(row);
          });
          body.appendChild(tr);
        });
        tableHost.appendChild(h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' }, h('thead', null, h('tr', null,
            h('th', null, '文件名'), h('th', null, '标题'),
            h('th', null, '艺术家'), h('th', null, '专辑'),
            h('th', null, '年份'), h('th', null, '流派'))), body)));
      }

      function openEditor(row) {
        setLoading(editorHost, '正在读取标签…');
        api('/api/music-tag/detail?rel=' + encodeURIComponent(row.rel))
          .then(function (detail) {
            var fields = detail.fields || {};
            var inputs = {};
            var grid = h('div', { class: 'form-grid' });
            [['title', '标题'], ['artist', '艺术家'], ['album', '专辑'],
              ['albumartist', '专辑艺术家'], ['year', '年份'],
              ['track', '音轨'], ['track_count', '总音轨'],
              ['disc', '碟号'], ['disc_count', '总碟数'], ['genre', '流派'],
              ['composer', '作曲'], ['lyricist', '作词'],
              ['comment', '备注']].forEach(function (pair) {
              var key = pair[0];
              var input = h('input', {
                class: 'input', type: 'text', value: fields[key] || ''
              });
              inputs[key] = input;
              grid.appendChild(h('label', null,
                h('span', { class: 'muted small' }, pair[1]), input));
            });
            var lyrics = h('textarea', {
              class: 'textarea', rows: '8',
              placeholder: '歌词（LRC 或纯文本）'
            });
            lyrics.value = fields.lyrics || '';
            var lyricHost = h('div');
            var historyHost = h('div');
            var saveBtn = h('button', { class: 'btn primary', type: 'button' },
              '写入标签');
            var lyricBtn = h('button', { class: 'btn', type: 'button' },
              '抓取歌词');
            var historyBtn = h('button', { class: 'btn', type: 'button' },
              '标签历史');
            var busy = false;
            var persistedFields = Object.assign({}, fields);

            function currentFormChanged() {
              return Object.keys(inputs).some(function (key) {
                return inputs[key].value !== String(persistedFields[key] || '');
              }) || lyrics.value !== String(persistedFields.lyrics || '');
            }

            function showHistory(data) {
              var rows = data.rows || [];
              historyHost.replaceChildren();
              if (!rows.length) {
                historyHost.appendChild(empty('该文件还没有标签历史。写入标签后会自动记录快照。'));
                return;
              }
              var card = h('div', { class: 'card', style: 'margin-top:8px' });
              card.appendChild(h('div', { class: 'row-title', style: 'margin-bottom:8px' },
                '标签历史 · ' + rows.length + ' 条（最多保留 5 条）'));
              var preview = h('div', { class: 'card', style: 'margin-top:8px' });
              preview.appendChild(h('div', { class: 'muted small' }, '选择「预览」查看快照内容。'));
              var list = h('div', { style: 'display:grid;gap:8px' });
              var labels = {
                title: '标题', artist: '艺术家', album: '专辑',
                albumartist: '专辑艺术家', year: '年份', track: '音轨',
                track_count: '总音轨', disc: '碟号', disc_count: '总碟数',
                genre: '流派', composer: '作曲', lyricist: '作词',
                comment: '备注', lyrics: '歌词'
              };
              function renderSnapshot(item) {
                preview.replaceChildren(h('div', { class: 'row-title', style: 'margin-bottom:8px' },
                  '快照内容 · ' + (item.time || '-')));
                Object.keys(labels).forEach(function (key) {
                  var value = String(item[key] || '');
                  var row = h('div', { class: 'kv' },
                    h('span', { class: 'muted' }, labels[key]),
                    h('span', { style: 'white-space:pre-wrap;overflow-wrap:anywhere' },
                      value || '-'));
                  preview.appendChild(row);
                });
              }
              rows.forEach(function (item) {
                var previewBtn = h('button', { class: 'btn mini', type: 'button' }, '预览');
                var loadBtn = h('button', { class: 'btn mini', type: 'button' }, '载入编辑器');
                previewBtn.addEventListener('click', function () {
                  renderSnapshot(item);
                });
                loadBtn.addEventListener('click', function () {
                  if (currentFormChanged() && !window.confirm(
                    '载入历史快照将替换当前未保存的修改。继续？')) return;
                  Object.keys(inputs).forEach(function (key) {
                    inputs[key].value = String(item[key] || '');
                  });
                  lyrics.value = String(item.lyrics || '');
                  toast('已载入历史快照，请检查后手动写入', 'success');
                });
                list.appendChild(h('div', { class: 'toolbar', style: 'min-width:0' },
                  h('span', { class: 'ellipsis', title: item.time || '',
                    style: 'flex:1;min-width:0' },
                    [item.time, item.title, item.artist].filter(Boolean).join(' · ')),
                  previewBtn, loadBtn));
              });
              card.appendChild(list);
              card.appendChild(preview);
              historyHost.appendChild(card);
              renderSnapshot(rows[0]);
            }

            historyBtn.addEventListener('click', function () {
              historyBtn.disabled = true;
              api('/api/music-tag/history?rel=' + encodeURIComponent(row.rel))
                .then(showHistory)
                .catch(function (error) {
                  setError(historyHost, error, function () { historyBtn.click(); });
                }).then(function () { historyBtn.disabled = false; });
            });

            saveBtn.addEventListener('click', function () {
              if (busy) return;
              busy = true;
              saveBtn.disabled = true;
              saveBtn.textContent = '写入中…';
              var payloadFields = {};
              Object.keys(inputs).forEach(function (key) {
                payloadFields[key] = inputs[key].value;
              });
              payloadFields.lyrics = lyrics.value;
              post('/api/tools/music-tag/write', {
                rel: row.rel, fields: payloadFields
              }).then(function (result) {
                watchJob(jobHost, result.job, function (job) {
                  var outcome = job.result || {};
                  toast(outcome.summary || '已写入', 'success');
                  var fresh = outcome.fields || {};
                  Object.keys(inputs).forEach(function (key) {
                    inputs[key].value = fresh[key] || '';
                  });
                  lyrics.value = fresh.lyrics || '';
                  persistedFields = Object.assign({}, fresh);
                  var target = files.find(function (item) { return item.rel === row.rel; });
                  if (target) {
                    target.title = fresh.title || '';
                    target.artist = fresh.artist || '';
                    target.album = fresh.album || '';
                    target.year = fresh.year || '';
                    target.genre = fresh.genre || '';
                    renderFiles();
                  }
                }, {
                  onSettled: function (job) {
                    busy = false;
                    saveBtn.disabled = false;
                    saveBtn.textContent = '写入标签';
                    if (job.status === 'error') {
                      toast('写入失败：' + (job.error || '未知错误'), 'error');
                    }
                  }
                });
              }).catch(function (error) {
                busy = false;
                saveBtn.disabled = false;
                saveBtn.textContent = '写入标签';
                toast('写入失败：' + error.message, 'error');
              });
            });

            lyricBtn.addEventListener('click', function () {
              if (busy) return;
              busy = true;
              lyricBtn.disabled = true;
              lyricBtn.textContent = '搜索歌词…';
              post('/api/tools/music-tag/lyric/search', {
                rel: row.rel, title: inputs.title.value,
                artist: inputs.artist.value
              }).then(function (result) {
                watchJob(jobHost, result.job, function (job) {
                  var outcome = job.result || {};
                  var candidates = outcome.candidates || [];
                  if (!candidates.length) {
                    lyricHost.replaceChildren(empty('没有找到歌词候选。'));
                    return;
                  }
                  var list = h('div');
                  candidates.forEach(function (cand) {
                    var useBtn = h('button', { class: 'btn mini', type: 'button' },
                      '使用');
                    useBtn.addEventListener('click', function () {
                      useBtn.disabled = true;
                      post('/api/tools/music-tag/lyric/apply', {
                        rel: row.rel, source: cand.source,
                        source_id: cand.source_id,
                        title: outcome.query ? outcome.query.title : '',
                        artist: outcome.query ? outcome.query.artist : ''
                      }).then(function (applied) {
                        watchJob(jobHost, applied.job, function (done) {
                          var data = done.result || {};
                          lyrics.value = data.lyrics || '';
                          toast(data.summary || '歌词已写入', 'success');
                        }, {
                          onSettled: function (failed) {
                            useBtn.disabled = false;
                            if (failed.status === 'error') {
                              toast('写入失败：' + (failed.error || ''), 'error');
                            }
                          }
                        });
                      }).catch(function (error) {
                        useBtn.disabled = false;
                        toast('写入失败：' + error.message, 'error');
                      });
                    });
                    list.appendChild(h('div', { class: 'toolbar', style: 'margin-top:4px' },
                      h('span', { class: 'badge' }, cand.source),
                      h('span', { class: 'ellipsis', style: 'flex:1' },
                        cand.title + ' · ' + (cand.artist || '-')
                        + (cand.album ? ' · ' + cand.album : '')),
                      useBtn));
                  });
                  lyricHost.replaceChildren(
                    h('div', { class: 'muted small', style: 'margin-top:8px' },
                      '歌词候选 · ' + candidates.length + ' 个'), list);
                }, {
                  onSettled: function (job) {
                    busy = false;
                    lyricBtn.disabled = false;
                    lyricBtn.textContent = '抓取歌词';
                    if (job.status === 'error') {
                      toast('搜索失败：' + (job.error || '未知错误'), 'error');
                    }
                  }
                });
              }).catch(function (error) {
                busy = false;
                lyricBtn.disabled = false;
                lyricBtn.textContent = '抓取歌词';
                toast('搜索失败：' + error.message, 'error');
              });
            });

            editorHost.replaceChildren(
              h('div', { class: 'card', style: 'margin-bottom:12px' },
                h('div', { class: 'row-title', style: 'margin-bottom:8px' },
                  '编辑标签 · ' + row.name),
                grid,
                h('div', { class: 'muted small', style: 'margin:10px 0 4px' },
                  '歌词'),
                lyrics,
                h('div', { class: 'toolbar', style: 'margin-top:8px' },
                  saveBtn, lyricBtn, historyBtn,
                  h('span', { class: 'muted small' },
                    '写入前会自动记录标签历史。')),
                lyricHost, historyHost));
            editorHost.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
          }).catch(function (error) {
            setError(editorHost, error, function () { openEditor(row); });
          });
      }

      if (!files.length) selected = '';
      renderFiles();
      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'row-title', style: 'margin-bottom:8px' },
          '音乐目录 · ' + fmtNumber(data.total)
          + (data.truncated ? '（仅前 300 个）' : '')),
        h('div', { class: 'toolbar' },
          relInput, addFileBtn, addFolderBtn,
          h('span', { style: 'flex:1' }),
          h('span', { class: 'muted small ellipsis', title: data.root,
            style: 'max-width:100%' }, '音乐目录：' + (data.root || '-'))),
        h('div', { class: 'muted small', style: 'margin-top:6px' },
          '点表格行打开编辑器；路径只能是音乐目录内的相对路径。')));
      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'row-title', style: 'margin-bottom:8px' },
          '音乐文件'),
        tableHost));
      root.appendChild(editorHost);
      root.appendChild(jobHost);

      var history = data.history || [];
      var historyCard = h('div', { class: 'card' },
        h('div', { class: 'row-title', style: 'margin-bottom:8px' },
          '标签历史 · ' + fmtNumber(history.length) + ' 条'));
      if (!history.length) {
        historyCard.appendChild(empty('暂无标签历史。写入标签后会自动记录快照。'));
      } else {
        var hbody = h('tbody');
        history.forEach(function (row) {
          hbody.appendChild(h('tr', null,
            h('td', null, row.time),
            h('td', { class: 'ellipsis' }, row.file),
            h('td', { class: 'ellipsis' }, row.title || '-'),
            h('td', null, row.artist || '-'),
            h('td', null, row.album || '-')));
        });
        historyCard.appendChild(h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' }, h('thead', null, h('tr', null,
            h('th', null, '时间'), h('th', null, '文件'),
            h('th', null, '标题'), h('th', null, '艺术家'),
            h('th', null, '专辑'))), hbody)));
      }
      root.appendChild(historyCard);
    }).catch(function (error) {
      setError(root, error, function () { renderMusicTag(root); });
    });
  }

  function renderDesktopOnly(host, title, message) {
    host.replaceChildren(h('div', { class: 'card' },
      h('div', { class: 'row-title' }, title),
      h('div', { class: 'muted small', style: 'margin-top:8px' }, message)));
  }

  function renderPerformance(root) {
    setLoading(root, '正在读取本机性能读数…');
    api('/api/performance').then(function (data) {
      root.replaceChildren();
      var cpu = data.cpu || {};
      var mem = data.memory || {};
      var proc = data.process || {};
      var settings = data.settings || {};
      var options = h('div', { class: 'toolbar', style: 'margin-bottom:8px' });
      Object.keys(data.fields || {}).forEach(function (name) {
        var field = data.fields[name];
        var input = h('input', { type: 'checkbox', name: name,
          checked: settings[name] == null ? field.default : settings[name] });
        options.appendChild(h('label', { class: 'check' }, input, field.label));
        input.addEventListener('change', function () {
          var previous = settings[name];
          var payload = {};
          payload[name] = input.checked;
          input.disabled = true;
          post('/api/performance/settings', payload).then(function (saved) {
            settings[name] = saved[name];
            input.checked = saved[name];
            toast('已保存，下次启动桌面软件时生效', 'success');
          }).catch(function (error) {
            input.checked = previous;
            toast('保存自动监控设置失败：' + error.message + '，请重试', 'error');
          }).finally(function () { input.disabled = false; });
        });
      });
      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        options,
        h('div', { class: 'toolbar' },
          ['开始检测', '停止检测', '记录标记', 'Python 快照', '导出报告']
            .map(function (text) {
              return h('button', {
                class: 'btn', type: 'button', disabled: true
              }, text);
            }),
          h('span', { class: 'muted small' },
            '桌面端采样 / 快照 / 导出与子进程明细仍在桌面端'))));
      var cards = h('div', {
        class: 'grid cols-4', style: 'margin-bottom:12px'
      });
      cards.appendChild(statCard('CPU',
        cpu.percent == null ? '-' : cpu.percent + '%',
        (cpu.cores || '-') + ' 逻辑核'));
      cards.appendChild(statCard('内存',
        mem.percent == null ? '-' : Math.round(mem.percent * 10) / 10 + '%',
        fmtBytes(mem.used || 0) + ' / ' + fmtBytes(mem.total || 0)));
      cards.appendChild(statCard('当前服务进程',
        'PID ' + (proc.pid || '-'),
        'RSS ' + fmtBytes(proc.rss || 0) + ' · 私有 '
          + fmtBytes(proc.private || 0)));
      cards.appendChild(statCard('线程 / Python',
        fmtNumber(proc.threads || 0) + ' 线程',
        'Python ' + (proc.python || '-')));
      root.appendChild(cards);

      var disks = data.disks || [];
      var diskCard = h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'row-title', style: 'margin-bottom:8px' }, '磁盘'));
      if (!disks.length) {
        diskCard.appendChild(empty('未读取到磁盘信息。'));
      } else {
        var head = h('tr', null,
          h('th', null, '分卷'), h('th', { class: 'num' }, '已用'),
          h('th', { class: 'num' }, '可用'), h('th', { class: 'num' }, '总量'),
          h('th', { class: 'num' }, '占比'));
        var body = h('tbody');
        disks.forEach(function (disk) {
          body.appendChild(h('tr', null,
            h('td', null, disk.mount),
            h('td', { class: 'num' }, fmtBytes(disk.used)),
            h('td', { class: 'num' }, fmtBytes(disk.free)),
            h('td', { class: 'num' }, fmtBytes(disk.total)),
            h('td', { class: 'num' },
              disk.percent == null ? '-' : disk.percent + '%')));
        });
        diskCard.appendChild(h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' },
            h('thead', null, head), body)));
      }
      root.appendChild(diskCard);
      root.appendChild(h('div', { class: 'muted small', style: 'margin-top:8px' },
        data.note || ''));
      var reportHost = h('div');
      root.appendChild(reportHost);
      window.PerformanceReports.mount(reportHost, { api: api, withToken: withToken, h: h,
        fmtBytes: fmtBytes, cycleSort: cycleSort, sortRows: sortRows });
    }).catch(function (error) {
      setError(root, error, function () { renderPerformance(root); });
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
      // 以本地配置（含 0 主播的六个平台）为底，连着桌面控制桥时用它的主播覆盖
      var platforms = (data.platforms || []).map(function (item) {
        return { key: item.key, name: item.name, anchors: item.anchors || [] };
      });
      var byKey = {};
      platforms.forEach(function (item) { byKey[item.key] = item; });
      ((bridge.platforms) || []).forEach(function (item) {
        if (!item || !item.key) return;
        if (byKey[item.key]) byKey[item.key].anchors = item.anchors || [];
        else {
          byKey[item.key] = { key: item.key, name: item.name, anchors: item.anchors || [] };
          platforms.push(byKey[item.key]);
        }
      });
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

      var addBtn = h('button', {
        class: 'btn', type: 'button', disabled: !connected
      }, '添加主播');
      var importBtn = h('button', {
        class: 'btn', type: 'button', disabled: !connected
      }, '批量导入');

      addBtn.addEventListener('click', function () {
        if (!connected) {
          toast('需要桌面端保持运行，才能通过控制桥写入主播', 'warning');
          return;
        }
        var platform = currentPlatform();
        var roomInput = h('input', {
          class: 'input grow', placeholder: '房间号或直播间链接'
        });
        var nameInput = h('input', {
          class: 'input', placeholder: '主播名称（可留空，识别后自动填）',
          style: 'min-width:220px'
        });
        var remarkInput = h('input', {
          class: 'input', placeholder: '备注（可选）', style: 'min-width:160px'
        });
        var monCb = h('input', { type: 'checkbox', checked: true });
        var recCb = h('input', { type: 'checkbox' });
        var infoBox = h('div', {
          class: 'muted small', style: 'margin-top:8px'
        }, '填房间号后可点「识别」补全名称与开播状态；不识别也能直接添加。');
        var inspectBtn = h('button', { class: 'btn', type: 'button' }, '识别');
        var okBtn = h('button', {
          class: 'btn primary', type: 'button'
        }, '添加');
        var overlay = h('div', { class: 'preview-overlay' });
        overlay.addEventListener('click', function (event) {
          if (event.target === overlay) overlay.remove();
        });

        inspectBtn.addEventListener('click', function () {
          var room = roomInput.value.trim();
          if (!room) {
            toast('请先填写房间号或直播间链接', 'warning');
            return;
          }
          inspectBtn.disabled = true;
          infoBox.textContent = '识别中…';
          post('/api/record/inspect', {
            platform: platform.key, room: room
          }).then(function (info) {
            inspectBtn.disabled = false;
            roomInput.value = info.room || room;
            if (!nameInput.value.trim() && info.name) {
              nameInput.value = info.name;
            }
            infoBox.textContent = '识别成功：' + (info.name || '-')
              + ' · ' + (info.live ? '直播中' : '未开播')
              + (info.online ? ' · 在线 ' + fmtNumber(info.online) : '')
              + (info.title ? ' · ' + info.title : '')
              + (info.qualities && info.qualities.length
                ? ' · 清晰度：' + info.qualities.join(' / ') : '')
              + (info.stream ? '' : (' · ' + (info.error || '未取到直播流')));
          }).catch(function (error) {
            inspectBtn.disabled = false;
            infoBox.textContent = '识别失败：' + error.message
              + '（可以只填房间号直接添加）';
          });
        });

        okBtn.addEventListener('click', function () {
          var room = roomInput.value.trim();
          if (!room) {
            toast('请先填写房间号或直播间链接', 'warning');
            return;
          }
          okBtn.disabled = true;
          post('/api/record/add', {
            platform: platform.key, room: room,
            name: nameInput.value.trim(),
            remark: remarkInput.value.trim(),
            monitoring: monCb.checked,
            want_record: recCb.checked
          }).then(function (result) {
            overlay.remove();
            toast(result.message || '已添加', 'success');
            renderRecord(host);
          }).catch(function (error) {
            okBtn.disabled = false;
            toast('添加失败：' + error.message, 'error');
          });
        });

        overlay.appendChild(h('div', { class: 'preview-box' },
          h('div', { class: 'preview-caption' },
            h('span', null, '添加主播 · ' + (platform.name || platform.key)),
            h('button', {
              class: 'btn mini', type: 'button',
              onclick: function () { overlay.remove(); }
            }, '关闭')),
          h('div', { class: 'preview-body' },
            h('div', { class: 'toolbar' }, roomInput, inspectBtn),
            h('div', { class: 'toolbar', style: 'margin-top:8px' }, nameInput),
            h('div', { class: 'toolbar', style: 'margin-top:8px' }, remarkInput),
            h('div', { class: 'toolbar', style: 'margin-top:8px' },
              h('label', { class: 'check' }, monCb, ' 加入监控（检测开播）'),
              h('label', { class: 'check' }, recCb, ' 开播即录')),
            infoBox,
            h('div', { class: 'muted small', style: 'margin-top:8px' },
              '添加会写入桌面端的录制配置（桌面为唯一写入方），'
              + '需要桌面程序保持运行。'),
            h('div', { class: 'toolbar', style: 'margin-top:10px' },
              h('span', { style: 'flex:1' }), okBtn))));
        document.body.appendChild(overlay);
        roomInput.focus();
      });

      importBtn.addEventListener('click', function () {
        if (!connected) {
          toast('需要桌面端保持运行，才能通过控制桥写入主播', 'warning');
          return;
        }
        var platform = currentPlatform();
        var area = h('textarea', {
          class: 'textarea', rows: '8',
          placeholder: '每行一个房间号或直播间链接',
          style: 'width:100%;font-family:monospace;font-size:12px'
        });
        var okBtn = h('button', {
          class: 'btn primary', type: 'button'
        }, '导入');
        var overlay = h('div', { class: 'preview-overlay' });
        overlay.addEventListener('click', function (event) {
          if (event.target === overlay) overlay.remove();
        });
        okBtn.addEventListener('click', function () {
          if (!area.value.trim()) {
            toast('请先粘贴房间号', 'warning');
            return;
          }
          okBtn.disabled = true;
          post('/api/record/import', {
            platform: platform.key, text: area.value
          }).then(function (result) {
            overlay.remove();
            toast(result.message || '已导入', 'success');
            renderRecord(host);
          }).catch(function (error) {
            okBtn.disabled = false;
            toast('导入失败：' + error.message, 'error');
          });
        });
        overlay.appendChild(h('div', { class: 'preview-box' },
          h('div', { class: 'preview-caption' },
            h('span', null, '批量导入 · ' + (platform.name || platform.key)),
            h('button', {
              class: 'btn mini', type: 'button',
              onclick: function () { overlay.remove(); }
            }, '关闭')),
          h('div', { class: 'preview-body' }, area,
            h('div', { class: 'muted small', style: 'margin-top:8px' },
              '重复或无法识别的行会跳过；导入后默认加入监控。'),
            h('div', { class: 'toolbar', style: 'margin-top:10px' },
              h('span', { style: 'flex:1' }), okBtn))));
        document.body.appendChild(overlay);
        area.focus();
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
        h('span', { class: 'spacer' }), addBtn, importBtn,
        selectAllBtn, selectNoneBtn));
      host.appendChild(h('div', { class: 'toolbar' },
        h('span', { class: 'muted small' },
          '未勾选时批量操作作用于本平台全部主播；批量修改、录制设置、推送设置、'
          + '写时间码与弹幕姬仍在桌面端。'),
        h('span', { style: 'flex:1' }), refreshBtn));
      host.appendChild(infoBar);
      host.appendChild(tableHost);
      renderRecordingRepair(host, state.record.platform);
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
  function renderRecordingRepair(host, platform) {
    var source = h('select', {
      class: 'select', 'aria-label': '待修复录制文件',
      style: 'width:100%;max-width:100%;min-width:0'
    });
    var confirmed = h('input', { type: 'checkbox' });
    var note = h('div', { class: 'muted small', style: 'margin:8px 0' },
      'FLV 修复标签和时间戳；TS 跳过损坏数据包并无损重封装。已丢失的画面或声音无法恢复。');
    var status = h('div', { class: 'muted small', role: 'status' });
    var jobHost = h('div');
    var refresh = h('button', { class: 'btn', type: 'button' }, '刷新文件');
    var run = h('button', { class: 'btn primary', type: 'button', disabled: true }, '开始修复');
    var busy = false;
    function updateRun() { run.disabled = busy || !source.value || !confirmed.checked; }
    source.addEventListener('change', function () {
      confirmed.checked = false;
      updateRun();
    });
    confirmed.addEventListener('change', updateRun);
    function loadSources() {
      refresh.disabled = true;
      source.disabled = true;
      run.disabled = true;
      confirmed.checked = false;
      status.textContent = '正在查找录制文件…';
      api('/api/record/repair/sources?platform=' + encodeURIComponent(platform))
        .then(function (data) {
          source.replaceChildren(h('option', { value: '' }, '选择已停止录制的文件'));
          (data.files || []).forEach(function (file) {
            source.appendChild(h('option', { value: file.rel, title: file.rel },
              file.rel + ' · ' + fmtBytes(file.size)));
          });
          status.textContent = data.files && data.files.length
            ? '已找到 ' + data.files.length + ' 个文件' + (data.truncated ? '（仅显示部分，请在桌面端选择其他文件）' : '')
            : '该平台录制目录中没有 FLV / TS 文件，录制结束后点「刷新文件」。';
          source.disabled = !data.files || !data.files.length;
          refresh.disabled = false;
          updateRun();
        }).catch(function (error) {
          source.replaceChildren();
          status.textContent = '查找录制文件失败：' + error.message + '，请重试。';
          refresh.disabled = false;
        });
    }
    refresh.addEventListener('click', loadSources);
    run.addEventListener('click', function () {
      if (busy || !source.value || !confirmed.checked) return;
      busy = true;
      updateRun();
      source.disabled = refresh.disabled = confirmed.disabled = true;
      function settled() {
        busy = false;
        source.disabled = refresh.disabled = confirmed.disabled = false;
        confirmed.checked = false;
        updateRun();
      }
      post('/api/record/repair', {
        platform: platform, rel: source.value, confirmed_stopped: true
      }).then(function (result) {
        watchJob(jobHost, result.job, null, {
          cancelable: true, indeterminate: true, onSettled: settled
        });
      }).catch(function (error) {
        status.textContent = '开始修复失败：' + error.message;
        settled();
      });
    });
    host.appendChild(h('div', { class: 'card', style: 'margin-top:12px' },
      h('div', { class: 'card-title' }, 'FLV / TS 修复'), note, source,
      h('label', { class: 'muted small', style: 'display:flex;align-items:center;gap:8px;margin-top:8px' },
        confirmed, '我已确认所选文件已停止录制（源文件保留，修复结果单独下载）'),
      h('div', { class: 'toolbar', style: 'margin-top:8px' },
        status, h('span', { class: 'spacer' }), refresh, run), jobHost));
    loadSources();
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
      setLoading(listHost, '正在读取本地文件…');
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
      var meta = h('div', {
        class: 'muted small', style: 'padding:0 14px 8px'
      }, '正在读取文件信息…');
      var body = h('div', { class: 'preview-body' }, '正在加载预览…');
      box.appendChild(caption);
      box.appendChild(meta);
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

      api('/api/assets/local/stat?cat=' + encodeURIComponent(category)
        + '&dir=' + encodeURIComponent(file.rel_dir || '')
        + '&name=' + encodeURIComponent(file.name))
        .then(function (info) {
          var kind = info.kind === 'image' ? '图片'
            : (info.kind === 'video' ? '视频（显示首帧）' : '文件');
          meta.textContent = [(info.dir || '根目录'), kind,
            fmtBytes(info.size), info.mtime].join(' · ');
        }).catch(function () {
          meta.textContent = [file.rel_dir || '根目录',
            file.size ? fmtBytes(file.size) : ''].filter(Boolean).join(' · ');
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
        var isTableDoc = current.kind === 'sheet' || current.kind === 'smartsheet';
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
        syncBtn.hidden = !isTableDoc;
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
          ['bridge', '中间层同步'], ['sync', isTableDoc ? '表格同步' : '正文预览'],
          ['daily', '指标历史'], ['snapshot', '采集明细'],
          ['revision', '修订记录'], ['members', '成员名单'],
          ['docs', '文档管理'], ['writeback', '映射回写'],
          ['edit', '修改 / 删除'], ['promo', '宣传图追加']
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
          if (state.tcdoc.tab === 'sync') {
            if (isTableDoc) renderSyncTab();
            else renderBodyTab();
          }
          else if (state.tcdoc.tab === 'daily') renderDailyTab();
          else if (state.tcdoc.tab === 'snapshot') renderSnapshotTab();
          else if (state.tcdoc.tab === 'revision') renderRevisionTab();
          else if (state.tcdoc.tab === 'members') renderMembersTab();
          else if (state.tcdoc.tab === 'docs') renderDocsTab();
          else if (state.tcdoc.tab === 'writeback') renderWritebackTab();
          else if (state.tcdoc.tab === 'edit') renderTcdocEdit(tabHost);
          else if (state.tcdoc.tab === 'promo') renderTcdocPromo(tabHost);
          else renderBridgeTab();
        }

        function renderBridgeTab() {
          var box = h('div');
          tabHost.appendChild(box);
          var bridgeState = state.tcdoc.bridge;
          var selected = new Set();
          var checkboxMap = {};
          var lastPreview = null;
          var busy = false;
          var refreshInfo = h('div', { class: 'muted small', style: 'margin:6px 0' });
          var gateBadge = h('span', { class: 'badge' }, '读取中…');
          var summary = h('div', { class: 'muted small', style: 'margin:8px 0' });
          var tableHost = h('div');
          var jobHost2 = h('div');
          var tableSel = h('select', { class: 'select grow' });
          var daysSel = h('select', { class: 'select' });
          var accSel = h('select', { class: 'select' },
            h('option', { value: 'default' }, '腾讯表账号'),
            h('option', { value: 'all' }, '全部账号'));
          var dupSel = h('select', { class: 'select' },
            h('option', { value: 'skip' }, '重复：跳过'),
            h('option', { value: 'fill' }, '重复：只填空'),
            h('option', { value: 'overwrite' }, '重复：覆盖自动字段'));
          var compareBtn = h('button', { class: 'btn', type: 'button' }, '重新比对');
          var refreshBtn = h('button', { class: 'btn', type: 'button' }, '同步前刷新');
          var tsvBtn = h('button', { class: 'btn', type: 'button', disabled: true }, '查看 TSV');
          var uploadBtn = h('button', {
            class: 'btn primary', type: 'button', disabled: true
          }, '上传选中');
          var optBtn = h('button', {
            class: 'btn', type: 'button', disabled: true
          }, '检查新下拉选项');
          var optAllow = h('input', { type: 'checkbox' });
          var optLabel = h('label', {
            class: 'check', style: 'display:none'
          }, optAllow, ' 允许新建下拉选项（仅本次）');
          var optPanel = h('div');
          var selLabel = h('span', { class: 'muted small' }, '已选 0 条');
          var manualBtn = h('button', {
            class: 'btn', type: 'button', disabled: true
          }, '手动录入');
          var targets = [];
          var maxUpload = 50;
          var targetsRequest = 0;
          var compareRequest = 0;
          var targetsLoading = false;

          function targetNow() {
            var key = tableSel.value;
            return targets.filter(function (item) {
              return item.doc_key + '|' + item.tab_id === key;
            })[0] || null;
          }

          function syncButtons() {
            var target = targetNow();
            var isSnapshot = !!target && target.profile === 'fragments';
            var gate = lastPreview ? lastPreview.refresh.allowed : false;
            var canUpload = !!(target && target.write_mode === 'append');
            selLabel.textContent = '已选 ' + selected.size + ' 条';
            tsvBtn.disabled = !lastPreview || !lastPreview.tsv;
            uploadBtn.disabled = busy || !canUpload || !gate
              || selected.size === 0 || selected.size > maxUpload;
            uploadBtn.textContent = busy ? '处理中…'
              : (canUpload ? '上传选中' : '该表只读');
            optBtn.disabled = busy || !canUpload || selected.size === 0;
            uploadBtn.style.display = isSnapshot ? 'none' : '';
            optBtn.style.display = isSnapshot ? 'none' : '';
            selLabel.style.display = isSnapshot ? 'none' : '';
            compareBtn.textContent = isSnapshot ? '刷新快照' : '重新比对';
            compareBtn.disabled = busy;
            refreshBtn.disabled = busy;
          }

          function applyTargetOptions() {
            tableSel.replaceChildren();
            if (!targets.length) {
              tableSel.appendChild(h('option', { value: '' },
                '本地没有已同步的表格（请先「表格同步」）'));
              tableSel.disabled = true;
              bridgeState.docKey = '';
              bridgeState.tabId = '';
              updateTargetUi();
              return;
            }
            tableSel.disabled = false;
            targets.forEach(function (item) {
              tableSel.appendChild(h('option', {
                value: item.doc_key + '|' + item.tab_id
              }, item.doc_name + ' / ' + item.tab_name
                + '（' + item.n_rows + ' 行'
                + (item.write_mode === 'append' ? '' : '，只读') + '）'));
            });
            var wanted = bridgeState.docKey + '|' + bridgeState.tabId;
            if (wanted !== '|' && targets.some(function (item) {
              return item.doc_key + '|' + item.tab_id === wanted;
            })) {
              tableSel.value = wanted;
            }
            updateTargetUi();
          }

          function updateTargetUi() {
            var target = targetNow();
            var isLives = !!target && target.profile === 'lives';
            var isSnapshot = !!target && target.profile === 'fragments';
            accSel.style.display = (isLives || isSnapshot) ? 'none' : '';
            daysSel.disabled = isSnapshot;
            manualBtn.style.display = isSnapshot ? 'none' : '';
            optAllow.checked = false;
            optLabel.style.display = 'none';
            optPanel.replaceChildren();
            dupSel.style.display = isLives ? '' : 'none';
            manualBtn.disabled = busy || !target;
            accSel.value = bridgeState.accounts;
            dupSel.value = bridgeState.dupMode;
            if (target) {
              bridgeState.docKey = target.doc_key;
              bridgeState.tabId = target.tab_id;
            }
            syncButtons();
          }

          function failList(items) {
            if (!items || !items.length) return null;
            return h('div', { class: 'muted small', style: 'margin-top:6px' },
              '未写入 ' + items.length + ' 条：'
              + items.map(function (item) {
                return (item.name || item.title || item.ref || '-')
                  + '（' + (item.reason || item.error || '未知原因') + '）';
              }).join('；'));
          }

          function runCompare() {
            if (targetsLoading) return;
            var target = targetNow();
            if (!target) {
              toast('请先选择表格', 'warning');
              return;
            }
            var request = ++compareRequest;
            busy = true;
            syncButtons();
            setLoading(tableHost, '正在比对本地数据与表格快照…');
            summary.textContent = '';
            post('/api/tcdoc/bridge/preview', {
              doc_key: target.doc_key, tab_id: target.tab_id,
              days: Number(daysSel.value) || 30,
              accounts: accSel.value, dup_mode: dupSel.value
            }).then(function (result) {
              if (request !== compareRequest) return;
              busy = false;
              lastPreview = result;
              selected.clear();
              checkboxMap = {};
              bridgeState.days = result.days;
              bridgeState.dupMode = result.dup_mode || 'skip';
              gateBadge.className = 'badge' + (result.refresh.allowed ? ' ok' : '');
              gateBadge.textContent = result.refresh.allowed
                ? '已刷新，可上传' : '需同步前刷新';
              refreshInfo.textContent = result.refresh.allowed
                ? '刷新门禁通过：' + (result.refresh.message || '')
                : result.refresh.message || '数据未刷新，上传已锁定。';
              var stats = result.stats || {};
              if (result.profile === 'fragments') {
                summary.textContent = '歌曲片段 · 本地快照 '
                  + fmtNumber(result.total) + ' 行（只读）';
                var snapshotSort = { order: '', asc: true };
                var snapshotWidths = result.columns.map(function () { return 1; });
                function drawSnapshot() {
                  var cols = result.columns.map(function (name, i) {
                    return { key: String(i), label: name };
                  });
                  var colgroup = h('colgroup');
                  var totalWidth = snapshotWidths.reduce(function (a, b) { return a + b; }, 0) || 1;
                  var head = h('tr');
                  cols.forEach(function (col, i) {
                    colgroup.appendChild(h('col', { style: 'width:'
                      + (100 * snapshotWidths[i] / totalWidth) + '%' }));
                    head.appendChild(h('th', {
                      class: 'sortable',
                      onclick: function () { cycleSort(snapshotSort, col.key); drawSnapshot(); }
                    }, col.label + (snapshotSort.order === col.key
                      ? (snapshotSort.asc ? ' ↑' : ' ↓') : '')));
                  });
                  var body = h('tbody');
                  sortRows(result.snapshot_rows || [], cols, snapshotSort).forEach(function (cells) {
                    var row = h('tr');
                    cells.forEach(function (value) {
                      row.appendChild(h('td', {
                        title: value, style: 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap'
                      }, value || '-'));
                    });
                    body.appendChild(row);
                  });
                  function fit() {
                    snapshotWidths = result.columns.map(function (name, i) {
                      return Math.min(40, Math.max(name.length, 6,
                        Math.max.apply(null, (result.snapshot_rows || []).map(function (row) {
                          return String(row[i] || '').length;
                        }))));
                    });
                    drawSnapshot();
                  }
                  function reset() {
                    snapshotWidths = result.columns.map(function () { return 1; });
                    drawSnapshot();
                  }
                  head.addEventListener('contextmenu', function (event) {
                    event.preventDefault();
                    var menu = h('div', { class: 'card', style: 'position:fixed;z-index:10000;left:'
                      + event.clientX + 'px;top:' + event.clientY + 'px' },
                      h('button', { class: 'btn mini', onclick: function () { menu.remove(); fit(); } }, '列宽自适应'),
                      h('button', { class: 'btn mini', onclick: function () { menu.remove(); reset(); } }, '重置列宽'));
                    document.body.appendChild(menu);
                    document.addEventListener('click', function () { menu.remove(); }, { once: true });
                  });
                  tableHost.replaceChildren(result.total
                    ? h('div', { class: 'table-wrap' }, h('table', {
                      class: 'db-table', style: 'table-layout:fixed;width:100%'
                    }, colgroup, h('thead', null, head), body))
                    : empty('歌曲片段快照暂无记录，请先在「表格同步」同步该子表。'),
                    h('div', { class: 'toolbar', style: 'margin-top:8px' },
                      h('span', { class: 'muted small', style: 'flex:1' }, result.note || ''),
                      h('button', { class: 'btn mini', onclick: fit }, '自适应'),
                      h('button', { class: 'btn mini', onclick: reset }, '重置')));
                }
                drawSnapshot();
                syncButtons();
                return;
              }
              summary.textContent = '待同步 ' + fmtNumber(result.total)
                + ' 条 · 已同步 ' + fmtNumber(stats.synced_groups)
                + ' · 本地 ' + fmtNumber(stats.local_items)
                + ' 条' + (stats.filtered_partner
                  ? ' · 合作方过滤 ' + fmtNumber(stats.filtered_partner) : '')
                + (stats.with_images ? ' · 含图 ' + fmtNumber(stats.with_images)
                  + ' 条（' + fmtNumber(stats.image_count) + ' 张）' : '')
                + (stats.remote_rows ? ' · 表格 ' + fmtNumber(stats.remote_rows)
                  + ' 行' : '')
                + (stats.since ? ' · 范围自 ' + stats.since : '')
                + (result.existing ? ' · 可追加图片 ' + result.existing + ' 条' : '');
              if (!result.items.length) {
                tableHost.replaceChildren(empty('当前范围没有待同步记录。'));
                syncButtons();
                return;
              }
              var head = h('tr', null,
                h('th', { style: 'width:32px' }, ''),
                h('th', null, '标题'), h('th', null, '时间'),
                h('th', null, '账号'), h('th', null, '平台'),
                h('th', null, '图片'), h('th', null, '标记'));
              var body = h('tbody');
              result.items.forEach(function (item) {
                var check = h('input', {
                  type: 'checkbox', checked: selected.has(item.key)
                });
                checkboxMap[item.key] = check;
                check.addEventListener('change', function () {
                  if (check.checked) selected.add(item.key);
                  else selected.delete(item.key);
                  syncButtons();
                });
                body.appendChild(h('tr', null,
                  h('td', null, check),
                  h('td', { title: item.title }, item.title || '-'),
                  h('td', { class: 'muted small' }, item.time || '-'),
                  h('td', { class: 'muted small' }, item.account || '-'),
                  h('td', { class: 'muted small' }, item.platform || '-'),
                  h('td', { class: 'num' }, item.images ? item.images : ''),
                  h('td', { class: 'muted small' }, item.flag || '')));
              });
              tableHost.replaceChildren(
                h('div', { class: 'table-wrap' },
                  h('table', { class: 'db-table' },
                    h('thead', null, head), body)),
                h('div', { class: 'toolbar', style: 'margin-top:8px' },
                  h('button', {
                    class: 'btn', type: 'button',
                    onclick: function () {
                      selected.clear();
                      result.items.slice(0, maxUpload).forEach(function (item) {
                        selected.add(item.key);
                      });
                      Object.keys(checkboxMap).forEach(function (key) {
                        checkboxMap[key].checked = selected.has(key);
                      });
                      syncButtons();
                    }
                  }, '全选前 ' + maxUpload + ' 条'),
                  h('button', {
                    class: 'btn ghost', type: 'button',
                    onclick: function () {
                      selected.clear();
                      Object.keys(checkboxMap).forEach(function (key) {
                        checkboxMap[key].checked = false;
                      });
                      syncButtons();
                    }
                  }, '清空选择'),
                  result.note ? h('span', { class: 'muted small' },
                    result.note) : null));
              syncButtons();
            }).catch(function (error) {
              if (request !== compareRequest) return;
              busy = false;
              syncButtons();
              setError(tableHost, error, runCompare);
            });
          }

          optBtn.addEventListener('click', function () {
            var target = targetNow();
            if (!target || !selected.size) return;
            optBtn.disabled = true;
            setLoading(optPanel, '正在读取云端字段并比对下拉选项…');
            post('/api/tcdoc/bridge/options', {
              doc_key: target.doc_key, tab_id: target.tab_id,
              days: Number(daysSel.value) || 30,
              accounts: accSel.value, keys: Array.from(selected)
            }).then(function (data) {
              var columns = data.columns || [];
              optAllow.checked = false;
              if (!columns.length) {
                optLabel.style.display = 'none';
                optPanel.replaceChildren(h('div', {
                  class: 'muted small', style: 'margin-top:6px'
                }, '勾选的记录不需要新建下拉选项。'));
                syncButtons();
                return;
              }
              var body = h('tbody');
              columns.forEach(function (item) {
                body.appendChild(h('tr', null,
                  h('td', null, item.column),
                  h('td', null, item.values.join('、'))));
              });
              optLabel.style.display = '';
              optPanel.replaceChildren(h('div', {
                class: 'table-wrap', style: 'margin-top:8px'
              }, h('table', { class: 'db-table' },
                h('thead', null, h('tr', null,
                  h('th', null, '下拉列'),
                  h('th', null, '将新建的选项值'))), body),
              h('div', { class: 'muted small', style: 'margin-top:6px' },
                '共 ' + fmtNumber(data.total) + ' 个选项值 · ' + data.note)));
              syncButtons();
            }).catch(function (error) {
              optBtn.disabled = false;
              toast('检查下拉选项失败：' + error.message, 'error');
              optPanel.replaceChildren();
            });
          });

          uploadBtn.addEventListener('click', function () {
            var target = targetNow();
            if (!target || !lastPreview || !selected.size) return;
            if (selected.size > maxUpload) {
              toast('单次最多上传 ' + maxUpload + ' 条', 'warning');
              return;
            }
            var withImages = lastPreview.items.filter(function (item) {
              return selected.has(item.key) && item.images;
            }).length;
            var allowNew = optAllow.checked
              && optLabel.style.display !== 'none';
            var ok = window.confirm('确定向「' + target.doc_name + ' / '
              + target.tab_name + '」新增 ' + selected.size + ' 条记录？\n'
              + (withImages ? '其中 ' + withImages + ' 条带本地图片（含评论区图片）。\n' : '')
              + (allowNew ? '本次允许为下拉列新建上面列出的选项值。\n' : '')
              + '只新增 / 追加，不修改或删除已有行；写入后读回校验并记录推送历史。');
            if (!ok) return;
            busy = true;
            syncButtons();
            post('/api/tcdoc/bridge/upload', {
              doc_key: target.doc_key, tab_id: target.tab_id,
              days: Number(daysSel.value) || 30,
              accounts: accSel.value, dup_mode: dupSel.value,
              keys: Array.from(selected), confirmed: true,
              allow_new_options: allowNew
            }).then(function (data) {
              watchJob(jobHost2, data.job, function (job) {
                busy = false;
                var result = job.result || {};
                toast(result.summary || '上传完成',
                  result.failed_verify ? 'warning' : 'success');
                if (jobHost2) {
                  var lines = [
                    h('div', { class: 'muted small' }, result.summary || ''),
                    failList(result.blocked || []),
                    h('div', { class: 'muted small' },
                      '新增 ' + fmtNumber(result.added) + ' 条'
                      + (result.updated ? ' · 追加 ' + result.updated + ' 条' : '')
                      + (result.skipped ? ' · 跳过 ' + result.skipped + ' 条' : '')
                      + (result.missing_images
                        ? ' · 缺图 ' + result.missing_images + ' 张' : ''))
                  ];
                  if ((result.created_options || []).length) {
                    lines.push(h('div', { class: 'muted small' },
                      '新建下拉选项：'
                      + result.created_options.map(function (item) {
                        return item.column + '（' + item.values.join('、') + '）';
                      }).join('；')));
                  }
                  jobHost2.replaceChildren(h('div', {
                    class: 'card', style: 'margin-top:8px'
                  }, lines));
                }
                selected.clear();
                runCompare();
              }, {
                cancelable: true,
                onSettled: function (job) {
                  busy = false;
                  syncButtons();
                  if (job.status === 'error') {
                    toast('上传失败：' + (job.error || '未知错误'), 'error');
                  }
                }
              });
            }).catch(function (error) {
              busy = false;
              syncButtons();
              toast('上传失败：' + error.message, 'error');
            });
          });

          compareBtn.addEventListener('click', reloadTargets);
          tableSel.addEventListener('change', function () {
            compareRequest += 1;
            busy = false;
            lastPreview = null;
            selected.clear();
            checkboxMap = {};
            summary.textContent = '';
            tableHost.replaceChildren();
            updateTargetUi();
          });
          accSel.addEventListener('change', function () {
            bridgeState.accounts = accSel.value;
            runCompare();
          });
          dupSel.addEventListener('change', function () {
            bridgeState.dupMode = dupSel.value;
            runCompare();
          });

          refreshBtn.addEventListener('click', function () {
            busy = true;
            syncButtons();
            post('/api/tcdoc/bridge/refresh', {}).then(function (data) {
              watchJob(jobHost2, data.job, function (job) {
                busy = false;
                toast('同步前刷新完成，可以上传', 'success');
                syncButtons();
                reloadTargets();
              }, {
                onSettled: function (job) {
                  busy = false;
                  syncButtons();
                  if (job.status !== 'done') {
                    toast('刷新未完成：' + (job.error || '请查看任务日志'),
                      'error');
                  }
                }
              });
            }).catch(function (error) {
              busy = false;
              syncButtons();
              toast('刷新失败：' + error.message, 'error');
            });
          });

          manualBtn.addEventListener('click', function () {
            var target = targetNow();
            if (!target || busy) return;
            var identity = { doc_key: target.doc_key, tab_id: target.tab_id };
            manualBtn.disabled = true;
            post('/api/tcdoc/bridge/manual/schema', identity).then(function (schema) {
              var overlay = h('div', { class: 'preview-overlay', tabindex: '-1' });
              var mode = h('select', { class: 'select', style: 'flex:1' },
                h('option', { value: 'new' }, '新增待复制行'));
              if (schema.playlist_allowed) {
                mode.appendChild(h('option', { value: 'playlist' }, '补充已有活动歌单'));
              }
              var fields = {};
              var panel = h('div');
              var message = h('div', { class: 'muted small', style: 'margin-top:8px;white-space:pre-wrap' });
              var tsv = h('textarea', { class: 'textarea', rows: 5, readonly: true,
                style: 'font-family:monospace;font-size:12px;display:none' });
              var copy = h('button', { class: 'btn', type: 'button' }, '复制 TSV');
              var submit = h('button', { class: 'btn primary', type: 'button' }, '生成 TSV');
              var playlistPanel = h('div', { style: 'display:none' });
              var playlistInputs = {};
              var evSel = h('select', { class: 'select', style: 'width:100%' });
              var overwrite = h('input', { type: 'checkbox' });
              var events = [];
              var dirty = false;
              function labelRow(text, input) {
                return h('label', { style: 'display:grid;grid-template-columns:30% minmax(0,1fr);gap:8px;align-items:center;margin-top:8px' },
                  h('span', { class: 'small', style: 'text-align:right;overflow-wrap:anywhere' }, text), input);
              }
              (schema.fields || []).forEach(function (field) {
                var input = h('input', { class: 'input', type: 'text', placeholder: field.name,
                  name: field.internal, style: 'width:100%;min-width:0' });
                input.addEventListener('input', function () { dirty = true; });
                fields[field.internal] = input;
                panel.appendChild(labelRow(field.name, input));
              });
              var eventNote = h('div', { class: 'muted small', style: 'margin-top:8px' });
              playlistPanel.appendChild(labelRow('活动 *', evSel));
              [['netease', '活动歌单_网易云'], ['kugou', '活动歌单_酷狗'], ['qq', '活动歌单_QQ']].forEach(function (pair) {
                var input = h('input', { class: 'input', type: 'text', placeholder: pair[0] === 'kugou' ? '完整链接或 collection_3_<uid>_<listid>_0' : '歌单链接或歌单 ID（可留空）', style: 'width:100%;min-width:0' });
                input.addEventListener('input', function () { dirty = true; });
                playlistInputs[pair[0]] = input;
                playlistPanel.appendChild(labelRow(pair[1], input));
              });
              playlistPanel.appendChild(h('label', { class: 'check small', style: 'display:block;margin-top:12px' }, overwrite, ' 覆盖该列已有的其它链接'));
              function loadEvents() {
                eventNote.textContent = '正在读取活动快照…';
                api('/api/tcdoc/bridge/events?doc_key=' + encodeURIComponent(identity.doc_key) + '&tab_id=' + encodeURIComponent(identity.tab_id)).then(function (data) {
                  events = data.events || [];
                  evSel.replaceChildren(h('option', { value: '' }, '选择一场活动'));
                  events.forEach(function (event) { evSel.appendChild(h('option', { value: String(event.row_no) }, [event.name, event.date, event.city].filter(Boolean).join(' · '))); });
                  eventNote.textContent = events.length ? '只更新已选活动的歌单列；已有其它链接默认保留。' : '本地没有线下活动快照，请先同步表格。';
                  submit.disabled = !events.length;
                }).catch(function (error) { eventNote.textContent = '读取活动失败：' + error.message; });
              }
              function updateMode() {
                var playlist = mode.value === 'playlist';
                panel.style.display = playlist ? 'none' : '';
                playlistPanel.style.display = playlist ? '' : 'none';
                submit.textContent = playlist ? '推送到腾讯文档' : '生成 TSV';
                if (playlist && !events.length) loadEvents();
              }
              mode.addEventListener('change', updateMode);
              copy.addEventListener('click', function () { tsv.focus(); tsv.select(); toast('已选中 TSV，请按 Ctrl+C 复制', 'info'); });
              submit.addEventListener('click', function () {
                if (mode.value === 'new') {
                  var values = {};
                  Object.keys(fields).forEach(function (key) { values[key] = fields[key].value.trim(); });
                  post('/api/tcdoc/bridge/manual/tsv', { doc_key: identity.doc_key, tab_id: identity.tab_id, values: values }).then(function (result) {
                    tsv.value = result.tsv || ''; tsv.style.display = ''; copy.style.display = ''; message.textContent = '已生成 1 行，请复制后到对应表末尾粘贴。'; dirty = false;
                  }).catch(function (error) { message.textContent = '生成 TSV 失败：' + error.message; });
                  return;
                }
                var selected = events.filter(function (event) { return String(event.row_no) === evSel.value; })[0];
                if (!selected) { message.textContent = '请先选择一场线下活动'; return; }
                var payload = { doc_key: identity.doc_key, tab_id: identity.tab_id, row_no: Number(evSel.value), overwrite: overwrite.checked };
                var filled = [];
                Object.keys(playlistInputs).forEach(function (key) { payload[key] = playlistInputs[key].value.trim(); if (payload[key]) filled.push(key); });
                if (!filled.length) { message.textContent = '至少填写一个平台的歌单链接'; return; }
                if (!window.confirm('补充「' + selected.name + '」的活动歌单？\n\n只更新该活动填写的平台歌单列，写入后读回核对。')) return;
                submit.disabled = true;
                post('/api/tcdoc/bridge/playlist', payload).then(function (res) {
                  watchJob(jobHost2, res.job, function (job) { var receipt = job.result || {}; if (receipt.ok) { dirty = false; close(); toast(receipt.summary || '已完成歌单补录', 'success'); } else message.textContent = receipt.summary || '没有写入，请检查回执。'; }, { onSettled: function () { submit.disabled = false; } });
                }).catch(function (error) { submit.disabled = false; message.textContent = '写入失败：' + error.message; });
              });
              function close() {
                if (dirty && !window.confirm('放弃本次已填写的内容？')) return;
                overlay.remove();
                manualBtn.disabled = false;
              }
              overlay.addEventListener('click', function (event) { if (event.target === overlay) close(); });
              overlay.appendChild(h('div', { class: 'preview-box', style: 'width:900px;max-width:96vw;max-height:90vh' },
                h('div', { class: 'preview-caption' }, h('span', null, '手动录入 · ' + schema.tab_name), h('button', { class: 'btn mini', type: 'button', onclick: close }, '关闭')),
                h('div', { class: 'preview-body', style: 'padding:12px;max-height:68vh' }, schema.playlist_allowed ? h('div', { class: 'toolbar' }, h('span', { class: 'small' }, '录入方式'), mode) : null, h('div', { class: 'muted small', style: 'margin-bottom:8px' }, '新增待复制行只生成 TSV，不写入腾讯文档。'), panel, playlistPanel, eventNote, message, tsv, copy),
                h('div', { class: 'toolbar', style: 'padding:12px;margin:0' }, h('span', { style: 'flex:1' }), h('button', { class: 'btn', type: 'button', onclick: close }, '取消'), submit)));
              copy.style.display = 'none'; updateMode(); document.body.appendChild(overlay); overlay.focus();
            }).catch(function (error) { manualBtn.disabled = false; toast('读取录入字段失败：' + error.message, 'error'); });
          });

          tsvBtn.addEventListener('click', function () {
            if (!lastPreview || !lastPreview.tsv) return;
            var area = h('textarea', {
              class: 'textarea', rows: '12', readonly: true,
              style: 'width:100%;font-family:monospace;font-size:12px'
            });
            area.value = lastPreview.tsv;
            var overlay = h('div', { class: 'preview-overlay' });
            overlay.addEventListener('click', function (event) {
              if (event.target === overlay) overlay.remove();
            });
            overlay.appendChild(h('div', { class: 'preview-box' },
              h('div', { class: 'preview-caption' },
                h('span', null, (lastPreview.profile === 'fragments' ? '快照 TSV' : '待同步 TSV')
                  + '（全选后 Ctrl+C 复制，不含表头）'),
                h('button', {
                  class: 'btn mini', type: 'button',
                  onclick: function () { overlay.remove(); }
                }, '关闭')),
              h('div', { class: 'preview-body' }, area)));
            document.body.appendChild(overlay);
            area.focus();
            area.select();
          });

          box.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
            h('div', { class: 'toolbar' },
              h('span', { class: 'muted small' }, '表格'), tableSel,
              h('span', { class: 'muted small' }, '时间'), daysSel,
              accSel, dupSel, compareBtn),
            h('div', { class: 'toolbar', style: 'margin-top:8px' },
              refreshBtn, gateBadge, tsvBtn, optBtn, uploadBtn, manualBtn,
              h('span', { style: 'flex:1' }), selLabel),
            optLabel,
            optPanel,
            refreshInfo,
            h('div', { class: 'muted small', style: 'margin-top:4px' },
              '比对只读本地快照与本地采集数据；批量上传只限「官方动态」「直播记录」且仅新增，'
              + '需先「同步前刷新」（或桌面端免校对模式），单次最多 50 条。')));
          box.appendChild(summary);
          box.appendChild(tableHost);
          box.appendChild(jobHost2);

          function reloadTargets() {
            var request = ++targetsRequest;
            compareRequest += 1;
            targetsLoading = true;
            busy = true;
            lastPreview = null;
            selected.clear();
            checkboxMap = {};
            summary.textContent = '';
            tableSel.disabled = true;
            manualBtn.disabled = true;
            syncButtons();
            setLoading(tableHost, '正在读取目标表格…');
            api('/api/tcdoc/bridge').then(function (data) {
              if (request !== targetsRequest) return;
              targetsLoading = false;
              busy = false;
              targets = data.targets || [];
              maxUpload = data.max_upload || 50;
              daysSel.replaceChildren();
              (data.days || []).forEach(function (item) {
                daysSel.appendChild(h('option', {
                  value: item.value, selected: item.value === bridgeState.days
                }, item.label));
              });
              if (!daysSel.value) daysSel.value = String(bridgeState.days);
              gateBadge.className = 'badge' + (data.refresh.allowed ? ' ok' : '');
              gateBadge.textContent = data.refresh.allowed
                ? '已刷新，可上传' : '需同步前刷新';
              refreshInfo.textContent = (data.refresh.allowed
                ? '刷新门禁通过：' : '') + (data.refresh.message || '')
                + (data.bypass ? '（桌面端已开启免校对模式）' : '');
              applyTargetOptions();
              if (targets.length) runCompare();
              else {
                tableHost.replaceChildren(empty(
                  '当前范围没有可用表格，可调整子表范围或先同步表格。'));
              }
            }).catch(function (error) {
              if (request !== targetsRequest) return;
              targetsLoading = false;
              busy = false;
              tableSel.disabled = !targets.length;
              updateTargetUi();
              setError(tableHost, error, reloadTargets);
            });
          }
          reloadTargets();
        }

        function renderBodyTab() {
          var docKey = state.tcdoc.docKey;
          var pages = [];
          var busy = false;
          var pageSelect = h('select', { class: 'select grow', 'aria-label': '正文页面' });
          var exportText = h('button', { class: 'btn', type: 'button', disabled: true,
            title: '导出当前预览的文字；图片保留在本地存档中' }, '导出文字');
          var fetchBody = h('button', { class: 'btn primary', type: 'button' }, '抓取正文');
          var note = h('div', { class: 'muted small', style: 'margin:8px 0' });
          var content = h('div', { class: 'card', style: 'max-height:65vh;overflow:auto' });
          var toolbar = h('div', { class: 'toolbar' },
            h('span', { class: 'muted small' }, '页面'), pageSelect,
            h('span', { class: 'spacer' }), exportText, fetchBody);
          tabHost.appendChild(toolbar);
          tabHost.appendChild(note);
          tabHost.appendChild(content);

          function currentPages() {
            if (pageSelect.value === 'all') return pages;
            return pages.filter(function (item) { return item.id === pageSelect.value; });
          }

          function imageUrl(pageId, imageKey) {
            return withToken('/api/tcdoc/body/image?doc_key=' + encodeURIComponent(docKey)
              + '&page_id=' + encodeURIComponent(pageId)
              + '&image_key=' + encodeURIComponent(imageKey));
          }

          function draw() {
            content.replaceChildren();
            var visible = currentPages();
            if (!visible.length) {
              content.appendChild(empty('本地暂无正文存档，点「抓取正文」保存文字与图片。'));
              return;
            }
            visible.forEach(function (page) {
              var section = h('section', { style: 'margin-bottom:16px' });
              if (pages.length > 1) section.appendChild(h('div', {
                class: 'row-title', style: 'margin-bottom:8px'
              }, page.title || '未命名页面'));
              if (page.text || !(page.images || []).length) {
                section.appendChild(h('div', {
                  style: 'white-space:pre-wrap;overflow-wrap:anywhere'
                }, page.text || '（空白页）'));
              }
              if (!page.images_captured) section.appendChild(h('div', {
                class: 'muted small', style: 'margin-top:8px'
              }, '旧存档仅含文字，点「重新抓取」补齐正文图片。'));
              (page.images || []).forEach(function (item, index) {
                var label = item.name || ('图片 ' + (index + 1));
                var figure = h('figure', { style: 'margin:12px 0' });
                if (item.status === 'downloaded') {
                  var url = imageUrl(page.id, item.key);
                  var picture = h('img', { src: url, alt: label, loading: 'lazy',
                    decoding: 'async', referrerpolicy: 'no-referrer',
                    style: 'display:block;max-width:100%;height:auto' });
                  var original = h('a', { href: url, target: '_blank',
                    rel: 'noopener noreferrer', title: '打开原图' }, picture);
                  picture.addEventListener('error', function () {
                    original.replaceWith(h('div', { class: 'error' },
                      label + '：本地图片不可用，请重新抓取正文后重试。'));
                  });
                  figure.appendChild(original);
                  if (item.error) figure.appendChild(h('div', {
                    class: 'muted small', style: 'margin-top:4px'
                  }, '本次图片更新失败，已保留上次图片：' + item.error));
                } else {
                  figure.appendChild(h('div', { class: 'error' },
                    label + '：' + (item.error || '图片下载失败，请重新抓取后重试')));
                }
                figure.appendChild(h('figcaption', {
                  class: 'muted small', style: 'margin-top:4px;overflow-wrap:anywhere'
                }, label));
                section.appendChild(figure);
              });
              content.appendChild(section);
            });
            content.scrollTop = 0;
          }

          function applyBody(result) {
            if (state.tcdoc.docKey !== docKey || !toolbar.isConnected) return;
            pages = result.pages || [];
            var selected = pageSelect.value;
            pageSelect.replaceChildren(h('option', { value: 'all' },
              '全部（' + pages.length + ' 篇）'));
            pages.forEach(function (page) {
              pageSelect.appendChild(h('option', { value: page.id }, page.title || '未命名页面'));
            });
            if (pages.some(function (page) { return page.id === selected; })) pageSelect.value = selected;
            pageSelect.disabled = pages.length < 2;
            exportText.disabled = !pages.length || busy;
            fetchBody.textContent = pages.length ? '重新抓取' : '抓取正文';
            var images = result.images || {};
            var capture = result.capture || {};
            note.textContent = (result.last ? '本地存档于 ' + result.last + ' · ' : '')
              + '共 ' + pages.length + ' 篇 · 图片 ' + (images.downloaded || 0)
              + ' / ' + (images.total || 0) + ' 张'
              + (images.failed ? ' · ' + images.failed + ' 张失败，可重新抓取重试' : '')
              + (capture.skipped ? ' · ' + capture.skipped + ' 篇抓取失败，已保留旧存档' : '')
              + (capture.complete === false ? ' · 页面列表未完整读取，缺失页面旧存档保留' : '')
              + (capture.snapshot_failed ? ' · Markdown 快照写入失败，正文已存入数据库' : '');
            draw();
          }

          function loadBody() {
            setLoading(content, '正在读取本地正文与图片…');
            api('/api/tcdoc/body?doc_key=' + encodeURIComponent(docKey))
              .then(applyBody).catch(function (error) {
                if (toolbar.isConnected) setError(content, error, loadBody);
              });
          }

          pageSelect.addEventListener('change', draw);
          exportText.addEventListener('click', function () {
            var visible = currentPages();
            if (!visible.length) return;
            var text = visible.map(function (page) {
              return '# ' + (page.title || current.name || '文档') + '\n\n' + page.text;
            }).join('\n\n');
            var blobUrl = URL.createObjectURL(new Blob(['\ufeff' + text], {
              type: 'text/markdown;charset=utf-8'
            }));
            var link = h('a', { href: blobUrl,
              download: (current.name || '正文').replace(/[<>:"/\\|?*]/g, '_') + '.md' });
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(function () { URL.revokeObjectURL(blobUrl); }, 2000);
          });
          fetchBody.addEventListener('click', function () {
            if (busy) return;
            busy = true;
            fetchBody.disabled = true;
            exportText.disabled = true;
            fetchBody.textContent = '抓取中…';
            post('/api/tcdoc/body/fetch', { doc_key: docKey }).then(function (result) {
              watchJob(jobHost, result.job, function (job) {
                busy = false;
                applyBody(job.result || {});
              }, { cancelable: true, onSettled: function (job) {
                busy = false;
                fetchBody.disabled = false;
                exportText.disabled = !pages.length;
                fetchBody.textContent = pages.length ? '重新抓取' : '抓取正文';
                if (job.status === 'cancelled') {
                  note.textContent = '已取消，已下载的图片保留；可重新抓取继续。';
                } else if (job.status === 'error') {
                  note.textContent = '抓取失败：' + (job.error || '请稍后重试');
                }
              } });
            }).catch(function (error) {
              busy = false;
              fetchBody.disabled = false;
              exportText.disabled = !pages.length;
              fetchBody.textContent = pages.length ? '重新抓取' : '抓取正文';
              note.textContent = '抓取失败：' + error.message;
            });
          });
          loadBody();
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

  function renderTcdocPromo(host) {
    var daysInput = h('input', {
      class: 'input', type: 'number', value: '365', min: '1', max: '3650',
      style: 'width:90px'
    });
    var query = h('input', {
      class: 'input', placeholder: '按活动名 / 日期 / 城市筛选',
      style: 'min-width:200px'
    });
    var loadBtn = h('button', { class: 'btn', type: 'button' }, '读取可追加活动');
    var applyBtn = h('button', {
      class: 'btn primary', type: 'button', disabled: true
    }, '追加选中');
    var selInfo = h('span', { class: 'muted small' }, '未选择');
    var note = h('div', { class: 'muted small', style: 'margin:8px 0' },
      '正在读取…');
    var tableHost = h('div');
    var items = [];
    var selected = new Set();
    var gateOk = true;

    function updateSel() {
      var images = 0;
      items.forEach(function (item) {
        if (selected.has(item.key)) images += item.add_count || 0;
      });
      selInfo.textContent = selected.size
        ? ('已选 ' + selected.size + ' 场 · ' + images + ' 张图') : '未选择';
      applyBtn.disabled = !selected.size || !gateOk;
    }

    function render() {
      if (!items.length) {
        tableHost.replaceChildren(empty('没有可追加宣传图的活动。'));
        return;
      }
      var head = h('tr');
      ['选择', '活动', '日期', '城市', '表内图片', '待追加', '状态']
        .forEach(function (title, index) {
          head.appendChild(h('th', {
            class: index >= 4 && index <= 5 ? 'num' : ''
          }, title));
        });
      var body = h('tbody');
      items.forEach(function (item) {
        var box = h('input', {
          type: 'checkbox', checked: selected.has(item.key),
          disabled: !!item.blocked || !gateOk
        });
        box.addEventListener('change', function () {
          if (box.checked) selected.add(item.key);
          else selected.delete(item.key);
          updateSel();
        });
        body.appendChild(h('tr', null,
          h('td', null, box),
          h('td', { class: 'ellipsis' }, item.name || item.key),
          h('td', null, item.date),
          h('td', null, item.city),
          h('td', { class: 'num' }, item.existing_images),
          h('td', { class: 'num' }, item.add_count),
          h('td', {
            style: item.blocked ? 'color: var(--danger)' : ''
          }, item.blocked ? ('受阻：' + item.blocked)
            : ('可追加 ' + item.add_count + ' 张'))));
      });
      tableHost.replaceChildren(h('div', { class: 'table-wrap' },
        h('table', { class: 'db-table' },
          h('thead', null, head), body)));
    }

    function load() {
      selected.clear();
      setLoading(tableHost, '正在对比云端「宣传图」与本地图片…');
      post('/api/tcdoc/promo/preview', {
        days: Number(daysInput.value) || 365, query: query.value.trim(), limit: 200
      }).then(function (data) {
        items = data.items || [];
        var refresh = data.refresh || {};
        gateOk = !!refresh.allowed;
        note.textContent = '「' + data.tab_name + '」' + data.doc_name
          + ' · ' + data.tab_id + ' · 可追加 ' + fmtNumber(data.total)
          + ' 场 / ' + fmtNumber(data.images) + ' 张 · 已补齐 '
          + fmtNumber(data.synced) + ' 场 · ' + data.note
          + (gateOk ? '' : (' · 当前不可写：' + (refresh.message || '需要先同步前刷新')));
        updateSel();
        render();
        loadBtn.disabled = false;
      }).catch(function (error) {
        loadBtn.disabled = false;
        setError(tableHost, error, load);
      });
    }

    applyBtn.addEventListener('click', function () {
      if (!selected.size || !gateOk) return;
      var images = 0;
      items.forEach(function (item) {
        if (selected.has(item.key)) images += item.add_count || 0;
      });
      if (!window.confirm('将向 ' + selected.size + ' 场线下活动追加 '
        + images + ' 张宣传图。\n\n只更新这些行的「宣传图」列，'
        + '不改其它字段、不删或替换已有图片。\n\n确定追加？')) {
        return;
      }
      applyBtn.disabled = true;
      applyBtn.textContent = '正在追加…';
      post('/api/tcdoc/promo/apply', {
        days: Number(daysInput.value) || 365,
        keys: Array.from(selected), confirmed: true
      }).then(function (result) {
        applyBtn.textContent = '追加选中';
        toast(result.summary, result.failed_verify ? 'warning' : 'success');
        load();
      }).catch(function (error) {
        applyBtn.textContent = '追加选中';
        applyBtn.disabled = false;
        toast('追加失败：' + error.message, 'error');
      });
    });
    loadBtn.addEventListener('click', load);
    query.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') load();
    });

    host.appendChild(h('div', { class: 'card' },
      h('div', { class: 'toolbar' },
        h('span', { class: 'muted small' }, '时间范围（天）'), daysInput,
        h('span', { class: 'muted small' }, '筛选'), query,
        loadBtn, h('span', { style: 'flex:1' }), selInfo, applyBtn),
      h('div', { class: 'muted small', style: 'margin-top:6px' },
        '仅「线下活动」（ss_e1kkm2）的「宣传图」列：把本地 cover / images '
        + '里云端还没有的图片追加到已存在的活动行；不改其它字段、'
        + '不删或替换已有图片。需要先「同步前刷新」或在桌面端开启免校对模式。'),
      note, tableHost));
    loadBtn.disabled = true;
    load();
  }

  function renderTcdocEdit(host) {
    var rows = [];
    var selected = new Set();
    var tabSel = h('select', { class: 'select' },
      h('option', { value: 'official' }, '官方动态'),
      h('option', { value: 'live' }, '直播记录'));
    var query = h('input', {
      class: 'input', placeholder: '按标题 / 时间 / 账号筛选',
      style: 'min-width:200px'
    });
    var loadBtn = h('button', { class: 'btn', type: 'button' }, '读取云端记录');
    var delBtn = h('button', {
      class: 'btn', type: 'button', disabled: true
    }, '删除选中');
    var selInfo = h('span', { class: 'muted small' }, '未选择');
    var note = h('div', { class: 'muted small', style: 'margin:8px 0' },
      '正在读取腾讯文档…');
    var tableHost = h('div');
    var gateOk = true;
    var gateText = '';

    function updateSel() {
      selInfo.textContent = selected.size ? '已选 ' + selected.size + ' 条' : '未选择';
      delBtn.disabled = !selected.size || !gateOk;
    }

    function render() {
      if (!rows.length) {
        tableHost.replaceChildren(empty('没有符合条件的记录。'));
        return;
      }
      var keys = Object.keys(rows[0].summary || {});
      var head = h('tr');
      head.appendChild(h('th', null, '选择'));
      head.appendChild(h('th', null, '记录 ID'));
      keys.forEach(function (key) { head.appendChild(h('th', null, key)); });
      head.appendChild(h('th', null, '操作'));
      function mark() {
        tableHost.querySelectorAll('tbody tr').forEach(function (tr) {
          var rid = tr.getAttribute('data-rid');
          tr.classList.toggle('selected', selected.has(rid));
        });
      }
      var body = h('tbody');
      rows.forEach(function (row) {
        var box = h('input', {
          type: 'checkbox', checked: selected.has(row.record_id)
        });
        box.addEventListener('change', function () {
          if (box.checked) selected.add(row.record_id);
          else selected.delete(row.record_id);
          updateSel();
          mark();
        });
        var editBtn = h('button', {
          class: 'btn mini', type: 'button'
        }, '修改');
        editBtn.addEventListener('click', function () {
          if (!gateOk) {
            toast('需要先完成「同步前刷新」或在桌面端开启免校对模式', 'warning');
            return;
          }
          openEditor(row);
        });
        var cells = [h('td', null, box),
          h('td', { class: 'ellipsis' }, row.record_id.slice(0, 12))];
        keys.forEach(function (key) {
          cells.push(h('td', { class: 'ellipsis' },
            (row.summary || {})[key] || ''));
        });
        cells.push(h('td', null, editBtn));
        var tr = h('tr', { 'data-rid': row.record_id }, cells);
        if (selected.has(row.record_id)) tr.classList.add('selected');
        body.appendChild(tr);
      });
      tableHost.replaceChildren(h('div', { class: 'table-wrap' },
        h('table', { class: 'db-table' },
          h('thead', null, head), body)));
    }

    function openEditor(row) {
      var overlay = h('div', { class: 'preview-overlay' });
      overlay.addEventListener('click', function (event) {
        if (event.target === overlay) overlay.remove();
      });
      var inputs = {};
      var form = h('div');
      Object.keys(row.values || {}).forEach(function (title) {
        var input = h('textarea', { class: 'textarea', rows: 2 });
        input.value = row.values[title] || '';
        inputs[title] = input;
        form.appendChild(h('div', {
          class: 'muted small', style: 'margin-top:8px'
        }, title));
        form.appendChild(input);
      });
      var diffBox = h('div');
      var submit = h('button', {
        class: 'btn primary', type: 'button'
      }, '预览改动');
      var stage = 'preview';
      var diffRows = [];
      submit.addEventListener('click', function () {
        if (stage === 'preview') {
          diffRows = Object.keys(inputs).filter(function (title) {
            return inputs[title].value.trim() !== (row.values[title] || '').trim();
          }).map(function (title) {
            return { title: title, old: row.values[title] || '',
                     value: inputs[title].value.trim() };
          });
          if (!diffRows.length) {
            toast('没有改动', 'warning');
            return;
          }
          var tbody = h('tbody');
          diffRows.forEach(function (item) {
            tbody.appendChild(h('tr', null,
              h('td', null, item.title),
              h('td', { class: 'ellipsis' }, item.old),
              h('td', {
                class: 'ellipsis', style: 'color: var(--link)'
              }, item.value)));
          });
          diffBox.replaceChildren(h('div', {
            class: 'table-wrap', style: 'margin-top:10px'
          }, h('table', { class: 'db-table' }, h('thead', null, h('tr', null,
            h('th', null, '字段'), h('th', null, '当前'),
            h('th', null, '改为'))), tbody)));
          stage = 'confirm';
          submit.textContent = '确认提交（' + diffRows.length + ' 项）';
          return;
        }
        if (!window.confirm('将修改记录 ' + row.record_id + ' 的 '
          + diffRows.length + ' 个字段。\n\n只改这一行、只改这些列，'
          + '不改表结构、不改其它行。\n\n确定提交？')) {
          return;
        }
        var values = {};
        diffRows.forEach(function (item) { values[item.title] = item.value; });
        submit.disabled = true;
        post('/api/tcdoc/edit/update', {
          tab: tabSel.value, record_id: row.record_id,
          values: values, confirmed: true
        }).then(function (result) {
          overlay.remove();
          toast(result.summary, result.verified ? 'success' : 'warning');
          load();
        }).catch(function (error) {
          submit.disabled = false;
          toast('修改失败：' + error.message, 'error');
        });
      });
      overlay.appendChild(h('div', { class: 'preview-box' },
        h('div', { class: 'toolbar' },
          h('span', { class: 'row-title' }, '修改记录'),
          h('span', { style: 'flex:1' }),
          submit,
          h('button', {
            class: 'btn', type: 'button',
            onclick: function () { overlay.remove(); }
          }, '关闭')),
        h('div', { class: 'muted small', style: 'margin-top:4px' },
          '记录 ' + row.record_id + ' · ' + (tabSel.value === 'live'
            ? '直播记录' : '官方动态') + ' · 只列出文本列'),
        form, diffBox));
      document.body.appendChild(overlay);
    }

    function load() {
      selected.clear();
      updateSel();
      setLoading(tableHost, '正在读取腾讯文档…');
      post('/api/tcdoc/edit/preview', {
        tab: tabSel.value, query: query.value.trim(), limit: 200
      }).then(function (data) {
        rows = data.rows || [];
        var refresh = data.refresh || {};
        gateOk = !!refresh.allowed;
        gateText = gateOk ? '' : ('当前不可写：' + (refresh.message || '需要先同步前刷新'));
        note.textContent = '「' + data.name + '」共 ' + fmtNumber(data.total)
          + ' 条，显示 ' + fmtNumber(data.shown) + ' 条（' + data.doc_name
          + ' · ' + data.tab_id + '）· ' + data.note
          + (gateText ? ' · ' + gateText : '');
        updateSel();
        render();
      }).catch(function (error) {
        setError(tableHost, error, load);
      });
    }

    loadBtn.addEventListener('click', load);
    query.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') load();
    });
    delBtn.addEventListener('click', function () {
      var ids = Array.from(selected);
      if (!ids.length) return;
      if (!gateOk) {
        toast('需要先完成「同步前刷新」或在桌面端开启免校对模式', 'warning');
        return;
      }
      var titles = ids.map(function (rid) {
        var row = rows.filter(function (item) {
          return item.record_id === rid;
        })[0];
        var names = row ? Object.keys(row.summary || {}).map(function (key) {
          return row.summary[key];
        }) : [];
        return (names[0] || rid) + '（' + rid.slice(0, 12) + '）';
      });
      if (!window.confirm('将删除「' + (tabSel.value === 'live'
        ? '直播记录' : '官方动态') + '」的 ' + ids.length + ' 条记录：\n\n'
        + titles.slice(0, 10).join('\n')
        + (ids.length > 10 ? '\n…' : '')
        + '\n\n删除后本页无法撤销，请确认。')) {
        return;
      }
      delBtn.disabled = true;
      post('/api/tcdoc/edit/delete', {
        tab: tabSel.value, record_ids: ids, confirmed: true
      }).then(function (result) {
        toast(result.summary, result.verified ? 'success' : 'warning');
        load();
      }).catch(function (error) {
        delBtn.disabled = false;
        toast('删除失败：' + error.message, 'error');
      });
    });

    host.appendChild(h('div', { class: 'card' },
      h('div', { class: 'toolbar' },
        h('span', { class: 'muted small' }, '表'), tabSel,
        h('span', { class: 'muted small' }, '筛选'), query,
        loadBtn, h('span', { style: 'flex:1' }), selInfo, delBtn),
      h('div', { class: 'muted small', style: 'margin-top:6px' },
        '只允许「官方动态」「直播记录」的行级修改 / 删除，按记录 ID 操作；'
        + '修改仅限文本列，删除与修改都要先预览再确认，写后读回核对。'),
      note, tableHost));
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
