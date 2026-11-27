(function () {
  'use strict';

  window.createMusicPictureEditor = function (host, options) {
    var h = options.h;
    var original = [];
    var draft = [];
    var revision = '';
    var limits = {};
    var dirty = false;
    var reading = false;
    var busy = false;
    var requestVersion = 0;
    var note = h('div', { class: 'muted small', role: 'status' });
    var list = h('div', { class: 'music-picture-grid' });
    var title = h('span', { class: 'row-title' }, '内嵌图片');
    var add = h('button', { class: 'btn', type: 'button', disabled: true }, '添加图片');
    var replace = h('button', { class: 'btn', type: 'button', disabled: true }, '更换前封面');
    var removeFront = h('button', { class: 'btn', type: 'button', disabled: true }, '移除前封面');
    var cancel = h('button', { class: 'btn', type: 'button', disabled: true }, '取消修改');
    var reload = h('button', { class: 'btn', type: 'button', disabled: true }, '重新读取');
    var input = h('input', {
      type: 'file', accept: 'image/jpeg,image/png,image/gif,image/webp,image/bmp,image/tiff',
      multiple: true, hidden: true
    });
    var replaceMode = false;
    var panel = h('div', { class: 'card music-picture-editor' },
      h('div', { class: 'toolbar' }, title),
      h('div', { class: 'toolbar' }, add, replace, removeFront, cancel, reload),
      input, note, list);
    host.replaceChildren(panel);

    function unavailable() { return busy || reading || options.isBusy(); }
    function changed() {
      title.textContent = '内嵌图片 · ' + draft.length + ' 张';
      note.textContent = reading ? '正在读取所选图片…'
        : (dirty ? '图片修改已暂存，点击「写入标签」后保存到音频。'
          : '图片与桌面共用；编辑先暂存，写入前可取消修改。');
      var locked = unavailable() || !revision;
      add.disabled = locked;
      replace.disabled = locked;
      removeFront.disabled = locked || !draft.some(function (item) { return item.type === 3; });
      cancel.disabled = locked || !dirty;
      reload.disabled = unavailable();
      list.querySelectorAll('button').forEach(function (button) { button.disabled = locked; });
      if (options.onChange) options.onChange();
    }
    function imageUrl(item, thumbnail) {
      if (item.data) return 'data:' + item.mime + ';base64,' + item.data;
      return options.withToken('/api/music-tag/picture?rel=' + encodeURIComponent(options.rel)
        + '&index=' + item.source + '&revision=' + encodeURIComponent(revision)
        + (thumbnail ? '&thumbnail=1' : '&download=1'));
    }
    function markDirty() { dirty = true; render(); }
    function render() {
      list.replaceChildren();
      if (!draft.length) {
        list.appendChild(h('div', { class: 'muted small' },
          '没有内嵌图片，可添加图片后写入标签。'));
      }
      draft.forEach(function (item, index) {
        var preview = h('img', {
          class: 'music-picture-preview', src: imageUrl(item, true),
          alt: '内嵌图片 ' + (index + 1), loading: 'lazy'
        });
        var previewError = h('span', { class: 'muted small' });
        preview.addEventListener('error', function () {
          preview.hidden = true;
          previewError.textContent = '图片无法预览，可移除后重新添加。';
        });
        var remove = h('button', { class: 'btn mini', type: 'button' }, '移除');
        var front = h('button', { class: 'btn mini', type: 'button' }, '设为前置');
        var exportButton = h('button', { class: 'btn mini', type: 'button' }, '导出');
        remove.addEventListener('click', function () {
          if (unavailable()) return;
          draft.splice(index, 1);
          markDirty();
        });
        front.addEventListener('click', function () {
          if (unavailable()) return;
          var selected = draft.splice(index, 1)[0];
          selected.type = 3;
          selected.front = true;
          draft.unshift(selected);
          markDirty();
        });
        exportButton.addEventListener('click', function () {
          if (unavailable()) return;
          var link = h('a', {
            href: imageUrl(item, false), download: item.name || ('cover_' + (index + 1))
          });
          document.body.appendChild(link);
          link.click();
          link.remove();
        });
        list.appendChild(h('div', { class: 'music-picture-item' },
          preview, previewError,
          h('div', { class: 'row-title' }, (index + 1) + '. '
            + (item.type === 3 ? '前置封面' : (item.type_name || '内嵌图片'))),
          h('div', { class: 'muted small music-picture-description' },
            [item.desc || item.name, item.mime, options.fmtBytes(item.size)].filter(Boolean).join(' · ')),
          h('div', { class: 'toolbar' }, remove, front, exportButton)));
      });
      changed();
    }
    function setSnapshot(state) {
      revision = state.revision || '';
      limits = state.limits || {};
      original = (state.pictures || []).map(function (item) {
        return Object.assign({ source: item.index }, item);
      });
      draft = original.map(function (item) { return Object.assign({}, item); });
      dirty = false;
      render();
    }
    function load() {
      var version = ++requestVersion;
      reading = true;
      changed();
      note.textContent = '正在读取内嵌图片…';
      options.api('/api/music-tag/pictures?rel=' + encodeURIComponent(options.rel))
        .then(function (state) {
          if (version === requestVersion) { reading = false; setSnapshot(state); }
        }).catch(function (error) {
          if (version !== requestVersion) return;
          reading = false;
          changed();
          note.replaceChildren(h('span', null, '读取图片失败：' + error.message + ' '),
            h('button', { class: 'btn mini', type: 'button', onclick: load }, '重试'));
        });
    }
    function selectImages(replacing) {
      if (unavailable()) return;
      replaceMode = replacing;
      input.multiple = !replacing;
      input.value = '';
      input.click();
    }
    add.addEventListener('click', function () { selectImages(false); });
    replace.addEventListener('click', function () { selectImages(true); });
    removeFront.addEventListener('click', function () {
      if (unavailable()) return;
      draft = draft.filter(function (item) { return item.type !== 3; });
      markDirty();
    });
    cancel.addEventListener('click', function () {
      if (unavailable()) return;
      draft = original.map(function (item) { return Object.assign({}, item); });
      dirty = false;
      render();
    });
    reload.addEventListener('click', function () {
      if (unavailable()) return;
      if (dirty && !window.confirm('重新读取将放弃尚未写入的图片修改。继续？')) return;
      load();
    });
    input.addEventListener('change', function () {
      var files = Array.from(input.files || []);
      if (!files.length || unavailable()) return;
      if (files.some(function (file) {
        return file.type ? !/^image\/(jpeg|png|gif|webp|bmp|tiff)$/i.test(file.type)
          : !/\.(jpe?g|png|gif|webp|bmp|tiff?)$/i.test(file.name);
      })) {
        options.toast('添加图片失败：只支持 JPEG、PNG、GIF、WebP、BMP 和 TIFF 图片。', 'error');
        return;
      }
      var base = replaceMode ? draft.filter(function (item) { return item.type !== 3; }) : draft.slice();
      var total = base.filter(function (item) { return !!item.data; })
        .reduce(function (sum, item) { return sum + item.size; }, 0);
      if (base.length + files.length > limits.max_pictures || files.some(function (file) {
        return !file.size || file.size > limits.max_bytes;
      }) || total + files.reduce(function (sum, file) { return sum + file.size; }, 0) > limits.max_upload_bytes) {
        options.toast('添加图片失败：每张及本次新增图片合计最多 20 MB，最多 64 张。', 'error');
        return;
      }
      reading = true;
      changed();
      Promise.all(files.map(function (file) {
        return new Promise(function (resolve, reject) {
          var reader = new FileReader();
          reader.onerror = function () { reject(new Error('图片读取失败，请重新选择')); };
          reader.onload = function () {
            var url = String(reader.result || '');
            var split = url.indexOf(',');
            if (split < 0) { reject(new Error('图片内容无效，请重新选择')); return; }
            resolve({ data: url.slice(split + 1), mime: file.type || 'image/jpeg',
              name: file.name, desc: file.name, type: 3, size: file.size });
          };
          reader.readAsDataURL(file);
        });
      })).then(function (images) {
        draft = replaceMode ? images.concat(base) : base.concat(images);
        dirty = true;
      }).catch(function (error) {
        options.toast('添加图片失败：' + error.message, 'error');
      }).then(function () { reading = false; render(); });
    });
    load();
    return {
      isDirty: function () { return dirty; },
      isReading: function () { return reading; },
      setBusy: function (value) { busy = value; changed(); },
      commit: function (state) { ++requestVersion; reading = false; setSnapshot(state); },
      reload: load,
      payload: function () {
        if (reading) throw new Error('图片尚未读取完成，请稍后重试');
        if (!dirty) return null;
        return { picture_revision: revision, pictures: draft.map(function (item) {
          if (item.data) return { data: item.data, name: item.name };
          var descriptor = { source: item.source };
          if (item.front) descriptor.front = true;
          return descriptor;
        }) };
      }
    };
  };
})();
