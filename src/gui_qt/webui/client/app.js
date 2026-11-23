(function () {
  'use strict';

  var params = new URLSearchParams(location.search);
  var token = params.get('token') || sessionStorage.getItem('webui-token') || '';
  if (params.get('token')) sessionStorage.setItem('webui-token', token);

  var $ = function (id) { return document.getElementById(id); };
  var stage = $('stage'), fit = $('fit'), canvas = $('canvas');
  var conn = $('conn'), statusText = $('statusText'), statusPage = $('statusPage');
  var select = $('pageSelect'), hint = $('hint'), hintText = $('hintText');
  var zoomFit = $('zoomFit');

  var snap = null, version = 0, lastSkel = '', lastAt = 0, es = null;
  var fitMode = true, zoom = 1, scale = 1;
  var focusState = null;
  var timers = new Map();
  var nodeEls = new Map();

  if (!token) {
    hintText.textContent = '访问链接缺少令牌。请回到桌面端「全局设置 → 页面镜像」重新点「打开 WebUI」。';
    hint.classList.add('show');
    return;
  }

  function api(path) {
    return path + (path.indexOf('?') >= 0 ? '&' : '?') + 'token=' + encodeURIComponent(token);
  }
  function send(action) {
    fetch(api('/api/action'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-WebUI-Token': token },
      body: JSON.stringify(action),
      cache: 'no-store'
    }).catch(function () {});
  }
  function setConn(text, bad) {
    conn.textContent = text;
    conn.classList.toggle('bad', !!bad);
  }
  function updateStatus() {
    statusText.textContent = '第 ' + version + ' 版 · ' +
      new Date().toLocaleTimeString('zh-CN', { hour12: false });
  }

  function loadPages() {
    fetch(api('/api/pages'), { cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error(String(r.status));
      return r.json();
    }).then(function (data) {
      select.replaceChildren();
      var groups = data.groups || [];
      for (var gi = 0; gi < groups.length; gi++) {
        var group = groups[gi];
        var og = document.createElement('optgroup');
        og.label = group.g;
        var items = group.items || [];
        for (var ii = 0; ii < items.length; ii++) {
          var op = document.createElement('option');
          op.value = items[ii].k;
          op.textContent = items[ii].t;
          og.appendChild(op);
        }
        select.appendChild(og);
      }
      if (snap) select.value = snap.page;
    }).catch(function () {
      setConn('页面列表加载失败', true);
    });
  }

  function applyTokens(t) {
    t = t || {};
    var map = {
      window: '--window', surface: '--surface', surface_alt: '--surface-alt',
      text: '--text', muted: '--muted', border: '--border', accent: '--accent',
      accent_soft: '--accent-soft', accent_hover: '--accent-hover',
      on_accent: '--on-accent', link: '--link', danger: '--danger',
      danger_soft: '--danger-soft', success: '--success', warning: '--warning',
      input_border: '--input-border', hover: '--hover', disabled_bg: '--disabled-bg',
      header_bg: '--header-bg', scroll: '--scroll'
    };
    var root = document.documentElement.style;
    Object.keys(map).forEach(function (key) {
      if (t[key]) root.setProperty(map[key], t[key]);
    });
    document.documentElement.style.colorScheme = t.dark ? 'dark' : 'light';
  }

  function saveFocus() {
    focusState = null;
    var active = document.activeElement;
    if (!active || !canvas.contains(active)) return;
    var holder = active.closest('.n');
    if (!holder) return;
    focusState = { id: holder.dataset.id, value: active.value };
    if (typeof active.selectionStart === 'number') {
      focusState.start = active.selectionStart;
      focusState.end = active.selectionEnd;
    }
  }

  function restoreFocus() {
    if (!focusState) return;
    var holder = null;
    var nodes = canvas.querySelectorAll('[data-id]');
    for (var i = 0; i < nodes.length; i++) {
      if (nodes[i].dataset.id === focusState.id) { holder = nodes[i]; break; }
    }
    var state = focusState;
    focusState = null;
    if (!holder) return;
    var target = holder.matches('input,textarea,select') ? holder
      : holder.querySelector('input,textarea,select');
    if (!target) return;
    try { target.focus({ preventScroll: true }); } catch (e) { target.focus(); }
    if (typeof state.start === 'number') {
      try { target.setSelectionRange(state.start, state.end); } catch (e) { /* 数字框不支持 */ }
    }
  }

  function render(data) {
    var previous = snap;
    var canPatch = !!(previous && previous.page === data.page &&
      previous.size[0] === data.size[0] && previous.size[1] === data.size[1] &&
      data.skel && data.skel === lastSkel && nodeEls.size > 0);
    snap = data;
    version = data.v;
    lastAt = Date.now();
    applyTokens(data.tokens);
    document.title = data.title ? (data.title + ' · 页面镜像') : '页面镜像';
    $('pageTitle').textContent = data.title || '';
    statusPage.textContent = data.title ? ('页面：' + data.title) : '';
    var hasOption = false;
    for (var i = 0; i < select.options.length; i++) {
      if (select.options[i].value === data.page) { hasOption = true; break; }
    }
    if (!hasOption && data.page) {
      var extra = document.createElement('option');
      extra.value = data.page;
      extra.textContent = data.title || data.page;
      select.appendChild(extra);
    }
    select.value = data.page;
    if (canPatch) {
      var rootEl = nodeEls.get(data.root.id);
      if (rootEl && patchNode(rootEl, data.root, data)) {
        updateStatus();
        return;
      }
    }
    fullRender(data);
  }

  function fullRender(data) {
    lastSkel = data.skel || '';
    saveFocus();
    nodeEls.clear();
    canvas.replaceChildren();
    canvas.style.width = data.size[0] + 'px';
    canvas.style.height = data.size[1] + 'px';
    canvas.appendChild(buildNode(data.root, data));
    syncScale();
    restoreFocus();
    updateStatus();
  }

  function syncScale() {
    if (!snap) return;
    var avail = Math.max(240, stage.clientWidth - 24);
    var width = snap.size[0] || 1;
    scale = fitMode ? Math.min(1, avail / width) : zoom;
    canvas.style.transform = 'scale(' + scale + ')';
    fit.style.width = (width * scale) + 'px';
    fit.style.height = ((snap.size[1] || 1) * scale) + 'px';
    zoomFit.classList.toggle('on', fitMode);
  }

  function appendKids(box, node, state) {
    var kids = node.kids || [];
    for (var i = 0; i < kids.length; i++) {
      box.appendChild(buildNode(kids[i], state));
    }
  }

  function buildNode(node, state) {
    var box = document.createElement('div');
    box.className = 'n ' + node.k;
    box.style.left = node.x + 'px';
    box.style.top = node.y + 'px';
    box.style.width = node.w + 'px';
    box.style.height = node.h + 'px';
    box.dataset.id = node.id;
    box.__node = node;
    box.__role = node.role || '';
    nodeEls.set(node.id, box);
    if (node.tip) box.title = node.tip;
    if (node.role) box.classList.add('role-' + node.role);
    if (node.fg) box.style.color = node.fg;
    if (node.fs && !node.role) box.style.fontSize = node.fs + 'px';
    if (node.bold && !node.role) box.style.fontWeight = 'bold';

    switch (node.k) {
      case 'page': box.classList.add('page'); appendKids(box, node, state); break;
      case 'label': fillLabel(box, node); break;
      case 'button':
      case 'toolbutton': fillButton(box, node); break;
      case 'check':
      case 'radio': fillCheck(box, node); break;
      case 'line':
      case 'datetime': fillLine(box, node); break;
      case 'text':
      case 'plain': fillArea(box, node); break;
      case 'combo': fillCombo(box, node); break;
      case 'spin':
      case 'dspin': fillSpin(box, node); break;
      case 'slider': fillSlider(box, node); break;
      case 'progress': fillProgress(box, node); break;
      case 'tabs': fillTabs(box, node, state); break;
      case 'scroll': fillScroll(box, node, state); break;
      case 'table': fillTable(box, node); break;
      case 'list': fillList(box, node); break;
      case 'tree': fillTree(box, node); break;
      case 'group': fillGroup(box, node, state); break;
      case 'frame':
        if (node.variant === 'card') box.classList.add('card');
        else if (node.variant === 'subtle') box.classList.add('subtle');
        else if (node.variant === 'toolbar') box.classList.add('toolbarcard');
        appendKids(box, node, state);
        break;
      default: appendKids(box, node, state);
    }
    return box;
  }

  function updateRole(el, node) {
    var role = node.role || '';
    if (el.__role === role) return;
    if (el.__role) el.classList.remove('role-' + el.__role);
    if (role) el.classList.add('role-' + role);
    el.__role = role;
  }

  function setBase(el, node) {
    el.style.left = node.x + 'px';
    el.style.top = node.y + 'px';
    el.style.width = node.w + 'px';
    el.style.height = node.h + 'px';
    if (node.tip) el.title = node.tip;
    else el.removeAttribute('title');
    updateRole(el, node);
    el.style.color = node.fg || '';
    el.style.fontSize = (node.fs && !node.role) ? (node.fs + 'px') : '';
    el.style.fontWeight = (node.bold && !node.role) ? 'bold' : '';
  }

  function patchKids(el, node, state) {
    var kids = node.kids || [];
    for (var i = 0; i < kids.length; i++) {
      var child = nodeEls.get(kids[i].id);
      if (!child) return false;
      if (!patchNode(child, kids[i], state)) return false;
    }
    return true;
  }

  function tableSig(node) {
    return JSON.stringify([node.cols, node.hh, node.rhw, node.rows, node.cur,
      node.total, node.vmax, node.v, node.hmax, node.hv]);
  }

  function listSig(node) {
    return JSON.stringify([node.items, node.rh, node.total, node.vmax, node.v]);
  }

  function treeSig(node) {
    return JSON.stringify([node.items, node.rh, node.vmax, node.v]);
  }

  function patchNode(el, node, state) {
    el.__node = node;
    setBase(el, node);
    switch (node.k) {
      case 'page':
        el.classList.add('page');
        return patchKids(el, node, state);
      case 'label': {
        var img = el.querySelector(':scope > img');
        if (node.img) {
          if (!img) {
            el.replaceChildren();
            img = document.createElement('img');
            el.appendChild(img);
          }
          if (img.src !== node.img) img.src = node.img;
        } else {
          if (img) el.replaceChildren();
          if (el.textContent !== (node.t || '')) el.textContent = node.t || '';
        }
        el.classList.toggle('wrap', !!node.wrap);
        var align = node.al || '';
        el.style.display = 'flex';
        el.style.justifyContent = align.charAt(0) === 'c' ? 'center'
          : (align.charAt(0) === 'r' ? 'flex-end' : 'flex-start');
        el.style.alignItems = align.charAt(1) === 'b' ? 'flex-end'
          : (align.charAt(1) === 'c' ? 'center' : 'flex-start');
        return true;
      }
      case 'button':
      case 'toolbutton': {
        el.classList.toggle('primary', node.variant === 'primary');
        el.classList.toggle('danger', node.variant === 'danger');
        el.classList.toggle('secondary', node.variant === 'secondary');
        el.classList.toggle('ghost', node.variant === 'ghost');
        el.classList.toggle('off', !!node.off);
        var bimg = el.querySelector(':scope > img');
        if (node.icon) {
          if (!bimg) {
            bimg = document.createElement('img');
            bimg.alt = '';
            el.insertBefore(bimg, el.firstChild);
          }
          if (bimg.src !== node.icon) bimg.src = node.icon;
        } else if (bimg) {
          bimg.remove();
        }
        var txt = el.querySelector('.txt');
        if (node.t) {
          if (!txt) {
            txt = document.createElement('span');
            txt.className = 'txt';
            el.appendChild(txt);
          }
          if (txt.textContent !== node.t) txt.textContent = node.t;
        } else if (txt) {
          txt.remove();
        }
        if (node.off) {
          el.setAttribute('disabled', '');
          el.setAttribute('aria-disabled', 'true');
          el.tabIndex = -1;
        } else {
          el.removeAttribute('disabled');
          el.removeAttribute('aria-disabled');
          el.tabIndex = 0;
        }
        return true;
      }
      case 'check':
      case 'radio': {
        el.classList.toggle('on', !!node.chk);
        el.classList.toggle('off', !!node.off);
        var mark = el.querySelector(':scope > .box');
        if (!mark) return false;
        mark.textContent = node.chk ? (node.k === 'radio' ? '●' : '✓') : '';
        var label = el.querySelector(':scope > .txt');
        if (label && label.textContent !== (node.t || '')) {
          label.textContent = node.t || '';
        }
        el.setAttribute('aria-checked', node.chk ? 'true' : 'false');
        if (node.off) {
          el.setAttribute('aria-disabled', 'true');
          el.tabIndex = -1;
        } else {
          el.removeAttribute('aria-disabled');
          el.tabIndex = 0;
        }
        return true;
      }
      case 'line':
      case 'datetime': {
        var input = el.querySelector('input');
        if (!input) return false;
        if (document.activeElement !== input) {
          var value = node.t == null ? '' : String(node.t);
          if (input.value !== value) input.value = value;
        }
        input.readOnly = !!(node.ro || node.secret);
        input.placeholder = node.ph || '';
        input.title = node.secret ? '密码输入框，请在桌面端查看与修改' : '';
        return true;
      }
      case 'text':
      case 'plain': {
        var area = el.querySelector('textarea');
        if (!area) return false;
        if (document.activeElement !== area && area.value !== (node.t || '')) {
          area.value = node.t || '';
        }
        area.readOnly = !!node.ro;
        area.placeholder = node.ph || '';
        return true;
      }
      case 'combo': {
        var combo = el.querySelector('select');
        if (!combo) return false;
        var itemsSig = JSON.stringify(node.items || []);
        if (combo.__itemsSig !== itemsSig) {
          combo.replaceChildren();
          (node.items || []).forEach(function (text, index) {
            var op = document.createElement('option');
            op.value = String(index);
            op.textContent = text;
            combo.appendChild(op);
          });
          combo.__itemsSig = itemsSig;
        }
        if (document.activeElement !== combo) {
          combo.value = String(node.cur || 0);
        }
        return true;
      }
      case 'spin':
      case 'dspin': {
        var spin = el.querySelector('input');
        if (!spin) return false;
        spin.min = String(node.min);
        spin.max = String(node.max);
        spin.step = String(node.step || 1);
        if (document.activeElement !== spin && spin.value !== String(node.v)) {
          spin.value = String(node.v);
        }
        return true;
      }
      case 'slider': {
        var slider = el.querySelector('input');
        if (!slider) return false;
        slider.min = String(node.min);
        slider.max = String(node.max);
        if (document.activeElement !== slider && slider.value !== String(node.v)) {
          slider.value = String(node.v);
        }
        return true;
      }
      case 'progress': {
        var fill = el.querySelector('.fill');
        var ptext = el.querySelector('.ptext');
        if (!fill || !ptext) return false;
        var span = Math.max(0, (node.max - node.min) || 1);
        var ratio = Math.max(0, Math.min(1, (node.v - node.min) / span));
        fill.style.width = (ratio * 100) + '%';
        var text = node.text || (Math.round(ratio * 100) + '%');
        if (ptext.textContent !== text) ptext.textContent = text;
        return true;
      }
      case 'tabs': {
        var bar = el.querySelector(':scope > .tabbar');
        if (!bar) return false;
        var tabsSig = JSON.stringify(node.tabs || []);
        if (bar.__sig !== tabsSig) buildTabBar(bar, node);
        var buttons = bar.querySelectorAll('.tab');
        (node.tabs || []).forEach(function (tab, index) {
          if (!buttons[index]) return;
          buttons[index].classList.toggle('on', index === node.cur);
          buttons[index].disabled = !!tab.off;
        });
        return patchKids(el, node, state);
      }
      case 'scroll':
        if (!patchKids(el, node, state)) return false;
        updateScrollbar(el, node);
        return true;
      case 'table': {
        var tsig = tableSig(node);
        if (el.__dataSig !== tsig) {
          el.replaceChildren();
          fillTable(el, node);
        } else {
          updateScrollbar(el, node);
        }
        return true;
      }
      case 'list': {
        var lsig = listSig(node);
        if (el.__dataSig !== lsig) {
          el.replaceChildren();
          fillList(el, node);
        } else {
          updateScrollbar(el, node);
        }
        return true;
      }
      case 'tree': {
        var rsig = treeSig(node);
        if (el.__dataSig !== rsig) {
          el.replaceChildren();
          fillTree(el, node);
        } else {
          updateScrollbar(el, node);
        }
        return true;
      }
      case 'group': {
        var title = el.querySelector(':scope > .gtitle');
        if (title && title.textContent !== (node.t || '')) {
          title.textContent = node.t || '';
        }
        return patchKids(el, node, state);
      }
      case 'frame':
        el.classList.toggle('card', node.variant === 'card');
        el.classList.toggle('subtle', node.variant === 'subtle');
        el.classList.toggle('toolbarcard', node.variant === 'toolbar');
        return patchKids(el, node, state);
      default:
        return patchKids(el, node, state);
    }
  }

  function fillLabel(box, node) {
    var align = node.al || '';
    box.classList.add('label');
    if (node.wrap) box.classList.add('wrap');
    box.style.display = 'flex';
    box.style.justifyContent = align.charAt(0) === 'c' ? 'center'
      : (align.charAt(0) === 'r' ? 'flex-end' : 'flex-start');
    box.style.alignItems = align.charAt(1) === 'b' ? 'flex-end'
      : (align.charAt(1) === 'c' ? 'center' : 'flex-start');
    if (node.img) {
      var img = document.createElement('img');
      img.src = node.img;
      img.alt = '';
      box.appendChild(img);
      return;
    }
    box.textContent = node.t || '';
  }

  function fillButton(box, node) {
    box.classList.add(node.k === 'toolbutton' ? 'toolbtn' : 'btn');
    if (node.variant === 'primary') box.classList.add('primary');
    else if (node.variant === 'danger') box.classList.add('danger');
    else if (node.variant === 'secondary') box.classList.add('secondary');
    else if (node.variant === 'ghost') box.classList.add('ghost');
    if (node.icon) {
      var img = document.createElement('img');
      img.src = node.icon;
      img.alt = '';
      box.appendChild(img);
    }
    if (node.t) {
      var txt = document.createElement('span');
      txt.className = 'txt';
      txt.textContent = node.t;
      box.appendChild(txt);
    }
    box.setAttribute('role', 'button');
    if (node.off) {
      box.classList.add('off');
      box.setAttribute('aria-disabled', 'true');
      box.tabIndex = -1;
    } else {
      box.tabIndex = 0;
    }
    function activate() {
      var cur = box.__node || node;
      if (cur.off) return;
      if (cur.menu) {
        alert('该按钮带下拉菜单，请在桌面端操作。');
        return;
      }
      if (cur.hint === 'desktop' && !box.dataset.ack) {
        if (!confirm('该操作会在桌面端打开文件选择或其他对话框，继续执行？')) return;
        box.dataset.ack = '1';
      }
      send({ a: 'click', id: cur.id });
    }
    box.addEventListener('click', function (event) {
      event.stopPropagation();
      activate();
    });
    box.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        activate();
      }
    });
  }

  function fillCheck(box, node) {
    box.classList.add('chk');
    if (node.k === 'radio') box.classList.add('radio');
    if (node.chk) box.classList.add('on');
    if (node.off) box.classList.add('off');
    var mark = document.createElement('span');
    mark.className = 'box';
    mark.textContent = node.chk ? (node.k === 'radio' ? '●' : '✓') : '';
    var label = document.createElement('span');
    label.className = 'txt';
    label.textContent = node.t || '';
    box.appendChild(mark);
    box.appendChild(label);
    box.setAttribute('role', node.k === 'radio' ? 'radio' : 'checkbox');
    box.setAttribute('aria-checked', node.chk ? 'true' : 'false');
    if (node.off) {
      box.setAttribute('aria-disabled', 'true');
      box.tabIndex = -1;
    } else {
      box.tabIndex = 0;
    }
    function toggle() {
      var cur = box.__node || node;
      if (cur.off) return;
      send({ a: 'click', id: cur.id });
    }
    box.addEventListener('click', function (event) {
      event.stopPropagation();
      toggle();
    });
    box.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        toggle();
      }
    });
  }

  function scheduleText(node, input) {
    var old = timers.get(node.id);
    if (old) clearTimeout(old.timer);
    var timer = setTimeout(function () {
      timers.delete(node.id);
      send({ a: 'text', id: node.id, value: input.value });
    }, 350);
    timers.set(node.id, { timer: timer, input: input });
  }

  function flushText(node, input, commit) {
    var old = timers.get(node.id);
    if (old) {
      clearTimeout(old.timer);
      timers.delete(node.id);
    }
    send({ a: commit ? 'textCommit' : 'text', id: node.id, value: input.value });
  }

  function fillLine(box, node) {
    var input = document.createElement('input');
    input.className = 'input';
    input.type = 'text';
    input.spellcheck = false;
    input.value = node.t || '';
    if (node.ph) input.placeholder = node.ph;
    if (node.ro) input.readOnly = true;
    if (node.secret) {
      input.readOnly = true;
      input.title = '密码输入框，请在桌面端查看与修改';
    }
    if (focusState && focusState.id === node.id) input.value = focusState.value;
    if (!node.ro) {
      input.addEventListener('input', function () { scheduleText(node, input); });
      input.addEventListener('keydown', function (event) {
        if (event.key === 'Enter') {
          event.preventDefault();
          flushText(node, input, true);
        }
      });
      input.addEventListener('blur', function () { flushText(node, input, false); });
    }
    box.appendChild(input);
  }

  function fillArea(box, node) {
    var area = document.createElement('textarea');
    area.className = 'area';
    area.spellcheck = false;
    area.value = node.t || '';
    if (node.ph) area.placeholder = node.ph;
    if (node.ro) area.readOnly = true;
    if (node.cls && node.cls.indexOf('Plain') >= 0) area.classList.add('mono');
    if (focusState && focusState.id === node.id) area.value = focusState.value;
    if (!node.ro) {
      area.addEventListener('input', function () { scheduleText(node, area); });
      area.addEventListener('blur', function () { flushText(node, area, false); });
    }
    box.appendChild(area);
  }

  function fillCombo(box, node) {
    var combo = document.createElement('select');
    combo.className = 'combo';
    var items = node.items || [];
    for (var i = 0; i < items.length; i++) {
      var op = document.createElement('option');
      op.value = String(i);
      op.textContent = items[i];
      combo.appendChild(op);
    }
    combo.value = String(node.cur || 0);
    combo.__itemsSig = JSON.stringify(items);
    combo.addEventListener('change', function () {
      send({ a: 'index', id: box.__node.id, value: Number(combo.value) });
    });
    box.appendChild(combo);
  }

  function fillSpin(box, node) {
    var input = document.createElement('input');
    input.className = 'input';
    input.type = 'number';
    input.value = String(node.v);
    input.min = String(node.min);
    input.max = String(node.max);
    input.step = String(node.step || 1);
    var tip = (node.prefix || '') + '数值' + (node.suffix || '');
    input.title = tip;
    input.addEventListener('change', function () {
      send({ a: 'value', id: node.id, value: Number(input.value) });
    });
    box.appendChild(input);
  }

  function fillSlider(box, node) {
    var input = document.createElement('input');
    input.className = 'slider';
    input.type = 'range';
    input.min = String(node.min);
    input.max = String(node.max);
    input.value = String(node.v);
    input.addEventListener('change', function () {
      send({ a: 'value', id: node.id, value: Number(input.value) });
    });
    box.appendChild(input);
  }

  function fillProgress(box, node) {
    box.classList.add('progress');
    var fill = document.createElement('div');
    fill.className = 'fill';
    var span = Math.max(0, (node.max - node.min) || 1);
    var ratio = Math.max(0, Math.min(1, (node.v - node.min) / span));
    fill.style.width = (ratio * 100) + '%';
    var label = document.createElement('span');
    label.className = 'ptext';
    label.textContent = node.text || (Math.round(ratio * 100) + '%');
    box.appendChild(fill);
    box.appendChild(label);
  }

  function buildTabBar(bar, node) {
    bar.replaceChildren();
    bar.style.height = (node.barh || 30) + 'px';
    bar.style.width = '100%';
    (node.tabs || []).forEach(function (tab, index) {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'tab' + (index === node.cur ? ' on' : '');
      if (tab.off) button.disabled = true;
      if (tab.icon) {
        var img = document.createElement('img');
        img.src = tab.icon;
        img.alt = '';
        button.appendChild(img);
      }
      var text = document.createElement('span');
      text.textContent = tab.t || '';
      button.appendChild(text);
      button.addEventListener('click', function () {
        send({ a: 'tab', id: bar.__nodeId, value: index });
      });
      bar.appendChild(button);
    });
    bar.__nodeId = node.id;
    bar.__sig = JSON.stringify(node.tabs || []);
  }

  function fillTabs(box, node, state) {
    box.classList.add('tabs');
    var bar = document.createElement('div');
    bar.className = 'tabbar';
    bar.__nodeId = node.id;
    buildTabBar(bar, node);
    box.appendChild(bar);
    appendKids(box, node, state);
  }

  function fillScroll(box, node, state) {
    box.classList.add('scroll');
    appendKids(box, node, state);
    attachWheel(box);
    updateScrollbar(box, node);
  }

  function updateScrollbar(box, node) {
    var old = box.querySelector(':scope > .gsbar');
    if (old) old.remove();
    if (!node.vmax) return;
    var trackH = Math.max(10, (node.h || 0) - 6);
    var page = node.page || 0;
    var ratio = page > 0 ? page / (node.vmax + page) : 0.18;
    var thumbH = Math.max(24, Math.min(trackH, trackH * ratio));
    var top = 3 + (node.v / (node.vmax || 1)) * Math.max(0, trackH - thumbH);
    var bar = document.createElement('div');
    bar.className = 'gsbar';
    bar.style.top = top + 'px';
    bar.style.height = thumbH + 'px';
    box.appendChild(bar);
  }

  function attachWheel(box) {
    if (box.__wheelBound) return;
    box.__wheelBound = true;
    var pendingY = 0, pendingX = 0, timer = null;
    function flush() {
      timer = null;
      var node = box.__node;
      if (!node || (!pendingY && !pendingX)) return;
      send({ a: 'scroll', id: node.id, dy: Math.round(pendingY), dx: Math.round(pendingX) });
      pendingY = 0;
      pendingX = 0;
    }
    box.addEventListener('wheel', function (event) {
      var node = box.__node;
      if (!node) return;
      var unit = event.deltaMode === 1 ? 16 : 1;
      var dy = event.deltaY * unit;
      var dx = event.deltaX * unit;
      if (event.shiftKey && !dx) { dx = dy; dy = 0; }
      if (!node.vmax && dy) return;
      if (!node.hmax && dx) return;
      event.preventDefault();
      pendingY += dy;
      pendingX += dx;
      if (timer) clearTimeout(timer);
      timer = setTimeout(flush, 80);
    }, { passive: false });
  }

  function fillTable(box, node) {
    box.classList.add('tblwrap');
    var table = document.createElement('table');
    table.className = 'tbl';
    var vp = node.vp || [0, 0, node.w, node.h];
    table.style.position = 'absolute';
    table.style.left = vp[0] + 'px';
    table.style.top = vp[1] + 'px';
    var cols = node.cols || [];
    var totalW = 0;
    for (var ci = 0; ci < cols.length; ci++) totalW += cols[ci].w;
    table.style.width = Math.max(vp[2], totalW) + 'px';

    var thead = document.createElement('thead');
    var htr = document.createElement('tr');
    htr.style.height = (node.hh || 24) + 'px';
    cols.forEach(function (col) {
      var th = document.createElement('th');
      th.style.width = col.w + 'px';
      th.textContent = col.t || '';
      th.title = col.t || '';
      th.addEventListener('click', function () {
        send({ a: 'headerClick', id: node.id, c: col.c });
      });
      htr.appendChild(th);
    });
    thead.appendChild(htr);
    table.appendChild(thead);

    var tbody = document.createElement('tbody');
    var rows = node.rows || [];
    var current = node.cur || [-1, -1];
    rows.forEach(function (row) {
      var tr = document.createElement('tr');
      tr.style.height = (row.h || 24) + 'px';
      if (row.r === current[0]) tr.className = 'sel';
      (row.cells || []).forEach(function (cell) {
        var td = document.createElement('td');
        if ('cs' in cell) {
          var mark = document.createElement('span');
          mark.className = 'cellchk' + (cell.cs === 2 ? ' on' : '');
          mark.textContent = cell.cs === 2 ? '✓' : (cell.cs === 1 ? '－' : '');
          mark.addEventListener('click', function (event) {
            event.stopPropagation();
            send({ a: 'cellCheck', id: node.id, r: row.r, c: cell.c,
                   value: cell.cs === 2 ? 0 : 1 });
          });
          td.appendChild(mark);
        }
        td.appendChild(document.createTextNode(cell.t || ''));
        td.addEventListener('click', function () {
          send({ a: 'cellClick', id: node.id, r: row.r, c: cell.c });
        });
        td.addEventListener('dblclick', function () {
          send({ a: 'cellDblClick', id: node.id, r: row.r, c: cell.c });
        });
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    box.appendChild(table);
    attachWheel(box);
    updateScrollbar(box, node);
    box.__dataSig = tableSig(node);
  }

  function rowShell(box, node, index, height, text, extraClass) {
    var vp = node.vp || [0, 0, node.w, node.h];
    var row = document.createElement('div');
    row.className = 'row' + (extraClass ? (' ' + extraClass) : '');
    row.style.left = vp[0] + 'px';
    row.style.top = (vp[1] + index * height) + 'px';
    row.style.width = vp[2] + 'px';
    row.style.height = height + 'px';
    box.appendChild(row);
    return row;
  }

  function fillList(box, node) {
    box.classList.add('listwrap');
    var rh = node.rh || 24;
    var items = node.items || [];
    var multi = rh >= 34;
    items.forEach(function (item, index) {
      var row = rowShell(box, node, index, rh);
      if (multi) row.classList.add('multi');
      if ('cs' in item) {
        var mark = document.createElement('span');
        mark.className = 'cellchk' + (item.cs === 2 ? ' on' : '');
        mark.textContent = item.cs === 2 ? '✓' : (item.cs === 1 ? '－' : '');
        mark.addEventListener('click', function (event) {
          event.stopPropagation();
          send({ a: 'itemCheck', id: node.id, r: item.r,
                 value: item.cs === 2 ? 0 : 1 });
        });
        row.appendChild(mark);
      }
      if (item.icon) {
        var img = document.createElement('img');
        img.src = item.icon;
        img.alt = '';
        row.appendChild(img);
      }
      var text = document.createElement('span');
      text.textContent = item.t || '';
      text.style.overflow = 'hidden';
      text.style.textOverflow = 'ellipsis';
      row.appendChild(text);
      row.addEventListener('click', function () {
        send({ a: 'itemClick', id: node.id, r: item.r });
      });
      row.addEventListener('dblclick', function () {
        send({ a: 'itemDblClick', id: node.id, r: item.r });
      });
    });
    attachWheel(box);
    updateScrollbar(box, node);
    box.__dataSig = listSig(node);
  }

  function fillTree(box, node) {
    box.classList.add('listwrap');
    var rh = node.rh || 22;
    var items = node.items || [];
    items.forEach(function (item, index) {
      var row = rowShell(box, node, index, rh);
      row.style.paddingLeft = (6 + (item.lv || 0) * 14) + 'px';
      var exp = document.createElement('span');
      exp.className = 'exp';
      exp.textContent = item.kids ? (item.exp ? '▾' : '▸') : '';
      row.appendChild(exp);
      if ('cs' in item) {
        var mark = document.createElement('span');
        mark.className = 'cellchk' + (item.cs === 2 ? ' on' : '');
        mark.textContent = item.cs === 2 ? '✓' : (item.cs === 1 ? '－' : '');
        mark.addEventListener('click', function (event) {
          event.stopPropagation();
          send({ a: 'itemCheck', id: node.id, p: item.p,
                 value: item.cs === 2 ? 0 : 1 });
        });
        row.appendChild(mark);
      }
      if (item.icon) {
        var img = document.createElement('img');
        img.src = item.icon;
        img.alt = '';
        row.appendChild(img);
      }
      var text = document.createElement('span');
      text.textContent = item.t || '';
      row.appendChild(text);
      row.addEventListener('click', function () {
        send({ a: 'itemClick', id: node.id, p: item.p });
      });
      row.addEventListener('dblclick', function () {
        send({ a: 'itemDblClick', id: node.id, p: item.p });
      });
    });
    attachWheel(box);
    updateScrollbar(box, node);
    box.__dataSig = treeSig(node);
  }

  function fillGroup(box, node, state) {
    box.classList.add('group');
    if (node.t) {
      var title = document.createElement('span');
      title.className = 'gtitle';
      title.textContent = node.t;
      box.appendChild(title);
    }
    appendKids(box, node, state);
  }

  function pollState() {
    fetch(api('/api/state'), { cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error(String(r.status));
      return r.json();
    }).then(function (data) {
      render(data);
      setConn('已连接');
    }).catch(function () {
      setConn('连接中断', true);
    });
  }

  function connect() {
    if (es) es.close();
    es = new EventSource(api('/api/events?since=' + version));
    es.addEventListener('state', function (event) {
      try {
        render(JSON.parse(event.data));
        setConn('已连接');
      } catch (e) { setConn('快照解析失败', true); }
    });
    es.onopen = function () { setConn('已连接'); };
    es.onerror = function () { setConn('连接中断，正在重试', true); };
  }

  select.addEventListener('change', function () {
    if (!snap || !select.value || select.value === snap.page) return;
    setConn('切换中…');
    send({ a: 'page', key: select.value });
  });

  $('refresh').addEventListener('click', pollState);
  zoomFit.addEventListener('click', function () {
    fitMode = true;
    syncScale();
  });
  $('zoomOut').addEventListener('click', function () {
    fitMode = false;
    zoom = Math.max(0.4, Math.round((scale - 0.1) * 10) / 10);
    syncScale();
  });
  $('zoomIn').addEventListener('click', function () {
    fitMode = false;
    zoom = Math.min(2, Math.round((scale + 0.1) * 10) / 10);
    syncScale();
  });
  window.addEventListener('resize', syncScale);

  setInterval(function () {
    if (Date.now() - lastAt > 10000) pollState();
  }, 6000);

  loadPages();
  pollState();
  connect();
})();
