(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const state = {
    playlists: [], media: [], panels: [], fonts: [], status: null, network: null,
    selectedPlaylist: null, selectedPanel: null, itemContext: null, editingPanel: null,
    tags: [], tagFilter: null, editingMedia: null, draftTags: []
  };

  const TRANSITIONS = [
    ["impact", "Comic slam"], ["morph", "Morph"], ["flash", "Camera flash"], ["fade", "Fade"],
    ["fade_out", "Fade through black"], ["slide_left", "Push left"], ["slide_right", "Push right"],
    ["slide_up", "Push up"], ["slide_down", "Push down"], ["zoom", "Zoom in"],
    ["rotate", "Spin in"], ["pixel_dissolve", "Sparkle"], ["none", "Instant"]
  ];
  const TRANSITION_LABEL = Object.fromEntries(TRANSITIONS);
  const FIT_LABEL = { crop: "fills sign", fit: "whole picture", letterbox: "whole picture", stretch: "stretched" };
  const MODE_LABEL = { loop: "In order", shuffle: "Shuffle", random: "Random", once: "Stops at the end" };
  const TYPE_LABEL = { image: "Picture", gif: "Animated GIF", video: "Video" };

  const DEFAULT_STYLE = {
    font_family: "impact", font_size: 0, bold: false, italic: false, alignment: "center",
    color_mode: "solid", color_value: "#ffdd00", outline: true, outline_color: "#000000",
    border_style: "none", border_color: "#ffffff", effect: "none", accent_color: "#ffd400",
    background_color: "#000000", background_media_id: null, motion_speed: "normal"
  };
  const PRESETS = [
    ["protest", "Protest", { font_family: "impact", color_mode: "solid", color_value: "#ffdd00", outline: true, outline_color: "#000000", effect: "none", border_style: "none", background_color: "#000000" }],
    ["pow", "Comic POW", { font_family: "comic", color_mode: "solid", color_value: "#ff2020", outline: true, outline_color: "#000000", effect: "pow", accent_color: "#ffd400", border_style: "none", background_color: "#000000" }],
    ["rainbow", "Rainbow", { font_family: "block", color_mode: "rainbow", outline: true, outline_color: "#000000", effect: "pulse", border_style: "rainbow", background_color: "#000000" }],
    ["alarm", "Alarm", { font_family: "impact", color_mode: "solid", color_value: "#ffffff", outline: true, outline_color: "#000000", effect: "flash", border_style: "marquee", border_color: "#ffdd00", background_color: "#c00000" }],
    ["ticker", "Ticker", { font_family: "impact", color_mode: "solid", color_value: "#ffffff", outline: false, effect: "scroll", border_style: "none", background_color: "#000000" }],
    ["fire", "Fire", { font_family: "marker", color_mode: "fire", outline: true, outline_color: "#000000", effect: "shake", border_style: "none", background_color: "#000000" }],
    ["retro", "Retro", { font_family: "pixel", color_mode: "solid", color_value: "#00ff66", outline: false, effect: "none", border_style: "double", border_color: "#00ff66", background_color: "#000000" }]
  ];

  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value == null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    children.flat().forEach((child) => { if (child != null) node.append(child); });
    return node;
  }

  async function request(path, options = {}) {
    const response = await fetch(path, options);
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const payload = (await response.json()).detail;
        if (Array.isArray(payload)) detail = payload.map((entry) => entry.msg).join("; ");
        else detail = typeof payload === "object" && payload?.message ? payload.message : payload || detail;
      } catch (_) {}
      throw new Error(detail);
    }
    return response.status === 204 ? null : response.json();
  }

  const send = (path, body, method = "POST") => request(path, {
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
  });

  function notice(message, kind = "ok") {
    const node = $("#notice");
    $("#notice-text").textContent = message;
    node.hidden = false;
    node.classList.toggle("error", kind === "error");
    clearTimeout(notice.timer);
    if (kind !== "error") notice.timer = setTimeout(() => { node.hidden = true; }, 4000);
  }
  const fail = (error) => notice(error.message, "error");

  // In-page confirmation: the iPhone captive-portal window silently refuses
  // window.confirm(), which made every delete a no-op there.
  function confirmAction(message, okLabel = "Delete") {
    const dialog = $("#confirm-dialog");
    $("#confirm-title").textContent = message;
    $("#confirm-ok").textContent = okLabel;
    dialog.returnValue = "cancel";
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "ok"), { once: true });
      dialog.showModal();
    });
  }

  function formatBytes(value) {
    if (!Number.isFinite(value)) return "—";
    const units = ["B", "KB", "MB", "GB"];
    let amount = value;
    let unit = 0;
    while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; }
    return `${amount.toFixed(unit ? 1 : 0)} ${units[unit]}`;
  }

  const seconds = (ms) => {
    const value = ms / 1000;
    return `${Number.isInteger(value) ? value : value.toFixed(1)} s`;
  };

  function ensureOption(select, value, label) {
    if (![...select.options].some((option) => option.value === String(value))) {
      select.append(el("option", { value, text: label }));
    }
    select.value = String(value);
  }

  function fillSelect(select, options, value) {
    select.replaceChildren(...options.map(([optionValue, label]) => el("option", { value: optionValue, text: label })));
    if (value != null) select.value = String(value);
  }

  const panelPreviewUrl = (panel) => `/api/text-panels/${panel.id}/preview?v=${encodeURIComponent(panel.updated_at)}`;

  function thumb(type, id, panel) {
    const box = el("div", { class: "thumb" });
    const img = el("img", { alt: "", loading: "lazy" });
    img.onerror = () => { img.remove(); box.textContent = type === "text_panel" ? "T" : "▶"; };
    img.src = type === "text_panel" ? panelPreviewUrl(panel) : `/api/media/${id}/thumbnail`;
    box.append(img);
    return box;
  }

  // ---------- Sign / dashboard ----------

  function renderStatus(status, network) {
    state.status = status;
    if (network) state.network = network;
    const playback = status.playback || {};
    const playing = playback.status === "playing";
    const paused = playback.status === "paused";
    const active = playing || paused;
    const sending = active && (playback.source_type === "media" || playback.source_type === "text_panel");
    const playlist = state.playlists.find((entry) => entry.id === playback.playlist_id);
    const title = sending ? `“${playback.source_label}”` : playlist?.name || playback.source_label || "Nothing playing";

    $("#now-title").textContent = active ? title : playback.status === "error" ? "Sign has a problem" : "Sign is off";
    const pill = $("#playback-pill");
    pill.textContent = paused ? "Paused" : sending ? "Showing" : playing ? "Playing" : playback.status === "error" ? "Problem" : "Off";
    pill.classList.toggle("playing", playing);
    $("#dashboard-summary").textContent = active
      ? `${paused ? "Paused" : "Showing"}: ${title}`
      : "The sign is off. Pick a playlist or message to show.";
    $("#now-detail").textContent = playback.error
      || (sending ? "Shown on its own" : playlist ? MODE_LABEL[playback.play_mode] || "Playlist" : "Pick a playlist or message to begin");
    $("#now-progress").textContent = !sending && playback.position != null && playback.item_count
      ? `Item ${playback.position + 1} of ${playback.item_count}` : "—";

    const warnings = $("#now-warnings");
    warnings.replaceChildren(...(playback.warnings || []).map((text) => el("p", { text: `⚠ ${text}` })));
    warnings.hidden = !(playback.warnings || []).length;

    $("#send-banner").hidden = !sending;
    if (sending) {
      $("#send-label").textContent = `“${playback.source_label || "item"}”`;
      const resume = state.playlists.find((entry) => entry.id === playback.resume_playlist_id);
      $("#release-button").textContent = resume ? `Back to “${resume.name}”` : "Clear the sign";
    }
    $("#pause-button").hidden = !playing;
    $("#resume-button").hidden = !paused;
    $("#restart-button").hidden = !(active && !sending && playlist);
    $("#stop-button").disabled = !active;

    const matrix = status.matrix;
    $("#health-matrix").textContent = `${matrix.width} × ${matrix.height} ${matrix.connected ? "connected" : "unavailable"}`;
    $("#health-temp").textContent = status.temperature_c == null ? "—" : `${status.temperature_c.toFixed(1)} °C`;
    $("#health-storage").textContent = `${formatBytes(status.storage.free_bytes)} free`;
    const net = state.network;
    if (net) {
      $("#health-network").textContent = net.access_point.active ? `${net.access_point.ssid || "Wi-Fi"} on` : "Off";
      $("#health-upstream").textContent = net.upstream.available ? `${net.upstream.interface || "Connected"}` : "Offline (that's fine)";
    }
    $("#connection-dot").className = `status-dot ${matrix.connected ? "online" : "error"}`;
    $("#connection-label").textContent = "Player online";
    $("#connection-detail").textContent = `${matrix.width} × ${matrix.height} sign`;
    renderDashboardPlaylists();
  }

  function renderDashboardPlaylists() {
    const list = $("#dashboard-playlists");
    if (!state.playlists.length) {
      list.replaceChildren(el("p", { class: "empty", text: "No playlists yet. Make one under Playlists." }));
      return;
    }
    const current = state.status?.playback;
    list.replaceChildren(...state.playlists.map((playlist) => {
      const live = current && current.playlist_id === playlist.id && current.status !== "stopped";
      return el("div", { class: "compact-item" },
        el("div", {}, el("strong", { text: playlist.name }),
          el("p", { text: `${playlist.items.length} items · ${MODE_LABEL[playlist.play_mode] || ""}${playlist.is_default ? " · plays at power-on" : ""}` })),
        live ? el("span", { class: "pill playing", text: "Playing" })
          : el("button", { class: "button primary", text: "▶ Play", disabled: !playlist.items.length || !playlist.is_enabled, onclick: () => startPlaylist(playlist.id) }));
    }));
  }

  async function startPlaylist(id, fromStart = false) {
    try {
      await send("/api/playback/start", { playlist_id: id, from_start: fromStart });
      const playlist = state.playlists.find((entry) => entry.id === id);
      notice(`Playing “${playlist?.name || "playlist"}”.`);
      await refreshStatus();
      showView("dashboard");
    } catch (error) { fail(error); }
  }

  const currentView = () => document.querySelector(".view:not([hidden])")?.id || "dashboard";

  // Remembers where "Show now" was tapped so coming back lands on that element.
  async function showNow(targetType, targetId, label, fitMode = "crop", anchor = null) {
    try {
      const origin = currentView();
      await send("/api/playback/send", { target_type: targetType, target_id: targetId, fit_mode: fitMode });
      state.returnTo = origin !== "dashboard" ? { view: origin, anchor } : null;
      notice(`“${label}” is on the sign.`);
      await refreshStatus();
      showView("dashboard");
    } catch (error) { fail(error); }
  }

  function revealAnchor(anchor) {
    if (!anchor) return;
    requestAnimationFrame(() => {
      const node = document.querySelector(`[data-anchor="${anchor}"]`);
      if (!node) return;
      node.scrollIntoView({ block: "center" });
      node.classList.remove("returned");
      void node.offsetWidth;  // restart the highlight animation
      node.classList.add("returned");
    });
  }

  let liveTimer = null;
  function liveLoop() {
    clearTimeout(liveTimer);
    const img = $("#live-frame");
    const visible = !$("#dashboard").hidden && document.visibilityState === "visible";
    if (!visible || !state.status?.matrix?.connected) {
      liveTimer = setTimeout(liveLoop, 1000);
      return;
    }
    img.onload = () => {
      img.hidden = false;
      $("#live-placeholder").hidden = true;
      liveTimer = setTimeout(liveLoop, 150);
    };
    img.onerror = () => { liveTimer = setTimeout(liveLoop, 2000); };
    img.src = `/api/playback/frame?t=${Date.now()}`;
  }

  // ---------- Messages (text panels) ----------

  function renderPanels() {
    const list = $("#text-list");
    if (!state.panels.length) {
      list.replaceChildren(el("p", { class: "empty", text: "No messages yet." }));
      return;
    }
    list.replaceChildren(...state.panels.map((panel) => el("button", {
      class: `selection-item with-thumb${panel.id === state.selectedPanel ? " active" : ""}`,
      "data-anchor": `panel-${panel.id}`,
      onclick: () => showPanel(panel.id)
    }, thumb("text_panel", panel.id, panel), el("span", { class: "selection-text" }, el("strong", { text: panel.name }), el("span", { text: panel.content })))));
    if (state.selectedPanel !== null) showPanel(state.selectedPanel, false);
  }

  function showPanel(id, scroll = true) {
    const panel = state.panels.find((entry) => entry.id === id);
    const detail = $("#text-detail");
    if (!panel) {
      state.selectedPanel = null;
      detail.replaceChildren(el("div", { class: "empty-state" }, el("span", { class: "empty-icon", text: "T" }), el("h2", { text: "Pick a message" })));
      return;
    }
    state.selectedPanel = id;
    document.querySelectorAll("#text-list .selection-item").forEach((node, index) => node.classList.toggle("active", state.panels[index]?.id === id));
    detail.replaceChildren(
      el("div", { class: "card-heading", "data-anchor": `panel-detail-${panel.id}` }, el("div", {}, el("p", { class: "eyebrow", text: "Message" }), el("h2", { text: panel.name }))),
      el("div", { class: "sign-preview" }, el("img", { src: panelPreviewUrl(panel), alt: `Preview of ${panel.name}` })),
      el("p", { class: "panel-copy", text: panel.content }),
      el("div", { class: "card-actions" },
        el("button", { class: "button primary", text: "Show now", onclick: () => showNow("text_panel", panel.id, panel.name, "crop", `panel-detail-${panel.id}`) }),
        el("button", { class: "button", text: "Edit", onclick: () => openTextEditor(panel) }),
        el("button", { class: "button", text: "＋ Playlist", onclick: () => openItemDialog({ targetType: "text_panel", targetId: panel.id }) }),
        el("button", { class: "button danger", text: "Delete", onclick: () => removePanel(panel) })));
    if (scroll && window.innerWidth < 900) detail.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function presetButtons(container, onPick, selected) {
    container.replaceChildren(...PRESETS.map(([key, label]) => el("button", {
      type: "button", class: `preset${key === selected ? " active" : ""}`, "data-preset": key, text: label,
      onclick: (event) => {
        container.querySelectorAll(".preset").forEach((node) => node.classList.toggle("active", node === event.currentTarget));
        onPick(key);
      }
    })));
  }

  function applyStyle(form, style) {
    const fields = form.elements;
    for (const [key, value] of Object.entries(style)) {
      const field = fields[key];
      if (!field) continue;
      if (field.type === "checkbox") field.checked = Boolean(value);
      else if (key === "font_size") ensureOption(field, value, `${value}px`);
      else if (key === "background_media_id") field.value = value == null ? "" : String(value);
      else field.value = value;
    }
    syncTextFields(form);
  }

  function syncTextFields(form) {
    $("#accent-row").hidden = form.elements.effect.value !== "pow";
    form.elements.color_value.disabled = !["solid", "cycle"].includes(form.elements.color_mode.value);
    form.elements.outline_color.disabled = !form.elements.outline.checked;
    form.elements.border_color.disabled = ["none", "rainbow"].includes(form.elements.border_style.value);
    const moving = form.elements.effect.value !== "none" || form.elements.color_mode.value !== "solid"
      || ["marquee", "rainbow"].includes(form.elements.border_style.value);
    form.elements.motion_speed.disabled = !moving;
  }

  function collectText(form) {
    const fields = form.elements;
    return {
      content: fields.content.value,
      font_family: fields.font_family.value || DEFAULT_STYLE.font_family,
      font_size: Number(fields.font_size.value),
      bold: fields.bold.checked,
      italic: fields.italic.checked,
      outline: fields.outline.checked,
      alignment: fields.alignment.value,
      color_mode: fields.color_mode.value,
      color_value: fields.color_value.value,
      outline_color: fields.outline_color.value,
      accent_color: fields.accent_color.value,
      border_style: fields.border_style.value,
      border_color: fields.border_color.value,
      effect: fields.effect.value,
      background_color: fields.background_color.value,
      background_media_id: fields.background_media_id.value ? Number(fields.background_media_id.value) : null,
      motion_speed: fields.motion_speed.value
    };
  }

  let previewTimer = null;
  let previewSequence = 0;
  let previewUrl = null;
  function schedulePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(renderPreview, 300);
  }

  async function renderPreview() {
    const sequence = ++previewSequence;
    const form = $("#text-form");
    const body = collectText(form);
    if (!body.content.trim()) body.content = "Type a message";
    $("#text-preview-status").textContent = "Drawing preview…";
    try {
      const response = await fetch("/api/text-panels/preview", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
      });
      if (!response.ok) {
        let detail = "Preview unavailable";
        try { const payload = (await response.json()).detail; if (Array.isArray(payload)) detail = `Preview unavailable: ${payload.map((entry) => entry.msg).join("; ")}`; } catch (_) {}
        throw new Error(detail);
      }
      const blob = await response.blob();
      if (sequence !== previewSequence) return;
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(blob);
      $("#text-preview").src = previewUrl;
      $("#text-preview-status").textContent = "This is how it will look on the sign.";
    } catch (error) {
      if (sequence === previewSequence) $("#text-preview-status").textContent = error.message;
    }
  }

  function openTextEditor(panel = null) {
    const dialog = $("#text-dialog");
    const form = $("#text-form");
    form.reset();
    state.editingPanel = panel ? panel.id : null;
    $("#text-dialog-title").textContent = panel ? "Edit message" : "New message";
    $("#text-submit").textContent = panel ? "Save changes" : "Save";
    fillSelect($("#text-font"), state.fonts.map((font) => [font.id, font.label]));
    const backgrounds = state.media.filter((item) => item.media_type !== "video");
    fillSelect($("#text-background"), [["", "None"], ...backgrounds.map((item) => [item.id, item.display_name])]);
    const style = panel ? { ...DEFAULT_STYLE, ...panel } : { ...DEFAULT_STYLE };
    if (style.font_family === "default") style.font_family = "sans";
    applyStyle(form, style);
    form.elements.content.value = panel ? panel.content : "";
    form.elements.name.value = panel ? panel.name : "";
    presetButtons($("#text-presets"), (key) => {
      applyStyle(form, PRESETS.find(([presetKey]) => presetKey === key)[2]);
      schedulePreview();
    }, null);
    dialog.showModal();
    renderPreview();
  }

  async function saveText(form) {
    const body = collectText(form);
    if (!body.content.trim()) throw new Error("Type a message first");
    body.name = form.elements.name.value.trim() || body.content.trim().replace(/\s+/g, " ").slice(0, 60);
    const panel = state.editingPanel
      ? await send(`/api/text-panels/${state.editingPanel}`, body, "PUT")
      : await send("/api/text-panels", body);
    state.selectedPanel = panel.id;
    return panel;
  }

  async function removePanel(panel) {
    if (!(await confirmAction(`Delete the message “${panel.name}”?`))) return;
    try {
      await request(`/api/text-panels/${panel.id}`, { method: "DELETE" });
      state.selectedPanel = null;
      await load();
      notice("Message deleted.");
    } catch (error) { fail(error); }
  }

  // ---------- Playlists ----------

  function renderPlaylists() {
    const list = $("#playlist-list");
    if (!state.playlists.length) {
      list.replaceChildren(el("p", { class: "empty", text: "No playlists yet." }));
    } else {
      list.replaceChildren(...state.playlists.map((playlist) => el("button", {
        class: `selection-item${playlist.id === state.selectedPlaylist ? " active" : ""}`,
        onclick: () => selectPlaylist(playlist.id)
      }, el("strong", { text: playlist.name }), el("span", { text: `${playlist.items.length} items · ${MODE_LABEL[playlist.play_mode] || ""}` }))));
    }
    if (state.selectedPlaylist !== null) selectPlaylist(state.selectedPlaylist, false);
  }

  function describeTarget(item) {
    if (item.item_type === "text_panel") {
      const panel = state.panels.find((entry) => entry.id === item.text_panel_id);
      return { kind: "text_panel", id: item.text_panel_id, name: panel?.name || "Message", panel, animated: false, typeLabel: "Message" };
    }
    const media = state.media.find((entry) => entry.id === item.media_id);
    return { kind: "media", id: item.media_id, name: media?.display_name || "Picture", media, animated: media && media.media_type !== "image", typeLabel: TYPE_LABEL[media?.media_type] || "Media" };
  }

  function itemSummary(item, target) {
    const timing = item.play_by === "repeats" && target.animated
      ? `plays ${item.repeat_count}×`
      : seconds(item.duration_ms);
    const parts = [target.typeLabel, timing, TRANSITION_LABEL[item.transition_type] || item.transition_type];
    if (target.kind === "media" && target.media?.media_type !== undefined) parts.push(FIT_LABEL[item.fit_mode] || item.fit_mode);
    return parts.join(" · ");
  }

  function selectPlaylist(id, scroll = true) {
    const playlist = state.playlists.find((entry) => entry.id === id);
    const editor = $("#playlist-editor");
    if (!playlist) {
      state.selectedPlaylist = null;
      editor.replaceChildren(el("div", { class: "empty-state" }, el("span", { class: "empty-icon", text: "☷" }), el("h2", { text: "Pick a playlist" })));
      return;
    }
    state.selectedPlaylist = id;
    document.querySelectorAll("#playlist-list .selection-item").forEach((node, index) => node.classList.toggle("active", state.playlists[index]?.id === id));
    const items = playlist.items.map((item, index) => {
      const target = describeTarget(item);
      return el("div", { class: "playlist-row", "data-anchor": `item-${item.id}` },
        thumb(target.kind, target.id, target.panel),
        el("div", { class: "item-main" },
          el("strong", { text: `${index + 1}. ${target.name}` }),
          el("p", { text: itemSummary(item, target) })),
        el("div", { class: "item-controls" },
          el("button", { class: "icon-button", "aria-label": "Move up", text: "↑", disabled: index === 0, onclick: () => moveItem(playlist, item, index - 1) }),
          el("button", { class: "icon-button", "aria-label": "Move down", text: "↓", disabled: index === playlist.items.length - 1, onclick: () => moveItem(playlist, item, index + 1) }),
          el("button", { class: "icon-button", "aria-label": "Change", text: "✎", onclick: () => openItemDialog({ playlistId: playlist.id, item }) }),
          el("button", { class: "icon-button", "aria-label": "Show now", text: "▶", onclick: () => showNow(target.kind, target.id, target.name, item.fit_mode, `item-${item.id}`) }),
          el("button", { class: "icon-button danger", "aria-label": "Remove", text: "✕", onclick: () => removeItem(playlist, item) })));
    });
    editor.replaceChildren(
      el("div", { class: "card-heading" },
        el("div", {}, el("p", { class: "eyebrow", text: "Playlist" }), el("h2", { text: playlist.name })),
        el("span", { class: "pill", text: `${MODE_LABEL[playlist.play_mode] || ""}${playlist.is_default ? " · power-on" : ""}` })),
      el("p", { class: "media-meta", text: playlist.description || (playlist.items.length ? `Picks up where it left off (item ${Math.min(playlist.resume_position, playlist.items.length - 1) + 1}).` : "Add messages, pictures, or videos.") }),
      el("div", { class: "card-actions" },
        el("button", { class: "button primary", text: "▶ Play", disabled: !playlist.items.length, onclick: () => startPlaylist(playlist.id) }),
        el("button", { class: "button", text: "⟲ From the top", disabled: !playlist.items.length, onclick: () => startPlaylist(playlist.id, true) }),
        el("button", { class: "button", text: "👁 Preview", disabled: !playlist.items.length, onclick: () => openRunThrough(playlist.id, 0) }),
        el("button", { class: "button", text: "＋ Add", onclick: () => openItemDialog({ playlistId: playlist.id }) }),
        el("button", { class: "button", text: "Settings", onclick: () => openPlaylistDialog(playlist) }),
        el("button", { class: "button", text: "Copy", onclick: () => duplicatePlaylist(playlist) }),
        el("button", { class: "text-button danger", text: "Delete", onclick: () => removePlaylist(playlist) })),
      runThroughPane(playlist),
      el("div", { class: "playlist-list" }, items.length ? items : el("p", { class: "empty", text: "Nothing in this playlist yet. Tap ＋ Add." })));
    if (scroll && window.innerWidth < 900) editor.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  const RUN_THROUGH_COUNT = 8;

  function openRunThrough(playlistId, start) {
    state.runThrough = { playlistId, start };
    selectPlaylist(playlistId, false);
  }

  // Re-rendered whenever the playlist changes, so edits show up straight away.
  function runThroughPane(playlist) {
    const preview = state.runThrough;
    if (!preview || preview.playlistId !== playlist.id || !playlist.items.length) return null;
    const total = playlist.items.length;
    const start = Math.min(preview.start, Math.max(0, total - 1));
    const end = Math.min(total, start + RUN_THROUGH_COUNT);
    const status = el("p", { class: "form-help", text: "Drawing preview…" });
    const img = el("img", { alt: `Preview of items ${start + 1} to ${end}` });
    img.onload = () => { status.textContent = `Items ${start + 1}–${end} of ${total}, each shortened, with their real entrances.`; };
    img.onerror = () => { status.textContent = "Preview unavailable."; };
    img.src = `/api/playlists/${playlist.id}/run-through?start=${start}&count=${RUN_THROUGH_COUNT}&v=${Date.now()}`;
    return el("div", { class: "run-through" },
      el("div", { class: "sign-preview" }, img),
      status,
      el("div", { class: "card-actions" },
        el("button", { class: "button small", text: "◀ Earlier", disabled: start === 0, onclick: () => openRunThrough(playlist.id, Math.max(0, start - RUN_THROUGH_COUNT)) }),
        el("button", { class: "button small", text: "Later ▶", disabled: end >= total, onclick: () => openRunThrough(playlist.id, start + RUN_THROUGH_COUNT) }),
        el("button", { class: "button small", text: "Close preview", onclick: () => { state.runThrough = null; selectPlaylist(playlist.id, false); } })));
  }

  function itemBody(item, overrides = {}) {
    return {
      position: item.position,
      item_type: item.item_type,
      media_id: item.media_id,
      text_panel_id: item.text_panel_id,
      duration_ms: item.duration_ms,
      repeat_count: item.repeat_count,
      play_by: item.play_by,
      transition_type: item.transition_type,
      transition_duration_ms: item.transition_duration_ms,
      fit_mode: item.fit_mode,
      ...overrides
    };
  }

  async function moveItem(playlist, item, position) {
    if (position < 0 || position >= playlist.items.length) return;
    try {
      await send(`/api/playlists/${playlist.id}/items/${item.id}`, itemBody(item, { position }), "PUT");
      await load();
    } catch (error) { fail(error); }
  }

  async function removeItem(playlist, item) {
    if (!(await confirmAction("Remove this from the playlist?", "Remove"))) return;
    try {
      await request(`/api/playlists/${playlist.id}/items/${item.id}`, { method: "DELETE" });
      await load();
      notice("Removed.");
    } catch (error) { fail(error); }
  }

  async function removePlaylist(playlist) {
    if (!(await confirmAction(`Delete the playlist “${playlist.name}”?`))) return;
    try {
      await request(`/api/playlists/${playlist.id}`, { method: "DELETE" });
      state.selectedPlaylist = null;
      await load();
      notice("Playlist deleted.");
    } catch (error) { fail(error); }
  }

  async function duplicatePlaylist(playlist) {
    try {
      const copy = await request(`/api/playlists/${playlist.id}/duplicate`, { method: "POST" });
      state.selectedPlaylist = copy.id;
      await load();
      notice(`Made “${copy.name}”.`);
    } catch (error) { fail(error); }
  }

  function openPlaylistDialog(playlist = null) {
    const form = $("#playlist-form");
    form.reset();
    form.dataset.editId = playlist ? playlist.id : "";
    $("#playlist-dialog-title").textContent = playlist ? "Playlist settings" : "New playlist";
    $("#playlist-submit").textContent = playlist ? "Save" : "Create playlist";
    if (playlist) {
      form.elements.name.value = playlist.name;
      form.elements.description.value = playlist.description || "";
      form.elements.play_mode.value = playlist.play_mode || "loop";
      form.elements.is_enabled.checked = playlist.is_enabled;
      form.elements.is_default.checked = playlist.is_default;
    }
    $("#playlist-dialog").showModal();
  }

  async function savePlaylist(form) {
    const data = new FormData(form);
    const body = {
      name: data.get("name"),
      description: data.get("description") || "",
      play_mode: data.get("play_mode"),
      is_enabled: data.get("is_enabled") === "on",
      is_default: data.get("is_default") === "on"
    };
    const editId = form.dataset.editId ? Number(form.dataset.editId) : null;
    const playlist = editId
      ? await send(`/api/playlists/${editId}`, body, "PUT")
      : await send("/api/playlists", body);
    state.selectedPlaylist = playlist.id;
    showView("playlists");
    return editId ? "Playlist saved." : `Playlist “${playlist.name}” made. Now add things to it.`;
  }

  // Item dialog: add from a playlist, add from the library/messages, or change an item.
  function openItemDialog(context) {
    state.itemContext = context;
    const form = $("#item-form");
    form.reset();
    const editing = Boolean(context.item);
    const fromContent = Boolean(context.targetType);
    const many = context.targetIds?.length > 1;
    $("#item-dialog-title").textContent = editing ? "Change item" : many ? `Add ${context.targetIds.length} to a playlist` : "Add to playlist";
    $("#item-submit").textContent = editing ? "Save" : "Add";
    $("#item-playlist-row").hidden = !fromContent;
    $("#item-content-row").hidden = fromContent;
    if (fromContent) {
      if (!state.playlists.length) { notice("Make a playlist first.", "error"); return; }
      fillSelect($("#item-playlist"), state.playlists.map((playlist) => [playlist.id, playlist.name]), state.selectedPlaylist ?? state.playlists[0].id);
    }
    const item = context.item;
    const type = item ? item.item_type : context.targetType || "text_panel";
    form.elements.item_type.value = type;
    populateItemContent(item ? (item.media_id ?? item.text_panel_id) : context.targetId);
    fillSelect($("#item-transition"), TRANSITIONS, item ? item.transition_type : type === "text_panel" ? "impact" : "fade");
    if (item) {
      form.elements.duration.value = item.duration_ms / 1000;
      form.elements.repeats.value = item.repeat_count;
      form.elements.play_by.value = item.play_by;
      ensureOption(form.elements.transition_duration, item.transition_duration_ms, `${item.transition_duration_ms} ms`);
      form.elements.fit_mode.value = item.fit_mode === "letterbox" ? "fit" : item.fit_mode;
    }
    syncItemFields();
    $("#item-preview").removeAttribute("src");
    $("#item-dialog").showModal();
    renderItemPreview();
  }

  function populateItemContent(selected) {
    const form = $("#item-form");
    const type = form.elements.item_type.value;
    const options = type === "media"
      ? state.media.map((item) => [item.id, `${item.display_name} (${TYPE_LABEL[item.media_type]})`])
      : state.panels.map((panel) => [panel.id, panel.name]);
    fillSelect($("#item-content"), options.length ? options : [["", type === "media" ? "Nothing uploaded yet" : "No messages yet"]], selected);
  }

  function selectedItemTarget() {
    const context = state.itemContext || {};
    const form = $("#item-form");
    const type = context.targetType || form.elements.item_type.value;
    const id = context.targetType ? context.targetId : Number(form.elements.content_id.value);
    const media = type === "media" ? state.media.find((entry) => entry.id === id) : null;
    return { type, id, media };
  }

  // What a file really is, so the edit panes can say whether a GIF animates.
  function describeMedia(media) {
    if (!media) return "";
    if (media.media_type === "gif") {
      return media.duration_ms ? `Animated GIF · one loop is ${seconds(media.duration_ms)}` : "Animated GIF";
    }
    if (media.media_type === "video") return media.duration_ms ? `Video · ${seconds(media.duration_ms)} long` : "Video";
    return media.mime_type === "image/gif" ? "Still picture (a GIF with only one frame)" : "Still picture";
  }

  function updateItemInfo() {
    const info = $("#item-media-info");
    const form = $("#item-form");
    const ids = state.itemContext?.targetIds;
    let text = "";
    if (ids?.length > 1) {
      const chosen = ids.map((id) => state.media.find((entry) => entry.id === id)).filter(Boolean);
      const moving = chosen.filter((media) => media.media_type !== "image").length;
      text = `${chosen.length} items: ${moving} animated or video, ${chosen.length - moving} still.`;
    } else {
      const { media } = selectedItemTarget();
      text = describeMedia(media);
      const repeats = Math.max(1, Number(form.elements.repeats.value) || 1);
      if (media?.duration_ms && media.media_type !== "image" && form.elements.play_by.value === "repeats") {
        text += ` · ${repeats} time${repeats === 1 ? "" : "s"} ≈ ${seconds(repeats * media.duration_ms)}`;
      }
    }
    info.textContent = text;
    info.hidden = !text;
  }

  function syncItemFields() {
    const { type, media } = selectedItemTarget();
    const ids = state.itemContext?.targetIds;
    const animated = ids
      ? ids.some((id) => state.media.find((entry) => entry.id === id)?.media_type !== "image")
      : media && media.media_type !== "image";
    const form = $("#item-form");
    $("#item-repeats-row").hidden = !animated;
    if (!animated) form.elements.play_by.value = "time";
    $("#item-fit-row").hidden = type !== "media";
    updateItemInfo();
  }

  function itemFormBody(form) {
    const context = state.itemContext;
    const { type, id } = selectedItemTarget();
    const fields = form.elements;
    const playlistId = context.targetType ? Number(fields.playlist_id.value) : context.playlistId;
    const body = {
      item_type: type,
      media_id: type === "media" ? id : null,
      text_panel_id: type === "text_panel" ? id : null,
      duration_ms: Math.max(100, Math.round(Number(fields.duration.value) * 1000) || 6000),
      repeat_count: Math.max(1, Number(fields.repeats.value) || 1),
      play_by: fields.play_by.value,
      transition_type: fields.transition.value,
      transition_duration_ms: Number(fields.transition_duration.value),
      fit_mode: fields.fit_mode.value
    };
    return { playlistId, id, body };
  }

  // The item that plays just before this one, so the preview shows its real entrance.
  function previousItemId(playlistId) {
    const playlist = state.playlists.find((entry) => entry.id === playlistId);
    if (!playlist || !playlist.items.length) return null;
    const editing = state.itemContext?.item;
    if (!editing) return playlist.items[playlist.items.length - 1].id;
    const others = playlist.items.length;
    if (others < 2) return null;
    const index = playlist.items.findIndex((entry) => entry.id === editing.id);
    return playlist.items[(index - 1 + others) % others].id;
  }

  let itemPreviewTimer = null;
  let itemPreviewSequence = 0;
  let itemPreviewUrl = null;
  function scheduleItemPreview() {
    clearTimeout(itemPreviewTimer);
    itemPreviewTimer = setTimeout(renderItemPreview, 400);
  }

  async function renderItemPreview() {
    const sequence = ++itemPreviewSequence;
    const status = $("#item-preview-status");
    const { playlistId, id, body } = itemFormBody($("#item-form"));
    if (!id) { status.textContent = "Choose something to preview."; return; }
    status.textContent = "Drawing preview…";
    try {
      const response = await fetch("/api/playlists/preview-item", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...body, previous_item_id: previousItemId(playlistId) })
      });
      if (!response.ok) throw new Error("Preview unavailable");
      const blob = await response.blob();
      if (sequence !== itemPreviewSequence) return;
      if (itemPreviewUrl) URL.revokeObjectURL(itemPreviewUrl);
      itemPreviewUrl = URL.createObjectURL(blob);
      $("#item-preview").src = itemPreviewUrl;
      const count = state.itemContext?.targetIds?.length || 1;
      status.textContent = count > 1
        ? `The first of ${count}, coming in with these settings (shortened).`
        : previousItemId(playlistId)
          ? "The item before it, then this one coming in (shortened)."
          : "This item coming onto the sign (shortened).";
    } catch (error) {
      if (sequence === itemPreviewSequence) status.textContent = error.message;
    }
  }

  async function saveItem(form) {
    const context = state.itemContext;
    const { playlistId, id, body } = itemFormBody(form);
    if (!id) throw new Error("Choose what to add");
    if (context.targetIds?.length > 1) {
      // A tag group goes in as one item per picture or video, in library order.
      for (const mediaId of context.targetIds) {
        await send(`/api/playlists/${playlistId}/items`, { ...body, media_id: mediaId });
      }
      state.selectedPlaylist = playlistId;
      const playlist = state.playlists.find((entry) => entry.id === playlistId);
      return `Added ${context.targetIds.length} to “${playlist?.name || "playlist"}”.`;
    }
    if (context.item) {
      await send(`/api/playlists/${playlistId}/items/${context.item.id}`, { ...body, position: context.item.position }, "PUT");
    } else {
      await send(`/api/playlists/${playlistId}/items`, body);
    }
    state.selectedPlaylist = playlistId;
    const playlist = state.playlists.find((entry) => entry.id === playlistId);
    return context.item ? "Item saved." : `Added to “${playlist?.name || "playlist"}”.`;
  }

  // ---------- Pictures & videos ----------

  function renderTagFilter(visible) {
    const bar = $("#media-tags");
    if (!state.tags.length) { bar.hidden = true; return; }
    bar.hidden = false;
    const chip = (name, label) => el("button", {
      type: "button", class: `tag-chip${state.tagFilter === name ? " active" : ""}`, text: label,
      onclick: () => { state.tagFilter = state.tagFilter === name || name === null ? null : name; renderMedia(); }
    });
    const add = state.tagFilter && visible.length
      ? el("button", {
        type: "button", class: "button small primary", text: `＋ Add these ${visible.length} to a playlist`,
        onclick: () => openItemDialog({ targetType: "media", targetId: visible[0].id, targetIds: visible.map((item) => item.id) })
      })
      : null;
    bar.replaceChildren(
      el("div", { class: "tag-row" }, chip(null, "All"), ...state.tags.map((tag) => chip(tag.name, `#${tag.name} ${tag.count}`))),
      add);
  }

  function renderMedia() {
    const grid = $("#media-grid");
    const query = $("#media-search").value.trim().toLowerCase().replace(/^#/, "");
    const type = $("#media-filter").value;
    const items = state.media.filter((item) =>
      (!type || item.media_type === type)
      && (!state.tagFilter || item.tags.includes(state.tagFilter))
      && (!query || item.display_name.toLowerCase().includes(query) || item.tags.some((tag) => tag.includes(query))));
    $("#media-count").textContent = `${items.length} item${items.length === 1 ? "" : "s"}`;
    renderTagFilter(items);
    if (!items.length) {
      grid.replaceChildren(el("p", { class: "empty", text: state.media.length ? "Nothing matches." : "Nothing uploaded yet." }));
      return;
    }
    grid.replaceChildren(...items.map((item) => {
      const length = item.duration_ms ? ` · ${seconds(item.duration_ms)}` : "";
      const preparing = item.preparing ? " · getting it ready for the sign…" : "";
      return el("article", { class: "media-card", "data-anchor": `media-${item.id}` },
        el("div", { class: "media-thumb" }, thumb("media", item.id), el("span", { class: "media-type", text: TYPE_LABEL[item.media_type] })),
        el("div", { class: "media-body" },
          el("h3", { text: item.display_name, title: item.display_name }),
          el("p", { class: "media-meta", text: `${formatBytes(item.file_size_bytes)}${length}${preparing}` }),
          item.tags.length
            ? el("div", { class: "card-tags" }, ...item.tags.map((tag) => el("button", {
              type: "button", class: "tag-chip small", text: `#${tag}`,
              onclick: () => { state.tagFilter = tag; renderMedia(); window.scrollTo({ top: 0, behavior: "smooth" }); }
            })))
            : null,
          el("div", { class: "media-actions" },
            el("button", { class: "button small", text: "Show now", onclick: () => showNow("media", item.id, item.display_name, "crop", `media-${item.id}`) }),
            el("button", { class: "button small", text: "＋ Playlist", onclick: () => openItemDialog({ targetType: "media", targetId: item.id }) }),
            el("button", { class: "icon-button", "aria-label": "Name and tags", text: "✎", onclick: () => openMediaDialog(item) }),
            el("button", { class: "icon-button danger", "aria-label": "Delete", text: "✕", onclick: () => removeMedia(item) }))));
    }));
  }

  function renderDraftTags() {
    const draft = state.draftTags;
    $("#media-tag-list").replaceChildren(...(draft.length
      ? draft.map((tag) => el("button", {
        type: "button", class: "tag-chip active", text: `#${tag} ✕`, "aria-label": `Remove tag ${tag}`,
        onclick: () => { state.draftTags = draft.filter((entry) => entry !== tag); renderDraftTags(); }
      }))
      : [el("span", { class: "muted", text: "No tags yet." })]));
    const suggestions = state.tags.map((tag) => tag.name).filter((name) => !draft.includes(name)).slice(0, 12);
    $("#media-tag-suggestions").replaceChildren(...suggestions.map((name) => el("button", {
      type: "button", class: "tag-chip", text: `＋ #${name}`,
      onclick: () => addDraftTags(name)
    })));
    $("#media-tag-suggestions-row").hidden = !suggestions.length;
  }

  const cleanTag = (value) => value.trim().replace(/^#+/, "").replace(/\s+/g, " ").toLowerCase().slice(0, 32);

  function addDraftTags(text) {
    for (const tag of text.split(",").map(cleanTag).filter(Boolean)) {
      if (!state.draftTags.includes(tag) && state.draftTags.length < 20) state.draftTags.push(tag);
    }
    renderDraftTags();
  }

  function openMediaDialog(item) {
    state.editingMedia = item.id;
    state.draftTags = [...item.tags];
    const form = $("#media-form");
    form.reset();
    form.elements.display_name.value = item.display_name;
    const preview = $("#media-dialog-thumb");
    preview.replaceChildren(thumb("media", item.id));
    const size = item.width && item.height ? ` · ${item.width} × ${item.height}` : "";
    $("#media-dialog-info").textContent = `${describeMedia(item)}${size} · ${formatBytes(item.file_size_bytes)}`;
    renderDraftTags();
    $("#media-dialog").showModal();
  }

  async function saveMedia(form) {
    addDraftTags(form.elements.new_tag.value);
    const media = await send(`/api/media/${state.editingMedia}`, {
      display_name: form.elements.display_name.value,
      tags: state.draftTags
    }, "PATCH");
    return `Saved “${media.display_name}”.`;
  }

  async function removeMedia(item) {
    if (!(await confirmAction(`Delete “${item.display_name}”?`))) return;
    try {
      await request(`/api/media/${item.id}`, { method: "DELETE" });
      await load();
      notice("Deleted.");
    } catch (error) { fail(error); }
  }

  function uploadFile(file, row) {
    return new Promise((resolve) => {
      const bar = $("progress", row);
      const status = $(".upload-status", row);
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/media");
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) bar.value = event.loaded / event.total;
        if (event.loaded === event.total) status.textContent = "Checking…";
      };
      xhr.onload = () => {
        bar.value = 1;
        if (xhr.status < 300) { status.textContent = "✓ Added"; row.classList.add("done"); resolve(true); return; }
        let detail = `failed (${xhr.status})`;
        try { const payload = JSON.parse(xhr.responseText).detail; detail = typeof payload === "string" ? payload : detail; } catch (_) {}
        status.textContent = `✕ ${detail}`;
        row.classList.add("failed");
        resolve(false);
      };
      xhr.onerror = () => { status.textContent = "✕ connection lost"; row.classList.add("failed"); resolve(false); };
      const data = new FormData();
      data.append("file", file);
      xhr.send(data);
    });
  }

  async function submitUpload() {
    const files = [...$("#media-files").files];
    if (!files.length) throw new Error("Choose at least one file");
    const rows = files.map((file) => el("div", { class: "upload-row" },
      el("span", { class: "upload-name", text: file.name }),
      el("progress", { max: "1", value: "0" }),
      el("span", { class: "upload-status", text: "Waiting…" })));
    $("#upload-files").replaceChildren(...rows);
    let added = 0;
    for (let index = 0; index < files.length; index += 1) {
      $(".upload-status", rows[index]).textContent = "Sending…";
      if (await uploadFile(files[index], rows[index])) added += 1;
    }
    await load();
    return { message: `${added} of ${files.length} added.`, keepOpen: added !== files.length };
  }

  // ---------- Settings ----------

  function renderSettings(status, network, diagnostics) {
    const metrics = (node, rows) => node.replaceChildren(...rows.map(([term, value]) => el("div", {}, el("dt", { text: term }), el("dd", { text: value }))));
    $("#settings-network-dot").className = `status-dot ${network.access_point.active ? "online" : "error"}`;
    metrics($("#network-details"), [
      ["Hostname", network.hostname || "—"],
      ["Local address", network.local_address || "—"],
      ["Wi-Fi access point", network.access_point.active ? `${network.access_point.ssid} on` : "Off"],
      ["Internet", network.upstream.available ? `${network.upstream.interface || "connected"}` : "Offline"]
    ]);
    metrics($("#matrix-details"), [
      ["Resolution", `${status.matrix.width} × ${status.matrix.height}`],
      ["Mode", status.matrix.mode],
      ["Connected", status.matrix.connected ? "Yes" : "No"],
      ["Error", status.matrix.error || "None"],
      ["Temperature", status.temperature_c == null ? "—" : `${status.temperature_c.toFixed(1)} °C`]
    ]);
    if (diagnostics.backup_estimate_bytes != null) {
      $("#backup-estimate").textContent = `About ${formatBytes(diagnostics.backup_estimate_bytes)}. The sign takes a moment to pack it before the download starts.`;
    }
    const records = diagnostics.database?.records || {};
    $("#diagnostic-summary").textContent = [
      `Checked ${new Date(diagnostics.timestamp).toLocaleString()}`,
      `Media ${records.media ?? 0} · Messages ${records.text_panels ?? 0} · Playlists ${records.playlists ?? 0} · Items ${records.playlist_items ?? 0}`,
      `Data writable: ${diagnostics.directories?.data_writable ? "yes" : "no"} · Media writable: ${diagnostics.directories?.media_writable ? "yes" : "no"}`,
      `Storage ${formatBytes(status.storage.used_bytes)} of ${formatBytes(status.storage.total_bytes)} used`,
      diagnostics.process?.load_average
        ? `Load average ${diagnostics.process.load_average.map((value) => value.toFixed(2)).join(" ")}`
        : "Load average unavailable"
    ].join("\n");
  }

  // ---------- Loading ----------

  async function loadFonts() {
    if (state.fonts.length) return;
    try { state.fonts = await request("/api/text-panels/fonts"); }
    catch (_) { state.fonts = [{ id: DEFAULT_STYLE.font_family, label: "Impact (tall and heavy)" }]; }
  }

  async function load() {
    // Fonts load on their own so one failing list can never leave the editor fontless.
    await loadFonts();
    try {
      const [status, network, playlists, media, panels, diagnostics, tags] = await Promise.all([
        request("/api/status"), request("/api/network"), request("/api/playlists"),
        request("/api/media"), request("/api/text-panels"), request("/api/diagnostics"),
        request("/api/media/tags")
      ]);
      Object.assign(state, { playlists, media, panels, tags });
      if (state.tagFilter && !tags.some((tag) => tag.name === state.tagFilter)) state.tagFilter = null;
      renderPlaylists(); renderMedia(); renderPanels();
      renderStatus(status, network);
      renderSettings(status, network, diagnostics);
    } catch (error) {
      $("#connection-label").textContent = "Can't reach the sign";
      $("#connection-detail").textContent = "Check you're on the sign's Wi-Fi";
      $("#connection-dot").className = "status-dot error";
      fail(error);
    }
  }

  async function refreshStatus() {
    try {
      renderStatus(await request("/api/status"));
    } catch (_) {
      $("#connection-label").textContent = "Can't reach the sign";
      $("#connection-dot").className = "status-dot error";
    }
  }

  function showView(name) {
    if (!document.getElementById(name)?.classList.contains("view")) name = "dashboard";
    if (location.hash.slice(1) !== name) history.replaceState(null, "", `#${name}`);
    document.querySelectorAll(".view").forEach((view) => { view.hidden = view.id !== name; });
    document.querySelectorAll("[data-view]").forEach((link) => link.classList.toggle("active", link.dataset.view === name));
    if (name === "dashboard") liveLoop();
    if (state.returnTo && state.returnTo.view === name) {
      const { anchor } = state.returnTo;
      state.returnTo = null;
      revealAnchor(anchor);
    }
  }

  // ---------- Events ----------

  const SUBMITTERS = {
    "upload-form": submitUpload,
    "playlist-form": savePlaylist,
    "item-form": saveItem,
    "media-form": saveMedia,
    "text-form": async (form) => {
      const panel = await saveText(form);
      showView("text");
      return `Message “${panel.name}” saved.`;
    },
    "quick-form": async (form) => {
      const content = form.elements.content.value.trim();
      if (!content) throw new Error("Type a message first");
      const key = $("#quick-presets .preset.active")?.dataset.preset || "protest";
      const style = PRESETS.find(([presetKey]) => presetKey === key)[2];
      const panel = await send("/api/text-panels", {
        ...DEFAULT_STYLE, ...style, content, name: `Quick: ${content.replace(/\s+/g, " ").slice(0, 50)}`
      });
      await send("/api/playback/send", { target_type: "text_panel", target_id: panel.id });
      form.reset();
      return { message: "It's on the sign.", keepOpen: true };
    }
  };

  document.addEventListener("submit", async (event) => {
    const handler = SUBMITTERS[event.target.id];
    if (!handler) return;
    event.preventDefault();
    const submit = $("[type=submit]", event.target);
    if (submit) submit.disabled = true;
    try {
      const result = await handler(event.target);
      const { message, keepOpen } = typeof result === "string" ? { message: result, keepOpen: false } : result;
      if (!keepOpen) event.target.closest("dialog")?.close();
      await load();
      notice(message);
    } catch (error) {
      fail(error);
    } finally {
      if (submit) submit.disabled = false;
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.target.id !== "media-new-tag") return;
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      addDraftTags(event.target.value);
      event.target.value = "";
    }
  });

  document.addEventListener("input", (event) => {
    if (event.target.closest("#text-form")) {
      syncTextFields($("#text-form"));
      schedulePreview();
    }
    if (event.target.id === "media-search") renderMedia();
    if (event.target.closest("#item-timing") && event.target.type === "number") {
      $("#item-form").elements.play_by.value = event.target.name === "repeats" ? "repeats" : "time";
    }
    if (event.target.closest("#item-form")) { updateItemInfo(); scheduleItemPreview(); }
  });

  document.addEventListener("change", (event) => {
    if (event.target.closest("#text-form")) {
      syncTextFields($("#text-form"));
      schedulePreview();
    }
    if (event.target.id === "item-type") { populateItemContent(); syncItemFields(); }
    if (event.target.id === "item-content") syncItemFields();
    if (event.target.name === "play_by") updateItemInfo();
    if (event.target.closest("#item-form")) scheduleItemPreview();
    if (event.target.id === "media-filter") renderMedia();
    if (event.target.id === "media-files") {
      const names = [...event.target.files].map((file) => el("div", { class: "upload-row" }, el("span", { class: "upload-name", text: file.name })));
      $("#upload-files").replaceChildren(...(names.length ? names : ["Nothing chosen yet"]));
    }
  });

  const ACTIONS = {
    "dismiss-notice": () => { $("#notice").hidden = true; },
    "refresh-status": () => load(),
    "refresh-diagnostics": () => load(),
    "open-upload": () => {
      $("#upload-form").reset();
      $("#upload-files").textContent = "Nothing chosen yet";
      $("#upload-dialog").showModal();
    },
    "new-playlist": () => openPlaylistDialog(),
    "new-text-panel": () => openTextEditor(),
    "start-over": () => {
      const id = state.status?.playback?.playlist_id;
      if (id) startPlaylist(id, true);
    },
    "save-and-show": async () => {
      try {
        const panel = await saveText($("#text-form"));
        $("#text-dialog").close();
        await load();
        await showNow("text_panel", panel.id, panel.name, "crop", `panel-detail-${panel.id}`);
      } catch (error) { fail(error); }
    },
    "copy-diagnostics": async () => {
      const text = $("#diagnostic-summary").textContent.trim();
      try { await navigator.clipboard.writeText(text); notice("Diagnostics copied."); }
      catch (_) { notice("Copying is unavailable in this browser.", "error"); }
    }
  };

  document.addEventListener("click", (event) => {
    if (event.target.closest("#backup-link")) notice("Packing the backup… the download starts when it's ready.");
    const view = event.target.closest("[data-view]");
    if (view) { event.preventDefault(); showView(view.dataset.view); }
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
      const action = playback.dataset.playback;
      request(`/api/playback/${action}`, { method: "POST" })
        .then(async () => {
          await load();
          // Finishing a "Show now" goes back to where it was started.
          if (action === "release" && state.returnTo) showView(state.returnTo.view);
        })
        .catch(fail);
    }
  });

  window.addEventListener("hashchange", () => showView(location.hash.slice(1)));
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") refreshStatus(); });
  setInterval(() => { if (document.visibilityState === "visible") refreshStatus(); }, 3000);

  presetButtons($("#quick-presets"), () => {}, "protest");
  showView(location.hash.slice(1) || "dashboard");
  load();
})();
