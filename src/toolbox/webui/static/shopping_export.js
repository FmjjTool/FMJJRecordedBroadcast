(function () {
  'use strict';

  window.createShoppingResultsView = function (options) {
    var h = options.h;
    var outcome = options.outcome;
    var resultId = outcome.export_available ? outcome.export_result_id || '' : '';
    var expiresAt = resultId ? Date.now() + (Number(outcome.expires_in) || 1800) * 1000 : 0;
    var exporting = false;
    var order = { order: '', asc: true };
    function numberKey(value) {
      var match = String(value || '').replace(/,/g, '').match(/^(?:[¥￥]\s*)?(\d+(?:\.\d+)?)\s*([万亿kw]?)/i);
      if (!match) return null;
      var scale = { '万': 10000, '亿': 100000000, k: 1000, w: 10000 };
      return Number(match[1]) * (scale[(match[2] || '').toLowerCase()] || 1);
    }
    var rows = (outcome.items || []).map(function (item) {
      return Object.assign({}, item, { _price: numberKey(item.price), _sales: numberKey(item.sales) });
    });
    var columns = [
      { key: 'title', label: '商品', width: 52 },
      { key: '_price', label: '价格', width: 18 },
      { key: '_sales', label: '销量', width: 16 },
      { key: 'url', label: '链接', width: 14 }
    ];
    var widths = columns.map(function (column) { return column.width; });
    var table = h('table', { class: 'db-table fg-table', 'aria-label': '购物解析结果' });
    var tableHost = h('div');
    var notice = h('div', { class: 'small', role: 'status', style: 'margin-top:6px' });
    var exportBtn = h('button', { class: 'btn', type: 'button', id: 'shopping-export-xlsx' }, '导出 Excel');
    var supported = outcome.platform === 'douyin' || outcome.platform === 'bili';
    var footer = h('div', { class: 'toolbar', style: 'margin-top:8px' },
      h('span', { class: 'muted small' }, '本次结果 ' + rows.length + ' 件'),
      h('span', { style: 'flex:1' }));
    var element = h('div', null,
      h('div', { class: 'muted small', style: 'margin-bottom:6px' }, outcome.selection_note || ''),
      tableHost, footer, notice);

    function setWidths() {
      var total = widths.reduce(function (sum, value) { return sum + value; }, 0);
      Array.from(table.querySelectorAll('thead th')).forEach(function (header, index) {
        header.style.width = (widths[index] / total * 100) + '%';
      });
    }
    function fit() {
      var canvas = document.createElement('canvas');
      var context = canvas.getContext('2d');
      context.font = getComputedStyle(table).font;
      widths = columns.map(function (column) {
        var longest = context.measureText(column.label).width;
        rows.forEach(function (item) {
          var value = column.key === '_price' ? item.price
            : (column.key === '_sales' ? item.sales : (column.key === 'url' ? '打开' : item.title));
          longest = Math.max(longest, context.measureText(String(value || '')).width);
        });
        return Math.max(52, Math.min(360, longest + 24));
      });
      setWidths();
    }
    function reset() { widths = columns.map(function (column) { return column.width; }); setWidths(); }
    function safeLink(value) {
      try {
        var url = new URL(value);
        return /^(https?:)$/.test(url.protocol) && !url.username && !url.password ? url.href : '';
      } catch (_error) { return ''; }
    }
    function draw() {
      var head = h('tr');
      columns.forEach(function (column) {
        var active = order.order === column.key;
        function change() { options.cycleSort(order, column.key); draw(); sync(); }
        var header = h('th', { class: 'sortable', tabindex: '0',
          'aria-sort': active ? (order.asc ? 'ascending' : 'descending') : 'none',
          onclick: change }, column.label + (active ? (order.asc ? ' ↑' : ' ↓') : ''));
        header.addEventListener('keydown', function (event) {
          if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); change(); }
        });
        head.appendChild(header);
      });
      var body = h('tbody');
      options.sortRows(rows, columns, order).forEach(function (item) {
        var link = safeLink(item.url);
        body.appendChild(h('tr', null,
          h('td', { title: item.title || '' }, item.title || '-'),
          h('td', { title: item.price || '' }, item.price || '-'),
          h('td', { class: 'num', title: item.sales || '' }, item.sales || '-'),
          h('td', null, link ? h('a', { href: link, target: '_blank',
            rel: 'noopener noreferrer', 'aria-label': '打开商品 ' + (item.title || '') }, '打开') : '-')));
      });
      table.replaceChildren(h('thead', null, head), body);
      setWidths();
    }
    function sync() {
      if (resultId && expiresAt && Date.now() >= expiresAt) {
        resultId = '';
        notice.textContent = '解析结果已过期，请重新解析后导出。';
      }
      exportBtn.disabled = options.isBusy() || !resultId || !rows.length;
      exportBtn.textContent = exporting ? '导出中…' : '导出 Excel';
    }
    function invalidate(message) {
      if (!supported) return;
      resultId = '';
      notice.textContent = message || '输入或平台范围已变化，请重新解析后导出。';
      sync();
    }
    footer.appendChild(h('button', { class: 'btn mini', type: 'button', onclick: fit }, '自适应'));
    footer.appendChild(h('button', { class: 'btn mini', type: 'button', onclick: reset }, '重置'));
    if (supported) {
      footer.appendChild(exportBtn);
      notice.textContent = resultId ? '导出本次全部结果，完成后点击「下载结果」保存 Excel。'
        : (outcome.export_message || '本次结果暂不能导出，请重新解析后重试。');
    } else {
      notice.textContent = outcome.note || '';
    }
    table.addEventListener('contextmenu', function (event) {
      if (!event.target.closest('th')) return;
      event.preventDefault();
      var old = tableHost.querySelector('[role="menu"]'); if (old) old.remove();
      var menu = h('div', { class: 'fg-column-menu', role: 'menu' });
      [['列宽自适应', fit], ['重置列宽', reset]].forEach(function (action) {
        menu.appendChild(h('button', { class: 'btn', type: 'button', role: 'menuitem',
          onclick: function () { action[1](); menu.remove(); } }, action[0]));
      });
      menu.style.left = Math.max(8, Math.min(event.clientX, innerWidth - 180)) + 'px';
      menu.style.top = Math.max(8, Math.min(event.clientY, innerHeight - 90)) + 'px';
      tableHost.appendChild(menu); menu.firstChild.focus();
      menu.addEventListener('keydown', function (keyEvent) { if (keyEvent.key === 'Escape') menu.remove(); });
      menu.addEventListener('focusout', function (focusEvent) {
        if (!menu.contains(focusEvent.relatedTarget)) setTimeout(function () {
          if (menu.isConnected && !menu.contains(document.activeElement)) menu.remove();
        }, 0);
      });
    });
    exportBtn.addEventListener('click', function () {
      sync();
      if (exportBtn.disabled) return;
      if (!window.confirm('导出本次解析的 ' + rows.length + ' 件商品为 Excel？\n\n使用本次完整解析结果，不重新抓取商品。')) return;
      exporting = true;
      options.setBusy(true);
      notice.textContent = '正在生成 Excel…';
      sync();
      options.post('/api/tools/shopping/export', { result_id: resultId }).then(function (response) {
        options.watchJob(options.jobHost, response.job, function (job) {
          notice.textContent = (job.result || {}).summary || 'Excel 已生成，点击「下载结果」保存。';
        }, {
          cancelable: true, indeterminate: true, clearPollErrorOnSuccess: true,
          onSettled: function (job) {
            exporting = false; options.setBusy(false);
            if (job.status === 'error') {
              notice.textContent = '导出失败：' + (job.error || '请重试');
              options.toast(notice.textContent, 'error');
            }
            if (job.status === 'cancelled') notice.textContent = '已取消导出，可使用本次解析结果重试。';
            sync();
          },
          onPollError: function (_error, retry) { setTimeout(retry, 1000); }
        });
      }).catch(function (error) {
        exporting = false; options.setBusy(false);
        notice.textContent = '导出失败：' + error.message;
        options.toast(notice.textContent, 'error'); sync();
      });
    });
    draw();
    tableHost.appendChild(rows.length ? h('div', { class: 'table-wrap' }, table)
      : h('div', { class: 'empty' }, '没有解析到商品，请检查链接后重试。'));
    sync();
    return { element: element, sync: sync, invalidate: invalidate };
  };
})();
