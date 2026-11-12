/* Homecoming Launcher page.
   The page sends actions and renders state. In the desktop app, state comes
   from the native bridge (QWebChannel). In a browser it comes from a labelled
   preview fixture, and game actions are disabled. The page makes no
   business decisions of its own. */
(function () {
  "use strict";

  var DESIGN_W = 1635;
  var DESIGN_H = 962;
  var ADAPTIVE_MAX = 1099;
  var STACK_MAX = 599;
  var HERO_COUNT = 3;
  var LONG_TITLE_CHARS = 32;
  var CARD_START = 1;
  var CARD_COUNT = 4;
  var SLIDE_MS = 8000;
  var TICK_MS = 250;
  var TOAST_MS = 3000;
  var KEY_DOTS = "••••••••••••";
  var KEY_TYPING_MS = 1000;
  var PREVIEW_COPY = "Preview mode — game actions are disabled.";
  var PATCH_NOTES = "https://forums.homecomingservers.com/patch-notes/issue-28/page-4/issue-28-page-4-r47/";
  var EXTERNAL = {
    news: "https://forums.homecomingservers.com/forum/11-announcements/",
    forums: "https://forums.homecomingservers.com/",
    support: "https://forums.homecomingservers.com/forum/12-help-amp-support/",
    discord: "https://discord.com/invite/cityofheroes"
  };
  var TOKEN_VARS = {
    base: "--base", rail: "--rail", card: "--card", border: "--border", text: "--text",
    muted: "--muted", accent: "--accent", accentDeep: "--accent-deep", online: "--online"
  };
  var STATUS_KEYS = ["online", "offline", "starting", "maintenance", "unknown"];
  var FIELD_IDS = ["gameRoot", "protonPath", "compatPrefix", "scale"];
  // Preview-only sample of the normalized feed. The desktop app uses the live feed or the bundled snapshot.
  var PREVIEW_ARTICLES = [
    { id: "preview-issue-28", title: "ISSUE 28: LEGACY", subtitle: "PAGE 4 IS NOW LIVE!", headline: "ISSUE 28: LEGACY, PAGE 4 IS NOW LIVE!", description: "We're excited to announce that Issue 28: Legacy, Page 4 is now live!", date: "2026-10-06", url: PATCH_NOTES, category: "PATCH NOTES", imageKey: "background" },
    { id: "preview-halloween", title: "2026 HALLOWEEN EVENT IS NOW LIVE!", subtitle: "", headline: "2026 HALLOWEEN EVENT IS NOW LIVE!", description: "Available from October 6th until November 3rd", date: "2026-10-06", url: "https://forums.homecomingservers.com/topic/65858-2026-halloween-event-is-now-live/", category: "EVENT", imageKey: "feature1" },
    { id: "preview-dev-diary", title: "NEW DEV DIARY - CITY OF COWARDS: WHY DO ENEMIES RUN?", subtitle: "", headline: "NEW DEV DIARY - CITY OF COWARDS: WHY DO ENEMIES RUN?", description: "Issue 28, Page 4 is just around the corner and entering its final stages of testing on Open Beta!", date: "2026-10-03", url: "https://forums.homecomingservers.com/topic/65806-dev-diary-city-of-cowards-why-do-enemies-run/", category: "DEV DIARY", imageKey: "feature3" },
    { id: "preview-open-beta", title: "ISSUE 28: LEGACY", subtitle: "PAGE 4 IS NOW ON OPEN BETA!", headline: "ISSUE 28: LEGACY, PAGE 4 IS NOW ON OPEN BETA!", description: "We're excited to announce that Issue 28: Legacy, Page 4 is finally available for testing on Open Beta!", date: "2026-08-20", url: "https://forums.homecomingservers.com/topic/65308-open-beta-patch-notes-for-august-20th-2026-issue-28-page-4/", category: "OPEN BETA", imageKey: "feature2" },
    { id: "preview-july-patch", title: "PATCH NOTES FOR JULY 7TH", subtitle: "", headline: "PATCH NOTES FOR JULY 7TH", description: "Today's update addresses a couple bugs.", date: "2026-07-07", url: "https://forums.homecomingservers.com/patch-notes/issue-28/page-3/july-7-update-r46/", category: "PATCH NOTES", imageKey: "feature4" }
  ];

  var params = new URLSearchParams(window.location.search);
  var testMode = params.get("test") === "1";
  var forcePreview = params.get("preview") === "1";
  var bridge = null;
  var state = null;
  var previewMode = false;
  var previewSerial = 0;
  var lastSkinKey = "";
  var lastSettingsKey = "";
  var lastToastKey = "";
  var expiredToastKey = "";
  var keyTouched = false;
  var keyTimer = null;
  var voicePick = null;
  var lastSerials = { watch: -1, picked: 0 };
  var lastVoiceKey = "";
  var toastTimer = null;
  var settingsVisible = false;
  var lastFocus = null;
  var stateCount = 0;
  var testLog = [];
  var externalLog = [];
  // One carousel timer for the whole page. Pauses are checked on each tick, so state updates never reset it.
  var carousel = { activeId: "", index: 0, elapsed: 0, hover: false, focus: false };
  var reducedQuery = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;

  function $(id) { return document.getElementById(id); }
  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  function isPrimary(e) {
    return e.button === 0 && !e.ctrlKey && !e.shiftKey && !e.altKey && !e.metaKey;
  }
  function nativeReady() {
    return !forcePreview && typeof qt !== "undefined" && qt && qt.webChannelTransport;
  }
  function reducedMotion() {
    return !!(reducedQuery && reducedQuery.matches);
  }
  function profile(id) {
    var found = (state.profiles || []).filter(function (p) { return p.id === id; })[0];
    return found || { id: id, installed: false, version: null };
  }
  function findSkin(list, id) {
    return (list || []).filter(function (skin) { return skin.id === id; })[0] || null;
  }
  function currentSkin() {
    return state ? findSkin(state.skins, state.skinId) : null;
  }
  function formatDate(iso) {
    if (!iso) return "";
    var date = new Date(iso + "T00:00:00Z");
    return date.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric", timeZone: "UTC" }).toUpperCase();
  }
  function cssUrl(url) {
    return 'url("' + String(url).replace(/"/g, "%22") + '")';
  }
  function heroArticles() {
    return ((state && state.feed.articles) || []).slice(0, HERO_COUNT);
  }

  /* ----- sending actions ----- */

  function send(action, payload) {
    payload = payload || {};
    if (previewMode) {
      previewDispatch(action, payload);
      return;
    }
    if (bridge) bridge.dispatch(action, JSON.stringify(payload));
  }

  /* ----- startup ----- */

  function start() {
    bindEvents();
    layout();
    window.setInterval(carouselTick, TICK_MS);
    if (nativeReady()) {
      // The channel script is native-only, so it loads here and not in the HTML.
      var script = document.createElement("script");
      script.src = "qrc:///qtwebchannel/qwebchannel.js";
      script.onload = connectBridge;
      document.head.appendChild(script);
    } else {
      previewMode = true;
      loadPreview();
    }
  }

  function connectBridge() {
    new QWebChannel(qt.webChannelTransport, function (channel) {
      bridge = channel.objects.launcher;
      bridge.stateChanged.connect(function (json) { applyState(JSON.parse(json)); });
      bridge.getState(function (json) { applyState(JSON.parse(json)); });
    });
  }

  // The Homecoming skin, copied from skins/homecoming/skin.json. A fetch would fail under file:// and log errors.
  function loadPreview() {
    var assets = {};
    ["background", "logo", "brand", "feature1", "feature2", "feature3", "feature4"].forEach(function (key) {
      assets[key] = "../skins/homecoming/assets/" + key + ".png";
    });
    var tokens = {
      base: "#01060c", rail: "#030c17", card: "#061626", border: "#266082", text: "#f5f7fa",
      muted: "#9bb6d0", accent: "#ff7b29", accentDeep: "#9d2d18", online: "#8fed00"
    };
    applyState(previewFixture([{ id: "homecoming", name: "Homecoming", tokens: tokens, assets: assets }]));
  }

  function previewFixture(skins) {
    var skinId = skins.length ? skins[0].id : "";
    return {
      mode: "preview",
      phase: "ready",
      profile: "live",
      profiles: [
        { id: "live", name: "Homecoming", installed: true, version: "Issue 28, Page 4 - 28.4.8623" },
        { id: "beta", name: "Open Beta", installed: false, version: null }
      ],
      job: null,
      shards: ["Torchbearer", "Excelsior", "Indomitable", "Everlasting", "Victory", "Reunion"].map(function (name) {
        return { name: name, status: "Online" };
      }),
      shardsState: "fresh",
      feed: { state: "offline", articles: clone(PREVIEW_ARTICLES), fetchedAt: null, badge: "OFFLINE NEWS" },
      error: null,
      notice: null,
      confirm: null,
      settingsOpen: false,
      settingsTab: "general",
      settingsSaving: false,
      settingsError: null,
      watch: { error: null, gameFolderMessage: null, chatLogMessage: null, chimeError: null, speakError: null },
      voice: { keyState: "none", line: null, groups: [] },
      settings: {
        gameRoot: "",
        protonPath: "",
        compatPrefix: "",
        skinId: skinId,
        scale: 1,
        showWatch: true,
        showWatchSerial: 0,
        startAutomatically: true,
        alertChime: true,
        speakAlerts: false,
        voiceId: null,
        picked: null,
        epoch: 0,
        lastProfile: "live",
        installed: true,
        version: "Issue 28, Page 4 - 28.4.8623"
      },
      pathProblems: [],
      skins: skins,
      skinErrors: [],
      skinId: skinId,
      scale: 1
    };
  }

  /* ----- rendering ----- */

  function applyState(next) {
    state = next;
    stateCount += 1;
    document.body.dataset.ready = "1";
    document.body.dataset.mode = state.mode;
    document.body.dataset.phase = state.phase;
    applySkin();
    renderFeature();
    renderCards();
    renderRail();
    renderShards();
    renderSettings();
    renderConfirm();
    renderToast();
  }

  function applySkin() {
    var skin = currentSkin();
    var key = skin ? skin.id + JSON.stringify(skin.tokens) + JSON.stringify(skin.assets) : "";
    if (key === lastSkinKey) return;
    lastSkinKey = key;
    var root = document.documentElement.style;
    var tokens = skin ? skin.tokens : {};
    Object.keys(TOKEN_VARS).forEach(function (name) {
      if (tokens[name]) root.setProperty(TOKEN_VARS[name], tokens[name]);
    });
    var assets = skin ? skin.assets : {};
    setImage($("logo"), assets.logo);
    // Every skin, Homecoming included, shows its brand image. No brand asset means no brand.
    $("brand-img").hidden = !assets.brand;
    setImage($("brand-img"), assets.brand);
  }

  function setImage(img, url) {
    var next = url || "";
    if (img.getAttribute("src") !== next) img.setAttribute("src", next);
  }

  /* ----- hero carousel: one timer, paused by pointer, focus, hidden page, settings, dialogs and reduced motion ----- */

  function renderFeature() {
    var heroes = heroArticles();
    var badge = $("news-badge");
    badge.textContent = state.feed.badge || "";
    badge.hidden = !state.feed.badge;
    var index = 0;
    heroes.forEach(function (hero, i) {
      if (hero.id === carousel.activeId) index = i;
    });
    carousel.index = index;
    carousel.activeId = heroes.length ? heroes[index].id : "";
    renderMarks(heroes.length);
    renderSlide(false);
  }

  function renderSlide(animate) {
    var hero = heroArticles()[carousel.index] || { id: "", title: "", subtitle: "", description: "", date: null, category: "", imageKey: "background" };
    var title = $("hero-title");
    var words = (hero.title || "").split(" ");
    var accent = words.pop() || "";
    title.textContent = "";
    if (words.length) title.appendChild(document.createTextNode(words.join(" ") + " "));
    var span = document.createElement("span");
    span.className = "accent";
    span.textContent = accent;
    title.appendChild(span);
    // Long headlines, or headlines with no subtitle, switch to the two-line title style in CSS.
    $("feature").classList.toggle("is-long-title", !hero.subtitle || (hero.title || "").length > LONG_TITLE_CHARS);
    $("hero-subtitle").hidden = !hero.subtitle;
    $("hero-subtitle").textContent = hero.subtitle || "";
    $("hero-description").textContent = hero.description || "";
    $("hero-date").textContent = formatDate(hero.date);

    var cta = $("hero-cta");
    cta.dataset.id = hero.id || "";
    cta.disabled = !hero.id;
    $("hero-cta-label").textContent = hero.category === "PATCH NOTES" ? "READ PATCH NOTES" : "READ MORE";

    var skin = currentSkin();
    var assets = skin ? skin.assets : {};
    var image = assets[hero.imageKey] || assets.background;
    $("art").style.backgroundImage = image ? cssUrl(image) : "none";
    $("art").classList.toggle("is-article-art", !!image && image !== assets.background);

    var marks = document.querySelectorAll(".slide-mark");
    for (var i = 0; i < marks.length; i += 1) {
      marks[i].setAttribute("aria-current", i === carousel.index ? "true" : "false");
    }

    if (animate && !reducedMotion()) {
      var zone = $("feature");
      zone.classList.remove("is-changing");
      void zone.offsetWidth;
      zone.classList.add("is-changing");
    }
  }

  function renderMarks(count) {
    var marks = document.querySelectorAll(".slide-mark");
    for (var i = 0; i < marks.length; i += 1) {
      marks[i].hidden = i >= count;
      marks[i].setAttribute("aria-label", "Show news " + (i + 1) + " of " + count);
    }
    $("slide-marks").hidden = count < 1;
  }

  function goTo(index) {
    var heroes = heroArticles();
    if (!heroes.length) return;
    carousel.index = ((index % heroes.length) + heroes.length) % heroes.length;
    carousel.activeId = heroes[carousel.index].id;
    carousel.elapsed = 0;
    renderSlide(true);
  }

  function carouselPaused() {
    if (!state || heroArticles().length < 2) return true;
    return reducedMotion() || document.hidden || carousel.hover || carousel.focus ||
      state.settingsOpen || !!state.confirm;
  }

  function carouselTick() {
    if (carouselPaused()) return;
    carousel.elapsed += TICK_MS;
    if (carousel.elapsed >= SLIDE_MS) goTo(carousel.index + 1);
  }

  function focusMark(index) {
    var mark = document.querySelector('.slide-mark[data-slide="' + index + '"]');
    if (mark) mark.focus();
  }

  /* ----- news cards: feed items 1 to 4 ----- */

  function renderCards() {
    var articles = ((state && state.feed.articles) || []).slice(CARD_START, CARD_START + CARD_COUNT);
    var skin = currentSkin();
    var assets = skin ? skin.assets : {};
    var cards = document.querySelectorAll(".card");
    for (var i = 0; i < cards.length; i += 1) {
      var card = cards[i];
      var article = articles[i];
      card.hidden = !article;
      if (!article) continue;
      card.dataset.id = article.id;
      card.setAttribute("aria-label", "Read official news: " + (article.headline || article.title));
      setImage(card.querySelector(".card-img"), assets[article.imageKey]);
      var kicker = article.category || "";
      if (article.date) kicker += " · " + formatDate(article.date);
      card.querySelector(".card-kicker").textContent = kicker;
      card.querySelector(".card-title").textContent = article.headline || article.title || "";
      card.querySelector(".card-text").textContent = article.description || "";
    }
  }

  /* ----- rail: play, status line, beta, per-button progress, servers ----- */

  function renderRail() {
    var phase = state.phase;
    var job = state.job;
    var live = profile("live");
    var beta = profile("beta");
    var play = $("play");
    var label = "PLAY";
    var aria = "Play Homecoming";
    var disabled = true;

    // Each button reacts only to a job that names its own profile.
    var liveJob = job && job.profile === "live" ? job : null;
    if (previewMode) {
      aria = "Play Homecoming (unavailable in preview)";
    } else if (liveJob) {
      if (job.kind === "update") { label = "UPDATING"; aria = "Updating Homecoming"; }
      else if (job.kind === "launch") { label = "LAUNCHING"; aria = "Launching Homecoming"; }
      else if (job.kind === "install") { label = "INSTALL"; aria = "Installing Homecoming"; }
      else { aria = "Checking installation"; }
    } else if (job) {
      if (job.kind === "discover") aria = "Checking installation";
    } else if (phase === "error") {
      label = "RETRY";
      aria = "Retry checking installation";
      disabled = false;
    } else if (phase === "ready") {
      label = live.installed ? "PLAY" : "INSTALL";
      aria = live.installed ? "Play Homecoming" : "Install Homecoming";
      disabled = false;
    }
    $("play-title").textContent = label;
    play.dataset.label = label;
    play.setAttribute("aria-label", aria);
    play.disabled = disabled;

    // Only warnings stay in the status line. Errors go to the toast, and success notices go to the toast too.
    var line = $("status-line");
    var text = "";
    var tone = "";
    if (previewMode) {
      text = PREVIEW_COPY;
      tone = "warn";
    } else if (phase === "missing") {
      text = (state.pathProblems[0] || {}).message || "Set up your installation in Settings.";
      tone = "warn";
    } else if (!job && state.notice && state.notice.tone === "warn") {
      text = state.notice.text;
      tone = "warn";
    }
    line.textContent = text;
    line.dataset.tone = tone;
    line.hidden = !text;

    renderButtonProgress(play, job, { profile: "live", title: "HOMECOMING" });

    var betaButton = $("beta");
    var betaSub = $("beta-sub");
    var betaAria = beta.installed ? "Update and launch Open Beta" : "Install Open Beta";
    var betaDisabled = true;
    var betaText = beta.installed ? (beta.version || "Ready") : "Not Installed";
    if (previewMode) {
      betaText = beta.installed ? (beta.version || "Ready") : "Not Installed";
    } else if (job || phase === "discovering") {
      betaDisabled = true;
      if (phase === "discovering") betaText = "Checking installation…";
    } else if (phase === "error") {
      betaText = "Retry";
      betaAria = "Retry checking installation";
      betaDisabled = false;
    } else if (phase === "ready") {
      betaDisabled = false;
    } else {
      betaText = "Set up your installation";
    }
    betaSub.textContent = betaText;
    betaButton.setAttribute("aria-label", betaAria);
    betaButton.disabled = betaDisabled;
    renderButtonProgress(betaButton, job, { profile: "beta", title: "OPEN BETA" });
  }

  /* One presentation for both targets. Only a job that names this profile, and is an update, install or launch, shows here. */
  function renderButtonProgress(button, job, identity) {
    var wrap = button.querySelector(".button-progress");
    var active = !previewMode && !!job && job.profile === identity.profile &&
      (job.kind === "update" || job.kind === "install" || job.kind === "launch");
    if (!active) {
      delete button.dataset.busyProfile;
      wrap.hidden = true;
      return;
    }
    var launching = job.kind === "launch";
    var unknown = !launching && !!job.uncertain;
    var determinate = typeof job.progress === "number" && !unknown;
    // Live: the title already names the action, so the status line carries only the percentage (or WORKING when unknown).
    // Beta: its title is OPEN BETA, so the status keeps the phase label from the job.
    var status;
    if (launching) status = identity.profile === "beta" ? "LAUNCHING" : "";
    else if (unknown) status = "UPDATE STATUS UNKNOWN";
    else if (identity.profile === "live") status = determinate ? job.progress + "%" : "WORKING";
    else status = job.label || "";
    var detail = launching ? "" : (unknown ? "Still checking…" : (job.detail || ""));
    button.dataset.busyProfile = identity.profile;
    wrap.hidden = false;
    wrap.querySelector(".button-progress-identity").textContent = identity.title;
    wrap.querySelector(".button-progress-status").textContent = status;
    var detailText = wrap.querySelector(".button-progress-detail");
    detailText.textContent = detail;
    detailText.title = detail;
    wrap.querySelector(".button-progress-track").hidden = launching;
    wrap.dataset.determinate = determinate ? "true" : "false";
    wrap.querySelector(".button-progress-fill").style.width = determinate ? job.progress + "%" : "";
  }

  /* ----- toast: success notices fade after 3 seconds, errors stay until they clear ----- */

  function showToast(text, tone, ms, onExpire) {
    var toast = $("toast");
    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = null;
    toast.textContent = text;
    toast.dataset.tone = tone;
    toast.hidden = false;
    if (ms) {
      toastTimer = window.setTimeout(function () {
        toastTimer = null;
        toast.hidden = true;
        if (onExpire) onExpire();
      }, ms);
    }
  }

  function hideToast() {
    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = null;
    $("toast").hidden = true;
  }

  /* One thing at a time: the launcher's error, then a short notice for 3 seconds, then the watch message. */
  function renderToast() {
    var error = "";
    if (!previewMode && state.phase === "error" && state.error) {
      error = state.error;
    } else if (state.notice && state.notice.tone === "error") {
      error = state.notice.text;
    }
    if (error) {
      showToast(error, "error", 0);
      return;
    }
    var notice = state.notice;
    if (notice && (notice.tone === "info" || notice.tone === "ok")) {
      var key = JSON.stringify(notice);
      if (key !== lastToastKey) {
        lastToastKey = key;
        expiredToastKey = "";
        showToast(notice.text, "info", TOAST_MS, function () {
          expiredToastKey = key;
          renderToast();
        });
        return;
      }
      if (key !== expiredToastKey) return;
    } else if (!notice) {
      lastToastKey = "";
      expiredToastKey = "";
    }
    if (state.watch && state.watch.error) {
      showToast(state.watch.error, "error", 0);
      return;
    }
    hideToast();
  }

  function renderShards() {
    var title = $("shards-title");
    var stale = state.shardsState === "stale";
    title.textContent = stale ? "SERVER STATUS · STALE" : "SERVER STATUS";
    title.dataset.stale = stale ? "true" : "false";
    var list = $("shard-list");
    while (list.firstChild) list.removeChild(list.firstChild);
    state.shards.forEach(function (row) {
      var item = document.createElement("li");
      var key = String(row.status || "").toLowerCase();
      item.dataset.status = STATUS_KEYS.indexOf(key) >= 0 ? key : "unknown";
      var diamond = document.createElement("span");
      diamond.className = "diamond";
      diamond.setAttribute("aria-hidden", "true");
      var name = document.createElement("span");
      name.className = "sname";
      name.textContent = row.name;
      var status = document.createElement("span");
      status.className = "sstate";
      status.textContent = row.status;
      item.appendChild(diamond);
      item.appendChild(name);
      item.appendChild(status);
      list.appendChild(item);
    });
  }

  function renderSettings() {
    var open = state.settingsOpen;
    var key = JSON.stringify([state.settings.epoch, (state.skins || []).map(function (s) { return s.id; })]);
    if (open && (!settingsVisible || key !== lastSettingsKey)) {
      fillSettings();
    }
    if (open && !settingsVisible) {
      lastFocus = document.activeElement;
      settingsVisible = true;
      window.setTimeout(function () { $("f-gameRoot").focus(); }, 0);
    } else if (!open && settingsVisible) {
      settingsVisible = false;
      if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
    }
    if (open) lastSettingsKey = key;
    if (open) {
      applyForcedValues();
      renderTabs();
      renderAddons();
    }
    renderSettingsErrors();
    var busy = !!state.job;
    ["f-gameRoot", "f-protonPath", "f-compatPrefix"].forEach(function (id) { $(id).disabled = busy; });
    document.querySelectorAll('[data-action^="choose-"]').forEach(function (button) {
      button.disabled = busy || previewMode;
    });
    $("settings-save").disabled = !!state.settingsSaving;
    $("open-official").disabled = previewMode || state.pathProblems.length > 0;
    $("preview-note").hidden = !previewMode;
    $("gear").setAttribute("aria-expanded", open ? "true" : "false");
  }

  /* Values the launcher changes by itself while Settings is open: the Show watch switch and a chosen folder. */
  function applyForcedValues() {
    var s = state.settings;
    if (s.showWatchSerial !== lastSerials.watch) {
      lastSerials.watch = s.showWatchSerial;
      $("f-showWatch").checked = s.showWatch;
    }
    if (s.picked && s.picked.serial !== lastSerials.picked) {
      lastSerials.picked = s.picked.serial;
      $("f-" + s.picked.field).value = s.picked.value;
    }
  }

  function renderTabs() {
    ["general", "addons"].forEach(function (name) {
      var tab = $("tab-" + name);
      var selected = state.settingsTab === name;
      tab.setAttribute("aria-selected", selected ? "true" : "false");
      $("panel-" + name).hidden = !selected;
    });
  }

  function addMessage(list, text) {
    var item = document.createElement("li");
    item.textContent = text;
    list.appendChild(item);
  }

  function setLine(id, line) {
    var element = $(id);
    element.hidden = !line;
    element.textContent = line ? line.text || line : "";
  }

  function renderAddons() {
    var watch = state.watch;
    var list = $("watch-messages");
    while (list.firstChild) list.removeChild(list.firstChild);
    if (watch.error) addMessage(list, watch.error);
    if (watch.gameFolderMessage) addMessage(list, watch.gameFolderMessage);
    if (watch.chatLogMessage) addMessage(list, watch.chatLogMessage);
    if (watch.chimeError) addMessage(list, watch.chimeError);
    var voice = state.voice;
    setLine("key-line", voice.line);
    setLine("speak-line", watch.speakError);
    var key = $("f-key");
    key.disabled = voice.keyState === "unavailable";
    if (!keyTouched) key.value = voice.keyState === "stored" ? KEY_DOTS : "";
    renderVoices();
  }

  function renderVoices() {
    var voice = state.voice;
    var chosen = voicePick !== null ? voicePick : state.settings.voiceId;
    var key = JSON.stringify([voice.keyState === "stored", voice.groups, chosen]);
    if (key === lastVoiceKey) return;
    lastVoiceKey = key;
    closeVoiceList();
    var select = $("f-voiceId");
    while (select.firstChild) select.removeChild(select.firstChild);
    if (voice.keyState !== "stored") return;
    var placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.disabled = true;
    placeholder.textContent = "Choose a voice";
    select.appendChild(placeholder);
    var found = false;
    voice.groups.forEach(function (group) {
      var heading = document.createElement("optgroup");
      heading.label = group.label;
      group.voices.forEach(function (item) {
        var option = document.createElement("option");
        option.value = item.id;
        option.textContent = item.title;
        heading.appendChild(option);
        if (item.id === chosen) found = true;
      });
      select.appendChild(heading);
    });
    select.value = found ? chosen : "";
  }

  // The voice list opens inside the page: the built-in browser's own popup closed at once for this long list.
  var voiceList = null;

  function closeVoiceList() {
    if (voiceList) voiceList.hidden = true;
  }

  function voiceListOpen() {
    return !!voiceList && !voiceList.hidden;
  }

  function openVoiceList() {
    var select = $("f-voiceId");
    if (select.disabled || !select.options.length) return;
    var dialog = select.closest(".dialog");
    if (!voiceList) {
      voiceList = document.createElement("div");
      voiceList.className = "voice-list";
      voiceList.id = "voice-list";
      voiceList.setAttribute("role", "listbox");
      voiceList.setAttribute("aria-label", "Voices");
      voiceList.addEventListener("click", function (e) {
        var item = e.target.closest(".voice-item");
        if (!item) return;
        $("f-voiceId").value = item.dataset.value;
        $("f-voiceId").dispatchEvent(new Event("change", { bubbles: true }));
        closeVoiceList();
        $("f-voiceId").focus();
      });
    }
    dialog.appendChild(voiceList);
    voiceList.textContent = "";
    function add(option) {
      if (option.disabled) return;
      var item = document.createElement("div");
      item.className = "voice-item";
      item.setAttribute("role", "option");
      item.dataset.value = option.value;
      item.textContent = option.textContent;
      if (option.value === select.value) item.setAttribute("aria-selected", "true");
      voiceList.appendChild(item);
    }
    Array.prototype.forEach.call(select.children, function (child) {
      if (child.tagName === "OPTGROUP") {
        var heading = document.createElement("div");
        heading.className = "voice-group";
        heading.textContent = child.label;
        voiceList.appendChild(heading);
        Array.prototype.forEach.call(child.children, add);
      } else {
        add(child);
      }
    });
    var box = dialog.getBoundingClientRect(), at = select.getBoundingClientRect();
    var top = box.top + dialog.clientTop, left = box.left + dialog.clientLeft;
    var below = box.bottom - at.bottom - 8, above = at.top - box.top - 8;
    var down = below >= 240 || below >= above;
    voiceList.style.left = (at.left - left) + "px";
    voiceList.style.width = at.width + "px";
    voiceList.style.maxHeight = Math.min(360, down ? below : above) + "px";
    voiceList.style.top = down ? (at.bottom - top) + "px" : "";
    voiceList.style.bottom = down ? "" : (box.bottom - dialog.clientTop - at.top) + "px";
    voiceList.hidden = false;
    var chosen = voiceList.querySelector('[aria-selected="true"]');
    if (chosen) voiceList.scrollTop = chosen.offsetTop - (voiceList.clientHeight - chosen.offsetHeight) / 2;
  }

  function fillSettings() {
    var s = state.settings;
    $("f-gameRoot").value = s.gameRoot;
    $("f-protonPath").value = s.protonPath;
    $("f-compatPrefix").value = s.compatPrefix;
    $("f-showWatch").checked = s.showWatch;
    $("f-startAutomatically").checked = s.startAutomatically;
    $("f-alertChime").checked = s.alertChime;
    $("f-speakAlerts").checked = s.speakAlerts;
    lastSerials.watch = s.showWatchSerial;
    lastSerials.picked = s.picked ? s.picked.serial : 0;
    keyTouched = false;
    voicePick = null;
    lastVoiceKey = "";
    closeVoiceList();
    $("f-key").value = "";
    $("settings-body").scrollTop = 0;
    var select = $("f-skinId");
    while (select.firstChild) select.removeChild(select.firstChild);
    (state.skins || []).forEach(function (skin) {
      var option = document.createElement("option");
      option.value = skin.id;
      option.textContent = skin.name;
      select.appendChild(option);
    });
    if (!(state.skins || []).length) {
      var none = document.createElement("option");
      none.value = "";
      none.textContent = "No valid skins";
      select.appendChild(none);
    }
    select.value = s.skinId || "";
    $("f-scale").value = String(s.scale);
    var versionText = "Not installed";
    if (state.phase === "discovering") versionText = "Checking installation…";
    else if (s.installed) versionText = s.version || "Installed";
    $("f-version").textContent = versionText;
    $("missing-note").hidden = state.phase === "discovering" || !!s.installed;
    var errors = $("skin-errors");
    while (errors.firstChild) errors.removeChild(errors.firstChild);
    (state.skinErrors || []).forEach(function (row) {
      var item = document.createElement("li");
      item.textContent = row.folder + ": " + row.message;
      errors.appendChild(item);
    });
  }

  function renderSettingsErrors() {
    FIELD_IDS.forEach(function (field) {
      $("e-" + field).textContent = "";
      var input = $("f-" + field);
      if (input) input.removeAttribute("aria-invalid");
    });
    $("f-skinId").removeAttribute("aria-invalid");
    $("settings-error").textContent = "";
    var error = state.settingsError;
    if (!error) return;
    if (FIELD_IDS.indexOf(error.field) >= 0) {
      $("e-" + error.field).textContent = error.message;
      var input = $(error.field === "scale" ? "f-scale" : "f-" + error.field);
      input.setAttribute("aria-invalid", "true");
      input.focus();
    } else {
      if (error.field === "skinId") $("f-skinId").setAttribute("aria-invalid", "true");
      $("settings-error").textContent = error.message;
    }
  }

  function renderConfirm() {
    var confirm = state.confirm;
    var dialog = $("confirm");
    var primary = $("confirm-primary");
    var secondary = $("confirm-secondary");
    if (!confirm) {
      dialog.hidden = true;
    } else {
      dialog.hidden = false;
      if (confirm.kind === "install") {
        var name = confirm.profile === "beta" ? "Open Beta" : "Homecoming";
        $("confirm-title").textContent = "Install " + name + "?";
        $("confirm-body").textContent = "The official launcher will download game files to:";
        $("confirm-path").textContent = state.settings.gameRoot;
        primary.textContent = "Install";
        primary.dataset.action = "confirm-install";
        primary.dataset.profile = confirm.profile;
        secondary.textContent = "Cancel";
        secondary.dataset.action = "cancel-install";
      } else {
        $("confirm-title").textContent = "An operation is still running";
        $("confirm-body").textContent = "Closing this window will leave the official launcher running.";
        $("confirm-path").textContent = "";
        primary.textContent = "Close launcher";
        primary.dataset.action = "close-confirm";
        delete primary.dataset.profile;
        secondary.textContent = "Wait";
        secondary.dataset.action = "close-cancel";
      }
    }
    $("settings").hidden = !!confirm || !state.settingsOpen;
    $("modal-layer").hidden = !confirm && !state.settingsOpen;
  }

  /* ----- layout ----- */

  function layout() {
    var width = window.innerWidth;
    var height = window.innerHeight;
    var adaptive = width <= ADAPTIVE_MAX;
    document.body.classList.toggle("adaptive", adaptive);
    document.body.classList.toggle("stack", width <= STACK_MAX);
    var stage = $("stage");
    if (adaptive) {
      stage.style.transform = "";
      stage.style.left = "";
      stage.style.top = "";
      return;
    }
    var scale = Math.min(width / DESIGN_W, height / DESIGN_H);
    stage.style.transform = "scale(" + scale + ")";
    stage.style.left = Math.round((width - DESIGN_W * scale) / 2) + "px";
    stage.style.top = Math.round((height - DESIGN_H * scale) / 2) + "px";
  }

  /* ----- input ----- */

  function activeDialog() {
    if (!state) return null;
    if (state.confirm) return $("confirm");
    if (state.settingsOpen) return $("settings");
    return null;
  }

  function trapFocus(e) {
    var dialog = activeDialog();
    if (!dialog) return;
    var items = Array.prototype.filter.call(
      dialog.querySelectorAll("button, input, select"),
      function (el) { return !el.disabled && el.offsetParent !== null; }
    );
    if (!items.length) return;
    var first = items[0];
    var last = items[items.length - 1];
    var current = document.activeElement;
    if (!dialog.contains(current)) {
      e.preventDefault();
      (e.shiftKey ? last : first).focus();
    } else if (e.shiftKey && current === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && current === last) {
      e.preventDefault();
      first.focus();
    }
  }

  function bindEvents() {
    document.addEventListener("click", function (e) {
      if (!isPrimary(e)) return;
      var button = e.target.closest("button");
      if (!button || button.disabled) return;
      if (button.id === "gear") {
        e.preventDefault();
        send(state && state.settingsOpen ? "settings-cancel" : "settings-open");
        return;
      }
      var action = button.dataset.action;
      if (!action) return;
      e.preventDefault();
      var payload = {};
      if (action === "confirm-install") payload.profile = button.dataset.profile;
      if (action === "article") payload.id = button.dataset.id;
      send(action, payload);
    });

    $("topbar").addEventListener("mousedown", function (e) {
      if (!isPrimary(e) || e.target.closest("button, input, select")) return;
      send("window-drag");
    });

    $("settings-form").addEventListener("submit", function (e) {
      e.preventDefault();
      var typed = $("f-key").value;
      var key = { mode: "keep", value: "" };
      if (keyTouched) key = typed ? { mode: "set", value: typed } : { mode: "remove", value: "" };
      send("settings-save", {
        gameRoot: $("f-gameRoot").value.trim(),
        protonPath: $("f-protonPath").value.trim(),
        compatPrefix: $("f-compatPrefix").value.trim(),
        skinId: $("f-skinId").value,
        scale: Number($("f-scale").value),
        showWatch: $("f-showWatch").checked,
        startAutomatically: $("f-startAutomatically").checked,
        alertChime: $("f-alertChime").checked,
        speakAlerts: $("f-speakAlerts").checked,
        voiceId: voicePick !== null ? voicePick : state.settings.voiceId,
        key: key
      });
    });

    document.querySelectorAll(".tab").forEach(function (tab) {
      tab.addEventListener("click", function () { send("settings-tab", { tab: tab.dataset.tab }); });
    });
    var keyBox = $("f-key");
    keyBox.addEventListener("focus", function () { if (!keyTouched) keyBox.select(); });
    keyBox.addEventListener("input", function () {
      keyTouched = true;
      if (keyTimer) window.clearTimeout(keyTimer);
      var wait = keyBox.value ? KEY_TYPING_MS : 0;
      keyTimer = window.setTimeout(function () {
        keyTimer = null;
        send("key-typed", { key: keyBox.value });
      }, wait);
    });
    $("f-voiceId").addEventListener("change", function () {
      if ($("f-voiceId").value) voicePick = $("f-voiceId").value;
    });
    $("f-voiceId").addEventListener("mousedown", function (e) {
      if (!isPrimary(e)) return;
      e.preventDefault();
      if (voiceListOpen()) closeVoiceList(); else openVoiceList();
      $("f-voiceId").focus();
    });
    $("f-voiceId").addEventListener("keydown", function (e) {
      if (e.key === " " || e.key === "Enter" || (e.altKey && e.key === "ArrowDown")) {
        e.preventDefault();
        openVoiceList();
      }
    });
    $("settings-body").addEventListener("scroll", closeVoiceList);

    var marks = $("slide-marks");
    marks.addEventListener("click", function (e) {
      if (!isPrimary(e)) return;
      var mark = e.target.closest("button[data-slide]");
      if (mark) goTo(Number(mark.dataset.slide));
    });
    marks.addEventListener("keydown", function (e) {
      var count = heroArticles().length;
      if (!count) return;
      var next = null;
      if (e.key === "ArrowRight") next = carousel.index + 1;
      else if (e.key === "ArrowLeft") next = carousel.index - 1;
      else if (e.key === "Home") next = 0;
      else if (e.key === "End") next = count - 1;
      if (next === null) return;
      e.preventDefault();
      goTo(next);
      focusMark(carousel.index);
    });

    var feature = $("feature");
    feature.addEventListener("pointerenter", function () { carousel.hover = true; });
    feature.addEventListener("pointerleave", function () { carousel.hover = false; });
    feature.addEventListener("focusin", function () { carousel.focus = true; });
    feature.addEventListener("focusout", function (e) {
      carousel.focus = !!(e.relatedTarget && feature.contains(e.relatedTarget));
    });

    document.addEventListener("keydown", function (e) {
      if (!state) return;
      if (e.key === "Escape") {
        if (voiceListOpen()) {
          e.preventDefault();
          closeVoiceList();
        } else if (state.confirm) {
          e.preventDefault();
          send(state.confirm.kind === "install" ? "cancel-install" : "close-cancel");
        } else if (state.settingsOpen) {
          e.preventDefault();
          send("settings-cancel");
        }
        return;
      }
      if (e.key === "Tab") trapFocus(e);
    });

    window.addEventListener("resize", layout);
  }

  /* ----- browser preview: local, labelled, never touches the game ----- */

  function previewArticle(id) {
    return PREVIEW_ARTICLES.filter(function (article) { return article.id === id; })[0] || null;
  }

  function previewNotice(tone, text) {
    previewSerial += 1;
    return { tone: tone, text: text, serial: previewSerial };
  }

  function previewDispatch(action, payload) {
    testLog.push({ action: action, payload: payload });
    if (action === "window-drag" || action === "minimize") return;
    var next = clone(state);
    var url = EXTERNAL[action];
    // Same rule as the native bridge: an article request carries only an id.
    var article = action === "article" && Object.keys(payload).length === 1 ? previewArticle(payload.id) : null;
    if (action === "article" && !article) {
      next.notice = previewNotice("error", "That request was refused.");
    } else if (article) {
      url = article.url;
      externalLog.push(url);
      next.notice = previewNotice("info", "Preview: would open " + url + " in Chrome.");
    } else if (url) {
      externalLog.push(url);
      next.notice = previewNotice("info", "Preview: would open " + url + " in Chrome.");
    } else if (action === "screenshots") {
      next.notice = previewNotice("info", "Preview: screenshots open only in the desktop app.");
    } else if (action === "settings-open") {
      next.settingsOpen = true;
      next.settingsError = null;
    } else if (action === "settings-cancel") {
      next.settingsOpen = false;
      next.settingsError = null;
    } else if (action === "settings-tab") {
      next.settingsTab = payload.tab;
    } else if (action === "settings-save") {
      var skin = findSkin(next.skins, payload.skinId);
      if (!skin) {
        next.settingsError = { field: "skinId", message: "Skin: choose a valid skin from the list." };
      } else {
        next.skinId = skin.id;
        next.scale = Number(payload.scale);
        next.settings = Object.assign({}, next.settings, { skinId: skin.id, scale: next.scale });
        next.settingsOpen = false;
        next.settingsError = null;
        next.notice = previewNotice("info", "Preview: settings apply here but are not saved.");
      }
    } else if (action === "cancel-install" || action === "close-cancel") {
      next.confirm = null;
    } else if (action === "home") {
      next.notice = null;
    } else if (action === "close") {
      next.notice = previewNotice("info", "Preview: the window stays open.");
    } else if (["play", "beta", "confirm-install", "retry", "open-official", "close-confirm",
                "choose-game-root", "choose-proton", "choose-prefix"].indexOf(action) >= 0) {
      next.notice = { tone: "warn", text: PREVIEW_COPY };
    } else {
      return;
    }
    applyState(next);
  }

  /* ----- test hook: only with ?test=1; production ignores setFixture ----- */

  if (testMode) {
    window.__launcherTest = {
      mode: function () { return previewMode ? "preview" : "native"; },
      getState: function () { return state ? clone(state) : null; },
      dispatch: function (action, payload) { send(action, payload || {}); return true; },
      setFixture: function (fixture) {
        if (!previewMode || !state) return false;
        applyState(Object.assign(clone(state), clone(fixture)));
        return true;
      },
      carousel: function () {
        return { index: carousel.index, activeId: carousel.activeId, elapsed: carousel.elapsed, paused: carouselPaused() };
      },
      actionLog: function () { return testLog.slice(); },
      externalLog: function () { return externalLog.slice(); },
      renderCount: function () { return stateCount; }
    };
  }

  start();
})();
