import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { jsPDF } from "jspdf";

const API_BASE = "http://127.0.0.1:8001";

const initialForm = {
  destination: "",
  cityStops: "",
  destinationSplit: "",
  duration: "",
  budget: "",
  groupSize: "",
  dietaryRestrictions: "",
  interests: "",
  startDate: "",
};

const LEAFLET_JS_URL = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
const LEAFLET_CSS_URL = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";

function ensureLeafletAssets() {
  if (!document.querySelector(`link[href="${LEAFLET_CSS_URL}"]`)) {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = LEAFLET_CSS_URL;
    document.head.appendChild(link);
  }

  return new Promise((resolve, reject) => {
    if (window.L) {
      resolve(window.L);
      return;
    }

    const existing = document.querySelector(`script[src="${LEAFLET_JS_URL}"]`);
    if (existing) {
      existing.addEventListener("load", () => resolve(window.L), {
        once: true,
      });
      existing.addEventListener("error", reject, { once: true });
      return;
    }

    const script = document.createElement("script");
    script.src = LEAFLET_JS_URL;
    script.async = true;
    script.onload = () => resolve(window.L);
    script.onerror = reject;
    document.body.appendChild(script);
  });
}

function CityStopsMap({ markers, error, large = false, onExpand }) {
  const mapContainerRef = useRef(null);
  const mapRef = useRef(null);
  const markerRefs = useRef([]);

  useEffect(() => {
    let cancelled = false;

    if (!mapContainerRef.current || !markers?.length) {
      return undefined;
    }

    ensureLeafletAssets()
      .then((L) => {
        if (cancelled || !mapContainerRef.current || !L) {
          return;
        }

        if (!mapRef.current) {
          mapRef.current = L.map(mapContainerRef.current, {
            zoomControl: true,
          }).setView([markers[0].lat, markers[0].lng], 3);

          L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
            attribution: "&copy; OpenStreetMap contributors",
            maxZoom: 19,
          }).addTo(mapRef.current);
        }

        markerRefs.current.forEach((marker) => marker.remove());
        markerRefs.current = [];

        const bounds = [];
        markers.forEach((markerData) => {
          const marker = L.marker([markerData.lat, markerData.lng])
            .bindPopup(markerData.label)
            .addTo(mapRef.current);
          markerRefs.current.push(marker);
          bounds.push([markerData.lat, markerData.lng]);
        });

        if (bounds.length > 0) {
          mapRef.current.fitBounds(bounds, { padding: [24, 24], maxZoom: 8 });
        }
      })
      .catch(() => {
        // The fallback text below is enough if the map library cannot load.
      });

    return () => {
      cancelled = true;
    };
  }, [markers]);

  useEffect(() => {
    return () => {
      markerRefs.current.forEach((marker) => marker.remove());
      markerRefs.current = [];
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }
    };
  }, []);

  if (!markers?.length) {
    return null;
  }

  return (
    <div className={`map-box ${large ? "map-box-large" : ""}`}>
      <div className="map-box-header">
        <h3>City Stops Map</h3>
        {!large && onExpand ? (
          <button
            type="button"
            className="secondary-btn map-expand-btn"
            onClick={onExpand}
          >
            Expand
          </button>
        ) : null}
      </div>
      {error ? <p className="route-error">{error}</p> : null}
      <button
        type="button"
        className={`map-canvas-button ${large ? "is-large" : ""}`}
        onClick={large ? undefined : onExpand}
      >
        <div ref={mapContainerRef} className={`map-canvas ${large ? "is-large" : ""}`} />
      </button>
      <p className="map-caption">{markers.map((marker) => marker.label).join(" → ")}</p>
    </div>
  );
}

function App() {
  const [messages, setMessages] = useState([
    {
      role: "assistant",
      content:
        "Hi! Fill out the trip form or send me a message, and I’ll build a travel plan for you.",
    },
  ]);
  const [input, setInput] = useState("");
  const [tripState, setTripState] = useState({});
  const [loading, setLoading] = useState(false);
  const [form, setForm] = useState(initialForm);
  const [mapExpanded, setMapExpanded] = useState(false);
  const [lastMeta, setLastMeta] = useState({
    workflowStage: "",
    awaitingClarification: false,
    validationIssues: [],
    retrievedChunks: [],
    routeChecks: [],
    routeError: "",
    cityMarkers: [],
    cityMarkerError: "",
  });

  const canSubmitForm = useMemo(() => {
    return Object.values(form).some((value) => String(value).trim() !== "");
  }, [form]);

  const latestAssistantMessage = useMemo(() => {
    return [...messages]
      .reverse()
      .find((msg) => msg.role === "assistant" && msg.content?.trim());
  }, [messages]);

  const activeDestinations = useMemo(() => {
    const destinations = tripState?.trip_overview?.destinations;
    if (Array.isArray(destinations) && destinations.length > 0) {
      return destinations;
    }
    return form.destination
      .split(/,| and /i)
      .map((value) => value.trim())
      .filter(Boolean);
  }, [form.destination, tripState]);

  const activeSplit = useMemo(() => {
    const allocations = tripState?.trip_overview?.destination_day_allocations;
    if (allocations && typeof allocations === "object") {
      return Object.entries(allocations)
        .map(([destination, days]) => `${destination}: ${days} days`)
        .join(" | ");
    }
    return "";
  }, [tripState]);

  const activeCities = useMemo(() => {
    const cities = tripState?.trip_overview?.cities;
    if (Array.isArray(cities) && cities.length > 0) {
      return cities;
    }
    return form.cityStops
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
  }, [form.cityStops, tripState]);

  const handleFormChange = (e) => {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
  };

  const normalizeDestinationSplit = (rawValue) => {
    const parts = rawValue
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean);

    const normalized = parts
      .map((part) => {
        let match = part.match(
          /^(.+?)\s+(\d+)\s*(day|days|week|weeks|month|months)$/i
        );
        if (match) {
          const [, destination, amount, unit] = match;
          return `${destination.trim()} for ${amount} ${unit}`;
        }

        match = part.match(
          /^(\d+)\s*(day|days|week|weeks|month|months)\s+(.+)$/i
        );
        if (match) {
          const [, amount, unit, destination] = match;
          return `${destination.trim()} for ${amount} ${unit}`;
        }

        return part;
      })
      .filter(Boolean);

    if (normalized.length === 0) {
      return "";
    }

    return `I want to stay in ${normalized.join(" and ")}.`;
  };

  const buildFormMessage = () => {
    const lines = [];

    if (form.destination.trim()) {
      lines.push(`I want to visit ${form.destination.trim()}.`);
    }
    if (form.cityStops.trim()) {
      lines.push(`The city stops I want to visit are ${form.cityStops.trim()}.`);
    }
    if (form.duration.trim()) {
      lines.push(`The trip duration is ${form.duration.trim()} days.`);
    }
    if (form.destinationSplit.trim()) {
      lines.push(normalizeDestinationSplit(form.destinationSplit.trim()));
    }
    if (form.startDate) {
      lines.push(`My trip starts on ${form.startDate}.`);
    }
    if (form.budget.trim()) {
      lines.push(`My total budget is ${form.budget.trim()} CAD.`);
    }
    if (form.groupSize.trim()) {
      lines.push(`There are ${form.groupSize.trim()} travelers in the group.`);
    }
    if (form.interests.trim()) {
      lines.push(`Our interests are ${form.interests.trim()}.`);
    }
    if (form.dietaryRestrictions.trim()) {
      lines.push(
        `Dietary restrictions or food preferences: ${form.dietaryRestrictions.trim()}.`
      );
    }

    return lines.join(" ");
  };

  const sendMessage = async (messageText) => {
    if (!messageText.trim()) return;

    setMessages((prev) => [...prev, { role: "user", content: messageText }]);
    setLoading(true);

    try {
      const response = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          message: messageText,
          state: tripState,
        }),
      });

      if (!response.ok) {
        throw new Error(`Request failed with status ${response.status}`);
      }

      const data = await response.json();
      setTripState(data.state || {});

      const parts = [];

      if (data.assistant_message) {
        parts.push(data.assistant_message);
      } else {
        parts.push("No response received from the backend.");
      }

      if (data.clarifying_questions?.length) {
        parts.push(
          `### Clarifying questions\n${data.clarifying_questions
            .map((q) => `- ${q}`)
            .join("\n")}`
        );
      }

      if (data.validation_issues?.length) {
        parts.push(
          `### Validation issues\n${data.validation_issues
            .map((issue) => `- ${issue}`)
            .join("\n")}`
        );
      }

      setLastMeta({
        workflowStage: data.workflow_stage || "",
        awaitingClarification: Boolean(data.awaiting_clarification),
        validationIssues: data.validation_issues || [],
        retrievedChunks: data.retrieved_chunks || [],
        routeChecks: data.route_checks || [],
        routeError: data.route_error || "",
        cityMarkers: data.city_markers || [],
        cityMarkerError: data.city_marker_error || "",
      });

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: parts.join("\n\n"),
        },
      ]);
    } catch (error) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content:
            "I couldn’t connect to the backend. Make sure FastAPI is running on port 8001.",
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleChatSubmit = async (e) => {
    e.preventDefault();
    const text = input.trim();
    if (!text) return;
    setInput("");
    await sendMessage(text);
  };

  const handleFormSubmit = async (e) => {
    e.preventDefault();
    const formMessage = buildFormMessage();
    if (!formMessage) return;
    await sendMessage(formMessage);
  };

  const handleReset = () => {
    setForm(initialForm);
    setTripState({});
    setLastMeta({
      workflowStage: "",
      awaitingClarification: false,
      validationIssues: [],
      retrievedChunks: [],
      routeChecks: [],
      routeError: "",
      cityMarkers: [],
      cityMarkerError: "",
    });
    setMessages([
      {
        role: "assistant",
        content:
          "Session reset. Fill out the trip form or send me a new travel request.",
      },
    ]);
    setInput("");
  };

  const handleExportPdf = () => {
    if (!latestAssistantMessage?.content) return;

    const doc = new jsPDF({
      orientation: "portrait",
      unit: "mm",
      format: "a4",
    });

    const destination =
      form.destination?.trim() ||
      tripState?.trip_overview?.destination ||
      "trip";

    const safeName = String(destination).toLowerCase().replace(/\s+/g, "-");
    const fileName = `${safeName}-itinerary.pdf`;

    const pageWidth = doc.internal.pageSize.getWidth();
    const pageHeight = doc.internal.pageSize.getHeight();
    const margin = 15;
    const maxWidth = pageWidth - margin * 2;
    const lineHeight = 7;

    let y = margin;

    const cleanText = latestAssistantMessage.content
      .replace(/```[\s\S]*?```/g, "")
      .replace(/\r/g, "");

    const lines = cleanText.split("\n");

    doc.setFont("helvetica", "bold");
    doc.setFontSize(16);
    doc.text("Travel Itinerary", margin, y);
    y += 10;

    doc.setFont("helvetica", "normal");
    doc.setFontSize(11);

    for (const rawLine of lines) {
      const line = rawLine.trimEnd();

      if (line === "") {
        y += lineHeight;
        if (y > pageHeight - margin) {
          doc.addPage();
          y = margin;
        }
        continue;
      }

      const isH1 = line.startsWith("# ");
      const isH2 = line.startsWith("## ");
      const isH3 = line.startsWith("### ");
      const isBullet = line.startsWith("- ");
      const isBoldLabel = line.startsWith("**") && line.endsWith("**");

      let text = line
        .replace(/^###\s*/, "")
        .replace(/^##\s*/, "")
        .replace(/^#\s*/, "")
        .replace(/\*\*/g, "");

      if (isH1) {
        doc.setFont("helvetica", "bold");
        doc.setFontSize(15);
      } else if (isH2) {
        doc.setFont("helvetica", "bold");
        doc.setFontSize(13);
      } else if (isH3) {
        doc.setFont("helvetica", "bold");
        doc.setFontSize(12);
      } else if (isBoldLabel) {
        doc.setFont("helvetica", "bold");
        doc.setFontSize(11);
      } else {
        doc.setFont("helvetica", "normal");
        doc.setFontSize(11);
      }

      if (isBullet) {
        text = "• " + text.replace(/^- /, "");
      }

      const wrapped = doc.splitTextToSize(text, maxWidth);

      for (const wrappedLine of wrapped) {
        if (y > pageHeight - margin) {
          doc.addPage();
          y = margin;
        }
        doc.text(wrappedLine, margin, y);
        y += lineHeight;
      }

      y += 1;
    }

    doc.save(fileName);
  };

  return (
    <div className="app-shell">
      <div className="app-background" />
      <main className="app-container">
        <header className="hero">
          <div>
            <p className="eyebrow">SE4471 Project</p>
            <h1>AI Travel Planner</h1>
            <p className="hero-subtitle">
              Plan trips with retrieval-grounded recommendations, workflow
              stages, and structured day-by-day itineraries.
            </p>
          </div>

          <div className="status-card">
            <div className="status-row">
              <span className="status-label">Stage</span>
              <span className="pill">{lastMeta.workflowStage || "ready"}</span>
            </div>
            <div className="status-row">
              <span className="status-label">Clarification</span>
              <span
                className={`pill ${
                  lastMeta.awaitingClarification ? "warn" : "ok"
                }`}
              >
                {lastMeta.awaitingClarification ? "needed" : "complete"}
              </span>
            </div>
            <div className="status-row">
              <span className="status-label">Validation issues</span>
              <span className="pill">{lastMeta.validationIssues.length}</span>
            </div>
            <div className="status-row">
              <span className="status-label">Sources retrieved</span>
              <span className="pill">{lastMeta.retrievedChunks.length}</span>
            </div>
          </div>
        </header>

        <section className="layout-grid">
          <aside className="panel form-panel">
            <div className="panel-header">
              <h2>Trip Parameters</h2>
              <p>Use the form for a fast first draft, then refine in chat.</p>
            </div>

            <form className="trip-form" onSubmit={handleFormSubmit} autoComplete="off">
              <label>
                <span>Destination(s)</span>
                <input
                  name="destination"
                  value={form.destination}
                  onChange={handleFormChange}
                  placeholder="Japan, Canada"
                />
                <small className="field-hint">
                  Enter one place or multiple places separated by commas or
                  “and”.
                </small>
              </label>

              <label>
                <span>City stops</span>
                <input
                  name="cityStops"
                  value={form.cityStops}
                  onChange={handleFormChange}
                  placeholder="Tokyo, Kyoto, Toronto, Montreal"
                />
                <small className="field-hint">
                  Optional. Add city stops to improve route checks and make the
                  itinerary more specific.
                </small>
              </label>

              <label>
                <span>Days per destination</span>
                <input
                  name="destinationSplit"
                  value={form.destinationSplit}
                  onChange={handleFormChange}
                  placeholder="Japan 5 days, Canada 5 days"
                />
                <small className="field-hint">
                  Optional for multi-stop trips. Example: `Japan 5 days,
                  Canada 5 days`.
                </small>
              </label>

              <div className="two-col">
                <label>
                  <span>Duration (days)</span>
                  <input
                    name="duration"
                    value={form.duration}
                    onChange={handleFormChange}
                    placeholder="7"
                  />
                </label>

                <label>
                  <span>Budget (CAD)</span>
                  <input
                    name="budget"
                    value={form.budget}
                    onChange={handleFormChange}
                    placeholder="2000"
                  />
                </label>
              </div>

              <div className="two-col">
                <label>
                  <span>Group size</span>
                  <input
                    name="groupSize"
                    value={form.groupSize}
                    onChange={handleFormChange}
                    placeholder="2"
                  />
                </label>

                <label>
                  <span>Start date</span>
                  <input
                    type="date"
                    name="startDate"
                    value={form.startDate || ""}
                    onChange={handleFormChange}
                    autoComplete="off"
                    className={!form.startDate ? "date-empty" : ""}
                  />
                </label>
              </div>

              <label>
                <span>Interests</span>
                <input
                  name="interests"
                  value={form.interests}
                  onChange={handleFormChange}
                  placeholder="food, culture, temples, shopping"
                />
              </label>

              <label>
                <span>Dietary restrictions</span>
                <input
                  name="dietaryRestrictions"
                  value={form.dietaryRestrictions}
                  onChange={handleFormChange}
                  placeholder="vegetarian, halal, nut allergy"
                />
              </label>

              <div className="button-row">
                <button
                  type="submit"
                  className="primary-btn"
                  disabled={loading || !canSubmitForm}
                >
                  {loading ? "Planning..." : "Generate from Form"}
                </button>

                <button
                  type="button"
                  className="secondary-btn"
                  onClick={handleReset}
                  disabled={loading}
                >
                  Reset
                </button>
              </div>
            </form>

            {(activeDestinations.length > 1 || activeSplit) && (
              <div className="trip-summary-box">
                <h3>Multi-stop trip</h3>
                {activeDestinations.length > 1 && (
                  <p>
                    <strong>Stops:</strong> {activeDestinations.join(" → ")}
                  </p>
                )}
                {activeSplit && (
                  <p>
                    <strong>Current split:</strong> {activeSplit}
                  </p>
                )}
                {!activeSplit && activeDestinations.length > 1 && (
                  <p className="summary-note">
                    Add a day split here or answer the follow-up in chat so the
                    itinerary can divide time across each destination.
                  </p>
                )}
                {activeCities.length > 0 && (
                  <p>
                    <strong>City stops:</strong> {activeCities.join(" → ")}
                  </p>
                )}
              </div>
            )}

            <CityStopsMap
              markers={lastMeta.cityMarkers}
              error={lastMeta.cityMarkerError}
              onExpand={() => setMapExpanded(true)}
            />

            {(lastMeta.routeChecks.length > 0 || lastMeta.routeError) && (
              <div className="routes-box">
                <h3>Route Checks</h3>
                {lastMeta.routeError && (
                  <p className="route-error">{lastMeta.routeError}</p>
                )}
                {lastMeta.routeChecks.length > 0 && (
                  <ul>
                    {lastMeta.routeChecks.map((route, index) => (
                      <li key={`${route.origin}-${route.destination}-${index}`}>
                        <strong>
                          {route.origin} → {route.destination}
                        </strong>
                        <div className="source-meta">
                          {route.skipped
                            ? "Needs flight or separate long-distance transfer"
                            : route.ok
                            ? `${route.duration_text} · ${route.distance_text} · ${String(
                                route.travel_mode || "DRIVE"
                              ).toLowerCase()}`
                            : route.error || "Route unavailable"}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            {lastMeta.retrievedChunks.length > 0 && (
              <div className="sources-box">
                <h3>Retrieved Sources</h3>
                <ul>
                  {lastMeta.retrievedChunks.map((chunk, index) => (
                    <li key={`${chunk.source}-${chunk.chunk_index}-${index}`}>
                      <strong>{chunk.title || chunk.source}</strong>
                      <div className="source-meta">
                        {chunk.source} · chunk {chunk.chunk_index}
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            )}

          </aside>

          <section className="panel chat-panel">
            <div className="panel-header panel-header-row">
              <div>
                <h2>Planner Chat</h2>
                <p>
                  Answer follow-up questions here and review the generated
                  itinerary.
                </p>
              </div>

              <button
                type="button"
                className="secondary-btn export-btn"
                onClick={handleExportPdf}
                disabled={!latestAssistantMessage || loading}
              >
                Export latest answer as PDF
              </button>
            </div>

            <div className="messages">
              {messages.map((msg, index) => (
                <article
                  key={index}
                  className={`message-bubble ${
                    msg.role === "user"
                      ? "user-bubble"
                      : "assistant-bubble"
                  }`}
                >
                  <div className="message-role">
                    {msg.role === "user" ? "You" : "Assistant"}
                  </div>
                  <div className="markdown-body">
                    <ReactMarkdown>{msg.content}</ReactMarkdown>
                  </div>
                </article>
              ))}

              {loading && (
                <div className="typing-indicator">
                  <span />
                  <span />
                  <span />
                </div>
              )}
            </div>

            <form className="chat-form" onSubmit={handleChatSubmit}>
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Type a follow-up, answer clarifying questions, or refine the itinerary..."
                disabled={loading}
              />
              <button
                type="submit"
                className="primary-btn"
                disabled={loading || !input.trim()}
              >
                Send
              </button>
            </form>
          </section>
        </section>
      </main>
      {mapExpanded && lastMeta.cityMarkers.length > 0 && (
        <div className="map-modal" onClick={() => setMapExpanded(false)}>
          <div
            className="map-modal-card"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="map-modal-header">
              <h2>City Stops Map</h2>
              <button
                type="button"
                className="secondary-btn"
                onClick={() => setMapExpanded(false)}
              >
                Close
              </button>
            </div>
            <CityStopsMap
              markers={lastMeta.cityMarkers}
              error={lastMeta.cityMarkerError}
              large
            />
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
