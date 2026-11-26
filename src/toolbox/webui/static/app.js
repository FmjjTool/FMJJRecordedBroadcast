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
    image_sorter: { kind: 'all', q: '' },
    rename_batch: {
      path: '', recursive: false, filter: 'all', tab: 'rename',
      dirRename: false
    },
    shopping: { tab: 'taobao' },
    info_accounts: { platform: '', scope: 'all' },
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
      renderDesktopOnly(host, '封面制作',
        '封面制作使用桌面端自绘排版与字体资源，Web 端不提供编辑，请在桌面端使用。');
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
        ['localwiki', '资料站', '部署状态与站点访问统计'],
        ['livestream_summary', '直播总结', '转录清单与已保存总结'],
        ['image_sorter', '媒体筛选', '源文件夹扫描与图片预览'],
        ['rename_batch', '批量改名', '规则预览与冲突检查'],
        ['tickets', '票务解析', '演出缓存与导出目录'],
        ['shopping', '购物解析', '淘宝 / 抖音 / B站小店产物'],
        ['music_playlist', '歌单', '本地预览与执行报告'],
        ['music_download', '下载', '本地音乐文件列表'],
        ['music_tag', '标签', '标签快照与历史'],
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
        '视频评论区与公众号解析可直接抓取并导出到本地产物目录；'
        + 'B站资源（表情包 / 装扮 / UP 信息）与抖音表情包抓取仍在桌面端。'
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
        h('option', { value: 'up' }, 'UP 信息'));
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
          '拉取直播间的「会员表情栏」（需要抖音登录 Cookie），下载到「抖音表情包」导出目录。'),
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
    setLoading(root, '正在读取网盘备份状态…');
    api('/api/netdisk').then(function (data) {
      root.replaceChildren();
      var tabs = h('div', { class: 'tabs' });
      var host = h('div');
      var providers = data.providers || [];
      var providerLabel = {};
      providers.forEach(function (item) { providerLabel[item.value] = item.label; });
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
            renderNetdisk(root);
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
              setTimeout(function () { renderNetdisk(root); }, 1200);
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
              renderNetdisk(root);
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
              renderNetdisk(root);
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

      renderTab();
    }).catch(function (error) {
      setError(root, error, function () { renderNetdisk(root); });
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
          h('span', { class: 'row-title' }, '文本模式'),
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
        h('option', { value: 'ai' }, '大模型总结'),
        h('option', { value: 'rule' }, '本地规则保底'));
      modeSel.value = settings.mode === 'rule' ? 'rule' : 'ai';
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
        post('/api/tools/livestream-summary/run', {
          rel: fileSelect.value, mode: modeSel.value
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
              if (job.status === 'error') {
                statusText.textContent = '失败';
                toast('总结失败：' + (job.error || '未知错误'), 'error');
              }
            }
          });
        }).catch(function (error) {
          running = false;
          startBtn.disabled = false;
          startBtn.textContent = '开始总结';
          statusText.textContent = '失败';
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

      saveBtn.addEventListener('click', function () {
        saveBtn.disabled = true;
        post('/api/tools/livestream-summary/settings', {
          base_url: baseInput.value, model: modelInput.value,
          chunk_chars: Number(chunkInput.value) || 6000,
          mode: modeSel.value, api_key: keyInput.value
        }).then(function (result) {
          keyInput.value = '';
          settingsHost.textContent = result.summary;
          toast(result.summary, 'success');
        }).catch(function (error) {
          toast('保存失败：' + error.message, 'error');
        }).then(function () { saveBtn.disabled = false; });
      });

      testBtn.addEventListener('click', function () {
        testBtn.disabled = true;
        testBtn.textContent = '测试中…';
        post('/api/tools/livestream-summary/test', {}).then(function (result) {
          toast(result.summary || result.message, 'success');
          settingsHost.textContent = result.message;
        }).catch(function (error) {
          settingsHost.textContent = '连接失败：' + error.message;
          toast('连接失败：' + error.message, 'error');
        }).then(function () {
          testBtn.disabled = false;
          testBtn.textContent = '测试连接';
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
        h('div', { class: 'form-grid' },
          h('label', null, h('span', { class: 'muted small' }, '接口地址'),
            baseInput),
          h('label', null, h('span', { class: 'muted small' }, '模型名称'),
            modelInput),
          h('label', null, h('span', { class: 'muted small' }, '每块字数'),
            chunkInput),
          h('label', null, h('span', { class: 'muted small' }, 'API Key'),
            keyInput)),
        h('div', { class: 'toolbar', style: 'margin-top:8px' },
          saveBtn, testBtn),
        kv('当前模式', settings.mode === 'rule' ? '本地规则（不调用模型）' : 'AI 模型'),
        settingsHost));
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
              h('td', { class: 'ellipsis' },
                h('a', {
                  href: '#', title: '查看详情', onclick: function (event) {
                    event.preventDefault();
                    openEntryDetail(item);
                  }
                }, item.file || '-')),
              h('td', null, h('div', { class: 'toolbar' }, detailBtn, noDlBtn))));
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
    Promise.all([
      api('/api/tools/rename/overview'),
      api('/api/tools/rename/preview?limit=300'),
      api('/api/publish/overview').catch(function () { return null; }),
      api('/api/publish/templates').catch(function () { return null; }),
      api('/api/publish/history').catch(function () { return null; }),
      api('/api/publish/categories').catch(function () { return null; })
    ]).then(function (results) {
      var overview = results[0];
      var preview = results[1];
      var publishInfo = results[2] || {};
      var templateInfo = results[3] || {};
      var historyInfo = results[4] || {};
      var categoryInfo = results[5] || null;
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
          + (publishInfo.logged_in ? '' : ' 当前未检测到 B站登录 Cookie。')),
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
      host.appendChild(h('div', { class: 'table-wrap' },
        h('table', { class: 'db-table' }, h('thead', null, head), body)));
      host.appendChild(h('div', { class: 'muted small', style: 'margin-top:8px' },
        preview.note || '点击「命名」生成标准文件名。'));
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
        controls: true, preload: 'metadata',
        style: 'width:100%;max-height:320px;background:#000;border-radius:8px'
      });
      var previewNote = h('div', { class: 'muted small', style: 'margin-top:6px' },
        '勾选 1 个视频即可预览（拖动进度按需加载）。MKV / AVI 等浏览器不支持的格式可能无法播放，请用桌面端。');
      var previewHost = h('div', {
        class: 'card', style: 'margin-top:12px;display:none'
      }, previewVideo, previewNote);
      var opHost = h('div', { class: 'card', style: 'margin-top:12px' });
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
        syncPreview();
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
          return;
        }
        previewHost.style.display = '';
        if (paths[0] === previewPath) return;
        previewPath = paths[0];
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
        tabHost.appendChild(segments);
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
          expCsvBtn.disabled = running;
          expEdlBtn.disabled = running;
          expChapBtn.disabled = running;
          impCsvBtn.disabled = running;
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
            segments.value = data.text || '';
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
      var queryBtn = h('button', { class: 'btn primary', type: 'button' },
        '查询粉丝群');
      var queryHost = h('div', { style: 'margin-top:8px' });
      var queryJobHost = h('div');
      var querying = false;
      platformSel.addEventListener('change', function () {
        queryInput.placeholder = QUERY_PLATFORMS[platformSel.value][1];
        queryHost.replaceChildren();
      });
      queryBtn.addEventListener('click', function () {
        if (querying) return;
        if (!queryInput.value.trim()) {
          toast('请粘贴主页链接或输入 UID', 'warning');
          return;
        }
        querying = true;
        queryBtn.disabled = true;
        queryBtn.textContent = '查询中…';
        post('/api/fangroups/query', {
          platform: platformSel.value, text: queryInput.value.trim()
        }).then(function (result) {
          watchJob(queryJobHost, result.job, function (job) {
            querying = false;
            queryBtn.disabled = false;
            queryBtn.textContent = '查询粉丝群';
            var outcome = job.result || {};
            toast(outcome.summary || '查询完成',
              (outcome.groups || []).length ? 'success' : 'info');
            var groups = outcome.groups || [];
            if (!groups.length) {
              queryHost.replaceChildren(empty('没有查到公开粉丝群。'));
              return;
            }
            var head = h('tr', null, h('th', null, '群名'),
              h('th', null, '群号'), h('th', { class: 'num' }, '人数'),
              h('th', { class: 'num' }, '变化'), h('th', null, '标签'),
              h('th', null, '链接'));
            var body = h('tbody');
            groups.forEach(function (row) {
              var changeText = '-';
              if (row.change === null) changeText = '新群';
              else if (typeof row.change === 'number' && row.change !== 0) {
                changeText = (row.change > 0 ? '+' : '') + row.change;
              } else if (typeof row.change === 'number') changeText = '0';
              body.appendChild(h('tr', null,
                h('td', { class: 'ellipsis' }, row.name || '-'),
                h('td', { class: 'muted small' }, row.group_no || row.group_id || '-'),
                h('td', { class: 'num' }, row.member_text || '-'),
                h('td', { class: 'num' }, changeText),
                h('td', { class: 'muted small' }, row.tags || '-'),
                h('td', { class: 'ellipsis' }, row.url
                  ? h('a', { href: row.url, target: '_blank',
                    rel: 'noopener noreferrer' }, row.url) : '-')));
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
              h('div', { class: 'table-wrap' },
                h('table', { class: 'db-table' },
                  h('thead', null, head), body)));
          }, {
            cancelable: true,
            onSettled: function (job) {
              querying = false;
              queryBtn.disabled = false;
              queryBtn.textContent = '查询粉丝群';
              if (job.status === 'error') {
                toast('查询失败：' + (job.error || '未知错误'), 'error');
              }
            }
          });
        }).catch(function (error) {
          querying = false;
          queryBtn.disabled = false;
          queryBtn.textContent = '查询粉丝群';
          toast('查询失败：' + error.message, 'error');
        });
      });
      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'row-title' }, '查询粉丝群'),
        h('div', { class: 'toolbar', style: 'margin-top:8px' },
          h('span', { class: 'muted small' }, '平台'), platformSel,
          queryInput, queryBtn),
        h('div', { class: 'muted small', style: 'margin-top:6px' },
          '抖音 / 微博 / B站 需要对应平台登录态；B站网页端只能列出当前账号'
          + '已加入的应援团。查询会保存快照并加入定时快照名单。'),
        queryHost, queryJobHost));
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
          h('span', { style: 'flex:1' }), refreshBtn),
        h('div', { class: 'muted small', style: 'margin-top:6px' },
          '分享链接免登录；按群号查询需桌面端已登录 QQ 群。导出为桌面同格式 Excel，'
          + '头像按群目录保存、已有文件复用。'),
        qqJobHost));
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
      var searching = false;
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
              }, '抓取');
              btn.addEventListener('click', function () {
                btn.disabled = true;
                btn.textContent = '抓取中…';
                post('/api/douyin-index/fetch', {
                  uid: item.uid, days: Number(daysSel.value) || 30
                }).then(function (job) {
                  watchJob(fetchJobHost, job.job, function (done) {
                    var outcome = done.result || {};
                    toast(outcome.summary || '抓取完成', 'success');
                    setTimeout(function () { renderDouyinIndex(root); }, 800);
                  }, {
                    cancelable: true,
                    onSettled: function (done) {
                      if (done.status === 'error') {
                        btn.disabled = false;
                        btn.textContent = '抓取';
                        toast('抓取失败：' + (done.error || '未知错误'), 'error');
                      }
                    }
                  });
                }).catch(function (error) {
                  btn.disabled = false;
                  btn.textContent = '抓取';
                  toast('抓取失败：' + error.message, 'error');
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
        h('div', { class: 'row-title' }, '搜索达人并抓取'),
        h('div', { class: 'toolbar', style: 'margin-top:8px' },
          keyword, daysSel, searchBtn,
          h('span', { class: 'muted small' },
            '搜索需要抖音指数 Cookie；抓取会写入「抖音指数」导出目录。')),
        searchHost, fetchJobHost));
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

  function renderTickets(root) {
    setLoading(root, '正在读取票务缓存…');
    api('/api/tickets').then(function (data) {
      root.replaceChildren();
      var parseInput = h('input', {
        class: 'input grow',
        placeholder: '票务链接或关键词（留空可按关键词搜索）'
      });
      var scopeSel = h('select', { class: 'select' },
        h('option', { value: 'recent' }, '近期（在售）'),
        h('option', { value: 'all', selected: true }, '所有（含历史）'));
      var parseBtn = h('button', { class: 'btn primary', type: 'button' }, '解析演出');
      var exportBtn = h('button', { class: 'btn', type: 'button' }, '导出 JSON');
      var resultHost = h('div', { style: 'margin-top:8px' });
      var jobHost = h('div');
      var running = false;

      function sync() {
        parseBtn.disabled = running;
        exportBtn.disabled = running || !parseInput.value.trim();
        parseBtn.textContent = running ? '处理中…' : '解析演出';
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
        running = true;
        sync();
        post('/api/tickets/parse', {
          text: parseInput.value.trim(), scope: scopeSel.value
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
        if (!parseInput.value.trim()) {
          toast('请先填写链接或关键词', 'warning');
          return;
        }
        running = true;
        sync();
        post('/api/tickets/export', {
          text: parseInput.value.trim(), scope: scopeSel.value
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
        h('div', { class: 'muted small', style: 'margin-top:6px' },
          '支持大麦艺人/演出、秀动艺人/事件、B站会员购搜索或项目；'
          + '也可直接输入关键词走三平台搜索。导出会把全量 JSON 与图片写入「票务演出」目录。'),
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
      ['bili_shop', 'B站小店']];
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
    root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
      h('div', { class: 'muted small' },
        '可直接解析淘宝店铺 / 商品、抖音商品分享文案与 B站小店；'
        + '下方列表浏览已保存的商品目录（主图 / SKU / 详情图）。')));
    root.appendChild(buildShoppingParse(load));
    root.appendChild(tabs);
    root.appendChild(host);

    function buildShoppingParse(reload) {
      var platformSel = h('select', { class: 'select' },
        h('option', { value: 'taobao' }, '淘宝（店铺 / 商品）'),
        h('option', { value: 'douyin' }, '抖音商品'),
        h('option', { value: 'bili' }, 'B站小店'));
      var input = h('textarea', {
        class: 'textarea', rows: '3',
        placeholder: '店铺 / 商品链接或分享文案（抖音可一次多行/多条分享）'
      });
      var btn = h('button', { class: 'btn primary', type: 'button' }, '开始解析');
      var jobHost = h('div');
      var resultHost = h('div', { style: 'margin-top:8px' });
      var running = false;
      btn.addEventListener('click', function () {
        if (running) return;
        if (!input.value.trim()) {
          toast('请粘贴链接或分享文案', 'warning');
          return;
        }
        running = true;
        btn.disabled = true;
        btn.textContent = '解析中…';
        post('/api/tools/shopping/parse', {
          platform: platformSel.value, text: input.value
        }).then(function (result) {
          watchJob(jobHost, result.job, function (job) {
            running = false;
            btn.disabled = false;
            btn.textContent = '开始解析';
            var outcome = job.result || {};
            toast(outcome.summary || '解析完成',
              outcome.failures && outcome.failures.length ? 'warning' : 'success');
            var items = outcome.items || [];
            if (!items.length) {
              resultHost.replaceChildren(empty('没有解析到商品。'));
              reload();
              return;
            }
            var head = h('tr', null, h('th', null, '商品'),
              h('th', null, '价格'), h('th', { class: 'num' }, '销量'),
              h('th', null, '链接'));
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
              h('div', { class: 'muted small', style: 'margin-bottom:6px' },
                (outcome.shop && outcome.shop.name
                  ? outcome.shop.name + ' · ' : '') + items.length + ' 件商品'),
              h('div', { class: 'table-wrap' },
                h('table', { class: 'db-table' },
                  h('thead', null, head), body)));
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
        h('div', { class: 'row-title' }, '解析商品'),
        h('div', { class: 'toolbar', style: 'margin-top:8px' },
          h('span', { class: 'muted small' }, '平台'), platformSel, btn,
          h('span', { class: 'muted small' },
            '淘宝需要登录态；抖音 / B站按公开接口解析。')),
        input, resultHost, jobHost);
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
        if (ok && (row.platform === 'netease' || row.platform === 'qq')) {
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
      applyBtn.disabled = !previewId || !(data.matched > 0);
      var planRows = (data.plans || []).map(function (plan) {
        var counts = plan.counts || {};
        return h('div', { class: 'muted small' },
          plan.platform_name + '：可写入 ' + (counts.matched || 0) + ' 首'
          + '（待确认 ' + (counts.uncertain || 0) + '、未找到 '
          + (counts.missing || 0) + '、重复 ' + (counts.duplicate || 0) + '）');
      });
      var body = h('tbody');
      (data.rows || []).forEach(function (row) {
        body.appendChild(h('tr', null,
          h('td', null, h('span', { class: 'badge' }, row.platform_name)),
          h('td', { class: 'num' }, row.index),
          h('td', { class: 'ellipsis' }, row.source_title || '-'),
          h('td', { class: 'ellipsis' }, row.source_artist || '-'),
          h('td', null, row.state),
          h('td', { class: 'ellipsis' }, row.target_name || '-'),
          h('td', { class: 'ellipsis' }, row.target_artist || '-'),
          h('td', { class: 'num' }, row.score === null ? '-' : row.score)));
      });
      resultHost.replaceChildren(
        h('div', { style: 'margin-top:10px' },
          h('div', { class: 'row-title', style: 'margin-bottom:6px' },
            data.source + ' · ' + data.matched + '/' + data.total + ' 首可写入'),
          planRows,
          h('div', { class: 'table-wrap', style: 'margin-top:8px' },
            h('table', { class: 'db-table' }, h('thead', null, h('tr', null,
              h('th', null, '平台'), h('th', { class: 'num' }, '序号'),
              h('th', null, '来源歌曲'), h('th', null, '来源歌手'),
              h('th', null, '状态'), h('th', null, '匹配歌曲'),
              h('th', null, '匹配歌手'), h('th', { class: 'num' }, '相似度'))),
            body)),
          data.truncated ? h('div', { class: 'muted small' },
            '结果过多，仅显示前 ' + (data.rows || []).length + ' 行。') : null));
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
      var lines = (previewData.plans || []).map(function (plan) {
        return plan.platform_name + '：写入 ' + ((plan.counts || {}).matched || 0) + ' 首';
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
        include_uncertain: includeUncertain.checked
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
    var qualitySel = h('select', { class: 'select' },
      h('option', { value: '' }, '各平台默认音质'),
      h('option', { value: 'lossless' }, '无损（网易云）'),
      h('option', { value: 'exhigh' }, '320k'),
      h('option', { value: 'higher' }, '192k'));
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
              ['track', '音轨'], ['disc', '碟号'], ['genre', '流派'],
              ['composer', '作曲'], ['comment', '备注']].forEach(function (pair) {
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
            var saveBtn = h('button', { class: 'btn primary', type: 'button' },
              '写入标签');
            var lyricBtn = h('button', { class: 'btn', type: 'button' },
              '抓取歌词');
            var busy = false;

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
                  saveBtn, lyricBtn,
                  h('span', { class: 'muted small' },
                    '写入前会自动记录标签历史。')),
                lyricHost));
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
      root.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
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
          ['docs', '文档管理'], ['writeback', '映射回写'],
          ['edit', '修改 / 删除']
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
          else if (state.tcdoc.tab === 'edit') renderTcdocEdit(tabHost);
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
          var selLabel = h('span', { class: 'muted small' }, '已选 0 条');
          var playlistBtn = h('button', {
            class: 'btn', type: 'button', style: 'display:none'
          }, '歌单录入');
          var targets = [];
          var maxUpload = 50;

          function targetNow() {
            var key = tableSel.value;
            return targets.filter(function (item) {
              return item.doc_key + '|' + item.tab_id === key;
            })[0] || null;
          }

          function syncButtons() {
            var target = targetNow();
            var gate = lastPreview ? lastPreview.refresh.allowed : false;
            var canUpload = !!(target && target.write_mode === 'append');
            selLabel.textContent = '已选 ' + selected.size + ' 条';
            tsvBtn.disabled = !lastPreview || !lastPreview.tsv;
            uploadBtn.disabled = busy || !canUpload || !gate
              || selected.size === 0 || selected.size > maxUpload;
            uploadBtn.textContent = busy ? '处理中…'
              : (canUpload ? '上传选中' : '该表只读');
            compareBtn.disabled = busy;
            refreshBtn.disabled = busy;
          }

          function applyTargetOptions() {
            tableSel.replaceChildren();
            if (!targets.length) {
              tableSel.appendChild(h('option', { value: '' },
                '本地没有已同步的表格（请先「表格同步」）'));
              tableSel.disabled = true;
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
            accSel.style.display = isLives ? 'none' : '';
            dupSel.style.display = isLives ? '' : 'none';
            playlistBtn.style.display = (target && target.profile === 'events')
              ? '' : 'none';
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
            var target = targetNow();
            if (!target) {
              toast('请先选择表格', 'warning');
              return;
            }
            busy = true;
            syncButtons();
            setLoading(tableHost, '正在比对本地数据与表格快照…');
            summary.textContent = '';
            post('/api/tcdoc/bridge/preview', {
              doc_key: target.doc_key, tab_id: target.tab_id,
              days: Number(daysSel.value) || 30,
              accounts: accSel.value, dup_mode: dupSel.value
            }).then(function (result) {
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
              busy = false;
              syncButtons();
              setError(tableHost, error, runCompare);
            });
          }

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
            var ok = window.confirm('确定向「' + target.doc_name + ' / '
              + target.tab_name + '」新增 ' + selected.size + ' 条记录？\n'
              + (withImages ? '其中 ' + withImages + ' 条带本地图片（含评论区图片）。\n' : '')
              + '只新增 / 追加，不修改或删除已有行；写入后读回校验并记录推送历史。');
            if (!ok) return;
            busy = true;
            syncButtons();
            post('/api/tcdoc/bridge/upload', {
              doc_key: target.doc_key, tab_id: target.tab_id,
              days: Number(daysSel.value) || 30,
              accounts: accSel.value, dup_mode: dupSel.value,
              keys: Array.from(selected), confirmed: true
            }).then(function (data) {
              watchJob(jobHost2, data.job, function (job) {
                busy = false;
                var result = job.result || {};
                toast(result.summary || '上传完成',
                  result.failed_verify ? 'warning' : 'success');
                if (jobHost2) {
                  jobHost2.replaceChildren(h('div', {
                    class: 'card', style: 'margin-top:8px'
                  }, h('div', { class: 'muted small' }, result.summary || ''),
                    failList(result.blocked || []),
                    h('div', { class: 'muted small' },
                      '新增 ' + fmtNumber(result.added) + ' 条'
                      + (result.updated ? ' · 追加 ' + result.updated + ' 条' : '')
                      + (result.skipped ? ' · 跳过 ' + result.skipped + ' 条' : '')
                      + (result.missing_images
                        ? ' · 缺图 ' + result.missing_images + ' 张' : ''))));
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

          compareBtn.addEventListener('click', runCompare);
          tableSel.addEventListener('change', function () {
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
                runCompare();
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

          playlistBtn.addEventListener('click', function () {
            api('/api/tcdoc/bridge/events').then(function (data) {
              var events = data.events || [];
              if (!events.length) {
                toast('本地没有线下活动快照，请先同步表格', 'warning');
                return;
              }
              var evSel = h('select', { class: 'select', style: 'width:100%' });
              events.forEach(function (ev) {
                evSel.appendChild(h('option', { value: String(ev.row_no) },
                  [ev.name, ev.date, ev.city, (ev.song_count || 0) + ' 首']
                    .filter(Boolean).join(' · ')));
              });
              var inputs = {};
              var rows = [['netease', '活动歌单_网易云'],
                ['kugou', '活动歌单_酷狗'], ['qq', '活动歌单_QQ']]
                .map(function (pair) {
                  var input = h('input', {
                    class: 'input', type: 'text',
                    placeholder: '歌单链接，或歌单 ID（可留空）',
                    style: 'flex:1;min-width:200px'
                  });
                  inputs[pair[0]] = input;
                  return h('div', { class: 'toolbar', style: 'margin-top:6px' },
                    h('span', { class: 'muted small' }, pair[1]), input);
                });
              var pushBtn = h('button', {
                class: 'btn primary', type: 'button'
              }, '推送到腾讯文档');
              var overwriteBox = h('input', { type: 'checkbox' });
              var overlay = h('div', { class: 'preview-overlay' });
              overlay.addEventListener('click', function (event) {
                if (event.target === overlay) overlay.remove();
              });
              pushBtn.addEventListener('click', function () {
                var payload = { row_no: Number(evSel.value),
                  overwrite: overwriteBox.checked };
                var any = false;
                Object.keys(inputs).forEach(function (key) {
                  payload[key] = inputs[key].value.trim();
                  if (payload[key]) any = true;
                });
                if (!any) {
                  toast('至少填写一个平台的歌单链接', 'warning');
                  return;
                }
                pushBtn.disabled = true;
                pushBtn.textContent = '写入中…';
                post('/api/tcdoc/bridge/playlist', payload).then(function (res) {
                  watchJob(jobHost2, res.job, function (job) {
                    var receipt = job.result || {};
                    toast(receipt.summary || '已处理',
                      receipt.ok ? 'success' : 'warning');
                    overlay.remove();
                  }, {
                    onSettled: function (job) {
                      pushBtn.disabled = false;
                      pushBtn.textContent = '推送到腾讯文档';
                      if (job.status !== 'done') {
                        toast('写入失败：' + (job.error || '未知错误'), 'error');
                      }
                    }
                  });
                }).catch(function (error) {
                  pushBtn.disabled = false;
                  pushBtn.textContent = '推送到腾讯文档';
                  toast('写入失败：' + error.message, 'error');
                });
              });
              overlay.appendChild(h('div', { class: 'preview-box' },
                h('div', { class: 'preview-caption' },
                  h('span', null, '歌单录入 · 线下活动'),
                  h('button', {
                    class: 'btn mini', type: 'button',
                    onclick: function () { overlay.remove(); }
                  }, '关闭')),
                h('div', { class: 'preview-body' },
                  h('div', { class: 'toolbar' },
                    h('span', { class: 'muted small' }, '活动'), evSel),
                  rows,
                  h('label', { class: 'check muted small', style: 'margin-top:6px' },
                    overwriteBox, ' 覆盖该列已有的其它链接'),
                  h('div', { class: 'muted small', style: 'margin-top:8px' },
                    '只写「活动歌单_*」三列，已有其它链接不会覆盖；'
                    + '可填完整链接，也可只填歌单 ID。'),
                  h('div', { class: 'toolbar', style: 'margin-top:10px' },
                    h('span', { style: 'flex:1' }), pushBtn))));
              document.body.appendChild(overlay);
            }).catch(function (error) {
              toast('读取线下活动失败：' + error.message, 'error');
            });
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
                h('span', null, '待同步 TSV（全选后 Ctrl+C 复制，不含表头）'),
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
              refreshBtn, gateBadge, tsvBtn, uploadBtn, playlistBtn,
              h('span', { style: 'flex:1' }), selLabel),
            refreshInfo,
            h('div', { class: 'muted small', style: 'margin-top:4px' },
              '比对只读本地快照与本地采集数据；批量上传只限「官方动态」「直播记录」且仅新增，'
              + '需先「同步前刷新」（或桌面端免校对模式），单次最多 50 条。')));
          box.appendChild(summary);
          box.appendChild(tableHost);
          box.appendChild(jobHost2);

          api('/api/tcdoc/bridge').then(function (data) {
            targets = data.targets || [];
            maxUpload = data.max_upload || 50;
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
                '本地还没有已同步的表格。请先在上方「表格同步」拉取，'
                + '或到桌面端「腾讯文档」完成同步。'));
            }
          }).catch(function (error) {
            setError(tableHost, error, renderBridgeTab);
          });
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
