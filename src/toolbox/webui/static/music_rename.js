(function () {
  'use strict';

  window.createMusicRenamePanel = function (host, options) {
    var h = options.h;
    var selected = new Set();
    var approved = new Set();
    var configuration = null;
    var preview = null;
    var busy = false;
    var order = { order: '', asc: true };
    var filesHost = h('div', { class: 'music-rename-files' });
    var results = h('div');
    var jobHost = h('div');
    var notice = h('div', { class: 'small', role: 'status' });
    var pattern = h('input', { class: 'input', type: 'text', maxlength: '240',
      'aria-label': '文件名模板', value: '@2 - @1' });
    var presets = h('select', { class: 'select', 'aria-label': '模板预设' });
    var tokens = h('div', { class: 'muted small music-rename-tokens' });
    var all = h('button', { class: 'btn', type: 'button' }, '选择文件');
    var clear = h('button', { class: 'btn', type: 'button' }, '清空选择');
    var start = h('button', { class: 'btn primary', type: 'button', disabled: true }, '预览改名');
    var apply = h('button', { class: 'btn primary', type: 'button', disabled: true }, '执行勾选');
    var settingsHost = h('div');
    host.replaceChildren(h('div', { class: 'card music-rename-panel' },
      h('div', { class: 'row-title' }, '按标签批量改名'),
      h('div', { class: 'small' }, '读取文件已保存的标签生成新文件名，保留扩展名。预览不改文件；同名歌词与图片随文件改名，已有目标文件保留。'),
      h('div', { class: 'toolbar' }, all, clear), filesHost,
      settingsHost, tokens, h('div', { class: 'toolbar' }, start, notice),
      results, jobHost));

    function maximum() { return configuration ? configuration.max_files : 100; }
    function sync() {
      all.disabled = clear.disabled = busy || !configuration;
      start.disabled = busy || !configuration || !selected.size;
      apply.disabled = busy || !preview || !preview.plan_id || !approved.size;
      apply.textContent = '执行勾选' + (approved.size ? '（' + approved.size + '）' : '');
      host.querySelectorAll('input,select').forEach(function (control) {
        control.disabled = busy || control.dataset.renameLocked === '1';
      });
    }
    function invalidate() {
      preview = null; approved.clear(); results.replaceChildren();
      notice.textContent = '已选择 ' + selected.size + ' 个文件，最多 ' + maximum() + ' 个。';
      sync();
    }
    function setBusy(value) { busy = value; options.onBusy(value); sync(); }
    function renderFiles() {
      filesHost.replaceChildren();
      var files = options.getFiles();
      var known = new Set(files.map(function (file) { return file.rel; }));
      selected.forEach(function (rel) { if (!known.has(rel)) selected.delete(rel); });
      if (!files.length) filesHost.appendChild(h('div', { class: 'muted small' }, '没有可改名文件，请先添加音乐文件。'));
      files.forEach(function (file) {
        var check = h('input', { type: 'checkbox', checked: selected.has(file.rel) });
        check.addEventListener('change', function () {
          if (check.checked && selected.size >= maximum()) {
            check.checked = false; options.toast('每批最多选择 ' + maximum() + ' 个文件', 'info'); return;
          }
          if (check.checked) selected.add(file.rel); else selected.delete(file.rel);
          invalidate();
        });
        filesHost.appendChild(h('label', { class: 'check ellipsis', title: file.rel }, check,
          h('span', { class: 'ellipsis' }, file.name)));
      });
    }
    all.addEventListener('click', function () {
      selected = new Set(options.getFiles().slice(0, maximum()).map(function (file) { return file.rel; }));
      renderFiles(); invalidate();
    });
    clear.addEventListener('click', function () { selected.clear(); renderFiles(); invalidate(); });
    pattern.addEventListener('input', function () {
      presets.value = configuration && configuration.presets.indexOf(pattern.value) >= 0 ? pattern.value : '';
      invalidate();
    });
    presets.addEventListener('change', function () { if (presets.value) pattern.value = presets.value; invalidate(); });

    function drawResults() {
      results.replaceChildren();
      var rows = preview.rows || [];
      if (!rows.length) {
        results.appendChild(h('div', { class: 'small' }, '没有可改名结果，请调整模板或文件选择后重试。')); return;
      }
      var labels = { ready: '待改名', unchanged: '无变化', skipped: '已跳过', error: '失败',
        done: '已改名', cancelled: '已取消' };
      var columns = [{ key: 'rel', label: '原文件名' }, { key: 'target_rel', label: '新文件名' },
        { key: 'status', label: '状态' }];
      var head = h('tr', null, h('th', null, '改名'));
      columns.forEach(function (column) {
        var th = h('th', { scope: 'col', tabindex: '0' }, column.label
          + (order.order === column.key ? (order.asc ? ' ↑' : ' ↓') : ''));
        var sort = function () { options.cycleSort(order, column.key); drawResults(); };
        th.addEventListener('click', sort);
        th.addEventListener('keydown', function (event) {
          if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); sort(); }
        });
        head.appendChild(th);
      });
      var body = h('tbody');
      options.sortRows(rows, columns, order).forEach(function (row) {
        var check = h('input', { type: 'checkbox', checked: approved.has(row.id),
          disabled: busy || row.status !== 'ready', 'aria-label': '改名 ' + row.rel });
        check.dataset.renameLocked = row.status === 'ready' ? '0' : '1';
        check.addEventListener('change', function () {
          if (check.checked) approved.add(row.id); else approved.delete(row.id); sync();
        });
        var target = h('div', null, row.target_rel || '—');
        (row.related || []).forEach(function (item) {
          target.appendChild(h('div', { class: 'small' },
            (item.status === 'skipped' ? '保留原文件：' : '附属文件：') + item.rel
            + (item.status === 'skipped' ? '' : ' → ' + item.target_rel)
            + (item.message ? ' · ' + item.message : '')));
        });
        body.appendChild(h('tr', null, h('td', null, check), h('td', { title: row.rel }, row.rel),
          h('td', { title: row.target_rel || '' }, target),
          h('td', null, labels[row.status] || '已跳过',
            row.message && row.message !== labels[row.status] ? h('div', { class: 'small' }, row.message) : '')));
      });
      var table = h('table', { class: 'db-table music-rename-table' }, h('thead', null, head), body);
      var wrap = h('div', { class: 'table-wrap' }, table);
      function resetWidths() { head.querySelectorAll('th').forEach(function (th) { th.style.width = ''; }); }
      function fitWidths() {
        var weights = columns.map(function (column) {
          return Math.min(72, Math.max(8, ...rows.map(function (row) { return String(row[column.key] || '').length; })));
        });
        var sum = weights.reduce(function (a, b) { return a + b; }, 0);
        head.querySelectorAll('th').forEach(function (th, index) {
          th.style.width = index ? 'calc((100% - 40px) * ' + weights[index - 1] / sum + ')' : '40px';
        });
      }
      table.addEventListener('contextmenu', function (event) {
        if (!event.target.closest('th')) return;
        event.preventDefault();
        var old = results.querySelector('[role="menu"]'); if (old) old.remove();
        var menu = h('div', { class: 'fg-column-menu', role: 'menu' });
        [['列宽自适应', fitWidths], ['重置列宽', resetWidths]].forEach(function (action) {
          menu.appendChild(h('button', { class: 'btn', type: 'button', role: 'menuitem',
            onclick: function () { action[1](); menu.remove(); } }, action[0]));
        });
        menu.style.left = Math.max(8, Math.min(event.clientX, innerWidth - 180)) + 'px';
        menu.style.top = Math.max(8, Math.min(event.clientY, innerHeight - 90)) + 'px';
        results.appendChild(menu); menu.firstChild.focus();
        menu.addEventListener('keydown', function (key) { if (key.key === 'Escape') menu.remove(); });
        menu.addEventListener('focusout', function (event) {
          if (!menu.contains(event.relatedTarget)) setTimeout(function () {
            if (menu.isConnected && !menu.contains(document.activeElement)) menu.remove();
          }, 0);
        });
      });
      results.appendChild(wrap);
      results.appendChild(h('div', { class: 'toolbar music-rename-actions' },
        h('button', { class: 'btn mini', type: 'button', onclick: fitWidths }, '自适应'),
        h('button', { class: 'btn mini', type: 'button', onclick: resetWidths }, '重置'), apply));
      sync();
    }
    function run(endpoint, payload, onDone) {
      function failed() {
        if (endpoint.endsWith('/apply') && preview) {
          preview.plan_id = ''; approved.clear(); drawResults();
          notice.textContent = '执行未完成，请刷新页面核对文件状态后重新预览。';
        }
      }
      setBusy(true);
      options.post(endpoint, payload).then(function (response) {
        options.watchJob(jobHost, response.job, onDone, { cancelable: true, indeterminate: true,
          onSettled: function (job) {
            setBusy(false);
            if (job.status === 'error') { failed(); options.toast('批量改名失败：' + job.error, 'error'); }
            if (job.status === 'cancelled') notice.textContent = '已取消，请重新预览后继续。';
          }, onPollError: function (_error, retry) { setTimeout(retry, 1000); } });
      }).catch(function (error) { setBusy(false); failed(); options.toast('批量改名失败：' + error.message, 'error'); });
    }
    start.addEventListener('click', function () {
      if (busy || !selected.size || !options.canStart()) return;
      if (!pattern.value.trim()) { options.toast('请填写文件名模板', 'info'); pattern.focus(); return; }
      invalidate();
      run('/api/tools/music-tag/rename/preview', { rels: Array.from(selected), pattern: pattern.value.trim() }, function (job) {
        preview = job.result || {}; approved.clear(); order = { order: '', asc: true };
        (preview.rows || []).forEach(function (row) { if (row.status === 'ready') approved.add(row.id); });
        notice.textContent = (preview.cancelled ? '已取消；' : '') + (preview.summary
          || '可改名 ' + approved.size + ' / ' + (preview.rows || []).length + ' 个文件，请核对后执行。');
        drawResults();
      });
    });
    apply.addEventListener('click', function () {
      if (busy || !preview || !preview.plan_id || !approved.size || !options.canStart()) return;
      if (!window.confirm('按预览改名 ' + approved.size + ' 个音乐文件？\n\n同名歌词与图片一同改名；已有目标不会覆盖。未勾选的文件保持原样。')) return;
      run('/api/tools/music-tag/rename/apply', { plan_id: preview.plan_id, ids: Array.from(approved), confirm: true }, function (job) {
        var outcome = job.result || {};
        (outcome.rows || []).forEach(function (saved) {
          var row = preview.rows.find(function (item) { return item.id === saved.id || item.rel === saved.rel; });
          if (row) Object.assign(row, saved);
        });
        preview.plan_id = ''; approved.clear();
        options.onRenamed(outcome);
        selected.clear(); renderFiles(); drawResults();
        notice.textContent = (outcome.cancelled ? '已取消；' : '') + '已改名 ' + (outcome.moved || 0)
          + ' 个文件。请检查逐项结果，继续改名需重新预览。';
      });
    });
    function loadOptions() {
      settingsHost.textContent = '正在读取模板设置…';
      options.api('/api/music-tag/rename/options').then(function (data) {
        configuration = data; pattern.value = data.default_pattern;
        presets.replaceChildren(h('option', { value: '' }, '自定义'));
        (data.presets || []).forEach(function (value) { presets.appendChild(h('option', { value: value }, value)); });
        presets.value = pattern.value;
        settingsHost.replaceChildren(h('div', { class: 'music-rename-settings' },
          h('label', null, '文件名模板', pattern), h('label', null, '模板预设', presets)));
        tokens.textContent = (data.tokens || []).map(function (item) { return item.token + ' ' + item.label; }).join(' · ')
          + '；模板含标题或艺术家但该标签为空时跳过。';
        all.textContent = '选前 ' + maximum() + ' 个'; invalidate();
      }).catch(function (error) {
        settingsHost.replaceChildren(h('span', null, '读取模板失败：' + error.message),
          h('button', { class: 'btn', type: 'button', onclick: loadOptions }, '重试'));
      });
    }
    renderFiles(); loadOptions();
    return { refreshFiles: function () { if (!busy) { renderFiles(); invalidate(); } },
      isBusy: function () { return busy; } };
  };
})();
