const state = {
  session: null,
  friends: [],
  requests: [],
  activeFriend: null,
  messagesByFriend: new Map(),
  socket: null,
};

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
  friendForm: document.getElementById("friend-form"),
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
  sidebarClose: document.getElementById("sidebar-close"),
  themeBtn: document.getElementById("theme-btn"),
  themeBtnApp: document.getElementById("theme-btn-app"),
  fileInput: document.getElementById("file-input"),
  fileBtn: document.getElementById("file-btn"),
};

function switchAuthTab(mode) {
  const loginActive = mode === "login";
  elements.loginTab?.classList.toggle("active", loginActive);
  elements.registerTab?.classList.toggle("active", !loginActive);
  elements.loginForm?.classList.toggle("active", loginActive);
  elements.registerForm?.classList.toggle("active", !loginActive);
  if (elements.authError) elements.authError.textContent = "";
}

function toggleSidebar(forceOpen) {
  if (!elements.sidebar) return;
  const shouldOpen = forceOpen ?? !elements.sidebar.classList.contains("open");
  elements.sidebar.classList.toggle("open", shouldOpen);
}

function toggleTheme() {
  const current = document.documentElement.getAttribute("data-theme") || "light";
  document.documentElement.setAttribute("data-theme", current === "dark" ? "light" : "dark");
}

async function api(path, options = {}) {
  const hasFormData = options.body instanceof FormData;
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: {
      ...(hasFormData ? {} : { "Content-Type": "application/json" }),
      ...(options.headers || {}),
    },
  });

  if (response.status === 204) {
    return null;
  }

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    throw new Error(payload?.detail || payload || "Request failed");
  }
  return payload;
}

function setStatus(message) {
  if (elements.statusLine) elements.statusLine.textContent = message;
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString([], {
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "short",
  });
}

function createElement(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderRequests() {
  if (!elements.requestsList) return;
  elements.requestsList.innerHTML = "";
  if (!state.requests.length) {
    elements.requestsList.appendChild(createElement("div", "empty-small", "No incoming requests."));
    return;
  }

  for (const item of state.requests) {
    const card = createElement("div", "list-card");
    const user = createElement("div", "list-user");
    user.appendChild(createElement("div", "avatar mini", item.requester.initials || "??"));
    const meta = createElement("div");
    meta.appendChild(createElement("strong", "", item.requester.display_name || item.requester.username));
    meta.appendChild(createElement("span", "", `@${item.requester.username}`));
    user.appendChild(meta);

    const actions = createElement("div", "actions-row");
    const acceptBtn = createElement("button", "primary-btn small", "Accept");
    acceptBtn.type = "button";
    acceptBtn.addEventListener("click", () => handleFriendRequest(item.id, "accept"));
    const rejectBtn = createElement("button", "ghost-btn small", "Decline");
    rejectBtn.type = "button";
    rejectBtn.addEventListener("click", () => handleFriendRequest(item.id, "reject"));
    actions.append(acceptBtn, rejectBtn);

    card.append(user, actions);
    elements.requestsList.appendChild(card);
  }
}

function renderFriends() {
  if (!elements.friendsList) return;
  elements.friendsList.innerHTML = "";
  if (!state.friends.length) {
    elements.friendsList.appendChild(createElement("div", "empty-small", "No friends yet. Add somebody by username."));
    return;
  }

  for (const friend of state.friends) {
    const button = createElement("button", "friend-item");
    button.type = "button";
    button.classList.toggle("active", friend.username === state.activeFriend);

    const user = createElement("div", "list-user");
    user.appendChild(createElement("div", "avatar mini", friend.initials || "??"));
    const meta = createElement("div");
    meta.appendChild(createElement("strong", "", friend.display_name || friend.username));
    meta.appendChild(createElement("span", "", `@${friend.username}`));
    user.appendChild(meta);
    button.appendChild(user);
    button.appendChild(createElement("span", `presence-dot ${friend.is_online ? "online" : ""}`));
    button.addEventListener("click", () => selectFriend(friend.username));
    elements.friendsList.appendChild(button);
  }
}

function renderMessages() {
  if (!elements.messageStream) return;
  elements.messageStream.innerHTML = "";
  const messages = state.messagesByFriend.get(state.activeFriend) || [];
  if (!messages.length) {
    const empty = createElement("div", "empty-state");
    empty.appendChild(createElement("strong", "", "No messages yet"));
    empty.appendChild(createElement("p", "", "Start the conversation or send a file."));
    elements.messageStream.appendChild(empty);
    return;
  }

  for (const message of messages) {
    const mine = state.session && message.sender_username === state.session.user.username;
    const article = createElement("article", `message ${mine ? "mine" : ""}`);
    const bubble = createElement("div", "message-bubble");
    const head = createElement("div", "message-head");
    head.appendChild(createElement("strong", "", message.sender_display_name || message.sender_username || "User"));
    head.appendChild(createElement("span", "", formatDate(message.sent_at)));
    bubble.appendChild(head);

    if (message.content) {
      bubble.appendChild(createElement("div", "message-text", message.content));
    }

    if (message.attachment_url) {
      const attachment = createElement("a", "attachment-card");
      attachment.href = message.attachment_url;
      attachment.target = "_blank";
      attachment.rel = "noreferrer";
      attachment.appendChild(createElement("strong", "", message.attachment_name || "Attachment"));
      const meta = [];
      if (message.attachment_mime_type) meta.push(message.attachment_mime_type);
      if (message.attachment_size) meta.push(`${Math.ceil(message.attachment_size / 1024)} KB`);
      attachment.appendChild(createElement("span", "", meta.join(" • ")));
      bubble.appendChild(attachment);
    }

    article.appendChild(bubble);
    elements.messageStream.appendChild(article);
  }

  elements.messageStream.scrollTop = elements.messageStream.scrollHeight;
}

function updateActiveFriendMeta() {
  const friend = state.friends.find((item) => item.username === state.activeFriend);
  if (!friend) {
    if (elements.chatTitle) elements.chatTitle.textContent = "Choose a friend";
    if (elements.chatSubtitle) elements.chatSubtitle.textContent = "Accept a request or choose a friend from the list.";
    if (elements.chatAvatar) elements.chatAvatar.textContent = "DM";
    if (elements.chatStatus) elements.chatStatus.textContent = "Offline";
    if (elements.composerInput) elements.composerInput.disabled = true;
    if (elements.sendBtn) elements.sendBtn.disabled = true;
    if (elements.fileBtn) elements.fileBtn.disabled = true;
    return;
  }

  if (elements.chatTitle) elements.chatTitle.textContent = friend.display_name || friend.username;
  if (elements.chatSubtitle) elements.chatSubtitle.textContent = `@${friend.username}`;
  if (elements.chatAvatar) elements.chatAvatar.textContent = friend.initials || "??";
  if (elements.chatStatus) elements.chatStatus.textContent = friend.is_online ? "Online" : "Offline";
  if (elements.composerInput) elements.composerInput.disabled = false;
  if (elements.sendBtn) elements.sendBtn.disabled = false;
  if (elements.fileBtn) elements.fileBtn.disabled = false;
}

function disconnectSocket() {
  if (state.socket) {
    state.socket.close();
    state.socket = null;
  }
}

function connectSocket(username) {
  disconnectSocket();
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws/direct/${username}`);
  state.socket = socket;
  setStatus(`Connecting to @${username}...`);

  socket.addEventListener("open", () => setStatus(`Connected to @${username}`));
  socket.addEventListener("close", () => {
    if (state.socket === socket) setStatus("Connection closed");
  });

  socket.addEventListener("message", (event) => {
    let payload;
    try {
      payload = JSON.parse(event.data);
    } catch {
      return;
    }

    if (payload.type === "history") {
      state.messagesByFriend.set(username, payload.messages || []);
      const friend = state.friends.find((item) => item.username === username);
      if (friend && payload.friend) {
        friend.is_online = Boolean(payload.friend.is_online);
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

    if (payload.type === "error") {
      setStatus(payload.detail || "Chat error");
    }
  });
}

async function selectFriend(username) {
  if (!username) return;
  state.activeFriend = username;
  renderFriends();
  updateActiveFriendMeta();
  connectSocket(username);
  renderMessages();
  toggleSidebar(false);
}

async function loadFriends() {
  state.friends = await api("/api/friends");
  renderFriends();

  if (state.activeFriend && !state.friends.find((item) => item.username === state.activeFriend)) {
    state.activeFriend = null;
    disconnectSocket();
  }

  updateActiveFriendMeta();
}

async function loadRequests() {
  state.requests = await api("/api/friend-requests");
  renderRequests();
}

async function refreshSession() {
  state.session = await api("/api/session");
  if (!state.session.authenticated) {
    elements.authShell?.classList.remove("hidden");
    elements.appShell?.classList.add("hidden");
    disconnectSocket();
    return false;
  }

  elements.authShell?.classList.add("hidden");
  elements.appShell?.classList.remove("hidden");
  if (elements.userAvatar) elements.userAvatar.textContent = state.session.user.initials || "??";
  if (elements.userName) elements.userName.textContent = state.session.user.display_name || state.session.user.username;
  if (elements.userHandle) elements.userHandle.textContent = `@${state.session.user.username}`;

  await Promise.all([loadFriends(), loadRequests()]);
  if (!state.activeFriend && state.friends.length) {
    await selectFriend(state.friends[0].username);
  }
  return true;
}

async function submitAuth(path, formElement) {
  const payload = Object.fromEntries(new FormData(formElement).entries());
  try {
    await api(path, { method: "POST", body: JSON.stringify(payload) });
    if (elements.authError) elements.authError.textContent = "";
    await refreshSession();
  } catch (error) {
    if (elements.authError) elements.authError.textContent = error.message;
  }
}

async function handleFriendAdd(event) {
  event.preventDefault();
  const username = elements.friendUsername?.value.trim();
  if (!username) return;
  try {
    await api("/api/friend-requests", {
      method: "POST",
      body: JSON.stringify({ username }),
    });
    elements.friendUsername.value = "";
    setStatus("Friend request sent.");
  } catch (error) {
    setStatus(error.message);
  }
}

async function handleFriendRequest(requestId, action) {
  try {
    await api(`/api/friend-requests/${requestId}/${action}`, { method: "POST" });
    await Promise.all([loadRequests(), loadFriends()]);
    setStatus(action === "accept" ? "Friend request accepted." : "Friend request declined.");
  } catch (error) {
    setStatus(error.message);
  }
}

async function handleComposerSubmit(event) {
  event.preventDefault();
  const content = elements.composerInput?.value.trim();
  if (!content || !state.socket || state.socket.readyState !== WebSocket.OPEN) return;
  state.socket.send(JSON.stringify({ content }));
  elements.composerInput.value = "";
  autoresizeComposer();
}

async function handleFileUpload(event) {
  const file = event.target.files?.[0];
  if (!file || !state.activeFriend) return;
  const body = new FormData();
  body.append("file", file);
  try {
    await api(`/api/direct/${state.activeFriend}/files`, { method: "POST", body });
    setStatus(`File "${file.name}" sent.`);
  } catch (error) {
    setStatus(error.message);
  } finally {
    event.target.value = "";
  }
}

function autoresizeComposer() {
  if (!elements.composerInput) return;
  elements.composerInput.style.height = "auto";
  elements.composerInput.style.height = `${Math.min(elements.composerInput.scrollHeight, 100)}px`;
}

async function logout() {
  await api("/api/auth/logout", { method: "POST" });
  state.session = null;
  state.friends = [];
  state.requests = [];
  state.activeFriend = null;
  state.messagesByFriend.clear();
  disconnectSocket();
  elements.authShell?.classList.remove("hidden");
  elements.appShell?.classList.add("hidden");
  switchAuthTab("login");
  renderFriends();
  renderRequests();
  renderMessages();
  updateActiveFriendMeta();
  setStatus("Signed out");
}

function bindEvents() {
  elements.loginTab?.addEventListener("click", () => switchAuthTab("login"));
  elements.registerTab?.addEventListener("click", () => switchAuthTab("register"));
  elements.loginForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    submitAuth("/api/auth/login", elements.loginForm);
  });
  elements.registerForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    submitAuth("/api/auth/register", elements.registerForm);
  });
  elements.friendForm?.addEventListener("submit", handleFriendAdd);
  elements.logoutBtn?.addEventListener("click", logout);
  elements.composerForm?.addEventListener("submit", handleComposerSubmit);
  elements.composerInput?.addEventListener("input", autoresizeComposer);
  elements.composerInput?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      elements.composerForm.requestSubmit();
    }
  });
  elements.fileBtn?.addEventListener("click", () => elements.fileInput?.click());
  elements.fileInput?.addEventListener("change", handleFileUpload);
  elements.sidebarToggle?.addEventListener("click", () => toggleSidebar());
  elements.sidebarClose?.addEventListener("click", () => toggleSidebar(false));
  elements.themeBtn?.addEventListener("click", toggleTheme);
  elements.themeBtnApp?.addEventListener("click", toggleTheme);
}

async function init() {
  bindEvents();
  switchAuthTab("login");
  renderFriends();
  renderRequests();
  renderMessages();
  updateActiveFriendMeta();
  try {
    await refreshSession();
  } catch {
    setStatus("Ready for login");
  }
}

init();
