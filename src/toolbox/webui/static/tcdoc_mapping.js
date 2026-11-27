(function () {
  'use strict';
  var saved = null;

  window.renderTcdocMapping = function (host, helpers) {
    var h = helpers.h, api = helpers.api, post = helpers.post, toast = helpers.toast;
    var s = saved || (saved = { session: '', headers: [], total: 0, tab: 'official', fields: [],
      mapping: {}, key: '', preview: '', counts: null, selected: new Set(), page: 1,
      mode: 'all', order: { order: '', asc: true }, visible: [], job: '', action: '', result: '' });
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
