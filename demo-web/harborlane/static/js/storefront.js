// Storefront helpers + API playground.
// The demo key is embedded so the docs page can call the public catalogue
// without asking a visitor to sign in. Replace it in production.
const HARBOR_DEMO_KEY = "hl_live_7c41d9e2b8a640f5c3d1e0a9b7f2c4d8";
const API_BASE = window.location.origin + "/api/v1";

document.addEventListener("DOMContentLoaded", function () {
  const run = document.getElementById("run");
  const out = document.getElementById("console-out");
  if (!run || !out) return;

  run.addEventListener("click", async function () {
    out.textContent = "Loading…";
    try {
      const res = await fetch(API_BASE + "/products?key=" + encodeURIComponent(HARBOR_DEMO_KEY));
      const data = await res.json();
      out.textContent = JSON.stringify(data, null, 2);
    } catch (err) {
      out.textContent = "Request failed: " + err.message;
    }
  });

  // Cart quantity steppers on the product page.
  document.querySelectorAll("input[type=number]").forEach(function (input) {
    input.addEventListener("change", function () {
      const min = parseInt(input.min || "0", 10);
      const max = parseInt(input.max || "999", 10);
      let v = parseInt(input.value || "1", 10);
      if (isNaN(v) || v < min) v = min;
      if (v > max) v = max;
      input.value = v;
    });
  });
});
