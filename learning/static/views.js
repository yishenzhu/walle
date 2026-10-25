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
    const title = container.querySelector("#course-title");
    const goal = container.querySelector("#course-goal");
    const planMeta = container.querySelector("#plan-meta");
    const continueLearning = container.querySelector("#continue-learning");
    const lessonNav = container.querySelector("#lesson-nav");
    const readerHead = container.querySelector("#reader-head");
    const content = container.querySelector("#content");
    const prev = container.querySelector("#prev");
    const next = container.querySelector("#next");
    const completeButton = container.querySelector("#complete-lesson");
    const exportLink = container.querySelector("#export-link");

    const state = {
      courseId: "",
      course: null,
      lessonId: "",
      active: false,
      requestSeq: 0,
      lessonSeq: 0,
    };
    let progressSeq = 0;

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

    function continueTarget() {
      const lessons = state.course?.lessons || [];
      if (!lessons.length) return null;
      const lastId = state.course?.progress?.last_lesson;
      const last = lessons.find((item) => item.id === lastId);
      if (last && !last.completed) return last;
      return (
        lessons.find((item) => !item.completed) || lessons[lessons.length - 1]
      );
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
      completeButton.textContent = lesson.completed ? "标记未完成" : "标记完成";
    }

    function updateProgressUI() {
      const lessons = state.course?.lessons || [];
      const total = lessons.length;
      const completed = lessons.filter((item) => item.completed).length;
      planMeta.textContent = total
        ? `学习计划 · 已完成 ${completed}/${total} · 已生成 ${state.course.written_count || 0}/${total}`
        : "学习计划";
      const target = continueTarget();
      continueLearning.disabled = !target;
      continueLearning.textContent = target
        ? `继续学习：${target.title}`
        : "开始学习";
      updateCompleteButton();
    }

    function updateActions() {
      const lessons = state.course?.lessons || [];
      const index = lessons.findIndex((item) => item.id === state.lessonId);
      prev.disabled = index <= 0;
      next.disabled = index < 0 || index >= lessons.length - 1;
    }

    function renderNav() {
      lessonNav.replaceChildren();
      const lessons = state.course?.lessons || [];
      lessons.forEach((lesson, index) => {
        const button = el("button", "lesson-link");
        button.type = "button";
        if (lesson.id === state.lessonId) button.classList.add("active");
        if (lesson.completed) button.classList.add("completed");
        if (lesson.written === false) button.classList.add("missing");
        button.append(
          el(
            "span",
            "lesson-index",
            String(lesson.order || index + 1).padStart(2, "0"),
          ),
          el("span", "", lesson.title || lesson.id),
        );
        if (lesson.completed) button.append(el("span", "lesson-check", "✓"));
        else if (lesson.written === false) {
          button.append(el("span", "lesson-missing", "待生成"));
        }
        button.onclick = () => selectLesson(lesson.id);
        lessonNav.append(button);
      });
    }

    function applyProgress(progress) {
      state.course.progress = progress;
      const done = new Set(progress.completed || []);
      for (const lesson of state.course.lessons || []) {
        lesson.completed = done.has(lesson.id);
      }
      renderNav();
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
      fetch(
        `/api/courses/${encodeURIComponent(state.courseId)}/progress`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ lesson_id: lessonId }),
        },
      )
        .then((response) => (response.ok ? response.json() : null))
        .then((data) => {
          if (seq !== progressSeq) return;
          if (data && data.progress) applyProgress(data.progress);
        })
        .catch((err) => console.error(err));
    }

    function embedCanvases() {
      for (const p of [...content.querySelectorAll("p")]) {
        const match = (p.textContent || "")
          .trim()
          .match(/^\{\{canvas:([A-Za-z0-9_-]+)\}\}$/);
        if (!match) continue;
        const frame = document.createElement("iframe");
        frame.className = "canvas-embed";
        frame.title = "交互演示";
        frame.loading = "lazy";
        frame.referrerPolicy = "no-referrer";
        frame.sandbox =
          "allow-scripts allow-forms allow-popups allow-modals allow-downloads";
        frame.src = `/courses/${encodeURIComponent(state.courseId)}/canvas/${encodeURIComponent(match[1])}`;
        p.replaceWith(frame);
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
          "课程生成可能在写入课节前中断。在右侧对话框继续，Walle 会按章节顺序补写。",
        ),
      );
      const button = el("button", "continue-link", "填入续写提示");
      button.type = "button";
      button.onclick = () => onContinueLesson(continuePrompt(lesson));
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
      renderNav();
      updateActions();
      updateCompleteButton();
      markLastLesson(lessonId);
      readerHead.replaceChildren();
      content.replaceChildren(el("div", "empty", "加载课节…"));
      try {
        const data = await api(
          `/api/courses/${encodeURIComponent(state.courseId)}/lessons/${encodeURIComponent(lessonId)}`,
        );
        if (seq !== state.lessonSeq || !state.active) return;
        const lesson = data.lesson;
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
        embedCanvases();
        content.scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (err) {
        if (seq !== state.lessonSeq || !state.active) return;
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
        content.replaceChildren();
        const empty = el("div", "empty");
        empty.append(
          el("strong", "", "课程还没有课节"),
          document.createTextNode("在右侧对话框让 Walle 继续写入课程内容。"),
        );
        content.append(empty);
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
      try {
        const course = await api(
          `/api/courses/${encodeURIComponent(courseId)}`,
        );
        if (!state.active || state.requestSeq !== seq) return false;
        state.course = course;
        title.textContent = course.title || course.id;
        document.title = `${course.title || course.id} · Walle`;
        goal.textContent = course.goal || "";
        exportLink.href = `/courses/${encodeURIComponent(courseId)}/export`;
        renderNav();
        updateProgressUI();
        startLesson(search, course);
        return true;
      } catch (err) {
        if (!state.active || state.requestSeq !== seq) return false;
        title.textContent = "课程不存在";
        content.replaceChildren(
          el("div", "empty", `课程加载失败：${err.message}`),
        );
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
      conversation.exitCourse();
      document.title = "Walle";
    }

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
    continueLearning.addEventListener("click", () => {
      const target = continueTarget();
      if (target) selectLesson(target.id);
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
          <p>每个课程包包含学习计划、Markdown 课节和交互画布。进入课程后，右侧就是当前主对话。</p>
        </div>
        <div class="courses-ish"><a href="/" data-route>返回对话</a></div>
      </div>
      <div id="courses-grid" class="courses-grid"></div>
    </div>
  </section>
  <section id="course-view" class="page-view" hidden>
    <div class="course-page">
      <div class="course-head">
        <div class="course-breadcrumb">
          <a href="/courses" data-route>全部课程</a>
          <span>/</span>
          <h1 id="course-title">课程</h1>
        </div>
        <a id="export-link" class="ghost" href="#">导出课程</a>
      </div>
      <div class="course-layout">
        <aside class="course-sidebar">
          <div id="course-goal" class="course-goal"></div>
          <button id="continue-learning" class="continue-learning" type="button">开始学习</button>
          <div id="plan-meta" class="nav-head">学习计划</div>
          <nav id="lesson-nav" class="lesson-nav"></nav>
        </aside>
        <main class="course-reader">
          <div id="reader-head" class="reader-head"></div>
          <article id="content" class="course-md"></article>
          <div class="lesson-actions">
            <button id="prev" type="button">上一课</button>
            <button id="complete-lesson" type="button">标记完成</button>
            <button id="next" type="button">下一课</button>
          </div>
        </main>
      </div>
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
      onContinueLesson: (prompt) => ctx.setPrompt(prompt),
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
