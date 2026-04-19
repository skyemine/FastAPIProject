const state = {
  session: null,
  friends: [],
  requests: [],
  activeFriend: null,
  activeFriendMeta: null,
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
  sidebarOverlay: document.getElementById("sidebar-overlay"),
  sidebarClose: document.getElementById("sidebar-close"),
};

function switchAuthTab(mode) {
  const loginActive = mode === "login";
  elements.loginTab.classList.toggle("active", loginActive);
  elements.registerTab.classList.toggle("active", !loginActive);
  elements.loginForm.classList.toggle("active", loginActive);
  elements.registerForm.classList.toggle("active", !loginActive);
  elements.authError.textContent = "";
}

function toggleSidebar(show) {
  const isOpen =
    show !== undefined ? show : !elements.sidebar.classList.contains("open");
  elements.sidebar.classList.toggle("open", isOpen);
  elements.sidebarOverlay.classList.toggle("show", isOpen);
  document.body.classList.toggle("no-scroll", isOpen);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  if (response.status === 204) return null;
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) throw new Error(payload.detail || payload || "Request failed");
  return payload;
}

function setStatus(message) {
  elements.statusLine.textContent = message;
}

function formatDate(value) {
  const date = new Date(value);
  return date.toLocaleString([], {
    hour: "2-digit",
    minute: "2-digit",
    day: "2-digit",
    month: "short",
  });
}

function renderRequests() {
  elements.requestsList.innerHTML = "";
  if (!state.requests.length) {
    elements.requestsList.innerHTML =
      '<div class="empty-small">No pending requests.</div>';
    return;
  }
  for (const item of state.requests) {
    const card = document.createElement("div");
    card.className = "list-card";
    card.innerHTML = `
      <div class="list-user">
        <div class="avatar mini">${item.requester.initials}</div>
        <div>
          <strong>${item.requester.display_name}</strong>
          <span>@${item.requester.username}</span>
        </div>
      </div>
      <div class="actions-row">
        <button class="primary-btn small" data-action="accept">Accept</button>
        <button class="ghost-btn small" data-action="reject">Decline</button>
      </div>
    `;
    card
      .querySelector('[data-action="accept"]')
      .addEventListener("click", () => handleFriendRequest(item.id, "accept"));
    card
      .querySelector('[data-action="reject"]')
      .addEventListener("click", () => handleFriendRequest(item.id, "reject"));
    elements.requestsList.appendChild(card);
  }
}

function renderFriends() {
  elements.friendsList.innerHTML = "";
  if (!state.friends.length) {
    elements.friendsList.innerHTML =
      '<div class="empty-small">No friends yet.<br>Add someone by username.</div>';
    return;
  }
  for (const friend of state.friends) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "friend-item";
    button.classList.toggle("active", friend.username === state.activeFriend);
    button.innerHTML = `
      <div class="list-user">
        <div class="avatar mini">${friend.initials}</div>
        <div>
          <strong>${friend.display_name}</strong>
          <span>@${friend.username}</span>
        </div>
      </div>
      <span class="presence-dot ${friend.is_online ? "online" : ""}"></span>
    `;
    button.addEventListener("click", () => selectFriend(friend.username));
    elements.friendsList.appendChild(button);
  }
}

function renderMessages() {
  elements.messageStream.innerHTML = "";
  const messages = state.messagesByFriend.get(state.activeFriend) || [];
  if (!messages.length) {
    elements.messageStream.innerHTML =
      '<div class="empty-state">No messages yet.<br>Start this conversation.</div>';
    return;
  }
  for (const message of messages) {
    const mine =
      state.session &&
      message.sender_username === state.session.user.username;
    const article = document.createElement("article");
    article.className = `message ${mine ? "mine" : ""}`;
    article.innerHTML = `
      <div class="message-bubble">
        <div class="message-head">
          <strong>${message.sender_display_name}</strong>
          <span>${formatDate(message.sent_at)}</span>
        </div>
        <div>${message.content}</div>
      </div>
    `;
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
  const friend = state.friends.find(
    (item) => item.username === state.activeFriend
  );
  state.activeFriendMeta = friend || null;
  if (!friend) {
    elements.chatAvatar.textContent = "DM";
    elements.chatTitle.textContent = "Select a friend";
    elements.chatSubtitle.textContent =
      "Accept a request or choose a friend from the list.";
    elements.chatStatus.textContent = "Offline";
    elements.chatStatus.classList.remove("online");
    elements.composerInput.disabled = true;
    elements.sendBtn.disabled = true;
    return;
  }
  elements.chatAvatar.textContent = friend.initials;
  elements.chatTitle.textContent = friend.display_name;
  elements.chatSubtitle.textContent = `@${friend.username}`;
  elements.chatStatus.textContent = friend.is_online ? "Online" : "Offline";
  elements.chatStatus.classList.toggle("online", friend.is_online);
  elements.composerInput.disabled = false;
  elements.sendBtn.disabled = false;
}

function connectSocket(username) {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(
    `${protocol}://${window.location.host}/ws/direct/${username}`
  );
  state.socket = socket;
  setStatus(`Connecting to @${username}...`);

  socket.addEventListener("open", () => {
    setStatus(`Connected to @${username}`);
  });

  socket.addEventListener("close", () => {
    if (state.socket === socket) {
      setStatus("Disconnected");
    }
  });

  socket.addEventListener("message", (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === "history") {
      state.messagesByFriend.set(username, payload.messages);
      const friend = state.friends.find((item) => item.username === username);
      if (friend) {
        friend.is_online = payload.friend.is_online;
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
      setStatus(payload.detail);
    }
  });
}

async function selectFriend(username) {
  state.activeFriend = username;
  renderFriends();
  updateActiveFriendMeta();
  disconnectSocket();
  connectSocket(username);
  renderMessages();
  toggleSidebar(false);
}

async function loadFriends() {
  state.friends = await api("/api/friends");
  renderFriends();
  updateActiveFriendMeta();
  if (
    state.activeFriend &&
    !state.friends.find((item) => item.username === state.activeFriend)
  ) {
    state.activeFriend = null;
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
  elements.userAvatar.textContent = state.session.user.initials;
  elements.userName.textContent = state.session.user.display_name;
  elements.userHandle.textContent = `@${state.session.user.username}`;
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
    await refreshSession();
  } catch (error) {
    elements.authError.textContent = error.message;
  }
}

async function handleFriendAdd(event) {
  event.preventDefault();
  const payload = { username: elements.friendUsername.value.trim() };
  if (!payload.username) return;
  try {
    await api("/api/friend-requests", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    elements.friendUsername.value = "";
    setStatus("Friend request sent.");
  } catch (error) {
    setStatus(error.message);
  }
}

async function handleFriendRequest(requestId, action) {
  try {
    await api(`/api/friend-requests/${requestId}/${action}`, {
      method: "POST",
    });
    await Promise.all([loadRequests(), loadFriends()]);
    setStatus(
      action === "accept" ? "Friend added." : "Friend request declined."
    );
  } catch (error) {
    setStatus(error.message);
  }
}

async function handleComposerSubmit(event) {
  event.preventDefault();
  const content = elements.composerInput.value.trim();
  if (
    !content ||
    !state.socket ||
    state.socket.readyState !== WebSocket.OPEN
  ) {
    return;
  }
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
}

function bindEvents() {
  elements.loginTab.addEventListener("click", () => switchAuthTab("login"));
  elements.registerTab.addEventListener("click", () =>
    switchAuthTab("register")
  );
  elements.loginForm.addEventListener("submit", (event) => {
    event.preventDefault();
    submitAuth("/api/auth/login", elements.loginForm);
  });
  elements.registerForm.addEventListener("submit", (event) => {
    event.preventDefault();
    submitAuth("/api/auth/register", elements.registerForm);
  });
  elements.friendForm.addEventListener("submit", handleFriendAdd);
  elements.logoutBtn.addEventListener("click", logout);
  elements.composerForm.addEventListener("submit", handleComposerSubmit);
  elements.composerInput.addEventListener("input", autoresizeComposer);

  elements.sidebarToggle.addEventListener("click", () => toggleSidebar());
  elements.sidebarClose.addEventListener("click", () => toggleSidebar(false));
  elements.sidebarOverlay.addEventListener("click", () => toggleSidebar(false));

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && elements.sidebar.classList.contains("open")) {
      toggleSidebar(false);
    }
  });
}

async function init() {
  bindEvents();
  switchAuthTab("login");
  updateActiveFriendMeta();
  renderFriends();
  renderRequests();
  renderMessages();
  try {
    await refreshSession();
  } catch {
    setStatus("Ready for login");
  }
}

init();