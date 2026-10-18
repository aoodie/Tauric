// Read-only adapter for the pinned OpenClaw 2026.9.7 scheduler schema.
// Only the dedicated service-check agent is read; conversation text is never returned.
import fs from 'node:fs';
import http from 'node:http';
import { timingSafeEqual } from 'node:crypto';
import { DatabaseSync } from 'node:sqlite';
import { zstdDecompressSync } from 'node:zlib';

const agent = 'tauric-heartbeat';
const model = 'tauric-heartbeat:4b';
const stamp = value => Number.isFinite(value) && value > 0 ? new Date(value).toISOString() : null;

export function readHeartbeatStatus(root, enabled, runtimeAvailable = true, now = Date.now()) {
  const result = {
    observed_at: stamp(now), enabled, status: enabled ? 'unavailable' : 'disabled',
    model: enabled ? model : null, interval_seconds: null,
    last_started_at: null, last_finished_at: null, next_check_at: null,
    last_check_at: null, ready: null, http_status: null,
    last_run_status: null, automatic: null,
  };
  if (!enabled) return result;
  let scheduler, transcript;
  try {
    scheduler = new DatabaseSync(`${root}/state/openclaw.sqlite`, {readOnly:true});
    const rows = scheduler.prepare('SELECT job_id, enabled, job_json, state_json FROM cron_jobs WHERE agent_id = ? AND payload_kind = ? AND declaration_key = ?')
      .all(agent, 'heartbeat', `heartbeat:${agent}`);
    if (rows.length !== 1) return result;
    const row = rows[0], job = JSON.parse(row.job_json), state = JSON.parse(row.state_json);
    const interval = job.schedule?.kind === 'every' ? job.schedule.everyMs : null;
    result.enabled = Boolean(row.enabled);
    result.interval_seconds = Number.isFinite(interval) && interval > 0 ? interval / 1000 : null;
    result.next_check_at = row.enabled ? stamp(state.nextRunAtMs) : null;
    const run = scheduler.prepare('SELECT status, started_at_ms, finished_at_ms, request_run_id FROM cron_run_receipts WHERE job_id = ? AND agent_id = ? ORDER BY started_at_ms DESC LIMIT 1')
      .get(row.job_id, agent);
    result.status = row.enabled ? 'waiting' : 'paused';
    if (run) {
      result.last_started_at = stamp(run.started_at_ms);
      result.last_finished_at = stamp(run.finished_at_ms);
      result.last_run_status = ['running','ok','error','skipped','interrupted','superseded'].includes(run.status) ? run.status : null;
      result.automatic = run.request_run_id === null;
      let finished = false, localModel = false;
      const file = `${root}/agents/${agent}/agent/openclaw-agent.sqlite`;
      if (fs.existsSync(file)) {
        transcript = new DatabaseSync(file, {readOnly:true});
        // Bound the read to this run. A previous healthy check must never certify a new run.
        const events = transcript.prepare('SELECT event_json, event_zstd FROM transcript_events WHERE created_at >= ? AND created_at <= ? ORDER BY created_at DESC, seq DESC LIMIT 64')
          .all(run.started_at_ms, run.finished_at_ms ?? now);
        for (const event of events) {
          const json = event.event_json ?? zstdDecompressSync(event.event_zstd, {maxOutputLength:4194304}).toString();
          const message = JSON.parse(json).message;
          if (message?.role === 'assistant' && message.model === model && message.provider === 'tauric-heartbeat') {
            localModel = true;
            if (message.stopReason === 'stop') finished = true;
          }
          if (message?.role !== 'toolResult' || message.toolName !== 'tauric_heartbeat_status' || result.last_check_at) continue;
          const data = message.details;
          const checked = Date.parse(data?.checked_at);
          if (data?.scope !== 'api_and_migrated_storage' || data.execution_authority !== false ||
              !Number.isFinite(checked) || checked < run.started_at_ms || checked > (run.finished_at_ms ?? now) ||
              typeof data.ready !== 'boolean' || !Number.isInteger(data.http_status) || data.http_status < 100 || data.http_status > 599) continue;
          result.last_check_at = stamp(checked);
          result.ready = data.ready;
          result.http_status = data.http_status;
        }
      }
      if (row.enabled) {
        if (run.status === 'running') { result.status = 'running'; result.next_check_at = null; }
        else if (run.status !== 'ok' || result.ready === false) result.status = 'failed';
        else result.status = finished && localModel && result.ready === true && result.http_status === 200 ? 'healthy' : 'unverified';
        if ((run.status === 'running' && now - run.started_at_ms > 600000) ||
            (Number.isFinite(state.nextRunAtMs) && now - state.nextRunAtMs > 600000) ||
            (interval && now - (run.finished_at_ms ?? run.started_at_ms) > interval + 600000)) result.status = 'stale';
      }
    }
    if (!runtimeAvailable && row.enabled) result.status = 'unavailable';
    return result;
  } catch {
    // Missing/corrupt/newer schemas fail closed. No private error text reaches the browser.
    return {...result, status:'unavailable'};
  } finally {
    transcript?.close(); scheduler?.close();
  }
}

export function createHeartbeatStatusServer({root, enabled, token, runtimeAvailable}) {
  return http.createServer((request, response) => {
    response.setHeader('Cache-Control', 'no-store');
    response.setHeader('Content-Type', 'application/json');
    const supplied = Buffer.from(request.headers.authorization ?? '');
    const expected = Buffer.from(`Bearer ${token}`);
    if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
      response.writeHead(401); response.end('{"detail":"Service authentication required."}'); return;
    }
    if (request.method !== 'GET' || request.url !== '/heartbeat') {
      response.writeHead(404); response.end('{}'); return;
    }
    response.end(JSON.stringify(readHeartbeatStatus(root, enabled, runtimeAvailable())));
  });
}
