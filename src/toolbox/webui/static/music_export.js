(function () {
  'use strict';

  window.createMusicExportControl = function (options) {
    var h = options.h;
    var resultId = '';
    var expiresAt = 0;
    var button = h('button', { class: 'btn', type: 'button', disabled: true }, '导出选中（0）');
    var notice = h('div', { class: 'small music-export-notice', role: 'status' });
    function sync() {
      if (resultId && expiresAt && Date.now() >= expiresAt) {
        resultId = '';
        notice.textContent = '搜索结果已过期，请重新搜索后导出。';
      }
      var count = options.getSelectedIds().length;
      button.textContent = '导出选中（' + count + '）';
      button.disabled = options.isBusy() || !resultId || !count;
      button.title = resultId ? '导出勾选歌曲的 JSON、Excel 与 Markdown 信息包'
        : (notice.textContent || '请先搜索并勾选歌曲');
    }
    function failed(message) {
      resultId = '';
      notice.textContent = '导出失败：' + message + '；请重新搜索后重试。';
      options.setBusy(false); sync();
    }
    button.addEventListener('click', function () {
      sync();
      if (button.disabled) return;
      var ids = options.getSelectedIds();
      if (!window.confirm('导出勾选的 ' + ids.length + ' 首歌曲信息？\n\n'
          + '生成 JSON、Excel 与 Markdown 导出包供浏览器下载，不下载音频或封面。')) return;
      options.setBusy(true); sync();
      notice.textContent = '';
      options.post('/api/tools/music/export', { result_id: resultId, ids: ids }).then(function (response) {
        options.watchJob(options.jobHost, response.job, function (job) {
          var result = job.result || {};
          notice.textContent = result.summary || '导出包已生成，点击「下载结果」保存。';
        }, {
          cancelable: true,
          indeterminate: true,
          clearPollErrorOnSuccess: true,
          onSettled: function (job) {
            options.setBusy(false);
            if (job.status === 'error') failed(job.error || '请检查所选歌曲后重试');
            if (job.status === 'cancelled') notice.textContent = '已取消导出，勾选保留，可直接重试。';
            sync();
          },
          onPollError: function (_error, retry) { setTimeout(retry, 1000); }
        });
      }).catch(function (error) { failed(error.message); });
    });
    return {
      button: button,
      notice: notice,
      sync: sync,
      setResult: function (result) {
        resultId = result.export_available && result.export_result_id ? result.export_result_id : '';
        expiresAt = resultId ? Date.now() + (Number(result.expires_in) || 1800) * 1000 : 0;
        notice.textContent = resultId ? '' : (result.export_message || '请重新搜索后导出。');
        sync();
      }
    };
  };

  window.installMusicSearchColumns = function (table, footer, h) {
    var headers = Array.from(table.querySelectorAll('thead th'));
    function reset() { headers.forEach(function (header) { header.style.width = ''; }); }
    function fit() {
      var weights = headers.slice(1).map(function (_header, index) {
        return Math.max(6, ...Array.from(table.rows).map(function (row) {
          return Math.min(60, (row.cells[index + 1].textContent || '').length);
        }));
      });
      var sum = weights.reduce(function (a, b) { return a + b; }, 0);
      headers.forEach(function (header, index) {
        var fraction = weights[index - 1] / sum;
        header.style.width = index ? 'calc(' + (fraction * 100) + '% - ' + (fraction * 36) + 'px)' : '36px';
      });
    }
    footer.appendChild(h('button', { class: 'btn mini', type: 'button', onclick: fit }, '自适应'));
    footer.appendChild(h('button', { class: 'btn mini', type: 'button', onclick: reset }, '重置'));
    table.addEventListener('contextmenu', function (event) {
      if (!event.target.closest('th')) return;
      event.preventDefault();
      var old = footer.querySelector('[role="menu"]'); if (old) old.remove();
      var menu = h('div', { class: 'fg-column-menu', role: 'menu' });
      [['列宽自适应', fit], ['重置列宽', reset]].forEach(function (action) {
        menu.appendChild(h('button', { class: 'btn', type: 'button', role: 'menuitem',
          onclick: function () { action[1](); menu.remove(); } }, action[0]));
      });
      menu.style.left = Math.max(8, Math.min(event.clientX, innerWidth - 180)) + 'px';
      menu.style.top = Math.max(8, Math.min(event.clientY, innerHeight - 90)) + 'px';
      footer.appendChild(menu); menu.firstChild.focus();
      menu.addEventListener('keydown', function (event) { if (event.key === 'Escape') menu.remove(); });
      menu.addEventListener('focusout', function (event) {
        if (!menu.contains(event.relatedTarget)) setTimeout(function () {
          if (menu.isConnected && !menu.contains(document.activeElement)) menu.remove();
        }, 0);
      });
    });
  };
})();
