function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#039;",
  })[character]);
}

function deviceTime(value) {
  if (!value) return "Schedule unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], {
    weekday:"short", month:"short", day:"numeric", hour:"numeric", minute:"2-digit", timeZoneName:"short",
  });
}

function loadPilotLeaflet() {
  if (window.PilotTrackerLeaflet) return Promise.resolve(window.PilotTrackerLeaflet);
  if (window.PilotTrackerLeafletPromise) return window.PilotTrackerLeafletPromise;
  window.PilotTrackerLeafletPromise = Promise.all([
    new Promise((resolve, reject) => {
      const existing = document.querySelector("link[data-pilot-leaflet]");
      if (existing?.sheet) {
        resolve();
        return;
      }
      const stylesheet = existing || document.createElement("link");
      const loaded = () => resolve();
      const failed = () => reject(new Error("Pilot Tracker map stylesheet failed to load"));
      stylesheet.addEventListener("load", loaded, {once:true});
      stylesheet.addEventListener("error", failed, {once:true});
      if (!existing) {
        stylesheet.rel = "stylesheet";
        stylesheet.href = "/pilot_tracker_frontend/leaflet.css";
        stylesheet.dataset.pilotLeaflet = "";
        document.head.appendChild(stylesheet);
      }
    }),
    new Promise((resolve, reject) => {
      if (window.L) {
        resolve();
        return;
      }
      const existing = document.querySelector("script[data-pilot-leaflet]");
      const script = existing || document.createElement("script");
      script.addEventListener("load", resolve, {once:true});
      script.addEventListener("error", () => reject(new Error("Pilot Tracker map library failed to load")), {once:true});
      if (!existing) {
        script.src = "/pilot_tracker_frontend/leaflet.js";
        script.dataset.pilotLeaflet = "";
        document.head.appendChild(script);
      }
    }),
  ]).then(() => {
    window.PilotTrackerLeaflet = window.L.noConflict();
    return window.PilotTrackerLeaflet;
  }).catch((error) => {
    window.PilotTrackerLeafletPromise = null;
    throw error;
  });
  return window.PilotTrackerLeafletPromise;
}

class PilotTrackerLiveMap extends HTMLElement {
  setConfig(config) {
    this.config = {entity:"sensor.pilot_tracker_flight_map", title:"Pilot", height:420, ...config};
    this.renderShell();
  }

  set hass(hass) {
    this._hass = hass;
    this.updateMap();
  }

  connectedCallback() { this.renderShell(); this.initializeMap(); }

  renderShell() {
    if (!this.config || this.querySelector(".pilot-map-canvas")) return;
    this.innerHTML = `<style>
      pilot-tracker-live-map{display:block} .pilot-live-shell{overflow:hidden;background:var(--ha-card-background,var(--card-background-color,#fff))}
      .pilot-live-title{display:flex;justify-content:space-between;align-items:center;padding:15px 18px;font-size:21px;font-weight:600}
      .pilot-live-count{color:var(--secondary-text-color);font-size:15px;font-weight:400}.pilot-map-canvas{height:${Number(this.config.height) || 420}px;background:#dbe8cf}
      .pilot-plane{width:42px;height:42px;filter:drop-shadow(0 2px 2px #0008)}.pilot-plane svg{fill:#049bd3;stroke:#fff;stroke-width:.5}
      .pilot-live-info{padding:14px 18px;border-top:1px solid var(--divider-color)}.pilot-live-flight{font-size:21px;font-weight:700}
      .pilot-live-route{margin-top:6px;color:var(--secondary-text-color);font-size:16px}.pilot-live-stats{margin-top:7px;color:var(--secondary-text-color);font-size:14px}
    </style><div class="pilot-live-shell"><div class="pilot-live-title"><span>${escapeHtml(this.config.title)}</span><span class="pilot-live-count">1 tracked</span></div><div class="pilot-map-canvas"></div><div class="pilot-live-info"></div></div>`;
    this.initializeMap();
  }

  async initializeMap() {
    if (this._map || this._initializing) return;
    this._initializing = true;
    try {
      const L = await loadPilotLeaflet();
      if (!this.isConnected || !this.querySelector(".pilot-map-canvas")) return;
      this._L = L;
      this._map = L.map(this.querySelector(".pilot-map-canvas"), {zoomControl:true}).setView([39, -98], 4);
      this._tileLayer = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom:18,
        keepBuffer:4,
        updateWhenIdle:true,
        attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      }).addTo(this._map);
      this._track = L.polyline([], {color:"#ff8a00", weight:5, opacity:.9}).addTo(this._map);
      this._resizeObserver = new ResizeObserver(() => {
        if (this._map && this.offsetParent !== null) this.stabilizeLayout();
      });
      this._resizeObserver.observe(this);
      if (window.IntersectionObserver) {
        this._intersectionObserver = new IntersectionObserver((entries) => {
          if (entries.some((entry) => entry.isIntersecting)) this.stabilizeLayout();
        });
        this._intersectionObserver.observe(this);
      }
      this.stabilizeLayout();
      this.updateMap();
    } catch (error) {
      const canvas = this.querySelector(".pilot-map-canvas");
      if (canvas) canvas.textContent = error.message;
    } finally {
      this._initializing = false;
    }
  }

  stabilizeLayout() {
    if (!this._map || this.offsetParent === null) return;
    clearTimeout(this._layoutTimer);
    this._map.invalidateSize({pan:false, debounceMoveend:true});
    // Bubble/conditional cards animate their expansion. Measure once more after
    // that transition so Leaflet requests tiles for the final card dimensions.
    this._layoutTimer = setTimeout(() => {
      if (!this._map || this.offsetParent === null) return;
      this._map.invalidateSize({pan:false, debounceMoveend:true});
      this.fitCurrentFlight();
    }, 400);
  }

  fitCurrentFlight() {
    if (!this._map || !this._hass || !this.config) return;
    const state = this._hass.states[this.config.entity];
    const flight = state?.attributes?.flights?.[0];
    if (!flight || flight.latitude == null || flight.longitude == null) return;
    const bounds = String(state.attributes.bounds || "").split(",").map(Number);
    if (bounds.length === 4 && bounds.every(Number.isFinite)) {
      this._map.fitBounds([[bounds[1],bounds[2]],[bounds[0],bounds[3]]]);
    } else {
      this._map.setView([Number(flight.latitude), Number(flight.longitude)], 6);
    }
  }

  updateMap() {
    if (!this._hass || !this.config) return;
    if (!this._map) { this.renderShell(); this.initializeMap(); return; }
    const state = this._hass.states[this.config.entity];
    const flight = state?.attributes?.flights?.[0];
    if (!flight || flight.latitude == null || flight.longitude == null) return;
    const latitude = Number(flight.latitude);
    const longitude = Number(flight.longitude);
    const heading = Number(flight.heading ?? flight.track ?? 0);
    const key = flight.flight_number || flight.callsign || flight.id || flight.aircraft_registration || "flight";
    const icon = this._L.divIcon({className:"", iconSize:[42,42], iconAnchor:[21,21], html:`<div class="pilot-plane" style="transform:rotate(${heading}deg)"><svg viewBox="0 0 24 24"><path d="M21,16V14L13,9V3.5C13,2.67 12.33,2 11.5,2S10,2.67 10,3.5V9L2,14V16L10,13.5V19L8,20.5V22L11.5,21L15,22V20.5L13,19V13.5L21,16Z"/></svg></div>`});
    if (!this._marker) this._marker = this._L.marker([latitude, longitude], {icon}).addTo(this._map);
    else { this._marker.setLatLng([latitude, longitude]); this._marker.setIcon(icon); }
    const points = Array.isArray(flight.pilot_track) ? flight.pilot_track.filter(point => Array.isArray(point) && point.length >= 2) : [];
    this._track.setLatLngs(points);
    if (this._flightKey !== key) {
      this._flightKey = key;
      const bounds = String(state.attributes.bounds || "").split(",").map(Number);
      if (bounds.length === 4 && bounds.every(Number.isFinite)) this._map.fitBounds([[bounds[1],bounds[2]],[bounds[0],bounds[3]]]);
      else this._map.setView([latitude, longitude], 6);
    }
    const flightNumber = flight.flight_number || flight.callsign || key;
    const origin = flight.airport_origin_code_iata || flight.origin || "—";
    const destination = flight.airport_destination_code_iata || flight.destination || "—";
    const speed = flight.ground_speed != null ? `${Math.round(Number(flight.ground_speed))} kts` : "";
    const altitude = flight.altitude != null ? `${Math.round(Number(flight.altitude))} ft` : "";
    const info = this.querySelector(".pilot-live-info");
    if (info) info.innerHTML = `<div class="pilot-live-flight">✈ ${escapeHtml(flightNumber)}</div><div class="pilot-live-route">${escapeHtml(origin)} → ${escapeHtml(destination)}</div><div class="pilot-live-stats">${escapeHtml([speed, altitude].filter(Boolean).join(" · "))}</div>`;
  }

  disconnectedCallback() {
    clearTimeout(this._layoutTimer);
    this._resizeObserver?.disconnect();
    this._intersectionObserver?.disconnect();
    if (this._map) this._map.remove();
    this._map = null;
  }
}

if (!customElements.get("pilot-tracker-live-map")) {
  customElements.define("pilot-tracker-live-map", PilotTrackerLiveMap);
}

class PilotTrackerCard extends HTMLElement {
  setConfig(config) {
    this.config = {
      trip_entity: "sensor.trip",
      status_entity: "sensor.status",
      current_flight_entity: "sensor.current_flight",
      current_origin_entity: "sensor.current_origin",
      current_destination_entity: "sensor.current_destination",
      next_flight_entity: "sensor.next_flight",
      next_origin_entity: "sensor.next_origin",
      next_destination_entity: "sensor.next_destination",
      tracking_entity: "sensor.tracking_source",
      map_entity: "sensor.pilot_tracker_flight_map",
      title: "Pilot Tracker",
      ...config,
    };
    this.render();
  }

  set hass(hass) {
    this._hass = hass;
    const signature = this.uiSignature();
    if (this._uiSignature === signature && this.querySelector("ha-card")) {
      const map = this.querySelector("pilot-tracker-live-map");
      if (map) map.hass = hass;
      return;
    }
    this._uiSignature = signature;
    this.render();
  }

  getCardSize() { return 10; }

  getGridOptions() {
    return {columns: 12, min_columns: 6, rows: 12, min_rows: 8};
  }

  state(id, fallback = "—") {
    return this._hass?.states?.[id]?.state ?? fallback;
  }

  attr(id, key, fallback = null) {
    return this._hass?.states?.[id]?.attributes?.[key] ?? fallback;
  }

  connectedCallback() { this.render(); }

  uiSignature() {
    if (!this._hass || !this.config) return "";
    const ids = [this.config.trip_entity, this.config.status_entity,
      this.config.current_flight_entity, this.config.current_origin_entity,
      this.config.current_destination_entity, this.config.next_flight_entity,
      this.config.next_origin_entity, this.config.next_destination_entity,
      this.config.tracking_entity, this.config.map_entity];
    return JSON.stringify(ids.map((id) => {
      const state = this._hass.states[id];
      if (!state) return null;
      return [state.state, state.attributes.schedules, state.attributes.last_candidate_rejection,
        state.attributes.last_candidate_rejection_detail,
        state.attributes.tracked_flights, state.attributes.position_fresh,
        state.attributes.scheduled_departure, state.attributes.scheduled_arrival];
    }));
  }

  render() {
    if (!this.config || !this._hass) return;
    const status = this.state(this.config.status_entity, "unknown");
    const trip = this.state(this.config.trip_entity, "none");
    const schedules = this.attr(this.config.trip_entity, "schedules", []);
    const selectedSchedule = schedules.find((item) => item.key === this._selectedScheduleKey)
      || schedules.find((item) => item.selected) || schedules[0];
    this._selectedScheduleKey = selectedSchedule?.key;
    const rejection = this.attr(this.config.tracking_entity, "last_candidate_rejection");
    const rejectionDetail = this.attr(this.config.tracking_entity, "last_candidate_rejection_detail");
    const conflicts = schedules.filter((item) => item.conflicting);
    const current = this.state(this.config.current_flight_entity, "none");
    const next = this.state(this.config.next_flight_entity, "none");
    const hasFlight = Number(this.state(this.config.map_entity, "0")) > 0;
    const nextDeparture = this.attr(this.config.next_flight_entity, "scheduled_departure");
    this.innerHTML = `
      <ha-card>
        <style>
          ha-card { overflow:hidden; }
          .header { padding:20px 20px 14px; display:flex; justify-content:space-between; gap:12px; align-items:center; }
          h2 { margin:0; font-size:24px; }
          .status { border-radius:999px; padding:7px 12px; font-weight:600; background:var(--secondary-background-color); }
          .status.tracking_flight { background:#1976d2; color:white; }
          .status.error { background:var(--error-color); color:white; }
          .route { display:grid; grid-template-columns:1fr auto 1fr; align-items:center; padding:4px 22px 18px; gap:14px; }
          .airport { font-size:32px; font-weight:700; }
          .airport:last-child { text-align:right; }
          .flight { text-align:center; color:var(--secondary-text-color); }
          .map { min-height:360px; border-top:1px solid var(--divider-color); border-bottom:1px solid var(--divider-color); }
          .next-panel { margin:0 20px 14px; padding:24px; border-radius:14px; text-align:center; background:var(--secondary-background-color); }
          .next-route { font-size:30px; font-weight:700; margin:8px 0; }
          .details { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; padding:16px 20px; }
          .label { color:var(--secondary-text-color); font-size:12px; text-transform:uppercase; }
          .value { font-size:17px; margin-top:4px; }
          .alert { margin:12px 20px; padding:14px; border-radius:10px; background:color-mix(in srgb,var(--error-color) 18%,transparent); }
          .actions { display:flex; flex-wrap:wrap; gap:8px; padding:4px 20px 18px; }
          button { border:0; border-radius:18px; padding:9px 14px; cursor:pointer; color:var(--primary-text-color); background:var(--secondary-background-color); }
          button.primary { background:var(--primary-color); color:var(--text-primary-color); }
          .schedules { padding:0 20px 20px; }
          .schedule-picker { width:100%; box-sizing:border-box; margin:8px 0 12px; padding:11px; border:1px solid var(--divider-color); border-radius:10px; color:var(--primary-text-color); background:var(--card-background-color); }
          .schedule-detail { border:1px solid var(--divider-color); border-radius:12px; overflow:hidden; }
          .schedule-head { display:flex; justify-content:space-between; gap:12px; align-items:center; padding:14px; background:var(--secondary-background-color); }
          .schedule-meta { color:var(--secondary-text-color); margin-top:4px; }
          .leg-table { overflow-x:auto; }
          .legs { width:100%; border-collapse:collapse; }
          .legs th,.legs td { padding:10px 8px; text-align:left; border-top:1px solid var(--divider-color); white-space:nowrap; }
          .legs th { color:var(--secondary-text-color); font-size:11px; text-transform:uppercase; }
          .leg-route { font-weight:600; }
          .schedule-actions { display:flex; gap:8px; padding:12px 14px; border-top:1px solid var(--divider-color); }
          .danger { color:var(--error-color); }
          textarea { box-sizing:border-box; width:100%; min-height:180px; padding:10px; color:var(--primary-text-color); background:var(--card-background-color); }
          dialog { width:min(680px,90vw); border:0; border-radius:14px; color:var(--primary-text-color); background:var(--card-background-color); }
          @media(max-width:600px){ .details{grid-template-columns:1fr 1fr}.map{min-height:300px}.airport{font-size:25px}.schedule-head{align-items:flex-start;flex-direction:column}.legs th:nth-child(4),.legs td:nth-child(4){display:none}.legs th,.legs td{padding:9px 5px;font-size:13px} }
        </style>
        <div class="header"><h2>${this.config.title}</h2><span class="status ${status}">${this.pretty(status)}</span></div>
        <div class="route">
          <div class="airport">${this.state(this.config.current_origin_entity)}</div>
          <div class="flight"><ha-icon icon="mdi:airplane"></ha-icon><br>${current}</div>
          <div class="airport">${this.state(this.config.current_destination_entity)}</div>
        </div>
        ${rejection ? `<div class="alert"><b>Attention:</b> ${this.rejectionText(rejection, rejectionDetail)}</div>` : ""}
        ${conflicts.length ? `<div class="alert"><b>Schedule conflict:</b> Choose the pairing to keep below.</div>` : ""}
        ${hasFlight ? `<div class="map" id="pilot-map"></div>` : this.nextFlightPanel(next)}
        <div class="details">
          <div><div class="label">Trip</div><div class="value">${trip}</div></div>
          <div><div class="label">Current flight</div><div class="value">${current}</div></div>
          <div><div class="label">Next flight</div><div class="value">${next}</div></div>
          <div><div class="label">Next route</div><div class="value">${this.state(this.config.next_origin_entity)} → ${this.state(this.config.next_destination_entity)}</div></div>
          <div><div class="label">Tracked flights</div><div class="value">${this.attr(this.config.tracking_entity,"tracked_flights",0)}</div></div>
          <div><div class="label">Position</div><div class="value">${this.attr(this.config.tracking_entity,"position_fresh",false) ? "Live" : "Waiting"}</div></div>
          <div><div class="label">Current departure · Device</div><div class="value">${escapeHtml(deviceTime(this.attr(this.config.current_flight_entity,"scheduled_departure")))}</div></div>
          <div><div class="label">Current departure · ${escapeHtml(this.attr(this.config.current_flight_entity,"origin_icao","Origin"))}</div><div class="value">${escapeHtml(this.attr(this.config.current_flight_entity,"departure_local_display","—"))}</div></div>
          <div><div class="label">Next departure · Device</div><div class="value">${escapeHtml(deviceTime(this.attr(this.config.next_flight_entity,"scheduled_departure")))}</div></div>
          <div><div class="label">Next departure · ${escapeHtml(this.attr(this.config.next_flight_entity,"origin_icao","Origin"))}</div><div class="value">${escapeHtml(this.attr(this.config.next_flight_entity,"departure_local_display","—"))}</div></div>
        </div>
        <div class="actions">
          <button class="primary" id="import">Import schedule</button>
          ${rejection === "aircraft_identifier_changed" ? `<button id="aircraft">Accept aircraft swap</button>` : ""}
          ${current !== "none" ? `<button id="arrival">Confirm arrival</button>` : ""}
        </div>
        <div class="schedules"><div class="label">Schedule manager</div>
          ${schedules.length ? `
            <select class="schedule-picker" id="schedule-select" aria-label="Select a loaded schedule">
              ${schedules.map((item) => `<option value="${escapeHtml(item.key)}" ${item.key === selectedSchedule?.key ? "selected" : ""}>${escapeHtml(item.trip_id)} · ${escapeHtml(item.start)}–${escapeHtml(item.end)} · ${item.leg_count} legs${item.selected ? " · active" : ""}${item.conflicting ? " · conflict" : ""}</option>`).join("")}
            </select>
            <button id="schedule-toggle">${this._scheduleExpanded ? "Hide schedule" : "View schedule"}</button>
            ${this._scheduleExpanded ? this.scheduleDetail(selectedSchedule) : ""}
          ` : `<div class="schedule-head">No schedules loaded</div>`}
        </div>
        <dialog id="import-dialog"><h3>Import Southwest pairing</h3><textarea id="schedule-text" placeholder="Paste HERB TIME/ESTIMATED pairing text"></textarea><div class="actions"><button id="cancel">Cancel</button><button class="primary" id="submit">Import</button></div></dialog>
      </ha-card>`;
    this.bind();
    this.renderMap();
  }

  pretty(value) { return String(value ?? "").replaceAll("_", " ").replace(/\b\w/g, c => c.toUpperCase()); }

  rejectionText(rejection, detail) {
    if (rejection === "missing_origin_data") {
      return `FlightRadar24 returned ${escapeHtml(detail?.flight || "this flight")} without origin route data; waiting for complete live details.`;
    }
    if (rejection === "missing_destination_data") {
      return `FlightRadar24 returned ${escapeHtml(detail?.flight || "this flight")} without destination route data; waiting for complete live details.`;
    }
    if (rejection === "origin_mismatch" && detail) {
      return `Origin mismatch for ${escapeHtml(detail.flight)}: expected ${escapeHtml(detail.expected_origin)}, FlightRadar24 returned ${escapeHtml(detail.received_origin || "unknown")}.`;
    }
    if (rejection === "destination_mismatch" && detail) {
      return `Destination mismatch for ${escapeHtml(detail.flight)}: expected ${escapeHtml(detail.expected_destination)}, FlightRadar24 returned ${escapeHtml(detail.received_destination || "unknown")}.`;
    }
    return escapeHtml(this.pretty(rejection));
  }

  nextFlightPanel(next) {
    const attributes = this._hass.states[this.config.next_flight_entity]?.attributes || {};
    const local = attributes.departure_local_display || "Schedule unavailable";
    const airport = attributes.origin_icao || attributes.origin || "Origin";
    const device = deviceTime(attributes.scheduled_departure);
    return `<div class="next-panel"><div class="label">Next scheduled flight</div><div class="next-route">${this.state(this.config.next_origin_entity)} → ${this.state(this.config.next_destination_entity)}</div><div>${next}</div><div style="margin-top:10px"><b>Device time:</b> ${escapeHtml(device)}</div><div><b>${escapeHtml(airport)} time:</b> ${escapeHtml(local)}</div></div>`;
  }

  formatDeviceTime(value) {
    return deviceTime(value);
  }

  scheduleDetail(schedule) {
    if (!schedule) return "";
    return `<div class="schedule-detail">
      <div class="schedule-head"><div><b>${escapeHtml(schedule.trip_id)}</b>${schedule.selected ? " · Active" : ""}${schedule.conflicting ? " · Conflict" : ""}<div class="schedule-meta">${escapeHtml(schedule.start)}–${escapeHtml(schedule.end)} · ${schedule.leg_count} legs · ${escapeHtml(this.pretty(schedule.time_mode))} time</div></div></div>
      <div class="leg-table"><table class="legs"><thead><tr><th>Date</th><th>Flight</th><th>Route</th><th>Duty</th><th>Departure</th><th>Arrival</th><th>Status</th></tr></thead><tbody>
        ${(schedule.legs || []).map((leg) => `<tr><td>${escapeHtml(this.shortDate(leg.departure))}</td><td><b>${escapeHtml(leg.flight)}</b></td><td class="leg-route">${escapeHtml(leg.origin)} → ${escapeHtml(leg.destination)}</td><td>${escapeHtml(leg.duty_period)}</td><td><b>Device:</b> ${escapeHtml(this.formatDeviceTime(leg.departure))}<br><b>${escapeHtml(leg.origin_icao || leg.origin)}:</b> ${escapeHtml(leg.departure_local_display)}</td><td><b>Device:</b> ${escapeHtml(this.formatDeviceTime(leg.arrival))}<br><b>${escapeHtml(leg.destination_icao || leg.destination)}:</b> ${escapeHtml(leg.arrival_local_display)}</td><td>${escapeHtml(this.pretty(leg.status))}</td></tr>`).join("")}
      </tbody></table></div>
      <div class="schedule-actions">${schedule.conflicting ? `<button data-keep="${escapeHtml(schedule.key)}">Keep this schedule</button>` : ""}<button class="danger" data-remove="${escapeHtml(schedule.key)}">Delete this schedule</button></div>
    </div>`;
  }

  formatDate(value) {
    if (!value) return "Schedule unavailable";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], {weekday:"short", month:"short", day:"numeric", hour:"numeric", minute:"2-digit"});
  }

  shortDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString([], {month:"short", day:"numeric"});
  }

  shortTime(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString([], {hour:"numeric", minute:"2-digit", timeZoneName:"short"});
  }

  bind() {
    const q = (selector) => this.querySelector(selector);
    q("#import")?.addEventListener("click", () => q("#import-dialog").showModal());
    q("#cancel")?.addEventListener("click", () => q("#import-dialog").close());
    q("#schedule-select")?.addEventListener("change", (event) => {
      this._selectedScheduleKey = event.target.value;
      this._scheduleExpanded = false;
      this.render();
    });
    q("#schedule-toggle")?.addEventListener("click", () => {
      this._scheduleExpanded = !this._scheduleExpanded;
      this.render();
    });
    q("#submit")?.addEventListener("click", async () => {
      await this._hass.callService("pilot_tracker", "import_schedule", {schedule_text:q("#schedule-text").value});
      q("#import-dialog").close();
    });
    q("#aircraft")?.addEventListener("click", () => this.confirmCall("Accept the newly detected aircraft for this leg?", "reset_aircraft"));
    q("#arrival")?.addEventListener("click", () => this.confirmCall("Confirm that the current flight has arrived?", "complete_current_leg"));
    this.querySelectorAll("[data-remove]").forEach((button) => button.addEventListener("click", () => {
      const schedule = this.attr(this.config.trip_entity, "schedules", []).find((item) => item.key === button.dataset.remove);
      const description = schedule ? `${schedule.trip_id}, ${schedule.start}–${schedule.end}, ${schedule.leg_count} legs` : "this schedule";
      if (confirm(`Delete ${description}? This cannot be undone.`)) this._hass.callService("pilot_tracker", "remove_schedule", {trip_key:button.dataset.remove});
    }));
    this.querySelectorAll("[data-keep]").forEach((button) => button.addEventListener("click", () => {
      if (confirm("Keep this schedule and delete the conflicting schedule(s)?")) this._hass.callService("pilot_tracker", "resolve_schedule_conflict", {keep_trip_key:button.dataset.keep});
    }));
  }

  confirmCall(message, service) { if (confirm(message)) this._hass.callService("pilot_tracker", service, {}); }

  renderMap() {
    const host = this.querySelector("#pilot-map");
    if (!host || !host.isConnected || host.firstChild) return;
    const card = document.createElement("pilot-tracker-live-map");
    card.setConfig({entity:this.config.map_entity, title:"Live aircraft", height:360});
    card.hass = this._hass;
    host.appendChild(card);
  }
}

if (!customElements.get("pilot-tracker-card")) {
  customElements.define("pilot-tracker-card", PilotTrackerCard);
}
window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "pilot-tracker-card")) {
  window.customCards.push({type:"pilot-tracker-card", name:"Pilot Tracker", description:"Schedule and live aircraft control for Pilot Tracker"});
}

class PilotTrackerMapCard extends HTMLElement {
  setConfig(config) {
    this.config = {
      map_entity: "sensor.pilot_tracker_flight_map",
      next_flight_entity: "sensor.next_flight",
      next_origin_entity: "sensor.next_origin",
      next_destination_entity: "sensor.next_destination",
      title: "Pilot",
      ...config,
    };
  }
  set hass(hass) {
    this._hass = hass;
    const mapState = hass.states[this.config.map_entity]?.state || "0";
    const nextState = hass.states[this.config.next_flight_entity];
    const signature = JSON.stringify([mapState, nextState?.state, nextState?.attributes?.scheduled_departure,
      hass.states[this.config.next_origin_entity]?.state, hass.states[this.config.next_destination_entity]?.state]);
    if (this._signature === signature && this.querySelector("pilot-tracker-live-map")) {
      this.querySelector("pilot-tracker-live-map").hass = hass;
      return;
    }
    this._signature = signature;
    this.render();
  }
  getCardSize() { return 8; }
  getGridOptions() {
    return {columns: 12, min_columns: 6, rows: 8, min_rows: 4};
  }
  connectedCallback() { this.render(); }
  render() {
    if (!this._hass || !this.config) return;
    const active = Number(this._hass.states[this.config.map_entity]?.state || 0) > 0;
    if (!active) {
      const flight = this._hass.states[this.config.next_flight_entity];
      const origin = this._hass.states[this.config.next_origin_entity]?.state || "—";
      const destination = this._hass.states[this.config.next_destination_entity]?.state || "—";
      const local = flight?.attributes?.departure_local_display || "Schedule unavailable";
      const airport = flight?.attributes?.origin_icao || origin;
      const device = deviceTime(flight?.attributes?.scheduled_departure);
      this.innerHTML = `<ha-card><style>.wrap{padding:28px;text-align:center}.label{color:var(--secondary-text-color);text-transform:uppercase;font-size:12px}.route{font-size:34px;font-weight:700;margin:10px}.flight{font-size:17px}.times{margin-top:10px;line-height:1.55}</style><div class="wrap"><div class="label">Next scheduled flight</div><div class="route">${escapeHtml(origin)} → ${escapeHtml(destination)}</div><div class="flight">${escapeHtml(flight?.state || "—")}</div><div class="times"><b>Device time:</b> ${escapeHtml(device)}<br><b>${escapeHtml(airport)} time:</b> ${escapeHtml(local)}</div></div></ha-card>`;
      return;
    }
    this.innerHTML = `<ha-card><div id="map"></div></ha-card>`;
    const host = this.querySelector("#map");
    if (!host || !host.isConnected || host.firstChild) return;
    const card = document.createElement("pilot-tracker-live-map");
    card.setConfig({entity:this.config.map_entity, title:this.config.title, height:this.config.height || 420});
    card.hass = this._hass;
    host.appendChild(card);
  }
}

if (!customElements.get("pilot-tracker-map-card")) {
  customElements.define("pilot-tracker-map-card", PilotTrackerMapCard);
}
if (!window.customCards.some((card) => card.type === "pilot-tracker-map-card")) {
  window.customCards.push({type:"pilot-tracker-map-card", name:"Pilot Tracker Map", description:"Clean live aircraft map with next-flight fallback"});
}
