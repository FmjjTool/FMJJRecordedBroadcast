(function () {
  'use strict';

  var params = new URLSearchParams(location.search);
  var token = params.get('token') || localStorage.getItem('webui-token') || '';
  if (params.get('token')) localStorage.setItem('webui-token', token);
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
    assets: { tab: 'entries', page: 1, q: '', platform: '', dbPlatform: '', dbPage: 1, dbQ: '', dbOrder: '', dbAsc: false, cat: '' },
    tcdoc: { docKey: '', rowsTab: '', rowsPage: 1 }
  };
  var routes = {
    collected: { title: '采集结果', load: renderCollected },
    assets: { title: '资产库', load: renderAssets },
    tcdoc: { title: '腾讯文档', load: renderTcdoc }
  };
  var searchTimer = null;

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
            showGate('访问令牌无效，请重新打开带令牌的地址。');
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

  function watchJob(host, jobId, onDone) {
    host.replaceChildren();
    var box = h('div', { class: 'job' });
    var head = h('div', { class: 'job-head' });
    var status = h('span', { class: 'job-status' }, '任务运行中');
    var link = h('span', { class: 'job-done' });
    var bar = h('div', { class: 'progress' }, h('i'));
    var resultLine = h('div', { class: 'muted small', style: 'margin:2px 0 6px' });
    var log = h('pre', { class: 'job-log' });
    head.appendChild(status);
    head.appendChild(link);
    box.appendChild(head);
    box.appendChild(bar);
    box.appendChild(resultLine);
    box.appendChild(log);
    host.appendChild(box);

    function tick() {
      if (!box.isConnected) return;
      api('/api/jobs/' + jobId).then(function (job) {
        status.textContent = job.status === 'done' ? '已完成' : (job.status === 'error' ? '失败' : (job.message || '运行中'));
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
          return;
        }
        if (job.status === 'error') {
          link.replaceChildren(h('span', { class: 'badge warn' }, job.error || '任务失败'));
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
      [['entries', '已获取资源'], ['db', '数据库浏览'], ['local', '本地资源']].forEach(function (pair) {
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

    function load() {
      setLoading(tableHost, '正在读取已获取资源…');
      api('/api/assets/entries?page=' + state.assets.page + '&page_size=20&q=' +
        encodeURIComponent(state.assets.q) + '&platform=' + encodeURIComponent(state.assets.platform))
        .then(function (data) {
          if (!data.items.length) {
            tableHost.replaceChildren(empty('没有匹配的导出 JSON。可清空搜索词或切换平台。'));
            pagerHost.replaceChildren();
            return;
          }
          var body = h('tbody');
          data.items.forEach(function (item) {
            var title = item.url
              ? h('a', { href: item.url, target: '_blank', rel: 'noopener noreferrer' }, item.title)
              : item.title;
            body.appendChild(h('tr', null,
              h('td', { class: 'ellipsis' }, title),
              h('td', null, item.author || '-'),
              h('td', null, platformLabel(item.platform)),
              h('td', null, fmtTime(item.publish_time)),
              h('td', { class: 'num' }, fmtNumber(item.play)),
              h('td', { class: 'num' }, fmtNumber(item.like)),
              h('td', { class: 'ellipsis' }, item.file || '-')));
          });
          tableHost.replaceChildren(h('div', { class: 'table-wrap' },
            h('table', null,
              h('thead', null, h('tr', null,
                h('th', null, '标题'), h('th', null, '作者'), h('th', null, '平台'),
                h('th', null, '发布时间'), h('th', { class: 'num' }, '播放'),
                h('th', { class: 'num' }, '点赞'), h('th', null, '导出文件'))),
              body)));
          pager(pagerHost, data.page, data.pages, data.total, function (page) {
            state.assets.page = page;
            load();
          });
        }).catch(function (error) { setError(tableHost, error, load); });
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
    host.appendChild(h('div', { class: 'toolbar' }, search, platform));
    host.appendChild(tableHost);
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
    var groupHost = h('div', { class: 'list' });
    var select = h('select', { class: 'select' });
    summary.categories.forEach(function (name) {
      select.appendChild(h('option', { value: name }, name));
    });
    if (!summary.categories.length) {
      host.appendChild(empty('暂无可浏览的本地分类目录。'));
      return;
    }
    if (!state.assets.cat || summary.categories.indexOf(state.assets.cat) < 0) {
      state.assets.cat = summary.categories.indexOf('已获取数据') >= 0
        ? '已获取数据' : summary.categories[0];
    }
    select.value = state.assets.cat;
    select.addEventListener('change', function () {
      state.assets.cat = select.value;
      load();
    });
    host.appendChild(h('div', { class: 'toolbar' }, h('span', { class: 'muted small' }, '分类'), select));
    host.appendChild(groupHost);

    function load() {
      setLoading(groupHost, '正在扫描分类目录…');
      api('/api/assets/local/groups?cat=' + encodeURIComponent(state.assets.cat)).then(function (data) {
        if (!data.groups.length) {
          groupHost.replaceChildren(empty('该分类下暂无文件。'));
          return;
        }
        var list = h('div', { class: 'list' });
        data.groups.forEach(function (group) {
          var files = h('ul', { class: 'row-files' });
          (group.files || []).slice(0, 12).forEach(function (file) {
            files.appendChild(h('li', null, file.name + (file.size ? ' · ' + fmtBytes(file.size) : '')));
          });
          if ((group.files || []).length > 12) {
            files.appendChild(h('li', null, '…共 ' + group.files.length + ' 个文件'));
          }
          list.appendChild(h('div', { class: 'row-card' },
            h('div', { class: 'row-head' }, h('div', { class: 'row-title' }, group.title || '-')),
            h('div', { class: 'row-meta' },
              h('span', null, group.date || '无日期'),
              h('span', null, '图片 ' + group.n_img),
              h('span', null, '视频 ' + group.n_vid),
              h('span', null, '音频 ' + group.n_aud),
              h('span', null, '文档 ' + group.n_doc),
              h('span', null, fmtBytes(group.size)),
              h('span', null, group.mtime || '')),
            files));
        });
        groupHost.replaceChildren(list);
        if (data.truncated) toast('目录文件较多，已截断显示。', 'info');
      }).catch(function (error) { setError(groupHost, error, load); });
    }
    load();
  }

  function fmtBytes(value) {
    var size = Number(value || 0);
    if (size < 1024) return size + ' B';
    if (size < 1024 * 1024) return (size / 1024).toFixed(1) + ' KB';
    if (size < 1024 * 1024 * 1024) return (size / 1024 / 1024).toFixed(1) + ' MB';
    return (size / 1024 / 1024 / 1024).toFixed(2) + ' GB';
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
    token = value;
    localStorage.setItem('webui-token', token);
    gate.classList.add('hidden');
    init();
  });
  gateToken.addEventListener('keydown', function (event) {
    if (event.key === 'Enter') document.getElementById('gateSubmit').click();
  });

  if (!token) {
    showGate('请从 config/webui_url.txt 打开完整地址，或粘贴令牌。');
  } else {
    init();
  }
})();
