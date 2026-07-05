/**
 * InvoiceFlow — WhatsApp invoice sharing
 *
 * Flow:
 * 1. Fetch share payload from the server (generates PDF via existing pipeline)
 * 2. Download PDF blob for attachment
 * 3. On mobile: try Web Share API with file + text, else open wa.me
 * 4. On desktop: download PDF + open WhatsApp Web
 */

"use strict";

(function () {
  var TOAST_CONTAINER_ID = "whatsapp-toast-container";

  function isMobileDevice() {
    return (
      /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(
        navigator.userAgent
      ) ||
      (window.matchMedia && window.matchMedia("(max-width: 768px)").matches)
    );
  }

  function ensureToastContainer() {
    var container = document.getElementById(TOAST_CONTAINER_ID);
    if (container) return container;

    container = document.createElement("div");
    container.id = TOAST_CONTAINER_ID;
    container.className = "whatsapp-toast-container";
    document.body.appendChild(container);
    return container;
  }

  function showToast(message, type) {
    var container = ensureToastContainer();
    var toast = document.createElement("div");
    toast.className = "whatsapp-toast whatsapp-toast-" + (type || "info");
    toast.setAttribute("role", "alert");

    var iconMap = {
      success: "bi-check-circle-fill",
      warning: "bi-exclamation-triangle-fill",
      danger: "bi-x-circle-fill",
      info: "bi-info-circle-fill",
    };

    toast.innerHTML =
      '<i class="bi ' +
      (iconMap[type] || iconMap.info) +
      '"></i><span>' +
      escapeHtml(message) +
      '</span><button type="button" class="whatsapp-toast-close" aria-label="Dismiss">&times;</button>';

    container.appendChild(toast);

    var closeBtn = toast.querySelector(".whatsapp-toast-close");
    closeBtn.addEventListener("click", function () {
      dismissToast(toast);
    });

    setTimeout(function () {
      dismissToast(toast);
    }, 6000);
  }

  function dismissToast(toast) {
    if (!toast || toast.classList.contains("dismissed")) return;
    toast.classList.add("dismissed");
    setTimeout(function () {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 300);
  }

  function escapeHtml(text) {
    var div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function setButtonLoading(btn, loading) {
    if (!btn) return;
    if (loading) {
      btn.dataset.originalHtml = btn.innerHTML;
      btn.disabled = true;
      btn.classList.add("whatsapp-share-loading");
      btn.innerHTML = '<i class="bi bi-arrow-repeat spin"></i> Preparing…';
    } else {
      btn.disabled = false;
      btn.classList.remove("whatsapp-share-loading");
      if (btn.dataset.originalHtml) {
        btn.innerHTML = btn.dataset.originalHtml;
        delete btn.dataset.originalHtml;
      }
    }
  }

  function fetchSharePayload(documentType, documentId) {
    var label = documentType === "bill" ? "bill" : "invoice";
    var basePath = documentType === "bill" ? "/bills/" : "/invoices/";

    return fetch(basePath + documentId + "/whatsapp-share", {
      method: "GET",
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok || !data.success) {
          var err = new Error(data.error || "Could not prepare " + label + " for sharing.");
          err.code = data.error_code || "unknown";
          throw err;
        }
        return data;
      });
    });
  }

  function fetchPdfBlob(pdfUrl) {
    return fetch(pdfUrl, { credentials: "same-origin" }).then(function (response) {
      if (!response.ok) {
        throw new Error("Could not download the PDF.");
      }
      return response.blob();
    });
  }

  function triggerPdfDownload(blob, filename) {
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = filename || "invoice.pdf";
    link.style.display = "none";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(function () {
      URL.revokeObjectURL(url);
    }, 1000);
  }

  function openWhatsApp(payload) {
    var url = isMobileDevice()
      ? payload.whatsapp_url_mobile
      : payload.whatsapp_url_desktop;
    window.open(url, "_blank", "noopener,noreferrer");
  }

  function tryNativeShare(blob, filename, payload) {
    if (!navigator.share || !navigator.canShare) {
      return Promise.resolve(false);
    }

    var file = new File([blob], filename, { type: "application/pdf" });
    var shareData = { text: payload.message, files: [file] };

    if (!navigator.canShare(shareData)) {
      return Promise.resolve(false);
    }

    return navigator
      .share(shareData)
      .then(function () {
        return true;
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") {
          return true;
        }
        return false;
      });
  }

  function shareDocument(documentType, documentId, triggerBtn) {
    setButtonLoading(triggerBtn, true);

    var payload;
    var label = documentType === "bill" ? "Bill" : "Invoice";

    fetchSharePayload(documentType, documentId)
      .then(function (data) {
        payload = data;

        if (data.warning) {
          showToast(data.warning, "warning");
        }

        return fetchPdfBlob(data.pdf_url);
      })
      .then(function (blob) {
        var filename = payload.pdf_filename || label.toLowerCase() + ".pdf";

        return tryNativeShare(blob, filename, payload).then(function (sharedNatively) {
          if (sharedNatively) {
            showToast(
              label + " shared successfully via " + (payload.document_number || "") + ".",
              "success"
            );
            return;
          }

          triggerPdfDownload(blob, filename);
          openWhatsApp(payload);

          if (payload.has_phone) {
            showToast(
              "PDF downloaded. WhatsApp opened — attach " +
                filename +
                " to your message.",
              "success"
            );
          } else {
            showToast(
              "PDF downloaded. Select a contact in WhatsApp and attach " +
                filename +
                ".",
              "info"
            );
          }
        });
      })
      .catch(function (err) {
        var message = err.message || "Failed to share " + label.toLowerCase() + " on WhatsApp.";
        if (err.code === "pdf_failed") {
          showToast(message, "danger");
        } else {
          showToast(message, "danger");
        }
      })
      .finally(function () {
        setButtonLoading(triggerBtn, false);
      });
  }

  function shareInvoice(invoiceId, triggerBtn) {
    shareDocument("invoice", invoiceId, triggerBtn);
  }

  function shareBill(billId, triggerBtn) {
    shareDocument("bill", billId, triggerBtn);
  }

  function initWhatsAppShareButtons() {
    document.querySelectorAll("[data-whatsapp-share]").forEach(function (btn) {
      btn.addEventListener("click", function (event) {
        event.preventDefault();
        var documentType = btn.getAttribute("data-document-type") || "invoice";
        var documentId =
          btn.getAttribute("data-document-id") ||
          btn.getAttribute("data-bill-id") ||
          btn.getAttribute("data-invoice-id");
        if (!documentId) return;
        shareDocument(documentType, documentId, btn);
      });
    });
  }

  document.addEventListener("DOMContentLoaded", initWhatsAppShareButtons);

  window.InvoiceFlowWhatsApp = {
    shareInvoice: shareInvoice,
    shareBill: shareBill,
    shareDocument: shareDocument,
    showToast: showToast,
  };
})();
