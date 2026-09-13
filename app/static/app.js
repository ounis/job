// Job Hunter — client behaviors.
document.addEventListener("DOMContentLoaded", function () {
  // Select-all toggles every job row checkbox.
  var selectAll = document.getElementById("select-all");
  if (selectAll) {
    selectAll.addEventListener("change", function () {
      document.querySelectorAll(".row-check").forEach(function (cb) {
        cb.checked = selectAll.checked;
      });
    });
  }

  // Extra validation for the bulk apply/prepare form: require a selection.
  var applyForm = document.getElementById("apply-form");
  if (applyForm) {
    applyForm.addEventListener("submit", function (e) {
      var checked = applyForm.querySelectorAll(".row-check:checked").length;
      if (checked === 0) {
        e.preventDefault();
        alert("Select at least one job to prepare.");
      }
    });
  }

  // Global guard: on ANY form submit, disable the triggering button and show a
  // working state so it can't be clicked/triggered again while the request is
  // in flight. Applies to every server-rendered POST form (scan, prepare,
  // apply, status, events, notes, ignore, forget, settings, cv, ...).
  // Bubble phase (default) so it runs AFTER inline onsubmit="return confirm()"
  // and the per-form validation above — those set defaultPrevented when they
  // cancel, and we must not lock a form whose submit was cancelled.
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (e.defaultPrevented) return; // validation failed or confirm() cancelled

    var btn =
      e.submitter ||
      form.querySelector('button[type=submit], button:not([type]), input[type=submit]');

    // Relabel + lock SYNCHRONOUSLY so the state paints before the browser
    // navigates. (Our submit buttons carry no name/value, so disabling them
    // doesn't drop any form data.) We disable via a class + the actual
    // disabled attr; the attr is set after a microtask-free direct call.
    if (btn && btn.tagName === "BUTTON") {
      if (!btn.dataset.label) btn.dataset.label = btn.textContent;
      btn.textContent = "Working…";
    }
    form.classList.add("is-submitting");
    form.querySelectorAll("button, input[type=submit]").forEach(function (el) {
      el.setAttribute("aria-disabled", "true");
      el.classList.add("is-locked");
      // Prevent further activations without removing the element from the form
      // submission that is already underway.
      el.addEventListener("click", _blockClick, true);
    });
  });

  function _blockClick(ev) {
    ev.preventDefault();
    ev.stopPropagation();
  }

  // If the user returns via browser back/forward (bfcache), the page may be
  // restored with buttons still locked — unlock them.
  window.addEventListener("pageshow", function () {
    document.querySelectorAll(".is-submitting").forEach(function (form) {
      form.classList.remove("is-submitting");
      form.querySelectorAll(".is-locked").forEach(function (el) {
        el.classList.remove("is-locked");
        el.removeAttribute("aria-disabled");
        el.removeEventListener("click", _blockClick, true);
        if (el.dataset.label) el.textContent = el.dataset.label;
      });
    });
  });
});
