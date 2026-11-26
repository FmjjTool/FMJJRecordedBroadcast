(function () {
  'use strict';
  var statusNames = { running: '运行中快照', stopped: '已停止', normal_exit: '正常退出',
    startup_failure: '启动失败', uncaught_exception: '出现错误', process_exit: '进程结束' };

  window.PerformanceReports = {
    mount: function (host, helpers) {
      var h = helpers.h, api = helpers.api, withToken = helpers.withToken;
      var cycleSort = helpers.cycleSort, sortRows = helpers.sortRows;
      var rows = [], selected = '', disposed = false;
      var listOrder = { order: '', asc: true }, phaseOrder = { order: '', asc: true };
      var list = h('div'), detail = h('div');
      var refresh = h('button', { class: 'btn', type: 'button' }, '刷新报告');
      var state = h('div', { class: 'muted small', role: 'status' });
      host.appendChild(h('div', { class: 'card', style: 'margin-top:12px' },
        h('div', { class: 'toolbar' }, h('span', { class: 'row-title' }, '已保存报告'),
          h('span', { style: 'flex:1' }), refresh),
        h('div', { class: 'muted small', style: 'margin:8px 0' },
          '每 15 秒保存，停止与正常退出时保存；保留最近 20 份。状态为保存时的状态，强杀或断电仅保留上次成功记录。'),
        state, list, detail));

      function timeText(value) { return value ? String(value).replace('T', ' ').slice(0, 19) : '—'; }
      function mb(value) { return value == null ? '—' : (value / 1048576).toFixed(1); }
      function live() { return !disposed && host.isConnected; }

      function table(columns, data, order, renderer) {
        var area = h('div', { style: 'position:relative' });
        var grid = h('table', { class: 'db-table', style: 'width:100%;min-width:0;table-layout:fixed' });
        var group = h('colgroup');
        columns.forEach(function (column) { group.appendChild(h('col', { style: 'width:' + column.width + '%' })); });
        var head = h('thead'), body = h('tbody');
        grid.appendChild(group); grid.appendChild(head); grid.appendChild(body);
        function paint() {
          var header = h('tr');
          columns.forEach(function (column) {
            var label = column.label + (order.order === column.key ? (order.asc ? ' ↑' : ' ↓') : '');
            var cell = h('th', { class: column.key ? 'sortable' : '', title: column.label,
              style: 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap' }, label);
            if (column.key) cell.addEventListener('click', function () { cycleSort(order, column.key); paint(); });
            header.appendChild(cell);
          });
          head.replaceChildren(header);
          body.replaceChildren();
          sortRows(data, columns, order).forEach(function (row) {
            var line = h('tr');
            columns.forEach(function (column) {
              var cell = h('td', { style: 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap' });
              renderer(cell, row, column);
              line.appendChild(cell);
            });
            body.appendChild(line);
          });
        }
        function widths(content) {
          var weights = columns.map(function (column) {
            if (!content || !column.key) return column.width;
            var longest = column.label.length;
            data.forEach(function (row) { longest = Math.max(longest, String(row[column.key] == null ? '' : row[column.key]).length); });
            return Math.min(30, Math.max(8, longest));
          });
          var total = weights.reduce(function (sum, value) { return sum + value; }, 0);
          Array.from(group.children).forEach(function (col, index) { col.style.width = weights[index] * 100 / total + '%'; });
        }
        head.addEventListener('contextmenu', function (event) {
          event.preventDefault();
          var old = area.querySelector('[role=menu]');
          if (old) old.remove();
          var menu = h('div', { role: 'menu', class: 'card',
            style: 'position:fixed;z-index:30;padding:8px;display:grid;gap:4px;left:'
              + Math.max(0, Math.min(event.clientX, window.innerWidth - 190)) + 'px;top:'
              + Math.max(0, Math.min(event.clientY, window.innerHeight - 110)) + 'px' });
          [['列宽自适应', true], ['重置列宽', false]].forEach(function (action) {
            menu.appendChild(h('button', { class: 'btn mini', type: 'button', role: 'menuitem', onclick: function () {
              widths(action[1]); menu.remove();
            } }, action[0]));
          });
          area.appendChild(menu);
          document.addEventListener('pointerdown', function () { menu.remove(); }, { once: true });
        });
        paint();
        area.appendChild(h('div', { class: 'table-wrap', style: 'overflow-x:hidden' }, grid));
        area.appendChild(h('div', { class: 'toolbar', style: 'justify-content:flex-end;margin-top:8px' },
          h('button', { class: 'btn mini', type: 'button', onclick: function () { widths(true); } }, '自适应'),
          h('button', { class: 'btn mini', type: 'button', onclick: function () { widths(false); } }, '重置')));
        return area;
      }

      function showReport(reportId) {
        selected = reportId;
        detail.replaceChildren(h('div', { class: 'muted small', role: 'status' }, '正在读取报告…'));
        api('/api/performance/reports/detail?id=' + encodeURIComponent(reportId)).then(function (report) {
          if (!live() || selected !== reportId) return;
          var startup = report.startup || {}, metadata = report.autosave || {};
          detail.replaceChildren(h('div', { class: 'toolbar', style: 'margin-top:12px' },
            h('span', { class: 'row-title' }, '启动阶段'), h('span', { style: 'flex:1' }),
            h('a', { class: 'btn mini', href: withToken('/api/performance/reports/download?id=' + encodeURIComponent(reportId)) }, '下载 JSON')));
          detail.appendChild(h('div', { class: 'muted small', style: 'margin:8px 0' },
            '保存时间 ' + timeText(metadata.saved_at) + ' · '
            + (startup.complete ? '启动与预载已完成' : '未记录到启动与预载完成')
            + '。起点为 Python 入口；阶段可能嵌套或并发，耗时与内存变化不能直接相加。'));
          var phases = startup.phases || [];
          if (!phases.length) {
            detail.appendChild(h('div', { class: 'muted small' }, '此报告没有启动阶段数据；开启自动监控后，下次启动可记录。'));
            return;
          }
          var columns = [{ key: 'name', label: '启动阶段', width: 32 }, { key: 'count', label: '次数', width: 9 },
            { key: 'total_ms', label: '耗时 ms', width: 16 }, { key: 'rss_delta', label: '工作集 MB', width: 16 },
            { key: 'private_delta', label: '私有提交 MB', width: 17 }, { key: 'failures', label: '失败数', width: 10 }];
          detail.appendChild(table(columns, phases, phaseOrder, function (cell, row, column) {
            var value = column.key === 'rss_delta' || column.key === 'private_delta' ? mb(row[column.key]) : row[column.key];
            cell.textContent = value == null ? '—' : String(value);
            cell.title = cell.textContent;
          }));
          var pending = startup.pending || [];
          if (pending.length) detail.appendChild(h('div', { class: 'muted small' },
            '保存时未完成的阶段：' + pending.map(function (item) { return item.name; }).join('、')));
        }).catch(function () {
          if (live() && selected === reportId) detail.replaceChildren(
            h('div', { class: 'muted small' }, '读取报告失败，记录可能已轮转，请刷新后重试'),
            h('button', { class: 'btn mini', type: 'button', onclick: function () { showReport(reportId); } }, '重试'));
        });
      }

      function paintList() {
        list.replaceChildren();
        if (!rows.length) {
          list.appendChild(h('div', { class: 'muted small', style: 'padding:12px 0' },
            '还没有已保存报告，开启桌面性能检测后可在此查看。'));
          return;
        }
        var columns = [{ key: 'started_at', label: '开始时间', width: 28 },
          { key: 'status', label: '状态', width: 17 }, { key: 'samples', label: '采样', width: 10 },
          { key: 'startup_complete', bool: true, label: '启动完成', width: 14 }, { label: '操作', width: 31 }];
        list.appendChild(table(columns, rows, listOrder, function (cell, row, column) {
          if (!column.key) {
            cell.appendChild(h('button', { class: 'btn mini', type: 'button', onclick: function () { showReport(row.id); } }, '查看'));
            cell.appendChild(h('a', { class: 'btn mini', style: 'margin-left:4px',
              href: withToken('/api/performance/reports/download?id=' + encodeURIComponent(row.id)) }, '下载'));
          } else {
            cell.textContent = column.key === 'started_at' ? timeText(row.started_at)
              : column.key === 'status' ? (statusNames[row.status] || '已保存')
              : column.key === 'startup_complete' ? (row.startup_complete ? '是' : '否') : String(row[column.key]);
          }
          cell.title = row.filename + ' · ' + cell.textContent;
        }));
      }

      function load() {
        refresh.disabled = true;
        state.textContent = '正在读取已保存报告…';
        return api('/api/performance/reports').then(function (result) {
          if (!live()) return;
          rows = result.reports || [];
          state.textContent = '共 ' + rows.length + ' 份 · ' + (result.directory_label || 'logs/performance');
          paintList();
        }).catch(function () {
          if (live()) state.textContent = '读取报告列表失败，请点刷新报告重试';
        }).finally(function () { if (live()) refresh.disabled = false; });
      }
      refresh.addEventListener('click', load);
      load();
      return { refresh: load, dispose: function () { disposed = true; } };
    }
  };
}());
