(function () {
  function setupTabs(btnSelector, panePrefix) {
    const tabs = Array.from(document.querySelectorAll(btnSelector));
    if (!tabs.length) return;

    function activate(name) {
      tabs.forEach(t => t.classList.toggle("active", t.dataset.tab === name));

      // single mode: logs have id="tab-python" etc.
      // compare mode: panes have id="tab-cmp-python" etc.
      tabs.forEach(t => {
        const id = panePrefix + t.dataset.tab;
        const el = document.getElementById(id);
        if (el) el.classList.toggle("show", t.dataset.tab === name);
      });

      // legacy <pre class="log"> tabs (single analyze)
      const legacy = {
        python: document.getElementById("tab-python"),
        exif: document.getElementById("tab-exif"),
        family: document.getElementById("tab-family"),
      };
      if (legacy.python || legacy.exif || legacy.family) {
        Object.entries(legacy).forEach(([k, el]) => {
          if (!el) return;
          el.classList.toggle("show", k === name);
        });
      }
    }

    tabs.forEach(t => t.addEventListener("click", () => activate(t.dataset.tab)));
    activate(tabs[0].dataset.tab);
  }

  // Single analyze tabs (python/exif/family) use pre id="tab-*"
  setupTabs(".tab", "tab-");

  // Compare panes use id="tab-cmp-*"
  setupTabs(".tab", "tab-");
})();
