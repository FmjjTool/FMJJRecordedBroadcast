(function () {
  'use strict';

  var state = { rows: [], page: 1, order: '', asc: true, busy: false, job: '',
    options: { path: '', recursive: true, template: '' } };

  function h(tag, props) {
    var node = document.createElement(tag);
    Object.keys(props || {}).forEach(function (key) {
      if (key === 'text') node.textContent = props[key];
      else if (key === 'class') node.className = props[key];
      else if (key === 'style') node.style.cssText = props[key];
      else node[key] = props[key];
    });
    for (var i = 2; i < arguments.length; i++) {
      var child = arguments[i];
      if (child != null) node.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  window.renderCoverAutomation = function (host, helpers) {
    var api = helpers.api, post = helpers.post, watchJob = helpers.watchJob;
    var toast = helpers.toast, cycleSort = helpers.cycleSort, sortRows = helpers.sortRows;
    host.replaceChildren(h('div', { class: 'loading', text: '读取封面模板中…' }));
    api('/api/cover/overview').then(function (overview) {
      if (!host.isConnected) return;
      host.replaceChildren();
      var form = h('div', { class: 'card' });
      var inputs = {};
      var toolbar = h('div', { class: 'toolbar', style: 'display:flex;flex-wrap:wrap;gap:12px;align-items:center' });
      toolbar.appendChild(h('span', { class: 'small', text: '工作目录：' + (overview.work_dir || '未设置') }));
      Object.keys(overview.fields || {}).forEach(function (name) {
        var spec = overview.fields[name], field;
        if (spec.type === 'bool') {
          field = h('input', { type: 'checkbox', checked: !!state.options[name] });
        } else if (spec.type === 'template') {
          field = h('select', { class: 'select', style: 'min-width:160px;max-width:280px' });
          field.appendChild(h('option', { value: '', text: '请选择封面模板' }));
          (overview.templates || []).forEach(function (item) {
            field.appendChild(h('option', { value: item.key, text: item.name }));
          });
          if (!(overview.templates || []).some(function (item) { return item.key === state.options[name]; })) state.options[name] = '';
          field.value = state.options[name];
        } else {
          field = h('input', { class: 'input', type: 'text', value: state.options[name] || '',
            placeholder: '留空扫描整个工作目录', style: 'min-width:160px;max-width:280px' });
        }
        field.disabled = state.busy;
        inputs[name] = field;
        field.addEventListener('change', function () {
          state.options[name] = spec.type === 'bool' ? field.checked : field.value.trim();
          if (name !== 'template') {
            state.rows = []; state.page = 1; draw();
            message.textContent = '扫描范围已改变，请重新扫描。';
          }
          refreshButtons();
        });
        toolbar.appendChild(h('label', { class: 'small', style: 'display:flex;gap:6px;align-items:center' },
          spec.label, field));
      });
      var scan = h('button', { class: 'btn', type: 'button', text: '扫描预览' });
      var refresh = h('button', { class: 'btn', type: 'button', text: '刷新模板' });
      toolbar.appendChild(scan); toolbar.appendChild(refresh);
      var message = h('div', { class: 'small', style: 'margin-top:12px' });
      if (!overview.has_work_dir) message.textContent = '尚未设置有效工作目录，请先在全局设置中配置。';
      else if (!(overview.templates || []).length) message.textContent = '还没有封面模板，请在桌面端选择底图并保存模板，再刷新。';
      form.appendChild(toolbar); form.appendChild(message);
      form.appendChild(h('div', { class: 'muted small', style: 'margin-top:8px', text: overview.note || '' }));
      host.appendChild(form);

      var card = h('div', { class: 'card' });
      var summary = h('div', { class: 'row-title' });
      var tableHost = h('div', { style: 'margin-top:12px' });
      var footer = h('div', { style: 'display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:12px' });
      var selectAll = h('button', { class: 'btn', type: 'button', text: '全选可用项' });
      var selectNone = h('button', { class: 'btn', type: 'button', text: '取消全选' });
      var previous = h('button', { class: 'btn', type: 'button', text: '上一页' });
      var pageLabel = h('span', { class: 'small' });
      var next = h('button', { class: 'btn', type: 'button', text: '下一页' });
      var fit = h('button', { class: 'btn', type: 'button', text: '自适应' });
      var reset = h('button', { class: 'btn', type: 'button', text: '重置' });
      var start = h('button', { class: 'btn primary', type: 'button', text: '开始补封面', style: 'margin-left:auto' });
      [selectAll, selectNone, previous, pageLabel, next, fit, reset, start].forEach(function (node) { footer.appendChild(node); });
      card.appendChild(summary); card.appendChild(tableHost); card.appendChild(footer); host.appendChild(card);
      var jobHost = h('div'); host.appendChild(jobHost);

      function selectable(row) { return row.status === 'ready' || row.status === 'invalid'; }
      function selected() { return state.rows.filter(function (row) { return selectable(row) && row._selected; }); }
      function refreshButtons() {
        start.disabled = state.busy || !selected().length || !state.options.template;
        scan.disabled = state.busy || !overview.has_work_dir;
        refresh.disabled = state.busy;
        selectAll.disabled = selectNone.disabled = state.busy || !state.rows.length;
        Object.keys(inputs).forEach(function (key) { inputs[key].disabled = state.busy; });
        summary.textContent = '共 ' + state.rows.length + ' 条 · 已选 ' + selected().length + ' 条 · 已有封面自动跳过';
      }

      function draw() {
        tableHost.replaceChildren();
        var count = state.rows.length, pages = Math.max(1, Math.ceil(count / 200));
        state.page = Math.max(1, Math.min(state.page, pages));
        pageLabel.textContent = '第 ' + state.page + ' / ' + pages + ' 页 · 共 ' + count + ' 条';
        previous.disabled = state.page <= 1; next.disabled = state.page >= pages;
        if (!count) {
          tableHost.appendChild(h('div', { class: 'empty', text: '还没有扫描结果，点「扫描预览」识别本地视频标题和日期。' }));
          refreshButtons(); return;
        }
        var columns = [{ key: 'video', label: '视频' }, { key: 'title', label: '标题' },
          { key: 'date', label: '日期' }, { key: 'status', label: '状态' }];
        var table = h('table', { class: 'table', style: 'width:100%;min-width:0;max-width:100%;table-layout:fixed' });
        var tr = h('tr'); tr.appendChild(h('th', { style: 'width:36px', text: '选' }));
        columns.forEach(function (column, index) {
          var th = h('th', { class: 'sortable', style: 'width:' + [27, 29, 22, 22][index] + '%;cursor:pointer',
            text: column.label + (state.order === column.key ? (state.asc ? ' ↑' : ' ↓') : '') });
          th.addEventListener('click', function () { cycleSort(state, column.key); state.page = 1; draw(); });
          tr.appendChild(th);
        });
        table.appendChild(h('thead', {}, tr));
        var body = h('tbody');
        sortRows(state.rows, columns, state).slice((state.page - 1) * 200, state.page * 200).forEach(function (row) {
          var line = h('tr');
          var check = h('input', { type: 'checkbox', checked: !!row._selected, disabled: state.busy || !selectable(row), title: '选择视频' });
          check.addEventListener('change', function () { row._selected = check.checked; refreshButtons(); });
          line.appendChild(h('td', {}, check));
          line.appendChild(h('td', { style: 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap', title: row.video, text: row.video }));
          var reason = h('span', { title: row.reason || '' });
          function showStatus() {
            var labels = { ready: '待生成', exists: '已跳过', invalid: '需修正', conflict: '已跳过',
              generated: '已完成', skipped: '已跳过', failed: '失败' };
            reason.textContent = row._edited ? '已修正，开始时校验' : (labels[row.status] || row.status) + (row.reason ? '：' + row.reason : '');
          }
          ['title', 'date'].forEach(function (key) {
            var input = h('input', { class: 'input', type: 'text', value: row[key] || '',
              style: 'width:100%;min-width:0', disabled: state.busy || !selectable(row),
              title: key === 'date' ? 'YYYY-MM-DD 或 YYYY-MM-DD~YYYY-MM-DD；留空不显示日期' : '封面标题' });
            input.addEventListener('input', function () {
              row[key] = input.value; row._edited = true; row._selected = true;
              check.checked = true; showStatus(); refreshButtons();
            });
            line.appendChild(h('td', {}, input));
          });
          showStatus(); line.appendChild(h('td', { style: 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap' }, reason));
          body.appendChild(line);
        });
        table.appendChild(body); tableHost.appendChild(table); refreshButtons();
      }

      function loadRows() {
        state.busy = true; refreshButtons();
        message.textContent = '扫描中…';
        return post('/api/cover/preview', state.options).then(function (data) {
          state.rows = (data.rows || []).map(function (row) { row._selected = row.status === 'ready'; return row; });
          state.page = 1;
          message.textContent = '可生成 ' + data.ready + ' 条 · 已有封面 ' + data.exists + ' 条 · 需检查 ' + data.invalid + ' 条。标题和日期可直接修改。';
        }).catch(function (error) {
          message.textContent = '扫描失败：' + error.message + '，请检查目录后重试。';
        }).finally(function () { state.busy = false; draw(); });
      }
      scan.addEventListener('click', loadRows);
      refresh.addEventListener('click', function () { window.renderCoverAutomation(host, helpers); });
      selectAll.addEventListener('click', function () { state.rows.forEach(function (row) { row._selected = row.status === 'ready' || !!row._edited; }); draw(); });
      selectNone.addEventListener('click', function () { state.rows.forEach(function (row) { row._selected = false; }); draw(); });
      previous.addEventListener('click', function () { state.page--; draw(); });
      next.addEventListener('click', function () { state.page++; draw(); });
      fit.addEventListener('click', function () { var table = tableHost.querySelector('table'); if (table) table.style.tableLayout = 'auto'; });
      reset.addEventListener('click', draw);
      start.addEventListener('click', function () {
        var rows = selected().map(function (row) { return { video: row.video, title: row.title, date: row.date }; });
        if (!rows.length || !window.confirm('为选中的 ' + rows.length + ' 条视频补齐同名 JPG 封面？\n封面写入视频同目录；已有封面会跳过。')) return;
        state.busy = true; draw(); message.textContent = '';
        post('/api/cover/generate', Object.assign({}, state.options, { rows: rows, confirmed: true })).then(function (data) {
          state.job = data.job; followJob();
        }).catch(function (error) {
          state.busy = false; draw(); message.textContent = '补封面失败：' + error.message + '，请修改后重试。';
        });
      });
      function followJob() {
        watchJob(jobHost, state.job, function (job) {
          var result = job.result || {};
          if (result.cancelled) {
            var label = jobHost.querySelector('.job-status'); if (label) label.textContent = '已取消';
          }
          message.textContent = result.summary || '已完成，封面位于视频同目录。';
          (result.items || []).forEach(function (item) {
            var row = state.rows.find(function (candidate) { return candidate.video === item.video; });
            if (!row) return;
            row._selected = false; row._edited = false; row.reason = item.reason || '';
            row.status = item.status === 'failed' ? 'invalid' : item.status;
          });
        }, { cancelable: true, onSettled: function () { state.busy = false; state.job = ''; draw(); } });
      }
      draw();
      if (state.busy && state.job) followJob();
    }).catch(function (error) {
      host.replaceChildren(h('div', { class: 'error', text: '读取封面模板失败：' + error.message }),
        h('button', { class: 'btn', type: 'button', text: '重试', onclick: function () { window.renderCoverAutomation(host, helpers); } }));
    });
  };
}());
