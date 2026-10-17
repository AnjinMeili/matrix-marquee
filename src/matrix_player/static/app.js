(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const state = { playlists: [], media: [], panels: [], selectedPlaylist: null, addTarget: null };

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
    const sending = playback.source_type === "media" || playback.source_type === "text_panel";
    const itemIndex = active?.items.findIndex((item) => item.id === playback.item_id) ?? -1;
    const title = sending ? playback.source_label : active?.name || "Nothing playing";

    const banner = $("#send-banner");
    banner.hidden = !(sending && playing);
    if (sending && playing) {
      const resumeTarget = state.playlists.find((item) => item.id === playback.resume_playlist_id);
      $("#send-label").textContent = playback.source_label || "item";
      $("#send-resume").textContent = resumeTarget
        ? ` Finishing returns to “${resumeTarget.name}”.`
        : " Finishing blanks the panel.";
    }

    $("#dashboard-summary").textContent = playing
      ? (sending ? `showing: ${title}` : `${playback.status}: ${title}`)
      : "Player is stopped.";
    $("#now-title").textContent = title || "Nothing playing";
    $("#playback-pill").textContent = sending && playing ? "showing" : playback.status || "stopped";
    $("#now-detail").textContent = playback.error
      || (sending ? "Sent directly to the panel" : playing ? "Playlist output is active" : "Select a playlist to begin");
    $("#now-progress").textContent = sending ? "single item" : itemIndex >= 0 ? `${itemIndex + 1} / ${active.items.length}` : "—";
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

  function renderSettings(status, network, diagnostics) {
    const metrics = (node, rows) => {
      node.innerHTML = "";
      rows.forEach(([term, value]) => {
        const row = document.createElement("div");
        row.innerHTML = "<dt></dt><dd></dd>";
        $("dt", row).textContent = term;
        $("dd", row).textContent = value;
        node.appendChild(row);
      });
    };
    $("#settings-network-dot").className = `status-dot ${network.upstream.available ? "online" : "error"}`;
    metrics($("#network-details"), [
      ["Hostname", network.hostname || "—"],
      ["Local address", network.local_address || "—"],
      ["Access point", network.access_point.active ? `${network.access_point.ssid} active` : "Inactive"],
      ["Upstream", network.upstream.available ? `${network.upstream.interface} available` : "Offline"]
    ]);
    metrics($("#matrix-details"), [
      ["Resolution", `${status.matrix.width} × ${status.matrix.height}`],
      ["Mode", status.matrix.mode],
      ["Connected", status.matrix.connected ? "Yes" : "No"],
      ["Temperature", status.temperature_c == null ? "—" : `${status.temperature_c.toFixed(1)} °C`]
    ]);
    const records = diagnostics.database?.records || {};
    $("#diagnostic-summary").textContent = [
      `Checked ${new Date(diagnostics.timestamp).toLocaleString()}`,
      `Media ${records.media ?? 0} · Text panels ${records.text_panels ?? 0} · Playlists ${records.playlists ?? 0} · Items ${records.playlist_items ?? 0}`,
      `Data writable: ${diagnostics.directories?.data_writable ? "yes" : "no"} · Media writable: ${diagnostics.directories?.media_writable ? "yes" : "no"}`,
      `Storage ${formatBytes(status.storage.used_bytes)} of ${formatBytes(status.storage.total_bytes)} used`,
      diagnostics.process?.load_average
        ? `Load average ${diagnostics.process.load_average.map((value) => value.toFixed(2)).join(" ")}`
        : "Load average unavailable"
    ].join("\n");
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
      action.innerHTML = `<strong></strong><span>${playlist.items.length} items</span><button class="button">Send to panel</button>`;
      $("strong", action).textContent = playlist.name;
      $("button", action).onclick = () => sendToPanel("playlist", playlist.id, playlist.name);
      dashboard.appendChild(action);
    });
    if (state.selectedPlaylist !== null) selectPlaylist(state.selectedPlaylist);
  }

  function selectPlaylist(id) {
    const playlist = state.playlists.find((entry) => entry.id === id);
    if (!playlist) return;
    state.selectedPlaylist = id;
    const editor = $("#playlist-editor");
    editor.innerHTML = `<div class="card-heading"><div><p class="eyebrow">Playlist</p><h2></h2></div><span class="pill"></span></div><p class="media-meta"></p><div class="card-actions"><button class="button primary" data-send-playlist>Send to panel</button><button class="button" data-action="add-playlist-item">＋ Add item</button><button class="button" data-action="edit-playlist">Edit</button><button class="text-button danger" data-delete-playlist>Delete playlist</button></div><div class="selection-list" id="playlist-items"></div>`;
    $("h2", editor).textContent = playlist.name;
    $(".pill", editor).textContent = `${playlist.items.length} items${playlist.is_default ? " · default" : ""}`;
    $(".media-meta", editor).textContent = playlist.description || "No description.";
    $("[data-send-playlist]", editor).onclick = () => sendToPanel("playlist", playlist.id, playlist.name);
    $("[data-delete-playlist]", editor).onclick = () => removePlaylist(playlist);
    const items = $("#playlist-items", editor);
    items.innerHTML = playlist.items.length ? "" : '<p class="empty">This playlist has no items.</p>';
    playlist.items.forEach((item, index) => {
      const media = state.media.find((entry) => entry.id === item.media_id);
      const panel = state.panels.find((entry) => entry.id === item.text_panel_id);
      const row = document.createElement("div");
      row.className = "compact-item";
      row.innerHTML = `<strong></strong><span>${item.fit_mode} · ${Math.round(item.duration_ms / 1000)}s</span><div class="card-actions"><button class="icon-button" data-move="up" aria-label="Move up"${index === 0 ? " disabled" : ""}>↑</button><button class="icon-button" data-move="down" aria-label="Move down"${index === playlist.items.length - 1 ? " disabled" : ""}>↓</button><button class="button" data-send-item>Send to panel</button><button class="text-button danger" data-remove-item>Remove</button></div>`;
      $("strong", row).textContent = media?.display_name || panel?.name || `Item ${index + 1}`;
      const target = media ? ["media", media.id, media.display_name] : panel ? ["text_panel", panel.id, panel.name] : null;
      const button = $("[data-send-item]", row);
      if (target) button.onclick = () => sendToPanel(target[0], target[1], target[2], item.fit_mode);
      else button.disabled = true;
      $("[data-move=up]", row).onclick = () => moveItem(playlist, item, index - 1);
      $("[data-move=down]", row).onclick = () => moveItem(playlist, item, index + 1);
      $("[data-remove-item]", row).onclick = () => removeItem(playlist, item);
      items.appendChild(row);
    });
  }

  async function moveItem(playlist, item, position) {
    if (position < 0 || position >= playlist.items.length) return;
    try {
      await request(`/api/playlists/${playlist.id}/items/${item.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          position,
          item_type: item.item_type,
          media_id: item.media_id,
          text_panel_id: item.text_panel_id,
          duration_ms: item.duration_ms,
          repeat_count: item.repeat_count,
          transition_type: item.transition_type,
          transition_duration_ms: item.transition_duration_ms,
          fit_mode: item.fit_mode
        })
      });
      await load();
    } catch (error) {
      notice(error.message, true);
    }
  }

  async function removeItem(playlist, item) {
    if (!window.confirm("Remove this item from the playlist?")) return;
    try {
      await request(`/api/playlists/${playlist.id}/items/${item.id}`, { method: "DELETE" });
      await load();
      notice("Item removed.");
    } catch (error) {
      notice(error.message, true);
    }
  }

  async function removePlaylist(playlist) {
    if (!window.confirm(`Delete the playlist “${playlist.name}”?`)) return;
    try {
      await request(`/api/playlists/${playlist.id}`, { method: "DELETE" });
      state.selectedPlaylist = null;
      $("#playlist-editor").innerHTML = '<div class="empty-state"><span class="empty-icon">☷</span><h2>Select a playlist</h2><p>Choose one from the list or create a new playlist.</p></div>';
      await load();
      notice("Playlist deleted.");
    } catch (error) {
      notice(error.message, true);
    }
  }

  function openAddToPlaylist(targetType, targetId, label) {
    if (!state.playlists.length) return notice("Create a playlist first", true);
    state.addTarget = { targetType, targetId, label };
    const select = $("#add-to-playlist-select");
    select.innerHTML = "";
    state.playlists.forEach((playlist) => {
      const option = document.createElement("option");
      option.value = playlist.id;
      option.textContent = `${playlist.name} (${playlist.items.length})`;
      select.appendChild(option);
    });
    if (state.selectedPlaylist !== null) select.value = String(state.selectedPlaylist);
    $("#add-to-playlist-target").textContent = `Adding “${label}”`;
    $("#add-to-playlist-dialog").showModal();
  }

  async function sendToPanel(targetType, targetId, label, fitMode = "stretch") {
    try {
      await request("/api/playback/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_type: targetType, target_id: targetId, fit_mode: fitMode })
      });
      await load();
      notice(targetType === "playlist" ? `Playing “${label}”.` : `“${label}” sent to the panel.`);
      location.hash = "dashboard";
      selectView("dashboard");
    } catch (error) {
      notice(error.message, true);
    }
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
      card.innerHTML = `<div class="media-thumb">${preview}<span class="media-type">${item.media_type}</span></div><div class="media-body"><h3></h3><p class="media-meta"></p><div class="media-actions"><button class="button" data-send>Send to panel</button><button class="button" data-add>＋ Playlist</button><button class="text-button danger" data-remove>Delete</button></div></div>`;
      $("h3", card).textContent = item.display_name;
      $(".media-meta", card).textContent = formatBytes(item.file_size_bytes);
      $("[data-send]", card).onclick = () => sendToPanel("media", item.id, item.display_name);
      $("[data-add]", card).onclick = () => openAddToPlaylist("media", item.id, item.display_name);
      $("[data-remove]", card).onclick = () => removeMedia(item.id);
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
    $("#text-editor").innerHTML = `<div class="card-heading"><div><p class="eyebrow">Text panel</p><h2></h2></div></div><p class="panel-copy"></p><div class="card-actions"><button class="button primary" data-send-panel>Send to panel</button><button class="button" data-add-panel>＋ Playlist</button><button class="button danger" data-delete-panel>Delete panel</button></div>`;
    const editor = $("#text-editor");
    $("h2", editor).textContent = panel.name;
    $(".panel-copy", editor).textContent = panel.content;
    $("[data-send-panel]", editor).onclick = () => sendToPanel("text_panel", panel.id, panel.name);
    $("[data-add-panel]", editor).onclick = () => openAddToPlaylist("text_panel", panel.id, panel.name);
    $("[data-delete-panel]", editor).onclick = () => removePanel(panel.id);
  }

  async function load() {
    try {
      const [status, network, playlists, media, panels, diagnostics] = await Promise.all([
        request("/api/status"), request("/api/network"), request("/api/playlists"),
        request("/api/media"), request("/api/text-panels"), request("/api/diagnostics")
      ]);
      state.playlists = playlists; state.media = media; state.panels = panels;
      $("#connection-label").textContent = "Player online";
      $("#connection-detail").textContent = `${status.matrix?.width || 64} × ${status.matrix?.height || 96}`;
      renderPlaylists(); renderMedia(); renderPanels(); renderStatus(status, network);
      renderSettings(status, network, diagnostics);
    } catch (error) {
      $("#connection-label").textContent = "Service unavailable";
      $("#connection-detail").textContent = "Check local connection";
      notice(error.message, true);
    }
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

  function openDialog(id) {
    const dialog = $(`#${id}`);
    const form = $("form", dialog);
    if (form && form.method !== "dialog") form.reset();
    if (id === "upload-dialog") $("#upload-files").textContent = "No files selected";
    if (id === "add-item-dialog") populateItemOptions();
    dialog.showModal();
    return dialog;
  }

  function populateItemOptions() {
    const type = $("#item-type").value;
    const options = type === "media"
      ? state.media.map((item) => [item.id, item.display_name])
      : state.panels.map((panel) => [panel.id, panel.name]);
    const select = $("#item-content");
    select.innerHTML = "";
    options.forEach(([id, label]) => {
      const option = document.createElement("option");
      option.value = id;
      option.textContent = label;
      select.appendChild(option);
    });
    if (!options.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = type === "media" ? "No media uploaded" : "No text panels";
      select.appendChild(option);
    }
  }

  async function submitUpload(form) {
    const files = $("#media-files").files;
    if (!files.length) throw new Error("Choose at least one file");
    const results = [];
    for (const file of files) {
      const data = new FormData();
      data.append("file", file);
      try {
        await request("/api/media", { method: "POST", body: data });
        results.push(`${file.name} uploaded`);
      } catch (error) {
        results.push(`${file.name}: ${error.message}`);
      }
    }
    return results.join("; ");
  }

  async function submitTextPanel(form) {
    const data = new FormData(form);
    const panel = await request("/api/text-panels", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: data.get("name"),
        content: data.get("content"),
        font_size: Number(data.get("font_size")),
        alignment: data.get("alignment"),
        color_value: data.get("color_value"),
        border_style: data.get("border_style")
      })
    });
    return `Text panel “${panel.name}” created.`;
  }

  async function submitPlaylist(form) {
    const data = new FormData(form);
    const editId = form.dataset.editId ? Number(form.dataset.editId) : null;
    const payload = {
      name: data.get("name"),
      description: data.get("description") || "",
      is_enabled: data.get("is_enabled") === "on",
      is_default: data.get("is_default") === "on"
    };
    if (editId) {
      const playlist = await request(`/api/playlists/${editId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      state.selectedPlaylist = playlist.id;
      return `Playlist “${playlist.name}” updated.`;
    }
    const playlist = await request("/api/playlists", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    state.selectedPlaylist = playlist.id;
    return `Playlist “${playlist.name}” created.`;
  }

  async function submitAddToPlaylist(form) {
    const target = state.addTarget;
    if (!target) throw new Error("Nothing selected to add");
    const data = new FormData(form);
    const playlistId = Number(data.get("playlist_id"));
    const playlist = state.playlists.find((entry) => entry.id === playlistId);
    await request(`/api/playlists/${playlistId}/items`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        item_type: target.targetType,
        media_id: target.targetType === "media" ? target.targetId : null,
        text_panel_id: target.targetType === "text_panel" ? target.targetId : null,
        duration_ms: Math.round(Number(data.get("duration")) * 1000),
        repeat_count: 1,
        transition_type: data.get("transition"),
        transition_duration_ms: Math.round(Number(data.get("transition_duration")) * 1000),
        fit_mode: data.get("fit_mode")
      })
    });
    state.addTarget = null;
    return `“${target.label}” added to “${playlist ? playlist.name : "playlist"}”.`;
  }

  async function submitPlaylistItem(form) {
    if (state.selectedPlaylist === null) throw new Error("Select a playlist first");
    const data = new FormData(form);
    const contentId = Number(data.get("content_id"));
    if (!contentId) throw new Error("Choose content to add");
    const itemType = data.get("item_type");
    await request(`/api/playlists/${state.selectedPlaylist}/items`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        item_type: itemType,
        media_id: itemType === "media" ? contentId : null,
        text_panel_id: itemType === "text_panel" ? contentId : null,
        duration_ms: Math.round(Number(data.get("duration")) * 1000),
        repeat_count: Number(data.get("repeats")),
        transition_type: data.get("transition"),
        transition_duration_ms: Math.round(Number(data.get("transition_duration")) * 1000),
        fit_mode: data.get("fit_mode")
      })
    });
    return "Item added to playlist.";
  }

  const SUBMITTERS = {
    "upload-form": submitUpload,
    "text-form": submitTextPanel,
    "playlist-form": submitPlaylist,
    "add-item-form": submitPlaylistItem,
    "add-to-playlist-form": submitAddToPlaylist
  };

  document.addEventListener("submit", async (event) => {
    const handler = SUBMITTERS[event.target.id];
    if (!handler) return;
    event.preventDefault();
    const submit = $("[type=submit]", event.target);
    if (submit) submit.disabled = true;
    try {
      const message = await handler(event.target);
      event.target.closest("dialog").close();
      await load();
      notice(message);
    } catch (error) {
      notice(error.message, true);
    } finally {
      if (submit) submit.disabled = false;
    }
  });

  document.addEventListener("change", (event) => {
    if (event.target.id === "item-type") populateItemOptions();
    if (event.target.id === "media-files") {
      const files = [...event.target.files].map((file) => file.name);
      $("#upload-files").textContent = files.length ? files.join(", ") : "No files selected";
    }
  });

  const ACTIONS = {
    "dismiss-notice": () => { $("#notice").hidden = true; },
    "refresh-status": () => load(),
    "refresh-diagnostics": () => load(),
    "open-upload": () => openDialog("upload-dialog"),
    "new-playlist": () => {
      const form = $("#playlist-form");
      delete form.dataset.editId;
      $("#playlist-dialog-title").textContent = "Create playlist";
      $("#playlist-submit").textContent = "Create playlist";
      openDialog("playlist-dialog");
    },
    "edit-playlist": () => {
      const playlist = state.playlists.find((entry) => entry.id === state.selectedPlaylist);
      if (!playlist) return notice("Select a playlist first", true);
      $("#playlist-dialog-title").textContent = "Edit playlist";
      $("#playlist-submit").textContent = "Save changes";
      const dialog = openDialog("playlist-dialog");
      const form = $("#playlist-form", dialog);
      form.dataset.editId = playlist.id;
      form.elements.name.value = playlist.name;
      form.elements.description.value = playlist.description || "";
      form.elements.is_enabled.checked = playlist.is_enabled;
      form.elements.is_default.checked = playlist.is_default;
    },
    "new-text-panel": () => openDialog("text-dialog"),
    "add-playlist-item": () => {
      if (state.selectedPlaylist === null) return notice("Select a playlist first", true);
      openDialog("add-item-dialog");
    },
    "copy-diagnostics": async () => {
      const text = $("#diagnostic-summary").textContent.trim();
      try { await navigator.clipboard.writeText(text); notice("Diagnostics copied."); }
      catch (_) { notice("Copying is unavailable in this browser.", true); }
    }
  };

  document.addEventListener("click", (event) => {
    const view = event.target.closest("[data-view]");
    if (view) { event.preventDefault(); location.hash = view.dataset.view; selectView(view.dataset.view); }
    const action = event.target.closest("[data-action]");
    if (action && ACTIONS[action.dataset.action]) {
      event.preventDefault();
      ACTIONS[action.dataset.action]();
    }
    const close = event.target.closest("[data-close-dialog]");
    if (close) {
      event.preventDefault();
      close.closest("dialog").close();
    }
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
