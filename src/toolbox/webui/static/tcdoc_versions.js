(function () {
  'use strict';

  // Same immutable document versions as desktop; only the preview lives here.
  window.renderTcdocVersions = function (host, helpers, options) {
    var h = helpers.h, api = helpers.api, withToken = helpers.withToken;
    var cycleSort = helpers.cycleSort, tableRequest = 0;
    var docKey = options.doc_key, request = 0, versions = [], opened = null, busy = false;
    var page = 1, order = { order: '', asc: true };
    var search = h('input', { class: 'input', type: 'search',
      placeholder: '按采集时间或版本号搜索', 'aria-label': '搜索采集版本' });
    var select = h('select', { class: 'select', style: 'flex:1;min-width:160px;max-width:100%',
      'aria-label': '采集版本' });
    var open = h('button', { class: 'btn primary', type: 'button', disabled: true }, '打开版本');
    var refresh = h('button', { class: 'btn', type: 'button' }, '刷新');
    var latest = h('button', { class: 'btn', type: 'button' }, '返回最新');
    var note = h('div', { class: 'small', style: 'margin:8px 0;overflow-wrap:anywhere',
      role: 'status' });
    var content = h('div');
    host.replaceChildren(h('div', { class: 'card' },
      h('div', { class: 'toolbar', style: 'flex-wrap:wrap' },
        h('span', null, '采集版本'), search, select, refresh, latest, open), note, content));

    function active(serial) { return host.isConnected && serial === request; }
    function imageUrl(assetId) {
      return withToken('/api/tcdoc/versions/image?doc_key=' + encodeURIComponent(docKey)
        + '&version_id=' + encodeURIComponent(opened.meta.version_id)
        + '&asset_id=' + encodeURIComponent(assetId));
    }
    function imageList(images) {
      var box = h('div');
      (images || []).forEach(function (item) {
        var caption = item.name || item.field || '留存图片';
        if (!item.asset_id || item.status !== 'downloaded') {
          box.appendChild(h('p', { class: 'muted small' }, caption + '：该次采集未留存图片'));
          return;
        }
        var url = imageUrl(item.asset_id);
        var img = h('img', { src: url, alt: caption, loading: 'lazy',
          width: item.width || 640, height: item.height || 360,
          style: 'display:block;max-width:100%;height:auto;max-height:600px;object-fit:contain' });
        img.addEventListener('error', function () {
          img.replaceWith(h('p', { class: 'small' }, caption + '：读取失败，请重新打开版本'));
        }, { once: true });
        box.appendChild(h('div', { style: 'margin:8px 0' },
          h('a', { href: url, target: '_blank', rel: 'noopener noreferrer' }, caption + ' · 打开原图'), img));
      });
      return box;
    }
    function drawTable(sheet, area) {
      var serial = ++tableRequest, versionId = opened.meta.version_id;
      area.replaceChildren(h('p', { class: 'small', role: 'status' }, '正在读取历史表格…'));
      api('/api/tcdoc/versions/rows?doc_key=' + encodeURIComponent(docKey)
        + '&version_id=' + encodeURIComponent(versionId) + '&tab_id=' + encodeURIComponent(sheet.tab_id)
        + '&page=' + page + '&sort_column=' + encodeURIComponent(order.order)
        + '&direction=' + (order.order === '' ? '' : order.asc ? 'asc' : 'desc')).then(function (result) {
      if (!area.isConnected || serial !== tableRequest || !opened || opened.meta.version_id !== versionId) return;
      var columns = (sheet.columns || []).map(function (column) {
        return typeof column === 'string' ? column : column.name;
      }).filter(Boolean);
      var rows = result.rows || [], pages = result.pages;
      page = result.page;
      var head = h('tr'), body = h('tbody'), pictures = h('div');
      ['行号'].concat(columns).forEach(function (name, i) {
        var key = String(i);
        var cell = h('th', { tabIndex: 0, class: 'sortable', role: 'columnheader',
          'aria-sort': order.order === key ? (order.asc ? 'ascending' : 'descending') : 'none' },
          name + (order.order === key ? (order.asc ? ' ↑' : ' ↓') : ''));
        function change() { cycleSort(order, key); page = 1; drawTable(sheet, area); }
        cell.addEventListener('click', change);
        cell.addEventListener('keydown', function (event) {
          if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); change(); }
        });
        head.appendChild(cell);
      });
      rows.forEach(function (original) {
        var row = h('tr', null, h('td', null, original.row_no));
        columns.forEach(function (name) {
          var value = (original.values || {})[name];
          var text = value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
          row.appendChild(h('td', { class: 'ellipsis', title: text }, text));
        });
        row.tabIndex = 0;
        function showImages() {
          pictures.replaceChildren(h('div', { class: 'small' }, '第 ' + original.row_no + ' 行 · 留存图片'),
            imageList(original.images || []));
        }
        row.addEventListener('dblclick', showImages);
        row.addEventListener('keydown', function (event) { if (event.key === 'Enter') showImages(); });
        body.appendChild(row);
      });
      var pager = h('div', { class: 'col-pager' });
      [['首页', 1], ['上一页', page - 1], ['下一页', page + 1], ['末页', pages]].forEach(function (item, index) {
        if (index === 2) pager.appendChild(h('span', { class: 'small' }, '第 ' + page + ' / ' + pages + ' 页 · 共 ' + result.total + ' 条'));
        var button = h('button', { class: 'btn mini', type: 'button',
          disabled: item[1] < 1 || item[1] > pages || item[1] === page }, item[0]);
        button.addEventListener('click', function () { page = item[1]; drawTable(sheet, area); });
        pager.appendChild(button);
      });
      area.replaceChildren(h('div', { class: 'small muted', style: 'margin:8px 0' },
        '只读历史 · 表头可排序 · 双击行或按 Enter 查看该行留存图片'),
      result.total ? h('div', { class: 'table-wrap' }, h('table', { class: 'db-table' },
        h('thead', null, head), body)) : h('p', { class: 'muted small' }, '此版本的工作表没有记录，可切换其他工作表'),
      pager, pictures);
      }).catch(function (error) {
        if (!area.isConnected || serial !== tableRequest) return;
        var retry = h('button', { class: 'btn', type: 'button' }, '重试');
        retry.addEventListener('click', function () { drawTable(sheet, area); });
        area.replaceChildren(h('p', null, '读取历史表格失败：' + error.message), retry);
      });
    }
    function draw() {
      content.replaceChildren();
      var sheets = opened.sheets || [], pages = opened.pages || [];
      if (opened.meta.source === 'tables') {
        sheets = sheets.filter(function (sheet) { return !sheet.skipped; });
        if (!sheets.length) { content.appendChild(h('p', null, '此版本没有可展示的工作表，请选择其他版本')); return; }
        var sheetSelect = h('select', { class: 'select', 'aria-label': '历史工作表' });
        sheets.forEach(function (sheet, index) {
          sheetSelect.appendChild(h('option', { value: String(index) }, sheet.tab_name || sheet.tab_id));
        });
        var table = h('div');
        sheetSelect.addEventListener('change', function () {
          page = 1; order = { order: '', asc: true }; drawTable(sheets[Number(sheetSelect.value)], table);
        });
        content.appendChild(h('div', { class: 'toolbar' }, h('span', null, '工作表'), sheetSelect));
        content.appendChild(table);
        drawTable(sheets[0], table);
      } else {
        var pageSelect = h('select', { class: 'select', 'aria-label': '历史页面' }, h('option', { value: '' }, '全部'));
        pages.forEach(function (item, index) {
          pageSelect.appendChild(h('option', { value: String(index) }, item.title || item.id));
        });
        var body = h('div');
        function showPages() {
          body.replaceChildren();
          var selected = pageSelect.value === '' ? pages : [pages[Number(pageSelect.value)]];
          selected.forEach(function (item) {
            body.appendChild(h('section', { style: 'margin:12px 0' },
              h('div', { class: 'row-title' }, item.title || item.id),
              h('div', { style: 'white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0' }, item.text),
              imageList(item.images)));
          });
          if (!selected.length) body.appendChild(h('p', null, '此版本未采集到正文，请选择其他版本'));
        }
        pageSelect.addEventListener('change', showPages);
        content.appendChild(h('div', { class: 'toolbar' }, h('span', null, '页面'), pageSelect));
        content.appendChild(body); showPages();
      }
    }
    function filter() {
      var keep = select.value, query = search.value.trim().toLowerCase();
      select.replaceChildren();
      versions.filter(function (item) { return item.label.toLowerCase().includes(query); }).forEach(function (item) {
        select.appendChild(h('option', { value: item.version_id,
          title: item.label }, item.label + (item.source === 'tables' ? ' · 表格' : ' · 正文')
          + (item.complete ? '' : ' · 部分采集')));
      });
      if (Array.from(select.options).some(function (item) { return item.value === keep; })) select.value = keep;
      select.disabled = busy || !select.options.length; open.disabled = busy || !select.options.length;
      select.title = select.selectedOptions.length ? select.selectedOptions[0].textContent : '';
    }
    function load() {
      var serial = ++request;
      opened = null; content.replaceChildren();
      refresh.disabled = true; open.disabled = true; select.disabled = true;
      note.textContent = '正在读取采集版本…';
      api('/api/tcdoc/versions?doc_key=' + encodeURIComponent(docKey)).then(function (data) {
        if (!active(serial)) return;
        versions = data.versions || []; filter(); refresh.disabled = false;
        note.textContent = versions.length ? '共 ' + versions.length + ' 个版本，请选择后点「打开版本」。历史内容只读。'
          : '还没有采集版本，请先同步表格或抓取正文；此前的旧内容无法补建。';
      }).catch(function (error) {
        if (!active(serial)) return;
        refresh.disabled = false; note.textContent = '读取采集版本失败：' + error.message + '，请点「刷新」重试';
      });
    }
    open.addEventListener('click', function () {
      if (!select.value) return;
      var versionId = select.value, serial = ++request;
      busy = true; search.disabled = true;
      open.disabled = true; refresh.disabled = true; select.disabled = true;
      content.replaceChildren(); note.textContent = '正在打开采集版本…';
      api('/api/tcdoc/versions/open?doc_key=' + encodeURIComponent(docKey)
        + '&version_id=' + encodeURIComponent(versionId)).then(function (data) {
        if (!active(serial)) return;
        opened = data; page = 1; order = { order: '', asc: true };
        note.textContent = '当前打开：' + opened.meta.label + ' · 只读历史'
          + (opened.meta.complete ? '' : ' · 部分采集，含保留的旧内容')
          + (opened.meta.missing_images ? ' · 未留存图片 ' + opened.meta.missing_images + ' 张' : '');
        draw();
      }).catch(function (error) {
        if (active(serial)) note.textContent = '打开采集版本失败：' + error.message + '，可重试或选择其他版本';
      }).finally(function () {
        if (active(serial)) { busy = false; search.disabled = false; refresh.disabled = false; filter(); }
      });
    });
    search.addEventListener('input', filter);
    select.addEventListener('change', function () { select.title = select.selectedOptions[0].textContent; });
    refresh.addEventListener('click', load);
    latest.addEventListener('click', function () { ++request; options.onLatest(); });
    load();
  };
})();
