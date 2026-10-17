import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { zstdCompressSync } from 'node:zlib';
import { readHeartbeatStatus, createHeartbeatStatusServer } from './heartbeat-status.mjs';

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'tauric-heartbeat-test-'));
const current = Date.now(), start = current - 90000, finish = current - 10000;
fs.mkdirSync(`${root}/state`);
fs.mkdirSync(`${root}/agents/tauric-heartbeat/agent`, {recursive:true});
const db = new DatabaseSync(`${root}/state/openclaw.sqlite`);
db.exec('CREATE TABLE cron_jobs (job_id TEXT, enabled INTEGER, job_json TEXT, state_json TEXT, agent_id TEXT, payload_kind TEXT, declaration_key TEXT); CREATE TABLE cron_run_receipts (job_id TEXT, agent_id TEXT, status TEXT, started_at_ms INTEGER, finished_at_ms INTEGER, request_run_id TEXT)');
const transcript = new DatabaseSync(`${root}/agents/tauric-heartbeat/agent/openclaw-agent.sqlite`);
transcript.exec('CREATE TABLE transcript_events (created_at INTEGER, seq INTEGER, event_json TEXT, event_zstd BLOB)');
function events({ready=true, completed=true, compressed=false, local=true}={}) {
  transcript.exec('DELETE FROM transcript_events');
  const messages = [{role:'toolResult', toolName:'tauric_heartbeat_status', details:{checked_at:new Date(start+1000).toISOString(),ready,http_status:ready?200:503,scope:'api_and_migrated_storage',execution_authority:false,private_text:'never export this'}},
    {role:'assistant', model:local?'tauric-heartbeat:4b':'cloud',provider:local?'tauric-heartbeat':'external',stopReason:completed?'stop':'length',content:[{type:'text',text:'PRIVATE REASONING'}]}];
  for (const [seq,message] of messages.entries()) {
    const json=JSON.stringify({message});
    transcript.prepare('INSERT INTO transcript_events VALUES (?, ?, ?, ?)').run(start+2000+seq,seq,compressed?null:json,compressed?zstdCompressSync(Buffer.from(json)):null);
  }
}
const check=()=>readHeartbeatStatus(root,true,true,current);
try {
  assert.equal(readHeartbeatStatus('/missing',false).status,'disabled');
  assert.equal(check().status,'unavailable');
  db.prepare('INSERT INTO cron_jobs VALUES (?, ?, ?, ?, ?, ?, ?)').run('job',1,JSON.stringify({schedule:{kind:'every',everyMs:1800000}}),JSON.stringify({nextRunAtMs:current+1800000}),'tauric-heartbeat','heartbeat','heartbeat:tauric-heartbeat');
  assert.equal(check().status,'waiting');
  db.prepare('INSERT INTO cron_run_receipts VALUES (?, ?, ?, ?, ?, ?)').run('job','tauric-heartbeat','ok',start,finish,null);
  events();
  assert.equal(check().status,'healthy'); assert.equal(check().automatic,true);
  assert.equal(JSON.stringify(check()).includes('PRIVATE'),false);
  assert.equal(JSON.stringify(check()).includes('private_text'),false);
  events({compressed:true}); assert.equal(check().status,'healthy');
  events({ready:false}); assert.equal(check().status,'failed');
  events({completed:false}); assert.equal(check().status,'unverified');
  events({local:false}); assert.equal(check().status,'unverified');
  events();
  db.prepare('UPDATE cron_run_receipts SET started_at_ms=?, finished_at_ms=?, request_run_id=?').run(current-5000,current-1000,'manual:test');
  assert.equal(check().status,'unverified'); assert.equal(check().ready,null); assert.equal(check().automatic,false);
  db.prepare('UPDATE cron_run_receipts SET status=?, finished_at_ms=NULL').run('running');
  assert.equal(check().status,'running'); assert.equal(check().next_check_at,null);
  db.prepare('UPDATE cron_run_receipts SET started_at_ms=?').run(current-700000);
  assert.equal(check().status,'stale');
  db.prepare('UPDATE cron_jobs SET enabled=0').run(); assert.equal(check().status,'paused');
  db.prepare('UPDATE cron_jobs SET enabled=1').run();
  assert.equal(readHeartbeatStatus(root,true,false,current).status,'unavailable');
  db.prepare('UPDATE cron_run_receipts SET status=?,started_at_ms=?,finished_at_ms=?').run('error',start,finish);
  assert.equal(check().status,'failed');
  const server=createHeartbeatStatusServer({root,enabled:true,token:'fixture-token',runtimeAvailable:()=>true});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try {
    const url=`http://127.0.0.1:${server.address().port}`;
    assert.equal((await fetch(url+'/heartbeat')).status,401);
    assert.equal((await fetch(url+'/heartbeat',{headers:{Authorization:'Bearer wrong-token'}})).status,401);
    const headers={Authorization:'Bearer fixture-token'};
    assert.equal((await fetch(url+'/other',{headers})).status,404);
    assert.equal((await fetch(url+'/heartbeat',{headers,method:'POST'})).status,404);
    const response=await fetch(url+'/heartbeat',{headers});
    assert.equal(response.status,200); assert.equal(response.headers.get('cache-control'),'no-store');
    assert.equal((await response.json()).status,'failed');
  } finally { await new Promise(resolve=>server.close(resolve)); }
  console.log('Heartbeat status: verified success, compressed state, failures, stale, pause, isolation, authentication and sanitization passed.');
} finally { transcript.close(); db.close(); fs.rmSync(root,{recursive:true,force:true}); }
