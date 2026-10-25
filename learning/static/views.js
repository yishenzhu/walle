/* 学习空间视图：课程列表 + 课程阅读。
 * 课程答疑不在这里：右侧主对话就是答疑，课程视图只负责课程内容。 */
(function () {
  function createCoursesView({ container, api }) {
    const grid = container.querySelector("#courses-grid");
    let loading = false;

    function el(tag, cls, text) {
      const node = document.createElement(tag);
      if (cls) node.className = cls;
      if (text !== undefined) node.textContent = text;
      return node;
    }

    function timeText(value) {
      if (!value) return "";
      const date = new Date(Number(value) * 1000);
      return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString();
    }

    function render(courses) {
      grid.replaceChildren();
      if (!courses.length) {
        const empty = el("div", "course-empty");
        empty.append(
          el("strong", "", "还没有课程"),
          el("p", "", "回到对话，让 Walle 制定学习计划，课程会自动出现在这里。"),
        );
        grid.append(empty);
        return;
      }
      for (const course of courses) {
        const target = course.resume_lesson || course.last_lesson;
        const query = target ? `?lesson=${encodeURIComponent(target)}` : "";
        const card = el("a", "course-card");
        card.href = `/courses/${encodeURIComponent(course.id)}${query}`;
        card.dataset.route = "";
        card.append(
          el("h2", "", course.title || course.id),
          el("p", "", course.goal || "暂无课程简介"),
        );
        const meta = el("div", "course-meta");
        meta.append(
          el(
            "span",
            "",
            `${course.completed_count || 0}/${course.lesson_count || 0} 已完成`,
          ),
          el("span", "", timeText(course.updated_at)),
        );
        card.append(meta);
        grid.append(card);
      }
    }

    async function show() {
      if (loading) return;
      loading = true;
      try {
        const data = await api("/api/courses");
        render(data.courses || []);
        document.title = "学习空间 · Walle";
      } catch (err) {
        grid.replaceChildren(
          el("div", "course-empty", `课程加载失败：${err.message}`),
        );
      } finally {
        loading = false;
      }
    }

    function hide() {}

    return { show, hide };
  }

  function createCourseView({
    container,
    api,
    renderMarkdown,
    conversation,
    router,
    onContinueLesson,
  }) {
    const coursePage = container.querySelector(".course-page");
    const coursePanel = container.querySelector(".course-panel");
    const splitter = container.querySelector("#course-splitter");
    const title = container.querySelector("#course-title");
    const progressText = container.querySelector("#course-progress-text");
    const progressFill = container.querySelector("#course-progress-fill");
    const lessonPicker = container.querySelector("#lesson-picker");
    const lessonTrigger = container.querySelector("#lesson-trigger");
    const lessonTriggerLabel = container.querySelector("#lesson-trigger-label");
    const lessonMenu = container.querySelector("#lesson-menu");
    const exportLink = container.querySelector("#export-link");
    const scrollArea = container.querySelector("#course-scroll");
    const readerHead = container.querySelector("#reader-head");
    const content = container.querySelector("#content");
    const prev = container.querySelector("#prev");
    const next = container.querySelector("#next");
    const completeButton = container.querySelector("#complete-lesson");
    const tabs = [...container.querySelectorAll(".panel-tab")];
    const canvasPane = container.querySelector("#panel-canvas");
    const chatPane = container.querySelector("#panel-chat");
    const canvasBar = container.querySelector("#canvas-bar");
    const canvasTabs = container.querySelector("#canvas-tabs");
    const canvasEmpty = container.querySelector("#canvas-empty");
    const canvasRequest = container.querySelector("#canvas-request");
    const canvasFrame = container.querySelector("#canvas-frame");

    const state = {
      courseId: "",
      course: null,
      lessonId: "",
      lesson: null,
      canvases: [],
      canvasId: "",
      panel: "canvas",
      active: false,
      requestSeq: 0,
      lessonSeq: 0,
    };
    let progressSeq = 0;
    let appHome = null;
    let scrollTick = 0;
    let dragState = null;
    const MIN_PANEL_WIDTH = 260;
    const MIN_MAIN_WIDTH = 320;
    const SPLITTER_WIDTH = 6;

    function clampPanelWidth(width) {
      const pageWidth =
        coursePage.getBoundingClientRect().width || window.innerWidth;
      const max = Math.max(
        MIN_PANEL_WIDTH,
        pageWidth - MIN_MAIN_WIDTH - SPLITTER_WIDTH,
      );
      return Math.min(Math.max(width, MIN_PANEL_WIDTH), max);
    }

    function applyPanelWidth(width) {
      const next = clampPanelWidth(width);
      coursePanel.style.flex = `0 0 ${next}px`;
      coursePanel.style.width = `${next}px`;
      coursePanel.style.maxWidth = "none";
      coursePanel.style.minWidth = "0";
      splitter.setAttribute("aria-valuenow", String(Math.round(next)));
    }

    function clearPanelWidth() {
      coursePanel.style.flex = "";
      coursePanel.style.width = "";
      coursePanel.style.maxWidth = "";
      coursePanel.style.minWidth = "";
      splitter.removeAttribute("aria-valuenow");
    }

    function storedPanelWidth() {
      const value = Number(localStorage.getItem("walle.course.panelWidth"));
      return Number.isFinite(value) && value > 0 ? value : 0;
    }

    function restorePanelWidth() {
      if (window.innerWidth <= 480) {
        clearPanelWidth();
        return;
      }
      const stored = storedPanelWidth();
      if (stored) applyPanelWidth(stored);
      else clearPanelWidth();
    }

    function savePanelWidth() {
      const width = Math.round(coursePanel.getBoundingClientRect().width);
      localStorage.setItem("walle.course.panelWidth", String(width));
    }

    function el(tag, cls, text) {
      const node = document.createElement(tag);
      if (cls) node.className = cls;
      if (text !== undefined) node.textContent = text;
      return node;
    }

    function stripLeadingTitle(markdown) {
      return (markdown || "").replace(/^\s*#\s+[^\n]+\n+/, "");
    }

    function currentLesson() {
      return (
        (state.course?.lessons || []).find(
          (item) => item.id === state.lessonId,
        ) || null
      );
    }

    function chatApp() {
      return (
        chatPane.querySelector(".app") ||
        document.querySelector(".shell > .app") ||
        document.querySelector(".app")
      );
    }

    function scrollChat() {
      const log = chatPane.querySelector("#log");
      if (log) log.scrollTop = log.scrollHeight;
    }

    function dockChat() {
      const app = chatApp();
      if (!app) return;
      if (!appHome) {
        appHome = { parent: app.parentElement, next: app.nextSibling };
      }
      if (app.parentElement !== chatPane) {
        chatPane.append(app);
        app.classList.add("docked");
        scrollChat();
      }
    }

    function undockChat() {
      const app = chatPane.querySelector(".app");
      if (!app || !appHome) return;
      app.classList.remove("docked");
      if (appHome.next && appHome.next.parentElement === appHome.parent) {
        appHome.parent.insertBefore(app, appHome.next);
      } else {
        appHome.parent.append(app);
      }
    }

    function setPanel(name) {
      state.panel = name === "chat" ? "chat" : "canvas";
      for (const tab of tabs) {
        const active = tab.dataset.panel === state.panel;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", active ? "true" : "false");
      }
      canvasPane.hidden = state.panel !== "canvas";
      chatPane.hidden = state.panel !== "chat";
      if (state.panel === "chat") scrollChat();
    }

    function canvasUrl(canvasId) {
      return `/courses/${encodeURIComponent(state.courseId)}/canvas/${encodeURIComponent(canvasId)}`;
    }

    function lessonCanvases(lesson) {
      const titles = new Map();
      for (const item of (lesson && lesson.canvases) || []) {
        if (item && item.id) titles.set(item.id, item.title || item.id);
      }
      const ids = [];
      const add = (canvasId) => {
        if (canvasId && !ids.includes(canvasId)) ids.push(canvasId);
      };
      const pattern = /\{\{canvas:([A-Za-z0-9_-]+)\}\}/g;
      const text = (lesson && lesson.markdown) || "";
      let match = pattern.exec(text);
      while (match) {
        add(match[1]);
        match = pattern.exec(text);
      }
      for (const canvasId of (lesson && lesson.canvas_ids) || []) add(canvasId);
      for (const canvasId of titles.keys()) add(canvasId);
      return ids.map((canvasId) => ({
        id: canvasId,
        title: titles.get(canvasId) || canvasId,
      }));
    }

    function renderCanvas() {
      const items = state.canvases;
      if (!items.length) {
        canvasBar.hidden = true;
        canvasFrame.hidden = true;
        canvasEmpty.hidden = false;
        canvasTabs.replaceChildren();
        return;
      }
      if (!items.some((item) => item.id === state.canvasId)) {
        state.canvasId = items[0].id;
      }
      canvasBar.hidden = items.length < 2;
      canvasEmpty.hidden = true;
      canvasFrame.hidden = false;
      canvasTabs.replaceChildren();
      for (const item of items) {
        const button = el("button", "canvas-tab", item.title || item.id);
        button.type = "button";
        button.title = item.title || item.id;
        if (item.id === state.canvasId) button.classList.add("active");
        button.onclick = () => {
          state.canvasId = item.id;
          renderCanvas();
        };
        canvasTabs.append(button);
      }
      canvasFrame.src = canvasUrl(state.canvasId);
    }

    function canvasPrompt(lesson) {
      const name = state.course?.title || state.courseId;
      const cid = state.course?.id || state.courseId;
      const where = lesson
        ? `第 ${lesson.order || "?"} 课「${lesson.title || lesson.id}」`
        : "课程";
      return (
        `为课程《${name}》（course_id=${cid}）的${where}补一个交互画布：` +
        "写入 canvas 文件，并在课节正文中用 {{canvas:...}} 引用，确保画布能直接打开。"
      );
    }

    function openLessonMenu() {
      if (lessonTrigger.disabled) return;
      lessonMenu.hidden = false;
      lessonTrigger.setAttribute("aria-expanded", "true");
      const active =
        lessonMenu.querySelector(".lesson-option.active") ||
        lessonMenu.querySelector(".lesson-option");
      if (active) active.focus({ preventScroll: true });
    }

    function closeLessonMenu() {
      if (lessonMenu.hidden) return;
      const focusInside = lessonMenu.contains(document.activeElement);
      lessonMenu.hidden = true;
      lessonTrigger.setAttribute("aria-expanded", "false");
      if (focusInside) lessonTrigger.focus({ preventScroll: true });
    }

    function toggleLessonMenu() {
      if (lessonMenu.hidden) openLessonMenu();
      else closeLessonMenu();
    }

    function renderLessonSelect() {
      const lessons = state.course?.lessons || [];
      lessonMenu.replaceChildren();
      for (const [index, lesson] of lessons.entries()) {
        const option = el("button", "lesson-option");
        option.type = "button";
        option.setAttribute("role", "option");
        option.dataset.lessonId = lesson.id;
        option.title = lesson.title || lesson.id;
        const order = String(lesson.order || index + 1).padStart(2, "0");
        option.append(
          el("span", "lesson-option-index", order),
          el("span", "lesson-option-title", lesson.title || lesson.id),
        );
        if (lesson.completed) {
          option.append(el("span", "lesson-option-state", "✓"));
        } else if (lesson.written === false) {
          option.append(el("span", "lesson-option-state missing", "待生成"));
        }
        if (lesson.id === state.lessonId) {
          option.classList.add("active");
          option.setAttribute("aria-selected", "true");
        }
        option.onclick = () => {
          closeLessonMenu();
          selectLesson(lesson.id);
        };
        lessonMenu.append(option);
      }
      const current = lessons.find((item) => item.id === state.lessonId);
      lessonTriggerLabel.textContent = current
        ? `${String(current.order || 1).padStart(2, "0")} · ${current.title || current.id}`
        : "选择课节";
      lessonTrigger.title = current
        ? `${String(current.order || 1).padStart(2, "0")} · ${current.title || current.id}`
        : "选择课节";
      lessonTrigger.disabled = !lessons.length;
    }

    function updateCompleteButton() {
      const lesson = currentLesson();
      if (!lesson) {
        completeButton.hidden = true;
        return;
      }
      completeButton.hidden = false;
      if (lesson.written === false) {
        completeButton.disabled = true;
        completeButton.textContent = "暂无正文";
        return;
      }
      completeButton.disabled = false;
      completeButton.textContent = lesson.completed ? "已完成 ✓" : "标记完成";
    }

    function updateProgressUI() {
      const lessons = state.course?.lessons || [];
      const total = lessons.length;
      const completed = lessons.filter((item) => item.completed).length;
      const written =
        typeof state.course?.written_count === "number"
          ? state.course.written_count
          : lessons.filter((item) => item.written !== false).length;
      progressText.textContent = total
        ? `${completed}/${total} 已完成 · ${written}/${total} 已生成`
        : "";
      progressFill.style.width = total ? `${(completed / total) * 100}%` : "0%";
      updateCompleteButton();
    }

    function updateActions() {
      const lessons = state.course?.lessons || [];
      const index = lessons.findIndex((item) => item.id === state.lessonId);
      prev.disabled = index <= 0;
      next.disabled = index < 0 || index >= lessons.length - 1;
    }

    function applyProgress(progress) {
      state.course.progress = progress;
      const done = new Set(progress.completed || []);
      for (const lesson of state.course.lessons || []) {
        lesson.completed = done.has(lesson.id);
      }
      renderLessonSelect();
      updateActions();
      updateProgressUI();
    }

    async function saveProgress(lessonId, completed) {
      const seq = ++progressSeq;
      const response = await fetch(
        `/api/courses/${encodeURIComponent(state.courseId)}/progress`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ lesson_id: lessonId, completed }),
        },
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || `HTTP ${response.status}`);
      }
      if (seq === progressSeq) applyProgress(data.progress);
    }

    function markLastLesson(lessonId) {
      const seq = ++progressSeq;
      fetch(`/api/courses/${encodeURIComponent(state.courseId)}/progress`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lesson_id: lessonId }),
      })
        .then((response) => (response.ok ? response.json() : null))
        .then((data) => {
          if (seq !== progressSeq) return;
          if (data && data.progress) applyProgress(data.progress);
        })
        .catch((err) => console.error(err));
    }

    function canvasIcon() {
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "canvas-link-icon");
      svg.setAttribute("viewBox", "0 0 24 24");
      svg.setAttribute("width", "14");
      svg.setAttribute("height", "14");
      svg.setAttribute("aria-hidden", "true");
      svg.innerHTML =
        '<rect x="3" y="4" width="18" height="14" rx="2" fill="none" stroke="currentColor" stroke-width="1.6"></rect>' +
        '<path d="M3 14l5-4 4 3 3-2 6 5" fill="none" stroke="currentColor" stroke-width="1.6"></path>';
      return svg;
    }

    function decorateCanvases() {
      const titles = new Map(
        state.canvases.map((item) => [item.id, item.title || item.id]),
      );
      for (const p of [...content.querySelectorAll("p")]) {
        const match = (p.textContent || "")
          .trim()
          .match(/^\{\{canvas:([A-Za-z0-9_-]+)\}\}$/);
        if (!match) continue;
        const canvasId = match[1];
        const link = el("a", "canvas-link");
        link.href = canvasUrl(canvasId);
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.dataset.canvasId = canvasId;
        link.title = titles.get(canvasId) || canvasId;
        link.append(
          canvasIcon(),
          el("span", "canvas-link-title", titles.get(canvasId) || canvasId),
        );
        link.onclick = (event) => {
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
            return;
          }
          event.preventDefault();
          state.canvasId = canvasId;
          renderCanvas();
          setPanel("canvas");
        };
        p.replaceWith(link);
      }
    }

    function syncCanvasToScroll() {
      if (!state.canvases.length) return;
      const links = [...content.querySelectorAll(".canvas-link")];
      if (!links.length) return;
      const anchor = scrollArea.getBoundingClientRect().top + 96;
      let active = links[0];
      for (const link of links) {
        if (link.getBoundingClientRect().top <= anchor) active = link;
        else break;
      }
      const canvasId = active.dataset.canvasId;
      if (canvasId && canvasId !== state.canvasId) {
        state.canvasId = canvasId;
        renderCanvas();
      }
    }

    function continuePrompt(lesson) {
      const name = state.course?.title || state.courseId;
      const cid = state.course?.id || state.courseId;
      if (lesson) {
        return (
          `继续完成课程《${name}》（course_id=${cid}）：` +
          `从第 ${lesson.order || "?"} 课「${lesson.title}」开始，按章节顺序完成每一课；` +
          "当前课的正文和画布都完成后再进入下一课。"
        );
      }
      return (
        `继续完成课程《${name}》（course_id=${cid}）：` +
        "按章节顺序完成每一课；当前课的正文和画布都完成后再进入下一课。"
      );
    }

    function showMissingLesson(lesson) {
      content.replaceChildren();
      const empty = el("div", "empty");
      empty.append(el("strong", "", "这一课还没有正文"));
      empty.append(
        document.createTextNode(
          "课程生成可能在写入课节前中断。点击下方会切换到学习模式，发送提示即可继续生成。",
        ),
      );
      const button = el("button", "continue-link", "填入续写提示");
      button.type = "button";
      button.onclick = () => {
        setPanel("chat");
        onContinueLesson(continuePrompt(lesson));
      };
      empty.append(button);
      content.append(empty);
    }

    async function selectLesson(lessonId, push = true, options = {}) {
      if (!lessonId) return;
      const seq = ++state.lessonSeq;
      const context = options.context !== false;
      state.lessonId = lessonId;
      localStorage.setItem(`walle.course.${state.courseId}.lesson`, lessonId);
      if (push) {
        const path = `/courses/${encodeURIComponent(state.courseId)}?lesson=${encodeURIComponent(lessonId)}`;
        router.replace(path);
      }
      if (context) conversation.setCourse(state.courseId, lessonId);
      renderLessonSelect();
      updateActions();
      updateCompleteButton();
      markLastLesson(lessonId);
      readerHead.replaceChildren();
      content.replaceChildren(el("div", "empty", "加载课节…"));
      scrollArea.scrollTop = 0;
      try {
        const data = await api(
          `/api/courses/${encodeURIComponent(state.courseId)}/lessons/${encodeURIComponent(lessonId)}`,
        );
        if (seq !== state.lessonSeq || !state.active) return;
        const lesson = data.lesson;
        state.lesson = lesson;
        state.canvases = lessonCanvases(lesson);
        state.canvasId = state.canvases[0]?.id || "";
        renderCanvas();
        readerHead.append(
          el("h2", "", lesson.title || lesson.id),
          el("span", "", `第 ${lesson.order || "?"} 课`),
        );
        if (!(lesson.markdown || "").trim()) {
          showMissingLesson(lesson);
          return;
        }
        content.innerHTML = renderMarkdown(stripLeadingTitle(lesson.markdown));
        for (const link of content.querySelectorAll("a")) {
          link.target = "_blank";
          link.rel = "noopener noreferrer";
        }
        decorateCanvases();
        syncCanvasToScroll();
      } catch (err) {
        if (seq !== state.lessonSeq || !state.active) return;
        state.canvases = [];
        state.canvasId = "";
        renderCanvas();
        content.replaceChildren(
          el("div", "empty", `课节加载失败：${err.message}`),
        );
      }
    }

    function startLesson(search, course) {
      const lessons = state.course.lessons || [];
      const sessionId = course.session_id || "";
      const cwd = course.workspace || "";
      if (!lessons.length) {
        readerHead.replaceChildren();
        content.replaceChildren();
        const empty = el("div", "empty");
        empty.append(
          el("strong", "", "课程还没有课节"),
          document.createTextNode("在右侧「对话」里让 Walle 继续写入课程内容。"),
        );
        content.append(empty);
        state.canvases = [];
        state.canvasId = "";
        renderCanvas();
        conversation.enterCourse(state.courseId, "", sessionId, cwd);
        return;
      }
      const requested = new URLSearchParams(search || "").get("lesson");
      const saved = localStorage.getItem(
        `walle.course.${state.courseId}.lesson`,
      );
      const known = lessons.some((item) => item.id === requested)
        ? requested
        : lessons.some((item) => item.id === saved)
          ? saved
          : lessons[0].id;
      conversation.enterCourse(state.courseId, known, sessionId, cwd);
      selectLesson(known, false, { context: false });
    }

    async function show(courseId, search = "") {
      const seq = ++state.requestSeq;
      state.lessonSeq += 1;
      state.active = true;
      state.courseId = courseId;
      state.lesson = null;
      state.canvases = [];
      state.canvasId = "";
      readerHead.replaceChildren();
      content.replaceChildren(el("div", "empty", "加载课程…"));
      progressText.textContent = "加载中…";
      progressFill.style.width = "0%";
      dockChat();
      setPanel("canvas");
      restorePanelWidth();
      requestAnimationFrame(restorePanelWidth);
      try {
        const course = await api(
          `/api/courses/${encodeURIComponent(courseId)}`,
        );
        if (!state.active || state.requestSeq !== seq) return false;
        state.course = course;
        title.textContent = course.title || course.id;
        title.title = course.goal || course.title || course.id;
        document.title = `${course.title || course.id} · Walle`;
        exportLink.href = `/courses/${encodeURIComponent(courseId)}/export`;
        renderLessonSelect();
        updateProgressUI();
        startLesson(search, course);
        return true;
      } catch (err) {
        if (!state.active || state.requestSeq !== seq) return false;
        title.textContent = "课程不存在";
        title.removeAttribute("title");
        progressText.textContent = "";
        progressFill.style.width = "0%";
        readerHead.replaceChildren();
        content.replaceChildren(
          el("div", "empty", `课程加载失败：${err.message}`),
        );
        state.canvases = [];
        state.canvasId = "";
        renderCanvas();
        return false;
      }
    }

    function hide() {
      if (!state.active && !state.courseId) return;
      state.requestSeq += 1;
      state.lessonSeq += 1;
      state.active = false;
      state.courseId = "";
      state.course = null;
      state.lessonId = "";
      state.lesson = null;
      state.canvases = [];
      state.canvasId = "";
      dragState = null;
      document.body.classList.remove("course-resizing");
      closeLessonMenu();
      undockChat();
      conversation.exitCourse();
      document.title = "Walle";
    }

    lessonTrigger.addEventListener("click", (event) => {
      event.stopPropagation();
      toggleLessonMenu();
    });
    lessonTrigger.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        openLessonMenu();
      }
    });
    lessonMenu.addEventListener("keydown", (event) => {
      const options = [...lessonMenu.querySelectorAll(".lesson-option")];
      const index = options.indexOf(document.activeElement);
      if (event.key === "ArrowDown") {
        event.preventDefault();
        options[Math.min(index + 1, options.length - 1)]?.focus();
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        options[Math.max(index - 1, 0)]?.focus();
      } else if (event.key === "Home") {
        event.preventDefault();
        options[0]?.focus();
      } else if (event.key === "End") {
        event.preventDefault();
        options[options.length - 1]?.focus();
      }
    });
    document.addEventListener("click", (event) => {
      if (!lessonPicker.contains(event.target)) closeLessonMenu();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeLessonMenu();
    });
    window.addEventListener("resize", closeLessonMenu);
    for (const tab of tabs) {
      tab.addEventListener("click", () => setPanel(tab.dataset.panel));
    }
    scrollArea.addEventListener(
      "scroll",
      () => {
        if (scrollTick) return;
        scrollTick = requestAnimationFrame(() => {
          scrollTick = 0;
          syncCanvasToScroll();
        });
      },
      { passive: true },
    );
    window.addEventListener("resize", syncCanvasToScroll);
    splitter.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      splitter.focus({ preventScroll: true });
      const rect = coursePanel.getBoundingClientRect();
      dragState = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startWidth: rect.width,
      };
      splitter.setPointerCapture(event.pointerId);
      document.body.classList.add("course-resizing");
    });
    splitter.addEventListener("pointermove", (event) => {
      if (!dragState || event.pointerId !== dragState.pointerId) return;
      applyPanelWidth(dragState.startWidth - (event.clientX - dragState.startX));
    });
    function endPanelDrag(event) {
      if (!dragState || (event && event.pointerId !== dragState.pointerId)) {
        return;
      }
      const pointerId = dragState.pointerId;
      dragState = null;
      document.body.classList.remove("course-resizing");
      if (splitter.hasPointerCapture(pointerId)) {
        splitter.releasePointerCapture(pointerId);
      }
      savePanelWidth();
    }
    splitter.addEventListener("pointerup", endPanelDrag);
    splitter.addEventListener("pointercancel", endPanelDrag);
    splitter.addEventListener("dblclick", () => {
      clearPanelWidth();
      localStorage.removeItem("walle.course.panelWidth");
    });
    splitter.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const current = coursePanel.getBoundingClientRect().width;
      applyPanelWidth(current + (event.key === "ArrowLeft" ? 16 : -16));
      savePanelWidth();
    });
    window.addEventListener("resize", restorePanelWidth);
    canvasRequest.addEventListener("click", () => {
      setPanel("chat");
      onContinueLesson(canvasPrompt(currentLesson()));
    });
    prev.addEventListener("click", () => {
      const lessons = state.course?.lessons || [];
      const index = lessons.findIndex((item) => item.id === state.lessonId);
      if (index > 0) selectLesson(lessons[index - 1].id);
    });
    next.addEventListener("click", () => {
      const lessons = state.course?.lessons || [];
      const index = lessons.findIndex((item) => item.id === state.lessonId);
      if (index >= 0 && index < lessons.length - 1) {
        selectLesson(lessons[index + 1].id);
      }
    });
    completeButton.addEventListener("click", async () => {
      const lesson = currentLesson();
      if (!lesson || lesson.written === false) return;
      completeButton.disabled = true;
      try {
        await saveProgress(lesson.id, !lesson.completed);
      } catch (err) {
        console.error(err);
        completeButton.textContent = "保存失败";
        setTimeout(updateCompleteButton, 1200);
      }
    });

    return { show, hide };
  }

  const TEMPLATE = `
  <section id="courses-view" class="page-view" hidden>
    <div class="courses-page">
      <div class="courses-head">
        <div>
          <h1>学习空间</h1>
          <p>进入课程后，左侧阅读课节，右侧可在画布和对话之间切换。</p>
        </div>
        <div class="courses-ish"><a class="ghost" href="/" data-route>返回对话</a></div>
      </div>
      <div id="courses-grid" class="courses-grid"></div>
    </div>
  </section>
  <section id="course-view" class="page-view" hidden>
    <div class="course-page">
      <main class="course-main">
        <header class="course-bar">
          <a class="ghost course-all" href="/courses" data-route title="返回课程列表">所有课程</a>
          <div class="course-heading">
            <h1 id="course-title">课程</h1>
            <div class="course-progress">
              <span id="course-progress-text" class="course-progress-text"></span>
              <span class="course-progress-track"><i id="course-progress-fill"></i></span>
            </div>
          </div>
          <div id="lesson-picker" class="lesson-picker">
            <button id="lesson-trigger" class="lesson-trigger" type="button" aria-haspopup="listbox" aria-expanded="false">
              <span id="lesson-trigger-label" class="lesson-trigger-label">选择课节</span>
              <svg class="lesson-chevron" viewBox="0 0 10 6" width="10" height="6" aria-hidden="true">
                <path d="M1 1l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"></path>
              </svg>
            </button>
            <div id="lesson-menu" class="lesson-menu" role="listbox" hidden></div>
          </div>
          <a id="export-link" class="ghost course-export" href="#">导出</a>
        </header>
        <div id="course-scroll" class="course-scroll">
          <div id="reader-head" class="reader-head"></div>
          <article id="content" class="course-md"></article>
          <div class="lesson-actions">
            <button id="prev" type="button">上一课</button>
            <button id="complete-lesson" type="button">标记完成</button>
            <button id="next" type="button">下一课</button>
          </div>
        </div>
      </main>
      <div id="course-splitter" class="course-splitter" role="separator"
        aria-orientation="vertical" aria-label="调整课程内容宽度"
        tabindex="0" title="拖动调整宽度，双击重置"></div>
      <aside class="course-panel">
        <div class="panel-tabs" role="tablist">
          <button class="panel-tab active" type="button" role="tab" data-panel="canvas" aria-selected="true">画布</button>
          <button class="panel-tab" type="button" role="tab" data-panel="chat" aria-selected="false">对话</button>
        </div>
        <div class="panel-body">
          <section id="panel-canvas" class="panel-pane" role="tabpanel">
            <div id="canvas-bar" class="canvas-bar" hidden>
              <div id="canvas-tabs" class="canvas-tabs"></div>
            </div>
            <div id="canvas-empty" class="panel-empty" hidden>
              <strong>这一课还没有画布</strong>
              <p>可以让 Walle 为这一课补一个交互演示。</p>
              <button id="canvas-request" class="continue-link" type="button">让 Walle 补画布</button>
            </div>
            <iframe id="canvas-frame" class="canvas-frame" title="课程画布" hidden
              sandbox="allow-scripts allow-forms allow-popups allow-modals allow-downloads"
              referrerpolicy="no-referrer"></iframe>
          </section>
          <section id="panel-chat" class="panel-pane" role="tabpanel" hidden></section>
        </div>
      </aside>
    </div>
  </section>`;

  let views = null;

  function ensureViews(container, ctx) {
    if (views && views.container === container) return views;
    container.innerHTML = TEMPLATE;
    const coursesEl = container.querySelector("#courses-view");
    const courseEl = container.querySelector("#course-view");
    const coursesView = createCoursesView({ container: coursesEl, api: ctx.api });
    const courseView = createCourseView({
      container: courseEl,
      api: ctx.api,
      renderMarkdown: ctx.renderMarkdown,
      conversation: ctx.conversation,
      router: ctx.router,
      onContinueLesson: (prompt) => {
        ctx.setPrompt(prompt);
        ctx.conversation.setAgent("learning");
      },
    });
    views = { container, coursesEl, courseEl, coursesView, courseView };
    return views;
  }

  function match(pathname) {
    if (pathname === "/courses" || pathname === "/courses/") {
      return { name: "courses", path: "/courses", search: location.search };
    }
    const found = pathname.match(/^\/courses\/([^/]+)\/?$/);
    if (!found) return null;
    let courseId = found[1];
    try {
      courseId = decodeURIComponent(courseId);
    } catch (err) {
      console.error(err);
    }
    return {
      name: "course",
      courseId,
      path: `/courses/${encodeURIComponent(courseId)}`,
      search: location.search,
    };
  }

  function render(route, container, ctx) {
    const view = ensureViews(container, ctx);
    document.body.classList.remove("view-courses", "view-course");
    if (route.name === "courses") {
      document.body.classList.add("view-courses");
      document.title = "学习空间 · Walle";
      view.courseView.hide();
      view.coursesEl.hidden = false;
      view.courseEl.hidden = true;
      view.coursesView.show();
      return true;
    }
    if (route.name === "course") {
      document.body.classList.add("view-course");
      view.coursesEl.hidden = true;
      view.courseEl.hidden = false;
      view.coursesView.hide();
      view.courseView.show(route.courseId, route.search);
      return true;
    }
    view.coursesView.hide();
    view.courseView.hide();
    return false;
  }

  function onToolResult(data, ctx) {
    if (data.error) return false;
    const result = data.result || {};
    if (result.type === "course") {
      courseCard(result, ctx);
      return true;
    }
    if (result.type === "lesson") {
      lessonCard(result, ctx);
      return true;
    }
    return false;
  }

  function courseCard(data, ctx) {
    const card = ctx.el("a", "canvas-card");
    card.href = `/courses/${encodeURIComponent(data.course_id)}`;
    card.target = "_blank";
    card.rel = "noopener noreferrer";
    const title = ctx.el("div", "canvas-title");
    title.append(
      ctx.el("div", "canvas-kicker", "课程"),
      ctx.el("div", "canvas-name", data.title || "学习空间"),
    );
    card.append(title, ctx.el("span", "canvas-open", "打开学习空间"));
    ctx.log.append(card);
    ctx.scroll();
  }

  function lessonCard(data, ctx) {
    const card = ctx.el("a", "canvas-card");
    card.href = `/courses/${encodeURIComponent(data.course_id)}?lesson=${encodeURIComponent(data.lesson_id)}`;
    card.target = "_blank";
    card.rel = "noopener noreferrer";
    const title = ctx.el("div", "canvas-title");
    title.append(
      ctx.el("div", "canvas-kicker", `第 ${data.order || "?"} 课`),
      ctx.el("div", "canvas-name", data.title || "课节"),
    );
    card.append(title, ctx.el("span", "canvas-open", "打开课节"));
    ctx.log.append(card);
    ctx.scroll();
  }

  function canvasUrl(state) {
    if (!state.courseId) return null;
    return `/courses/${encodeURIComponent(state.courseId)}/canvas/${encodeURIComponent(state.id)}?v=${state.version}`;
  }

  function ownsPath(pathname) {
    return pathname.startsWith("/courses");
  }

  window.WallePlugins.register({
    name: "learning",
    match,
    render,
    onToolResult,
    canvasUrl,
    ownsPath,
  });
})();
