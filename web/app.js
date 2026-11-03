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
    user: null, // Populated from active session: { user_id, name, email, role, email_verified }
    customerId: null, // Dynamically populated from active session
    customerName: null,
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
    pendingPostLoginAction: null, // e.g. "checkout" or "orders"
    csrfToken: null, // CSRF protection token bound to session
  };

  const STORAGE_KEY_CART = "miniswiggy_cart_v2";

  // Cookie helper for CSRF token extraction
  function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(";").shift();
    return null;
  }

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

    // Auth Nav & Profile
    authNavLoggedOut: document.getElementById("auth-nav-logged-out"),
    btnNavLogin: document.getElementById("btn-nav-login"),
    btnNavRegister: document.getElementById("btn-nav-register"),
    authNavLoggedIn: document.getElementById("auth-nav-logged-in"),
    customerIdentityBadge: document.getElementById("customer-identity-badge"),
    currentCustomerAvatar: document.getElementById("current-customer-avatar"),
    currentCustomerName: document.getElementById("current-customer-name"),
    btnNavLogout: document.getElementById("btn-nav-logout"),

    // Auth Modal
    authModalOverlay: document.getElementById("auth-modal-overlay"),
    authModal: document.getElementById("auth-modal"),
    tabBtnLogin: document.getElementById("tab-btn-login"),
    tabBtnRegister: document.getElementById("tab-btn-register"),
    btnCloseAuth: document.getElementById("btn-close-auth"),

    // Login Form
    formLogin: document.getElementById("form-login"),
    loginEmail: document.getElementById("login-email"),
    loginPassword: document.getElementById("login-password"),
    loginErrorBox: document.getElementById("login-error-box"),
    loginSuccessBox: document.getElementById("login-success-box"),
    btnSubmitLogin: document.getElementById("btn-submit-login"),
    linkSwitchToRegister: document.getElementById("link-switch-to-register"),

    // Register Form
    formRegister: document.getElementById("form-register"),
    registerName: document.getElementById("register-name"),
    registerEmail: document.getElementById("register-email"),
    registerPassword: document.getElementById("register-password"),
    registerConfirmPassword: document.getElementById("register-confirm-password"),
    registerErrorBox: document.getElementById("register-error-box"),
    registerSuccessBox: document.getElementById("register-success-box"),
    btnSubmitRegister: document.getElementById("btn-submit-register"),
    linkSwitchToLogin: document.getElementById("link-switch-to-login"),

    // Verification Card
    registerVerificationCard: document.getElementById("register-verification-card"),
    verificationCardMsg: document.getElementById("verification-card-msg"),
    devLinkPreview: document.getElementById("dev-link-preview"),
    btnDevAutoVerify: document.getElementById("btn-dev-auto-verify"),
    btnBackToLoginAfterVerify: document.getElementById("btn-back-to-login-after-verify"),

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
    detailReceiptFooter: document.getElementById("detail-receipt-footer"),
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
        const method = (options.method || "GET").toUpperCase();
        const headers = {
          "Content-Type": "application/json",
          ...options.headers,
        };

        // Attach CSRF protection header for state-changing requests
        if (["POST", "PATCH", "PUT", "DELETE"].includes(method)) {
          const csrf = state.csrfToken || getCookie("csrf_token");
          if (csrf) {
            headers["X-CSRF-Token"] = csrf;
          }
        }

        const response = await fetch(url, {
          credentials: "include",
          headers,
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

    // V4.1 Authentication APIs
    register(payload) {
      return this.request("/api/auth/register", {
        method: "POST",
        body: jsonSerialize(payload),
      });
    },

    login(payload) {
      return this.request("/api/auth/login", {
        method: "POST",
        body: jsonSerialize(payload),
      });
    },

    logout() {
      return this.request("/api/auth/logout", {
        method: "POST",
        body: jsonSerialize({}),
      });
    },

    getMe() {
      return this.request("/api/auth/me");
    },

    verifyEmail(token) {
      return this.request(`/api/auth/verify-email?token=${encodeURIComponent(token)}`);
    },

    resendVerification(email) {
      return this.request("/api/auth/resend-verification", {
        method: "POST",
        body: jsonSerialize({ email }),
      });
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
      const q = customerId ? `?customer_id=${customerId}` : "";
      return this.request(`/api/orders/${orderId}${q}`);
    },

    getOrderHistory(customerId = state.customerId) {
      const path = customerId ? `/api/customers/${customerId}/orders` : `/api/customers/me/orders`;
      return this.request(path);
    },

    getRefund(orderId, customerId = state.customerId) {
      const q = customerId ? `?customer_id=${customerId}` : "";
      return this.request(`/api/orders/${orderId}/refund${q}`);
    },

    retryRefund(orderId, customerId = state.customerId) {
      const body = customerId ? { customer_id: customerId } : {};
      return this.request(`/api/orders/${orderId}/refund/retry`, {
        method: "POST",
        body: jsonSerialize(body),
      });
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
  // Authentication & Session Management (V4.1)
  // -------------------------------------------------------------------
  async function checkSession() {
    try {
      const res = await api.getMe();
      if (res && res.ok && res.data) {
        setUserSession(res.data, res.data.csrf_token || res.csrf_token);
      } else {
        clearUserSession();
      }
    } catch {
      clearUserSession();
    }
  }

  function setUserSession(user, csrfToken = null) {
    state.user = user;
    state.customerId = user.user_id;
    state.customerName = `${user.name} (ID: ${user.user_id})`;
    if (csrfToken) {
      state.csrfToken = csrfToken;
    } else if (user.csrf_token) {
      state.csrfToken = user.csrf_token;
    } else {
      state.csrfToken = getCookie("csrf_token");
    }

    if (elements.authNavLoggedOut) elements.authNavLoggedOut.style.display = "none";
    if (elements.authNavLoggedIn) elements.authNavLoggedIn.style.display = "flex";
    if (elements.currentCustomerName) elements.currentCustomerName.textContent = user.name;
    if (elements.currentCustomerAvatar) {
      elements.currentCustomerAvatar.textContent = (user.name || "U").charAt(0).toUpperCase();
    }

    // If order history is open, refresh
    if (state.currentView === "orders") {
      loadOrderHistory();
    }
  }

  function clearUserSession() {
    state.user = null;
    state.customerId = null;
    state.customerName = null;
    state.csrfToken = null;

    if (elements.authNavLoggedOut) elements.authNavLoggedOut.style.display = "flex";
    if (elements.authNavLoggedIn) elements.authNavLoggedIn.style.display = "none";
    if (elements.currentCustomerName) elements.currentCustomerName.textContent = "Guest";
    if (elements.currentCustomerAvatar) elements.currentCustomerAvatar.textContent = "?";
  }

  function openAuthModal(tab = "login") {
    switchAuthTab(tab);
    elements.authModalOverlay?.classList.add("open");
  }

  function closeAuthModal() {
    elements.authModalOverlay?.classList.remove("open");
    clearAuthAlerts();
  }

  function switchAuthTab(tab) {
    clearAuthAlerts();
    if (tab === "login") {
      elements.tabBtnLogin?.classList.add("active");
      elements.tabBtnRegister?.classList.remove("active");
      if (elements.formLogin) elements.formLogin.style.display = "block";
      if (elements.formRegister) elements.formRegister.style.display = "none";
      if (elements.registerVerificationCard) elements.registerVerificationCard.style.display = "none";
      setTimeout(() => elements.loginEmail?.focus(), 50);
    } else {
      elements.tabBtnLogin?.classList.remove("active");
      elements.tabBtnRegister?.classList.add("active");
      if (elements.formLogin) elements.formLogin.style.display = "none";
      if (elements.formRegister) elements.formRegister.style.display = "block";
      if (elements.registerVerificationCard) elements.registerVerificationCard.style.display = "none";
      setTimeout(() => elements.registerName?.focus(), 50);
    }
  }

  function showLoginError(msg) {
    if (elements.loginErrorBox) {
      elements.loginErrorBox.textContent = msg;
      elements.loginErrorBox.style.display = "block";
    }
  }

  function showLoginSuccess(msg) {
    if (elements.loginSuccessBox) {
      elements.loginSuccessBox.textContent = msg;
      elements.loginSuccessBox.style.display = "block";
    }
  }

  function showRegisterError(msg) {
    if (elements.registerErrorBox) {
      elements.registerErrorBox.textContent = msg;
      elements.registerErrorBox.style.display = "block";
    }
  }

  function clearAuthAlerts() {
    if (elements.loginErrorBox) elements.loginErrorBox.style.display = "none";
    if (elements.loginSuccessBox) elements.loginSuccessBox.style.display = "none";
    if (elements.registerErrorBox) elements.registerErrorBox.style.display = "none";
    if (elements.registerSuccessBox) elements.registerSuccessBox.style.display = "none";
  }

  async function handleLoginSubmit(e) {
    e.preventDefault();
    clearAuthAlerts();

    const email = elements.loginEmail.value.trim();
    const password = elements.loginPassword.value;

    if (!email || !password) {
      showLoginError("Email and password are required.");
      return;
    }

    elements.btnSubmitLogin.disabled = true;
    elements.btnSubmitLogin.textContent = "Logging in...";

    try {
      const res = await api.login({ email, password });
      if (res.ok) {
        setUserSession(res.data.user, res.data.csrf_token);
        showToast(`Welcome back, ${res.data.user.name}!`, "success");
        closeAuthModal();

        // Resume any pending action
        if (state.pendingPostLoginAction === "checkout") {
          state.pendingPostLoginAction = null;
          openCheckoutModal();
        } else if (state.pendingPostLoginAction === "orders") {
          state.pendingPostLoginAction = null;
          switchView("orders");
        }
      }
    } catch (err) {
      if (err.code === "EMAIL_NOT_VERIFIED") {
        showLoginError("Your email has not been verified yet. Check your terminal console or logs/dev_emails.jsonl for the verification link.");
      } else {
        showLoginError(err.message || "Invalid email or password.");
      }
    } finally {
      elements.btnSubmitLogin.disabled = false;
      elements.btnSubmitLogin.textContent = "Log In";
    }
  }

  async function handleRegisterSubmit(e) {
    e.preventDefault();
    clearAuthAlerts();

    const name = elements.registerName.value.trim();
    const email = elements.registerEmail.value.trim();
    const password = elements.registerPassword.value;
    const confirmPassword = elements.registerConfirmPassword.value;

    if (!name || !email || !password) {
      showRegisterError("All fields are required.");
      return;
    }

    if (password !== confirmPassword) {
      showRegisterError("Passwords do not match.");
      return;
    }

    if (password.length < 8) {
      showRegisterError("Password must be at least 8 characters long.");
      return;
    }

    elements.btnSubmitRegister.disabled = true;
    elements.btnSubmitRegister.textContent = "Creating Account...";

    try {
      const res = await api.register({ name, email, password, role: "customer" });
      if (res.ok) {
        const token = res.data.verification_token;
        showToast("Account created successfully! Verification required.", "success");

        if (elements.formRegister) elements.formRegister.style.display = "none";
        if (elements.registerVerificationCard) elements.registerVerificationCard.style.display = "block";
        if (elements.verificationCardMsg) {
          elements.verificationCardMsg.textContent = `Account created for ${email}. Please verify your email before logging in.`;
        }

        const devLink = `http://127.0.0.1:8000/api/auth/verify-email?token=${token}`;
        if (elements.devLinkPreview) {
          elements.devLinkPreview.innerHTML = `<code>${escapeHtml(devLink)}</code>`;
        }

        if (elements.btnDevAutoVerify) {
          elements.btnDevAutoVerify.onclick = async () => {
            elements.btnDevAutoVerify.disabled = true;
            elements.btnDevAutoVerify.textContent = "Verifying...";
            try {
              const vRes = await api.verifyEmail(token);
              if (vRes.ok) {
                showToast("Email verified successfully! You can now log in.", "success");
                switchAuthTab("login");
                elements.loginEmail.value = email;
                elements.loginPassword.value = password;
                showLoginSuccess("Email verified successfully! Log in to continue.");
              }
            } catch (vErr) {
              showToast(vErr.message || "Email verification failed.", "error");
              elements.btnDevAutoVerify.disabled = false;
              elements.btnDevAutoVerify.textContent = "Verify Email Now (Development Shortcut)";
            }
          };
        }

        if (elements.btnBackToLoginAfterVerify) {
          elements.btnBackToLoginAfterVerify.onclick = () => {
            switchAuthTab("login");
            elements.loginEmail.value = email;
          };
        }
      }
    } catch (err) {
      showRegisterError(err.message || "Registration failed.");
    } finally {
      elements.btnSubmitRegister.disabled = false;
      elements.btnSubmitRegister.textContent = "Register Account";
    }
  }

  async function handleLogout() {
    try {
      await api.logout();
      showToast("Logged out successfully.", "info");
    } catch (err) {
      console.warn("Logout error:", err);
    } finally {
      clearUserSession();
      if (state.currentView === "orders") {
        switchView("restaurants");
      }
    }
  }

  async function checkUrlParams() {
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get("token") || urlParams.get("verify_token");
    if (token) {
      try {
        const res = await api.verifyEmail(token);
        if (res.ok) {
          showToast("Email verified successfully! Please log in.", "success");
          openAuthModal("login");
          showLoginSuccess("Email verified successfully! Please log in.");
        }
      } catch (err) {
        showToast(err.message || "Verification token invalid or expired.", "error");
      }
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

    if (!state.user) {
      showToast("Please log in or register to place your food order.", "warning");
      state.pendingPostLoginAction = "checkout";
      openAuthModal("login");
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

  function isOrderPayEligible(order) {
    if (!order) return false;
    const payStatus = String(order.payment_status || "").toLowerCase().trim();
    const ordStatus = String(order.order_status || "").toLowerCase().trim();
    const isUnpaid = payStatus === "pending" || payStatus === "failed";
    const isNotTerminated = ordStatus !== "rejected" && ordStatus !== "cancelled";
    const isEligibleStatus = [
      "placed",
      "pending",
      "accepted",
      "preparing",
      "ready",
      "ready_for_pickup",
    ].includes(ordStatus);
    return isUnpaid && isNotTerminated && isEligibleStatus;
  }

  function openPaymentModal(order) {
    state.activeOrder = order;

    elements.payOrderId.textContent = order.order_id;
    elements.payOrderStatus.textContent = `${order.order_status} · ${order.payment_status}`;
    elements.payOrderStatus.className = `info-status ${getOrderStatusClass(order.order_status)}`;
    elements.payServerAmount.textContent = formatRupees(order.total_amount);
    elements.btnConfirmPayment.disabled = false;
    elements.btnConfirmPayment.textContent = "Pay Now (Simulated)";

    // Close any open overlays
    elements.receiptModalOverlay.classList.remove("open");
    elements.cartDrawerOverlay.classList.remove("open");

    // Display Payment Step in Checkout Modal
    showCheckoutStep("payment");
    elements.checkoutModalOverlay.classList.add("open");
  }

  async function handlePaymentSubmission() {
    if (!state.activeOrder) return;

    const orderId = state.activeOrder.order_id;
    const selectedMethod = document.querySelector('input[name="pay-method"]:checked')?.value || "simulated_upi";
    const simResult = document.querySelector('input[name="pay-sim-result"]:checked')?.value || "success";

    elements.btnConfirmPayment.disabled = true;
    elements.btnConfirmPayment.textContent = "Processing Simulated Payment...";

    const paymentPayload = {
      customer_id: state.customerId,
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
        state.activeOrder.payment_status = "paid";
        if (payData.order_status) {
          state.activeOrder.order_status = payData.order_status;
        }
        // Immediately reload order history in the background so it shows "Paid"
        loadOrderHistory();
        await displayOrderReceipt(orderId, "confirmation");
      } else {
        // Simulated failure
        elements.btnConfirmPayment.disabled = false;
        elements.btnConfirmPayment.textContent = "Retry Payment (Simulated)";
        showToast("Payment simulation failed as requested. Order is still unpaid.", "warning");
        elements.payOrderStatus.textContent = `${state.activeOrder.order_status} · payment failed`;
        elements.payOrderStatus.className = "info-status status-failed";
        loadOrderHistory();
      }
    } catch (err) {
      elements.btnConfirmPayment.disabled = false;
      elements.btnConfirmPayment.textContent = "Pay Now (Simulated)";

      if (err.code === "PAYMENT_ALREADY_COMPLETED") {
        showToast("Order was already paid! Fetching receipt...", "info");
        loadOrderHistory();
        await displayOrderReceipt(orderId, "confirmation");
      } else {
        showToast(`Payment error: ${err.message}`, "error");
        loadOrderHistory();
      }
    }
  }

  async function displayOrderReceipt(orderId, targetContainer) {
    try {
      const res = await api.getOrder(orderId);
      const order = res.data;

      const isPaid = String(order.payment_status || "").toLowerCase() === "paid";
      const canPay = isOrderPayEligible(order);

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
          <span class="info-status ${getOrderStatusClass(order.order_status)}">${escapeHtml(order.order_status)}</span>
        </div>
        <div class="receipt-row">
          <span class="text-muted">Payment Status:</span>
          ${
            isPaid
              ? `<span class="badge-paid">PAID</span>`
              : canPay
              ? `<span class="badge-pending">PENDING</span>`
              : `<span class="info-status ${getOrderStatusClass(order.order_status)}">${escapeHtml(order.payment_status)}</span>`
          }
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
        ${
          order.refund
            ? `
          <div class="refund-callout">
            <div class="refund-callout-header">
              <span class="refund-callout-title">Simulated Refund Details</span>
              ${
                order.refund.refund_status === "refunded"
                  ? `<span class="badge-refunded">Refunded &middot; ${formatRupees(order.refund.amount)}</span>`
                  : order.refund.refund_status === "failed"
                  ? `<span class="badge-refund-failed">Refund Failed</span>`
                  : `<span class="badge-refund-pending">Refund ${escapeHtml(order.refund.refund_status)}</span>`
              }
            </div>
            <div class="receipt-row" style="font-size: 0.82rem;">
              <span class="text-muted">Refund Amount:</span>
              <strong>${formatRupees(order.refund.amount)}</strong>
            </div>
            ${
              order.refund.transaction_ref
                ? `
            <div class="receipt-row" style="font-size: 0.82rem;">
              <span class="text-muted">Refund Ref:</span>
              <code style="font-size: 0.75rem;">${escapeHtml(order.refund.transaction_ref)}</code>
            </div>`
                : ""
            }
            ${
              order.refund.reason
                ? `
            <div class="receipt-row" style="font-size: 0.82rem;">
              <span class="text-muted">Reason:</span>
              <span>${escapeHtml(order.refund.reason)}</span>
            </div>`
                : ""
            }
            <div class="receipt-row" style="font-size: 0.82rem;">
              <span class="text-muted">Processing Attempts:</span>
              <span>${order.refund.attempt_count || 1}</span>
            </div>
            ${
              order.refund.refund_status === "failed"
                ? `
            <div style="margin-top: 0.5rem; text-align: right;">
              <button class="btn-retry-refund" id="btn-receipt-retry-refund" data-order-id="${order.order_id}">
                Retry Refund
              </button>
            </div>`
                : ""
            }
          </div>
        `
            : String(order.order_status || "").toLowerCase() === "rejected" && !isPaid
            ? `
          <div class="receipt-row" style="font-size: 0.82rem; color: var(--text-muted);">
            <span>Refund Status:</span>
            <span>No payment received &middot; No refund applicable</span>
          </div>`
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
          <span>${isPaid ? "Total Paid:" : "Amount Payable:"}</span>
          <span style="color: var(--primary);">${formatRupees(order.total_amount)}</span>
        </div>
      `;

      if (targetContainer === "confirmation") {
        elements.confirmationReceiptContent.innerHTML = html;
        showCheckoutStep("confirmation");
      } else {
        elements.detailReceiptId.textContent = order.order_id;
        elements.detailReceiptBody.innerHTML = html;

        // Bind Retry Refund button if rendered inside the receipt body
        document.getElementById("btn-receipt-retry-refund")?.addEventListener("click", async () => {
          const btn = document.getElementById("btn-receipt-retry-refund");
          if (btn) {
            btn.disabled = true;
            btn.textContent = "Retrying...";
          }
          try {
            await api.retryRefund(order.order_id);
            showToast("Refund retry processed successfully!", "success");
            loadOrderHistory();
            displayOrderReceipt(order.order_id, "modal");
          } catch (err) {
            showToast(`Refund retry failed: ${err.message}`, "error");
            if (btn) {
              btn.disabled = false;
              btn.textContent = "Retry Refund";
            }
          }
        });

        // Render dynamic footer actions based on payment eligibility
        if (elements.detailReceiptFooter) {
          if (canPay) {
            elements.detailReceiptFooter.innerHTML = `
              <button class="btn-secondary" id="btn-dismiss-receipt">Close</button>
              <button class="btn-success btn-pay-from-receipt" id="btn-receipt-pay-now" data-order-id="${order.order_id}">
                Pay Now &middot; ${formatRupees(order.total_amount)}
              </button>
            `;
            document.getElementById("btn-receipt-pay-now")?.addEventListener("click", () => {
              openPaymentModal(order);
            });
            document.getElementById("btn-dismiss-receipt")?.addEventListener("click", () => {
              elements.receiptModalOverlay.classList.remove("open");
            });
          } else if (order.refund && order.refund.refund_status === "refunded") {
            elements.detailReceiptFooter.innerHTML = `
              <span class="badge-refunded" style="margin-right: auto;">✓ Refunded (${formatRupees(order.refund.amount)})</span>
              <button class="btn-secondary" id="btn-dismiss-receipt">Close</button>
            `;
            document.getElementById("btn-dismiss-receipt")?.addEventListener("click", () => {
              elements.receiptModalOverlay.classList.remove("open");
            });
          } else if (isPaid) {
            elements.detailReceiptFooter.innerHTML = `
              <span class="badge-paid" style="margin-right: auto;">✓ Paid</span>
              <button class="btn-secondary" id="btn-dismiss-receipt">Close</button>
            `;
            document.getElementById("btn-dismiss-receipt")?.addEventListener("click", () => {
              elements.receiptModalOverlay.classList.remove("open");
            });
          } else {
            elements.detailReceiptFooter.innerHTML = `
              <span style="color: var(--danger); font-size: 0.85rem; font-weight: 600; margin-right: auto;">Order ${escapeHtml(order.order_status)}</span>
              <button class="btn-secondary" id="btn-dismiss-receipt">Close</button>
            `;
            document.getElementById("btn-dismiss-receipt")?.addEventListener("click", () => {
              elements.receiptModalOverlay.classList.remove("open");
            });
          }
        }

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
    if (!state.user) {
      elements.ordersHistoryList.innerHTML = `
        <div class="empty-state">
          <p style="font-weight: 700; font-size: 1.1rem; margin-bottom: 0.5rem;">Please log in to view your order history</p>
          <p style="font-size: 0.85rem; margin-bottom: 1.25rem;">Sign in or create an account to view and track your food orders.</p>
          <button class="btn-primary" id="btn-login-from-orders">Log In</button>
        </div>
      `;
      document.getElementById("btn-login-from-orders")?.addEventListener("click", () => openAuthModal("login"));
      return;
    }

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

  function getOrderStatusClass(status) {
    const s = String(status || "").toLowerCase();
    if (s === "accepted") return "status-accepted";
    if (s === "preparing") return "status-preparing";
    if (s === "ready" || s === "ready_for_pickup") return "status-ready";
    if (s === "placed" || s === "pending") return "status-placed";
    if (s === "rejected" || s === "cancelled") return "status-rejected";
    return "status-placed";
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
        const statusClass = getOrderStatusClass(ord.order_status);
        const canPay = isOrderPayEligible(ord);
        const isPaid = String(ord.payment_status || "").toLowerCase() === "paid";
        const isRejected = String(ord.order_status || "").toLowerCase() === "rejected" || String(ord.order_status || "").toLowerCase() === "cancelled";

        let refundBadge = "";
        let retryBtn = "";
        if (ord.refund) {
          if (ord.refund.refund_status === "refunded") {
            refundBadge = `<span class="badge-refunded">Refunded &middot; ${formatRupees(ord.refund.amount)}</span>`;
          } else if (ord.refund.refund_status === "failed") {
            refundBadge = `<span class="badge-refund-failed">Refund Failed</span>`;
            retryBtn = `<button class="btn-retry-refund btn-retry-order-refund" data-order-id="${ord.order_id}">Retry Refund</button>`;
          } else {
            refundBadge = `<span class="badge-refund-pending">Refund ${escapeHtml(ord.refund.refund_status)}</span>`;
          }
        } else if (isRejected && !isPaid) {
          refundBadge = `<span style="font-size: 0.72rem; color: var(--text-muted); font-weight: 500;">No payment &middot; No refund</span>`;
        }

        return `
          <div class="order-history-card" data-order-id="${ord.order_id}">
            <div>
              <div class="order-history-header">
                <span class="order-history-id">Order #${ord.order_id}</span>
                <span class="info-status ${statusClass}">${escapeHtml(ord.order_status)} &middot; ${escapeHtml(ord.payment_status)}</span>
              </div>
              <div class="order-history-rest">${escapeHtml(ord.restaurant_name)}</div>
              <div class="order-history-date">${escapeHtml(ord.created_at || "Recent")}</div>
            </div>
            <div style="display: flex; align-items: center; gap: 0.65rem; flex-wrap: wrap;">
              <span class="order-history-total">${formatRupees(ord.total_amount)}</span>
              <button class="btn-secondary btn-sm btn-view-receipt" data-order-id="${ord.order_id}">View Receipt</button>
              ${
                canPay
                  ? `<button class="btn-success btn-sm btn-pay-order" data-order-id="${ord.order_id}">Pay Now</button>`
                  : isPaid && !ord.refund
                  ? `<span class="badge-paid">Paid</span>`
                  : ""
              }
              ${refundBadge}
              ${retryBtn}
            </div>
          </div>
        `;
      })
      .join("");

    // Bind View Receipt buttons
    elements.ordersHistoryList.querySelectorAll(".btn-view-receipt").forEach((btn) => {
      btn.addEventListener("click", () => {
        const oid = Number(btn.getAttribute("data-order-id"));
        displayOrderReceipt(oid, "modal");
      });
    });

    // Bind Pay Now buttons
    elements.ordersHistoryList.querySelectorAll(".btn-pay-order").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const oid = Number(btn.getAttribute("data-order-id"));
        btn.disabled = true;
        btn.textContent = "Loading...";
        try {
          const res = await api.getOrder(oid);
          const order = res.data;
          if (!isOrderPayEligible(order)) {
            showToast(`Order #${oid} is not eligible for payment (${order.order_status} / ${order.payment_status}).`, "warning");
            loadOrderHistory();
            return;
          }
          openPaymentModal(order);
        } catch (err) {
          showToast(`Could not load order for payment: ${err.message}`, "error");
        } finally {
          btn.disabled = false;
          btn.textContent = "Pay Now";
        }
      });
    });

    // Bind Retry Refund buttons
    elements.ordersHistoryList.querySelectorAll(".btn-retry-order-refund").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const oid = Number(btn.getAttribute("data-order-id"));
        btn.disabled = true;
        btn.textContent = "Retrying...";
        try {
          await api.retryRefund(oid);
          showToast(`Refund processed successfully for Order #${oid}!`, "success");
          loadOrderHistory();
        } catch (err) {
          showToast(`Refund retry failed: ${err.message}`, "error");
          loadOrderHistory();
        }
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

    // Auth Nav & Modal Triggers
    elements.btnNavLogin?.addEventListener("click", () => openAuthModal("login"));
    elements.btnNavRegister?.addEventListener("click", () => openAuthModal("register"));
    elements.btnNavLogout?.addEventListener("click", handleLogout);
    elements.btnCloseAuth?.addEventListener("click", closeAuthModal);
    elements.authModalOverlay?.addEventListener("click", (e) => {
      if (e.target === elements.authModalOverlay) closeAuthModal();
    });

    // Auth Tab Switching
    elements.tabBtnLogin?.addEventListener("click", () => switchAuthTab("login"));
    elements.tabBtnRegister?.addEventListener("click", () => switchAuthTab("register"));
    elements.linkSwitchToRegister?.addEventListener("click", () => switchAuthTab("register"));
    elements.linkSwitchToLogin?.addEventListener("click", () => switchAuthTab("login"));

    // Form Submissions
    elements.formLogin?.addEventListener("submit", handleLoginSubmit);
    elements.formRegister?.addEventListener("submit", handleRegisterSubmit);

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
  async function init() {
    setupEventListeners();
    loadCartFromStorage();
    checkBackendStatus();
    await checkSession();
    await checkUrlParams();
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
