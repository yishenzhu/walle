/* 会话客户端：连接、状态与命令。

视图不持有 WebSocket；页面切换只换视图，不动连接。 */
(function () {
  const DEFAULT_AGENT = "default";
  const MAX_RECONNECT_DELAY = 8000;

  class SessionConn {
    constructor({ buildUrl, onOpen, onMessage, onClose, onError }) {
      this.buildUrl = buildUrl;
      this.onOpen = onOpen;
      this.onMessage = onMessage;
      this.onClose = onClose;
      this.onError = onError;
      this.socket = null;
    }

    get isOpen() {
      return !!this.socket && this.socket.readyState === WebSocket.OPEN;
    }

    open(target) {
      this.close({ silent: true });
      const socket = new WebSocket(this.buildUrl(target));
      this.socket = socket;
      socket.onopen = () => {
        if (this.socket === socket) this.onOpen && this.onOpen();
      };
      socket.onmessage = (event) => {
        if (this.socket !== socket) return;
        this.onMessage && this.onMessage(event);
      };
      socket.onclose = () => {
        if (this.socket !== socket) return;
        this.socket = null;
        this.onClose && this.onClose();
      };
      socket.onerror = () => {
        if (this.socket === socket) this.onError && this.onError();
      };
    }

    send(frame) {
      if (!this.isOpen) return false;
      this.socket.send(JSON.stringify(frame));
      return true;
    }

    close({ silent = false } = {}) {
      const socket = this.socket;
      this.socket = null;
      if (!socket) return;
      if (silent) {
        socket.onopen = null;
        socket.onmessage = null;
        socket.onclose = null;
        socket.onerror = null;
      }
      try {
        socket.close();
      } catch (err) {
        console.error(err);
      }
    }
  }

  class ConversationStore {
    constructor(initial = {}) {
      this.state = {
        cwd: "",
        sessionId: "",
        agentMode: "",
        courseId: "",
        lessonId: "",
        ...initial,
      };
      this.listeners = new Set();
    }

    get() {
      return this.state;
    }

    set(patch) {
      const next = { ...this.state, ...patch };
      let changed = false;
      for (const key of Object.keys(patch)) {
        if (this.state[key] !== next[key]) {
          changed = true;
          break;
        }
      }
      if (!changed) return;
      this.state = next;
      for (const listener of this.listeners) listener(this.state);
    }

    subscribe(listener) {
      this.listeners.add(listener);
      return () => this.listeners.delete(listener);
    }
  }

  class ConversationController {
    constructor({
      store,
      onMessage,
      onStatus,
      onOpen,
      onBeforeConnect,
      onSessionChange,
    }) {
      this.store = store;
      this.onMessage = onMessage;
      this.onStatus = onStatus || (() => {});
      this.onOpen = onOpen || (() => {});
      this.onBeforeConnect = onBeforeConnect || (() => {});
      this.onSessionChange = onSessionChange || (() => {});
      this.target = null;
      this.reconnectTimer = null;
      this.reconnectAttempts = 0;
      this.suppressed = false;
      this.suppressStatus = "已断开";
      this.pendingAgent = null;
      this.pendingCourse = null;
      this.courseAgent = null;
      this.connection = new SessionConn({
        buildUrl: (target) => this.buildUrl(target),
        onOpen: () => this.handleOpen(),
        onMessage: (event) => this.handleMessage(event),
        onClose: () => this.handleClose(),
        onError: () => this.onStatus("连接错误", "off"),
      });
    }

    get isOpen() {
      return this.connection.isOpen;
    }

    buildUrl(target) {
      const params = new URLSearchParams();
      if (target.cwd) params.set("cwd", target.cwd);
      params.set("session", target.sessionId);
      if (target.attach) params.set("attach", "1");
      const agent = this.desiredAgent();
      if (agent) params.set("agent", agent);
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const query = params.toString();
      return `${proto}://${location.host}/ws/chat${query ? `?${query}` : ""}`;
    }

    desiredAgent() {
      const state = this.store.get();
      if (state.courseId) return this.courseAgent || "tutor";
      return this.pendingAgent || state.agentMode || "";
    }

    connect(cwd, sessionId, attach) {
      this.clearReconnect();
      this.suppressed = false;
      this.reconnectAttempts = 0;
      this.target = { cwd: cwd || "", sessionId, attach: !!attach };
      this.store.set({ cwd: cwd || "", sessionId });
      this.onSessionChange(cwd || "", sessionId);
      this.onBeforeConnect();
      this.openSocket();
    }

    reconnect() {
      if (!this.target || this.suppressed) return;
      this.target.attach = true;
      this.openSocket();
    }

    openSocket() {
      if (!this.target) return;
      this.clearReconnect();
      this.connection.open({
        cwd: this.target.cwd,
        sessionId: this.target.sessionId,
        attach: this.target.attach,
      });
    }

    handleOpen() {
      const pendingAgent = this.pendingAgent;
      this.pendingAgent = null;
      this.pendingCourse = null;
      this.courseAgent = null;
      this.onOpen();
      this.onStatus("已连接", "on");
      if (pendingAgent) {
        this.sendControl("session.set_agent", { agent: pendingAgent });
      }
      this.applyCourse();
    }

    handleMessage(event) {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch (err) {
        console.error(err);
        return;
      }
      if (msg.type === "welcome") this.noteWelcome();
      this.onMessage(msg);
    }

    handleClose() {
      if (this.suppressed) {
        this.onStatus(this.suppressStatus, "off");
        return;
      }
      this.onStatus("已断开", "off");
      this.scheduleReconnect();
    }

    scheduleReconnect() {
      if (this.suppressed || this.reconnectTimer || !this.target) return;
      const delay = Math.min(
        1000 * (2 ** this.reconnectAttempts),
        MAX_RECONNECT_DELAY,
      );
      this.reconnectAttempts += 1;
      this.onStatus("重连中…", "off");
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null;
        this.reconnect();
      }, delay);
    }

    noteWelcome() {
      this.reconnectAttempts = 0;
      this.suppressed = false;
      this.suppressStatus = "已断开";
    }

    suppressReconnect(statusText) {
      this.suppressed = true;
      this.suppressStatus = statusText || "已断开";
      if (statusText) this.onStatus(statusText, "off");
    }

    send(frame) {
      return this.connection.send(frame);
    }

    sendControl(type, fields = {}) {
      return this.send({ type, ...fields });
    }

    sendInput(content) {
      return this.send({ type: "input.create", content });
    }

    cancel() {
      return this.send({ type: "run.cancel" });
    }

    setAgent(agent) {
      this.courseAgent = agent || null;
      const target = agent || DEFAULT_AGENT;
      if (!this.sendControl("session.set_agent", { agent: target })) {
        this.pendingAgent = target;
      } else {
        this.pendingAgent = null;
      }
    }

    setMode(mode) {
      this.store.set({ agentMode: mode || "" });
      const agent = this.store.get().courseId
        ? "tutor"
        : mode || DEFAULT_AGENT;
      this.setAgent(agent);
    }

    enterCourse(courseId, lessonId, sessionId = "", cwd = "") {
      this.courseAgent = "tutor";
      this.store.set({ courseId: courseId || "", lessonId: lessonId || "" });
      const state = this.store.get();
      const targetSession = sessionId || state.sessionId;
      const targetCwd = cwd || state.cwd;
      // 课程属于生成它的那段对话：进入课程就回到原会话，不另起上下文。
      if (
        targetSession &&
        (targetSession !== state.sessionId ||
          targetCwd !== state.cwd ||
          !this.isOpen)
      ) {
        this.connect(targetCwd, targetSession, true);
        return;
      }
      this.applyCourse();
      if (!this.sendControl("session.set_agent", { agent: "tutor" })) {
        this.pendingAgent = "tutor";
      }
    }

    setCourse(courseId, lessonId) {
      this.store.set({ courseId: courseId || "", lessonId: lessonId || "" });
      this.applyCourse();
    }

    exitCourse() {
      this.courseAgent = null;
      this.store.set({ courseId: "", lessonId: "" });
      this.applyCourse();
      const agent = this.store.get().agentMode || DEFAULT_AGENT;
      if (!this.sendControl("session.set_agent", { agent })) {
        this.pendingAgent = agent;
      }
    }

    applyCourse() {
      const state = this.store.get();
      if (
        !this.sendControl("session.set_metadata", {
          values: {
            course_id: state.courseId || null,
            lesson_id: state.lessonId || null,
          },
        })
      ) {
        this.pendingCourse = {
          courseId: state.courseId,
          lessonId: state.lessonId,
        };
      } else {
        this.pendingCourse = null;
      }
    }

    clearReconnect() {
      if (this.reconnectTimer) {
        clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
      }
    }
  }

  window.WalleConversation = {
    SessionConn,
    ConversationStore,
    ConversationController,
  };
})();
