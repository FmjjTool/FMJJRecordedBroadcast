(function () {
  'use strict';
  var saved = null;

  function renderTcdocCloudMapping(host, helpers, state) {
    var h = helpers.h, api = helpers.api, post = helpers.post, toast = helpers.toast;
    var c = state.cloud || (state.cloud = {
      session: '', tab: 'official', fields: [], visible: [], selected: new Set(),
      drafts: {}, page: 1, query: '', order: { order: '', asc: true },
      preview: '', plans: [], needsRefresh: false, job: '', action: '', result: ''
    });
    var busy = !!c.job, serial = 0, rows = [];
    var target = h('select', { class: 'select', 'aria-label': '目标工作表' },
      h('option', { value: 'official', selected: c.tab === 'official' }, '官方动态'),
      h('option', { value: 'live', selected: c.tab === 'live' }, '直播记录'));
    var search = h('input', { class: 'input', type: 'search',
      placeholder: '搜索记录 ID 或字段内容', value: c.query, style: 'min-width:220px' });
    var refresh = h('button', { class: 'btn', type: 'button' }, '刷新云端');
    var status = h('span', { class: 'muted small' }, c.result || '');
    var columns = h('details', { open: true, style: 'margin-top:8px' },
      h('summary', { class: 'small' }, '选择显示列'));
    var tableHost = h('div'), paging = h('div', { class: 'pager' });
    var previewHost = h('div'), jobHost = h('div');
    var selection = h('span', { class: 'small', role: 'status' });
    var clear = h('button', { class: 'btn', type: 'button' }, '取消勾选');
    var previewBtn = h('button', { class: 'btn primary', type: 'button' }, '预览并回写');

    function message(text, kind) {
      status.className = kind === 'error' ? 'error small' : 'muted small';
      status.textContent = text || ''; c.result = text || '';
    }
    function hasDrafts() { return Object.keys(c.drafts).some(function (rid) {
      return Object.keys(c.drafts[rid] || {}).length > 0;
    }); }
    function hasSelectedDrafts() { return Array.from(c.selected).some(function (rid) {
      return Object.keys(c.drafts[rid] || {}).length > 0;
    }); }
    function confirmDraftDiscard(action) {
      return !hasDrafts() || window.confirm(action + '会清除尚未回写的云端修改草稿，继续吗？');
    }
    function refreshCloud() {
      if (!confirmDraftDiscard('刷新')) return;
      run('refresh', { tab: c.tab, session: c.session });
    }
    function update() {
      busy = !!c.job;
      target.disabled = busy; search.disabled = busy; refresh.disabled = busy;
      clear.disabled = busy || !c.selected.size;
      previewBtn.disabled = busy || c.needsRefresh || !c.selected.size || !hasSelectedDrafts();
      selection.textContent = '已选择 ' + c.selected.size + ' 条 · 未回写草稿 ' +
        Object.keys(c.drafts).reduce(function (sum, key) { return sum + Object.keys(c.drafts[key]).length; }, 0) +
        ' 项' + (c.needsRefresh ? ' · 请先刷新' : '');
      previewBtn.textContent = '预览并回写（' + c.selected.size + ' 条）';
      columns.querySelectorAll('input').forEach(function (input) { input.disabled = busy; });
      tableHost.querySelectorAll('input,button').forEach(function (input) { input.disabled = busy; });
    }
    function run(action, payload) {
      if (c.job) return;
      c.job = 'pending'; c.action = action; update(); message('正在处理…');
      post('/api/tcdoc/mapping/cloud/' + action, payload).then(function (result) {
        c.job = result.job; observeJob();
      }).catch(function (error) {
        c.job = ''; update(); message(error.message, 'error');
      });
    }
    function observeJob() {
      var action = c.action;
      helpers.watchJob(jobHost, c.job, function (job) {
        c.job = '';
        var result = job.result || {};
        if (result.cancelled) { message('任务已取消，可重新操作'); update(); return; }
        if (action === 'schema' || action === 'refresh') {
          c.session = result.session || ''; c.fields = result.fields || [];
          c.visible = c.visible.filter(function (id) {
            return c.fields.some(function (field) { return field.id === id; });
          });
          if (!c.visible.length) c.visible = c.fields.slice(0, 8).map(function (field) { return field.id; });
          c.selected.clear(); c.drafts = {}; c.preview = ''; c.plans = []; c.page = 1;
          c.needsRefresh = false;
          message((result.target || '云端数据') + ' · 已读取 ' + (result.total || 0) + ' 条');
          drawColumns(); loadRows();
        } else if (action === 'preview') {
          if (result.error) message(result.error, 'error');
          else { c.preview = result.preview || ''; c.plans = result.plans || []; drawPreview(); message('已生成回写预览，请核对后确认'); }
        } else if (action === 'apply') {
          (result.record_ids || []).forEach(function (rid) { delete c.drafts[rid]; c.selected.delete(rid); });
          c.preview = ''; c.plans = []; c.needsRefresh = !!(result.needs_refresh || result.updated ||
            result.unknown_record_outcome || result.error);
          drawPreview(); drawTable();
          if (result.error || result.unknown_record_outcome) {
            message(result.error || '回写结果不明，请先人工核对，再刷新云端记录', 'error');
          } else {
            message(result.summary || '回写完成，已读回核对');
            if (c.needsRefresh && (!hasDrafts() || confirmDraftDiscard('刷新'))) {
              run('refresh', { tab: c.tab, session: c.session });
            } else if (c.needsRefresh) {
              message('回写已完成；刷新前无法继续回写，未回写草稿仍保留。');
            }
          }
        }
        update();
      }, { cancelable: true, indeterminate: true, onSettled: function (job) {
        c.job = '';
        if (job.status === 'error') message(job.error || '任务未能完成，请重试', 'error');
        if (job.status === 'cancelled') message('任务已取消，可重新操作');
        update();
      }});
    }
    function drawColumns() {
      columns.querySelectorAll('label').forEach(function (node) { node.remove(); });
      c.fields.forEach(function (field) {
        var check = h('input', { type: 'checkbox', checked: c.visible.indexOf(field.id) >= 0 });
        check.addEventListener('change', function () {
          c.visible = check.checked ? c.visible.concat(field.id) : c.visible.filter(function (id) { return id !== field.id; });
          if (!c.visible.length) { check.checked = true; c.visible = [field.id]; toast('至少保留一列显示', 'info'); return; }
          drawTable();
        });
        columns.appendChild(h('label', { class: 'small', style: 'display:inline-flex;gap:4px;margin:6px 12px 0 0' }, check, field.name + ' · ' + field.type));
      });
      update();
    }
    function loadRows() {
      if (!c.session) { drawTable(); return; }
      var request = ++serial;
      api('/api/tcdoc/mapping/cloud/rows?session=' + encodeURIComponent(c.session)
        + '&page=' + c.page + '&query=' + encodeURIComponent(c.query)
        + '&sort_column=' + encodeURIComponent(c.order.order)
        + '&direction=' + (c.order.order ? (c.order.asc ? 'asc' : 'desc') : '')
        + '&visible=' + encodeURIComponent(c.visible.join(','))).then(function (result) {
        if (!host.isConnected || request !== serial) return;
        rows = result.rows || []; c.page = result.page || 1;
        c.needsRefresh = !!result.needs_refresh;
        drawTable();
        helpers.pager(paging, result.page, result.pages, result.total, function (page) { c.page = page; loadRows(); });
      }).catch(function (error) { if (request === serial) message(error.message, 'error'); });
    }
    function drawTable() {
      if (!c.session) { tableHost.replaceChildren(helpers.empty('正在读取云端数据，或请点击刷新云端。')); return; }
      if (!rows.length) { tableHost.replaceChildren(helpers.empty('当前筛选没有记录。')); return; }
      var fieldsById = {}; c.fields.forEach(function (field) { fieldsById[field.id] = field; });
      var shown = c.visible.map(function (id) { return fieldsById[id]; }).filter(Boolean);
      var head = h('tr', null, h('th', null, ''));
      var body = h('tbody');
      function header(key, title) {
        var th = h('th', { class: 'sortable', tabIndex: 0,
          'aria-sort': c.order.order === key ? (c.order.asc ? 'ascending' : 'descending') : 'none' },
          title + (c.order.order === key ? (c.order.asc ? ' ↑' : ' ↓') : ''));
        function change() { helpers.cycleSort(c.order, key); c.page = 1; loadRows(); }
        th.addEventListener('click', change); th.addEventListener('keydown', function (event) {
          if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); change(); }
        }); return th;
      }
      head.appendChild(header('record_id', 'recordID'));
      shown.forEach(function (field) { head.appendChild(header(field.id, field.name)); });
      rows.forEach(function (row) {
        var tr = h('tr');
        var cb = h('input', { type: 'checkbox', checked: c.selected.has(row.record_id),
          'aria-label': '选择 ' + row.record_id });
        cb.addEventListener('change', function () {
          if (cb.checked) c.selected.add(row.record_id); else c.selected.delete(row.record_id); update();
        });
        tr.appendChild(h('td', null, cb));
        tr.appendChild(h('td', { class: 'ellipsis', title: row.record_id }, row.record_id));
        shown.forEach(function (field) {
          var original = row.values[field.id] || '';
          var value = c.drafts[row.record_id] && c.drafts[row.record_id][field.id] !== undefined
            ? c.drafts[row.record_id][field.id] : original;
          var cell = h('td', { class: 'ellipsis', title: value || original }, value || '-');
          if (c.drafts[row.record_id] && c.drafts[row.record_id][field.id] !== undefined) cell.className += ' changed';
          if (field.editable) cell.addEventListener('dblclick', function () {
            if (c.needsRefresh) { toast('请先刷新云端记录，再继续编辑', 'info'); return; }
            if (!c.selected.has(row.record_id) || busy) { toast('请先勾选记录后再编辑', 'info'); return; }
            var next = window.prompt('编辑「' + field.name + '」', value);
            if (next === null) return;
            c.drafts[row.record_id] = c.drafts[row.record_id] || {};
            if (next === original) delete c.drafts[row.record_id][field.id]; else c.drafts[row.record_id][field.id] = next;
            if (!Object.keys(c.drafts[row.record_id]).length) delete c.drafts[row.record_id];
            drawTable(); update();
          });
          tr.appendChild(cell);
        });
        body.appendChild(tr);
      });
      tableHost.replaceChildren(h('div', { class: 'table-wrap' }, h('table', { class: 'db-table' }, h('thead', null, head), body)));
      update();
    }
    function drawPreview() {
      if (!c.plans.length) { previewHost.replaceChildren(); return; }
      var lines = []; c.plans.forEach(function (plan) {
        lines.push('记录 ID：' + plan.record_id);
        (plan.changes || []).forEach(function (change) { lines.push(change.field + '：' + (change.remote || '空') + ' → ' + (change.local || '空')); });
        lines.push('');
      });
      var confirm = h('button', { class: 'btn primary', type: 'button' }, '确认回写');
      confirm.addEventListener('click', function () {
        if (!window.confirm('确认将预览中的 ' + c.plans.length + ' 条记录逐条回写云端吗？\n\n写入后会读回核对，结果不明时不会自动重试。')) return;
        run('apply', { session: c.session, preview: c.preview, confirmed: true });
      });
      previewHost.replaceChildren(h('div', { class: 'card', style: 'margin-top:12px' },
        h('div', { class: 'card-title' }, '回写预览'), h('pre', { class: 'small', style: 'white-space:pre-wrap;max-height:260px;overflow:auto' }, lines.join('\n')), confirm));
    }
    target.addEventListener('change', function () {
      var nextTab = target.value;
      if (nextTab !== c.tab && !confirmDraftDiscard('切换子表')) {
        target.value = c.tab; return;
      }
      c.tab = nextTab; c.session = ''; c.fields = []; c.visible = []; c.selected.clear();
      c.drafts = {}; c.preview = ''; c.plans = []; c.needsRefresh = false;
      drawColumns(); drawTable(); run('schema', { tab: c.tab });
    });
    search.addEventListener('input', function () { c.query = search.value; c.page = 1; loadRows(); });
    refresh.addEventListener('click', refreshCloud);
    clear.addEventListener('click', function () { c.selected.clear(); drawTable(); update(); });
    previewBtn.addEventListener('click', function () {
      if (c.needsRefresh) { message('请先刷新云端记录，再预览回写', 'error'); return; }
      var updates = Array.from(c.selected).filter(function (rid) {
        return Object.keys(c.drafts[rid] || {}).length > 0;
      }).map(function (rid) { return { record_id: rid, values: c.drafts[rid] }; });
      if (!updates.length) { toast('请先选择含有修改草稿的记录', 'info'); return; }
      run('preview', { session: c.session, updates: updates });
    });
    host.replaceChildren(
      h('div', { class: 'card' },
        h('div', { class: 'toolbar', style: 'flex-wrap:wrap;gap:8px' },
          h('span', { class: 'muted small' }, '目标工作表'), target,
          h('span', { style: 'flex:1' }), refresh),
        h('div', { class: 'toolbar', style: 'margin-top:8px;flex-wrap:wrap;gap:8px' }, search, status),
        columns, jobHost),
      h('div', { class: 'card' }, tableHost, paging,
        h('div', { class: 'toolbar', style: 'margin-top:12px;flex-wrap:wrap;gap:8px' }, selection, clear,
          h('span', { style: 'flex:1' }), previewBtn)), previewHost);
    drawColumns(); update();
    if (!c.session) run('schema', { tab: c.tab }); else loadRows();
  }

  window.renderTcdocMapping = function (host, helpers) {
    var h = helpers.h, api = helpers.api, post = helpers.post, toast = helpers.toast;
    var s = saved || (saved = { mode: 'cloud', cloud: null, session: '', headers: [], total: 0, tab: 'official', fields: [],
      mapping: {}, key: '', preview: '', counts: null, selected: new Set(), page: 1,
      mode: 'all', order: { order: '', asc: true }, visible: [], job: '', action: '', result: '' });
    var modeHost = h('div');
    var modeTabs = h('div', { class: 'tabs', style: 'margin-bottom:12px' });
    [['cloud', '云端数据（默认）'], ['local', '本地表格映射']].forEach(function (item) {
      modeTabs.appendChild(h('button', { class: 'tab' + (s.mode === item[0] ? ' active' : ''),
        type: 'button', onclick: function () { s.mode = item[0]; renderTcdocMapping(host, helpers); } }, item[1]));
    });
    host.replaceChildren(modeTabs, modeHost);
    if (s.mode === 'cloud') {
      renderTcdocCloudMapping(modeHost, helpers, s);
      return;
    }
    host = modeHost;
    var busy = !!s.job, serial = 0, rows = [], widths = [];
    var file = h('input', { type: 'file', accept: '.csv,.tsv,.xlsx,.json', class: 'input',
      'aria-label': '选择本地表格', style: 'max-width:340px' });
    var importBtn = h('button', { type: 'button', class: 'btn' }, '导入表格');
    var fileInfo = h('span', { class: 'muted small' }, s.session ? '本地 ' + s.total + ' 行' : '尚未导入表格');
    var target = h('select', { class: 'select', 'aria-label': '目标工作表' },
      h('option', { value: 'official', selected: s.tab === 'official' }, '官方动态'),
      h('option', { value: 'live', selected: s.tab === 'live' }, '直播记录'));
    var connect = h('button', { class: 'btn', type: 'button' }, '连接云端');
    var compare = h('button', { class: 'btn primary', type: 'button' }, '识别差异');
    var write = h('button', { class: 'btn primary', type: 'button' }, '写入选中行');
    var clear = h('button', { class: 'btn', type: 'button' }, '取消勾选');
    var selected = h('span', { class: 'small', role: 'status' });
    var note = h('div', { class: 'small', role: 'status', style: 'margin:8px 0;overflow-wrap:anywhere' }, s.result);
    var mappingHost = h('div'), tableHost = h('div'), detailHost = h('div'), jobHost = h('div');
    var filters = h('select', { class: 'select', 'aria-label': '显示记录' });
    [['all', '全部本地行'], ['differences', '仅差异行'], ['新增', '仅新增'], ['更新', '仅更新'],
      ['不变', '内容一致'], ['错误', '需要处理']].forEach(function (item) {
      filters.appendChild(h('option', { value: item[0], selected: item[0] === s.mode }, item[1]));
    });
    var counts = h('span', { class: 'small' });
    var columns = h('details', null, h('summary', { class: 'small' }, '显示列'));
    var paging = h('div', { class: 'pager', style: 'justify-content:flex-end' });
    var card = h('div', { class: 'card' },
      h('div', { class: 'toolbar', style: 'flex-wrap:wrap;gap:8px' }, file, importBtn, fileInfo),
      h('div', { class: 'toolbar', style: 'margin-top:12px;flex-wrap:wrap;gap:8px' },
        h('label', null, '目标工作表'), target, connect, h('span', { style: 'flex:1' }), compare),
      mappingHost, note, jobHost);
    var tableCard = h('div', { class: 'card' },
      h('div', { class: 'toolbar', style: 'flex-wrap:wrap;gap:8px' }, filters, counts, columns),
      tableHost, paging,
      h('div', { class: 'toolbar', style: 'margin-top:12px;flex-wrap:wrap;gap:8px' }, selected, clear,
        h('span', { style: 'flex:1' }), write));
    host.replaceChildren(card, tableCard, detailHost);

    function invalidate() {
      s.preview = ''; s.counts = null; s.selected.clear(); s.mode = 'all'; s.page = 1;
      filters.value = 'all'; detailHost.replaceChildren();
    }
    function update() {
      file.disabled = importBtn.disabled = target.disabled = busy;
      connect.disabled = busy || !s.session;
      compare.disabled = busy || !s.fields.length || !s.key || !s.mapping[s.key];
      write.disabled = busy || !s.preview || !s.selected.size;
      clear.disabled = busy || !s.selected.size;
      filters.disabled = busy || !s.preview;
      selected.textContent = '已勾选 ' + s.selected.size + ' 行';
      write.textContent = '写入选中 ' + s.selected.size + ' 行';
      mappingHost.querySelectorAll('select').forEach(function (input) { input.disabled = busy; });
      tableHost.querySelectorAll('[data-row-select]').forEach(function (input) { input.disabled = busy; });
      counts.textContent = s.counts ? ['新增', '更新', '不变', '错误'].map(function (key) {
        return key + ' ' + s.counts[key] + ' 行';
      }).join(' · ') : s.session ? '本地 ' + s.total + ' 行 · 请识别差异后勾选' : '';
    }
    function error(message) { note.className = 'error small'; note.textContent = message; }
    function message(text) { note.className = 'small'; note.textContent = text; s.result = text; }
    function finish(action, result) {
      if (result.cancelled && action !== 'apply') { message('已取消，可重试'); return; }
      if (action === 'schema') {
        s.fields = result.fields || []; s.mapping = result.mapping || {}; s.key = result.key_col || '';
        invalidate(); drawMapping(); loadRows();
        message('已匹配 ' + Object.keys(s.mapping).length + ' 列，请核对匹配列后识别差异');
      } else if (action === 'compare') {
        s.preview = result.preview || ''; s.counts = result.counts; s.selected.clear();
        s.mode = 'differences'; filters.value = s.mode; s.page = 1;
        message(result.summary); loadRows();
      } else {
        invalidate(); loadRows();
        if (result.error) error(result.summary || result.error);
        else message(result.summary || '写入已完成，请重新比对查看剩余差异');
      }
    }
    function observeJob() {
      var action = s.action;
      helpers.watchJob(jobHost, s.job, function (job) {
        finish(action, job.result || {});
      }, { cancelable: true, indeterminate: true, onSettled: function (job) {
        busy = false; s.job = ''; s.action = '';
        if (job.status === 'error') {
          if (action === 'apply') invalidate();
          error(job.error || '任务未能完成，请重试');
        } else if (job.status === 'cancelled') {
          if (action === 'apply') invalidate();
          message('已取消，请重新比对后继续');
        }
        update();
      } });
    }
    function run(action, payload) {
      if (busy) return; busy = true; update(); message('进行中');
      post('/api/tcdoc/mapping/' + action, payload).then(function (result) {
        s.job = result.job; s.action = action; observeJob();
      }).catch(function (err) { busy = false; error(err.message); update(); });
    }
    function drawMapping() {
      mappingHost.replaceChildren();
      if (!s.fields.length) return;
      var grid = h('div', { style: 'display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:8px;margin-top:12px' });
      s.headers.forEach(function (name) {
        var select = h('select', { class: 'select', style: 'min-width:0;width:100%', 'aria-label': name + ' 对应云端列' },
          h('option', { value: '' }, '不写入此列'));
        s.fields.filter(function (field) { return field.supported; }).forEach(function (field) {
          select.appendChild(h('option', { value: field.id, selected: s.mapping[name] === field.id }, field.name + ' · ' + field.type));
        });
        select.addEventListener('change', function () {
          s.mapping[name] = select.value; invalidate(); drawMapping(); loadRows(); update();
        });
        grid.appendChild(h('label', { class: 'small', style: 'display:grid;gap:4px;min-width:0' }, name + ' → 云端列', select));
      });
      var key = h('select', { class: 'select', 'aria-label': '匹配标识列' }, h('option', { value: '' }, '请选择'));
      s.headers.filter(function (name) { return !!s.mapping[name]; }).forEach(function (name) {
        key.appendChild(h('option', { value: name, selected: s.key === name }, name));
      });
      if (!s.mapping[s.key]) s.key = '';
      key.addEventListener('change', function () { s.key = key.value; invalidate(); loadRows(); update(); });
      mappingHost.appendChild(grid);
      mappingHost.appendChild(h('div', { class: 'toolbar', style: 'margin-top:12px;flex-wrap:wrap' },
        h('label', null, '用哪一列匹配同一行'), key,
        h('span', { class: 'muted small' }, '优先选择作品链接或唯一编号；重复标识会阻止写入')));
    }
    function drawColumns() {
      columns.querySelectorAll('label').forEach(function (node) { node.remove(); });
      s.headers.forEach(function (name) {
        var check = h('input', { type: 'checkbox', checked: s.visible.indexOf(name) >= 0 });
        check.addEventListener('change', function () {
          if (check.checked && s.visible.length >= 4) { check.checked = false; toast('同时显示最多 4 列，双击行可查看全部列', 'info'); return; }
          s.visible = check.checked ? s.visible.concat(name) : s.visible.filter(function (value) { return value !== name; });
          widths = []; drawTable();
        });
        columns.appendChild(h('label', { class: 'small', style: 'display:block;margin:6px 0' }, check, ' ', name));
      });
    }
    function showDetail(row) {
      var content = h('div', { class: 'card' },
        h('div', { class: 'toolbar' }, h('strong', null, '本地第 ' + row.row_no + ' 行 · ' + row.op),
          h('span', { style: 'flex:1' }), h('button', { class: 'btn', type: 'button',
            onclick: function () { detailHost.replaceChildren(); } }, '关闭详情')));
      var changes = row.changes || [];
      if (changes.length) {
        content.appendChild(h('div', { class: 'small', style: 'margin-top:8px' }, '将写入的字段（云端 → 本地）'));
        changes.forEach(function (change) {
          content.appendChild(h('div', { style: 'margin-top:8px;overflow-wrap:anywhere' },
            h('strong', null, change.field), h('div', { class: 'muted small' }, '云端：' + (change.remote || '空')),
            h('div', { class: 'small' }, '本地：' + (change.local || '空'))));
        });
      } else content.appendChild(h('p', { class: 'small' }, row.message || '尚未比对云端'));
      content.appendChild(h('div', { class: 'small', style: 'margin-top:16px' }, '全部本地内容'));
      s.headers.forEach(function (name) {
        content.appendChild(h('div', { class: 'small', style: 'margin-top:6px;overflow-wrap:anywhere' },
          h('strong', null, name + '：'), row.local[name] || '空'));
      });
      detailHost.replaceChildren(content);
      content.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
    function drawTable() {
      if (!s.session) { tableHost.replaceChildren(helpers.empty('请选择本地 CSV、TSV、XLSX 或 JSON 文件，再点「导入表格」。')); return; }
      if (!rows.length) { tableHost.replaceChildren(helpers.empty(s.preview
        ? '当前筛选没有记录，可切换「全部本地行」查看。' : '当前表格没有可显示的行，请重新导入。')); return; }
      var cols = [{ key: 'row_no', title: '本地行', weight: 1 }, { key: 'op', title: '状态', weight: 1 }]
        .concat(s.visible.map(function (name) { return { key: 'local:' + name, local: name, title: name, weight: 3 }; }))
        .concat([{ key: 'message', title: '差异说明', weight: 4 }]);
      if (widths.length !== cols.length) widths = cols.map(function (col) { return col.weight; });
      var total = widths.reduce(function (a, b) { return a + b; }, 0), cg = h('colgroup', null, h('col', { style: 'width:36px' }));
      cols.forEach(function (col, index) { cg.appendChild(h('col', { style: 'width:calc('
        + (widths[index] / total * 100) + '% - ' + (widths[index] / total * 90) + 'px)' })); });
      cg.appendChild(h('col', { style: 'width:54px' }));
      var head = h('tr', null, h('th', null, '')), body = h('tbody');
      cols.forEach(function (col) {
        var th = h('th', { class: 'sortable', tabIndex: 0,
          'aria-sort': s.order.order === col.key ? (s.order.asc ? 'ascending' : 'descending') : 'none' },
          col.title + (s.order.order === col.key ? (s.order.asc ? ' ↑' : ' ↓') : ''));
        function change() { helpers.cycleSort(s.order, col.key); s.page = 1; loadRows(); }
        th.addEventListener('click', change); th.addEventListener('keydown', function (e) {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); change(); }
        }); head.appendChild(th);
      });
      head.appendChild(h('th', null, '详情'));
      rows.forEach(function (row) {
        var tr = h('tr'), selectable = !!s.preview && (row.op === '新增' || row.op === '更新');
        var cb = selectable ? h('input', { type: 'checkbox', checked: s.selected.has(row.row_no),
          'data-row-select': row.row_no, 'aria-label': '写入本地第 ' + row.row_no + ' 行', disabled: busy }) : null;
        if (cb) cb.addEventListener('change', function () {
          if (cb.checked) s.selected.add(row.row_no); else s.selected.delete(row.row_no); update();
        });
        tr.appendChild(h('td', null, cb));
        cols.forEach(function (col) {
          var value = col.local ? row.local[col.local] : row[col.key];
          tr.appendChild(h('td', { class: 'ellipsis', title: value || '',
            style: 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap' }, value || '-'));
        });
        tr.appendChild(h('td', null, h('button', { class: 'btn mini', type: 'button',
          onclick: function () { showDetail(row); } }, '查看')));
        tr.addEventListener('dblclick', function () { showDetail(row); }); body.appendChild(tr);
      });
      function fit() {
        widths = cols.map(function (col) {
          return Math.min(35, Math.max(4, col.title.length, Math.max.apply(null, rows.map(function (row) {
            return String((col.local ? row.local[col.local] : row[col.key]) || '').length;
          }))));
        }); drawTable();
      }
      function reset() { widths = []; drawTable(); }
      head.addEventListener('contextmenu', function (event) {
        event.preventDefault();
        var menu = h('div', { class: 'card', style: 'position:fixed;z-index:10000;left:' + event.clientX + 'px;top:' + event.clientY + 'px' },
          h('button', { class: 'btn mini', onclick: function () { menu.remove(); fit(); } }, '列宽自适应'),
          h('button', { class: 'btn mini', onclick: function () { menu.remove(); reset(); } }, '重置列宽'));
        document.body.appendChild(menu); document.addEventListener('click', function () { menu.remove(); }, { once: true });
      });
      tableHost.replaceChildren(h('div', { class: 'table-wrap', style: 'margin-top:8px' }, h('table',
        { class: 'db-table', style: 'table-layout:fixed;width:100%' }, cg, h('thead', null, head), body)),
        h('div', { class: 'toolbar', style: 'margin-top:8px' },
          h('span', { class: 'muted small', style: 'flex:1' }, '双击行或点「查看」核对全部列与云端差异'),
          h('button', { class: 'btn mini', type: 'button', onclick: fit }, '自适应'),
          h('button', { class: 'btn mini', type: 'button', onclick: reset }, '重置')));
    }
    function loadRows() {
      if (!s.session) { drawTable(); return; }
      var request = ++serial;
      api('/api/tcdoc/mapping/rows?session=' + encodeURIComponent(s.session) + '&page=' + s.page
        + '&mode=' + encodeURIComponent(s.mode) + '&preview=' + encodeURIComponent(s.preview)
        + '&sort_column=' + encodeURIComponent(s.order.order)
        + '&direction=' + (s.order.order ? (s.order.asc ? 'asc' : 'desc') : '')).then(function (result) {
        if (!host.isConnected || request !== serial) return; rows = result.rows || []; s.page = result.page; drawTable();
        helpers.pager(paging, result.page, result.pages, result.total, function (page) { s.page = page; loadRows(); });
      }).catch(function (err) { if (request === serial) error(err.message); });
    }
    importBtn.addEventListener('click', function () {
      var chosen = file.files && file.files[0];
      if (!chosen) { error('请先选择本地表格'); return; }
      if (chosen.size > 4 * 1024 * 1024) { error('导入文件不能超过 4 MB，请拆分后重试'); return; }
      busy = true; update();
      var reader = new FileReader();
      reader.onerror = function () { busy = false; error('文件读取失败，请重新选择'); update(); };
      reader.onload = function () {
        post('/api/tcdoc/mapping/import', { name: chosen.name, content: String(reader.result).split(',')[1] }).then(function (result) {
          s.session = result.session; s.headers = result.headers; s.total = result.total;
          s.fields = []; s.mapping = {}; s.key = ''; s.visible = s.headers.slice(0, 3); s.order = { order: '', asc: true };
          invalidate(); fileInfo.textContent = '本地 ' + result.total + ' 行 · ' + result.format;
          message('本地表格已显示，请选择目标工作表并连接云端');
          drawMapping(); drawColumns(); loadRows();
        }).catch(function (err) { error(err.message); }).finally(function () { busy = false; update(); });
      }; reader.readAsDataURL(chosen);
    });
    target.addEventListener('change', function () {
      s.tab = target.value; s.fields = []; s.mapping = {}; s.key = ''; invalidate(); drawMapping(); loadRows(); update();
      message('目标工作表已切换，请连接云端');
    });
    connect.addEventListener('click', function () { run('schema', { session: s.session, tab: s.tab }); });
    compare.addEventListener('click', function () {
      invalidate(); run('compare', { session: s.session, mapping: s.mapping, key_col: s.key });
    });
    filters.addEventListener('change', function () { s.mode = filters.value; s.page = 1; loadRows(); });
    clear.addEventListener('click', function () { s.selected.clear(); drawTable(); update(); });
    write.addEventListener('click', function () {
      if (!s.preview || !s.selected.size || busy) return;
      var numbers = Array.from(s.selected).sort(function (a, b) { return a - b; });
      var name = s.tab === 'live' ? '直播记录' : '官方动态';
      if (!window.confirm('将向《封茗囧菌数据汇总库》的「' + name + '」写入选中的 ' + numbers.length + ' 行。\n'
        + '本地行号：' + numbers.slice(0, 30).join('、') + (numbers.length > 30 ? ' 等' : '')
        + '\n仅执行已勾选行的新增或差异字段更新，写后逐行读回核对。\n确认写入这些行？')) return;
      var preview = s.preview; s.preview = '';
      run('apply', { session: s.session, preview: preview, selected: numbers, confirmed: true });
    });
    drawMapping(); drawColumns(); loadRows(); update();
    if (s.job) observeJob();
  };
})();
