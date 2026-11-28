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

  window.renderMusicResolvedEntity = function (h, outcome, onSelect) {
    var kind = outcome.kind || '';
    var detail = outcome.detail || {};
    var title = {
      playlist: '歌单信息', album: '专辑信息', artist: '歌手信息',
      albums: '专辑候选', artists: '歌手候选'
    }[kind] || '音乐信息';
    var root = h('div', { class: 'music-song-detail' });

    function candidateRows(candidates, mode) {
      var host = h('div', { class: 'grid' });
      (candidates || []).forEach(function (candidate) {
        if (!candidate || !candidate.id || !candidate.name) return;
        var secondary = [candidate.artist, candidate.count ? candidate.count + ' 首' : '']
          .filter(Boolean).join(' · ');
        var select = h('button', {
          class: 'btn small', type: 'button',
          onclick: function () { if (onSelect) onSelect(mode, String(candidate.id)); }
        }, mode === 'artist' ? '查看歌手' : '解析专辑');
        host.appendChild(h('div', { class: 'kv' },
          h('div', { style: 'min-width:0;overflow-wrap:anywhere' },
            h('strong', null, candidate.name),
            secondary ? h('div', { class: 'muted small' }, secondary) : null),
          select));
      });
      if (!host.childNodes.length) {
        host.appendChild(h('div', { class: 'muted small' }, '没有可显示的候选项。'));
      }
      return host;
    }

    function appendFields(fields) {
      fields.forEach(function (field) {
        var value = detail[field[0]];
        if (value === null || value === undefined || value === '') return;
        root.appendChild(h('div', { class: 'kv' },
          h('span', { class: 'muted small' }, field[1]),
          h('span', { style: 'min-width:0;text-align:right;overflow-wrap:anywhere' }, String(value))));
      });
    }

    if (kind === 'albums' || kind === 'artists') {
      var candidates = outcome.entities || detail.candidates || [];
      root.appendChild(h('div', { class: 'row-title' }, title));
      root.appendChild(candidateRows(candidates, kind === 'artists' ? 'artist' : 'album'));
      return root;
    }

    root.appendChild(h('div', { class: 'row-title' }, detail.name || title));
    if (kind === 'playlist') {
      appendFields([['creator', '创建者'], ['song_count', '歌曲数'],
        ['returned_count', '已读取'], ['play_count', '播放数'], ['tags', '标签']]);
    } else if (kind === 'album') {
      appendFields([['singer', '歌手'], ['release_date', '发行日期'],
        ['album_type', '专辑类型'], ['language', '语种'], ['genre', '流派'],
        ['company', '发行公司'], ['song_count', '歌曲数'], ['returned_count', '已读取']]);
    } else if (kind === 'artist') {
      appendFields([['song_count', '歌曲数'], ['album_count', '专辑数'], ['fans', '粉丝数']]);
    }
    if (detail.description || detail.desc) {
      root.appendChild(h('pre', { class: 'music-song-lyrics' }, detail.description || detail.desc));
    }
    if (kind === 'artist' && (detail.albums || []).length) {
      root.appendChild(h('div', { class: 'row-title' }, '相关专辑'));
      root.appendChild(candidateRows(detail.albums, 'album'));
    }
    return root;
  };
})();
