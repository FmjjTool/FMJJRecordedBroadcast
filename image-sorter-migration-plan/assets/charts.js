(function () {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();
  var rewrite = style.getPropertyValue('--rewrite').trim();
  var keep = style.getPropertyValue('--keep').trim();

  // ---- Chart 1: 源项目各模块代码量 ----
  var el1 = document.getElementById('chart-volume');
  if (el1) {
    var c1 = echarts.init(el1, null, { renderer: 'svg' });
    var mods = [
      ['utils+config 杂项', 500, 'mod'],
      ['logger.py（删）', 97, 'drop'],
      ['theme_manager.py（删）', 130, 'drop'],
      ['runtime_paths.py（重写）', 59, 'rewrite'],
      ['constants.py（改造）', 94, 'mod'],
      ['app_config.py（重写）', 347, 'rewrite'],
      ['media_hash_manager.py', 62, 'keep'],
      ['folder_manager.py', 76, 'keep'],
      ['operation_history.py', 208, 'keep'],
      ['file_processor.py', 203, 'keep'],
      ['sqlite_cache.py', 214, 'keep'],
      ['file_scanner.py', 273, 'keep'],
      ['shortcut_mapping_dialog.py（重写）', 166, 'rewrite'],
      ['feedback_manager.py（删）', 80, 'drop'],
      ['md5_filter_thread.py（改造）', 123, 'mod'],
      ['media_compress_thread.py（改造）', 185, 'mod'],
      ['duplicate_detector.py（改造）', 229, 'mod'],
      ['thumbnail_generator.py（改造）', 248, 'mod'],
      ['cache_manager.py（改造）', 361, 'mod'],
      ['shortcut_manager.py（改造）', 240, 'mod'],
      ['media_info_extractor.py（大改）', 518, 'mod'],
      ['media_viewer.py（重写）', 659, 'rewrite'],
      ['lazy_load_mixin.py（改造）', 964, 'mod'],
      ['file_list_panel.py（重写）', 1141, 'rewrite'],
      ['folder_panel.py（重写）', 1183, 'rewrite'],
      ['file_info_panel.py（重写）', 1586, 'rewrite'],
      ['main_window.py（重写）', 3275, 'rewrite']
    ];
    var colorMap = { keep: keep, mod: accent, rewrite: rewrite, drop: muted };
    c1.setOption({
      animation: false,
      tooltip: { trigger: 'item', appendToBody: true, formatter: function (p) { return p.name + '：' + p.value + ' 行'; } },
      grid: { left: 250, right: 60, top: 10, bottom: 30 },
      xAxis: {
        type: 'value',
        axisLabel: { color: muted, fontSize: 11 },
        splitLine: { lineStyle: { color: rule } }
      },
      yAxis: {
        type: 'category',
        data: mods.map(function (m) { return m[0]; }),
        axisLabel: { color: ink, fontSize: 11 },
        axisLine: { lineStyle: { color: rule } },
        axisTick: { show: false }
      },
      series: [{
        type: 'bar',
        data: mods.map(function (m) { return { value: m[1], itemStyle: { color: colorMap[m[2]], opacity: m[2] === 'drop' ? 0.45 : 1 } }; }),
        barWidth: 13,
        itemStyle: { borderRadius: [0, 2, 2, 0] },
        label: { show: true, position: 'right', color: muted, fontSize: 10, formatter: '{c}' }
      }]
    });
    window.addEventListener('resize', function () { c1.resize(); });
  }

  // ---- Chart 2: 依赖体积增量 ----
  var el2 = document.getElementById('chart-size');
  if (el2) {
    var c2 = echarts.init(el2, null, { renderer: 'svg' });
    c2.setOption({
      animation: false,
      tooltip: { trigger: 'axis', appendToBody: true, valueFormatter: function (v) { return v + ' MB'; } },
      legend: { data: ['解压包增量', '安装器增量'], textStyle: { color: muted, fontSize: 11 }, top: 0 },
      grid: { left: 50, right: 30, top: 40, bottom: 40 },
      xAxis: {
        type: 'category',
        data: ['pillow-heif\n(引入)', 'QtMultimedia\n(引入)', 'opencv-python\n(不引入，省)', '净增量'],
        axisLabel: { color: ink, fontSize: 11, interval: 0 },
        axisLine: { lineStyle: { color: rule } }
      },
      yAxis: {
        type: 'value',
        name: 'MB',
        nameTextStyle: { color: muted },
        axisLabel: { color: muted, fontSize: 11 },
        splitLine: { lineStyle: { color: rule } }
      },
      series: [
        {
          name: '解压包增量', type: 'bar', barWidth: 34,
          data: [12, 25, -95, -58],
          itemStyle: { color: function (p) { return p.value >= 0 ? accent : muted; }, borderRadius: 3, opacity: 0.9 },
          label: { show: true, position: 'top', color: ink, fontSize: 10, formatter: function (p) { return p.value > 0 ? '+' + p.value : String(p.value); } }
        },
        {
          name: '安装器增量', type: 'bar', barWidth: 34,
          data: [5, 10, -40, -25],
          itemStyle: { color: function (p) { return p.value >= 0 ? accent2 : muted; }, borderRadius: 3, opacity: 0.9 },
          label: { show: true, position: 'top', color: ink, fontSize: 10, formatter: function (p) { return p.value > 0 ? '+' + p.value : String(p.value); } }
        }
      ]
    });
    window.addEventListener('resize', function () { c2.resize(); });
  }

  // ---- Chart 3: 处置分类占比 ----
  var el3 = document.getElementById('chart-disposition');
  if (el3) {
    var c3 = echarts.init(el3, null, { renderer: 'svg' });
    c3.setOption({
      animation: false,
      tooltip: { trigger: 'item', appendToBody: true, formatter: '{b}：{c} 行（{d}%）' },
      legend: { orient: 'vertical', right: 10, top: 'center', textStyle: { color: ink, fontSize: 12 } },
      series: [{
        type: 'pie',
        radius: ['45%', '72%'],
        center: ['35%', '50%'],
        label: { show: false },
        data: [
          { value: 1077, name: '直迁（7 文件）', itemStyle: { color: keep } },
          { value: 2962, name: '改造（9 文件）', itemStyle: { color: accent } },
          { value: 8416, name: '重写（8 文件 UI + config）', itemStyle: { color: rewrite } },
          { value: 482, name: '删除（theme / logger / 入口）', itemStyle: { color: muted, opacity: 0.6 } }
        ]
      }]
    });
    window.addEventListener('resize', function () { c3.resize(); });
  }
})();
