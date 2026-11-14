/* OTC Desk — POC interactions. No framework, no build step. */
(function () {
  "use strict";

  const money = (v) =>
    "₦" + Number(v || 0).toLocaleString("en-NG", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  document.addEventListener("DOMContentLoaded", function () {
    /* ---- mobile sidebar ------------------------------------------------ */
    const toggle = document.getElementById("menu-toggle");
    const sidebar = document.getElementById("sidebar");
    if (toggle && sidebar) {
      toggle.addEventListener("click", () => sidebar.classList.toggle("open"));
      document.addEventListener("click", (e) => {
        if (
          sidebar.classList.contains("open") &&
          !sidebar.contains(e.target) &&
          !toggle.contains(e.target)
        ) {
          sidebar.classList.remove("open");
        }
      });
    }

    /* ---- copy to clipboard --------------------------------------------- */
    document.querySelectorAll(".js-copy").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        const holder = btn.closest("[data-copy]");
        const value = holder && holder.dataset.copy;
        if (!value) return;
        e.preventDefault();
        try {
          await navigator.clipboard.writeText(value);
          const old = btn.innerHTML;
          btn.innerHTML = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m5 12.5 4.5 4.5L19 7"/></svg>';
          setTimeout(() => (btn.innerHTML = old), 1200);
        } catch (err) {
          window.prompt("Copy:", value);
        }
      });
    });

    /* ---- deal ticket summary ------------------------------------------- */
    const form = document.getElementById("deal-form");
    if (form) {
      const net = document.getElementById("network");
      const amount = document.getElementById("amount_crypto");
      const rate = document.getElementById("rate_ngn");
      const assetEl = document.getElementById("asset-affix");
      const hint = document.getElementById("network-hint");
      const sumAsset = document.getElementById("summary-asset");
      const rateDirty = { value: false };

      const selected = () => net.options[net.selectedIndex];

      const refreshLabels = () => {
        const opt = selected();
        const asset = opt.dataset.asset;
        assetEl.textContent = asset;
        sumAsset.textContent = asset;
        hint.textContent = "Address format: " + opt.dataset.hint;
        document.getElementById("sum-conf").textContent = opt.dataset.conf + " blocks";
      };

      const recompute = () => {
        const opt = selected();
        const asset = opt.dataset.asset;
        const qty = parseFloat(amount.value.replace(/,/g, "")) || 0;
        const px = parseFloat(rate.value.replace(/,/g, "")) || 0;
        const gross = qty * px;
        const fee = gross * 0.006;

        document.getElementById("sum-crypto").textContent =
          qty.toLocaleString("en-NG", { maximumFractionDigits: 8 }) + " " + asset;
        document.getElementById("sum-rate").textContent = px ? money(px) : "—";
        document.getElementById("sum-gross").textContent = money(gross);
        document.getElementById("sum-fee").textContent = money(fee);
        document.getElementById("sum-net").textContent = money(gross - fee);
      };

      net.addEventListener("change", () => {
        refreshLabels();
        if (!rateDirty.value) rate.value = selected().dataset.rate;
        recompute();
      });
      rate.addEventListener("input", () => (rateDirty.value = true));
      amount.addEventListener("input", recompute);
      rate.addEventListener("input", recompute);

      refreshLabels();
      recompute();
    }

    /* ---- live deal status ---------------------------------------------- */
    const live = document.getElementById("deal-live");
    if (live) {
      const dealId = live.dataset.dealId;
      const initial = live.dataset.status;
      let ticks = 0;
      const timer = setInterval(async () => {
        if (++ticks > 120) return clearInterval(timer);
        try {
          const res = await fetch("/api/deals/" + dealId + "/status", { headers: { Accept: "application/json" } });
          if (!res.ok) return;
          const data = await res.json();
          if (data.status !== initial) window.location.reload();
        } catch (err) {
          /* offline: keep polling quietly */
        }
      }, 5000);
    }

    /* ---- confirm destructive forms ------------------------------------- */
    document.querySelectorAll("form[data-confirm]").forEach((f) => {
      f.addEventListener("submit", (e) => {
        if (!window.confirm(f.dataset.confirm)) e.preventDefault();
      });
    });
  });
})();
