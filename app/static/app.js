const state = {
  session: null,
  friends: [],
  requests: [],
  activeFriend: null,
  messagesByFriend: new Map(),
  socket: null,
  theme: localStorage.getItem("prism-theme") || "light",
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
  avatarInput: document.getElementById("avatar-input"),
  avatarBtn: document.getElementById("avatar-btn"),
  viewerModal: document.getElementById("viewer-modal"),
  viewerTitle: document.getElementById("viewer-title"),
  viewerMeta: document.getElementById("viewer-meta"),
  viewerBody: document.getElementById("viewer-body"),
  viewerClose: document.getElementById("viewer-close"),
  viewerDownload: document.getElementById("viewer-download"),
};

function switchAuthTab(mode) {
  const loginActive = mode === "login";
  elements.loginTab?.classList.toggle("active", loginActive);
  elements.registerTab?.classList.toggle("active", !loginActive);
  elements.loginForm?.classList.toggle("active", loginActive);
  elements.registerForm?.classList.toggle("active", !loginActive);
  if (elements.authError) elements.authError.textContent = "";
}

function applyTheme(theme) {
  state.theme = theme === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", state.theme);
  localStorage.setItem("prism-theme", state.theme);
}

function toggleTheme() {
  applyTheme(state.theme === "dark" ? "light" : "dark");
}

function toggleSidebar(forceOpen) {
  if (!elements.sidebar || !elements.sidebarToggle) return;
  const shouldOpen = forceOpen ?? !elements.sidebar.classList.contains("open");
  elements.sidebar.classList.toggle("open", shouldOpen);
  elements.sidebarToggle.setAttribute("aria-expanded", String(shouldOpen));
}

async function api(path, options = {}) {
  const hasFormData = options.body instanceof FormData;
  const response = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
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
  if (elements.statusLine) {
    elements.statusLine.textContent = message;
  }
}

function parseServerDate(value) {
  if (!value) return null;
  const normalized = /\dZ$|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatDate(value) {
  const date = parseServerDate(value);
  if (!date) return "";
  return new Intl.DateTimeFormat("uk-UA", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatFullDate(value) {
  const date = parseServerDate(value);
  if (!date) return "";
  return new Intl.DateTimeFormat("uk-UA", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function createElement(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function setAvatar(element, userLike, fallback = "??") {
  if (!element) return;
  element.innerHTML = "";
  const avatarUrl = userLike?.avatar_url;
  if (avatarUrl) {
    const image = document.createElement("img");
    image.src = avatarUrl;
    image.alt = userLike?.display_name || userLike?.username || "avatar";
    image.loading = "lazy";
    element.appendChild(image);
    return;
  }
  element.textContent = userLike?.initials || fallback;
}

function formatFileSize(bytes) {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function inferAttachmentKind(message) {
  const mime = (message.attachment_mime_type || "").toLowerCase();
  const fileName = (message.attachment_name || "").toLowerCase();
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("video/")) return "video";
  if (mime === "application/pdf") return "pdf";
  if (
    mime.startsWith("text/") ||
    mime.includes("json") ||
    [".py", ".js", ".ts", ".tsx", ".jsx", ".css", ".html", ".md", ".json", ".yml", ".yaml", ".txt", ".log"].some((ext) =>
      fileName.endsWith(ext)
    )
  ) {
    return "text";
  }
  return "file";
}

function setAuthenticatedSession(user) {
  state.session = {
    authenticated: true,
    user,
    app_name: "Prism",
  };

  elements.authShell?.classList.add("hidden");
  elements.appShell?.classList.remove("hidden");
  elements.sidebarToggle?.classList.remove("hidden");
  setAvatar(elements.userAvatar, user, "??");
  if (elements.userName) elements.userName.textContent = user.display_name || user.username;
  if (elements.userHandle) elements.userHandle.textContent = `@${user.username}`;
}

function closeViewer() {
  if (!elements.viewerModal) return;
  elements.viewerModal.close();
  if (elements.viewerBody) elements.viewerBody.innerHTML = "";
  if (elements.viewerMeta) elements.viewerMeta.textContent = "";
  if (elements.viewerTitle) elements.viewerTitle.textContent = "File";
}

async function openAttachmentViewer(message) {
  if (!elements.viewerModal || !elements.viewerBody || !message.attachment_url) return;
  const kind = inferAttachmentKind(message);
  const fileName = message.attachment_name || "Attachment";
  const metaParts = [];
  if (message.attachment_mime_type) metaParts.push(message.attachment_mime_type);
  if (message.attachment_size) metaParts.push(formatFileSize(message.attachment_size));

  elements.viewerTitle.textContent = fileName;
  elements.viewerMeta.textContent = metaParts.join(" | ");
  elements.viewerDownload.href = message.attachment_url;
  elements.viewerBody.innerHTML = '<div class="viewer-loading">Loading preview...</div>';
  elements.viewerModal.showModal();

  try {
    if (kind === "file") {
      elements.viewerBody.innerHTML = "";
      elements.viewerBody.appendChild(createElement("div", "viewer-note", "Preview is not available for this file type."));
      return;
    }

    const response = await fetch(message.attachment_url, { credentials: "same-origin", cache: "no-store" });
    if (!response.ok) {
      throw new Error("Could not load attachment.");
    }

    if (kind === "text") {
      const text = await response.text();
      elements.viewerBody.innerHTML = "";
      const pre = createElement("pre", "code-viewer");
      pre.textContent = text;
      elements.viewerBody.appendChild(pre);
      return;
    }

    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    elements.viewerBody.innerHTML = "";

    if (kind === "image") {
      const img = createElement("img", "media-viewer");
      img.src = objectUrl;
      img.alt = fileName;
      elements.viewerBody.appendChild(img);
      return;
    }

    if (kind === "video") {
      const video = createElement("video", "media-viewer");
      video.src = objectUrl;
      video.controls = true;
      video.playsInline = true;
      elements.viewerBody.appendChild(video);
      return;
    }

    if (kind === "pdf") {
      const frame = createElement("iframe", "pdf-viewer");
      frame.src = objectUrl;
      frame.title = fileName;
      elements.viewerBody.appendChild(frame);
    }
  } catch (error) {
    elements.viewerBody.innerHTML = "";
    elements.viewerBody.appendChild(createElement("div", "viewer-note", error.message || "Preview failed."));
  }
}

function renderInlineAttachment(message) {
  const kind = inferAttachmentKind(message);
  const wrap = createElement("div", "attachment-inline");

  if (kind === "image") {
    const image = createElement("img", "inline-image");
    image.src = message.attachment_url;
    image.alt = message.attachment_name || "Image";
    image.loading = "lazy";
    image.addEventListener("click", () => openAttachmentViewer(message));
    wrap.appendChild(image);
    return wrap;
  }

  if (kind === "video") {
    const video = createElement("video", "inline-video");
    video.src = message.attachment_url;
    video.controls = true;
    video.preload = "metadata";
    video.playsInline = true;
    wrap.appendChild(video);
    return wrap;
  }

  if (kind === "text") {
    const button = createElement("button", "inline-code-card");
    button.type = "button";
    button.appendChild(createElement("strong", "", message.attachment_name || "Code preview"));
    button.appendChild(createElement("span", "", "Open full code preview"));
    button.addEventListener("click", () => openAttachmentViewer(message));
    wrap.appendChild(button);
    return wrap;
  }

  if (kind === "pdf") {
    const button = createElement("button", "inline-file-card");
    button.type = "button";
    button.appendChild(createElement("strong", "", message.attachment_name || "PDF document"));
    button.appendChild(createElement("span", "", "Open PDF preview"));
    button.addEventListener("click", () => openAttachmentViewer(message));
    wrap.appendChild(button);
    return wrap;
  }

  const link = createElement("a", "inline-file-card");
  link.href = message.attachment_url;
  link.target = "_blank";
  link.rel = "noreferrer";
  link.appendChild(createElement("strong", "", message.attachment_name || "Attachment"));
  link.appendChild(createElement("span", "", "Open file"));
  wrap.appendChild(link);
  return wrap;
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
    const avatar = createElement("div", "avatar");
    setAvatar(avatar, item.requester, "??");
    user.appendChild(avatar);

    const meta = createElement("div", "user-meta");
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
    const avatar = createElement("div", "avatar");
    setAvatar(avatar, friend, "??");
    user.appendChild(avatar);

    const meta = createElement("div", "user-meta");
    meta.appendChild(createElement("strong", "", friend.display_name || friend.username));
    meta.appendChild(createElement("span", "", `@${friend.username}`));
    if (friend.last_message || friend.last_message_at) {
      const preview = createElement("small", "friend-preview");
      const when = friend.last_message_at ? formatDate(friend.last_message_at) : "";
      preview.textContent = [friend.last_message || "Attachment", when].filter(Boolean).join(" | ");
      meta.appendChild(preview);
    }
    user.appendChild(meta);

    const presence = createElement("div", "presence-wrap");
    presence.appendChild(createElement("span", `presence-dot ${friend.is_online ? "online" : ""}`));
    const statusText = createElement("small", "presence-label", friend.is_online ? "Online" : "Offline");
    presence.appendChild(statusText);

    button.append(user, presence);
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
    const mine = Boolean(state.session && message.sender_username === state.session.user.username);
    const article = createElement("article", `message ${mine ? "mine" : ""}`);
    const bubble = createElement("div", "message-bubble");
    const head = createElement("div", "message-head");
    head.appendChild(createElement("strong", "", message.sender_display_name || message.sender_username || "User"));
    const time = createElement("span", "", formatDate(message.sent_at));
    time.title = formatFullDate(message.sent_at);
    head.appendChild(time);
    bubble.appendChild(head);

    if (message.content) {
      bubble.appendChild(createElement("div", "message-text", message.content));
    }

    if (message.attachment_url) {
      bubble.appendChild(renderInlineAttachment(message));
      const attachment = createElement("button", "attachment-card");
      attachment.type = "button";
      attachment.appendChild(createElement("strong", "", message.attachment_name || "Attachment"));
      const meta = [message.attachment_mime_type || inferAttachmentKind(message), formatFileSize(message.attachment_size)]
        .filter(Boolean)
        .join(" | ");
      attachment.appendChild(createElement("span", "", meta));
      attachment.addEventListener("click", () => openAttachmentViewer(message));
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
    if (elements.chatTitle) elements.chatTitle.textContent = "Select a friend";
    if (elements.chatSubtitle) elements.chatSubtitle.textContent = "Accept a request or choose a friend from the list.";
    if (elements.chatAvatar) {
      elements.chatAvatar.innerHTML = "";
      elements.chatAvatar.textContent = "DM";
    }
    if (elements.chatStatus) elements.chatStatus.textContent = "Offline";
    if (elements.composerInput) elements.composerInput.disabled = true;
    if (elements.sendBtn) elements.sendBtn.disabled = true;
    if (elements.fileBtn) elements.fileBtn.disabled = true;
    return;
  }

  if (elements.chatTitle) elements.chatTitle.textContent = friend.display_name || friend.username;
  if (elements.chatSubtitle) elements.chatSubtitle.textContent = `@${friend.username}`;
  setAvatar(elements.chatAvatar, friend, "??");
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
        if (payload.friend.avatar_url) friend.avatar_url = payload.friend.avatar_url;
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
  renderMessages();
  connectSocket(username);
  if (window.innerWidth < 960) toggleSidebar(false);
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
  const session = await api("/api/session");
  state.session = session;

  if (!session.authenticated) {
    elements.authShell?.classList.remove("hidden");
    elements.appShell?.classList.add("hidden");
    elements.sidebarToggle?.classList.add("hidden");
    disconnectSocket();
    return false;
  }

  setAuthenticatedSession(session.user);
  await Promise.all([loadFriends(), loadRequests()]);
  if (!state.activeFriend && state.friends.length) {
    await selectFriend(state.friends[0].username);
  } else {
    renderMessages();
  }
  return true;
}

async function submitAuth(path, formElement) {
  const payload = Object.fromEntries(new FormData(formElement).entries());
  try {
    const user = await api(path, { method: "POST", body: JSON.stringify(payload) });
    if (elements.authError) elements.authError.textContent = "";
    formElement.reset();
    setAuthenticatedSession(user);
    await Promise.all([loadFriends(), loadRequests()]);
    if (!state.activeFriend && state.friends.length) {
      await selectFriend(state.friends[0].username);
    } else {
      renderMessages();
      updateActiveFriendMeta();
    }
    setStatus("Signed in");
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
    setStatus(`Friend request sent to @${username}.`);
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
  setStatus(`Uploading ${file.name}...`);

  try {
    await api(`/api/direct/${state.activeFriend}/files`, { method: "POST", body });
    setStatus(`File "${file.name}" sent.`);
  } catch (error) {
    setStatus(error.message);
  } finally {
    event.target.value = "";
  }
}

async function handleAvatarUpload(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  const body = new FormData();
  body.append("file", file);

  try {
    const user = await api("/api/users/me/avatar", { method: "POST", body });
    if (state.session?.user) {
      state.session.user = user;
    }
    setAuthenticatedSession(user);
    setStatus("Avatar updated");
  } catch (error) {
    setStatus(error.message);
  } finally {
    event.target.value = "";
  }
}

function autoresizeComposer() {
  if (!elements.composerInput) return;
  elements.composerInput.style.height = "auto";
  elements.composerInput.style.height = `${Math.min(elements.composerInput.scrollHeight, 120)}px`;
}

async function logout() {
  await api("/api/auth/logout", { method: "POST" });
  state.session = null;
  state.friends = [];
  state.requests = [];
  state.activeFriend = null;
  state.messagesByFriend.clear();
  disconnectSocket();
  closeViewer();
  elements.authShell?.classList.remove("hidden");
  elements.appShell?.classList.add("hidden");
  elements.sidebarToggle?.classList.add("hidden");
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
      elements.composerForm?.requestSubmit();
    }
  });
  elements.fileBtn?.addEventListener("click", () => elements.fileInput?.click());
  elements.fileInput?.addEventListener("change", handleFileUpload);
  elements.avatarBtn?.addEventListener("click", () => elements.avatarInput?.click());
  elements.userAvatar?.addEventListener("click", () => elements.avatarInput?.click());
  elements.avatarInput?.addEventListener("change", handleAvatarUpload);
  elements.sidebarToggle?.addEventListener("click", () => toggleSidebar());
  elements.sidebarClose?.addEventListener("click", () => toggleSidebar(false));
  elements.themeBtn?.addEventListener("click", toggleTheme);
  elements.themeBtnApp?.addEventListener("click", toggleTheme);
  elements.viewerClose?.addEventListener("click", closeViewer);
  elements.viewerModal?.addEventListener("click", (event) => {
    if (event.target === elements.viewerModal) closeViewer();
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && elements.viewerModal?.open) {
      closeViewer();
    }
  });
}

async function init() {
  applyTheme(state.theme);
  bindEvents();
  switchAuthTab("login");
  renderFriends();
  renderRequests();
  renderMessages();
  updateActiveFriendMeta();
  setStatus("Ready for login");

  try {
    await refreshSession();
  } catch {
    setStatus("Ready for login");
  }
}

init();
