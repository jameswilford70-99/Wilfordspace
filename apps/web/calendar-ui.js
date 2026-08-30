(function () {
  "use strict";

  let calendarMonth = new Date();
  let selectedEvent = null;
  let events = [];
  let meals = [];

  function byId(id) {
    return document.getElementById(id);
  }

  function escapeValue(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  async function request(path, options = {}) {
    const response = await fetch(path, {
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {})
      },
      ...options
    });

    let data = null;

    try {
      data = await response.json();
    } catch {
      data = null;
    }

    if (!response.ok) {
      const detail =
        data?.detail ||
        data?.message ||
        `Request failed with status ${response.status}`;

      throw new Error(detail);
    }

    return data;
  }

  function localDateKey(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");

    return `${year}-${month}-${day}`;
  }

  function parseDate(value) {
    const parts = String(value).slice(0, 10).split("-");

    return new Date(
      Number(parts[0]),
      Number(parts[1]) - 1,
      Number(parts[2]),
      12
    );
  }

  function monthStart() {
    return new Date(
      calendarMonth.getFullYear(),
      calendarMonth.getMonth(),
      1,
      12
    );
  }

  function monthEnd() {
    return new Date(
      calendarMonth.getFullYear(),
      calendarMonth.getMonth() + 1,
      0,
      12
    );
  }

  function formatMonth() {
    return calendarMonth.toLocaleDateString(undefined, {
      month: "long",
      year: "numeric"
    });
  }

  function recipeTitle(meal) {
    return (
      meal.recipe?.title ||
      meal.recipe?.name ||
      meal.custom_name ||
      meal.title ||
      "Planned meal"
    );
  }

  function addCalendarStyles() {
    if (byId("wilfordspace-calendar-ui-styles")) {
      return;
    }

    const style = document.createElement("style");
    style.id = "wilfordspace-calendar-ui-styles";

    style.textContent = `
      .ws-calendar-shell {
        width: 100%;
      }

      .ws-calendar-toolbar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 18px;
      }

      .ws-calendar-toolbar-title {
        color: var(--muted);
        font-size: 18px;
        font-weight: 800;
        text-align: center;
      }

      .ws-calendar-toolbar-actions {
        display: flex;
        gap: 8px;
      }

      .ws-calendar-grid {
        display: grid;
        grid-template-columns: repeat(7, minmax(120px, 1fr));
        overflow-x: auto;
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 10px;
      }

      .ws-calendar-weekday {
        min-width: 120px;
        padding: 12px 8px;
        color: var(--muted);
        background: var(--panel-3);
        border-right: 1px solid var(--border);
        border-bottom: 1px solid var(--border);
        font-size: 12px;
        font-weight: 800;
        text-align: center;
        text-transform: uppercase;
      }

      .ws-calendar-day {
        min-width: 120px;
        min-height: 135px;
        padding: 8px;
        background: var(--panel);
        border-right: 1px solid var(--border);
        border-bottom: 1px solid var(--border);
        cursor: pointer;
      }

      .ws-calendar-day:hover {
        background: var(--panel-2);
      }

      .ws-calendar-day.other-month {
        opacity: .4;
      }

      .ws-calendar-day.today {
        box-shadow: inset 0 3px 0 var(--accent);
      }

      .ws-calendar-number {
        display: grid;
        width: 28px;
        height: 28px;
        margin-bottom: 6px;
        place-items: center;
        border-radius: 50%;
        font-size: 13px;
        font-weight: 800;
      }

      .ws-calendar-day.today .ws-calendar-number {
        color: #1e1e1e;
        background: var(--accent);
      }

      .ws-calendar-event {
        overflow: hidden;
        margin: 4px 0;
        padding: 5px 6px;
        color: white;
        background: var(--accent);
        border-radius: 4px;
        font-size: 11px;
        line-height: 1.25;
        text-overflow: ellipsis;
        white-space: nowrap;
        cursor: pointer;
      }

      .ws-calendar-event.meal {
        background: #397e92;
      }

      .ws-calendar-event-time {
        opacity: .8;
      }

      .ws-calendar-subscriptions {
        margin-top: 20px;
      }

      .ws-calendar-subscriptions code {
        display: block;
        overflow-wrap: anywhere;
        margin: 10px 0;
        padding: 12px;
        color: var(--text);
        background: var(--panel-2);
        border: 1px solid var(--border);
        border-radius: 7px;
        font-size: 12px;
      }

      .ws-calendar-subscriptions p {
        color: var(--muted);
        font-size: 13px;
        line-height: 1.5;
      }

      .ws-calendar-form-row {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 12px;
      }

      @media (max-width: 700px) {
        .ws-calendar-toolbar {
          align-items: stretch;
          flex-direction: column;
        }

        .ws-calendar-toolbar-actions {
          justify-content: space-between;
        }

        .ws-calendar-form-row {
          grid-template-columns: 1fr;
        }
      }
    `;

    document.head.appendChild(style);
  }

  function renderCalendarShell() {
    const page = byId("calendar-view");

    if (!page) {
      return;
    }

    page.innerHTML = `
      <div class="page-head">
        <div>
          <h1>Calendar</h1>
          <p>Shared household calendar.</p>
        </div>

        <button id="ws-new-calendar-event">
          Add event
        </button>
      </div>

      <div class="ws-calendar-shell">
        <div class="ws-calendar-toolbar">
          <div class="ws-calendar-toolbar-actions">
            <button id="ws-calendar-previous" class="secondary">
              ← Previous
            </button>

            <button id="ws-calendar-today" class="secondary">
              Today
            </button>
          </div>

          <div id="ws-calendar-month" class="ws-calendar-toolbar-title"></div>

          <button id="ws-calendar-next" class="secondary">
            Next →
          </button>
        </div>

        <div class="ws-calendar-grid">
          <div class="ws-calendar-weekday">Sun</div>
          <div class="ws-calendar-weekday">Mon</div>
          <div class="ws-calendar-weekday">Tue</div>
          <div class="ws-calendar-weekday">Wed</div>
          <div class="ws-calendar-weekday">Thu</div>
          <div class="ws-calendar-weekday">Fri</div>
          <div class="ws-calendar-weekday">Sat</div>

          <div id="ws-calendar-days" style="display:contents"></div>
        </div>

        <div class="ws-calendar-subscriptions card">
          <h2>Calendar subscriptions</h2>

          <p>
            Subscribe to your WilfordSpace calendar from Apple Calendar,
            Google Calendar or another iCalendar-compatible application.
          </p>

          <button id="ws-load-feed-url" class="secondary">
            Load private feed URL
          </button>

          <div id="ws-feed-url-container" class="hidden">
            <code id="ws-feed-url"></code>

            <button id="ws-copy-feed-url">
              Copy feed URL
            </button>

            <p>
              Keep this URL private. Anyone who has it can read your
              household calendar.
            </p>

            <p>
              In Google Calendar choose
              <strong>Other calendars → From URL</strong>.
              In Apple Calendar choose
              <strong>Add Calendar Subscription</strong>.
            </p>
          </div>
        </div>

        <div id="ws-calendar-message" class="message"></div>
      </div>
    `;

    byId("ws-calendar-previous").onclick = () => {
      calendarMonth = new Date(
        calendarMonth.getFullYear(),
        calendarMonth.getMonth() - 1,
        1,
        12
      );

      loadCalendar();
    };

    byId("ws-calendar-next").onclick = () => {
      calendarMonth = new Date(
        calendarMonth.getFullYear(),
        calendarMonth.getMonth() + 1,
        1,
        12
      );

      loadCalendar();
    };

    byId("ws-calendar-today").onclick = () => {
      calendarMonth = new Date();

      loadCalendar();
    };

    byId("ws-new-calendar-event").onclick = () => {
      openEventModal();
    };

    byId("ws-load-feed-url").onclick = loadFeedUrl;
    byId("ws-copy-feed-url").onclick = copyFeedUrl;
  }

  async function loadCalendar() {
    const start = monthStart();
    const end = monthEnd();

    byId("ws-calendar-month").textContent = formatMonth();
    byId("ws-calendar-days").innerHTML = "Loading...";

    try {
      const calendarData = await request(
        `/api/calendar/events?start_date=${localDateKey(start)}&end_date=${localDateKey(end)}`
      );

      const mealsData = await request(
        `/api/meal-plan?start_date=${localDateKey(start)}&end_date=${localDateKey(end)}`
      );

      events = calendarData.events || [];
      meals = mealsData.meals || [];

      renderCalendar();
    } catch (error) {
      byId("ws-calendar-message").textContent = error.message;
    }
  }

  function renderCalendar() {
    const first = monthStart();

    const last = monthEnd();

    const gridStart = new Date(first);
    gridStart.setDate(first.getDate() - first.getDay());

    const gridEnd = new Date(last);
    gridEnd.setDate(last.getDate() + (6 - last.getDay()));

    const today = localDateKey(new Date());
    const html = [];

    for (
      const day = new Date(gridStart);
      day <= gridEnd;
      day.setDate(day.getDate() + 1)
    ) {
      const key = localDateKey(day);
      const inMonth = day.getMonth() === calendarMonth.getMonth();

      const dayEvents = events.filter(event => {
        return String(event.event_date).slice(0, 10) === key;
      });

      const dayMeals = meals.filter(meal => {
        return String(meal.date || meal.meal_date).slice(0, 10) === key;
      });

      html.push(`
        <div
          class="ws-calendar-day ${inMonth ? "" : "other-month"} ${key === today ? "today" : ""}"
          data-date="${key}"
        >
          <div class="ws-calendar-number">
            ${day.getDate()}
          </div>

          ${dayEvents.map(event => `
            <div
              class="ws-calendar-event"
              data-event-id="${event.id}"
              style="background:${escapeValue(event.colour || "#ef8b2c")}"
              title="${escapeValue(event.description || event.title)}"
            >
              ${
                event.all_day
                  ? ""
                  : `<span class="ws-calendar-event-time">${escapeValue(event.start_time || "")}</span> `
              }
              ${escapeValue(event.title)}
            </div>
          `).join("")}

          ${dayMeals.map(meal => `
            <div class="ws-calendar-event meal">
              Meal:
              ${escapeValue(recipeTitle(meal))}
            </div>
          `).join("")}
        </div>
      `);
    }

    byId("ws-calendar-days").innerHTML = html.join("");

    document.querySelectorAll(".ws-calendar-day").forEach(day => {
      day.onclick = event => {
        if (event.target.closest("[data-event-id]")) {
          return;
        }

        openEventModal(null, day.dataset.date);
      };
    });

    document.querySelectorAll("[data-event-id]").forEach(element => {
      element.onclick = event => {
        event.stopPropagation();

        const selected = events.find(item => {
          return String(item.id) === String(element.dataset.eventId);
        });

        openEventModal(selected);
      };
    });
  }

  function openEventModal(event = null, selectedDate = null) {
    selectedEvent = event;

    const modal = document.createElement("div");
    modal.className = "modal-backdrop";
    modal.id = "ws-event-modal";

    modal.innerHTML = `
      <div class="modal">
        <div class="modal-head">
          <h2>${event ? "Edit event" : "Add event"}</h2>
          <button class="close" id="ws-close-event-modal">×</button>
        </div>

        <form id="ws-event-form">
          <label>Title</label>
          <input id="ws-event-title" maxlength="250" required
            value="${escapeValue(event?.title || "")}">

          <label>Date</label>
          <input id="ws-event-date" type="date" required
            value="${escapeValue(
              event?.event_date?.slice(0, 10) ||
              selectedDate ||
              localDateKey(new Date())
            )}">

          <label>
            <input id="ws-event-all-day" type="checkbox"
              style="width:auto"
              ${event?.all_day ? "checked" : ""}>
            All-day event
          </label>

          <div id="ws-event-times">
            <div class="ws-calendar-form-row">
              <div>
                <label>Start time</label>
                <input id="ws-event-start" type="time"
                  value="${escapeValue(event?.start_time || "")}">
              </div>

              <div>
                <label>End time</label>
                <input id="ws-event-end" type="time"
                  value="${escapeValue(event?.end_time || "")}">
              </div>
            </div>
          </div>

          <label>Location</label>
          <input id="ws-event-location"
            value="${escapeValue(event?.location || "")}">

          <label>Category</label>
          <select id="ws-event-category">
            ${[
              "Other",
              "Family",
              "School",
              "Work",
              "Appointment",
              "Holiday"
            ].map(category => `
              <option
                value="${category}"
                ${event?.category === category ? "selected" : ""}
              >
                ${category}
              </option>
            `).join("")}
          </select>

          <label>Colour</label>
          <input id="ws-event-colour" type="color"
            value="${escapeValue(event?.colour || "#ef8b2c")}">

          <label>Description</label>
          <textarea id="ws-event-description">${escapeValue(
            event?.description || ""
          )}</textarea>

          <div class="form-actions">
            <button type="submit">
              Save event
            </button>

            ${
              event
                ? `<button type="button" id="ws-delete-event" class="danger">
                    Delete
                  </button>`
                : ""
            }

            <button type="button" id="ws-cancel-event" class="secondary">
              Cancel
            </button>
          </div>

          <div id="ws-event-message" class="message"></div>
        </form>
      </div>
    `;

    document.body.appendChild(modal);

    byId("ws-close-event-modal").onclick = closeEventModal;
    byId("ws-cancel-event").onclick = closeEventModal;
    byId("ws-event-form").onsubmit = saveEvent;
    byId("ws-event-all-day").onchange = toggleEventTimes;

    if (event) {
      byId("ws-delete-event").onclick = deleteEvent;
    }

    toggleEventTimes();
    byId("ws-event-title").focus();
  }

  function closeEventModal() {
    const modal = byId("ws-event-modal");

    if (modal) {
      modal.remove();
    }

    selectedEvent = null;
  }

  function toggleEventTimes() {
    byId("ws-event-times").classList.toggle(
      "hidden",
      byId("ws-event-all-day").checked
    );
  }

  async function saveEvent(event) {
    event.preventDefault();

    const id = selectedEvent?.id;

    const payload = {
      title: byId("ws-event-title").value.trim(),
      description: byId("ws-event-description").value.trim(),
      event_date: byId("ws-event-date").value,
      start_time: byId("ws-event-all-day").checked
        ? null
        : byId("ws-event-start").value || null,
      end_time: byId("ws-event-all-day").checked
        ? null
        : byId("ws-event-end").value || null,
      all_day: byId("ws-event-all-day").checked,
      location: byId("ws-event-location").value.trim(),
      category: byId("ws-event-category").value,
      colour: byId("ws-event-colour").value
    };

    try {
      await request(
        id
          ? `/api/calendar/events/${id}`
          : "/api/calendar/events",
        {
          method: id ? "PUT" : "POST",
          body: JSON.stringify(payload)
        }
      );

      closeEventModal();
      await loadCalendar();
    } catch (error) {
      byId("ws-event-message").textContent = error.message;
    }
  }

  async function deleteEvent() {
    if (!selectedEvent?.id) {
      return;
    }

    if (!confirm("Delete this calendar event?")) {
      return;
    }

    try {
      await request(
        `/api/calendar/events/${selectedEvent.id}`,
        {
          method: "DELETE"
        }
      );

      closeEventModal();
      await loadCalendar();
    } catch (error) {
      byId("ws-event-message").textContent = error.message;
    }
  }

  async function loadFeedUrl() {
    const button = byId("ws-load-feed-url");

    button.disabled = true;
    button.textContent = "Loading...";

    try {
      const data = await request("/api/calendar/feed-info");

      byId("ws-feed-url").textContent = data.feed_url;
      byId("ws-feed-url-container").classList.remove("hidden");
      button.textContent = "Refresh private feed URL";
    } catch (error) {
      byId("ws-calendar-message").textContent = error.message;
      button.textContent = "Load private feed URL";
    } finally {
      button.disabled = false;
    }
  }

  async function copyFeedUrl() {
    const url = byId("ws-feed-url").textContent;

    if (!url) {
      return;
    }

    try {
      await navigator.clipboard.writeText(url);
      byId("ws-copy-feed-url").textContent = "Copied";

      setTimeout(() => {
        byId("ws-copy-feed-url").textContent = "Copy feed URL";
      }, 1800);
    } catch {
      byId("ws-calendar-message").textContent =
        "Your browser could not copy the URL automatically.";
    }
  }

  function initialise() {
    addCalendarStyles();
    renderCalendarShell();

    document.querySelectorAll("[data-view='calendar-view']").forEach(button => {
      button.addEventListener("click", () => {
        setTimeout(loadCalendar, 0);
      });
    });

    loadCalendar();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialise);
  } else {
    initialise();
  }
})();
