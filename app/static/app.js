const state = {
  session: null,
  friends: [],
  requests: [],
  activeFriend: null,
  activeFriendMeta: null,
  messagesByFriend: new Map(),
  socket: null
};

const MOBILE_WIDTH = 860;
const THEME_KEY = "prism_theme";
const THEMES = ["light", "dark"];

const elements = {
  authShell: document.getElementById("auth-shell"),
  appShell: document.getElementById("app-shell"),
  authError: document.getElementById("auth-error"),
  loginForm: document.getElementById("login-form"),
  registerForm: document.getElementById("register-form"),
  loginTab: document.getElementById("tab-login"),
  registerTab: document.getElementById("tab-register"),
  logoutBtn: document.getElementById("logout-btn"),
  userAvatar: document.getElementById("user-avatar"),
  userName: document.getElementById("user-name"),
  userHandle: document.getElementById("user-handle"),
  friendAddBtn: document.getElementById("friend-add-btn"),
  friendUsername: document.getElementById("friend-username"),
  requestsList: document.getElementById("requests-list"),
  friendsList: document.getElementById("friends-list"),
  messageStream: document.getElementById("message-stream"),
  composerForm: document.getElementById("composer-form"),
  composerInput: document.getElementById("composer-input"),
  sendBtn: document.getElementById("send-btn"),
  statusLine: document.getElementById("status-line"),
  chatAvatar: document.getElementById("chat-avatar"),
  chatTitle: document.getElementById("chat-title"),
  chatSubtitle: document.getElementById("chat-subtitle"),
  chatStatus: document.getElementById("chat-status"),
  sidebar: document.getElementById("sidebar"),
  sidebarToggle: document.getElementById("sidebar-toggle"),
  sidebarOverlay: document.getElementById("sidebar-overlay"),
  sidebarClose: document.getElementById("sidebar-close"),
  themeBtn: document.getElementById("theme-btn"),
  themeBtnApp: document.getElementById("theme-btn-app")
};

function isMobileLayout() {
  return window.matchMedia(`(max-width: ${MOBILE_WIDTH}px)`).matches;
}

function applyTheme(theme) {
  const t = THEMES.includes(theme) ? theme : "light";
  document.documentElement.setAttribute("data-theme", t);
  localStorage.setItem(THEME_KEY, t);
  const label = t[0].toUpperCase() + t.slice(1);
  if (elements.themeBtn) elements.themeBtn.textContent = `Тема: ${label}`;
  if (elements.themeBtnApp) elements.themeBtnApp.textContent = label;
}

function nextTheme() {
  const current = document.documentElement.getAttribute("data-theme") || "light";
  const idx = THEMES.indexOf(current);
  applyTheme(THEMES[(idx + 1) % THEMES.length]);
}

function initTheme() {
  const saved = localStorage.getItem(THEME_KEY);
  // migrate old petal-soft saves to light
  const valid = saved && THEMES.includes(saved) ? saved : "light";
  applyTheme(valid);
}

function switchAuthTab(mode) {
  const loginActive = mode === "login";
  elements.loginTab.classList.toggle("active", loginActive);
  elements.registerTab.classList.toggle("active", !loginActive);
  elements.loginForm.classList.toggle("active", loginActive);
  elements.registerForm.classList.toggle("active", !loginActive);
  elements.loginTab.setAttribute("aria-selected", String(loginActive));
  elements.registerTab.setAttribute("aria-selected", String(!loginActive));
  elements.authError.textContent = "";
}

function toggleSidebar(show) {
  const isOpen = show !== undefined ? show : !elements.sidebar.classList.contains("open");
  elements.sidebar.classList.toggle("open", isOpen);
  if (elements.sidebarToggle) elements.sidebarToggle.setAttribute("aria-expanded", String(isOpen));
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {})
    },
    ...options
  });

  if (response.status === 204) return null;

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();

  if (!response.ok) throw new Error(payload?.detail || payload || "Request failed");
  return payload;
}

function setStatus(message) {
  elements.statusLine.textContent = message;
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString([], {
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "short"
  });
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderRequests() {
  elements.requestsList.innerHTML = "";
  if (!state.requests.length) {
    elements.requestsList.appendChild(el("div", "empty-small", "Немає вхідних запитів."));
    return;
  }

  for (const item of state.requests) {
    const card = el("div", "list-card");
    const user = el("div", "list-user");
    user.appendChild(el("div", "avatar mini", item.requester.initials || "??"));
    const meta = el("div");
    meta.appendChild(el("strong", "", item.requester.display_name || item.requester.username));
    meta.appendChild(el("span", "", `@${item.requester.username}`));
    user.appendChild(meta);

    const actions = el("div", "actions-row");
    const acceptBtn = el("button", "primary-btn small", "Прийняти");
    acceptBtn.type = "button";
    acceptBtn.addEventListener("click", () => handleFriendRequest(item.id, "accept"));

    const rejectBtn = el("button", "ghost-btn small", "Відхилити");
    rejectBtn.type = "button";
    rejectBtn.addEventListener("click", () => handleFriendRequest(item.id, "reject"));

    actions.appendChild(acceptBtn);
    actions.appendChild(rejectBtn);
    card.appendChild(user);
    card.appendChild(actions);
    elements.requestsList.appendChild(card);
  }
}

function renderFriends() {
  elements.friendsList.innerHTML = "";
  if (!state.friends.length) {
    const empty = el("div", "empty-small");
    empty.innerHTML = "Друзів ще немає.<br>Додайте когось за username.";
    elements.friendsList.appendChild(empty);
    return;
  }

  for (const friend of state.friends) {
    const button = el("button", "friend-item");
    button.type = "button";
    button.classList.toggle("active", friend.username === state.activeFriend);

    const user = el("div", "list-user");
    user.appendChild(el("div", "avatar mini", friend.initials || "??"));
    const meta = el("div");
    meta.appendChild(el("strong", "", friend.display_name || friend.username));
    meta.appendChild(el("span", "", `@${friend.username}`));
    user.appendChild(meta);

    const dot = el("span", `presence-dot ${friend.is_online ? "online" : ""}`);
    button.appendChild(user);
    button.appendChild(dot);
    button.addEventListener("click", () => selectFriend(friend.username));
    elements.friendsList.appendChild(button);
  }
}

function renderMessages() {
  elements.messageStream.innerHTML = "";
  const messages = state.messagesByFriend.get(state.activeFriend) || [];

  if (!messages.length) {
    const empty = el("div", "empty-state");
    empty.appendChild(el("strong", "", "Повідомлень ще немає"));
    empty.appendChild(el("p", "", "Почніть розмову першими"));
    elements.messageStream.appendChild(empty);
    return;
  }

  for (const message of messages) {
    const mine = state.session && message.sender_username === state.session.user.username;
    const article = el("article", `message ${mine ? "mine" : ""}`);
    const bubble = el("div", "message-bubble");
    const head = el("div", "message-head");
    head.appendChild(el("strong", "", message.sender_display_name || message.sender_username || "User"));
    head.appendChild(el("span", "", formatDate(message.sent_at)));
    bubble.appendChild(head);
    bubble.appendChild(el("div", "", message.content || ""));
    article.appendChild(bubble);
    elements.messageStream.appendChild(article);
  }

  elements.messageStream.scrollTop = elements.messageStream.scrollHeight;
}

function disconnectSocket() {
  if (state.socket) {
    state.socket.close();
    state.socket = null;
  }
}

function updateActiveFriendMeta() {
  const friend = state.friends.find(item => item.username === state.activeFriend);
  state.activeFriendMeta = friend || null;

  if (!friend) {
    elements.chatTitle.textContent = "Виберіть друга";
    elements.chatSubtitle.textContent = "Прийміть запит або виберіть друга зі списку.";
    elements.chatAvatar.textContent = "DM";
    elements.chatStatus.textContent = "Офлайн";
    elements.composerInput.disabled = true;
    elements.sendBtn.disabled = true;
    return;
  }

  elements.chatTitle.textContent = friend.display_name || friend.username;
  elements.chatSubtitle.textContent = `@${friend.username}`;
  elements.chatAvatar.textContent = friend.initials || "??";
  elements.chatStatus.textContent = friend.is_online ? "Онлайн" : "Офлайн";
  elements.composerInput.disabled = false;
  elements.sendBtn.disabled = false;
}

function connectSocket(username) {
  disconnectSocket();
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${location.host}/ws/chat/${username}`);
  state.socket = socket;

  setStatus(`Підключення до @${username}...`);

  socket.addEventListener("open", () => setStatus(`Чат з @${username} підключено`));
  socket.addEventListener("close", () => {
    if (state.socket === socket) setStatus("З'єднання розірвано");
  });

  socket.addEventListener("message", event => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }

    if (payload.type === "history") {
      state.messagesByFriend.set(username, payload.messages || []);
      const friend = state.friends.find(item => item.username === username);
      if (friend && payload.friend) {
        friend.is_online = !!payload.friend.is_online;
        renderFriends();
        updateActiveFriendMeta();
      }
      renderMessages();
      return;
    }

    if (payload.type === "message") {
      const list = state.messagesByFriend.get(username) || [];
      list.push(payload.message);
      state.messagesByFriend.set(username, list);
      renderMessages();
      loadFriends();
      return;
    }

    if (payload.type === "error") setStatus(payload.detail || "Помилка чату");
  });
}

async function selectFriend(username) {
  if (!username) return;
  state.activeFriend = username;
  renderFriends();
  updateActiveFriendMeta();
  disconnectSocket();
  connectSocket(username);
  renderMessages();
  if (isMobileLayout()) toggleSidebar(false);
  setTimeout(() => elements.composerInput.focus(), 50);
}

async function loadFriends() {
  state.friends = await api("/api/friends");
  renderFriends();
  updateActiveFriendMeta();

  if (state.activeFriend && !state.friends.find(item => item.username === state.activeFriend)) {
    state.activeFriend = null;
    disconnectSocket();
    updateActiveFriendMeta();
    renderMessages();
  }
}

async function loadRequests() {
  state.requests = await api("/api/friend-requests");
  renderRequests();
}

async function refreshSession() {
  state.session = await api("/api/session");

  if (!state.session.authenticated) {
    elements.authShell.classList.remove("hidden");
    elements.appShell.classList.add("hidden");
    disconnectSocket();
    return false;
  }

  elements.authShell.classList.add("hidden");
  elements.appShell.classList.remove("hidden");
  elements.userAvatar.textContent = state.session.user.initials || "PR";
  elements.userName.textContent = state.session.user.display_name || "Користувач";
  elements.userHandle.textContent = `@${state.session.user.username}`;

  await Promise.all([loadFriends(), loadRequests()]);
  if (!state.activeFriend && state.friends.length) await selectFriend(state.friends[0].username);
  return true;
}

async function submitAuth(path, formElement) {
  const payload = Object.fromEntries(new FormData(formElement).entries());
  try {
    await api(path, { method: "POST", body: JSON.stringify(payload) });
    elements.authError.textContent = "";
    await refreshSession();
  } catch (error) {
    elements.authError.textContent = error.message;
  }
}

async function handleFriendAdd(event) {
  if (event && event.preventDefault) event.preventDefault();
  const payload = { username: elements.friendUsername.value.trim() };
  if (!payload.username) return;

  try {
    await api("/api/friend-requests", { method: "POST", body: JSON.stringify(payload) });
    elements.friendUsername.value = "";
    setStatus("Запит дружби надіслано");
  } catch (error) {
    setStatus(error.message);
  }
}

async function handleFriendRequest(requestId, action) {
  try {
    await api(`/api/friend-requests/${requestId}/${action}`, { method: "POST" });
    await Promise.all([loadRequests(), loadFriends()]);
    setStatus(action === "accept" ? "Друга додано" : "Запит відхилено");
  } catch (error) {
    setStatus(error.message);
  }
}

async function handleComposerSubmit(event) {
  event.preventDefault();
  const content = elements.composerInput.value.trim();
  if (!content || !state.socket || state.socket.readyState !== WebSocket.OPEN) return;
  state.socket.send(JSON.stringify({ content }));
  elements.composerInput.value = "";
  autoresizeComposer();
}

function autoresizeComposer() {
  elements.composerInput.style.height = "auto";
  elements.composerInput.style.height = `${Math.min(elements.composerInput.scrollHeight, 100)}px`;
}

async function logout() {
  await api("/api/auth/logout", { method: "POST" });
  state.session = null;
  state.friends = [];
  state.requests = [];
  state.activeFriend = null;
  state.activeFriendMeta = null;
  state.messagesByFriend.clear();
  disconnectSocket();
  elements.authShell.classList.remove("hidden");
  elements.appShell.classList.add("hidden");
  switchAuthTab("login");
  updateActiveFriendMeta();
  renderFriends();
  renderRequests();
  renderMessages();
  toggleSidebar(false);
  setStatus("Ви вийшли з акаунта");
}

function bindEvents() {
  elements.loginTab.addEventListener("click", () => switchAuthTab("login"));
  elements.registerTab.addEventListener("click", () => switchAuthTab("register"));

  elements.loginForm.addEventListener("submit", event => {
    event.preventDefault();
    submitAuth("/api/auth/login", elements.loginForm);
  });

  elements.registerForm.addEventListener("submit", event => {
    event.preventDefault();
    submitAuth("/api/auth/register", elements.registerForm);
  });

  if (elements.friendAddBtn) elements.friendAddBtn.addEventListener("click", handleFriendAdd);
  elements.friendUsername.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); handleFriendAdd(e); }
  });
  elements.logoutBtn.addEventListener("click", logout);
  elements.composerForm.addEventListener("submit", handleComposerSubmit);
  elements.composerInput.addEventListener("input", autoresizeComposer);

  elements.composerInput.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      elements.composerForm.requestSubmit();
    }
  });

  elements.sidebarToggle.addEventListener("click", e => {
    e.preventDefault();
    e.stopPropagation();
    toggleSidebar();
  });

  elements.sidebarClose.addEventListener("click", e => {
    e.preventDefault();
    e.stopPropagation();
    toggleSidebar(false);
  });

  elements.sidebar.addEventListener("click", e => e.stopPropagation());
  elements.sidebar.addEventListener("pointerdown", e => e.stopPropagation());
  elements.sidebar.addEventListener("touchstart", e => e.stopPropagation(), { passive: true });

  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && elements.sidebar.classList.contains("open")) toggleSidebar(false);
  });

  window.addEventListener("resize", () => {
    if (!isMobileLayout() && elements.sidebar.classList.contains("open")) toggleSidebar(false);
  });

  if (elements.themeBtn) elements.themeBtn.addEventListener("click", nextTheme);
  if (elements.themeBtnApp) elements.themeBtnApp.addEventListener("click", nextTheme);
}

async function init() {
  initTheme();
  bindEvents();
  switchAuthTab("login");
  updateActiveFriendMeta();
  renderFriends();
  renderRequests();
  renderMessages();
  try {
    await refreshSession();
  } catch {
    setStatus("Готово до входу");
  }
}

init();