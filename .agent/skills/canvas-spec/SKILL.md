---
name: canvas-spec
description: Build interactive canvases with the shared JSXGraph runtime. Use for the canvas tool.
---

# Canvas spec

Prefer `spec` over `html` in `canvas`. The spec is a JS object:

```js
{
  title: "Vector",
  sub: "Drag x and y.",
  bbox: [-5, 5, 5, -5],
  controls: {
    x: { min: -4, max: 4, step: 0.1, value: 3, label: "x" },
  },
  draw(k, s) {
    k.arrow([0, 0], [s.x, 2], "accent", { label: "v" });
  },
  output(s) {
    return "x = " + s.x.toFixed(1);
  },
}
```

## Fields

- `title`: canvas title.
- `sub`: one short sentence shown above the board.
- `bbox`: `[left, top, right, bottom]`, default `[-5, 5, 5, -5]`.
- `controls`: range inputs keyed by name. Each value is
  `{min, max, step, value, label?, digits?}`. Values are passed to `draw` and
  `output` as `s`.
- `draw(k, s)`: creates the scene. Called again when a control changes.
- `output(s)`: optional text below the board.

## Kit

- `k.arrow(from, to, color, {label, width})`
- `k.segment(from, to, {dash, color, width})`
- `k.polygon(points, {fill, color, opacity})`
- `k.point(at, {label, color})`
- `k.text(at, text, {color, dx, dy, size})`

Colors: `accent`, `ok`, `blue`, `muted`, `text`, `grid2`.

## Rules

- Use `spec` for standard 2D math visuals: vectors, matrices, plots,
  projections, transformations, and simple animations.
- Use `canvas` for both course lessons and general pages. Course lessons reference the returned `canvas_id` via `{{canvas:<id>}}`.
- Use `html` only when the spec cannot express the page.
- Keep one concept per canvas. Use at most one canvas per lesson unless the
  lesson truly needs more.
- Pass `canvas_id` to update an existing canvas; do not create a duplicate.
- Call `canvas` before `write_lesson`, then put `{{canvas:<id>}}` on its
  own line in the lesson Markdown.
