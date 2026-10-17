
(() => {
  'use strict';

  const $ = (selector) => document.querySelector(selector);

  let authMode = 'login';
  let currentUser = null;
  let events = [];
  let registrations = [];
  let categories = [];
  let activeCategory = 'All';
  let searchTerm = '';
  let activeTab = 'dashboard';

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: 'same-origin',
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(options.headers || {})
      }
    });

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
      if (response.status === 401) {
        showAuth();
      }

      throw new Error(data.error || 'Something went wrong. Please try again.');
    }

    return data;
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (char) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;'
    })[char]);
  }

  function fmtDate(value) {
    if (!value) return 'Date to be announced';

    const date = new Date(`${value}T00:00:00`);

    if (Number.isNaN(date.getTime())) return value;

    return date.toLocaleDateString('en-PH', {
      month: 'short',
      day: 'numeric',
      year: 'numeric'
    });
  }

  function showToast(message) {
    const root = $('#toast-root');
    root.innerHTML = `<div class="toast">${escapeHtml(message)}</div>`;

    window.setTimeout(() => {
      root.innerHTML = '';
    }, 2800);
  }

  function showAuth() {
    $('#auth-view').hidden = false;
    $('#participant-app').hidden = true;
    $('#modal-root').innerHTML = '';

    // Show Admin Login on the public login/sign-up screen
    $('#open-admin-login').hidden = false;
  }

  function showApp() {
    $('#auth-view').hidden = true;
    $('#participant-app').hidden = false;
    $('#header-user').textContent = currentUser?.name || '';

    // Hide Admin Login when a participant is logged in
    $('#open-admin-login').hidden = true;

    // Close the admin login modal if it is open
    $('#admin-login-modal').hidden = true;
  }

  function setAuthMode(mode) {
    authMode = mode;

    const isSignup = mode === "signup";

    // Update active tab
    document.getElementById("login-tab").classList.toggle("active", !isSignup);
    document.getElementById("signup-tab").classList.toggle("active", isSignup);

    // Show or hide fields
    document.getElementById("student-id-field").hidden = !isSignup;
    document.getElementById("name-field").hidden = !isSignup;

    document.getElementById("auth-student-id").required = isSignup;
    document.getElementById("auth-name").required = isSignup;

    // Change button label
    document.getElementById("auth-submit").textContent =
      isSignup ? "Create Account" : "Log In";

    // Clear previous form values when switching modes
    document.getElementById("auth-form").reset();

    // Clear old error messages
    document.getElementById("auth-error").textContent = "";
  }

  $('#login-tab').addEventListener('click', () => setAuthMode('login'));
  $('#signup-tab').addEventListener('click', () => setAuthMode('signup'));
  const adminModal = $('#admin-login-modal');
  const adminForm = $('#admin-login-form');
  const adminError = $('#admin-login-error');
  const adminOpenButton = $('#open-admin-login');
  const adminCloseButton = $('#close-admin-login');

  adminOpenButton.addEventListener('click', () => {
    adminModal.hidden = false;
    adminError.hidden = true;
    adminForm.reset();
  });

  adminCloseButton.addEventListener('click', () => {
    adminModal.hidden = true;
  });

  adminModal.addEventListener('click', (event) => {
    if (event.target === adminModal) {
      adminModal.hidden = true;
    }
  });

  adminForm.addEventListener('submit', async (event) => {
    event.preventDefault();

    const submitButton = adminForm.querySelector(
      'button[type="submit"]'
    );

    const email = $('#admin-email').value.trim();
    const password = $('#admin-password').value;

    adminError.hidden = true;
    submitButton.disabled = true;
    submitButton.textContent = 'PLEASE WAIT...';

    try {
      await api('/api/auth/admin-login', {
        method: 'POST',
        body: JSON.stringify({ email, password })
      });

      // The server has accepted the admin login.
      window.location.href = '/admin';
    } catch (error) {
      adminError.textContent = error.message;
      adminError.hidden = false;
    } finally {
      submitButton.disabled = false;
      submitButton.textContent = 'LOGIN AS ADMIN';
    }
  });
  $('#auth-form').addEventListener('submit', async (event) => {
    event.preventDefault();

    const error = $('#auth-error');
    const button = $('#auth-submit');

    error.hidden = true;
    button.disabled = true;
    button.textContent = 'Please wait...';

    const studentId = $('#auth-student-id').value.trim();
    const name = $('#auth-name').value.trim();
    const email = $('#auth-email').value.trim();
    const password = $('#auth-password').value;

    try {
      if (authMode === 'signup') {
        if (!studentId || !name || !email || !password) {
          throw new Error('Please complete all required fields.');
        }

        await api('/api/auth/register', {
          method: 'POST',
          body: JSON.stringify({
            student_id: studentId,
            name,
            email,
            password
          })
        });
      } else {
        await api('/api/auth/login', {
          method: 'POST',
          body: JSON.stringify({ email, password })
        });
      }

      const userData = await api('/api/auth/me');

      if (userData.role !== 'participant' || !userData.user) {
        throw new Error('This page is for participant accounts only.');
      }

      currentUser = userData.user;

      await loadData();

      showApp();
      renderAll();

      showToast(
        authMode === 'signup'
          ? 'Account created successfully!'
          : 'Welcome back!'
      );

      $('#auth-form').reset();
      setAuthMode('login');
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    } finally {
      button.disabled = false;
      button.textContent = authMode === 'signup'
        ? 'Create Account'
        : 'Login';
    }
  });

  $('#logout-btn').addEventListener('click', async () => {
    try {
      await api('/api/auth/logout', {
        method: 'POST',
        body: '{}'
      });
    } catch (error) {
      showToast(error.message);
    }

    currentUser = null;
    events = [];
    registrations = [];
    categories = [];

    showAuth();
    $('#auth-form').reset();
    setAuthMode('login');

    showToast('You have logged out.');
  });

  async function loadData() {
    const [eventData, categoryData, registrationData] = await Promise.all([
      api('/api/events'),
      api('/api/categories'),
      api('/api/my-registrations')
    ]);

    events = Array.isArray(eventData) ? eventData : [];
    categories = Array.isArray(categoryData) ? categoryData : [];
    registrations = Array.isArray(registrationData) ? registrationData : [];
  }

  function getFilteredEvents() {
    return events.filter((event) => {
      const categoryMatches =
        activeCategory === 'All' || event.category === activeCategory;

      const searchable = [
        event.title,
        event.location,
        event.category,
        event.description
      ].join(' ').toLowerCase();

      return categoryMatches &&
        (!searchTerm || searchable.includes(searchTerm.toLowerCase()));
    }).sort((a, b) => new Date(a.date) - new Date(b.date));
  }

  function eventCard(event) {
    const registered = Number(event.registered || 0);
    const capacity = Number(event.capacity || 0);
    const spots = Math.max(0, capacity - registered);
    const full = spots <= 0;

    return `
      <article class="ticket-card ${full ? 'sold-out' : ''}"
        data-event-id="${escapeHtml(event.id)}">
        <div class="ticket-main">
          <span class="cat-tag">${escapeHtml(event.category || 'Event')}</span>
          <h3 class="ticket-title">${escapeHtml(event.title)}</h3>
          <div class="ticket-meta">
            <div>📅 ${escapeHtml(fmtDate(event.date))} · ${escapeHtml(event.time || 'Time TBA')}</div>
            <div>📍 ${escapeHtml(event.location || 'Location TBA')}</div>
          </div>
          <div class="ticket-desc">${escapeHtml(event.description || '')}</div>
        </div>
        <div class="ticket-stub">
          <div class="stub-label">REGISTRATION</div>
          <div class="stub-price">${full ? 'FULL' : 'FREE'}</div>
          <div class="stub-spots">${full ? 'No spots left' : `${spots} spots left`}</div>
          <div class="admit-badge">${full ? 'FULLY BOOKED' : 'RESERVE SPOT'}</div>
        </div>
      </article>
    `;
  }

  function renderStats() {
    $('#stat-events').textContent = events.length;

    $('#stat-registrations').textContent = events.reduce(
      (total, event) => total + Number(event.registered || 0),
      0
    );

    $('#stat-mine').textContent = registrations.length;
  }

  function renderFilters() {
    const options = ['All', ...categories];

    if (!options.includes(activeCategory)) {
      activeCategory = 'All';
    }

    $('#filters-row').innerHTML = `
      <div class="filters">
        ${options.map(category => `
          <button type="button"
            class="chip ${category === activeCategory ? 'active' : ''}"
            data-category="${escapeHtml(category)}">
            ${escapeHtml(category)}
          </button>
        `).join('')}
      </div>
    `;

    $('#filters-row').querySelectorAll('[data-category]').forEach(button => {
      button.addEventListener('click', () => {
        activeCategory = button.dataset.category;
        renderFilters();
        renderEvents();
      });
    });
  }

  function attachEventCardHandlers(container) {
    container.querySelectorAll('[data-event-id]').forEach(card => {
      card.addEventListener('click', () => {
        const selectedEvent = events.find(
          item => item.id === card.dataset.eventId
        );

        if (selectedEvent) openRegistrationModal(selectedEvent);
      });
    });
  }

  function renderEvents() {
    const list = getFilteredEvents();

    $('#events-count').textContent =
      `${list.length} event${list.length === 1 ? '' : 's'}`;

    $('#grid').innerHTML = list.length
      ? list.map(eventCard).join('')
      : '<div class="empty-state" style="grid-column:1/-1">No events found. Try another search or category.</div>';

    attachEventCardHandlers($('#grid'));
  }

  function renderDashboard() {
    const upcoming = events
      .filter(event =>
        !event.date || new Date(`${event.date}T23:59:59`) >= new Date()
      )
      .sort((a, b) => new Date(a.date) - new Date(b.date))
      .slice(0, 3);

    $('#dashboard-events').innerHTML = upcoming.length
      ? upcoming.map(eventCard).join('')
      : '<div class="empty-state">No upcoming events available.</div>';

    attachEventCardHandlers($('#dashboard-events'));

    const recent = [...registrations]
      .sort((a, b) =>
        new Date(b.registeredAt || 0) - new Date(a.registeredAt || 0)
      )
      .slice(0, 3);

    $('#dashboard-registrations').innerHTML = recent.length
      ? recent.map(registration => `
          <div class="my-ticket-row">
            <div>
              <div class="mt-title">${escapeHtml(registration.eventTitle || 'Event')}</div>
              <div class="mt-sub">Quantity: ${Number(registration.quantity || 1)}</div>
            </div>
            <span class="attendance-status">
              ${escapeHtml(registration.attendanceStatus || 'registered').toUpperCase()}
            </span>
          </div>
        `).join('')
      : '<div class="empty-state">You have not registered for any events yet.</div>';
  }

  function renderMine() {
    const sorted = [...registrations].sort((a, b) =>
      new Date(b.registeredAt || 0) - new Date(a.registeredAt || 0)
    );

    $('#mine-list').innerHTML = sorted.length
      ? sorted.map(registration => {
        const event = events.find(item => item.id === registration.eventId);
        const date = event
          ? `${fmtDate(event.date)} · ${event.time || ''}`
          : 'Event details unavailable';

        const status = String(
          registration.attendanceStatus || 'registered'
        ).toLowerCase();

        const canCancel = status !== 'attended' && status !== 'absent';

        return `
            <div class="my-ticket-row">
              <div>
                <div class="mt-title">
                  ${escapeHtml(registration.eventTitle || event?.title || 'Event')}
                </div>
                <div class="mt-sub">
                  ${escapeHtml(date)} · Qty ${Number(registration.quantity || 1)}
                </div>
                <div class="attendance-status">${escapeHtml(status.toUpperCase())}</div>
              </div>
              ${canCancel ? `
                <button type="button" class="btn btn-ghost"
                  data-cancel-id="${escapeHtml(registration.id)}">
                  Cancel Reservation
                </button>
              ` : ''}
            </div>
          `;
      }).join('')
      : '<div class="empty-state">No registrations yet. Browse events to reserve a free spot.</div>';

    $('#mine-list').querySelectorAll('[data-cancel-id]').forEach(button => {
      button.addEventListener('click', async () => {
        if (!confirm('Cancel this reservation?')) return;

        button.disabled = true;

        try {
          await api(
            `/api/registrations/${encodeURIComponent(button.dataset.cancelId)}/cancel`,
            { method: 'POST', body: '{}' }
          );

          await loadData();
          renderAll();
          showToast('Reservation cancelled.');
        } catch (error) {
          showToast(error.message);
          button.disabled = false;
        }
      });
    });
  }

  function renderProfile() {
    const name = currentUser?.name || 'Participant';
    const studentId = currentUser?.student_id || currentUser?.studentId || '';
    const email = currentUser?.email || '';

    $('#profile-content').innerHTML = `
      <div class="profile-card">
        <div class="profile-avatar">👤</div>
        <div class="profile-details">
          <span class="dashboard-card-label">PARTICIPANT ACCOUNT</span>
          <h3>${escapeHtml(name)}</h3>
          <p><strong>Student ID:</strong> ${escapeHtml(studentId || 'Not available')}</p>
          <p><strong>Email:</strong> ${escapeHtml(email)}</p>
          <div class="profile-meta">Registrations: ${registrations.length}</div>
          <p class="profile-note">
            Your account information is used for your event reservations.
          </p>
        </div>
      </div>
    `;
  }

  function renderAttended() {
    const attended = registrations.filter(
      registration =>
        String(registration.attendanceStatus || '').toLowerCase() === 'attended'
    );

    $('#attended-list').innerHTML = attended.length
      ? attended.map(registration => {
        const event = events.find(item => item.id === registration.eventId);

        return `
            <div class="my-ticket-row">
              <div>
                <div class="mt-title">
                  ${escapeHtml(registration.eventTitle || event?.title || 'Event')}
                </div>
                <div class="mt-sub">
                  ${event
            ? escapeHtml(`${fmtDate(event.date)} · ${event.time || ''}`)
            : 'Event details unavailable'}
                </div>
              </div>
              <span class="attendance-status">ATTENDED</span>
            </div>
          `;
      }).join('')
      : '<div class="empty-state">Your attended events will appear here after the admin records your attendance.</div>';
  }

  function updateActiveTab() {
    const views = {
      dashboard: '#dashboard-view',
      events: '#events-view',
      mine: '#mine-view',
      profile: '#profile-view',
      attended: '#attended-view'
    };

    Object.entries(views).forEach(([tab, selector]) => {
      $(selector).hidden = activeTab !== tab;
    });

    document.querySelectorAll('[data-tab]').forEach(button => {
      button.classList.toggle('active', button.dataset.tab === activeTab);
    });

    const titles = {
      dashboard: 'Dashboard',
      events: 'Browse Events',
      mine: 'My Registrations',
      profile: 'My Profile',
      attended: 'Attended Events'
    };

    $('#page-heading').innerHTML =
      `${escapeHtml(titles[activeTab] || 'Dashboard')}<span>.</span>`;

    $('#search-wrap').hidden = activeTab !== 'events';
    $('#welcome-banner').hidden = activeTab !== 'dashboard';
  }

  function renderAll() {
    renderStats();
    renderFilters();
    renderEvents();
    renderDashboard();
    renderMine();
    renderProfile();
    renderAttended();
    updateActiveTab();
  }

  function openRegistrationModal(event) {
    const registered = Number(event.registered || 0);
    const capacity = Number(event.capacity || 0);
    const spots = Math.max(0, capacity - registered);

    if (spots <= 0) {
      showToast('This event has no spots left.');
      return;
    }

    const alreadyRegistered = registrations.some(
      item => item.eventId === event.id
    );

    if (alreadyRegistered) {
      showToast('You already have a reservation for this event.');
      return;
    }

    $('#modal-root').innerHTML = `
      <div class="overlay" id="registration-overlay">
        <div class="modal">
          <div class="modal-head">
            <div>
              <h3>${escapeHtml(event.title)}</h3>
              <div class="sub">
                ${escapeHtml(fmtDate(event.date))} ·
                ${escapeHtml(event.time || 'Time TBA')} ·
                ${escapeHtml(event.location || 'Location TBA')}
              </div>
            </div>
            <button type="button" class="close-x" id="registration-close">✕</button>
          </div>

          <div class="modal-body">
            <p>Reserve a free spot using your participant account.</p>

            <div class="field">
              <label for="registration-quantity">Number of Spots</label>
              <input id="registration-quantity" type="number"
                min="1" max="${spots}" value="1" required>
            </div>

            <div class="total-row">
              <span class="lbl">Registration Fee</span>
              <span class="amt">FREE</span>
            </div>

            <div id="registration-error" class="form-err" hidden></div>

            <button type="button" class="btn btn-gold submit-btn"
              id="registration-submit">
              Confirm Reservation
            </button>
          </div>
        </div>
      </div>
    `;

    const close = () => {
      $('#modal-root').innerHTML = '';
    };

    $('#registration-close').addEventListener('click', close);

    $('#registration-overlay').addEventListener('click', clickEvent => {
      if (clickEvent.target.id === 'registration-overlay') close();
    });

    $('#registration-submit').addEventListener('click', async () => {
      const quantity = Number($('#registration-quantity').value);
      const error = $('#registration-error');
      const button = $('#registration-submit');

      if (!Number.isInteger(quantity) || quantity < 1 || quantity > spots) {
        error.textContent = `Choose a quantity from 1 to ${spots}.`;
        error.hidden = false;
        return;
      }

      button.disabled = true;
      button.textContent = 'Reserving...';
      error.hidden = true;

      try {
        await api(
          `/api/events/${encodeURIComponent(event.id)}/register`,
          {
            method: 'POST',
            body: JSON.stringify({ quantity })
          }
        );

        await loadData();
        close();
        renderAll();
        showToast('Your free reservation is confirmed!');
      } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
        button.disabled = false;
        button.textContent = 'Confirm Reservation';
      }
    });
  }

  document.querySelectorAll('.side-link').forEach(button => {
    button.addEventListener('click', () => {
      activeTab = button.dataset.tab;
      updateActiveTab();
    });
  });

  document.querySelectorAll('[data-go-tab]').forEach(button => {
    button.addEventListener('click', event => {
      event.preventDefault();
      activeTab = button.dataset.goTab;
      updateActiveTab();
    });
  });

  $('#search').addEventListener('input', event => {
    searchTerm = event.target.value.trim();
    renderEvents();
  });

  async function init() {
    setAuthMode('login');

    try {
      const data = await api('/api/auth/me');

      if (data.role === 'admin') {
        window.location.href = '/admin';
        return;
      }

      if (data.role === 'participant' && data.user) {
        currentUser = data.user;
        await loadData();
        showApp();
        renderAll();
        return;
      }
    } catch (error) {
      // No active session; show the login form.
    }

    showAuth();
  }

  init();
})();
