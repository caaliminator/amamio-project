/* Shared front-end helpers for the Inventory & Sales Management System.
   Kept generic — page-specific behaviour lives inline in each template. */
(function () {
  "use strict";

  /* ---- Mobile sidebar toggle ---- */
  document.addEventListener("click", function (e) {
    var sidebar = document.getElementById("navLinks");
    if (!sidebar) return;
    if (e.target.closest("#navToggle")) {
      sidebar.classList.toggle("open");
      return;
    }
    /* Close the off-canvas sidebar when tapping the backdrop or a nav link. */
    if (e.target.closest("#sidebarOverlay") || e.target.closest(".side-link")) {
      sidebar.classList.remove("open");
    }
  });

  /* ---- Collapsible sidebar groups ----
     Every group starts open.  A group the user folds away stays folded (per
     browser), except the one holding the current page. */
  var GROUP_KEY = "amamio.navFolded";

  function readGroups() {
    try { return JSON.parse(window.localStorage.getItem(GROUP_KEY)) || {}; }
    catch (e) { return {}; }
  }
  function writeGroups(state) {
    try { window.localStorage.setItem(GROUP_KEY, JSON.stringify(state)); } catch (e) { /* optional */ }
  }
  function setGroup(group, open) {
    group.classList.toggle("is-open", open);
    var head = group.querySelector(".side-group-head");
    if (head) head.setAttribute("aria-expanded", open ? "true" : "false");
  }

  // app.js loads in <head> (so page scripts can use window.IMS straight
  // away); anything that touches the page waits for it to be parsed.
  function whenReady(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  whenReady(function initGroups() {
    var state = readGroups();
    document.querySelectorAll(".side-group").forEach(function (group) {
      var name = group.getAttribute("data-group");
      if (!group.classList.contains("has-active") && state[name]) setGroup(group, false);
    });
    // Enable the open/close animation only after the first paint, so the
    // page doesn't visibly animate groups open on every load.
    window.requestAnimationFrame(function () {
      document.documentElement.classList.add("nav-ready");
    });
  });

  document.addEventListener("click", function (e) {
    var head = e.target.closest(".side-group-head");
    if (!head) return;
    var group = head.closest(".side-group");
    var open = !group.classList.contains("is-open");
    setGroup(group, open);
    var state = readGroups();
    state[group.getAttribute("data-group")] = !open;
    writeGroups(state);
  });

  /* ---- Minimised sidebar (computers) ----
     "Collapse menu" folds the sidebar to a rail of icons; the choice is
     remembered per browser (base.html sets the class before paint).  In the
     rail each link shows its name on hover (the data-tip tooltip). */
  var MINI_KEY = "amamio.navMini";
  function syncMini() {
    var mini = document.documentElement.classList.contains("nav-mini");
    var btn = document.getElementById("navMini");
    if (btn) {
      btn.setAttribute("aria-pressed", mini ? "true" : "false");
      btn.querySelector(".side-text").textContent = mini ? "Expand menu" : "Collapse menu";
      btn.setAttribute("aria-label", mini ? "Expand menu" : "Collapse menu");
    }
    document.querySelectorAll(".sidebar .side-link").forEach(function (a) {
      var label = (a.querySelector(".side-text") || {}).textContent || "";
      if (mini) a.setAttribute("data-tip", label.trim());
      else a.removeAttribute("data-tip");
    });
  }
  whenReady(syncMini);
  document.addEventListener("click", function (e) {
    if (!e.target.closest("#navMini")) return;
    var mini = document.documentElement.classList.toggle("nav-mini");
    try { window.localStorage.setItem(MINI_KEY, mini ? "1" : "0"); } catch (err) { /* optional */ }
    syncMini();
  });

  /* ---- Confirm dialogs on [data-confirm] elements/forms ---- */
  document.addEventListener("click", function (e) {
    var el = e.target.closest("[data-confirm]");
    if (el && !confirm(el.getAttribute("data-confirm"))) {
      e.preventDefault();
      e.stopPropagation();
    }
  });
  document.addEventListener("submit", function (e) {
    var form = e.target;
    // The button pressed may carry its own question (e.g. "Remove photo").
    var btn = e.submitter && e.submitter.hasAttribute("data-confirm") ? e.submitter : null;
    var question = btn ? btn.getAttribute("data-confirm")
                       : form.matches("[data-confirm]") && form.getAttribute("data-confirm");
    if (question && !confirm(question)) {
      e.preventDefault();
    }
  }, true);

  /* ---- Modal open/close ----
     Trigger:  <button data-modal-open="modalId">
     Close:    any element with [data-modal-close] inside, or backdrop click. */
  function openModal(id) {
    var m = document.getElementById(id);
    if (m) m.classList.add("open");
  }
  function closeModal(el) {
    var backdrop = el.closest(".modal-backdrop");
    if (backdrop) backdrop.classList.remove("open");
  }
  document.addEventListener("click", function (e) {
    var opener = e.target.closest("[data-modal-open]");
    if (opener) {
      e.preventDefault();
      openModal(opener.getAttribute("data-modal-open"));
      return;
    }
    if (e.target.closest("[data-modal-close]")) {
      closeModal(e.target);
      return;
    }
    if (e.target.classList && e.target.classList.contains("modal-backdrop")) {
      e.target.classList.remove("open");
    }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      var open = document.querySelector(".modal-backdrop.open");
      if (open) open.classList.remove("open");
    }
  });

  /* ---- Small fetch helper returning parsed JSON ---- */
  function fetchJSON(url, options) {
    options = options || {};
    var opts = Object.assign({ headers: {} }, options);
    if (opts.body && typeof opts.body === "object" &&
        !(opts.body instanceof FormData)) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    return fetch(url, opts).then(function (r) {
      var ct = r.headers.get("content-type") || "";
      var parse = ct.indexOf("application/json") !== -1
        ? r.json() : r.text();
      return parse.then(function (data) {
        if (!r.ok) throw { status: r.status, data: data };
        return data;
      });
    });
  }

  /* ---- Table paging ----
     Every .table — and every report .doc-table marked data-paginate — with
     more than 10 rows shows 10 at a time, with "Show 10 / 25 / 50 / All
     entries" (remembered per browser), Previous / Next and "Page x of y".  Rows are only hidden, never removed, so forms inside a table
     still submit every row and printing shows the whole table.  Opt out
     with data-no-paginate. */
  var PAGE_SIZE = 10;

  function paginate(table) {
    if (table.hasAttribute("data-no-paginate") || table.dataset.paged) return;
    var body = table.tBodies[0];
    if (!body) return;
    var rows = Array.prototype.slice.call(body.rows);
    if (rows.length <= PAGE_SIZE) return;
    table.dataset.paged = "1";
    var page = 0, size = PAGE_SIZE;
    try { size = parseInt(localStorage.getItem("amamio.pageSize"), 10) || PAGE_SIZE; } catch (e) {}

    var bar = document.createElement("div");
    bar.className = "pager no-print";
    bar.innerHTML =
      '<span class="pager-left"><label class="pager-size">Show <select aria-label="Rows per page">' +
        '<option value="10">10</option><option value="25">25</option><option value="50">50</option><option value="100000">All</option>' +
        "</select> entries</label>" +
      '<span class="pager-info" aria-live="polite"></span></span>' +
      '<span class="pager-controls">' +
        '<button type="button" class="pager-btn" data-dir="-1" aria-label="Previous page">&lsaquo; Previous</button>' +
        '<span class="pager-page"></span>' +
        '<button type="button" class="pager-btn" data-dir="1" aria-label="Next page">Next &rsaquo;</button>' +
      "</span>";

    var anchor = table.closest(".table-wrap") || table;
    anchor.parentNode.insertBefore(bar, anchor.nextSibling);

    var info = bar.querySelector(".pager-info");
    var label = bar.querySelector(".pager-page");
    var prev = bar.querySelector('[data-dir="-1"]');
    var next = bar.querySelector('[data-dir="1"]');
    var sizeBox = bar.querySelector(".pager-size select");
    if (!sizeBox.querySelector('option[value="' + size + '"]')) size = PAGE_SIZE;
    sizeBox.value = String(size);
    sizeBox.addEventListener("change", function () {
      size = parseInt(sizeBox.value, 10) || PAGE_SIZE;
      try { localStorage.setItem("amamio.pageSize", String(size)); } catch (e) {}
      page = 0;
      render();
    });

    function render() {
      var pages = Math.max(1, Math.ceil(rows.length / size));
      page = Math.min(Math.max(page, 0), pages - 1);
      var start = page * size;
      var end = Math.min(start + size, rows.length);
      rows.forEach(function (row, i) {
        row.classList.toggle("is-paged-out", i < start || i >= end);
      });
      info.innerHTML = "Showing <b>" + (start + 1) + "&ndash;" + end +
        "</b> of <b>" + rows.length + "</b>";
      label.textContent = "Page " + (page + 1) + " of " + pages;
      prev.disabled = page === 0;
      next.disabled = page >= pages - 1;
    }

    function go(delta) {
      page += delta;
      render();
      // Keep the top of the table in view after changing page.
      var top = anchor.getBoundingClientRect().top;
      if (top < 80) window.scrollBy({ top: top - 90, behavior: "smooth" });
    }

    prev.addEventListener("click", function () { go(-1); });
    next.addEventListener("click", function () { go(1); });
    render();
  }

  function paginateAll(root) {
    (root || document).querySelectorAll("table.table, table.doc-table[data-paginate]").forEach(paginate);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { paginateAll(); });
  } else {
    paginateAll();
  }

  /* ---- Wizard: one step on screen at a time ----
     <form data-wizard data-submit-label="Save">
       <section class="wz-step" data-title="…" data-hint="…"> fields </section>
       <section class="wz-step" data-title="Review" data-review></section>
     </form>
     - Next checks only the fields of the current step.
     - data-when="name=value" (or name!=value, or name=a|b) skips a step
       unless the condition holds.
     - A [data-review] step lists everything entered, grouped per step.
     - Pages may listen for "wz:enter" (detail.index) on a step, and
       "wz:validate" (cancelable; set detail.message) to add their own rules.
     - data-wizard-start="n" opens at step n (e.g. after a server error). */
  function fieldValue(form, name) {
    var els = form.querySelectorAll('[name="' + name + '"]');
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if ((el.type === "radio" || el.type === "checkbox")) {
        if (el.checked) return el.value;
      } else {
        return el.value;
      }
    }
    return "";
  }

  function stepApplies(form, step) {
    var rule = step.getAttribute("data-when");
    if (!rule) return true;
    var neg = rule.indexOf("!=") !== -1;
    var parts = rule.split(neg ? "!=" : "=");
    var value = fieldValue(form, parts[0].trim());
    var options = (parts[1] || "").split("|").map(function (s) { return s.trim(); });
    var hit = options.indexOf(value) !== -1;
    return neg ? !hit : hit;
  }

  function labelFor(el) {
    if (el.getAttribute("data-review-label")) return el.getAttribute("data-review-label");
    var group = el.closest("[data-review-label]");
    if (group) return group.getAttribute("data-review-label");
    if (el.id) {
      var lab = el.form && el.form.querySelector('label[for="' + el.id + '"]');
      if (lab) return lab.textContent.replace("*", "").trim();
    }
    var row = el.closest(".form-row");
    var fl = row && row.querySelector(".form-label");
    return fl ? fl.textContent.replace("*", "").trim() : (el.name || "");
  }

  function displayValue(el) {
    if (el.tagName === "SELECT") {
      var opt = el.options[el.selectedIndex];
      return opt && el.value !== "" ? opt.textContent.trim() : "";
    }
    if (el.type === "radio") {
      // A choice card shows only its title; a chip shows its label.
      var card = el.closest("label");
      var t = card && (card.querySelector(".choice-title") || card.querySelector("span"));
      return t ? t.textContent.trim() : el.value;
    }
    if (el.type === "checkbox") return el.checked ? "Yes" : "No";
    if (el.type === "date") {
      // As the |longdate filter writes it: "Nov. 8, 2026".
      var dm = /^(\d{4})-(\d{2})-(\d{2})$/.exec(el.value);
      var MON = ["Jan.", "Feb.", "Mar.", "Apr.", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec."];
      return dm ? MON[+dm[2] - 1] + " " + (+dm[3]) + ", " + dm[1] : el.value;
    }
    if (el.type === "file") return !el.files || !el.files.length ? ""
      : el.files.length === 1 ? el.files[0].name : el.files.length + " files";
    var unit = el.closest(".big-field") && el.closest(".big-field").querySelector(".unit");
    return el.value + (unit && el.value !== "" ? " " + unit.textContent.trim() : "");
  }

  function esc(value) {
    var d = document.createElement("div");
    d.textContent = value === null || value === undefined ? "" : String(value);
    return d.innerHTML;
  }

  function initWizard(form) {
    if (form.dataset.wzReady) return;
    form.dataset.wzReady = "1";
    var steps = Array.prototype.slice.call(form.querySelectorAll(".wz-step"));
    if (!steps.length) return;
    // The wizard checks each visible step itself; native validation would
    // trip over required fields in steps that were skipped.
    form.noValidate = true;
    var current = 0;

    // Header: progress bar, "Step n of m", title, hint.
    var head = document.createElement("div");
    head.className = "wz-head";
    head.innerHTML = '<div class="wz-progress" role="presentation"></div>' +
      '<div class="wz-count" aria-live="polite"></div>' +
      '<h2 class="wz-title"></h2><p class="wz-hint"></p>';
    form.insertBefore(head, steps[0]);
    var bar = head.querySelector(".wz-progress");
    steps.forEach(function (s, i) {
      var b = document.createElement("button");
      b.type = "button";
      b.setAttribute("aria-label", "Go to step: " + (s.getAttribute("data-title") || i + 1));
      b.addEventListener("click", function () { if (i < current) go(i); });
      bar.appendChild(b);
      // Each step's own error line.
      var err = document.createElement("div");
      err.className = "wz-error";
      err.setAttribute("role", "alert");
      s.appendChild(err);
    });

    // Footer: Back / Next / Save.
    var nav = document.createElement("div");
    nav.className = "wz-nav";
    var cancelHref = form.getAttribute("data-cancel");
    nav.innerHTML =
      '<button type="button" class="btn btn-ghost" data-wz-back>Back</button>' +
      (cancelHref ? '<a class="btn btn-ghost" data-wz-cancel href="' + esc(cancelHref) + '">Cancel</a>' : "") +
      '<span class="wz-spacer"></span>' +
      '<button type="button" class="btn btn-primary" data-wz-next>Next</button>' +
      '<button type="submit" class="btn btn-primary" data-wz-submit>' +
        esc(form.getAttribute("data-submit-label") || "Save") + "</button>";
    form.appendChild(nav);
    var backBtn = nav.querySelector("[data-wz-back]");
    var cancelBtn = nav.querySelector("[data-wz-cancel]");
    var nextBtn = nav.querySelector("[data-wz-next]");
    var submitBtn = nav.querySelector("[data-wz-submit]");

    function visible() {
      return steps.map(function (s, i) { return stepApplies(form, s) ? i : -1; })
                  .filter(function (i) { return i !== -1; });
    }

    function fieldsOf(step) {
      return Array.prototype.slice.call(
        step.querySelectorAll("input, select, textarea")).filter(function (el) {
          return !el.disabled && el.type !== "hidden" && el.name && !el.closest("[data-review-skip]");
        });
    }

    function showError(step, message) {
      var err = step.querySelector(".wz-error");
      err.textContent = message || "";
      err.classList.toggle("is-shown", !!message);
    }

    function validate(step) {
      showError(step, "");
      var fields = fieldsOf(step);
      for (var i = 0; i < fields.length; i++) {
        if (!fields[i].checkValidity()) {
          fields[i].reportValidity();
          return false;
        }
      }
      var ev = new CustomEvent("wz:validate", { cancelable: true, detail: {} });
      if (!step.dispatchEvent(ev)) {
        showError(step, ev.detail.message || "Please check this step.");
        return false;
      }
      return true;
    }

    function buildReview(step) {
      var html = "";
      visible().forEach(function (i) {
        var s = steps[i];
        if (s === step || s.hasAttribute("data-review") || s.hasAttribute("data-no-review")) return;
        var rows = [], seen = {};
        // A step can describe itself (e.g. a list of line items) by setting
        // step.reviewHtml = function () { return "<dt>…</dt><dd>…</dd>"; }
        if (typeof s.reviewHtml === "function") {
          rows.push(s.reviewHtml());
        } else {
          fieldsOf(s).forEach(function (el) {
            if (el.type === "radio" && !el.checked) return;
            if (seen[el.name] && el.type === "radio") return;
            var v = displayValue(el);
            if (v === "" || v === null) return;
            seen[el.name] = true;
            rows.push("<dt>" + esc(labelFor(el)) + "</dt><dd>" + esc(v) + "</dd>");
          });
        }
        if (!rows.join("")) return;
        html += '<div><div class="review-group-title">' + esc(s.getAttribute("data-title") || "") +
          ' <a href="#" class="review-edit" data-wz-goto="' + i + '">Change</a></div>' +
          '<dl class="review-list">' + rows.join("") + "</dl></div>";
      });
      var target = step.querySelector(".review");
      if (!target) {
        target = document.createElement("div");
        target.className = "review";
        var body = step.querySelector(".wz-body") || step;
        body.insertBefore(target, body.firstChild);
      }
      target.innerHTML = html || '<p class="muted">Nothing entered yet.</p>';
    }

    function render() {
      var vis = visible();
      var pos = vis.indexOf(current);
      steps.forEach(function (s, i) {
        s.classList.toggle("is-current", i === current);
        var b = bar.children[i];
        b.className = vis.indexOf(i) === -1 ? "is-skipped"
          : i === current ? "is-current" : (vis.indexOf(i) < pos ? "is-done" : "");
      });
      var step = steps[current];
      head.querySelector(".wz-count").textContent = "Step " + (pos + 1) + " of " + vis.length;
      head.querySelector(".wz-title").textContent = step.getAttribute("data-title") || "";
      var hint = step.getAttribute("data-hint") || "";
      var hintEl = head.querySelector(".wz-hint");
      hintEl.textContent = hint;
      hintEl.style.display = hint ? "" : "none";
      var last = pos === vis.length - 1;
      backBtn.style.display = pos === 0 ? "none" : "";
      if (cancelBtn) cancelBtn.style.display = pos === 0 ? "" : "none";
      nextBtn.style.display = last ? "none" : "";
      submitBtn.style.display = last ? "" : "none";
      if (step.hasAttribute("data-review")) buildReview(step);
      step.dispatchEvent(new CustomEvent("wz:enter", { detail: { index: current } }));
      var first = step.querySelector("input:not([type=hidden]):not([type=radio]):not([type=checkbox]), select, textarea");
      if (first && !step.hasAttribute("data-review") && window.matchMedia("(min-width: 900px)").matches) {
        try { first.focus({ preventScroll: true }); } catch (e) { first.focus(); }
      }
    }

    function go(index) {
      current = index;
      render();
      var top = form.getBoundingClientRect().top + window.pageYOffset - 90;
      if (window.pageYOffset > top) window.scrollTo({ top: top, behavior: "smooth" });
    }

    function next() {
      if (!validate(steps[current])) return;
      var vis = visible();
      var pos = vis.indexOf(current);
      if (pos < vis.length - 1) go(vis[pos + 1]);
    }

    function back() {
      var vis = visible();
      var pos = vis.indexOf(current);
      if (pos > 0) go(vis[pos - 1]);
    }

    nextBtn.addEventListener("click", next);
    backBtn.addEventListener("click", back);
    form.addEventListener("click", function (e) {
      var to = e.target.closest("[data-wz-goto]");
      if (to) { e.preventDefault(); go(parseInt(to.getAttribute("data-wz-goto"), 10)); }
      var nx = e.target.closest("[data-wz-next-inline]");
      if (nx) { e.preventDefault(); next(); }
    });
    // A choice card marked data-autonext moves on as soon as it is picked.
    form.addEventListener("change", function (e) {
      if (e.target.matches("[data-autonext]") && e.target.checked) {
        window.setTimeout(next, 180);
      }
    });
    // Enter moves to the next step instead of submitting half a form.
    form.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" || e.target.tagName === "TEXTAREA" || e.target.type === "submit") return;
      var vis = visible();
      if (vis.indexOf(current) < vis.length - 1) { e.preventDefault(); next(); }
    });
    form.addEventListener("submit", function (e) {
      // Validate every visible step before saving; jump to the first problem.
      var vis = visible();
      for (var i = 0; i < vis.length; i++) {
        var s = steps[vis[i]];
        if (s.hasAttribute("data-review")) continue;
        var ok = fieldsOf(s).every(function (el) { return el.checkValidity(); });
        if (!ok) { e.preventDefault(); go(vis[i]); validate(s); return; }
      }
      // Fields in skipped steps must not be submitted.
      steps.forEach(function (s, i) {
        if (vis.indexOf(i) === -1) {
          fieldsOf(s).forEach(function (el) { el.disabled = true; });
        }
      });
      submitBtn.disabled = true;
      submitBtn.textContent = "Saving…";
    });

    var start = parseInt(form.getAttribute("data-wizard-start") || "0", 10) || 0;
    current = Math.min(Math.max(start, 0), steps.length - 1);
    render();
    form.__wizard = { go: go, next: next, back: back, render: render };
  }

  function initWizards(root) {
    (root || document).querySelectorAll("form[data-wizard]").forEach(initWizard);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { initWizards(); });
  } else {
    initWizards();
  }

  /* ---- Live tally: <div data-tally data-total="12" data-fields="a,b,c"> ----
     Shows how the counted parts add up against the declared total.  With
     data-no-over the parts can't add up to more than the total: the tally
     and the fields turn red, a one-click fix takes the extra off the first
     field (e.g. Usable), and the wizard step won't go on until it fits.
     Short of the total stays allowed (amber). */
  function initTallies() {
    document.querySelectorAll("[data-tally]").forEach(function (box) {
      var form = box.closest("form");
      var names = (box.getAttribute("data-fields") || "").split(",").map(function (n) { return n.trim(); });
      var total = parseFloat(box.getAttribute("data-total")) || 0;
      var unit = box.getAttribute("data-unit") || "";
      var strict = box.hasAttribute("data-no-over");
      var fields = names.map(function (n) { return form.querySelector('[name="' + n + '"]'); });
      var labels = names.map(function (n, i) {
        var lab = fields[i] && form.querySelector('label[for="' + fields[i].id + '"]');
        return lab ? lab.childNodes[0].textContent.trim() : n;
      });
      var round = function (v) { return Math.round(v * 1000) / 1000; };
      var over = 0;
      function update() {
        var sum = 0;
        fields.forEach(function (el) { sum += el ? (parseFloat(el.value) || 0) : 0; });
        sum = round(sum);
        var diff = round(sum - total);
        over = strict && diff > 0 ? diff : 0;
        box.classList.toggle("is-even", diff === 0);
        box.classList.toggle("is-off", diff !== 0 && !over);
        box.classList.toggle("is-over", !!over);
        fields.forEach(function (el) {
          if (el) el.classList.toggle("is-over", !!over && (parseFloat(el.value) || 0) > 0);
        });
        var note;
        if (diff === 0) note = "Matches the label";
        else if (over) {
          var first = fields[0] ? parseFloat(fields[0].value) || 0 : 0;
          note = "<b>" + diff + " " + esc(unit) + "</b> more than the " + total + " " + esc(unit) + " declared" +
            (first >= diff ? ' <button type="button" class="tally-fix">Take ' + diff + " " + esc(unit) +
              " off " + esc(labels[0]) + "</button>" : "");
        } else note = diff > 0 ? "<b>" + diff + "</b> more than declared" : "<b>" + Math.abs(diff) + " " + esc(unit) + "</b> short of declared";
        box.innerHTML = "<span>Counted <b>" + sum + " " + esc(unit) + "</b> of <b>" +
          total + " " + esc(unit) + "</b> declared</span><span>" + note + "</span>";
      }
      box.addEventListener("click", function (e) {
        if (!e.target.closest(".tally-fix") || !over || !fields[0]) return;
        fields[0].value = round((parseFloat(fields[0].value) || 0) - over);
        fields[0].dispatchEvent(new Event("input", { bubbles: true }));
      });
      var step = box.closest(".wz-step");
      if (strict && step) {
        step.addEventListener("wz:validate", function (e) {
          update();
          if (!over) return;
          e.preventDefault();
          e.detail.message = labels.join(", ").replace(/, ([^,]*)$/, " and $1") + " add up to " +
            round(total + over) + " " + unit + " — more than the " + total + " " + unit +
            " declared. Lower one of them so they fit.";
        });
      }
      form.addEventListener("input", update);
      update();
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initTallies);
  } else {
    initTallies();
  }

  /* ---- Appearance: light / dark / match my device ----
     Switches at once, then saves the choice to the user's account so it
     follows them to any device.  Without JavaScript the form still posts. */
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (form.id !== "themeForm") return;
    var btn = e.submitter;
    if (!btn || !btn.value) return;
    e.preventDefault();
    var pref = btn.value, root = document.documentElement;
    root.setAttribute("data-theme-pref", pref);
    if (pref === "system") {
      var light = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
      root.setAttribute("data-theme", light ? "light" : "dark");
    } else {
      root.setAttribute("data-theme", pref);
    }
    form.querySelectorAll(".theme-option").forEach(function (b) {
      var on = b === btn;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
    var sw = document.getElementById("themeSwitch");
    if (sw) sw.open = false;
    var body = new FormData();
    body.append("theme", pref);
    fetch(form.action, { method: "POST", body: body,
                         headers: { "X-Requested-With": "fetch" } }).catch(function () {});
  });
  // Close the appearance menu on an outside click or Escape.
  document.addEventListener("click", function (e) {
    var sw = document.getElementById("themeSwitch");
    if (sw && sw.open && !sw.contains(e.target)) sw.open = false;
  });
  document.addEventListener("keydown", function (e) {
    var sw = document.getElementById("themeSwitch");
    if (e.key === "Escape" && sw && sw.open) sw.open = false;
  });

  /* ---- "About this page" note: remember if it was folded, per page ---- */
  var NOTE_KEY = "amamio.notesFolded";
  function foldedNotes() {
    try { return JSON.parse(window.localStorage.getItem(NOTE_KEY)) || {}; }
    catch (e) { return {}; }
  }
  whenReady(function () {
    var note = document.querySelector(".page-note[data-note-key]");
    if (!note) return;
    var key = note.getAttribute("data-note-key");
    if (foldedNotes()[key]) note.open = false;
    note.addEventListener("toggle", function () {
      var state = foldedNotes();
      if (note.open) delete state[key]; else state[key] = true;
      try { window.localStorage.setItem(NOTE_KEY, JSON.stringify(state)); } catch (e) { /* optional */ }
    });
  });

  /* ---- Hover tooltip for chart marks: any element with data-tip ---- */
  (function () {
    var tip = null;
    function show(el, e) {
      if (!tip) {
        tip = document.createElement("div");
        tip.className = "viz-tip";
        tip.setAttribute("role", "tooltip");
        document.body.appendChild(tip);
      }
      tip.textContent = el.getAttribute("data-tip");
      tip.style.display = "block";
      move(e);
    }
    function move(e) {
      if (!tip || tip.style.display !== "block") return;
      var x = e.clientX + 14, y = e.clientY + 14;
      var w = tip.offsetWidth, h = tip.offsetHeight;
      if (x + w > window.innerWidth - 8) x = e.clientX - w - 14;
      if (y + h > window.innerHeight - 8) y = e.clientY - h - 14;
      tip.style.left = x + "px";
      tip.style.top = y + "px";
    }
    document.addEventListener("mouseover", function (e) {
      var el = e.target.closest && e.target.closest("[data-tip]");
      if (el) show(el, e);
      else if (tip) tip.style.display = "none";
    });
    document.addEventListener("mousemove", move);
    document.addEventListener("scroll", function () { if (tip) tip.style.display = "none"; }, true);
  })();

  /* ---- Searchable dropdown: type to filter a list ----
       <div class="combo" data-combo="optionsId" [data-combo-new="'%s' …"]>
         <input type="text" class="combo-input" name="…">
         [<input type="hidden" class="combo-value" name="…" data-other="other">]
       </div>
     Options: <script type="application/json" id="optionsId"> holding
     [{"v": value, "l": label, "g": group, "s": side note, "d": {…}}];
     a page script can give one box its own list by setting
     combo.__options (e.g. only the suppliers of the item on that line).
     Every typed word must appear in the label (or in the group too, with
     data-combo-search-group on the .combo).  The hidden value is the
     option's value when the text matches a label exactly, else data-other
     (typed text allowed) or "".  Choosing an option — or typing one exactly —
     fires "combo:pick" on the .combo with the option as detail.
     The list opens when the box is clicked / tapped (the whole list, the
     current choice marked), when something is typed (only the matches), or
     on ↓ — never just because the box got the focus with Tab. */
  (function () {
    var cache = {};
    function options(combo) {
      // A page script may narrow one box's list: combo.__options = [...].
      if (combo.__options) return combo.__options;
      var id = combo.getAttribute("data-combo");
      if (!cache[id]) {
        var el = document.getElementById(id);
        try { cache[id] = el ? JSON.parse(el.textContent) : []; } catch (e) { cache[id] = []; }
      }
      return cache[id];
    }
    function norm(s) { return String(s || "").toLowerCase().replace(/\s+/g, " ").trim(); }
    function inputOf(combo) { return combo.querySelector(".combo-input"); }
    function listOf(combo) {
      var list = combo.querySelector(".combo-list");
      if (!list) {
        list = document.createElement("div");
        list.className = "combo-list";
        list.setAttribute("role", "listbox");
        list.hidden = true;
        combo.appendChild(list);
      }
      return list;
    }
    // Bold the typed words inside the label (escaped piece by piece).
    function highlight(label, words) {
      var low = label.toLowerCase(), marks = [], out = "", pos = 0;
      words.forEach(function (w) {
        var i = w ? low.indexOf(w) : -1;
        if (i > -1) marks.push([i, i + w.length]);
      });
      marks.sort(function (a, b) { return a[0] - b[0]; });
      marks.forEach(function (m) {
        var from = Math.max(m[0], pos);
        if (m[1] <= from) return;
        out += esc(label.slice(pos, from)) + "<mark>" + esc(label.slice(from, m[1])) + "</mark>";
        pos = m[1];
      });
      return out + esc(label.slice(pos));
    }
    // ``browse``: opened by a click — show the whole list (unless the box
    // holds part of a name, which still narrows it) with the current choice
    // marked, rather than only the option already in the box.
    function open(combo, browse) {
      var input = inputOf(combo), typed = input.value.trim();
      var all = options(combo), current = norm(typed);
      var chosen = browse && all.some(function (o) { return norm(o.l) === current; });
      var q = chosen ? "" : current, words = q ? q.split(" ") : [];
      var limit = browse ? 400 : 80;
      var html = "", group = null, shown = 0, more = 0, exact = false, activeAt = -1;
      var byGroup = combo.hasAttribute("data-combo-search-group");
      all.forEach(function (o, i) {
        var l = norm(o.l), hay = byGroup && o.g ? l + " " + norm(o.g) : l;
        if (!words.every(function (w) { return hay.indexOf(w) > -1; })) return;
        if (shown >= limit) { more++; return; }
        if (l === q) exact = true;
        if (o.g && o.g !== group) {
          group = o.g;
          html += '<div class="combo-group">' + esc(group) + "</div>";
        }
        var isCurrent = chosen && l === current;
        if (isCurrent) activeAt = shown;
        html += '<div class="combo-opt' + (isCurrent ? " is-active" : "") + '" role="option" data-i="' + i + '"' +
          (isCurrent ? ' aria-selected="true"' : "") + "><span>" +
          highlight(o.l, words) + "</span>" + (o.s ? "<small>" + esc(o.s) + "</small>" : "") + "</div>";
        shown++;
      });
      var note = combo.getAttribute("data-combo-new");
      if (q && !exact && note) html += '<div class="combo-note">' + esc(note.replace("%s", typed)) + "</div>";
      else if (!shown) html += '<div class="combo-note">No matches</div>';
      if (more) html += '<div class="combo-note">' + more + " more — type to narrow the list</div>";
      var list = listOf(combo);
      list.innerHTML = html;
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      combo.__active = activeAt;
      list.scrollTop = 0;
      if (activeAt > -1) list.querySelectorAll(".combo-opt")[activeAt].scrollIntoView({ block: "nearest" });
    }
    function close(combo) {
      listOf(combo).hidden = true;
      inputOf(combo).setAttribute("aria-expanded", "false");
    }
    function sync(combo) {
      var q = norm(inputOf(combo).value), match = null;
      options(combo).some(function (o) {
        if (q && norm(o.l) === q) { match = o; return true; }
        return false;
      });
      var hidden = combo.querySelector(".combo-value");
      if (hidden) hidden.value = match ? match.v : (q ? hidden.getAttribute("data-other") || "" : "");
      return match;
    }
    function announce(combo, o) {
      if (combo.__picked === o.v) return;
      combo.__picked = o.v;
      combo.dispatchEvent(new CustomEvent("combo:pick", { bubbles: true, detail: o }));
    }
    function pick(combo, o) {
      inputOf(combo).value = o.l;
      sync(combo);
      close(combo);
      announce(combo, o);
      inputOf(combo).dispatchEvent(new Event("change", { bubbles: true }));
    }
    function move(combo, step) {
      var opts = listOf(combo).querySelectorAll(".combo-opt");
      if (!opts.length) return;
      combo.__active = (combo.__active + step + opts.length) % opts.length;
      opts.forEach(function (el, i) { el.classList.toggle("is-active", i === combo.__active); });
      opts[combo.__active].scrollIntoView({ block: "nearest" });
    }
    function comboOf(el) { return el && el.closest && el.closest(".combo"); }

    document.addEventListener("input", function (e) {
      if (!e.target.classList.contains("combo-input")) return;
      var combo = comboOf(e.target);
      sync(combo);
      if (e.target.value.trim()) open(combo);
      else close(combo);
    });
    // A click / tap on the box opens the list; a second click closes it.
    document.addEventListener("click", function (e) {
      if (!e.target.classList || !e.target.classList.contains("combo-input")) return;
      if (e.target.disabled || e.target.readOnly) return;
      var combo = comboOf(e.target);
      if (listOf(combo).hidden) open(combo, true);
      else close(combo);
    });
    document.addEventListener("focusout", function (e) {
      if (!e.target.classList.contains("combo-input")) return;
      var combo = comboOf(e.target);
      close(combo);
      var match = sync(combo);
      if (match) announce(combo, match);
      else combo.__picked = null;
    });
    document.addEventListener("mousedown", function (e) {
      var opt = e.target.closest && e.target.closest(".combo-opt");
      if (!opt) return;
      e.preventDefault();            // keep the focus in the box
      var combo = comboOf(opt);
      pick(combo, options(combo)[+opt.getAttribute("data-i")]);
    });
    // Capture phase, so Enter picks an option before a wizard moves on.
    document.addEventListener("keydown", function (e) {
      if (!e.target.classList || !e.target.classList.contains("combo-input")) return;
      var combo = comboOf(e.target), list = listOf(combo);
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (list.hidden) open(combo);
        move(combo, e.key === "ArrowDown" ? 1 : -1);
      } else if (e.key === "Enter" && !list.hidden && combo.__active > -1) {
        e.preventDefault();
        e.stopPropagation();
        var opt = list.querySelectorAll(".combo-opt")[combo.__active];
        pick(combo, options(combo)[+opt.getAttribute("data-i")]);
      } else if (e.key === "Escape" && !list.hidden) {
        e.stopPropagation();
        close(combo);
      }
    }, true);
  })();

  /* ---- Calendar for every <input type="date"> ----
     The browser's own date popup can't be styled, so date fields open this
     calendar instead (themed, light and dark).  The field stays a real date
     field: its value is still YYYY-MM-DD, typing a date still works, and
     required / min / max are respected (days outside them can't be picked).
     Picking a day sets the value and fires "input" and "change".
     Click the field (or press Alt+↓ / Space in it) to open; ‹ › change the
     month; the title switches to months, then years.  In the calendar:
     arrows move a day / week, Page Up / Down a month, Enter picks, Esc
     closes.  Phones and tablets keep their own (touch-friendly) picker. */
  (function () {
    var touch = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;
    if (touch) return;
    var gecko = "MozAppearance" in document.documentElement.style;
    var MONTHS = ["January", "February", "March", "April", "May", "June", "July",
                  "August", "September", "October", "November", "December"];
    var MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    var DAYS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];
    // As the |longdate filter writes them: "Sept. 21, 2026", "May 4, 2026".
    var LONG_MON = ["Jan.", "Feb.", "Mar.", "Apr.", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec."];
    var DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
    var pop = null, field = null, view = null, mode = "days", cursor = null;

    function pad(n) { return (n < 10 ? "0" : "") + n; }
    function iso(y, m, d) { return y + "-" + pad(m + 1) + "-" + pad(d); }
    function parse(v) {
      var p = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v || "");
      return p ? { y: +p[1], m: +p[2] - 1, d: +p[3] } : null;
    }
    function today() { var t = new Date(); return iso(t.getFullYear(), t.getMonth(), t.getDate()); }
    function addDays(v, n) {
      var p = parse(v), t = new Date(p.y, p.m, p.d + n);
      return iso(t.getFullYear(), t.getMonth(), t.getDate());
    }
    function addMonths(v, n) {
      var p = parse(v), last = new Date(p.y, p.m + n + 1, 0).getDate();
      var t = new Date(p.y, p.m + n, Math.min(p.d, last));
      return iso(t.getFullYear(), t.getMonth(), t.getDate());
    }
    function allowed(v) {
      var lo = field.getAttribute("min"), hi = field.getAttribute("max");
      return !(lo && v < lo) && !(hi && v > hi);
    }
    function clampToRange(v) {
      var lo = field.getAttribute("min"), hi = field.getAttribute("max");
      if (lo && v < lo) return lo;
      if (hi && v > hi) return hi;
      return v;
    }
    function build() {
      pop = document.createElement("div");
      pop.className = "cal-pop";
      pop.setAttribute("role", "dialog");
      pop.setAttribute("aria-label", "Choose a date");
      pop.hidden = true;
      document.body.appendChild(pop);
      pop.addEventListener("mousedown", function (e) { e.preventDefault(); });   // keep focus where it is
      pop.addEventListener("click", onClick);
      pop.addEventListener("keydown", onKey);
    }
    function btn(cls, label, attrs, html) {
      return '<button type="button" class="' + cls + '" aria-label="' + label + '"' + (attrs || "") + ">" + html + "</button>";
    }
    var CHEV_L = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M15 18l-6-6 6-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    var CHEV_R = '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M9 18l6-6-6-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    var CARET = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

    function render() {
      var value = field.value, now = today(), html = "";
      var title = mode === "days" ? MONTHS[view.m] + " " + view.y
                : mode === "months" ? String(view.y)
                : (view.y - view.y % 12) + " – " + (view.y - view.y % 12 + 11);
      var sel = parse(value);
      html += '<div class="cal-head">' +
        btn("cal-title", mode === "years" ? "Years" : "Choose the " + (mode === "days" ? "month" : "year"),
            ' data-act="zoom"' + (mode === "years" ? " disabled" : ""), "<span>" + title + "</span>" + (mode === "years" ? "" : CARET)) +
        '<div class="cal-nav">' +
        btn("cal-arrow", mode === "days" ? "Previous month" : mode === "months" ? "Previous year" : "Previous years", ' data-act="prev"', CHEV_L) +
        btn("cal-arrow", mode === "days" ? "Next month" : mode === "months" ? "Next year" : "Next years", ' data-act="next"', CHEV_R) +
        "</div></div>";
      if (sel) {
        var sd = new Date(sel.y, sel.m, sel.d);
        html += '<div class="cal-picked">' + DAY_NAMES[sd.getDay()] + ", " + LONG_MON[sel.m] + " " + sel.d + ", " + sel.y + "</div>";
      }
      if (mode === "days") {
        html += '<div class="cal-grid cal-week" aria-hidden="true">' +
          DAYS.map(function (d, i) { return '<span class="' + (i === 0 || i === 6 ? "is-weekend" : "") + '">' + d + "</span>"; }).join("") + "</div>";
        html += '<div class="cal-grid cal-days" role="grid" aria-label="' + MONTHS[view.m] + " " + view.y + '">';
        var first = new Date(view.y, view.m, 1).getDay();
        for (var i = 0; i < 42; i++) {
          var t = new Date(view.y, view.m, 1 - first + i);
          var v = iso(t.getFullYear(), t.getMonth(), t.getDate());
          var cls = ["cal-day"];
          if (t.getMonth() !== view.m) cls.push("is-other");
          if (v === now) cls.push("is-today");
          if (v === value) cls.push("is-selected");
          if (v === cursor) cls.push("is-cursor");
          var ok = allowed(v);
          html += '<button type="button" class="' + cls.join(" ") + '" data-date="' + v + '"' +
            ' tabindex="' + (v === cursor ? "0" : "-1") + '"' +
            ' aria-label="' + DAY_NAMES[t.getDay()] + ", " + MONTHS[t.getMonth()] + " " + t.getDate() + ", " + t.getFullYear() + '"' +
            (v === value ? ' aria-selected="true"' : "") + (v === now ? ' aria-current="date"' : "") +
            (ok ? "" : " disabled") + ">" + t.getDate() + "</button>";
        }
        html += "</div>";
      } else if (mode === "months") {
        html += '<div class="cal-grid cal-big">';
        var thisMonth = now.slice(0, 7), selMonth = (value || "").slice(0, 7);
        MON.forEach(function (name, m) {
          var key = view.y + "-" + pad(m + 1);
          var lo = (field.getAttribute("min") || "").slice(0, 7), hi = (field.getAttribute("max") || "").slice(0, 7);
          var ok = !(lo && key < lo) && !(hi && key > hi);
          html += '<button type="button" class="cal-cell' + (key === thisMonth ? " is-today" : "") + (key === selMonth ? " is-selected" : "") +
            '" data-month="' + m + '"' + (ok ? "" : " disabled") + ">" + name + "</button>";
        });
        html += "</div>";
      } else {
        html += '<div class="cal-grid cal-big">';
        var start = view.y - view.y % 12, thisYear = +now.slice(0, 4), selYear = sel ? sel.y : null;
        var loY = +(field.getAttribute("min") || "0").slice(0, 4), hiY = +(field.getAttribute("max") || "9999").slice(0, 4);
        for (var y = start; y < start + 12; y++) {
          html += '<button type="button" class="cal-cell' + (y === thisYear ? " is-today" : "") + (y === selYear ? " is-selected" : "") +
            '" data-year="' + y + '"' + (y < loY || y > hiY ? " disabled" : "") + ">" + y + "</button>";
        }
        html += "</div>";
      }
      var canToday = allowed(now);
      html += '<div class="cal-foot">' +
        (field.required ? "<span></span>" : '<button type="button" class="cal-link" data-act="clear">Clear</button>') +
        '<button type="button" class="cal-link is-strong" data-act="today"' + (canToday ? "" : " disabled") + ">Today</button></div>";
      pop.innerHTML = html;
    }
    function place() {
      if (!field || pop.hidden) return;
      var r = field.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight;
      var vw = document.documentElement.clientWidth, vh = window.innerHeight;
      var left = Math.min(Math.max(8, r.left), vw - w - 8);
      var top = r.bottom + 6;
      if (top + h > vh - 8 && r.top - h - 6 > 8) top = r.top - h - 6;      // no room below: open above
      pop.style.left = (left + window.scrollX) + "px";
      pop.style.top = (top + window.scrollY) + "px";
    }
    function open(el, focusGrid) {
      if (!pop) build();
      if (el.disabled || el.readOnly) return;
      field = el;
      var base = parse(el.value) ? el.value : clampToRange(today());
      var p = parse(base);
      view = { y: p.y, m: p.m };
      cursor = base;
      mode = "days";
      render();
      pop.hidden = false;
      el.setAttribute("aria-expanded", "true");
      place();
      if (focusGrid) focusCursor();
    }
    function close(refocus) {
      if (!pop || pop.hidden) return;
      pop.hidden = true;
      if (field) {
        field.setAttribute("aria-expanded", "false");
        if (refocus) field.focus();
      }
      field = null;
    }
    function focusCursor() {
      var b = pop.querySelector('.cal-day[data-date="' + cursor + '"]');
      if (b) b.focus();
    }
    function choose(v) {
      var el = field;
      el.value = v;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      close(true);
    }
    function moveCursor(v) {
      cursor = v;
      var p = parse(v);
      if (p.y !== view.y || p.m !== view.m) { view = { y: p.y, m: p.m }; }
      render();
      focusCursor();
    }
    function onClick(e) {
      var b = e.target.closest("button");
      if (!b || b.disabled) return;
      var act = b.getAttribute("data-act");
      if (b.hasAttribute("data-date")) return choose(b.getAttribute("data-date"));
      if (b.hasAttribute("data-month")) { view.m = +b.getAttribute("data-month"); mode = "days"; cursor = iso(view.y, view.m, 1); return render(); }
      if (b.hasAttribute("data-year")) { view.y = +b.getAttribute("data-year"); mode = "months"; return render(); }
      if (act === "zoom") { mode = mode === "days" ? "months" : "years"; return render(); }
      var step = act === "prev" ? -1 : act === "next" ? 1 : 0;
      if (step) {
        if (mode === "days") { var t = new Date(view.y, view.m + step, 1); view = { y: t.getFullYear(), m: t.getMonth() }; }
        else view.y += step * (mode === "months" ? 1 : 12);
        return render();
      }
      if (act === "today") return choose(today());
      if (act === "clear") return choose("");
    }
    function onKey(e) {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); return close(true); }
      if (e.key === "Tab") return close(false);
      if (mode !== "days" || !e.target.classList.contains("cal-day")) return;
      var moves = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
      if (moves[e.key]) { e.preventDefault(); return moveCursor(addDays(cursor, moves[e.key])); }
      if (e.key === "PageUp" || e.key === "PageDown") { e.preventDefault(); return moveCursor(addMonths(cursor, e.key === "PageUp" ? -1 : 1)); }
      if (e.key === "Home" || e.key === "End") {
        e.preventDefault();
        var p = parse(cursor), wd = new Date(p.y, p.m, p.d).getDay();
        return moveCursor(addDays(cursor, e.key === "Home" ? -wd : 6 - wd));
      }
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); if (allowed(cursor)) choose(cursor); }
    }
    function isDate(el) { return el && el.tagName === "INPUT" && el.type === "date"; }

    // Firefox opens its own popup on a click: stop it, keep the focus.
    document.addEventListener("mousedown", function (e) {
      if (!isDate(e.target)) return;
      if (gecko) { e.preventDefault(); e.target.focus(); }
    });
    document.addEventListener("click", function (e) {
      if (isDate(e.target)) {
        if (pop && !pop.hidden && field === e.target) close(false);
        else open(e.target, false);
      }
    });
    document.addEventListener("keydown", function (e) {
      if (!isDate(e.target)) return;
      if ((e.altKey && e.key === "ArrowDown") || e.key === " " || e.key === "F4") {
        e.preventDefault();
        open(e.target, true);
      } else if (e.key === "Escape" && pop && !pop.hidden) {
        e.stopPropagation();
        close(false);
      } else if (e.key === "Tab") close(false);
    }, true);
    // Typing a date into the field moves the open calendar to it.
    document.addEventListener("input", function (e) {
      if (!pop || pop.hidden || e.target !== field) return;
      var p = parse(field.value);
      if (p) { view = { y: p.y, m: p.m }; cursor = field.value; mode = "days"; render(); place(); }
    });
    document.addEventListener("mousedown", function (e) {
      if (pop && !pop.hidden && !pop.contains(e.target) && e.target !== field) close(false);
    });
    window.addEventListener("resize", place);
    document.addEventListener("scroll", place, true);
  })();

  /* ---- Number-only fields ----
     Every <input type="number"> takes digits only: one decimal point when
     its step allows decimals (step="1" = whole numbers), a minus sign only
     when its min is below zero — never letters, "e", "+" or other symbols.
     Text fields that hold a number say what they allow with data-chars:
       phone   — digits, + - and spaces (a textarea also takes line breaks)
       tin     — digits and dashes          account — digits, dashes, spaces
     Typing a character that isn't allowed is refused (the box flashes);
     pasted text is cleaned, e.g. "₱1,250.50" → 1250.50. */
  (function () {
    var CHARS = { phone: /[0-9+\- ]/, tin: /[0-9\-]/, account: /[0-9\- ]/ };
    function ruleFor(el) {
      if (!el || !el.tagName) return null;
      if (el.tagName === "INPUT" && el.type === "number") {
        var step = el.getAttribute("step"), min = parseFloat(el.getAttribute("min"));
        var s = parseFloat(step);
        return { number: true, dot: !step || step === "any" || isNaN(s) || s !== Math.round(s),
                 minus: !isNaN(min) && min < 0 };
      }
      var kind = el.getAttribute("data-chars");
      if (kind && CHARS[kind]) return { re: CHARS[kind], lines: el.tagName === "TEXTAREA" };
      return null;
    }
    function ok(rule, ch) {
      if (rule.number) return /[0-9]/.test(ch) || (rule.dot && ch === ".") || (rule.minus && ch === "-");
      return rule.re.test(ch) || (rule.lines && (ch === "\n" || ch === "\r"));
    }
    function clean(rule, text) {
      text = String(text || "");
      if (rule.number) {
        var out = "", seenDot = false;
        text.replace(/,/g, "").split("").forEach(function (ch, i) {
          if (/[0-9]/.test(ch)) out += ch;
          else if (ch === "." && rule.dot && !seenDot) { out += ch; seenDot = true; }
          else if (ch === "-" && rule.minus && !out) out += ch;
        });
        return out;
      }
      return text.split("").filter(function (ch) { return ok(rule, ch); }).join("");
    }
    function flash(el) {
      el.classList.remove("input-refused");
      void el.offsetWidth;            // restart the animation
      el.classList.add("input-refused");
      clearTimeout(el.__refusedTimer);
      el.__refusedTimer = setTimeout(function () { el.classList.remove("input-refused"); }, 600);
    }
    document.addEventListener("beforeinput", function (e) {
      var el = e.target, rule = ruleFor(el);
      if (!rule || !/^insert/.test(e.inputType || "")) return;
      if (e.inputType === "insertLineBreak" || e.inputType === "insertParagraph") {
        if (!rule.lines) { e.preventDefault(); flash(el); }
        return;
      }
      var text = e.data;
      if (text == null && e.dataTransfer) text = e.dataTransfer.getData("text/plain");
      if (text == null || text === "") return;
      var bad = text.split("").some(function (ch) { return !ok(rule, ch); });
      if (rule.number && rule.dot && text.indexOf(".") > -1 && String(el.value).indexOf(".") > -1) bad = true;
      if (!bad) return;
      e.preventDefault();
      var fixed = clean(rule, text);
      if (fixed && e.inputType !== "insertText") {
        // Pasted / dropped: put in the cleaned text.
        if (rule.number) el.value = fixed;
        else if (typeof el.setRangeText === "function") el.setRangeText(fixed, el.selectionStart, el.selectionEnd, "end");
        el.dispatchEvent(new Event("input", { bubbles: true }));
      } else {
        flash(el);
      }
    });
    // Old browsers without beforeinput on number boxes: block the key itself.
    document.addEventListener("keydown", function (e) {
      var el = e.target, rule = ruleFor(el);
      if (!rule || !rule.number || e.ctrlKey || e.metaKey || e.altKey || !e.key || e.key.length !== 1) return;
      if (!ok(rule, e.key)) { e.preventDefault(); flash(el); }
    });
  })();

  /* ---- Photo drop box: <label class="dropzone" data-dropzone> around a file
     input; shows the chosen picture and its name before the form is saved. */
  document.addEventListener("change", function (e) {
    var box = e.target.closest && e.target.closest("[data-dropzone]");
    if (!box || e.target.type !== "file") return;
    var files = Array.prototype.slice.call(e.target.files || []);
    var f = files[0];
    box.querySelector(".dropzone-empty").hidden = !!f;
    box.querySelector(".dropzone-chosen").hidden = !f;
    box.classList.toggle("has-file", !!f);
    if (!f) return;
    var thumbs = box.querySelector(".dropzone-thumbs");
    if (thumbs) {                     // several pictures: a row of thumbnails
      thumbs.innerHTML = "";
      files.forEach(function (file) {
        if (/^image\//.test(file.type)) {
          var img = document.createElement("img");
          img.src = URL.createObjectURL(file);
          img.alt = "";
          thumbs.appendChild(img);
        } else {                      // a PDF or other document: a labelled tile
          var tile = document.createElement("span");
          tile.className = "dropzone-doc";
          tile.textContent = (file.name.split(".").pop() || "file").toUpperCase();
          thumbs.appendChild(tile);
        }
      });
      var allPics = files.every(function (x) { return /^image\//.test(x.type); });
      box.querySelector("[data-name]").textContent = files.length === 1 ? f.name
        : files.length + (allPics ? " photos: " : " files: ") + files.map(function (x) { return x.name; }).join(", ");
    } else {
      box.querySelector(".dropzone-chosen img").src = URL.createObjectURL(f);
      box.querySelector("[data-name]").textContent = f.name;
    }
  });

  /* Expose a tiny namespace for inline page scripts. */
  window.IMS = {
    initWizards: initWizards,
    esc: esc,
    openModal: openModal,
    closeModal: function (id) {
      var m = document.getElementById(id);
      if (m) m.classList.remove("open");
    },
    fetchJSON: fetchJSON,
    paginate: paginateAll
  };
})();
