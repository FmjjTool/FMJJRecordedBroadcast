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

  var snap = null, version = 0, lastSig = '', lastAt = 0, es = null;
  var fitMode = true, zoom = 1, scale = 1;
  var focusState = null;
  var timers = new Map();

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
    var sig = data.page + '|' + data.size.join('x') + '|' + JSON.stringify(data.root);
    if (sig === lastSig) {
      updateStatus();
      return;
    }
    lastSig = sig;
    saveFocus();
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
      box.setAttribute('aria-disabled', 'true');
      box.tabIndex = -1;
    } else {
      box.tabIndex = 0;
    }
    function activate() {
      if (node.off) return;
      if (node.menu) {
        alert('该按钮带下拉菜单，请在桌面端操作。');
        return;
      }
      if (node.hint === 'desktop' && !box.dataset.ack) {
        if (!confirm('该操作会在桌面端打开文件选择或其他对话框，继续执行？')) return;
        box.dataset.ack = '1';
      }
      send({ a: 'click', id: node.id });
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
      if (node.off) return;
      send({ a: 'click', id: node.id });
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
    combo.addEventListener('change', function () {
      send({ a: 'index', id: node.id, value: Number(combo.value) });
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

  function fillTabs(box, node, state) {
    box.classList.add('tabs');
    var bar = document.createElement('div');
    bar.className = 'tabbar';
    bar.style.height = (node.barh || 30) + 'px';
    bar.style.width = '100%';
    var tabs = node.tabs || [];
    tabs.forEach(function (tab, index) {
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
        send({ a: 'tab', id: node.id, value: index });
      });
      bar.appendChild(button);
    });
    box.appendChild(bar);
    appendKids(box, node, state);
  }

  function fillScroll(box, node, state) {
    box.classList.add('scroll');
    appendKids(box, node, state);
    attachWheel(box, node);
  }

  function attachWheel(box, node) {
    if (!node.vmax && !node.hmax) return;
    if (node.vmax > 0) {
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
    var pendingY = 0, pendingX = 0, timer = null;
    function flush() {
      timer = null;
      if (!pendingY && !pendingX) return;
      send({ a: 'scroll', id: node.id, dy: Math.round(pendingY), dx: Math.round(pendingX) });
      pendingY = 0;
      pendingX = 0;
    }
    box.addEventListener('wheel', function (event) {
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
      timer = setTimeout(flush, 60);
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
          mark.className = 'cellchk' + (cell.cs === 1 ? ' on' : '');
          mark.textContent = cell.cs === 1 ? '✓' : (cell.cs === 2 ? '－' : '');
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
    attachWheel(box, node);
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
    items.forEach(function (item, index) {
      var row = rowShell(box, node, index, rh);
      if ('cs' in item) {
        var mark = document.createElement('span');
        mark.className = 'cellchk' + (item.cs === 1 ? ' on' : '');
        mark.textContent = item.cs === 1 ? '✓' : (item.cs === 2 ? '－' : '');
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
    attachWheel(box, node);
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
        mark.className = 'cellchk' + (item.cs === 1 ? ' on' : '');
        mark.textContent = item.cs === 1 ? '✓' : (item.cs === 2 ? '－' : '');
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
    attachWheel(box, node);
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
