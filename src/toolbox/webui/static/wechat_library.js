(function () {
  'use strict';

  window.createWechatLibraryCard = function (options) {
    var h = options.h, api = options.api, post = options.post;
    var toast = options.toast, watchJob = options.watchJob;
    var root = h('div', { class: 'card', style: 'margin-bottom:12px' });
    var tabs = h('div', { class: 'tabs', role: 'tablist', 'aria-label': '公众号功能' });
    var panel = h('div');
    var jobs = h('div', { 'aria-live': 'polite' });
    var schema, view = 'links', busy = false, pending = [];
    var accountState = { offset: 0, sort: '', order: '', selected: '' };
    var articleState = { offset: 0, sort: '', order: '', selected: new Set() };
    var accountForm, articleForm, accountHost, articleHost, editHost;
    var tabButtons = {};

    root.appendChild(tabs); root.appendChild(panel); root.appendChild(jobs);
    [['links', '链接导出'], ['accounts', '公众号资料'], ['articles', '文章归档']].forEach(function (item) {
      tabButtons[item[0]] = h('button', {
        class: 'tab', type: 'button', role: 'tab',
        style: 'border-radius:0;border-top:0;border-left:0;border-right:0;background:transparent',
        onclick: function () { show(item[0]); }
      }, item[1]);
      tabs.appendChild(tabButtons[item[0]]);
    });

    function errorText(error) { return error && error.message ? error.message : '请稍后重试'; }
    function message(host, text, retry) {
      host.replaceChildren(h('div', { class: 'empty' }, text, retry
        ? h('button', { class: 'btn', type: 'button', style: 'margin-left:8px', onclick: retry }, '重试') : null));
    }
    function form(group, values, changed) {
      var box = h('div', { class: 'form-grid', style: 'margin-bottom:12px' });
      var controls = {};
      Object.keys(schema.groups[group]).forEach(function (name) {
        var spec = schema.groups[group][name];
        if (spec.hidden) return;
        var value = Object.prototype.hasOwnProperty.call(values || {}, name) ? values[name] : spec.default;
        var control;
        if (spec.type === 'strlist') {
          control = h('div', { class: 'toolbar', style: 'margin-bottom:0;min-height:32px' });
          controls[name] = [];
          (spec.options || []).forEach(function (item) {
            var check = h('input', { type: 'checkbox', name: name, value: item.value, checked: value.indexOf(item.value) >= 0 });
            check.addEventListener('change', function () { if (changed) changed(name); });
            controls[name].push(check);
            control.appendChild(h('label', {}, check, ' ' + item.label));
          });
        } else {
          var props = { class: spec.type === 'text' ? 'textarea' : (spec.type === 'choice' ? 'select' : 'input'), name: name, 'aria-label': spec.label };
          if (spec.type === 'choice') {
            control = h('select', props);
            (spec.options || []).forEach(function (item) { control.appendChild(h('option', { value: item.value }, item.label)); });
          } else if (spec.type === 'text') {
            props.rows = '4'; props.placeholder = spec.placeholder || '';
            control = h('textarea', props);
          } else {
            props.type = spec.type === 'int' ? 'number' : (spec.type === 'date' ? 'date' : 'text');
            if (spec.min !== undefined) props.min = spec.min;
            if (spec.max !== undefined) props.max = spec.max;
            if (spec.max_length) props.maxlength = spec.max_length;
            if (spec.readonly) props.readonly = true;
            control = h('input', props);
          }
          control.value = value == null ? '' : value;
          control.addEventListener(spec.type === 'str' || spec.type === 'text' ? 'input' : 'change', function () { if (changed) changed(name); });
          controls[name] = control;
        }
        var label = h('label', { class: 'small' }, h('span', { class: 'muted' }, spec.label), control);
        if (spec.type === 'text') label.style.gridColumn = '1 / -1';
        box.appendChild(label);
      });
      return { node: box, controls: controls, values: function () {
        var result = {};
        Object.keys(controls).forEach(function (name) {
          result[name] = Array.isArray(controls[name])
            ? controls[name].filter(function (el) { return el.checked; }).map(function (el) { return el.value; })
            : controls[name].value;
        });
        return result;
      } };
    }
    function mutation(label, fn, primary, available) {
      var button = h('button', { class: 'btn' + (primary ? ' primary' : ''), type: 'button', onclick: fn }, label);
      pending.push({ button: button, available: available || function () { return true; } });
      updateBusy();
      return button;
    }
    function updateBusy() {
      pending.forEach(function (entry) {
        entry.button.disabled = busy || !entry.available();
      });
    }
    function start(path, payload, label, confirmText) {
      if (busy) return;
      if (confirmText && !window.confirm(confirmText)) return;
      busy = true; updateBusy();
      post(path, payload).then(function (result) {
        watchJob(jobs, result.job, function (job) {
          var outcome = job.result || {};
          toast(outcome.summary || (label + '完成'), outcome.failed ? 'warning' : 'success');
          if (options.reload) options.reload();
        }, { cancelable: true, onSettled: function (job) {
          busy = false; updateBusy();
          if (job.status === 'error') toast(label + '失败：' + (job.error || '请稍后重试'), 'error');
          if (accountHost && accountHost.isConnected) loadAccounts();
          if (articleHost && articleHost.isConnected) loadArticles();
        } });
      }).catch(function (error) { busy = false; updateBusy(); toast(errorText(error), 'error'); });
    }
    function query(path, values) {
      var params = new URLSearchParams();
      Object.keys(values).forEach(function (key) { if (values[key] !== '') params.set(key, values[key]); });
      return api(path + '?' + params.toString());
    }
    function sort(state, key, reload) {
      if (state.sort !== key) { state.sort = key; state.order = 'asc'; }
      else if (state.order === 'asc') state.order = 'desc';
      else { state.sort = ''; state.order = ''; }
      state.offset = 0; reload();
    }
    function table(columns, rows, state, reload, rowFactory) {
      var head = h('tr');
      var body = h('tbody');
      var grid = h('table', { class: 'fg-table db-table' }, h('thead', {}, head), body);
      var defaults = columns.map(function (column) { return column.width || 1; });
      var widths = defaults.slice();
      columns.forEach(function (column) {
        var active = state.sort === column.key;
        var th = h('th', column.sort === false ? {} : {
          class: 'sortable', tabindex: '0', role: 'columnheader',
          'aria-sort': active ? (state.order === 'asc' ? 'ascending' : 'descending') : 'none',
          onclick: function () { sort(state, column.key, reload); },
          onkeydown: function (event) { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); sort(state, column.key, reload); } }
        }, column.node || column.label, active ? (state.order === 'asc' ? ' ↑' : ' ↓') : '');
        head.appendChild(th);
      });
      rows.forEach(function (row) { body.appendChild(rowFactory(row)); });
      function setWidths() {
        var total = widths.reduce(function (sum, value) { return sum + value; }, 0);
        Array.from(head.children).forEach(function (th, i) { th.style.width = (widths[i] / total * 100) + '%'; });
      }
      function fit() {
        widths = columns.map(function (column, index) {
          if (column.sort === false) return defaults[index];
          var length = String(column.label || '').length;
          rows.forEach(function (row) { length = Math.max(length, String(row[column.key] || '').length); });
          return Math.max(4, Math.min(36, length));
        });
        setWidths();
      }
      function reset() { widths = defaults.slice(); setWidths(); }
      head.addEventListener('contextmenu', function (event) {
        event.preventDefault();
        document.querySelectorAll('.wechat-column-menu').forEach(function (el) { el.remove(); });
        var menu = h('div', { class: 'fg-column-menu wechat-column-menu', style: 'left:' + Math.max(0, Math.min(event.clientX, window.innerWidth - 170)) + 'px;top:' + Math.max(0, Math.min(event.clientY, window.innerHeight - 100)) + 'px' },
          h('button', { class: 'btn mini', type: 'button', onclick: function () { fit(); menu.remove(); } }, '列宽自适应'),
          h('button', { class: 'btn mini', type: 'button', onclick: function () { reset(); menu.remove(); } }, '重置列宽'));
        document.body.appendChild(menu);
        setTimeout(function () { document.addEventListener('click', function () { menu.remove(); }, { once: true }); }, 0);
      });
      setWidths();
      return h('div', {}, h('div', { class: 'table-wrap', style: 'overflow-x:hidden' }, grid),
        h('div', { class: 'toolbar fg-table-controls' },
          h('button', { class: 'btn mini', type: 'button', onclick: fit }, '自适应'),
          h('button', { class: 'btn mini', type: 'button', onclick: reset }, '重置')));
    }
    function cell(value) { return h('td', { title: value == null ? '' : String(value) }, value === '' || value == null ? '—' : String(value)); }
    function pagination(state, total, limit, reload) {
      var page = Math.floor(state.offset / limit) + 1, count = Math.max(1, Math.ceil(total / limit));
      return h('div', { class: 'toolbar' },
        h('span', { class: 'muted small' }, '第 ' + page + ' / ' + count + ' 页 · 共 ' + total + ' 条'),
        h('span', { class: 'spacer' }),
        h('button', { class: 'btn', type: 'button', disabled: state.offset === 0, onclick: function () { state.offset = Math.max(0, state.offset - limit); reload(); } }, '上一页'),
        h('button', { class: 'btn', type: 'button', disabled: state.offset + limit >= total, onclick: function () { state.offset += limit; reload(); } }, '下一页'));
    }
    function renderLinks() {
      var links = form('collect', {}), formats = form('export', {});
      panel.appendChild(links.node); panel.appendChild(formats.node);
      panel.appendChild(h('div', { class: 'toolbar' },
        mutation('收录到归档', function () { start('/api/wechat/collect', links.values(), '收录'); }),
        mutation('开始导出', function () {
          var payload = Object.assign({}, links.values(), formats.values());
          if (!payload.text.trim()) { toast('请粘贴公众号文章链接', 'warning'); return; }
          if (!payload.formats.length) { toast('请至少选择一种导出格式', 'warning'); return; }
          start('/api/tools/artifacts/wechat', payload, '导出', '将按所选格式导出这些文章，是否继续？');
        }, true)));
      panel.appendChild(h('div', { class: 'muted small' }, '文件保存到公众号导出目录；PDF 由桌面端渲染。'));
    }
    function renderAccounts() {
      var debounce;
      accountForm = form('accounts', {}, function () {
        clearTimeout(debounce); debounce = setTimeout(function () { accountState.offset = 0; loadAccounts(); }, 300);
      });
      accountHost = h('div'); editHost = h('div');
      panel.appendChild(accountForm.node); panel.appendChild(accountHost); panel.appendChild(editHost);
      loadAccounts();
    }
    function loadAccounts() {
      var host = accountHost;
      if (!host || !host.isConnected) return;
      var request = (host._request || 0) + 1; host._request = request;
      message(host, '加载中…');
      var filters = Object.assign({}, accountForm.values(), { offset: accountState.offset, sort: accountState.sort, order: accountState.order });
      query('/api/wechat/accounts', filters).then(function (result) {
        if (!host.isConnected || host._request !== request) return;
        if (!result.items.length) { message(host, '暂无公众号资料，可先在链接导出中收录文章。'); editHost.replaceChildren(); return; }
        var columns = [{ key: 'name', label: '公众号', width: 4 }, { key: 'biz', label: '标识', width: 4 }, { key: 'remark', label: '备注', width: 5 }, { key: 'article_count', label: '文章数', width: 2 }, { key: 'last_sync', label: '上次同步', width: 4 }];
        host.replaceChildren(table(columns, result.items, accountState, loadAccounts, function (item) {
          var row = h('tr', { tabindex: '0', class: accountState.selected === item.biz ? 'selected' : '', 'aria-selected': String(accountState.selected === item.biz) },
            cell(item.name), cell(item.biz), cell(item.remark), cell(item.article_count), cell(item.last_sync));
          function select() {
            accountState.selected = item.biz;
            Array.from(row.parentNode.children).forEach(function (el) { el.classList.toggle('selected', el === row); el.setAttribute('aria-selected', String(el === row)); });
            editAccount(item);
          }
          row.addEventListener('click', select);
          row.addEventListener('keydown', function (event) { if (event.key === 'Enter') select(); });
          row.addEventListener('dblclick', function () { articleState.biz = item.biz; show('articles'); });
          return row;
        }), pagination(accountState, result.total, Number(filters.limit), loadAccounts));
        var selected = result.items.filter(function (item) { return item.biz === accountState.selected; })[0];
        if (selected) editAccount(selected);
        else message(editHost, '选择一条公众号资料以编辑备注或同步文章。');
      }).catch(function (error) { if (host.isConnected && host._request === request) message(host, errorText(error), loadAccounts); });
    }
    function editAccount(item) {
      pending = pending.filter(function (entry) { return entry.button.isConnected; });
      var editor = form('account', item), sync = form('sync', {});
      editHost.replaceChildren(editor.node, h('div', { class: 'toolbar' },
        mutation('保存资料', function () {
          if (busy) return; busy = true; updateBusy();
          post('/api/wechat/accounts/save', editor.values()).then(function () { toast('资料已保存', 'success'); loadAccounts(); })
            .catch(function (error) { toast(errorText(error), 'error'); })
            .finally(function () { busy = false; updateBusy(); });
        }, true),
        h('button', { class: 'btn', type: 'button', onclick: function () { articleState.biz = item.biz; show('articles'); } }, '查看文章')),
        sync.node, h('div', { class: 'toolbar' }, mutation('开始同步', function () {
          start('/api/wechat/sync', Object.assign({}, sync.values(), { biz: item.biz }), '同步', '将同步该公众号的文章资料，是否继续？');
        }, true)));
      updateBusy();
    }
    function renderArticles() {
      var debounce;
      articleForm = form('articles', { biz: articleState.biz || '' }, function () {
        clearTimeout(debounce); debounce = setTimeout(function () { articleState.offset = 0; loadArticles(); }, 300);
      });
      var formats = form('export', {}), selectedText = h('span', { class: 'muted small' });
      articleState.counter = function () { selectedText.textContent = '已选 ' + articleState.selected.size + ' 篇'; updateBusy(); };
      articleHost = h('div');
      panel.appendChild(articleForm.node);
      panel.appendChild(formats.node);
      panel.appendChild(h('div', { class: 'toolbar' },
        selectedText,
        mutation('导出所选', function () {
          start('/api/wechat/export', Object.assign({}, formats.values(), { ids: Array.from(articleState.selected) }), '导出', '将导出已选的 ' + articleState.selected.size + ' 篇文章，是否继续？');
        }, true, function () { return articleState.selected.size > 0; }),
        mutation('重试失败', function () {
          start('/api/wechat/retry', Object.assign({}, formats.values(), { biz: articleForm.values().biz }), '重试', '将重试当前公众号范围内所有失败文章，是否继续？');
        }),
        mutation('导出索引', function () {
          if (busy) return; busy = true; updateBusy();
          options.downloadPost('/api/wechat/index', Object.assign({}, articleForm.values(), { sort: articleState.sort, order: articleState.order }), 'wechat-articles.csv')
            .then(function () { toast('索引已导出', 'success'); })
            .catch(function (error) { toast(errorText(error), 'error'); })
            .finally(function () { busy = false; updateBusy(); });
        }),
        h('button', { class: 'btn', type: 'button', onclick: function () { articleState.selected.clear(); articleState.counter(); loadArticles(); } }, '清除选择')));
      panel.appendChild(articleHost); articleState.counter(); loadArticles();
    }
    function loadArticles() {
      var host = articleHost;
      if (!host || !host.isConnected) return;
      var request = (host._request || 0) + 1; host._request = request;
      message(host, '加载中…');
      var filters = Object.assign({}, articleForm.values(), { offset: articleState.offset, sort: articleState.sort, order: articleState.order });
      query('/api/wechat/articles', filters).then(function (result) {
        if (!host.isConnected || host._request !== request) return;
        if (!result.items.length) { message(host, '暂无符合条件的文章。'); return; }
        var all = h('input', { type: 'checkbox', 'aria-label': '选择本页全部文章' });
        all.checked = result.items.every(function (item) { return articleState.selected.has(item.id); });
        all.indeterminate = !all.checked && result.items.some(function (item) { return articleState.selected.has(item.id); });
        all.addEventListener('change', function () {
          result.items.forEach(function (item) { if (all.checked) articleState.selected.add(item.id); else articleState.selected.delete(item.id); });
          articleState.counter(); loadArticles();
        });
        var columns = [{ key: 'select', label: '', node: all, sort: false, width: 1 }, { key: 'account', label: '公众号', width: 4 }, { key: 'title', label: '文章标题', width: 10 }, { key: 'publish_time', label: '发布时间', width: 5 }, { key: 'status', label: '状态', width: 3 }, { key: 'error', label: '处理信息', width: 5 }];
        host.replaceChildren(table(columns, result.items, articleState, loadArticles, function (item) {
          var check = h('input', { type: 'checkbox', checked: articleState.selected.has(item.id), 'aria-label': '选择文章：' + item.title });
          var title = item.url ? h('a', { href: item.url, target: '_blank', rel: 'noopener noreferrer', title: '打开文章；右键可复制链接' }, item.title || '打开文章') : (item.title || '—');
          var row = h('tr', { class: check.checked ? 'selected' : '' }, h('td', {}, check), cell(item.account), h('td', { title: item.title }, title), cell(item.publish_time), cell(schema.statuses[item.status] || item.status), cell(item.error));
          check.addEventListener('change', function () {
            if (check.checked) articleState.selected.add(item.id); else articleState.selected.delete(item.id);
            row.classList.toggle('selected', check.checked);
            all.checked = result.items.every(function (entry) { return articleState.selected.has(entry.id); });
            all.indeterminate = !all.checked && result.items.some(function (entry) { return articleState.selected.has(entry.id); });
            articleState.counter();
          });
          return row;
        }), pagination(articleState, result.total, Number(filters.limit), loadArticles));
      }).catch(function (error) { if (host.isConnected && host._request === request) message(host, errorText(error), loadArticles); });
    }
    function show(next) {
      view = next;
      Object.keys(tabButtons).forEach(function (key) { tabButtons[key].classList.toggle('active', key === view); tabButtons[key].setAttribute('aria-selected', String(key === view)); });
      panel.replaceChildren(); pending = [];
      if (!schema) { message(panel, '加载中…'); return; }
      if (view === 'links') renderLinks();
      else if (view === 'accounts') renderAccounts();
      else renderArticles();
      updateBusy();
    }
    function init() {
      message(panel, '加载中…');
      api('/api/wechat/schema').then(function (result) { schema = result; show(view); })
        .catch(function (error) { message(panel, errorText(error), init); });
    }
    show(view); init();
    return root;
  };
})();
