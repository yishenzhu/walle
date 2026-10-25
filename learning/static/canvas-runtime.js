/* 共享画布运行时：读取 window.CANVAS_SPEC，用 JSXGraph 渲染。
 * 每个画布只提供标题、参数和 draw 函数，不再重复 HTML/CSS/初始化。 */
(function () {
  var spec = window.CANVAS_SPEC;
  if (!spec) return;

  var self = document.currentScript;
  var base = self && self.src ? self.src.replace(/[^/]+$/, "") : "/plugins/learning/";
  var JSX_URL = base + "vendor/jsxgraphcore.js";
  var CSS_URL = base + "vendor/jsxgraph.css";
  var COLORS = {
    accent: "#d8b98a",
    ok: "#8fbf9f",
    blue: "#7aa2f7",
    muted: "#7d7d7d",
    text: "#d4d4d4",
    grid2: "#4a4a4f",
  };

  function loadCss(href) {
    var link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    document.head.append(link);
  }

  function loadJs(src, done) {
    var script = document.createElement("script");
    script.src = src;
    script.onload = done;
    document.head.append(script);
  }

  function buildShell() {
    document.title = spec.title || "画布";
    var style = document.createElement("style");
    style.textContent =
      ':root{--bg:#1e1e1e;--panel:#252526;--border:#3c3c3c;--fg:#d4d4d4;--muted:#a3a3a3;--accent:#d8b98a}' +
      '*{box-sizing:border-box}' +
      'body{margin:0;padding:16px;background:var(--bg);color:var(--fg);' +
      'font-family:Inter,"PingFang SC","Noto Sans CJK SC","Microsoft YaHei",system-ui,sans-serif}' +
      'h1{font-size:17px;margin:0 0 4px}' +
      '.sub{color:var(--muted);font-size:13px;line-height:1.5;margin:0 0 12px}' +
      '.board{width:100%;height:400px;background:var(--panel);border:1px solid var(--border);border-radius:8px}' +
      '.jxgbox{background:var(--panel)!important;border:0!important;border-radius:8px}' +
      '.controls{display:flex;flex-wrap:wrap;gap:8px 16px;margin-top:10px}' +
      '.controls label{display:flex;align-items:center;gap:6px;color:var(--muted);font-size:12px}' +
      '.controls input[type=range]{width:150px;accent-color:var(--accent)}' +
      '.controls .val{color:var(--fg);min-width:38px;font-variant-numeric:tabular-nums}' +
      '.out{margin:10px 0 0;color:var(--muted);font-size:13px;line-height:1.7}';
    document.head.append(style);

    var h1 = document.createElement("h1");
    h1.textContent = spec.title || "";
    var sub = document.createElement("p");
    sub.className = "sub";
    sub.textContent = spec.sub || "";
    var board = document.createElement("div");
    board.id = "board";
    board.className = "board";
    var controls = document.createElement("div");
    controls.className = "controls";
    var out = document.createElement("p");
    out.className = "out";
    document.body.append(h1, sub, board, controls, out);
    return { board: board, controls: controls, out: out };
  }

  function start() {
    var shell = buildShell();

    JXG.Options.text.fontSize = 12;
    JXG.Options.text.color = "#d4d4d4";
    JXG.Options.axis.strokeColor = "#5a5a5a";
    JXG.Options.axis.label.color = "#a3a3a3";
    JXG.Options.grid.strokeColor = "#333333";
    JXG.Options.grid.strokeWidth = 1;
    JXG.Options.board.backgroundColor = "#252526";

    var board = JXG.JSXGraph.initBoard("board", {
      boundingbox: spec.bbox || [-5, 5, 5, -5],
      axis: true,
      grid: true,
      showNavigation: false,
      showCopyright: false,
      pan: { enabled: false },
      zoom: { enabled: false },
      backgroundColor: "#252526",
    });

    var values = {};
    var objects = [];
    var pending = 0;

    function makePoint(at, visible) {
      var p = board.create("point", at, {
        fixed: true,
        visible: !!visible,
        name: "",
        withLabel: false,
      });
      objects.push(p);
      return p;
    }

    function color(name) {
      return COLORS[name] || name || COLORS.accent;
    }

    var kit = {
      arrow: function (from, to, name, opts) {
        opts = opts || {};
        var p1 = makePoint(from, false);
        var p2 = makePoint(to, false);
        var arrow = board.create("arrow", [p1, p2], {
          strokeColor: color(name),
          strokeWidth: opts.width || 2,
        });
        objects.push(arrow);
        if (opts.label) {
          kit.text(to, opts.label, { color: name, dx: 0.16, dy: 0.16 });
        }
        return arrow;
      },
      segment: function (from, to, opts) {
        opts = opts || {};
        var p1 = makePoint(from, false);
        var p2 = makePoint(to, false);
        var segment = board.create("segment", [p1, p2], {
          strokeColor: color(opts.color),
          strokeWidth: opts.width || 1,
          dash: opts.dash || 0,
        });
        objects.push(segment);
        return segment;
      },
      polygon: function (points, opts) {
        opts = opts || {};
        var ps = points.map(function (p) {
          return makePoint(p, false);
        });
        var polygon = board.create("polygon", ps, {
          fillColor: color(opts.fill || "accent"),
          fillOpacity: opts.opacity == null ? 0.12 : opts.opacity,
          strokeColor: color(opts.color || opts.fill || "accent"),
          withLines: true,
        });
        objects.push(polygon);
        return polygon;
      },
      point: function (at, opts) {
        opts = opts || {};
        var p = makePoint(at, true);
        var c = color(opts.color);
        p.setAttribute({
          name: opts.label || "",
          withLabel: !!opts.label,
          strokeColor: c,
          fillColor: c,
          label: { color: c },
        });
        return p;
      },
      text: function (at, text, opts) {
        opts = opts || {};
        var t = board.create("text", [
          at[0] + (opts.dx || 0),
          at[1] + (opts.dy || 0),
          text,
        ], {
          fontSize: opts.size || 12,
          color: color(opts.color || "text"),
          anchorX: opts.anchorX || "left",
          anchorY: opts.anchorY || "middle",
        });
        objects.push(t);
        return t;
      },
    };

    function render() {
      board.suspendUpdate();
      while (objects.length) {
        board.removeObject(objects.pop());
      }
      spec.draw(kit, values);
      board.unsuspendUpdate();
      if (spec.output) {
        shell.out.textContent = spec.output(values);
      }
    }

    function schedule() {
      if (pending) return;
      pending = requestAnimationFrame(function () {
        pending = 0;
        render();
      });
    }

    Object.keys(spec.controls || {}).forEach(function (name) {
      var control = spec.controls[name];
      values[name] = control.value;

      var label = document.createElement("label");
      var caption = document.createElement("span");
      caption.textContent = control.label || name;
      var input = document.createElement("input");
      input.type = "range";
      input.min = control.min;
      input.max = control.max;
      input.step = control.step;
      input.value = control.value;
      var value = document.createElement("span");
      value.className = "val";
      value.textContent = Number(control.value).toFixed(control.digits == null ? 1 : control.digits);

      input.addEventListener("input", function () {
        values[name] = Number(input.value);
        value.textContent = Number(input.value).toFixed(
          control.digits == null ? 1 : control.digits,
        );
        schedule();
      });

      label.append(caption, input, value);
      shell.controls.append(label);
    });

    render();
  }

  loadCss(CSS_URL);
  loadJs(JSX_URL, start);
})();
