// Independently pinned private Gateway. Settings arrive over a private boundary.
import fs from "node:fs";
import { spawn } from "node:child_process";
import { buildConfig, gatewayRunning, writePrivateConfig } from "./config.mjs";
import { createHeartbeatStatusServer } from "./heartbeat-status.mjs";
const root = process.env.OPENCLAW_STATE_DIR ?? "/state";
const privateRoot = process.env.OPENCLAW_CONFIG_DIR ?? "/tmp/tauric-openclaw";
fs.mkdirSync(root, {recursive:true,mode:0o700});
fs.mkdirSync(privateRoot, {recursive:true,mode:0o700});
fs.copyFileSync("/run/setup-openclaw-private.json",`${privateRoot}/service-tokens.json`);
fs.chmodSync(`${privateRoot}/service-tokens.json`,0o600);
if(process.getuid?.() === 0) {
  fs.chownSync(root,1000,1000);
  fs.chownSync(privateRoot,1000,1000);
  fs.chownSync(`${privateRoot}/service-tokens.json`,1000,1000);
  process.setgroups([]); process.setgid(1000); process.setuid(1000);
}
process.umask(0o077);
const tokens=JSON.parse(fs.readFileSync(`${privateRoot}/service-tokens.json`,"utf8"));
// These generated files are rebuilt on startup; remove legacy persistent copies.
for (const name of ['openclaw.json','openclaw.json.next','service-tokens.json']) {
  fs.rmSync(`${root}/${name}`, {force:true});
}
for (const key of ["gateway_token","platform_token"]) if(typeof tokens[key] !== "string" || tokens[key].length < 40) throw new Error("Invalid private service configuration");
fs.mkdirSync(`${root}/workspace/skills`,{recursive:true,mode:0o700});
fs.cpSync("/app/skills/tauric-governance",`${root}/workspace/skills/tauric-governance`,{recursive:true});
fs.mkdirSync(`${root}/broker-workspace/skills`,{recursive:true,mode:0o700});
fs.cpSync("/app/skills/tauric-broker-governance",`${root}/broker-workspace/skills/tauric-broker-governance`,{recursive:true});
fs.mkdirSync(`${root}/chat-workspace`,{recursive:true,mode:0o700});
fs.copyFileSync('/app/chat_instructions.txt',`${root}/chat-workspace/AGENTS.md`);
const localHeartbeat = process.env.OPENCLAW_LOCAL_HEARTBEAT === '1';
if(localHeartbeat) fs.mkdirSync(`${root}/heartbeat-workspace`,{recursive:true,mode:0o700});
const headers = {Authorization:`Bearer ${tokens.platform_token}`,"Content-Type":"application/json"};
async function platform(path, body) {
  const response = await fetch(`http://api:8000/internal/openclaw/${path}`, {
    method:body ? "POST" : "GET",headers,body:body ? JSON.stringify(body) : undefined,
    signal:AbortSignal.timeout(10000),redirect:"error",
  });
  if(!response.ok) throw new Error("Private platform is unavailable");
  if(!body) return response.json();
}
let child, fingerprint, stopping=false, syncing=false;
const statusServer = createHeartbeatStatusServer({root, enabled:localHeartbeat,
  token:tokens.platform_token, runtimeAvailable:()=>gatewayRunning(child)});
statusServer.listen(18790, '0.0.0.0');
function launch(runtime) {
  const configPath = writePrivateConfig(privateRoot, buildConfig(root,tokens,runtime,{localHeartbeat}));
  // Provider exceptions and agent text may contain private inputs. Don't forward logs.
  child=spawn(process.execPath,["/app/node_modules/openclaw/openclaw.mjs","gateway","run"],{
    stdio:"ignore",env:{...process.env,HOME:root,OPENCLAW_STATE_DIR:root,
      OPENCLAW_CONFIG_PATH:configPath,OPENCLAW_GATEWAY_TOKEN:tokens.gateway_token}});
}
async function retire() {
  const previous=child;
  if(!gatewayRunning(previous)) return;
  await new Promise(resolve=>{
    const deadline=setTimeout(()=>previous.kill("SIGKILL"),5000);
    previous.once("exit",()=>{clearTimeout(deadline);resolve();});
    previous.kill("SIGTERM");
  });
}
async function sync() {
  if(stopping || syncing) return;
  syncing=true;
  try {
    const runtime=await platform("runtime-config");
    if(runtime.fingerprint !== fingerprint || !gatewayRunning(child)) {
      await retire();
      if(stopping) return;
      launch(runtime);fingerprint=runtime.fingerprint;
      console.log(localHeartbeat ? "Private configuration applied; local heartbeat scheduled." : "Private governance configuration applied; no model call started.");
    }
    const health=await fetch("http://127.0.0.1:18789/healthz",{signal:AbortSignal.timeout(2000)});
    if(health.ok && gatewayRunning(child)) await platform("heartbeat",{fingerprint,capabilities:["paper_governance","broker_entry_review"]});
  } catch {
    // A stale heartbeat blocks new jobs. Never reveal credentials or repeat reviews.
  } finally {syncing=false;}
}
launch({routes:{}});
await sync();
const timer=setInterval(sync,5000);
for(const event of ["SIGTERM","SIGINT"])process.on(event,async()=>{
  stopping=true;clearInterval(timer);statusServer.close();await retire();process.exit(0);
});
