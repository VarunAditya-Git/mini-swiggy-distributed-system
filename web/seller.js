/**
 * Mini Swiggy — Seller Dashboard & Restaurant Management Client (V3)
 * Pure vanilla JavaScript communicating with the Python HTTP Gateway (Port 8000),
 * which forwards requests over raw TCP socket streams (Port 5000) to SQLite.
 */

(function () {
  "use strict";

  // -------------------------------------------------------------------
  // State Management
  // -------------------------------------------------------------------
  const state = {
    sellerId: 2, // Default: Bob Seller (ID: 2) development identity
    restaurant: null,
    stats: null,
    activeTab: "orders",
    activeFilter: "all",
    orders: [],
    menu: [],
    pollTimer: null,
  };

  // -------------------------------------------------------------------
  // DOM Elements
  // -------------------------------------------------------------------
  const el = {
    // Navigation
    identitySelect: document.getElementById("seller-identity-select"),
    backendDot: document.getElementById("backend-dot"),
    backendStatusText: document.getElementById("backend-status-text"),
    btnRefresh: document.getElementById("btn-refresh-dashboard"),
    btnOpenAddItem: document.getElementById("btn-open-add-item-modal"),
    
    // Hero
    displayRestaurantName: document.getElementById("display-restaurant-name"),
    kitchenStatusBadge: document.getElementById("kitchen-status-badge"),
    restaurantDescText: document.getElementById("restaurant-desc-text"),

    // KPI Stats
    statTotalOrders: document.getElementById("stat-total-orders"),
    statPendingOrders: document.getElementById("stat-pending-orders"),
    statAcceptedOrders: document.getElementById("stat-accepted-orders"),
    statPreparingOrders: document.getElementById("stat-preparing-orders"),
    statReadyOrders: document.getElementById("stat-ready-orders"),
    statMenuCount: document.getElementById("stat-menu-count"),

    // Tabs
    tabBtnOrders: document.getElementById("tab-btn-orders"),
    tabBtnMenu: document.getElementById("tab-btn-menu"),
    tabContentOrders: document.getElementById("tab-content-orders"),
    tabContentMenu: document.getElementById("tab-content-menu"),
    tabOrdersCount: document.getElementById("tab-orders-count"),
    tabMenuCount: document.getElementById("tab-menu-count"),

    // Orders Filter & List
    filterPills: document.querySelectorAll(".filter-pill"),
    ordersListContainer: document.getElementById("orders-list-container"),

    // Menu Table
    menuTableBody: document.getElementById("menu-items-table-body"),

    // Modals
    modalAddItem: document.getElementById("modal-add-item"),
    formAddItem: document.getElementById("form-add-item"),
    addItemName: document.getElementById("add-item-name"),
    addItemPrice: document.getElementById("add-item-price"),
    addItemDesc: document.getElementById("add-item-desc"),
    addItemAvailable: document.getElementById("add-item-available"),

    modalEditItem: document.getElementById("modal-edit-item"),
    formEditItem: document.getElementById("form-edit-item"),
    editItemId: document.getElementById("edit-item-id"),
    editItemName: document.getElementById("edit-item-name"),
    editItemPrice: document.getElementById("edit-item-price"),
    editItemDesc: document.getElementById("edit-item-desc"),
    editItemAvailable: document.getElementById("edit-item-available"),
    editAvailLabelText: document.getElementById("edit-avail-label-text"),

    modalOrderDetails: document.getElementById("modal-order-details"),
    detailOrderId: document.getElementById("detail-order-id"),
    detailOrderBody: document.getElementById("detail-order-body"),

    // Toast
    toastContainer: document.getElementById("toast-container"),
  };

  // -------------------------------------------------------------------
  // Utility Functions
  // -------------------------------------------------------------------
  function formatRupees(amount) {
    const num = Number(amount) || 0;
    return `₹${num.toFixed(2)}`;
  }

  function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function showToast(message, type = "info") {
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.innerHTML = `
      <span>${type === "success" ? "✓" : type === "error" ? "✕" : "ℹ"}</span>
      <span>${escapeHtml(message)}</span>
    `;
    el.toastContainer.appendChild(toast);
    setTimeout(() => {
      toast.style.transition = "opacity 0.4s ease";
      toast.style.opacity = "0";
      setTimeout(() => toast.remove(), 400);
    }, 4000);
  }

  // -------------------------------------------------------------------
  // API Client Methods (Calling HTTP Gateway /api/seller/*)
  // -------------------------------------------------------------------
  const api = {
    async checkHealth() {
      try {
        const res = await fetch("/api/health");
        return await res.json();
      } catch (err) {
        return { ok: false, error: err.message };
      }
    },

    async getProfile(sellerId) {
      const res = await fetch(`/api/seller/profile?seller_id=${sellerId}`);
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.error?.message || `HTTP ${res.status}: Failed to load seller profile`);
      }
      return data.data;
    },

    async getOrders(sellerId, status = "") {
      let url = `/api/seller/orders?seller_id=${sellerId}`;
      if (status && status !== "all") {
        url += `&status=${encodeURIComponent(status)}`;
      }
      const res = await fetch(url);
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.error?.message || `HTTP ${res.status}: Failed to load orders`);
      }
      return data.data;
    },

    async updateOrderStatus(orderId, sellerId, status) {
      const res = await fetch(`/api/seller/orders/${orderId}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ seller_id: sellerId, status: status }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.error?.message || `HTTP ${res.status}: Status update failed`);
      }
      return data.data;
    },

    async getMenu(sellerId) {
      const res = await fetch(`/api/seller/menu?seller_id=${sellerId}`);
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.error?.message || `HTTP ${res.status}: Failed to load menu`);
      }
      return data.data;
    },

    async addMenuItem(sellerId, itemData) {
      const res = await fetch("/api/seller/menu", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ seller_id: sellerId, ...itemData }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.error?.message || `HTTP ${res.status}: Failed to add menu item`);
      }
      return data.data;
    },

    async updateMenuItem(itemId, sellerId, itemData) {
      const res = await fetch(`/api/seller/menu/${itemId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ seller_id: sellerId, ...itemData }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.error?.message || `HTTP ${res.status}: Failed to update menu item`);
      }
      return data.data;
    },

    async updateItemAvailability(itemId, sellerId, isAvailable) {
      const res = await fetch(`/api/seller/menu/${itemId}/availability`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ seller_id: sellerId, is_available: isAvailable ? 1 : 0 }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(data.error?.message || `HTTP ${res.status}: Failed to toggle availability`);
      }
      return data.data;
    },
  };

  // -------------------------------------------------------------------
  // View Rendering & Controller
  // -------------------------------------------------------------------
  async function updateBackendHealth() {
    const health = await api.checkHealth();
    if (health.ok && health.tcp_backend === "connected") {
      el.backendDot.className = "dot-indicator connected";
      el.backendStatusText.textContent = `Backend TCP Connected (Port 5000)`;
    } else {
      el.backendDot.className = "dot-indicator disconnected";
      el.backendStatusText.textContent = `Backend Disconnected: ${health.error || "Offline"}`;
    }
  }

  async function loadDashboard() {
    try {
      // 1. Fetch Profile and Stats
      const profileData = await api.getProfile(state.sellerId);
      state.restaurant = profileData.restaurant;
      state.stats = profileData.stats;

      // Update Hero Header
      el.displayRestaurantName.textContent = profileData.restaurant.name;
      el.kitchenStatusBadge.className = `kitchen-badge ${profileData.restaurant.is_open ? "open" : "closed"}`;
      el.kitchenStatusBadge.textContent = profileData.restaurant.is_open ? "Accepting Orders" : "Closed";
      el.restaurantDescText.textContent = `${profileData.restaurant.description} · Owned by ${profileData.seller.name} (${profileData.seller.email})`;

      // Update KPI Statistics
      el.statTotalOrders.textContent = profileData.stats.total_orders;
      el.statPendingOrders.textContent = profileData.stats.pending_orders;
      el.statAcceptedOrders.textContent = profileData.stats.accepted_orders;
      el.statPreparingOrders.textContent = profileData.stats.preparing_orders;
      el.statReadyOrders.textContent = profileData.stats.ready_orders;
      el.statMenuCount.textContent = profileData.stats.menu_items_count;

      el.tabOrdersCount.textContent = profileData.stats.total_orders;
      el.tabMenuCount.textContent = profileData.stats.menu_items_count;

      // 2. Fetch Orders for current filter
      await loadOrders();

      // 3. Fetch Menu Items
      await loadMenu();

    } catch (err) {
      console.error("Dashboard error:", err);
      el.displayRestaurantName.textContent = "Access Denied / Not Found";
      el.kitchenStatusBadge.className = "kitchen-badge closed";
      el.kitchenStatusBadge.textContent = "Error";
      el.restaurantDescText.textContent = err.message;

      el.ordersListContainer.innerHTML = `
        <div class="empty-state">
          <p style="color: var(--seller-red); font-weight: 700;">${escapeHtml(err.message)}</p>
          <p>Please select a valid seller identity from the top dropdown (e.g. Bob Seller ID 2).</p>
        </div>
      `;
      el.menuTableBody.innerHTML = `
        <tr>
          <td colspan="5" class="empty-state" style="color: var(--seller-red);">${escapeHtml(err.message)}</td>
        </tr>
      `;
      showToast(err.message, "error");
    }
  }

  async function loadOrders() {
    try {
      const orders = await api.getOrders(state.sellerId, state.activeFilter);
      state.orders = orders;
      renderOrders(orders);
    } catch (err) {
      el.ordersListContainer.innerHTML = `
        <div class="empty-state">
          <p style="color: var(--seller-red); font-weight: 700;">Could not load orders: ${escapeHtml(err.message)}</p>
        </div>
      `;
    }
  }

  function renderOrders(orders) {
    if (!orders || orders.length === 0) {
      el.ordersListContainer.innerHTML = `
        <div class="empty-state">
          <svg viewBox="0 0 24 24" width="48" height="48" fill="none" stroke="currentColor" stroke-width="1.5" style="margin: 0 auto 0.75rem; color: var(--seller-text-muted);">
            <rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect>
            <line x1="8" y1="21" x2="16" y2="21"></line>
            <line x1="12" y1="17" x2="12" y2="21"></line>
          </svg>
          <p style="font-size: 1.1rem; font-weight: 700; color: var(--seller-text);">No orders found</p>
          <p>No customer orders currently match the filter: <strong>${escapeHtml(state.activeFilter)}</strong>.</p>
        </div>
      `;
      return;
    }

    el.ordersListContainer.innerHTML = orders
      .map((ord) => {
        const isPaid = ord.payment_status === "paid";
        const status = ord.order_status;
        const normStatus = status === "placed" ? "pending" : status;

        // Render Action Buttons according to state lifecycle
        let actionButtonsHtml = "";
        if (normStatus === "pending" || normStatus === "placed") {
          actionButtonsHtml = `
            <button class="btn-action btn-accept" data-action="accept" data-order-id="${ord.order_id}">
              ✓ Accept Order
            </button>
            <button class="btn-action btn-reject" data-action="reject" data-order-id="${ord.order_id}">
              ✕ Reject
            </button>
          `;
        } else if (normStatus === "accepted") {
          actionButtonsHtml = `
            <button class="btn-action btn-prepare" data-action="prepare" data-order-id="${ord.order_id}">
              👨‍🍳 Start Preparing
            </button>
            <button class="btn-action btn-reject" data-action="reject" data-order-id="${ord.order_id}">
              ✕ Reject
            </button>
          `;
        } else if (normStatus === "preparing") {
          actionButtonsHtml = `
            <button class="btn-action btn-ready" data-action="ready" data-order-id="${ord.order_id}">
              📦 Mark Ready for Pickup
            </button>
          `;
        } else if (normStatus === "ready" || normStatus === "ready_for_pickup") {
          actionButtonsHtml = `
            <span class="status-badge ready">✓ Ready for Pickup</span>
          `;
        } else if (normStatus === "rejected" || normStatus === "cancelled") {
          actionButtonsHtml = `
            <span class="status-badge rejected">✕ Order Rejected</span>
          `;
        }

        let paymentBadgeHtml = `<span class="payment-tag ${isPaid ? "paid" : "pending"}">${isPaid ? "PAID" : "PAYMENT PENDING"}</span>`;
        if (ord.refund) {
          if (ord.refund.refund_status === "refunded") {
            paymentBadgeHtml = `<span class="payment-tag refunded">PAID &middot; REFUNDED (${formatRupees(ord.refund.amount)})</span>`;
          } else if (ord.refund.refund_status === "failed") {
            paymentBadgeHtml = `<span class="payment-tag refund-failed">PAID &middot; REFUND FAILED</span>`;
          } else {
            paymentBadgeHtml = `<span class="payment-tag refunded">PAID &middot; REFUND ${escapeHtml(ord.refund.refund_status.toUpperCase())}</span>`;
          }
        }

        return `
          <article class="order-card" data-order-id="${ord.order_id}">
            <div class="order-card-header">
              <div>
                <span class="order-number">Order #${ord.order_id}</span>
                <div class="order-meta-info">
                  <span>Customer: <strong>${escapeHtml(ord.customer_name)}</strong></span>
                  <span>·</span>
                  <span>Placed: ${escapeHtml(ord.created_at || "Recent")}</span>
                </div>
              </div>
              <span class="status-badge ${normStatus}">
                ${escapeHtml(normStatus.replace(/_/g, " "))}
              </span>
            </div>

            <div class="order-items-box">
              <table class="order-items-table">
                <thead>
                  <tr>
                    <th>Item</th>
                    <th style="width: 80px; text-align: center;">Qty</th>
                    <th style="width: 100px; text-align: right;">Price</th>
                    <th style="width: 100px; text-align: right;">Subtotal</th>
                  </tr>
                </thead>
                <tbody>
                  ${ord.items
                    .map(
                      (item) => `
                    <tr>
                      <td><strong>${escapeHtml(item.name)}</strong></td>
                      <td style="text-align: center;">&times; ${item.quantity}</td>
                      <td style="text-align: right; color: var(--seller-text-muted);">${formatRupees(item.unit_price)}</td>
                      <td style="text-align: right; font-weight: 700;">${formatRupees(item.subtotal)}</td>
                    </tr>
                  `
                    )
                    .join("")}
                </tbody>
              </table>
            </div>

            <div class="order-card-footer">
              <div class="order-summary-box">
                ${paymentBadgeHtml}
                <div>
                  <span style="font-size: 0.8rem; color: var(--seller-text-muted);">Total Order Value:</span>
                  <span class="total-due">${formatRupees(ord.total_amount)}</span>
                </div>
              </div>

              <div class="order-actions">
                ${actionButtonsHtml}
                <button class="btn-action btn-secondary btn-order-details" data-order-id="${ord.order_id}">
                  Details
                </button>
              </div>
            </div>
          </article>
        `;
      })
      .join("");

    // Bind action buttons
    el.ordersListContainer.querySelectorAll("[data-action]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        const orderId = Number(btn.getAttribute("data-order-id"));
        const actionType = btn.getAttribute("data-action");
        await handleOrderAction(orderId, actionType);
      });
    });

    // Bind details buttons
    el.ordersListContainer.querySelectorAll(".btn-order-details").forEach((btn) => {
      btn.addEventListener("click", () => {
        const orderId = Number(btn.getAttribute("data-order-id"));
        openOrderDetailsModal(orderId);
      });
    });
  }

  async function handleOrderAction(orderId, actionType) {
    let targetStatus = "";
    if (actionType === "accept") targetStatus = "accepted";
    else if (actionType === "reject") targetStatus = "rejected";
    else if (actionType === "prepare") targetStatus = "preparing";
    else if (actionType === "ready") targetStatus = "ready_for_pickup";

    if (!targetStatus) return;

    if (actionType === "reject") {
      const order = state.orders.find((o) => o.order_id === orderId);
      const isPaid = order && String(order.payment_status || "").toLowerCase() === "paid";
      let msg = `Are you sure you want to REJECT Order #${orderId}? This cannot be undone.`;
      if (isPaid) {
        msg = `Order #${orderId} was successfully PAID (${formatRupees(order.total_amount)}).\n\nRejecting this order will automatically initiate a simulated FULL REFUND of ${formatRupees(order.total_amount)} to the customer.\n\nProceed with rejection and refund?`;
      }
      const confirmed = window.confirm(msg);
      if (!confirmed) return;
    }

    try {
      const res = await api.updateOrderStatus(orderId, state.sellerId, targetStatus);
      showToast(res.message || `Order #${orderId} marked as ${targetStatus}`, "success");
      await loadDashboard();
    } catch (err) {
      showToast(`Action failed: ${err.message}`, "error");
    }
  }

  function openOrderDetailsModal(orderId) {
    const order = state.orders.find((o) => o.order_id === orderId);
    if (!order) return;

    el.detailOrderId.textContent = order.order_id;
    el.detailOrderBody.innerHTML = `
      <div style="display: flex; justify-content: space-between; border-bottom: 1px solid var(--seller-border); padding-bottom: 0.75rem; margin-bottom: 0.75rem;">
        <div>
          <span style="color: var(--seller-text-muted); font-size: 0.8rem; display: block;">Customer</span>
          <strong>${escapeHtml(order.customer_name)}</strong> (ID: ${order.customer_id})
        </div>
        <div style="text-align: right;">
          <span style="color: var(--seller-text-muted); font-size: 0.8rem; display: block;">Status</span>
          <span class="status-badge ${order.order_status}">${escapeHtml(order.order_status)}</span>
        </div>
      </div>

      <div style="margin-bottom: 1rem;">
        <span style="color: var(--seller-text-muted); font-size: 0.8rem; display: block; margin-bottom: 0.35rem;">Point-in-Time Order Snapshot</span>
        <div style="background: var(--seller-surface-alt); border-radius: var(--seller-radius-sm); padding: 0.75rem;">
          ${order.items
            .map(
              (it) => `
            <div style="display: flex; justify-content: space-between; padding: 0.35rem 0; border-bottom: 1px dashed var(--seller-border); font-size: 0.9rem;">
              <span>${escapeHtml(it.name)} &times; ${it.quantity}</span>
              <span>${formatRupees(it.subtotal)} (@ ${formatRupees(it.unit_price)})</span>
            </div>
          `
            )
            .join("")}
          <div style="display: flex; justify-content: space-between; padding-top: 0.6rem; font-weight: 800; font-size: 1rem; color: var(--seller-primary);">
            <span>Total Bill:</span>
            <span>${formatRupees(order.total_amount)}</span>
          </div>
        </div>
      </div>

      <div>
        <span style="color: var(--seller-text-muted); font-size: 0.8rem; display: block; margin-bottom: 0.35rem;">Payment Verification</span>
        <div style="background: var(--seller-surface-alt); border-radius: var(--seller-radius-sm); padding: 0.75rem; font-size: 0.85rem;">
          <div style="display: flex; justify-content: space-between; margin-bottom: 0.25rem;">
            <span>Payment Status:</span>
            <strong>${escapeHtml(order.payment_status.toUpperCase())}</strong>
          </div>
          ${
            order.payment?.transaction_ref
              ? `
            <div style="display: flex; justify-content: space-between; margin-bottom: 0.25rem;">
              <span>Transaction Ref:</span>
              <code style="color: var(--seller-primary);">${escapeHtml(order.payment.transaction_ref)}</code>
            </div>
          `
              : ""
          }
          <div style="display: flex; justify-content: space-between;">
            <span>Order Placed:</span>
            <span>${escapeHtml(order.created_at || "N/A")}</span>
          </div>
        </div>
      </div>

      ${
        order.refund
          ? `
        <div style="margin-top: 1rem;">
          <span style="color: var(--seller-text-muted); font-size: 0.8rem; display: block; margin-bottom: 0.35rem;">Simulated Refund Record</span>
          <div style="background: var(--seller-surface-alt); border-radius: var(--seller-radius-sm); padding: 0.75rem; font-size: 0.85rem; border-left: 3px solid var(--seller-purple);">
            <div style="display: flex; justify-content: space-between; margin-bottom: 0.25rem;">
              <span>Refund Status:</span>
              <strong>${escapeHtml(order.refund.refund_status.toUpperCase())}</strong>
            </div>
            <div style="display: flex; justify-content: space-between; margin-bottom: 0.25rem;">
              <span>Refund Amount:</span>
              <strong>${formatRupees(order.refund.amount)}</strong>
            </div>
            ${
              order.refund.transaction_ref
                ? `
              <div style="display: flex; justify-content: space-between; margin-bottom: 0.25rem;">
                <span>Refund Ref:</span>
                <code style="color: var(--seller-purple);">${escapeHtml(order.refund.transaction_ref)}</code>
              </div>
            `
                : ""
            }
            ${
              order.refund.reason
                ? `
              <div style="display: flex; justify-content: space-between; margin-bottom: 0.25rem;">
                <span>Reason:</span>
                <span>${escapeHtml(order.refund.reason)}</span>
              </div>
            `
                : ""
            }
            <div style="display: flex; justify-content: space-between;">
              <span>Attempts:</span>
              <span>${order.refund.attempt_count || 1}</span>
            </div>
          </div>
        </div>
      `
          : ""
      }
    `;

    el.modalOrderDetails.classList.add("open");
  }

  // -------------------------------------------------------------------
  // Menu Management
  // -------------------------------------------------------------------
  async function loadMenu() {
    try {
      const menu = await api.getMenu(state.sellerId);
      state.menu = menu;
      renderMenu(menu);
    } catch (err) {
      el.menuTableBody.innerHTML = `
        <tr>
          <td colspan="5" class="empty-state" style="color: var(--seller-red);">
            Failed to load menu items: ${escapeHtml(err.message)}
          </td>
        </tr>
      `;
    }
  }

  function renderMenu(items) {
    if (!items || items.length === 0) {
      el.menuTableBody.innerHTML = `
        <tr>
          <td colspan="5" class="empty-state">
            <p style="font-weight: 700; color: var(--seller-text);">No menu items found</p>
            <p>Click "Add Menu Item" above to add your first kitchen specialty!</p>
          </td>
        </tr>
      `;
      return;
    }

    el.menuTableBody.innerHTML = items
      .map((item) => {
        const isAvail = Number(item.is_available) === 1;
        return `
          <tr data-item-id="${item.item_id}">
            <td style="color: var(--seller-text-muted); font-weight: 700;">#${item.item_id}</td>
            <td>
              <div class="item-name-cell">${escapeHtml(item.name)}</div>
              <div class="item-desc-cell">${escapeHtml(item.description || "No description provided.")}</div>
            </td>
            <td>
              <span class="price-badge">${formatRupees(item.price)}</span>
            </td>
            <td>
              <label class="avail-label">
                <label class="switch">
                  <input type="checkbox" class="toggle-avail-input" data-item-id="${item.item_id}" ${isAvail ? "checked" : ""}>
                  <span class="slider"></span>
                </label>
                <span class="avail-text" style="color: ${isAvail ? "var(--seller-green)" : "var(--seller-red)"};">
                  ${isAvail ? "Available" : "Sold Out"}
                </span>
              </label>
            </td>
            <td style="text-align: right;">
              <button class="btn-edit-item" data-item-id="${item.item_id}">
                Edit Item
              </button>
            </td>
          </tr>
        `;
      })
      .join("");

    // Bind availability toggles
    el.menuTableBody.querySelectorAll(".toggle-avail-input").forEach((toggle) => {
      toggle.addEventListener("change", async (e) => {
        const itemId = Number(toggle.getAttribute("data-item-id"));
        const newStatus = toggle.checked;
        try {
          await api.updateItemAvailability(itemId, state.sellerId, newStatus);
          showToast(`Item #${itemId} marked as ${newStatus ? "Available" : "Sold Out"}`, "success");
          await loadDashboard();
        } catch (err) {
          toggle.checked = !newStatus; // revert
          showToast(`Failed to update availability: ${err.message}`, "error");
        }
      });
    });

    // Bind Edit buttons
    el.menuTableBody.querySelectorAll(".btn-edit-item").forEach((btn) => {
      btn.addEventListener("click", () => {
        const itemId = Number(btn.getAttribute("data-item-id"));
        const item = state.menu.find((m) => m.item_id === itemId);
        if (!item) return;

        el.editItemId.value = item.item_id;
        el.editItemName.value = item.name;
        el.editItemPrice.value = item.price;
        el.editItemDesc.value = item.description || "";
        el.editItemAvailable.checked = Number(item.is_available) === 1;
        el.editAvailLabelText.textContent = el.editItemAvailable.checked ? "Available" : "Sold Out";
        el.modalEditItem.classList.add("open");
      });
    });
  }

  // -------------------------------------------------------------------
  // Event Bindings
  // -------------------------------------------------------------------
  function setupEventListeners() {
    // Identity Selector
    el.identitySelect.addEventListener("change", (e) => {
      state.sellerId = Number(e.target.value);
      showToast(`Switched active seller identity to ID: ${state.sellerId}`, "info");
      loadDashboard();
    });

    // Refresh Dashboard Button
    el.btnRefresh.addEventListener("click", () => {
      showToast("Refreshing orders and menu from backend...", "info");
      loadDashboard();
    });

    // Tabs
    el.tabBtnOrders.addEventListener("click", () => switchTab("orders"));
    el.tabBtnMenu.addEventListener("click", () => switchTab("menu"));

    function switchTab(tabName) {
      state.activeTab = tabName;
      if (tabName === "orders") {
        el.tabBtnOrders.classList.add("active");
        el.tabBtnMenu.classList.remove("active");
        el.tabContentOrders.classList.add("active");
        el.tabContentMenu.classList.remove("active");
      } else {
        el.tabBtnMenu.classList.add("active");
        el.tabBtnOrders.classList.remove("active");
        el.tabContentMenu.classList.add("active");
        el.tabContentOrders.classList.remove("active");
      }
    }

    // Order Filters
    el.filterPills.forEach((pill) => {
      pill.addEventListener("click", () => {
        el.filterPills.forEach((p) => p.classList.remove("active"));
        pill.classList.add("active");
        state.activeFilter = pill.getAttribute("data-filter");
        loadOrders();
      });
    });

    // Modal Close buttons
    document.querySelectorAll("[data-close-modal]").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".modal-overlay").forEach((m) => m.classList.remove("open"));
      });
    });

    // Open Add Item Modal
    el.btnOpenAddItem.addEventListener("click", () => {
      el.formAddItem.reset();
      el.addItemAvailable.checked = true;
      el.modalAddItem.classList.add("open");
    });

    // Add Menu Item Submit
    el.formAddItem.addEventListener("submit", async (e) => {
      e.preventDefault();
      const name = el.addItemName.value.trim();
      const price = parseFloat(el.addItemPrice.value);
      const description = el.addItemDesc.value.trim();
      const isAvailable = el.addItemAvailable.checked ? 1 : 0;

      if (!name) {
        showToast("Food item name cannot be empty", "error");
        return;
      }
      if (isNaN(price) || price <= 0) {
        showToast("Price must be a positive number greater than 0", "error");
        return;
      }

      try {
        const res = await api.addMenuItem(state.sellerId, {
          name: name,
          price: price,
          description: description,
          is_available: isAvailable,
        });
        showToast(`Item '${res.name}' created successfully!`, "success");
        el.modalAddItem.classList.remove("open");
        await loadDashboard();
      } catch (err) {
        showToast(`Failed to add item: ${err.message}`, "error");
      }
    });

    // Edit Menu Item Submit
    el.formEditItem.addEventListener("submit", async (e) => {
      e.preventDefault();
      const itemId = Number(el.editItemId.value);
      const name = el.editItemName.value.trim();
      const price = parseFloat(el.editItemPrice.value);
      const description = el.editItemDesc.value.trim();
      const isAvailable = el.editItemAvailable.checked ? 1 : 0;

      if (!name) {
        showToast("Food item name cannot be empty", "error");
        return;
      }
      if (isNaN(price) || price <= 0) {
        showToast("Price must be a positive number greater than 0", "error");
        return;
      }

      try {
        const res = await api.updateMenuItem(itemId, state.sellerId, {
          name: name,
          price: price,
          description: description,
          is_available: isAvailable,
        });
        showToast(`Item '${res.name}' updated successfully!`, "success");
        el.modalEditItem.classList.remove("open");
        await loadDashboard();
      } catch (err) {
        showToast(`Failed to update item: ${err.message}`, "error");
      }
    });

    el.editItemAvailable.addEventListener("change", (e) => {
      el.editAvailLabelText.textContent = e.target.checked ? "Available" : "Sold Out";
    });
  }

  // -------------------------------------------------------------------
  // Initialization
  // -------------------------------------------------------------------
  async function init() {
    setupEventListeners();
    await updateBackendHealth();
    await loadDashboard();

    // Auto-poll health every 10 seconds
    setInterval(updateBackendHealth, 10000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
