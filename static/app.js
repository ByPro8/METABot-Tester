(function () {
  function activateTab(bar, btn) {
    var name = btn.getAttribute("data-tab");
    if (!name) return;

    var buttons = bar.querySelectorAll('button.tab[data-tab]');
    for (var i = 0; i < buttons.length; i++) {
      var b = buttons[i];
      var isActive = (b === btn);

      if (isActive) b.classList.add("active");
      else b.classList.remove("active");

      var pane = document.getElementById("tab-" + b.getAttribute("data-tab"));
      if (pane) {
        if (isActive) pane.classList.add("show");
        else pane.classList.remove("show");
      }
    }
  }

  // Delegated tab clicks (works even if DOM is “repaired” by browser)
  document.addEventListener("click", function (e) {
    var btn = e.target && e.target.closest ? e.target.closest('button.tab[data-tab]') : null;
    if (!btn) return;

    var bar = btn.closest ? btn.closest(".tabs") : null;
    if (!bar) return;

    e.preventDefault();
    e.stopPropagation();
    activateTab(bar, btn);
  }, true);

  document.addEventListener("DOMContentLoaded", function () {
    // Initialize each tabs bar
    var bars = document.querySelectorAll(".tabs");
    for (var i = 0; i < bars.length; i++) {
      var bar = bars[i];
      var btn = bar.querySelector('button.tab.active[data-tab]') || bar.querySelector('button.tab[data-tab]');
      if (btn) activateTab(bar, btn);
    }

    // Auto-submit on file select
    var inputs = document.querySelectorAll('input[type="file"]');
    for (var j = 0; j < inputs.length; j++) {
      (function (inp) {
        inp.addEventListener("change", function () {
          try {
            if (!inp.files || inp.files.length === 0) return;
            var form = inp.closest ? inp.closest("form") : null;
            if (!form) return;

            // show overlay if present
            var ov = document.getElementById("processingOverlay");
            if (ov) ov.style.display = "flex";

            setTimeout(function () {
              if (form.requestSubmit) form.requestSubmit();
              else form.submit();
            }, 50);
          } catch (e) {}
        });
      })(inputs[j]);
    }
  });
})();
