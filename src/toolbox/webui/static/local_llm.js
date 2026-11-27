(function (global) {
  'use strict';

  function sizeText(bytes) {
    var value = Number(bytes || 0);
    return value >= 1000000000 ? (value / 1000000000).toFixed(2) + ' GB'
      : (value / 1000000).toFixed(1) + ' MB';
  }

  function searchableModelList(h, select, label) {
    if (select.options.length <= 12) return select;
    var search = h('input', {
      class: 'input', type: 'search', 'aria-label': label + '搜索',
      placeholder: '输入名称筛选模型', style: 'min-width:140px;flex:1 1 140px'
    });
    var options = Array.prototype.slice.call(select.options);
    function filter() {
      var query = search.value.trim().toLocaleLowerCase();
      options.forEach(function (option) {
        option.hidden = option.value !== select.value && query !== ''
          && option.textContent.toLocaleLowerCase().indexOf(query) < 0;
      });
    }
    search.addEventListener('input', filter);
    select.addEventListener('change', filter);
    return h('div', {
      class: 'grow', style: 'display:flex;flex-wrap:wrap;gap:8px;min-width:0'
    }, search, select);
  }

  function createOptionsForm(h, fields, values, runtime) {
    values = values || {};
    runtime = runtime || {};
    var controls = {};
    var panel = h('div', { class: 'form-grid', 'data-local-options': 'true' });
    fields.forEach(function (field) {
      var value = values[field.key] == null ? field.default : values[field.key];
      var input;
      if (field.type === 'select' || field.type === 'model') {
        input = h('select', { class: 'select', name: field.key });
        var choices = field.type === 'model'
          ? (runtime.models || []).map(function (row) {
            return [row.id, (row.name || row.id) + ' · ' + sizeText(row.size)];
          }) : (field.choices || []);
        if (field.type === 'model' && !choices.length) {
          choices.push(['', '尚未安装模型，请在全局设置准备']);
        }
        if (value && !choices.some(function (row) { return row[0] === value; })) {
          choices.unshift([value, value + '（尚未安装）']);
        }
        choices.forEach(function (choice) {
          input.appendChild(h('option', { value: choice[0] }, choice[1]));
        });
        input.value = String(value);
      } else {
        input = h('input', {
          class: 'input', name: field.key, type: 'number', value: String(value),
          min: String(field.min), max: String(field.max), step: String(field.step || 1)
        });
      }
      controls[field.key] = input;
      panel.appendChild(h('label', null,
        h('span', { class: 'muted small' }, field.label),
        field.type === 'model' ? searchableModelList(h, input, field.label) : input));
    });
    function sync() {
      if (controls.gpu_layers) {
        controls.gpu_layers.disabled = controls.backend.value === 'cpu';
        controls.gpu_layers.title = controls.backend.value === 'cpu'
          ? 'CPU 模块不使用 GPU 卸载，切换到 GPU 后生效' : '';
      }
    }
    if (controls.backend) controls.backend.addEventListener('change', sync);
    sync();
    return {
      element: panel,
      read: function () {
        var result = {};
        fields.forEach(function (field) {
          var value = controls[field.key].value;
          result[field.key] = field.type === 'int' || field.type === 'float'
            ? Number(value) : value;
        });
        return result;
      },
      setDisabled: function (disabled) {
        Object.keys(controls).forEach(function (key) { controls[key].disabled = disabled; });
        if (!disabled) sync();
      }
    };
  }

  function mountSettings(host, helpers) {
    var h = helpers.h, api = helpers.api, post = helpers.post;
    var toast = helpers.toast, watchJob = helpers.watchJob;
    var busy = false;

    function load() {
      host.replaceChildren(h('div', { class: 'card' }, '正在检测可选本地推理组件…'));
      api('/api/system/llm').then(function (data) {
        var card = h('div', { class: 'card', style: 'margin-top:12px',
          'data-local-llm-settings': 'true' });
        var actions = [];
        var stateLine = h('div', { class: 'muted small', style: 'margin-top:8px' });
        var jobHost = h('div');
        card.appendChild(h('div', { class: 'row-title', style: 'margin-bottom:8px' },
          '可选本地推理组件'));
        card.appendChild(h('div', { class: 'muted small', style: 'margin-bottom:12px' },
          data.note || 'CPU / GPU 模块和模型独立下载，标准版与 FULL 版均不内置。'));
        if (!data.supported) {
          card.appendChild(h('div', { class: 'muted small' },
            '当前系统不支持这些运行组件，请使用模型接口或本地规则模式。'));
        }

        function syncActions() {
          actions.forEach(function (item) {
            item.button.disabled = !data.supported || busy || !item.available();
          });
        }
        function track(button, available) {
          actions.push({ button: button, available: available || function () { return true; } });
          syncActions();
          return button;
        }
        function start(action, payload, button) {
          if (busy) return;
          busy = true;
          syncActions();
          stateLine.textContent = '正在准备任务…';
          post('/api/system/llm/' + action, payload).then(function (result) {
            watchJob(jobHost, result.job, function (job) {
              stateLine.textContent = (job.result || {}).summary || '已完成';
              toast(stateLine.textContent, 'success');
            }, {
              cancelable: true, indeterminate: true,
              onSettled: function (job) {
                busy = false;
                if (job.status === 'done') {
                  load();
                } else {
                  syncActions();
                  stateLine.textContent = job.status === 'cancelled'
                    ? '已取消，可重新开始' : '失败：' + (job.error || '请检查日志后重试');
                }
              }
            });
          }).catch(function (error) {
            busy = false;
            syncActions();
            stateLine.textContent = '准备失败：' + error.message;
            toast(stateLine.textContent, 'error');
            if (button) button.focus();
          });
        }

        (data.runtimes || []).forEach(function (item) {
          var install = track(h('button', { class: 'btn', type: 'button' },
            item.installed ? '校验模块' : '下载模块'));
          var remove = track(h('button', { class: 'btn danger', type: 'button' }, '卸载模块'),
            function () { return item.installed; });
          install.addEventListener('click', function () {
            start('install-runtime', { backend: item.id }, install);
          });
          remove.addEventListener('click', function () {
            if (global.confirm('卸载「' + item.label
                + '」模块？只删除本软件下载的组件，模型和配置保留，可重新下载。')) {
              start('remove-runtime', { id: item.id, confirmed: true }, remove);
            }
          });
          card.appendChild(h('div', { class: 'toolbar', style: 'margin-bottom:8px' },
            h('span', { class: 'grow' }, item.label + ' · '
              + (item.installed ? '已安装' : '未安装') + ' · 下载约 '
              + sizeText(item.download_size || item.size)),
            install, remove));
        });

        var available = h('select', { class: 'select grow', 'aria-label': '下载模型' });
        (data.model_catalog || []).forEach(function (item) {
          available.appendChild(h('option', { value: item.id },
            item.label + ' · ' + sizeText(item.size)));
        });
        var download = track(h('button', { class: 'btn', type: 'button' }, '下载模型'),
          function () { return available.options.length > 0; });
        download.addEventListener('click', function () {
          start('download-model', { model: available.value }, download);
        });
        card.appendChild(h('div', { class: 'toolbar', style: 'margin-top:12px' },
          h('span', { class: 'muted small' }, '推荐模型'), available, download));

        var sources = h('select', { class: 'select grow', 'aria-label': '导入模型来源' },
          h('option', { value: '' }, '正在查找工作目录内的 GGUF 文件…'));
        var sourcesHost = h('div', { class: 'grow', style: 'min-width:0' }, sources);
        var importBtn = track(h('button', { class: 'btn', type: 'button' }, '导入模型'),
          function () { return Boolean(sources.value); });
        var sourceNote = h('div', { class: 'muted small', style: 'margin-top:6px' });
        api('/api/system/llm/sources').then(function (sourceData) {
          sources.replaceChildren();
          (sourceData.items || []).forEach(function (item) {
            sources.appendChild(h('option', { value: item.id }, item.name));
          });
          if (!sources.options.length) sources.appendChild(h('option', { value: '' },
            '工作目录内没有可导入的 GGUF 模型'));
          sourcesHost.replaceChildren(searchableModelList(h, sources, '导入模型来源'));
          syncActions();
          sourceNote.textContent = sourceData.note || '';
        }).catch(function (error) {
          sources.replaceChildren(h('option', { value: '' }, '读取失败，请刷新后重试'));
          syncActions();
          sourceNote.textContent = error.message;
        });
        sources.addEventListener('change', syncActions);
        importBtn.addEventListener('click', function () {
          start('import-model', { source: sources.value }, importBtn);
        });
        card.appendChild(h('div', { class: 'toolbar', style: 'margin-top:8px' },
          h('span', { class: 'muted small' }, '本地 GGUF'), sourcesHost, importBtn));
        card.appendChild(sourceNote);

        var installed = h('select', { class: 'select grow', 'aria-label': '已安装模型' });
        (data.models || []).forEach(function (item) {
          installed.appendChild(h('option', { value: item.id },
            (item.name || item.id) + ' · ' + sizeText(item.size)));
        });
        if (!installed.options.length) installed.appendChild(h('option', { value: '' }, '尚未安装模型'));
        var deleteBtn = track(h('button', { class: 'btn danger', type: 'button' }, '删除模型'),
          function () { return Boolean(installed.value); });
        installed.addEventListener('change', syncActions);
        deleteBtn.addEventListener('click', function () {
          if (global.confirm('删除所选模型及其分片？只删除本软件管理的副本，导入源文件保留，可重新下载。')) {
            start('remove-model', { id: installed.value, confirmed: true }, deleteBtn);
          }
        });
        var refresh = track(h('button', { class: 'btn', type: 'button' }, '刷新'));
        refresh.addEventListener('click', load);
        card.appendChild(h('div', { class: 'toolbar', style: 'margin-top:12px' },
          h('span', { class: 'muted small' }, '已安装模型'),
          searchableModelList(h, installed, '已安装模型'), deleteBtn, refresh));
        card.appendChild(h('div', { class: 'muted small', style: 'margin-top:8px' },
          'GPU 模块需要支持 Vulkan 的显卡驱动；模块与模型可分别选择，不会自动改用其他模块。'));
        card.appendChild(stateLine);
        card.appendChild(jobHost);
        host.replaceChildren(card);
      }).catch(function (error) {
        var retry = h('button', { class: 'btn', type: 'button' }, '重试');
        retry.addEventListener('click', load);
        host.replaceChildren(h('div', { class: 'card' },
          h('div', { class: 'error' }, '组件状态读取失败：' + error.message), retry));
      });
    }
    load();
  }

  function mountQuickActions(host, helpers, kind) {
    var h = helpers.h, busy = false, snapshot = null, request = 0;
    var controls = h('div', { class: 'toolbar', style: 'margin-top:8px' });
    var stateLine = h('div', { class: 'small', role: 'status', 'aria-live': 'polite' });
    var jobHost = h('div');
    host.replaceChildren(controls, stateLine, jobHost);

    function sync() {
      Array.prototype.forEach.call(controls.querySelectorAll('button,select'), function (control) {
        control.disabled = busy || !snapshot || !snapshot.supported
          || control.dataset.available === 'false';
      });
    }
    function start(action, payload, button) {
      if (busy) return;
      busy = true;
      sync();
      stateLine.textContent = '正在准备任务…';
      jobHost.replaceChildren(h('div', {
        class: 'progress is-indeterminate', role: 'progressbar', 'aria-label': '准备本地总结组件任务'
      }, h('i', { style: 'width:35%' })));
      helpers.post('/api/system/llm/' + action, payload).then(function (result) {
        helpers.watchJob(jobHost, result.job, function (job) {
          stateLine.textContent = (job.result || {}).summary || '已完成';
        }, {
          cancelable: true, indeterminate: true,
          onSettled: function (job) {
            busy = false;
            sync();
            if (job.status === 'done') {
              helpers.toast((job.result || {}).summary || '已完成', 'success');
              load();
              if (helpers.onChanged) helpers.onChanged();
            } else {
              stateLine.textContent = job.status === 'cancelled' ? '已取消，可重新开始'
                : '失败：' + (job.error || '请检查网络后重试');
              if (job.status === 'error') button.textContent = '重试';
            }
          }
        });
      }).catch(function (error) {
        busy = false;
        sync();
        jobHost.replaceChildren();
        stateLine.textContent = '准备失败：' + error.message;
        button.textContent = '重试';
      });
    }
    function draw(data) {
      snapshot = data;
      if (helpers.onStatus) helpers.onStatus(data);
      controls.replaceChildren();
      var isRuntime = kind === 'runtime';
      var choices = h('select', {
        class: 'select grow', 'aria-label': isRuntime ? '本地总结模块' : '本地总结推荐模型',
        style: 'min-width:140px;max-width:100%'
      });
      (isRuntime ? data.runtimes || [] : data.model_catalog || []).forEach(function (item) {
        choices.appendChild(h('option', { value: item.id }, item.label + ' · '
          + sizeText(item.download_size || item.size)));
      });
      var download = h('button', { class: 'btn', type: 'button' }, '下载');
      download.dataset.available = String(choices.options.length > 0);
      download.addEventListener('click', function () {
        start(isRuntime ? 'install-runtime' : 'download-model',
          isRuntime ? { backend: choices.value } : { model: choices.value }, download);
      });
      controls.appendChild(choices);
      controls.appendChild(download);
      var installed = isRuntime ? choices : h('select', {
        class: 'select grow', 'aria-label': '本地总结已安装模型', style: 'min-width:140px;max-width:100%'
      });
      if (!isRuntime) {
        (data.models || []).forEach(function (item) {
          installed.appendChild(h('option', { value: item.id }, (item.name || item.id)
            + ' · ' + sizeText(item.size)));
        });
        if (!installed.options.length) installed.appendChild(h('option', { value: '' }, '尚未安装模型'));
        controls.appendChild(searchableModelList(h, installed, '本地总结已安装模型'));
      }
      var remove = h('button', { class: 'btn danger', type: 'button' }, '卸载');
      function syncRemoval() {
        remove.dataset.available = String(isRuntime
          ? (data.runtimes || []).some(function (item) { return item.id === installed.value && item.installed; })
          : Boolean(installed.value));
        sync();
      }
      installed.addEventListener('change', syncRemoval);
      remove.addEventListener('click', function () {
        var name = installed.selectedOptions.length ? installed.selectedOptions[0].textContent : '';
        if (global.confirm('卸载「' + name + '」？只删除本软件管理的组件副本，'
            + (isRuntime ? '模型与配置' : '导入源文件与推理模块') + '保留，可重新下载。')) {
          start(isRuntime ? 'remove-runtime' : 'remove-model',
            { id: installed.value, confirmed: true }, remove);
        }
      });
      controls.appendChild(remove);
      syncRemoval();
      if (!data.supported) stateLine.textContent = '当前系统不支持安装这些本地组件';
    }
    function load() {
      var serial = ++request;
      helpers.api('/api/system/llm').then(function (data) {
        if (serial === request && host.isConnected) draw(data);
      }).catch(function (error) {
        if (serial !== request || !host.isConnected) return;
        stateLine.textContent = '组件状态读取失败：' + error.message;
        var retry = h('button', { class: 'btn', type: 'button' }, '重试');
        retry.addEventListener('click', load);
        controls.replaceChildren(retry);
      });
    }
    load();
  }

  global.FMJJLocalLLM = { createOptionsForm: createOptionsForm, mountSettings: mountSettings,
    mountQuickActions: mountQuickActions };
}(window));
