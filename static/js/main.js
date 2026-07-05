/**
 * InvoiceFlow — Main JavaScript
 * Enhanced with smooth transitions, better interactivity, and visual polish
 */

"use strict";

// ── Sidebar toggle (mobile) ───────────────────────────────
function toggleSidebar() {
  const sidebar = document.getElementById("sidebar");
  if (sidebar) {
    sidebar.classList.toggle("open");

    // Toggle body scroll lock when sidebar is open on mobile
    if (window.innerWidth < 992) {
      document.body.style.overflow = sidebar.classList.contains("open") ? "hidden" : "";
    }
  }
}

// Close sidebar when clicking outside on mobile
document.addEventListener("click", function (e) {
  const sidebar = document.getElementById("sidebar");
  const toggle = document.querySelector(".sidebar-toggle");
  if (!sidebar) return;
  if (
    window.innerWidth < 992 &&
    sidebar.classList.contains("open") &&
    !sidebar.contains(e.target) &&
    e.target !== toggle
  ) {
    sidebar.classList.remove("open");
    document.body.style.overflow = "";
  }
});

// Close sidebar on Escape key
document.addEventListener("keydown", function (e) {
  if (e.key === "Escape") {
    const sidebar = document.getElementById("sidebar");
    if (sidebar && sidebar.classList.contains("open")) {
      sidebar.classList.remove("open");
      document.body.style.overflow = "";
    }
  }
});

// ── Animate stat values on page load ─────────────────────
function animateCounters() {
  document.querySelectorAll(".stat-value").forEach((el) => {
    const raw = el.textContent.trim();
    const num = parseFloat(raw.replace(/[^0-9.]/g, ""));
    if (isNaN(num) || num === 0) return;

    const prefix = raw.startsWith("$") ? "$" : raw.startsWith("₹") ? "₹" : "";
    const isFloat = raw.includes(".");
    let start = 0;
    const duration = 800;
    const startTime = performance.now();

    function step(now) {
      const elapsed = now - startTime;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      const current = start + (num - start) * eased;
      el.textContent =
        prefix +
        (isFloat
          ? current.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
          : Math.round(current).toLocaleString("en-IN"));
      if (progress < 1) requestAnimationFrame(step);
    }

    requestAnimationFrame(step);
  });
}

// ── Smooth scroll for anchor links ────────────────────────
function initSmoothScroll() {
  document.querySelectorAll('a[href^="#"]').forEach((anchor) => {
    anchor.addEventListener("click", function (e) {
      const targetId = this.getAttribute("href");
      if (targetId === "#") return;

      const target = document.querySelector(targetId);
      if (target) {
        e.preventDefault();
        const offsetTop = target.getBoundingClientRect().top + window.pageYOffset - 80;
        window.scrollTo({
          top: offsetTop,
          behavior: "smooth",
        });
      }
    });
  });
}

// ── Table row hover effects ──────────────────────────────
function initTableInteractions() {
  document.querySelectorAll(".data-table tbody tr").forEach((row) => {
    row.addEventListener("mouseenter", function () {
      // Subtle lift on hover already handled by CSS
    });
  });
}

// ── Input clear buttons ──────────────────────────────────
function initInputClears() {
  document.querySelectorAll(".search-clear").forEach((btn) => {
    btn.addEventListener("click", function (e) {
      const input = this.closest(".search-bar-wrap")?.querySelector(".search-input");
      if (input) {
        input.value = "";
        // Auto-submit the parent form
        const form = this.closest("form");
        if (form) form.submit();
      }
    });
  });
}

// ── Flash message auto-dismiss with animation ───────────
function initFlashMessages() {
  document.querySelectorAll(".alert.alert-dismissible").forEach(function (alert) {
    setTimeout(function () {
      try {
        const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
        if (bsAlert) bsAlert.close();
      } catch (e) {
        // Bootstrap not available, fade out manually
        alert.style.transition = "opacity 0.3s ease, transform 0.3s ease";
        alert.style.opacity = "0";
        alert.style.transform = "translateY(-8px)";
        setTimeout(() => {
          if (alert.parentNode) alert.parentNode.removeChild(alert);
        }, 300);
      }
    }, 5000);
  });
}

// ── Init ──────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", function () {
  animateCounters();
  initSmoothScroll();
  initTableInteractions();
  initInputClears();
  initFlashMessages();

  // Add 'scrolled' class to topbar on scroll
  const topbar = document.querySelector(".topbar");
  let lastScrollY = 0;

  if (topbar) {
    window.addEventListener("scroll", function () {
      const scrollY = window.scrollY;
      if (scrollY > 10 && lastScrollY <= 10) {
        topbar.style.borderBottomColor = "rgba(0,0,0,.1)";
        topbar.style.boxShadow = "0 2px 12px rgba(0,0,0,.06)";
      } else if (scrollY <= 10 && lastScrollY > 10) {
        topbar.style.borderBottomColor = "";
        topbar.style.boxShadow = "";
      }
      lastScrollY = scrollY;
    }, { passive: true });
  }
});
