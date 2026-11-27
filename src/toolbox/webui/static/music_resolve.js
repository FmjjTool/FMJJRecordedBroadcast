(function () {
  'use strict';

  // Presentation only: all platform parsing and preview reads belong to L3.
  window.renderMusicSongDetail = function (h, outcome) {
    var row = (outcome.items || [])[0] || {};
    var detail = outcome.detail || {};
    var metadata = h('div', { class: 'music-song-metadata' },
      h('div', { class: 'row-title' }, row.name || '歌曲详情'));
    [['平台', row.platform_name], ['歌手', row.singer], ['专辑', row.album],
      ['时长', row.duration_text], ['语种', detail.language], ['流派', detail.genre],
      ['发行日期', detail.release_date], ['模式', detail.mode], ['ID', row.song_id]]
      .forEach(function (field) {
        if (field[1]) metadata.appendChild(h('div', { class: 'small' }, field[0] + '：' + field[1]));
      });
    if (detail.vip) metadata.appendChild(h('div', { class: 'small' }, '会员歌曲，下载可能受平台授权限制。'));
    var cover = h('div', { class: 'music-song-cover muted small' }, '暂无封面预览');
    if (/^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(detail.cover_preview || '')) {
      cover.replaceChildren(h('img', { src: detail.cover_preview, alt: '歌曲封面', width: '120', height: '120' }));
    }
    var lyrics = detail.lyrics || '';
    return h('div', { class: 'music-song-detail' },
      h('div', { class: 'music-song-summary' }, cover, metadata),
      h('div', { class: 'small', role: 'status' }, '单曲已选中，可在下方下载歌曲或导出信息。'),
      h('div', { class: 'row-title' }, '歌词'),
      lyrics ? h('pre', { class: 'music-song-lyrics', tabindex: '0', 'aria-label': '歌曲歌词' }, lyrics)
        : h('div', { class: 'muted small' }, '平台未提供歌词。'));
  };
})();
