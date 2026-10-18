import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { components } from "../api/schema";

type Task = components["schemas"]["TaskView"];
const finished = new Set(["completed", "failed", "cancelled"]);
const label = (status: string) =>
  (
    ({
      requires_owner: "Needs your attention",
      retry_scheduled: "Recovery scheduled",
      queued: "Ready to continue",
      running: "Working",
      waiting: "Waiting for a result",
      paused: "Paused",
      completed: "Verified complete",
      cancelled: "Cancelled",
      failed: "Failed",
    }) as Record<string, string>
  )[status] ?? status;

export function ProjectTasks() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  async function reload() {
    try {
      setTasks(await api.projectTasks());
      setError("");
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Project work is unavailable. Refresh to try again.",
      );
    }
  }
  useEffect(() => {
    let active = true;
    async function poll() {
      try {
        const rows = await api.projectTasks();
        if (active) {
          setTasks(rows);
          setError("");
        }
      } catch (e) {
        if (active)
          setError(
            e instanceof Error
              ? e.message
              : "Project work is unavailable. Refresh to try again.",
          );
      }
    }
    void poll();
    const timer = window.setInterval(() => void poll(), 10000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);
  async function control(task: Task, action: "pause" | "resume" | "cancel") {
    setBusy(task.id);
    try {
      await api.controlProjectTask(task.id, {
        expected_revision: task.revision,
        action,
        reason: `Owner chose ${action} in project work`,
      });
      await reload();
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "The task changed. Refresh before trying again.",
      );
    } finally {
      setBusy("");
    }
  }
  return (
    <details className="assistant-controls project-tasks">
      <summary>
        OpenClaw project work
        {tasks.filter((t) => !finished.has(t.status)).length
          ? ` · ${tasks.filter((t) => !finished.has(t.status)).length} active`
          : ""}
      </summary>
      <div className="assistant-control-heading">
        <p className="muted">
          Ask OpenClaw to carry out a project goal. It follows saved work and
          checks results. You can pause or cancel it here.
        </p>
        <button className="secondary small" onClick={() => void reload()}>
          Refresh work
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!tasks.length && !error && (
        <p className="empty-copy">
          No saved project goals yet. Describe a research or engineering goal in
          your message to OpenClaw.
        </p>
      )}
      {tasks.map((task) => (
        <article
          className={`assistant-receipt project-task${task.parent_id ? " project-task-child" : ""}`}
          key={task.id}
          aria-label={task.objective}
        >
          <header>
            <strong>{task.objective}</strong>
            <span
              className={`badge ${task.status === "completed" ? "cyan" : ""}`}
            >
              {label(task.status)}
            </span>
          </header>
          {task.parent_id && (
            <p className="muted">Part of another project goal</p>
          )}
          {Boolean(task.waiting.reason) && <p>{String(task.waiting.reason)}</p>}
          {task.error && <p role="status">{task.error}</p>}
          {Boolean(task.result.summary) && <p>{String(task.result.summary)}</p>}
          <ul>
            {task.criteria.map((criterion, i) => (
              <li key={i}>{criterion.description}</li>
            ))}
          </ul>
          <p className="muted">
            Updated {new Date(task.updated_at).toLocaleString()} · Follow-up
            deadline {new Date(task.deadline).toLocaleString()}
          </p>
          {!finished.has(task.status) && (
            <div className="assistant-actions">
              {task.status === "paused" || task.status === "requires_owner" ? (
                <button
                  className="secondary small"
                  disabled={busy === task.id}
                  onClick={() => void control(task, "resume")}
                >
                  Resume work
                </button>
              ) : (
                <button
                  className="secondary small"
                  disabled={busy === task.id}
                  onClick={() => void control(task, "pause")}
                >
                  Pause work
                </button>
              )}
              <button
                className="secondary small"
                disabled={busy === task.id}
                onClick={() => void control(task, "cancel")}
              >
                Cancel work
              </button>
            </div>
          )}
          {Object.keys(task.result).length > 0 && (
            <details>
              <summary>Verified results and saved references</summary>
              <pre>{JSON.stringify(task.result, null, 2)}</pre>
            </details>
          )}
        </article>
      ))}
    </details>
  );
}
