(function () {
  'use strict';

  window.createMusicMatchPanel = function (host, options) {
    var h = options.h;
    var selected = new Set();
    var approved = new Set();
    var configuration = null;
    var preview = null;
    var busy = false;
    var order = { order: '', asc: true };
    var fileList = h('div', { class: 'music-match-files' });
    var settingsHost = h('div');
    var results = h('div');
    var jobHost = h('div');
    var notice = h('div', { class: 'muted small', role: 'status' });
    var start = h('button', { class: 'btn primary', type: 'button', disabled: true }, '预览匹配');
    var apply = h('button', { class: 'btn primary', type: 'button', disabled: true }, '写入勾选');
    var all = h('button', { class: 'btn', type: 'button' }, '选前 20 个');
    var clear = h('button', { class: 'btn', type: 'button' }, '清空选择');
    var sources = {};
    var modes = {};
    var overwrite = {};
    var filenameOnly;
    var useArtist;
    var useAlbum;
    var threads;
    host.replaceChildren(h('div', { class: 'card music-match-panel' },
      h('div', { class: 'row-title' }, '自动匹配'),
      h('div', { class: 'muted small' },
        '读取已保存标签，从所选来源取最高候选；预览不改文件。默认只填空值，覆盖需逐项勾选。结果保留 30 分钟，写入前会检查文件变化。'),
      h('div', { class: 'toolbar' }, all, clear), fileList, settingsHost,
      h('div', { class: 'toolbar' }, start, apply, notice), results, jobHost));

    function syncButtons() {
      all.disabled = clear.disabled = busy;
      start.disabled = busy || !configuration || !selected.size;
      apply.disabled = busy || !preview || !preview.preview_id || !approved.size;
      apply.textContent = '写入勾选' + (approved.size ? '（' + approved.size + '）' : '');
      host.querySelectorAll('input,select').forEach(function (control) {
        control.disabled = busy || control.dataset.matchLocked === '1';
      });
    }
    function setBusy(value) {
      busy = value;
      options.onBusy(value);
      syncButtons();
    }
    function invalidate() {
      preview = null;
      approved.clear();
      results.replaceChildren();
      notice.textContent = '已选择 ' + selected.size + ' 个文件，最多 20 个。';
      syncButtons();
    }
    function renderFiles() {
      fileList.replaceChildren();
      var files = options.getFiles();
      var known = new Set(files.map(function (file) { return file.rel; }));
      selected.forEach(function (rel) { if (!known.has(rel)) selected.delete(rel); });
      if (!files.length) fileList.appendChild(h('span', { class: 'muted small' }, '没有可匹配文件，请先添加音乐文件。'));
      files.forEach(function (file) {
        var check = h('input', { type: 'checkbox', checked: selected.has(file.rel) });
        check.addEventListener('change', function () {
          if (check.checked && selected.size >= 20) {
            check.checked = false;
            options.toast('每次最多匹配 20 个文件，请分批选择', 'info');
            return;
          }
          if (check.checked) selected.add(file.rel); else selected.delete(file.rel);
          invalidate();
        });
        fileList.appendChild(h('label', { class: 'check ellipsis', title: file.rel }, check,
          h('span', { class: 'ellipsis' }, file.name)));
      });
    }
    all.addEventListener('click', function () {
      selected = new Set(options.getFiles().slice(0, 20).map(function (file) { return file.rel; }));
      renderFiles(); invalidate();
    });
    clear.addEventListener('click', function () { selected.clear(); renderFiles(); invalidate(); });

    function checkbox(label, checked) {
      var input = h('input', { type: 'checkbox', checked: checked });
      input.addEventListener('change', invalidate);
      return { input: input, label: h('label', { class: 'check' }, input, label) };
    }
    function renderSettings(data) {
      configuration = data;
      var defaults = data.defaults || data.settings || {};
      filenameOnly = checkbox('仅用文件名', !!defaults.use_filename_only);
      useArtist = checkbox('使用艺术家', defaults.use_artist !== false);
      useAlbum = checkbox('使用专辑', defaults.use_album !== false);
      threads = h('input', { class: 'input', type: 'number', min: '1', max: '8',
        value: defaults.threads || 3, style: 'width:76px' });
      threads.addEventListener('change', invalidate);
      var sourceRow = h('div', { class: 'toolbar' });
      (data.sources || []).forEach(function (source) {
        var key = source.key || source.name;
        var choice = checkbox(source.label || source.name || key,
          (defaults.sources || []).indexOf(key) >= 0);
        sources[key] = choice.input;
        sourceRow.appendChild(choice.label);
      });
      var fields = h('div', { class: 'music-match-fields' });
      (data.fields || []).forEach(function (field) {
        var key = field.key || field.name;
        var select = h('select', { class: 'select', 'aria-label': (field.label || key) + '写入方式' });
        (field.modes || []).forEach(function (mode) {
          select.appendChild(h('option', { value: mode.value }, mode.label));
        });
        select.value = String((defaults.modes || {})[key] || 0);
        select.addEventListener('change', invalidate);
        modes[key] = select;
        var replace = checkbox('覆盖已有值', !!(defaults.overwrite || {})[key]);
        replace.input.setAttribute('aria-label', (field.label || key) + '覆盖已有值');
        overwrite[key] = replace.input;
        fields.appendChild(h('div', { class: 'music-match-field' },
          h('span', null, field.label || key), select, replace.label));
      });
      settingsHost.replaceChildren(h('div', { class: 'toolbar' },
        filenameOnly.label, useArtist.label, useAlbum.label,
        h('label', { class: 'check' }, '并发', threads)), sourceRow, fields);
      invalidate();
    }
    function settings() {
      var count = Number(threads.value);
      if (!Number.isInteger(count) || count < 1 || count > 8) throw new Error('并发须为 1–8 的整数');
      var enabled = Object.keys(sources).filter(function (key) { return sources[key].checked; });
      if (!enabled.length) throw new Error('请至少选择一个标签源');
      var result = { use_filename_only: filenameOnly.input.checked, use_artist: useArtist.input.checked,
        use_album: useAlbum.input.checked, threads: count, sources: enabled, modes: {}, overwrite: {} };
      Object.keys(modes).forEach(function (key) {
        result.modes[key] = Number(modes[key].value);
        result.overwrite[key] = overwrite[key].checked;
      });
      return result;
    }
    function renderPreview(data) {
      preview = data;
      approved.clear();
      (data.rows || []).forEach(function (row) { if (row.status === 'ready') approved.add(row.rel); });
      order = { order: '', asc: true };
      drawResults();
      notice.textContent = data.summary || '请核对匹配结果后写入。';
      syncButtons();
    }
    function drawResults() {
      var rows = preview.rows || [];
      results.replaceChildren();
      if (!rows.length) { results.appendChild(h('div', { class: 'muted small' }, '没有匹配结果，请调整来源或搜索条件后重试。')); return; }
      var headers = h('tr', null, h('th', null, '写入'));
      rows.forEach(function (row) {
        var candidate = row.candidate || {};
        row.candidate_text = [candidate.source_name || candidate.source, candidate.title, candidate.artist, candidate.album].filter(Boolean).join(' · ');
      });
      var columns = [{ key: 'name', label: '文件' }, { key: 'status', label: '状态' },
        { key: 'candidate_text', label: '候选 / 变更' }];
      columns.forEach(function (column) {
        var header = h('th', null, column.label
          + (order.order === column.key ? (order.asc ? ' ↑' : ' ↓') : ''));
        header.addEventListener('click', function () { options.cycleSort(order, column.key); drawResults(); });
        headers.appendChild(header);
      });
      var body = h('tbody');
      options.sortRows(rows, columns, order).forEach(function (row) {
        var choice = h('input', { type: 'checkbox', checked: approved.has(row.rel),
          disabled: busy || row.status !== 'ready', 'aria-label': '写入 ' + row.name });
        choice.dataset.matchLocked = row.status === 'ready' ? '0' : '1';
        choice.addEventListener('change', function () {
          if (choice.checked) approved.add(row.rel); else approved.delete(row.rel);
          syncButtons();
        });
        var detail = h('div', { class: 'music-match-changes' });
        var candidate = row.candidate || {};
        if (candidate.title) detail.appendChild(h('div', { class: 'row-title' },
          [candidate.source_name || candidate.source, candidate.title, candidate.artist, candidate.album].filter(Boolean).join(' · ')));
        if (candidate.scores && candidate.scores.length) detail.appendChild(h('div', { class: 'muted small' },
          '标题 / 艺术家 / 专辑相似度：' + candidate.scores.map(function (score) {
            return Math.round(score * 100) + '%';
          }).join(' / ')));
        (row.changes || []).forEach(function (change) {
          detail.appendChild(h('div', { class: 'small' }, (change.label || change.key) + '：'
            + (change.before || '（空）') + ' → ' + (change.after || '（空）')));
        });
        if (row.cover_changed) detail.appendChild(h('div', { class: 'small' }, '更新内嵌前封面，保留其他图片'));
        (row.sidecars || []).forEach(function (sidecar) {
          detail.appendChild(h('div', { class: 'small' }, (sidecar.action === 'replace' ? '覆盖' : sidecar.action === 'skip' ? '跳过已有' : '新建')
            + '同名文件：' + (sidecar.name || sidecar.filename)));
        });
        if (row.message) detail.appendChild(h('div', { class: 'muted small' }, row.message));
        var labels = { ready: '待写入', unchanged: '无需修改', unmatched: '未匹配', error: '失败', cancelled: '已取消', saved: '已写入', skipped: '已跳过' };
        body.appendChild(h('tr', null, h('td', null, choice),
          h('td', { class: 'ellipsis', title: row.rel }, row.name),
          h('td', null, labels[row.status] || row.status), h('td', null, detail)));
      });
      var table = h('table', { class: 'db-table music-match-table' }, h('thead', null, headers), body);
      var wrap = h('div', { class: 'table-wrap' }, table);
      var auto = h('button', { class: 'btn mini', type: 'button' }, '自适应');
      var reset = h('button', { class: 'btn mini', type: 'button' }, '重置');
      auto.addEventListener('click', function () { table.style.tableLayout = 'auto'; });
      reset.addEventListener('click', function () { table.style.tableLayout = ''; });
      results.appendChild(wrap);
      results.appendChild(h('div', { class: 'toolbar music-match-columns' }, auto, reset));
    }
    function run(endpoint, payload, onDone) {
      setBusy(true);
      options.post(endpoint, payload).then(function (response) {
        options.watchJob(jobHost, response.job, onDone, {
          cancelable: true,
          indeterminate: true,
          onSettled: function (job) {
            setBusy(false);
            if (job.status === 'error') options.toast('自动匹配失败：' + job.error, 'error');
          },
          onPollError: function (_error, retry) { setTimeout(retry, 1000); }
        });
      }).catch(function (error) {
        setBusy(false); options.toast('自动匹配失败：' + error.message, 'error');
      });
    }
    start.addEventListener('click', function () {
      if (busy || !selected.size) return;
      var config;
      try { config = settings(); } catch (error) { options.toast(error.message, 'error'); return; }
      if (!options.canStart()) return;
      invalidate();
      run('/api/tools/music-tag/match/preview', { rels: Array.from(selected), settings: config }, function (job) { renderPreview(job.result || {}); });
    });
    apply.addEventListener('click', function () {
      if (busy || !preview || !approved.size || !options.canStart()) return;
      if (!window.confirm('将自动匹配结果写入 ' + approved.size + ' 个音乐文件？\n\n'
          + '按预览修改标签、图片与同名文件；覆盖已有同名文件前会保存安全副本。')) return;
      run('/api/tools/music-tag/match/apply', {
        preview_id: preview.preview_id, rels: Array.from(approved), confirmed: true
      }, function (job) {
        var outcome = job.result || {};
        (outcome.rows || []).forEach(function (saved) {
          var row = preview.rows.find(function (item) { return item.rel === saved.rel; });
          if (row) Object.assign(row, saved);
          approved.delete(saved.rel);
        });
        approved.clear();
        preview.preview_id = '';
        options.onSaved(outcome);
        notice.textContent = (outcome.summary || '已完成，请检查逐项结果。') + ' 再次写入或处理未选项需重新预览。';
        drawResults();
      });
    });
    function loadOptions() {
      settingsHost.textContent = '正在读取匹配设置…';
      options.api('/api/music-tag/match/options').then(renderSettings).catch(function (error) {
        settingsHost.replaceChildren(h('span', null, '读取设置失败：' + error.message),
          h('button', { class: 'btn mini', type: 'button', onclick: loadOptions }, '重试'));
      });
    }
    renderFiles(); loadOptions();
    return { refreshFiles: function () { if (!busy) { renderFiles(); invalidate(); } }, isBusy: function () { return busy; } };
  };
})();
