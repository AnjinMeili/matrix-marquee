(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const state = { playlists: [], media: [], panels: [], selectedPlaylist: null };

  async function request(path, options = {}) {
    const response = await fetch(path, options);
    if (!response.ok) {
      let detail = response.statusText;
      try { detail = (await response.json()).detail || detail; } catch (_) {}
      throw new Error(detail);
    }
    return response.status === 204 ? null : response.json();
  }

  function notice(message, error = false) {
    const node = $("#notice");
    $("#notice-text").textContent = message;
    node.hidden = false;
    node.classList.toggle("error", error);
  }

  function renderPlaylists() {
    const list = $("#playlist-list");
    const dashboard = $("#dashboard-playlists");
    list.innerHTML = "";
    dashboard.innerHTML = "";
    if (!state.playlists.length) {
      list.innerHTML = '<p class="empty">No playlists yet.</p>';
      dashboard.innerHTML = '<p class="empty">Create a playlist to begin.</p>';
      return;
    }
    state.playlists.forEach((playlist) => {
      const button = document.createElement("button");
      button.className = "selection-item";
      button.textContent = playlist.name;
      button.onclick = () => selectPlaylist(playlist.id);
      list.appendChild(button);
      const action = document.createElement("div");
      action.className = "compact-item";
      action.innerHTML = `<strong></strong><span>${playlist.items.length} items</span><button class="button">Play</button>`;
      $("strong", action).textContent = playlist.name;
      $("button", action).onclick = () => play(playlist.id);
      dashboard.appendChild(action);
    });
  }

  function renderMedia() {
    const grid = $("#media-grid");
    $("#media-count").textContent = `${state.media.length} item${state.media.length === 1 ? "" : "s"}`;
    grid.innerHTML = state.media.length ? "" : '<p class="empty">No media uploaded.</p>';
    state.media.forEach((item) => {
      const card = document.createElement("article");
      card.className = "media-card";
      card.innerHTML = `<img alt="" src="/api/media/${item.id}/thumbnail"><div class="media-card-body"><strong></strong><small>${item.media_type}</small><button class="text-button danger">Delete</button></div>`;
      $("strong", card).textContent = item.display_name;
      $("button", card).onclick = () => removeMedia(item.id);
      grid.appendChild(card);
    });
  }

  function renderPanels() {
    const list = $("#text-list");
    list.innerHTML = state.panels.length ? "" : '<p class="empty">No text panels yet.</p>';
    state.panels.forEach((panel) => {
      const button = document.createElement("button");
      button.className = "selection-item";
      button.textContent = panel.name;
      button.onclick = () => showPanel(panel);
      list.appendChild(button);
    });
  }

  function showPanel(panel) {
    $("#text-editor").innerHTML = `<div class="card-heading"><div><p class="eyebrow">Text panel</p><h2>${panel.name}</h2></div></div><p class="panel-copy"></p><button class="button danger" data-delete-panel>Delete panel</button>`;
    $(".panel-copy", $("#text-editor")).textContent = panel.content;
    $("[data-delete-panel]", $("#text-editor")).onclick = () => removePanel(panel.id);
  }

  async function load() {
    try {
      const [status, playlists, media, panels] = await Promise.all([
        request("/api/status"), request("/api/playlists"), request("/api/media"), request("/api/text-panels")
      ]);
      state.playlists = playlists; state.media = media; state.panels = panels;
      $("#connection-label").textContent = "Player online";
      $("#connection-detail").textContent = `${status.matrix?.width || 64} × ${status.matrix?.height || 96}`;
      $("#health-matrix").textContent = `${status.matrix?.width || 64} × ${status.matrix?.height || 96}`;
      renderPlaylists(); renderMedia(); renderPanels();
    } catch (error) {
      $("#connection-label").textContent = "Service unavailable";
      $("#connection-detail").textContent = "Check local connection";
      notice(error.message, true);
    }
  }

  async function play(id) {
    try { await request("/api/playback/start", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({playlist_id: id}) }); notice("Playback started."); }
    catch (error) { notice(error.message, true); }
  }

  async function removeMedia(id) {
    if (!window.confirm("Delete this media?")) return;
    try { await request(`/api/media/${id}`, { method: "DELETE" }); await load(); notice("Media deleted."); }
    catch (error) { notice(error.message, true); }
  }

  async function removePanel(id) {
    if (!window.confirm("Delete this text panel?")) return;
    try { await request(`/api/text-panels/${id}`, { method: "DELETE" }); await load(); notice("Text panel deleted."); }
    catch (error) { notice(error.message, true); }
  }

  function selectView(name) {
    document.querySelectorAll(".view").forEach((view) => { view.hidden = view.id !== name; });
    document.querySelectorAll("[data-view]").forEach((link) => link.classList.toggle("active", link.dataset.view === name));
  }

  document.addEventListener("click", (event) => {
    const view = event.target.closest("[data-view]");
    if (view) { event.preventDefault(); location.hash = view.dataset.view; selectView(view.dataset.view); }
    const action = event.target.closest("[data-action]");
    if (action && action.dataset.action === "dismiss-notice") $("#notice").hidden = true;
    if (action && action.dataset.action === "refresh-status") load();
  });
  window.addEventListener("hashchange", () => selectView(location.hash.slice(1) || "dashboard"));
  selectView(location.hash.slice(1) || "dashboard");
  load();
})();
