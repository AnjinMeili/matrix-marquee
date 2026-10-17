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

  function formatBytes(value) {
    if (!Number.isFinite(value)) return "—";
    const units = ["B", "KB", "MB", "GB"];
    let amount = value;
    let unit = 0;
    while (amount >= 1024 && unit < units.length - 1) {
      amount /= 1024;
      unit += 1;
    }
    return `${amount.toFixed(unit ? 1 : 0)} ${units[unit]}`;
  }

  function renderStatus(status, network) {
    const playback = status.playback || {};
    const active = state.playlists.find((playlist) => playlist.id === playback.playlist_id);
    const playing = playback.status === "playing" || playback.status === "paused";
    const itemIndex = active?.items.findIndex((item) => item.id === playback.item_id) ?? -1;
    const title = active?.name || "Nothing playing";

    $("#dashboard-summary").textContent = playing ? `${playback.status}: ${title}` : "Player is stopped.";
    $("#now-title").textContent = title;
    $("#playback-pill").textContent = playback.status || "stopped";
    $("#now-detail").textContent = playback.error || (playing ? "Playlist output is active" : "Select a playlist to begin");
    $("#now-progress").textContent = itemIndex >= 0 ? `${itemIndex + 1} / ${active.items.length}` : "—";
    $("#live-preview").innerHTML = `<div><strong></strong><span></span></div>`;
    $("strong", $("#live-preview")).textContent = playing ? title : "MATRIX";
    $("span", $("#live-preview")).textContent = playing ? `${status.matrix.width} × ${status.matrix.height}` : "Ready for playback";

    $("#health-matrix").textContent = status.matrix.connected
      ? `${status.matrix.width} × ${status.matrix.height} connected`
      : `${status.matrix.width} × ${status.matrix.height} unavailable`;
    $("#health-temp").textContent = status.temperature_c == null ? "—" : `${status.temperature_c.toFixed(1)} °C`;
    $("#health-storage").textContent = `${formatBytes(status.storage.used_bytes)} / ${formatBytes(status.storage.total_bytes)}`;
    $("#health-network").textContent = network.access_point.active
      ? `${network.access_point.ssid || "AP"} active`
      : "Inactive";
    $("#health-upstream").textContent = network.upstream.available
      ? `${network.upstream.interface || "Network"} available`
      : "Offline";
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
      const preview = item.thumbnail_path
        ? `<img alt="" src="/api/media/${item.id}/thumbnail">`
        : '<span aria-hidden="true">▶</span>';
      card.innerHTML = `<div class="media-thumb">${preview}<span class="media-type">${item.media_type}</span></div><div class="media-body"><h3></h3><p class="media-meta"></p><button class="text-button danger">Delete</button></div>`;
      $("h3", card).textContent = item.display_name;
      $(".media-meta", card).textContent = formatBytes(item.file_size_bytes);
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
      const [status, network, playlists, media, panels] = await Promise.all([
        request("/api/status"), request("/api/network"), request("/api/playlists"),
        request("/api/media"), request("/api/text-panels")
      ]);
      state.playlists = playlists; state.media = media; state.panels = panels;
      $("#connection-label").textContent = "Player online";
      $("#connection-detail").textContent = `${status.matrix?.width || 64} × ${status.matrix?.height || 96}`;
      renderPlaylists(); renderMedia(); renderPanels(); renderStatus(status, network);
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
    const playback = event.target.closest("[data-playback]");
    if (playback) {
      request(`/api/playback/${playback.dataset.playback}`, { method: "POST" })
        .then(() => load())
        .catch((error) => notice(error.message, true));
    }
  });
  window.addEventListener("hashchange", () => selectView(location.hash.slice(1) || "dashboard"));
  selectView(location.hash.slice(1) || "dashboard");
  load();
})();
