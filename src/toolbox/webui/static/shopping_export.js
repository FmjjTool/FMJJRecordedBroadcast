(function () {
  'use strict';

  window.createShoppingResultsView = function (options) {
    var h = options.h;
    var outcome = options.outcome;
    var taobao = outcome.platform === 'taobao' && outcome.kind === 'shop';
    var resultId = taobao ? (outcome.taobao_available ? outcome.taobao_result_id || '' : '')
      : (outcome.export_available ? outcome.export_result_id || '' : '');
    var expiresAt = resultId ? Date.now() + (Number(taobao ? outcome.taobao_expires_in : outcome.expires_in) || 1800) * 1000 : 0;
    var exporting = false;
    var order = { order: '', asc: true };
    var selected = new Set();
    var boxes = [];
    var page = 1;
    var pageSize = 200;
    var selectionLimit = 30;
    var exportFormat = 'excel';
    function numberKey(value) {
      var match = String(value || '').replace(/,/g, '').match(/^(?:[¥￥]\s*)?(\d+(?:\.\d+)?)\s*([万亿kw]?)/i);
      if (!match) return null;
      var scale = { '万': 10000, '亿': 100000000, k: 1000, w: 10000 };
      return Number(match[1]) * (scale[(match[2] || '').toLowerCase()] || 1);
    }
    var rows = (outcome.items || []).map(function (item) {
      return Object.assign({}, item, { item_id: String(item.item_id || ''),
        _price: numberKey(item.price), _sales: numberKey(item.sales), _status: '未开始', _statusNote: '' });
    });
    var columns = [
      { key: 'title', label: '商品', width: 52 },
      { key: '_price', label: '价格', width: 18, minWidth: 78 },
      { key: '_sales', label: '销量', width: 16, minWidth: 66 },
      { key: 'url', label: '链接', width: 14, minWidth: 60 }
    ];
    if (taobao) {
      columns[0].width = 42;
      columns[1].minWidth = 60; columns[2].minWidth = 54; columns[3].minWidth = 50;
      columns.push({ key: '_status', label: '详情导出', width: 20, minWidth: 78 });
    }
    var widths = columns.map(function (column) { return column.width; });
    var table = h('table', { class: 'db-table fg-table', 'aria-label': '购物解析结果' });
    var tableHost = h('div');
    var notice = h('div', { class: 'small', role: 'status', style: 'margin-top:6px' });
    var exportLabel = taobao ? '抓取详情并导出' : '导出 Excel';
    var exportBtn = h('button', { class: 'btn', type: 'button',
      id: taobao ? 'shopping-taobao-export' : 'shopping-export-xlsx' }, exportLabel);
    var jsonExportBtn = taobao ? h('button', { class: 'btn mini', type: 'button',
      id: 'shopping-taobao-json-export' }, '导出完整 JSON') : null;
    var supported = taobao || outcome.platform === 'douyin' || outcome.platform === 'bili';
    var selectionCount = h('span', { class: 'muted small', role: 'status', 'aria-live': 'polite' });
    var allBtn = h('button', { class: 'btn mini', type: 'button', onclick: function () {
      if (allBtn.disabled) return;
      rows.forEach(function (item) { if (item.item_id) selected.add(item.item_id); });
      draw(); sync();
    } }, '全选');
    var clearBtn = h('button', { class: 'btn mini', type: 'button', onclick: function () {
      if (clearBtn.disabled) return;
      selected.clear(); draw(); sync();
    } }, '清除');
    var selectionBar = taobao ? h('div', { class: 'toolbar', style: 'margin-bottom:8px' },
      allBtn, clearBtn, selectionCount) : null;
    var pagination = h('div', { class: 'toolbar', style: 'justify-content:flex-end;margin-top:8px' });
    var footer = h('div', { class: 'toolbar', style: 'margin-top:8px' },
      h('span', { class: 'muted small' }, '本次结果 ' + rows.length + ' 件'),
      h('span', { style: 'flex:1' }));
    var element = h('div', null,
      h('div', { class: 'muted small', style: 'margin-bottom:6px' }, outcome.selection_note || ''),
      selectionBar, tableHost, taobao ? pagination : null, footer, notice);

    function setWidths() {
      var total = widths.reduce(function (sum, value) { return sum + value; }, 0);
      var available = Math.max(280, (tableHost.clientWidth || 400) - (taobao ? 36 : 0));
      var sizes = widths.map(function (value, index) {
        return Math.max(columns[index].minWidth || 0, Math.floor(available * value / total));
      });
      sizes[0] = available - sizes.slice(1).reduce(function (sum, value) { return sum + value; }, 0);
      Array.from(table.querySelectorAll('thead th')).forEach(function (header, index) {
        header.style.width = (taobao && index === 0 ? 36 : sizes[index - (taobao ? 1 : 0)]) + 'px';
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
            : (column.key === '_sales' ? item.sales : (column.key === 'url' ? '打开'
              : (column.key === '_status' ? item._status : item.title)));
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
      boxes = [];
      if (taobao) head.appendChild(h('th', { 'aria-label': '选择商品', style: 'width:36px;padding:8px' }));
      columns.forEach(function (column) {
        var active = order.order === column.key;
        function change() { options.cycleSort(order, column.key); page = 1; draw(); sync(); }
        var header = h('th', { class: 'sortable', tabindex: '0',
          'aria-sort': active ? (order.asc ? 'ascending' : 'descending') : 'none',
          onclick: change }, column.label + (active ? (order.asc ? ' ↑' : ' ↓') : ''));
        header.addEventListener('keydown', function (event) {
          if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); change(); }
        });
        head.appendChild(header);
      });
      var body = h('tbody');
      var visible = options.sortRows(rows, columns, order);
      if (taobao) visible = visible.slice((page - 1) * pageSize, page * pageSize);
      visible.forEach(function (item) {
        var link = safeLink(item.url);
        var tr = h('tr', { class: taobao && selected.has(item.item_id) ? 'selected' : '' });
        if (taobao) {
          var box = h('input', { type: 'checkbox', checked: selected.has(item.item_id),
            'aria-label': '选择商品 ' + item.item_id, onchange: function () {
              if (options.isBusy() || !resultId) { box.checked = selected.has(item.item_id); return; }
              if (box.checked && selected.size >= selectionLimit) {
                box.checked = false;
                notice.textContent = '每批最多选择 30 件商品，请减少勾选后重试。';
                return;
              }
              if (box.checked) selected.add(item.item_id); else selected.delete(item.item_id);
              tr.classList.toggle('selected', box.checked); sync();
            } });
          boxes.push({ box: box, id: item.item_id });
          tr.appendChild(h('td', { style: 'padding:8px' }, box));
        }
        tr.append(
          h('td', { title: item.title || '' }, item.title || '-'),
          h('td', { title: item.price || '' }, item.price || '-'),
          h('td', { class: 'num', title: item.sales || '' }, item.sales || '-'),
          h('td', null, link ? h('a', { href: link, target: '_blank',
            rel: 'noopener noreferrer', 'aria-label': '打开商品 ' + (item.title || '') }, '打开') : '-'));
        if (taobao) tr.appendChild(h('td', { title: item._statusNote || item._status }, item._status));
        body.appendChild(tr);
      });
      table.replaceChildren(h('thead', null, head), body);
      if (taobao) drawPagination();
      setWidths();
    }
    function drawPagination() {
      pagination.replaceChildren();
      if (rows.length <= pageSize) return;
      var pages = Math.ceil(rows.length / pageSize);
      pagination.appendChild(h('span', { class: 'muted small' },
        '第 ' + page + ' / ' + pages + ' 页 · 共 ' + rows.length + ' 条'));
      [['首页', 1], ['上一页', page - 1], ['下一页', page + 1], ['末页', pages]].forEach(function (entry) {
        var disabled = options.isBusy() || entry[1] < 1 || entry[1] > pages || entry[1] === page;
        pagination.appendChild(h('button', { class: 'btn mini', type: 'button', disabled: disabled,
          onclick: function () { if (disabled) return; page = entry[1]; draw(); sync(); } }, entry[0]));
      });
    }
    function showBatchResult(result) {
      var statusLabels = { completed: '已完成', failed: '失败', skipped: '已跳过' };
      var updates = new Map((result.rows || []).map(function (item) { return [String(item.item_id), item]; }));
      rows.forEach(function (item) {
        var update = updates.get(item.item_id);
        if (!update) return;
        item._status = statusLabels[update.status] || '未开始';
        item._statusNote = update.message || item._status;
      });
      draw();
    }
    function sync() {
      if (resultId && expiresAt && Date.now() >= expiresAt) {
        resultId = '';
        notice.textContent = '解析结果已过期，请重新解析后导出。';
      }
      var busy = options.isBusy();
      exportBtn.disabled = busy || !resultId || !rows.length || (taobao && !selected.size);
      exportBtn.textContent = exporting && exportFormat === 'excel'
        ? (taobao ? '抓取并导出中…' : '导出中…') : exportLabel;
      if (taobao) {
        jsonExportBtn.disabled = busy || !resultId || !rows.length || !selected.size;
        jsonExportBtn.textContent = exporting && exportFormat === 'json' ? '抓取并导出 JSON 中…' : '导出完整 JSON';
        allBtn.disabled = busy || !resultId || !rows.length || rows.length > selectionLimit;
        allBtn.title = rows.length > selectionLimit ? '每批最多选择 30 件，请逐件勾选' : '选择本次全部商品';
        clearBtn.disabled = busy || !selected.size;
        selectionCount.textContent = '已选 ' + selected.size + ' / 30 件 · 共 ' + rows.length + ' 件';
        boxes.forEach(function (entry) {
          entry.box.checked = selected.has(entry.id);
          entry.box.disabled = busy || !resultId || !entry.id;
        });
        drawPagination();
      }
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
      if (jsonExportBtn) footer.appendChild(jsonExportBtn);
      notice.textContent = taobao ? (outcome.taobao_message || '本次商品列表暂不能导出，请重新解析后重试。')
        : (resultId ? '导出本次全部结果，完成后点击「下载结果」保存 Excel。'
          : (outcome.export_message || '本次结果暂不能导出，请重新解析后重试。'));
      if (taobao && resultId) notice.textContent += ' 每批最多 30 件；可导出逐件 Excel ZIP 或完整详情 JSON。';
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
    function submitExport(format) {
      sync();
      var button = format === 'json' ? jsonExportBtn : exportBtn;
      if (!button || button.disabled) return;
      var confirmText = taobao
        ? '抓取所选 ' + selected.size + ' 件淘宝商品的详情与图片，并逐件导出 Excel？\n\n结果保存到「淘宝商品」导出目录，浏览器可下载 ZIP。取消将在当前详情请求结束后停止后续商品，已完成文件保留。'
        : '导出本次解析的 ' + rows.length + ' 件商品为 Excel？\n\n使用本次完整解析结果，不重新抓取商品。';
      if (format === 'json') confirmText = '抓取所选 ' + selected.size + ' 件淘宝商品的完整详情并导出 JSON？JSON 将作为浏览器附件下载，保留公开图片链接，不下载图片文件或包含服务器本地路径。取消将在当前详情请求结束后停止后续商品。';
      if (!window.confirm(confirmText)) return;
      exporting = true;
      exportFormat = format;
      options.setBusy(true);
      notice.textContent = taobao ? '正在提交商品详情任务…' : '正在生成 Excel…';
      sync();
      var payload = { result_id: resultId };
      if (taobao) payload.item_ids = Array.from(selected);
      var endpoint = taobao
        ? (format === 'json' ? '/api/tools/shopping/taobao/export-json' : '/api/tools/shopping/taobao/export')
        : '/api/tools/shopping/export';
      options.post(endpoint, payload).then(function (response) {
        options.watchJob(options.jobHost, response.job, function (job) {
          var result = job.result || {};
          notice.textContent = result.message || result.summary || 'Excel 已生成，点击「下载结果」保存。';
        }, {
          cancelable: true, indeterminate: true, clearPollErrorOnSuccess: true,
          onSettled: function (job) {
            exporting = false; options.setBusy(false);
            if (taobao && job.result) {
              showBatchResult(job.result);
              if (exportFormat === 'excel' && job.result.succeeded && options.onExported) options.onExported();
            }
            if (job.status === 'error') {
              notice.textContent = '导出失败：' + (job.error || '请重试');
            }
            if (job.status === 'cancelled') notice.textContent = (job.result || {}).message
              || '已取消导出，可使用本次解析结果重试。';
            sync();
          },
          onPollError: function (_error, retry) { setTimeout(retry, 1000); }
        });
      }).catch(function (error) {
        exporting = false; options.setBusy(false);
        notice.textContent = '导出失败：' + error.message;
        sync();
      });
    }
    exportBtn.addEventListener('click', function () { submitExport('excel'); });
    if (jsonExportBtn) jsonExportBtn.addEventListener('click', function () { submitExport('json'); });
    draw();
    tableHost.appendChild(rows.length ? h('div', { class: 'table-wrap' }, table)
      : h('div', { class: 'empty' }, '没有解析到商品，请检查链接后重试。'));
    var resizeObserver = new ResizeObserver(function () {
      if (element.isConnected) setWidths(); else resizeObserver.disconnect();
    });
    resizeObserver.observe(tableHost);
    sync();
    return { element: element, sync: sync, invalidate: invalidate };
  };
})();
