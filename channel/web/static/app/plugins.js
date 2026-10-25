/* 通用 Web 插件宿主：插件清单加载、客户端路由、导航与工具结果扩展点。
 * 学习等业务插件只通过 register() 声明 match/render/toolResult/canvasUrl，
 * Shell 不包含任何具体插件逻辑。 */
(function () {
  const plugins = [];
  let context = null;

  class Router {
    constructor({ onRoute }) {
      this.onRoute = onRoute;
      this.started = false;
      this.onPopState = () => this.render();
      this.onClick = (event) => this.handleClick(event);
    }

    parse(pathname = location.pathname) {
      return { path: pathname, search: location.search };
    }

    start() {
      if (this.started) return;
      this.started = true;
      window.addEventListener("popstate", this.onPopState);
      document.addEventListener("click", this.onClick);
    }

    render() {
      const route = this.parse();
      Promise.resolve(this.onRoute(route)).catch((err) => console.error(err));
      return route;
    }

    navigate(path, { replace = false } = {}) {
      const next = new URL(path, location.origin);
      const current = `${location.pathname}${location.search}${location.hash}`;
      const target = `${next.pathname}${next.search}${next.hash}`;
      if (target !== current) {
        if (replace) history.replaceState(null, "", target);
        else history.pushState(null, "", target);
      }
      return this.render();
    }

    replace(path) {
      history.replaceState(null, "", path);
    }

    handleClick(event) {
      if (event.defaultPrevented || event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const link = event.target.closest("a[data-route]");
      if (!link) return;
      if (link.target && link.target !== "_self") return;
      if (link.hasAttribute("download")) return;
      const href = link.getAttribute("href");
      if (!href) return;
      const url = new URL(href, location.origin);
      if (url.origin !== location.origin) return;
      event.preventDefault();
      this.navigate(`${url.pathname}${url.search}${url.hash}`);
    }
  }

  function register(plugin) {
    if (!plugin || !plugin.name) return;
    plugins.push(plugin);
  }

  function match(pathname) {
    for (const plugin of plugins) {
      if (!plugin.match) continue;
      const route = plugin.match(pathname);
      if (route) return { plugin, route };
    }
    return null;
  }

  function render(route, container) {
    const found = match(route.path);
    if (!found || !found.plugin.render) return false;
    return found.plugin.render(found.route, container, context) !== false;
  }

  function toolResult(data) {
    for (const plugin of plugins) {
      if (plugin.onToolResult && plugin.onToolResult(data, context)) return true;
    }
    return false;
  }

  function canvasUrl(state) {
    for (const plugin of plugins) {
      if (!plugin.canvasUrl) continue;
      const url = plugin.canvasUrl(state, context);
      if (url) return url;
    }
    return null;
  }

  function ownsPath(pathname) {
    return plugins.some((plugin) => plugin.ownsPath && plugin.ownsPath(pathname));
  }

  function loadStyle(href) {
    return new Promise((resolve) => {
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = href;
      link.onload = resolve;
      link.onerror = resolve;
      document.head.append(link);
    });
  }

  function loadScript(src) {
    return new Promise((resolve) => {
      const script = document.createElement("script");
      script.src = src;
      script.onload = resolve;
      script.onerror = resolve;
      document.body.append(script);
    });
  }

  function renderNav(manifest) {
    const nav = document.getElementById("plugin-nav");
    if (!nav) return;
    for (const item of manifest.nav || []) {
      const link = document.createElement("a");
      link.className = "ghost";
      link.href = item.href;
      link.dataset.route = "";
      link.textContent = item.label || manifest.name;
      if (item.title) link.title = item.title;
      nav.append(link);
    }
  }

  async function start() {
    let data = { plugins: [] };
    try {
      const response = await fetch("/api/web/plugins", { cache: "no-store" });
      if (response.ok) data = await response.json();
    } catch (err) {
      console.error(err);
    }
    for (const manifest of data.plugins || []) {
      for (const href of manifest.styles || []) await loadStyle(href);
      for (const src of manifest.scripts || []) await loadScript(src);
      renderNav(manifest);
    }
  }

  function setContext(ctx) {
    context = ctx;
  }

  window.WallePlugins = {
    Router,
    register,
    start,
    match,
    render,
    toolResult,
    canvasUrl,
    ownsPath,
    setContext,
  };
})();
