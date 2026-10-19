/**
 * web/app.js
 * ==========
 * Mini Swiggy Customer Web Interface.
 * Pure Vanilla JavaScript — zero external dependencies.
 *
 * Communicates with the Python HTTP Gateway (127.0.0.1:8000), which forwards
 * NDJSON messages to the low-level TCP Socket Server (127.0.0.1:5000).
 */

(function () {
  "use strict";

  // -------------------------------------------------------------------
  // Application State
  // -------------------------------------------------------------------
  const state = {
    customerId: 1, // Fixed development customer: Alice Customer (ID: 1)
    customerName: "Alice Customer (ID: 1)",
    restaurants: [],
    currentRestaurant: null,
    menuItems: [],
    cart: {
      restaurantId: null,
      restaurantName: "",
      items: {}, // item_id -> { item_id, name, price, quantity }
    },
    activeOrder: null, // order returned by backend during checkout
    pendingConflict: null, // holds item to add if user confirms restaurant switch
    currentView: "restaurants", // "restaurants" | "menu" | "orders"
  };

  const STORAGE_KEY_CART = "miniswiggy_cart_v2";

  // -------------------------------------------------------------------
  // DOM Elements
  // -------------------------------------------------------------------
  const elements = {
    // Nav & Brand
    navBrand: document.getElementById("nav-brand"),
    backendStatusBadge: document.getElementById("backend-status-badge"),
    backendStatusText: document.getElementById("backend-status-text"),
    statusDot: document.querySelector(".status-dot"),
    btnNavOrders: document.getElementById("btn-nav-orders"),
    btnNavCart: document.getElementById("btn-nav-cart"),
    cartItemCount: document.getElementById("cart-item-count"),
    toastContainer: document.getElementById("toast-container"),

    // Views
    viewRestaurants: document.getElementById("view-restaurants"),
    viewMenu: document.getElementById("view-menu"),
    viewOrders: document.getElementById("view-orders"),

    // Restaurants View
    restaurantsList: document.getElementById("restaurants-list"),
    btnRefreshRestaurants: document.getElementById("btn-refresh-restaurants"),

    // Menu View
    btnBackToRestaurants: document.getElementById("btn-back-to-restaurants"),
    restaurantDetailHeader: document.getElementById("restaurant-detail-header"),
    menuItemsList: document.getElementById("menu-items-list"),

    // Orders View
    btnBackFromOrders: document.getElementById("btn-back-from-orders"),
    btnRefreshOrders: document.getElementById("btn-refresh-orders"),
    ordersHistoryList: document.getElementById("orders-history-list"),

    // Cart Drawer
    cartDrawerOverlay: document.getElementById("cart-drawer-overlay"),
    cartDrawer: document.getElementById("cart-drawer"),
    btnCloseCart: document.getElementById("btn-close-cart"),
    cartRestaurantName: document.getElementById("cart-restaurant-name"),
    cartItemsContainer: document.getElementById("cart-items-container"),
    cartSubtotalAmount: document.getElementById("cart-subtotal-amount"),
    cartTotalAmount: document.getElementById("cart-total-amount"),
    btnClearCart: document.getElementById("btn-clear-cart"),
    btnProceedCheckout: document.getElementById("btn-proceed-checkout"),

    // Checkout Modal
    checkoutModalOverlay: document.getElementById("checkout-modal-overlay"),
    checkoutModal: document.getElementById("checkout-modal"),
    btnCloseCheckout: document.getElementById("btn-close-checkout"),
    checkoutStepReview: document.getElementById("checkout-step-review"),
    checkoutStepPayment: document.getElementById("checkout-step-payment"),
    checkoutStepConfirmation: document.getElementById("checkout-step-confirmation"),
    checkoutItemsSummary: document.getElementById("checkout-items-summary"),
    checkoutEstimatedTotal: document.getElementById("checkout-estimated-total"),
    btnSubmitOrder: document.getElementById("btn-submit-order"),

    // Payment Step
    payOrderId: document.getElementById("pay-order-id"),
    payOrderStatus: document.getElementById("pay-order-status"),
    payServerAmount: document.getElementById("pay-server-amount"),
    btnConfirmPayment: document.getElementById("btn-confirm-payment"),

    // Confirmation Step
    confirmationBanner: document.getElementById("confirmation-banner"),
    confirmationTitle: document.getElementById("confirmation-title"),
    confirmationSubtitle: document.getElementById("confirmation-subtitle"),
    confirmationReceiptContent: document.getElementById("confirmation-receipt-content"),
    btnConfirmViewOrders: document.getElementById("btn-confirm-view-orders"),
    btnConfirmBackHome: document.getElementById("btn-confirm-back-home"),

    // Conflict Modal
    conflictModalOverlay: document.getElementById("conflict-modal-overlay"),
    conflictCurrentRestaurant: document.getElementById("conflict-current-restaurant"),
    conflictNewRestaurant: document.getElementById("conflict-new-restaurant"),
    btnCloseConflict: document.getElementById("btn-close-conflict"),
    btnConflictCancel: document.getElementById("btn-conflict-cancel"),
    btnConflictConfirm: document.getElementById("btn-conflict-confirm"),

    // Receipt Detail Modal
    receiptModalOverlay: document.getElementById("receipt-modal-overlay"),
    detailReceiptId: document.getElementById("detail-receipt-id"),
    detailReceiptBody: document.getElementById("detail-receipt-body"),
    btnCloseReceipt: document.getElementById("btn-close-receipt"),
    btnDismissReceipt: document.getElementById("btn-dismiss-receipt"),
  };

  // -------------------------------------------------------------------
  // Utility Functions
  // -------------------------------------------------------------------
  function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function formatRupees(amount) {
    const num = Number(amount) || 0;
    return `₹${num.toFixed(2)}`;
  }

  function showToast(message, type = "info") {
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    elements.toastContainer.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = "0";
      toast.style.transform = "translateY(10px)";
      toast.style.transition = "all 0.25s ease-out";
      setTimeout(() => toast.remove(), 250);
    }, 3500);
  }

  // -------------------------------------------------------------------
  // API Client (Communicates with HTTP Gateway at 127.0.0.1:8000)
  // -------------------------------------------------------------------
  const api = {
    async request(url, options = {}) {
      try {
        const response = await fetch(url, {
          headers: {
            "Content-Type": "application/json",
            ...options.headers,
          },
          ...options,
        });

        const data = await response.json().catch(() => ({}));

        if (!response.ok) {
          const errMsg = data.error?.message || `HTTP Error ${response.status}`;
          const errCode = data.error?.code || "HTTP_ERROR";
          const err = new Error(errMsg);
          err.code = errCode;
          err.status = response.status;
          err.data = data;
          throw err;
        }

        return data;
      } catch (err) {
        if (!err.status) {
          // Network failure reaching HTTP gateway
          err.code = "GATEWAY_UNREACHABLE";
          err.message = "Could not connect to HTTP gateway at 127.0.0.1:8000.";
        }
        throw err;
      }
    },

    checkHealth() {
      return this.request("/api/health");
    },

    getRestaurants() {
      return this.request("/api/restaurants");
    },

    getMenu(restaurantId) {
      return this.request(`/api/restaurants/${restaurantId}/menu`);
    },

    createOrder(payload) {
      return this.request("/api/orders", {
        method: "POST",
        body: jsonSerialize(payload),
      });
    },

    simulatePayment(orderId, payload) {
      return this.request(`/api/orders/${orderId}/payment`, {
        method: "POST",
        body: jsonSerialize(payload),
      });
    },

    getOrder(orderId, customerId = state.customerId) {
      return this.request(`/api/orders/${orderId}?customer_id=${customerId}`);
    },

    getOrderHistory(customerId = state.customerId) {
      return this.request(`/api/customers/${customerId}/orders`);
    },
  };

  function jsonSerialize(obj) {
    return JSON.stringify(obj);
  }

  // -------------------------------------------------------------------
  // Backend Connection Status Monitoring
  // -------------------------------------------------------------------
  async function checkBackendStatus() {
    try {
      const res = await api.checkHealth();
      if (res.ok && res.tcp_backend === "connected") {
        elements.statusDot.className = "status-dot status-connected";
        elements.backendStatusText.textContent = "TCP Connected (5000)";
      } else {
        elements.statusDot.className = "status-dot status-disconnected";
        elements.backendStatusText.textContent = "TCP Disconnected";
      }
    } catch {
      elements.statusDot.className = "status-dot status-disconnected";
      elements.backendStatusText.textContent = "Gateway Offline";
    }
  }

  // -------------------------------------------------------------------
  // Navigation & View Switching
  // -------------------------------------------------------------------
  function switchView(viewName) {
    state.currentView = viewName;
    elements.viewRestaurants.classList.remove("active");
    elements.viewMenu.classList.remove("active");
    elements.viewOrders.classList.remove("active");

    if (viewName === "restaurants") {
      elements.viewRestaurants.classList.add("active");
      loadRestaurants();
    } else if (viewName === "menu") {
      elements.viewMenu.classList.add("active");
    } else if (viewName === "orders") {
      elements.viewOrders.classList.add("active");
      loadOrderHistory();
    }
  }

  // -------------------------------------------------------------------
  // View 1: Restaurant Browsing
  // -------------------------------------------------------------------
  async function loadRestaurants() {
    elements.restaurantsList.innerHTML = `
      <div class="loading-state">
        <div class="spinner"></div>
        <p>Fetching kitchens from TCP backend...</p>
      </div>
    `;

    try {
      const res = await api.getRestaurants();
      state.restaurants = res.data || [];
      renderRestaurants();
    } catch (err) {
      elements.restaurantsList.innerHTML = `
        <div class="empty-state">
          <p style="color: var(--danger); font-weight: 700; margin-bottom: 0.5rem;">Could not load restaurants</p>
          <p style="font-size: 0.85rem; margin-bottom: 1rem;">${escapeHtml(err.message)}</p>
          <button class="btn-secondary btn-sm" id="btn-retry-restaurants">Retry Connection</button>
        </div>
      `;
      document.getElementById("btn-retry-restaurants")?.addEventListener("click", loadRestaurants);
    }
  }

  function renderRestaurants() {
    if (!state.restaurants.length) {
      elements.restaurantsList.innerHTML = `
        <div class="empty-state">
          <p>No restaurants found in database.</p>
        </div>
      `;
      return;
    }

    elements.restaurantsList.innerHTML = state.restaurants
      .map((r) => {
        const isOpen = Boolean(r.is_open);
        const isApproved = r.approval_status === "approved";
        const canOrder = isOpen && isApproved;
        const bannerClass = r.name.toLowerCase().includes("hyderabad")
          ? "hyderabad"
          : r.name.toLowerCase().includes("dosa")
          ? "dosa"
          : "pizza";

        return `
          <div class="restaurant-card ${!canOrder ? "closed" : ""}" data-id="${r.restaurant_id}">
            <div class="restaurant-banner-img ${bannerClass}">
              <span>${escapeHtml(r.name)}</span>
              <span class="card-status-badge ${isOpen ? "badge-open" : "badge-closed"}">
                ${isOpen ? "Open" : "Closed"}
              </span>
            </div>
            <div class="restaurant-body">
              <div>
                <h3 class="restaurant-title">${escapeHtml(r.name)}</h3>
                <p class="restaurant-desc">${escapeHtml(r.description || "Authentic culinary specialities.")}</p>
              </div>
              <div class="restaurant-footer">
                <span class="restaurant-meta">${isApproved ? "Approved Kitchen" : "Pending Approval"}</span>
                ${
                  canOrder
                    ? `<button class="btn-primary btn-sm btn-select-restaurant" data-id="${r.restaurant_id}">View Menu</button>`
                    : `<button class="btn-secondary btn-sm" disabled>Currently Closed</button>`
                }
              </div>
            </div>
          </div>
        `;
      })
      .join("");

    // Bind click events
    elements.restaurantsList.querySelectorAll(".btn-select-restaurant").forEach((btn) => {
      btn.addEventListener("click", () => {
        const rId = Number(btn.getAttribute("data-id"));
        openRestaurantMenu(rId);
      });
    });
  }

  // -------------------------------------------------------------------
  // View 2: Restaurant Menu
  // -------------------------------------------------------------------
  async function openRestaurantMenu(restaurantId) {
    const restaurant = state.restaurants.find((r) => r.restaurant_id === restaurantId);
    if (!restaurant) return;

    state.currentRestaurant = restaurant;
    switchView("menu");

    elements.restaurantDetailHeader.innerHTML = `
      <div class="rest-detail-info">
        <h1>${escapeHtml(restaurant.name)}</h1>
        <p class="rest-detail-desc">${escapeHtml(restaurant.description || "")}</p>
      </div>
      <div>
        <span class="card-status-badge ${restaurant.is_open ? "badge-open" : "badge-closed"}">
          ${restaurant.is_open ? "Accepting Orders" : "Closed"}
        </span>
      </div>
    `;

    elements.menuItemsList.innerHTML = `
      <div class="loading-state">
        <div class="spinner"></div>
        <p>Fetching menu from TCP backend...</p>
      </div>
    `;

    try {
      const res = await api.getMenu(restaurantId);
      state.menuItems = res.data || [];
      renderMenuItems();
    } catch (err) {
      elements.menuItemsList.innerHTML = `
        <div class="empty-state">
          <p style="color: var(--danger); font-weight: 700; margin-bottom: 0.5rem;">Could not load menu</p>
          <p style="font-size: 0.85rem;">${escapeHtml(err.message)}</p>
        </div>
      `;
    }
  }

  function renderMenuItems() {
    if (!state.menuItems.length) {
      elements.menuItemsList.innerHTML = `
        <div class="empty-state">
          <p>No menu items available for this restaurant.</p>
        </div>
      `;
      return;
    }

    elements.menuItemsList.innerHTML = state.menuItems
      .map((item) => {
        const isAvailable = Boolean(item.is_available);
        const inCartQty = getCartItemQuantity(item.item_id);

        return `
          <div class="menu-card" data-item-id="${item.item_id}">
            <div class="menu-info">
              <h4 class="menu-item-name">${escapeHtml(item.name)}</h4>
              <p class="menu-item-desc">${escapeHtml(item.description || "")}</p>
              <div class="menu-item-price">${formatRupees(item.price)}</div>
            </div>
            <div class="menu-action">
              ${
                !isAvailable
                  ? `<button class="btn-secondary btn-sm" disabled>Sold Out</button>`
                  : inCartQty > 0
                  ? `
                    <div class="qty-control">
                      <button class="qty-btn btn-qty-minus" data-item-id="${item.item_id}">-</button>
                      <span class="qty-display">${inCartQty}</span>
                      <button class="qty-btn btn-qty-plus" data-item-id="${item.item_id}">+</button>
                    </div>
                  `
                  : `<button class="btn-primary btn-sm btn-add-item" data-item-id="${item.item_id}">+ Add</button>`
              }
            </div>
          </div>
        `;
      })
      .join("");

    // Bind event handlers for Add / +/- controls
    elements.menuItemsList.querySelectorAll(".btn-add-item").forEach((btn) => {
      btn.addEventListener("click", () => {
        const itemId = Number(btn.getAttribute("data-item-id"));
        handleAddToCart(itemId);
      });
    });

    elements.menuItemsList.querySelectorAll(".btn-qty-plus").forEach((btn) => {
      btn.addEventListener("click", () => {
        const itemId = Number(btn.getAttribute("data-item-id"));
        handleAddToCart(itemId);
      });
    });

    elements.menuItemsList.querySelectorAll(".btn-qty-minus").forEach((btn) => {
      btn.addEventListener("click", () => {
        const itemId = Number(btn.getAttribute("data-item-id"));
        handleDecrementCartItem(itemId);
      });
    });
  }

  // -------------------------------------------------------------------
  // Cart State & Single-Restaurant Constraint
  // -------------------------------------------------------------------
  function getCartItemQuantity(itemId) {
    if (state.cart.restaurantId !== state.currentRestaurant?.restaurant_id) {
      return 0;
    }
    return state.cart.items[itemId]?.quantity || 0;
  }

  function handleAddToCart(itemId) {
    const item = state.menuItems.find((i) => i.item_id === itemId);
    if (!item) return;

    // Check single-restaurant constraint
    if (state.cart.restaurantId && state.cart.restaurantId !== state.currentRestaurant.restaurant_id) {
      // Conflict! Ask user before discarding existing cart
      state.pendingConflict = { item, restaurant: state.currentRestaurant };
      elements.conflictCurrentRestaurant.textContent = state.cart.restaurantName;
      elements.conflictNewRestaurant.textContent = state.currentRestaurant.name;
      elements.conflictModalOverlay.classList.add("open");
      return;
    }

    // Set restaurant on cart if empty
    if (!state.cart.restaurantId) {
      state.cart.restaurantId = state.currentRestaurant.restaurant_id;
      state.cart.restaurantName = state.currentRestaurant.name;
    }

    // Increment item
    if (state.cart.items[itemId]) {
      state.cart.items[itemId].quantity += 1;
    } else {
      state.cart.items[itemId] = {
        item_id: item.item_id,
        name: item.name,
        price: item.price,
        quantity: 1,
      };
    }

    saveCartToStorage();
    updateCartUI();
    renderMenuItems(); // Update button to +/- counter
    showToast(`Added ${item.name} to cart`, "success");
  }

  function handleDecrementCartItem(itemId) {
    if (!state.cart.items[itemId]) return;

    state.cart.items[itemId].quantity -= 1;
    if (state.cart.items[itemId].quantity <= 0) {
      delete state.cart.items[itemId];
    }

    // If cart is now empty, reset restaurant
    if (Object.keys(state.cart.items).length === 0) {
      state.cart.restaurantId = null;
      state.cart.restaurantName = "";
    }

    saveCartToStorage();
    updateCartUI();
    renderMenuItems();
  }

  function clearCart() {
    state.cart.restaurantId = null;
    state.cart.restaurantName = "";
    state.cart.items = {};
    saveCartToStorage();
    updateCartUI();
    if (state.currentView === "menu") {
      renderMenuItems();
    }
    showToast("Cart cleared", "info");
  }

  function updateCartUI() {
    const items = Object.values(state.cart.items);
    const totalCount = items.reduce((sum, it) => sum + it.quantity, 0);
    const subtotal = items.reduce((sum, it) => sum + it.price * it.quantity, 0);

    elements.cartItemCount.textContent = totalCount;
    elements.cartRestaurantName.textContent = state.cart.restaurantName || "No items in cart";
    elements.cartSubtotalAmount.textContent = formatRupees(subtotal);
    elements.cartTotalAmount.textContent = formatRupees(subtotal);

    if (items.length === 0) {
      elements.cartItemsContainer.innerHTML = `
        <div class="empty-state">
          <p style="font-weight: 600;">Your cart is empty</p>
          <p style="font-size: 0.8rem;">Add delicious food items from a restaurant menu.</p>
        </div>
      `;
      elements.btnProceedCheckout.disabled = true;
    } else {
      elements.cartItemsContainer.innerHTML = items
        .map(
          (it) => `
          <div class="cart-item-row">
            <div class="cart-item-info">
              <div class="cart-item-name">${escapeHtml(it.name)}</div>
              <div class="cart-item-unit-price">${formatRupees(it.price)} each</div>
            </div>
            <div class="qty-control">
              <button class="qty-btn btn-cart-minus" data-id="${it.item_id}">-</button>
              <span class="qty-display">${it.quantity}</span>
              <button class="qty-btn btn-cart-plus" data-id="${it.item_id}">+</button>
            </div>
            <div class="cart-item-subtotal">${formatRupees(it.price * it.quantity)}</div>
          </div>
        `
        )
        .join("");

      elements.btnProceedCheckout.disabled = false;

      // Bind events inside drawer
      elements.cartItemsContainer.querySelectorAll(".btn-cart-plus").forEach((b) => {
        b.addEventListener("click", () => {
          const id = Number(b.getAttribute("data-id"));
          state.cart.items[id].quantity += 1;
          saveCartToStorage();
          updateCartUI();
          if (state.currentView === "menu") renderMenuItems();
        });
      });

      elements.cartItemsContainer.querySelectorAll(".btn-cart-minus").forEach((b) => {
        b.addEventListener("click", () => {
          const id = Number(b.getAttribute("data-id"));
          handleDecrementCartItem(id);
        });
      });
    }
  }

  function saveCartToStorage() {
    try {
      const serialized = {
        restaurantId: state.cart.restaurantId,
        restaurantName: state.cart.restaurantName,
        items: Object.values(state.cart.items).map((i) => ({
          item_id: i.item_id,
          name: i.name,
          price: i.price,
          quantity: i.quantity,
        })),
      };
      localStorage.setItem(STORAGE_KEY_CART, JSON.stringify(serialized));
    } catch {
      // Storage unavailable or disabled
    }
  }

  function loadCartFromStorage() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY_CART);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (parsed && parsed.restaurantId && Array.isArray(parsed.items)) {
        state.cart.restaurantId = parsed.restaurantId;
        state.cart.restaurantName = parsed.restaurantName || "";
        state.cart.items = {};
        parsed.items.forEach((it) => {
          if (it.item_id && it.quantity > 0) {
            state.cart.items[it.item_id] = it;
          }
        });
        updateCartUI();
      }
    } catch {
      localStorage.removeItem(STORAGE_KEY_CART);
    }
  }

  // -------------------------------------------------------------------
  // View 3: Checkout & Simulated Payment Flow
  // -------------------------------------------------------------------
  function openCheckoutModal() {
    const items = Object.values(state.cart.items);
    if (!items.length) {
      showToast("Cannot checkout an empty cart", "warning");
      return;
    }

    // Close cart drawer
    elements.cartDrawerOverlay.classList.remove("open");

    // Populate review step
    elements.checkoutItemsSummary.innerHTML = items
      .map(
        (it) => `
        <div style="display: flex; justify-content: space-between; font-size: 0.85rem; padding: 0.25rem 0;">
          <span>${escapeHtml(it.name)} &times; ${it.quantity}</span>
          <span>${formatRupees(it.price * it.quantity)}</span>
        </div>
      `
      )
      .join("");

    const total = items.reduce((sum, it) => sum + it.price * it.quantity, 0);
    elements.checkoutEstimatedTotal.textContent = formatRupees(total);

    // Show Step 1
    showCheckoutStep("review");
    elements.btnSubmitOrder.disabled = false;
    elements.btnSubmitOrder.textContent = "Place Food Order";

    elements.checkoutModalOverlay.classList.add("open");
  }

  function showCheckoutStep(stepName) {
    elements.checkoutStepReview.classList.remove("active");
    elements.checkoutStepPayment.classList.remove("active");
    elements.checkoutStepConfirmation.classList.remove("active");

    if (stepName === "review") {
      elements.checkoutStepReview.classList.add("active");
    } else if (stepName === "payment") {
      elements.checkoutStepPayment.classList.add("active");
    } else if (stepName === "confirmation") {
      elements.checkoutStepConfirmation.classList.add("active");
    }
  }

  async function handleOrderSubmission() {
    const items = Object.values(state.cart.items).map((it) => ({
      item_id: it.item_id,
      quantity: it.quantity,
    }));

    if (!items.length) return;

    elements.btnSubmitOrder.disabled = true;
    elements.btnSubmitOrder.textContent = "Placing Order with Kitchen...";

    const orderPayload = {
      customer_id: state.customerId,
      restaurant_id: state.cart.restaurantId,
      items: items,
    };

    try {
      const res = await api.createOrder(orderPayload);
      const order = res.data;
      state.activeOrder = order;

      // Clear cart once order is registered on server
      clearCart();

      // Setup Payment Step
      elements.payOrderId.textContent = order.order_id;
      elements.payOrderStatus.textContent = `${order.order_status} · ${order.payment_status}`;
      elements.payServerAmount.textContent = formatRupees(order.total_amount);

      showCheckoutStep("payment");
      showToast(`Order #${order.order_id} placed! Proceed to payment.`, "success");
    } catch (err) {
      elements.btnSubmitOrder.disabled = false;
      elements.btnSubmitOrder.textContent = "Place Food Order";
      showToast(`Order failed: ${err.message}`, "error");
    }
  }

  async function handlePaymentSubmission() {
    if (!state.activeOrder) return;

    const orderId = state.activeOrder.order_id;
    const selectedMethod = document.querySelector('input[name="pay-method"]:checked')?.value || "simulated_upi";
    const simResult = document.querySelector('input[name="pay-sim-result"]:checked')?.value || "success";

    elements.btnConfirmPayment.disabled = true;
    elements.btnConfirmPayment.textContent = "Processing Simulated Payment...";

    const paymentPayload = {
      payment_method: selectedMethod,
      simulate_status: simResult === "failure" ? "failed" : "paid",
      simulate_failure: simResult === "failure",
    };

    try {
      const res = await api.simulatePayment(orderId, paymentPayload);
      const payData = res.data;

      if (payData.payment_status === "paid") {
        // Successful payment: fetch authoritative receipt
        showToast("Payment confirmed by backend!", "success");
        await displayOrderReceipt(orderId, "confirmation");
      } else {
        // Simulated failure
        elements.btnConfirmPayment.disabled = false;
        elements.btnConfirmPayment.textContent = "Retry Payment (Simulated)";
        showToast("Payment simulation failed as requested. Order is still unpaid.", "warning");
        elements.payOrderStatus.textContent = "placed · payment failed";
        elements.payOrderStatus.className = "info-status status-failed";
      }
    } catch (err) {
      elements.btnConfirmPayment.disabled = false;
      elements.btnConfirmPayment.textContent = "Pay Now (Simulated)";

      if (err.code === "PAYMENT_ALREADY_COMPLETED") {
        showToast("Order was already paid! Fetching receipt...", "info");
        await displayOrderReceipt(orderId, "confirmation");
      } else {
        showToast(`Payment error: ${err.message}`, "error");
      }
    }
  }

  async function displayOrderReceipt(orderId, targetContainer) {
    try {
      const res = await api.getOrder(orderId);
      const order = res.data;

      const html = `
        <div class="receipt-row">
          <span class="text-muted">Order ID:</span>
          <strong>#${order.order_id}</strong>
        </div>
        <div class="receipt-row">
          <span class="text-muted">Kitchen:</span>
          <strong>${escapeHtml(order.restaurant_name)}</strong>
        </div>
        <div class="receipt-row">
          <span class="text-muted">Customer:</span>
          <span>${escapeHtml(order.customer_name)}</span>
        </div>
        <div class="receipt-row">
          <span class="text-muted">Order Status:</span>
          <span class="info-status status-accepted">${escapeHtml(order.order_status)}</span>
        </div>
        <div class="receipt-row">
          <span class="text-muted">Payment Status:</span>
          <strong>${escapeHtml(order.payment_status)}</strong>
        </div>
        ${
          order.payment?.transaction_ref
            ? `
          <div class="receipt-row">
            <span class="text-muted">Transaction Ref:</span>
            <code style="font-size: 0.75rem;">${escapeHtml(order.payment.transaction_ref)}</code>
          </div>
        `
            : ""
        }
        <hr style="border: 0; border-top: 1px solid var(--border-color); margin: 0.75rem 0;">
        <h5 style="margin-bottom: 0.5rem; font-size: 0.9rem;">Items Snapshotted at Order Time:</h5>
        ${order.items
          .map(
            (it) => `
          <div class="receipt-row" style="font-size: 0.85rem;">
            <span>${escapeHtml(it.name)} &times; ${it.quantity}</span>
            <span>${formatRupees(it.subtotal)} (@ ${formatRupees(it.unit_price)})</span>
          </div>
        `
          )
          .join("")}
        <hr style="border: 0; border-top: 1px dashed var(--border-color); margin: 0.75rem 0;">
        <div class="receipt-row" style="font-size: 1.1rem; font-weight: 800;">
          <span>Total Paid:</span>
          <span style="color: var(--primary);">${formatRupees(order.total_amount)}</span>
        </div>
      `;

      if (targetContainer === "confirmation") {
        elements.confirmationReceiptContent.innerHTML = html;
        showCheckoutStep("confirmation");
      } else {
        elements.detailReceiptId.textContent = order.order_id;
        elements.detailReceiptBody.innerHTML = html;
        elements.receiptModalOverlay.classList.add("open");
      }
    } catch (err) {
      showToast(`Could not load receipt: ${err.message}`, "error");
    }
  }

  // -------------------------------------------------------------------
  // View 4: Order History
  // -------------------------------------------------------------------
  async function loadOrderHistory() {
    elements.ordersHistoryList.innerHTML = `
      <div class="loading-state">
        <div class="spinner"></div>
        <p>Retrieving past orders from TCP backend...</p>
      </div>
    `;

    try {
      const res = await api.getOrderHistory();
      const orders = res.data || [];
      renderOrderHistory(orders);
    } catch (err) {
      elements.ordersHistoryList.innerHTML = `
        <div class="empty-state">
          <p style="color: var(--danger); font-weight: 700; margin-bottom: 0.5rem;">Could not load order history</p>
          <p style="font-size: 0.85rem;">${escapeHtml(err.message)}</p>
        </div>
      `;
    }
  }

  function renderOrderHistory(orders) {
    if (!orders.length) {
      elements.ordersHistoryList.innerHTML = `
        <div class="empty-state">
          <p style="font-weight: 600; font-size: 1.1rem; margin-bottom: 0.5rem;">No past orders found</p>
          <p style="font-size: 0.85rem; margin-bottom: 1.25rem;">Browse available kitchens and place your first food order!</p>
          <button class="btn-primary" id="btn-empty-browse">Browse Kitchens</button>
        </div>
      `;
      document.getElementById("btn-empty-browse")?.addEventListener("click", () => switchView("restaurants"));
      return;
    }

    elements.ordersHistoryList.innerHTML = orders
      .map((ord) => {
        const isPaid = ord.payment_status === "paid";
        const statusClass = isPaid ? "status-accepted" : ord.payment_status === "failed" ? "status-failed" : "status-placed";

        return `
          <div class="order-history-card">
            <div>
              <div class="order-history-header">
                <span class="order-history-id">Order #${ord.order_id}</span>
                <span class="info-status ${statusClass}">${ord.order_status} · ${ord.payment_status}</span>
              </div>
              <div class="order-history-rest">${escapeHtml(ord.restaurant_name)}</div>
              <div class="order-history-date">${escapeHtml(ord.created_at || "Recent")}</div>
            </div>
            <div style="display: flex; align-items: center; gap: 1rem;">
              <span class="order-history-total">${formatRupees(ord.total_amount)}</span>
              <button class="btn-secondary btn-sm btn-view-receipt" data-order-id="${ord.order_id}">View Receipt</button>
            </div>
          </div>
        `;
      })
      .join("");

    elements.ordersHistoryList.querySelectorAll(".btn-view-receipt").forEach((btn) => {
      btn.addEventListener("click", () => {
        const oid = Number(btn.getAttribute("data-order-id"));
        displayOrderReceipt(oid, "modal");
      });
    });
  }

  // -------------------------------------------------------------------
  // Event Listeners & Binding
  // -------------------------------------------------------------------
  function setupEventListeners() {
    // Navigation Brand -> Home
    elements.navBrand.addEventListener("click", () => switchView("restaurants"));
    elements.navBrand.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") switchView("restaurants");
    });

    // Nav Orders & Cart
    elements.btnNavOrders.addEventListener("click", () => switchView("orders"));
    elements.btnNavCart.addEventListener("click", () => {
      elements.cartDrawerOverlay.classList.add("open");
    });
    elements.btnCloseCart.addEventListener("click", () => {
      elements.cartDrawerOverlay.classList.remove("open");
    });
    elements.cartDrawerOverlay.addEventListener("click", (e) => {
      if (e.target === elements.cartDrawerOverlay) {
        elements.cartDrawerOverlay.classList.remove("open");
      }
    });

    // View Switching Back buttons
    elements.btnBackToRestaurants.addEventListener("click", () => switchView("restaurants"));
    elements.btnBackFromOrders.addEventListener("click", () => switchView("restaurants"));
    elements.btnRefreshRestaurants.addEventListener("click", loadRestaurants);
    elements.btnRefreshOrders.addEventListener("click", loadOrderHistory);

    // Cart actions
    elements.btnClearCart.addEventListener("click", clearCart);
    elements.btnProceedCheckout.addEventListener("click", openCheckoutModal);

    // Checkout Modal
    elements.btnCloseCheckout.addEventListener("click", () => {
      elements.checkoutModalOverlay.classList.remove("open");
    });
    elements.btnSubmitOrder.addEventListener("click", handleOrderSubmission);
    elements.btnConfirmPayment.addEventListener("click", handlePaymentSubmission);
    elements.btnConfirmViewOrders.addEventListener("click", () => {
      elements.checkoutModalOverlay.classList.remove("open");
      switchView("orders");
    });
    elements.btnConfirmBackHome.addEventListener("click", () => {
      elements.checkoutModalOverlay.classList.remove("open");
      switchView("restaurants");
    });

    // Conflict Modal (Restaurant Switch Guard)
    elements.btnCloseConflict.addEventListener("click", () => {
      elements.conflictModalOverlay.classList.remove("open");
      state.pendingConflict = null;
    });
    elements.btnConflictCancel.addEventListener("click", () => {
      elements.conflictModalOverlay.classList.remove("open");
      state.pendingConflict = null;
    });
    elements.btnConflictConfirm.addEventListener("click", () => {
      if (state.pendingConflict) {
        clearCart();
        const { item } = state.pendingConflict;
        state.pendingConflict = null;
        elements.conflictModalOverlay.classList.remove("open");
        handleAddToCart(item.item_id);
      }
    });

    // Receipt Detail Modal
    elements.btnCloseReceipt.addEventListener("click", () => {
      elements.receiptModalOverlay.classList.remove("open");
    });
    elements.btnDismissReceipt.addEventListener("click", () => {
      elements.receiptModalOverlay.classList.remove("open");
    });
  }

  // -------------------------------------------------------------------
  // Initialization
  // -------------------------------------------------------------------
  function init() {
    setupEventListeners();
    loadCartFromStorage();
    checkBackendStatus();
    switchView("restaurants");

    // Periodic health check (every 10 seconds)
    setInterval(checkBackendStatus, 10000);
  }

  // Run on DOM Ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
