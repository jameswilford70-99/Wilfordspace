(function () {
  "use strict";

  let householdLoaded = false;

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  async function request(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {})
      }
    });

    let data = {};

    try {
      data = await response.json();
    } catch (_) {
      data = {};
    }

    if (!response.ok) {
      throw new Error(data.detail || "Request failed");
    }

    return data;
  }

  function addNavigation() {
    const sidebarBottom = document.querySelector(".sidebar-bottom");

    if (
      !sidebarBottom ||
      document.querySelector('[data-view="household-view"]')
    ) {
      return;
    }

    const button = document.createElement("button");

    button.className = "nav-button";
    button.dataset.view = "household-view";

    button.innerHTML = `
      <span class="nav-icon">♙</span>
      Household members
    `;

    sidebarBottom.insertBefore(
      button,
      sidebarBottom.firstChild
    );

    button.addEventListener("click", () => {
      showHouseholdView();
    });
  }

  function addPage() {
    const main = document.querySelector(".main");

    if (
      !main ||
      document.getElementById("household-view")
    ) {
      return;
    }

    const section = document.createElement("section");

    section.id = "household-view";
    section.className = "page hidden";

    section.innerHTML = `
      <div class="page-head">
        <div>
          <h1>Household members</h1>
          <p>
            Share WilfordSpace with people in your household.
          </p>
        </div>
      </div>

      <div id="household-member-card" class="card">
        <p>Loading household members...</p>
      </div>

      <div id="household-message" class="message"></div>
    `;

    main.appendChild(section);
  }

  function showHouseholdView() {
    document.querySelectorAll(".page").forEach(page => {
      page.classList.add("hidden");
    });

    document.querySelectorAll(".nav-button").forEach(button => {
      button.classList.remove("active");
    });

    const page = document.getElementById("household-view");
    const button = document.querySelector(
      '[data-view="household-view"]'
    );

    if (page) {
      page.classList.remove("hidden");
    }

    if (button) {
      button.classList.add("active");
    }

    loadMembers();
  }

  function setMessage(message, isError = false) {
    const element = document.getElementById(
      "household-message"
    );

    if (!element) {
      return;
    }

    element.textContent = message || "";
    element.classList.toggle("error", isError);
  }

  function renderMembers(data) {
    const card = document.getElementById(
      "household-member-card"
    );

    const household = data.household;
    const members = data.members || [];
    const canManage = Boolean(household.can_manage);

    card.innerHTML = `
      <div class="page-head">
        <div>
          <h2>${escapeHtml(household.name)}</h2>
          <p>
            ${members.length}
            household member${members.length === 1 ? "" : "s"}
          </p>
        </div>
      </div>

      ${
        canManage
          ? `
            <form id="household-add-form" class="form-card">
              <h3>Add a registered user</h3>

              <p>
                Users must register for WilfordSpace
                before they can be added.
              </p>

              <label for="household-member-email">
                Email address
              </label>

              <input
                id="household-member-email"
                type="email"
                placeholder="person@example.com"
                required
              >

              <div class="form-actions">
                <button type="submit">
                  Add member
                </button>
              </div>
            </form>
          `
          : ""
      }

      <div class="household-members-list">
        ${members
          .map(
            member => `
              <div class="household-member-row">
                <div>
                  <strong>
                    ${escapeHtml(member.name)}
                  </strong>

                  <div>
                    ${escapeHtml(member.email)}
                  </div>

                  <small>
                    ${
                      member.role === "owner"
                        ? "Owner"
                        : "Member"
                    }
                  </small>
                </div>

                <div class="household-member-actions">
                  ${
                    member.role === "owner"
                      ? `
                        <span class="member-badge">
                          Owner
                        </span>
                      `
                      : canManage
                        ? `
                          <button
                            class="secondary
                              remove-household-member"
                            data-user-id="${member.user_id}"
                            data-name="${escapeHtml(
                              member.name
                            )}"
                          >
                            Remove
                          </button>
                        `
                        : `
                          <span class="member-badge">
                            Member
                          </span>
                        `
                  }
                </div>
              </div>
            `
          )
          .join("")}
      </div>
    `;

    const form = document.getElementById(
      "household-add-form"
    );

    if (form) {
      form.addEventListener("submit", addMember);
    }

    document
      .querySelectorAll(".remove-household-member")
      .forEach(button => {
        button.addEventListener("click", () => {
          removeMember(
            button.dataset.userId,
            button.dataset.name
          );
        });
      });
  }

  async function loadMembers() {
    addPage();

    const card = document.getElementById(
      "household-member-card"
    );

    if (!householdLoaded && card) {
      card.innerHTML = `
        <p>Loading household members...</p>
      `;
    }

    try {
      const data = await request(
        "/api/household/members"
      );

      renderMembers(data);
      householdLoaded = true;
      setMessage("");
    } catch (error) {
      if (card) {
        card.innerHTML = `
          <p>Unable to load household members.</p>
        `;
      }

      setMessage(error.message, true);
    }
  }

  async function addMember(event) {
    event.preventDefault();

    const emailInput = document.getElementById(
      "household-member-email"
    );

    const email = emailInput.value.trim();

    if (!email) {
      return;
    }

    try {
      await request("/api/household/members", {
        method: "POST",
        body: JSON.stringify({
          email: email
        })
      });

      emailInput.value = "";

      setMessage("Household member added.");
      await loadMembers();
    } catch (error) {
      setMessage(error.message, true);
    }
  }

  async function removeMember(userId, name) {
    if (
      !confirm(
        `Remove ${name} from this household?`
      )
    ) {
      return;
    }

    try {
      await request(
        `/api/household/members/${userId}`,
        {
          method: "DELETE"
        }
      );

      setMessage("Household member removed.");
      await loadMembers();
    } catch (error) {
      setMessage(error.message, true);
    }
  }

  function initialise() {
    addNavigation();
    addPage();

    const originalApp = document.getElementById("app");

    if (!originalApp) {
      return;
    }

    const observer = new MutationObserver(() => {
      const isVisible =
        !originalApp.classList.contains("hidden");

      if (
        isVisible &&
        !document.querySelector(
          '[data-view="household-view"]'
        )
      ) {
        addNavigation();
      }
    });

    observer.observe(originalApp, {
      attributes: true,
      attributeFilter: ["class"]
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener(
      "DOMContentLoaded",
      initialise
    );
  } else {
    initialise();
  }
})();
