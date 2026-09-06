// Select-all toggles every job row checkbox.
document.addEventListener("DOMContentLoaded", function () {
  var selectAll = document.getElementById("select-all");
  if (selectAll) {
    selectAll.addEventListener("change", function () {
      document.querySelectorAll(".row-check").forEach(function (cb) {
        cb.checked = selectAll.checked;
      });
    });
  }

  // Guard: applying can be slow (AI + PDF). Give feedback + prevent double submit.
  var applyForm = document.getElementById("apply-form");
  if (applyForm) {
    applyForm.addEventListener("submit", function (e) {
      var checked = applyForm.querySelectorAll(".row-check:checked").length;
      if (checked === 0) {
        e.preventDefault();
        alert("Select at least one job to apply to.");
        return;
      }
      var btn = applyForm.querySelector("button[type=submit]");
      if (btn) {
        btn.disabled = true;
        btn.textContent = "Generating applications…";
      }
    });
  }
});
