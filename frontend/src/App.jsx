import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";

const API_BASE = "http://127.0.0.1:8001";

const initialForm = {
  destination: "",
  duration: "",
  budget: "",
  groupSize: "",
  dietaryRestrictions: "",
  interests: "",
  startDate: "",
  endDate: "",
};
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
  const [lastMeta, setLastMeta] = useState({
    workflowStage: "",
    awaitingClarification: false,
    validationIssues: [],
    retrievedChunks: [],
  });

  const canSubmitForm = useMemo(() => {
    return Object.values(form).some((value) => String(value).trim() !== "");
  }, [form]);

  const handleFormChange = (e) => {
    const { name, value } = e.target;
    setForm((prev) => ({ ...prev, [name]: value }));
  };

  const buildFormMessage = () => {
    const lines = [];

    if (form.destination.trim()) {
      lines.push(`I want to visit ${form.destination.trim()}.`);
    }
    if (form.duration.trim()) {
      lines.push(`The trip duration is ${form.duration.trim()} days.`);
    }
    if (form.startDate && form.endDate) {
      lines.push(`My travel dates are ${form.startDate} to ${form.endDate}.`);
    } else if (form.startDate) {
      lines.push(`My trip starts on ${form.startDate}.`);
    } else if (form.endDate) {
      lines.push(`My trip ends on ${form.endDate}.`);
    }
    if (form.budget.trim()) {
      lines.push(`My total budget is ${form.budget.trim()} USD.`);
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
            <span className="pill">
              {lastMeta.validationIssues.length}
            </span>
          </div>
          <div className="status-row">
            <span className="status-label">Sources retrieved</span>
            <span className="pill">
              {lastMeta.retrievedChunks.length}
            </span>
          </div>
        </div>
      </header>

      <section className="layout-grid">
        <aside className="panel form-panel">
          <div className="panel-header">
            <h2>Trip Parameters</h2>
            <p>Use the form for a fast first draft, then refine in chat.</p>
          </div>

          <form className="trip-form" onSubmit={handleFormSubmit}>
            {/* Destination */}
            <label>
              <span>Destination</span>
              <input
                name="destination"
                value={form.destination}
                onChange={handleFormChange}
                placeholder="Japan"
              />
            </label>

            {/* Duration + Budget */}
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
                <span>Budget (USD)</span>
                <input
                  name="budget"
                  value={form.budget}
                  onChange={handleFormChange}
                  placeholder="2000"
                />
              </label>
            </div>

            {/* Group + Dates */}
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
                  value={form.startDate}
                  onChange={handleFormChange}
                />
              </label>
            </div>

            {/* End Date */}
            <div className="two-col">
              <label>
                <span>End date</span>
                <input
                  type="date"
                  name="endDate"
                  value={form.endDate}
                  onChange={handleFormChange}
                />
              </label>

              {/* empty space for alignment */}
              <div />
            </div>

            {/* Interests */}
            <label>
              <span>Interests</span>
              <input
                name="interests"
                value={form.interests}
                onChange={handleFormChange}
                placeholder="food, culture, temples, shopping"
              />
            </label>

            {/* Dietary */}
            <label>
              <span>Dietary restrictions</span>
              <input
                name="dietaryRestrictions"
                value={form.dietaryRestrictions}
                onChange={handleFormChange}
                placeholder="vegetarian, halal, nut allergy"
              />
            </label>

            {/* Buttons */}
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

          {/* Sources */}
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

        {/* Chat */}
        <section className="panel chat-panel">
          <div className="panel-header">
            <h2>Planner Chat</h2>
            <p>
              Answer follow-up questions here and review the generated
              itinerary.
            </p>
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
  </div>
);
}

export default App;