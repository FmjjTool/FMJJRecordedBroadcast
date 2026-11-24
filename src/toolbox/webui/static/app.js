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
    route: 'collected',
    theme: localStorage.getItem('webui-theme') || 'auto',
    collected: { platform: 'douyin', page: 1, pageSize: 30, q: '', data: null, selected: new Set(), formats: new Set(), overview: null },
    assets: { tab: 'entries', page: 1, q: '', platform: '', dbPlatform: '', dbPage: 1, dbQ: '', dbOrder: '', dbAsc: false, cat: '', localPage: 1,
      offline: { year: '', selected: null, order: '', asc: true, platforms: new Set(['bilibili', 'douyin_archive']) } },
    record: { platform: '', order: '', asc: true },
    audio: { order: '', asc: true, selected: new Set() },
    calendar: { day: '', order: '', asc: true },
    migrate: { order: '', asc: true, selected: new Set(), mode: 'copy' },
    subtitle: { order: '', asc: true, selected: new Set(), formats: new Set() },
    tasks: { order: '', asc: true },
    tcdoc: { docKey: '', rowsTab: '', rowsPage: 1 }
  };
  var routes = {
    collected: { title: '采集结果', load: renderCollected },
    assets: { title: '资产库', load: renderAssets },
    organize: { title: '自动整理', load: function (host) { renderModule(host, 'organize'); } },
    editor: { title: '视频操作', load: function (host) { renderModule(host, 'editor'); } },
    rename: { title: '命名投稿', load: function (host) { renderModule(host, 'rename'); } },
    audio: { title: '音频提取', load: renderAudio },
    subtitle: { title: '字幕转换', load: renderSubtitle },
    launcher: { title: '继续操作', load: renderLauncher },
    calendar: { title: '日历浏览', load: renderCalendar },
    migrate: { title: '文件迁移', load: renderMigrate },
    record: { title: '录制', load: renderRecord },
    tcdoc: { title: '腾讯文档', load: renderTcdoc },
    tasks: { title: '任务', load: renderTasks },
    system: { title: '系统', load: renderSystem }
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
    if (!routes[route]) route = 'collected';
    state.route = route;
    location.hash = '#/' + route;
    document.querySelectorAll('.nav-item').forEach(function (button) {
      button.classList.toggle('active', button.dataset.route === route);
    });
    pageTitle.textContent = routes[route].title;
    sidebar.classList.remove('open');
    scrim.classList.remove('show');
    content.replaceChildren();
    routes[route].load(content);
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

  function renderCollected(root) {
    setLoading(root, '正在读取采集结果概览…');
    api('/api/collected/overview').then(function (overview) {
      state.collected.overview = overview;
      if (!state.collected.formats.size) {
        overview.formats.forEach(function (fmt) { state.collected.formats.add(fmt); });
      }
      var itemHost = h('div');
      var pagerHost = h('div', { class: 'pager' });
      var jobHost = h('div');
      var info = h('span', { class: 'muted small' });
      var tabs = h('div', { class: 'tabs' });
      var search = h('input', {
        class: 'input grow', placeholder: '搜索标题、作者或作品 ID', value: state.collected.q
      });
      var formatBoxes = h('div', { class: 'toolbar', style: 'margin:0' });
      var comment = h('input', { class: 'input', type: 'number', min: '0', max: '50', value: overview.comment_pages, style: 'width:76px' });
      var exportSelected = h('button', { class: 'btn primary', type: 'button', disabled: true }, '导出勾选');
      var exportFiltered = h('button', { class: 'btn', type: 'button' }, '导出当前筛选');
      var downloadSelected = h('button', { class: 'btn', type: 'button', disabled: true }, '下载勾选');

      overview.formats.forEach(function (fmt) {
        formatBoxes.appendChild(h('label', { class: 'check' },
          h('input', {
            type: 'checkbox', checked: state.collected.formats.has(fmt),
            onchange: function (event) {
              if (event.target.checked) state.collected.formats.add(fmt);
              else state.collected.formats.delete(fmt);
            }
          }), fmt.toUpperCase()));
      });
      formatBoxes.appendChild(h('label', { class: 'check', style: 'margin-left:8px' }, '微博评论页',
        comment));

      overview.platforms.forEach(function (platform) {
        tabs.appendChild(h('button', {
          class: 'tab' + (platform.key === state.collected.platform ? ' active' : ''),
          type: 'button', dataset: { platform: platform.key },
          onclick: function () {
            state.collected.platform = platform.key;
            state.collected.page = 1;
            tabs.querySelectorAll('.tab').forEach(function (button) {
              button.classList.toggle('active', button.dataset.platform === platform.key);
            });
            load();
          }
        }, platform.name + ' ' + fmtNumber(platform.total)));
      });

      function load() {
        setLoading(itemHost, '正在读取条目…');
        api('/api/collected/items?platform=' + encodeURIComponent(state.collected.platform) +
          '&page=' + state.collected.page + '&page_size=' + state.collected.pageSize +
          '&q=' + encodeURIComponent(state.collected.q)).then(function (data) {
          state.collected.data = data;
          itemHost.replaceChildren(buildCollectedTable(data));
          pager(pagerHost, data.page, data.pages, data.total, function (page) {
            state.collected.page = page;
            load();
          });
          updateInfo();
        }).catch(function (error) {
          setError(itemHost, error, load);
          pagerHost.replaceChildren();
        });
      }

      function updateInfo() {
        var count = state.collected.selected.size;
        info.textContent = selectedCountText(count) + ' · 导出格式 ' +
          (state.collected.formats.size ? Array.from(state.collected.formats).join(' / ') : '未选择');
        var noFormats = state.collected.formats.size === 0;
        exportSelected.disabled = count === 0 || noFormats;
        exportFiltered.disabled = noFormats;
        downloadSelected.disabled = count === 0;
      }

      function checkedKeys() {
        return Array.from(state.collected.selected);
      }

      function startExport(keys) {
        var formats = Array.from(state.collected.formats);
        if (!formats.length) {
          toast('请至少选择一种导出格式', 'error');
          return;
        }
        exportSelected.disabled = true;
        exportFiltered.disabled = true;
        post('/api/collected/export', {
          keys: keys || [], platform: state.collected.platform,
          keyword: state.collected.q, formats: formats,
          comment_pages: Number(comment.value || 0)
        }).then(function (data) {
          watchJob(jobHost, data.job, function () {
            exportSelected.disabled = state.collected.selected.size === 0;
            exportFiltered.disabled = false;
            load();
          });
        }).catch(function (error) {
          exportSelected.disabled = state.collected.selected.size === 0;
          exportFiltered.disabled = false;
          toast(error.message, 'error');
        });
      }

      function startDownload(keys) {
        if (!confirm('将按最高画质、4 线程把勾选内容下载到服务端导出目录，继续？')) return;
        exportSelected.disabled = true;
        exportFiltered.disabled = true;
        downloadSelected.disabled = true;
        post('/api/collected/download', { keys: keys }).then(function (data) {
          watchJob(jobHost, data.job, function () {
            exportSelected.disabled = state.collected.selected.size === 0;
            exportFiltered.disabled = false;
            downloadSelected.disabled = state.collected.selected.size === 0;
          });
        }).catch(function (error) {
          exportSelected.disabled = state.collected.selected.size === 0;
          exportFiltered.disabled = false;
          downloadSelected.disabled = state.collected.selected.size === 0;
          toast(error.message, 'error');
        });
      }

      exportSelected.addEventListener('click', function () { startExport(checkedKeys()); });
      exportFiltered.addEventListener('click', function () { startExport([]); });
      downloadSelected.addEventListener('click', function () { startDownload(checkedKeys()); });

      search.addEventListener('input', function () {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(function () {
          state.collected.q = search.value.trim();
          state.collected.page = 1;
          load();
          updateInfo();
        }, 320);
      });

      function buildCollectedTable(data) {
        if (!data.items.length) {
          return empty('没有符合条件的条目。可清空搜索词或切换平台。');
        }
        var pageKeys = data.items.map(function (item) { return item.key; });
        var allChecked = pageKeys.every(function (key) { return state.collected.selected.has(key); });
        var someChecked = pageKeys.some(function (key) { return state.collected.selected.has(key); });
        var selectAll = h('input', {
          type: 'checkbox', checked: allChecked,
          onchange: function (event) {
            pageKeys.forEach(function (key) {
              if (event.target.checked) state.collected.selected.add(key);
              else state.collected.selected.delete(key);
            });
            itemHost.replaceChildren(buildCollectedTable(data));
            updateInfo();
          }
        });
        selectAll.indeterminate = someChecked && !allChecked;
        var head = h('tr', null,
          h('th', { style: 'width:36px' }, selectAll),
          h('th', null, '标题'), h('th', null, '作者'), h('th', null, '发布时间'),
          h('th', null, '类型'), h('th', { class: 'num' }, '播放'), h('th', { class: 'num' }, '点赞'));
        var body = h('tbody');
        data.items.forEach(function (item) {
          var checkbox = h('input', {
            type: 'checkbox', checked: state.collected.selected.has(item.key),
            onchange: function (event) {
              if (event.target.checked) state.collected.selected.add(item.key);
              else state.collected.selected.delete(item.key);
              updateInfo();
            }
          });
          var title = item.url
            ? h('a', { href: item.url, target: '_blank', rel: 'noopener noreferrer' }, item.title)
            : item.title;
          body.appendChild(h('tr', null,
            h('td', null, checkbox),
            h('td', { class: 'ellipsis' }, title),
            h('td', null, item.author || '-'),
            h('td', null, fmtTime(item.publish_time)),
            h('td', null, h('span', { class: 'badge' }, kindLabel(item.kind))),
            h('td', { class: 'num' }, fmtNumber(item.play)),
            h('td', { class: 'num' }, fmtNumber(item.like))));
        });
        return h('div', { class: 'table-wrap' },
          h('table', null, h('thead', null, head), body));
      }

      root.replaceChildren(
        h('div', { class: 'toolbar' },
          h('span', { class: 'muted small' }, '平台'),
          tabs,
          h('span', { class: 'spacer' })),
        h('div', { class: 'card' },
          h('div', { class: 'toolbar' },
            search,
            formatBoxes,
            h('span', { class: 'spacer' }),
            info,
            exportSelected,
            exportFiltered,
            downloadSelected),
          itemHost,
          pagerHost),
        jobHost);
      info.id = 'collectedInfo';
      load();
    }).catch(function (error) {
      setError(root, error, function () { renderCollected(root); });
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
      h('option', { value: 'text' }, '纯文本'));
    statusSel.value = state.assets.status || '';
    var dlBtn = h('button', { class: 'btn primary', type: 'button' }, '下载选中');
    var selectAll = h('input', { type: 'checkbox', title: '全选本页' });
    var selectedLabel = h('span', { class: 'muted small' });
    var countsLabel = h('span', { class: 'muted small' });
    var selected = state.assets.dlSelected || (state.assets.dlSelected = new Set());
    var running = false;
    var STATUS_BADGE = {
      ok: ['已完整', 'ok'], partial: ['部分下载', 'warn'],
      missing: ['该下未下', 'danger'], text: ['纯文本', '']
    };

    function updateButtons() {
      dlBtn.disabled = running || selected.size === 0;
      dlBtn.textContent = running ? '下载中…' : '下载选中';
      selectedLabel.textContent = selected.size ? '已选 ' + selected.size + ' 条' : '';
    }

    function load() {
      setLoading(tableHost, '正在读取已获取资源…');
      var useStatus = !!state.assets.status;
      var url = (useStatus ? '/api/assets/entries-status' : '/api/assets/entries')
        + '?page=' + state.assets.page + '&page_size=20&q='
        + encodeURIComponent(state.assets.q) + '&platform=' + encodeURIComponent(state.assets.platform)
        + (useStatus ? '&status=' + encodeURIComponent(state.assets.status) : '');
      api(url)
        .then(function (data) {
          if (useStatus && data.counts) {
            var parts = [];
            ['ok', 'partial', 'missing', 'text'].forEach(function (key) {
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
              h('td', { class: 'ellipsis' }, item.file || '-')));
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
                h('th', { class: 'num' }, '点赞'), h('th', null, '导出文件'))),
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
      var useStatus = !!state.assets.status;
      var url = (useStatus ? '/api/assets/entries-status' : '/api/assets/entries')
        + '?page=' + state.assets.page + '&page_size=20&q='
        + encodeURIComponent(state.assets.q) + '&platform=' + encodeURIComponent(state.assets.platform)
        + (useStatus ? '&status=' + encodeURIComponent(state.assets.status) : '');
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
        h('th', null, '类型'), h('th', null, '路径状态'));
      var body = h('tbody');
      (data.items || []).forEach(function (item) {
        body.appendChild(h('tr', null,
          h('td', { class: 'num' }, item.index),
          h('td', null, item.name || '-'),
          h('td', null, item.type === 'link' ? '链接' : '软件'),
          h('td', null, item.exists ? '可用' : '未找到')));
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
      var body = h('tbody');
      rows.forEach(function (item) {
        body.appendChild(h('tr', null,
          h('td', null, item.label || '-'),
          h('td', { class: 'muted small' }, item.usage || '-'),
          h('td', null, item.schedule || '-'),
          h('td', null, item.installed ? '已安装' : '未安装'),
          h('td', null, item.next_run || '-')));
      });
      host.appendChild(h('div', { class: 'table-wrap' },
        h('table', { class: 'db-table' }, h('thead', null, head), body)));
      host.appendChild(h('div', { class: 'muted small', style: 'margin-top:8px' },
        '只读展示；安装 / 删除与时间设置请在桌面端「任务」页操作。'));
    }).catch(function (error) {
      setError(host, error, function () { renderTasks(host); });
    });
  }

  function renderSystem(host) {
    host.replaceChildren();
    setLoading(host, '正在读取系统信息…');
    api('/api/system').then(function (data) {
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

      var webui = h('div', { class: 'card' });
      webui.appendChild(h('div', { class: 'row-title', style: 'margin-bottom:8px' }, 'WebUI'));
      var cfg = data.webui || {};
      [
        ['端口', cfg.port],
        ['局域网访问', cfg.lan ? '已开启' : '关闭'],
        ['公网隧道', cfg.tunnel || 'none']
      ].forEach(function (pair) {
        webui.appendChild(h('div', { class: 'kv' },
          h('span', { class: 'muted' }, pair[0]),
          h('span', null, pair[1])));
      });
      webui.appendChild(h('div', { class: 'muted small', style: 'margin-top:8px' },
        data.note || ''));
      host.appendChild(webui);
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

  function renderRecord(host) {
    host.replaceChildren();
    setLoading(host, '正在读取录制配置…');
    api('/api/record/overview').then(function (data) {
      host.replaceChildren();
      host.appendChild(h('div', { class: 'card', style: 'margin-bottom:12px' },
        h('div', { class: 'muted small' }, data.note || '当前为只读。')));

      var summary = data.summary || {};
      var stats = h('div', { class: 'grid cols-4', style: 'margin-bottom:12px' });
      stats.appendChild(statCard('主播', fmtNumber(summary.anchors || 0), '已配置主播'));
      stats.appendChild(statCard('监控中', fmtNumber(summary.monitoring || 0), '配置为监控'));
      stats.appendChild(statCard('录制意图', fmtNumber(summary.want_record || 0), '开播后录制'));
      stats.appendChild(statCard('平台', fmtNumber(summary.platforms || 0), '有主播的平台'));
      host.appendChild(stats);

      var platforms = data.platforms || [];
      if (!platforms.length) {
        host.appendChild(empty('尚未配置录制主播。请先在桌面端「直播录制」页添加主播。'));
        return;
      }
      var keys = platforms.map(function (item) { return item.key; });
      if (state.record.platform && keys.indexOf(state.record.platform) < 0) {
        state.record.platform = '';
      }

      var toolbar = h('div', { class: 'toolbar' });
      var select = h('select', { class: 'select' });
      select.appendChild(h('option', { value: '' }, '全部平台'));
      platforms.forEach(function (item) {
        select.appendChild(h('option', { value: item.key },
          item.name + '（' + item.anchors.length + '）'));
      });
      select.value = state.record.platform || '';
      select.addEventListener('change', function () {
        state.record.platform = select.value;
        draw();
      });
      toolbar.appendChild(select);
      toolbar.appendChild(h('span', { class: 'muted small' },
        '点击表头排序；启停录制仍在桌面端操作。'));
      host.appendChild(toolbar);

      var tableHost = h('div');
      host.appendChild(tableHost);

      function visibleRows() {
        var rows = [];
        platforms.forEach(function (platform) {
          if (state.record.platform && platform.key !== state.record.platform) return;
          platform.anchors.forEach(function (anchor) {
            rows.push(Object.assign({}, anchor, {
              platform_key: platform.key,
              platform_name: platform.name
            }));
          });
        });
        return rows;
      }

      function draw() {
        var items = visibleRows();
        if (!items.length) {
          tableHost.replaceChildren(empty('该平台暂无主播。'));
          return;
        }
        var columns = [
          { key: 'platform_name', label: '平台' },
          { key: 'room', label: '房间号' },
          { key: 'name', label: '主播' },
          { key: 'remark', label: '备注' },
          { key: 'monitoring', label: '监控', bool: true },
          { key: 'want_record', label: '录制意图', bool: true },
          { key: 'quality', label: '画质' }
        ];
        items = sortRows(items, columns, state.record);
        var head = h('tr');
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
        var body = h('tbody');
        items.forEach(function (item) {
          body.appendChild(h('tr', null,
            h('td', null, item.platform_name || item.platform_key),
            h('td', null, item.room || '-'),
            h('td', { title: item.title || '' }, item.name || '-'),
            h('td', { title: item.remark || '' }, item.remark || '-'),
            h('td', null, item.monitoring ? '是' : '否'),
            h('td', null, item.want_record ? '是' : '否'),
            h('td', null, item.quality || '-')));
        });
        tableHost.replaceChildren(h('div', { class: 'table-wrap' },
          h('table', { class: 'db-table' }, h('thead', null, head), body)));
      }
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
        var docSelect = h('select', { class: 'select' });
        data.docs.forEach(function (doc) {
          docSelect.appendChild(h('option', { value: doc.key }, doc.name));
        });
        docSelect.value = data.selected || '';
        docSelect.addEventListener('change', function () {
          state.tcdoc.docKey = docSelect.value;
          renderTcdoc(root);
        });
        var jobHost = h('div');
        var latest = data.latest || {};
        var stats = data.tables.stats || {};
        var collectBtn = h('button', { class: 'btn primary', type: 'button' }, '立即采集');
        var syncBtn = h('button', { class: 'btn', type: 'button' }, '表格同步');
        var rowsHost = h('div');

        function start(url, label) {
          collectBtn.disabled = true;
          syncBtn.disabled = true;
          post(url, {}).then(function (job) {
            watchJob(jobHost, job.job, function () {
              collectBtn.disabled = false;
              syncBtn.disabled = false;
              toast(label + '完成', 'success');
              renderTcdoc(root);
            });
          }).catch(function (error) {
            collectBtn.disabled = false;
            syncBtn.disabled = false;
            toast(error.message, 'error');
          });
        }
        collectBtn.addEventListener('click', function () { start('/api/tcdoc/collect', '采集'); });
        syncBtn.addEventListener('click', function () { start('/api/tcdoc/sync', '表格同步'); });

        var dailyRows = h('tbody');
        (data.daily || []).slice().reverse().forEach(function (row) {
          dailyRows.appendChild(h('tr', null,
            h('td', null, fmtTime(row.ts)),
            h('td', { class: 'num' }, fmtNumber(row.views)),
            h('td', { class: 'num' }, fmtNumber(row.members)),
            h('td', { class: 'num' }, fmtNumber(row.readers)),
            h('td', { class: 'num' }, row.views_delta == null ? '-' : fmtNumber(row.views_delta)),
            h('td', { class: 'num' }, row.members_delta == null ? '-' : fmtNumber(row.members_delta))));
        });

        var sheetRows = h('tbody');
        (data.tables.sheets || []).forEach(function (sheet) {
          sheetRows.appendChild(h('tr', null,
            h('td', null, sheet.tab_name || sheet.tab_id),
            h('td', { class: 'num' }, fmtNumber(sheet.n_rows)),
            h('td', null, fmtTime(sheet.fetched_at)),
            h('td', null, sheet.skipped ? h('span', { class: 'badge warn' }, '已跳过') : h('span', { class: 'badge ok' }, '已同步')),
            h('td', null, h('button', {
              class: 'btn', type: 'button',
              onclick: function () {
                state.tcdoc.rowsTab = sheet.tab_id;
                state.tcdoc.rowsPage = 1;
                loadSheet(sheet);
              }
            }, '查看行'))));
        });

        function loadSheet(sheet) {
          setLoading(rowsHost, '正在读取表格行…');
          api('/api/tcdoc/table-rows?doc_key=' + encodeURIComponent(state.tcdoc.docKey) +
            '&tab_id=' + encodeURIComponent(sheet.tab_id) + '&page=' + state.tcdoc.rowsPage + '&page_size=50')
            .then(function (rows) {
              var columns = (sheet.columns || []).map(function (column) {
                return typeof column === 'string' ? column : (column && column.name) || '';
              }).filter(Boolean);
              if (!columns.length && rows.rows.length && rows.rows[0].values) {
                columns = Object.keys(rows.rows[0].values);
              }
              var head = h('tr', null, h('th', null, '行号'));
              columns.forEach(function (column) { head.appendChild(h('th', null, column)); });
              var body = h('tbody');
              rows.rows.forEach(function (row) {
                var tr = h('tr', null, h('td', { class: 'num' }, row.row_no));
                columns.forEach(function (column) {
                  var value = row.values ? row.values[column] : '';
                  if (value === null || value === undefined) value = '';
                  tr.appendChild(h('td', { class: 'ellipsis' }, String(value)));
                });
                body.appendChild(tr);
              });
              var hostTable = h('div', { class: 'table-wrap' },
                h('table', null, h('thead', null, head), body));
              var pagerHost = h('div', { class: 'pager' });
              pager(pagerHost, rows.page, rows.pages, rows.total, function (page) {
                state.tcdoc.rowsPage = page;
                loadSheet(sheet);
              });
              rowsHost.replaceChildren(
                h('div', { class: 'muted small', style: 'margin-bottom:8px' },
                  sheet.tab_name + ' · ' + rows.total + ' 行'),
                hostTable, pagerHost);
            }).catch(function (error) { setError(rowsHost, error); });
        }

        var pushRows = h('tbody');
        (data.push_history || []).forEach(function (entry) {
          pushRows.appendChild(h('tr', null,
            h('td', null, fmtTime(entry.time)),
            h('td', null, entry.tab_name || entry.tab_id || '-'),
            h('td', null, entry.source || '-'),
            h('td', null, h('span', { class: 'badge ' + (entry.status === '成功' ? 'ok' : 'warn') }, entry.status || '-')),
            h('td', { class: 'num' }, fmtNumber(entry.added)),
            h('td', { class: 'num' }, fmtNumber(entry.images)),
            h('td', { class: 'num' }, fmtNumber(entry.failed))));
        });

        root.replaceChildren(
          h('div', { class: 'toolbar' },
            h('span', { class: 'muted small' }, '文档'), docSelect,
            data.auto ? h('span', { class: 'badge ok' }, '自动维护已开启') : h('span', { class: 'badge' }, '自动维护已关闭'),
            h('span', { class: 'spacer' }),
            collectBtn, syncBtn),
          h('div', { class: 'grid cols-4' },
            statCard('最新浏览量', latest.views, latest.ts ? '采集于 ' + fmtTime(latest.ts) : '暂无采集记录'),
            statCard('成员数', latest.members, data.doc_stats ? ('本地记录 ' + fmtNumber(data.doc_stats.records) + ' 条') : ''),
            statCard('浏览人', data.browser_stats ? data.browser_stats.records : 0,
              data.browser_stats && data.browser_stats.last ? '最近 ' + fmtTime(data.browser_stats.last) : ''),
            statCard('表格行数', stats.rows, (stats.sheets || 0) + ' 张子表' + (stats.last ? ' · ' + fmtTime(stats.last) : ''))),
          jobHost,
          h('div', { class: 'card', style: 'margin-top:12px' },
            h('div', { class: 'card-title' }, '采集历史'),
            dailyRows.children.length
              ? h('div', { class: 'table-wrap' }, h('table', null,
                h('thead', null, h('tr', null,
                  h('th', null, '时间'), h('th', { class: 'num' }, '浏览量'),
                  h('th', { class: 'num' }, '成员数'), h('th', { class: 'num' }, '浏览人'),
                  h('th', { class: 'num' }, '浏览增量'), h('th', { class: 'num' }, '成员增量'))), dailyRows))
              : empty('暂无采集历史。点「立即采集」开始第一次采集。')),
          h('div', { class: 'card' }, h('div', { class: 'card-title' }, '表格同步状态'),
            sheetRows.children.length
              ? h('div', { class: 'table-wrap' }, h('table', null,
                h('thead', null, h('tr', null,
                  h('th', null, '子表'), h('th', { class: 'num' }, '行数'),
                  h('th', null, '同步时间'), h('th', null, '状态'), h('th', null, ''))), sheetRows))
              : empty('暂无表格同步数据。')),
          rowsHost,
          h('div', { class: 'card' }, h('div', { class: 'card-title' }, '推送历史'),
            pushRows.children.length
              ? h('div', { class: 'table-wrap' }, h('table', null,
                h('thead', null, h('tr', null,
                  h('th', null, '时间'), h('th', null, '子表'), h('th', null, '来源'),
                  h('th', null, '状态'), h('th', { class: 'num' }, '新增'),
                  h('th', { class: 'num' }, '图片'), h('th', { class: 'num' }, '失败'))), pushRows))
              : empty('暂无推送记录。')));
      }).catch(function (error) {
        setError(root, error, function () { renderTcdoc(root); });
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
    navigate(routes[hash] ? hash : 'collected');
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
    history.replaceState(null, '', location.pathname);
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
