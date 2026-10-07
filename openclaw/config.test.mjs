import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawn, spawnSync} from 'node:child_process';
import {once} from 'node:events';
import {buildConfig, gatewayRunning} from './config.mjs';
const killedGateway = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)']);
await once(killedGateway, 'spawn');
assert.equal(gatewayRunning(killedGateway), true);
killedGateway.kill('SIGKILL');
await once(killedGateway, 'exit');
assert.equal(killedGateway.exitCode, null);
assert.equal(killedGateway.signalCode, 'SIGKILL');
assert.equal(gatewayRunning(killedGateway), false);
assert.equal(gatewayRunning(undefined), false);
const tokens={gateway_token:'fixture-gateway-'+'x'.repeat(48),platform_token:'fixture-platform-'+'x'.repeat(48)};
const runtime={settings:{timeout_seconds:180,max_output_tokens:2000},routes:{
 cloud:{provider:'openai_compatible',base_url:'http://127.0.0.1:9/v1',model:'fixture-cloud',api_key:'fixture-key'},
 local:{provider:'ollama',base_url:'http://127.0.0.1:9/v1',model:'fixture-local',api_key:''},
}};
const config=buildConfig('/state',tokens,runtime);
runtime.chat={profile:'local',telegram:{enabled:true,owner_id:'12345678',token:'fixture-telegram-token'},discord:{enabled:true,owner_id:'123456789012345678',token:'fixture-discord-token'}};
const chatConfig=buildConfig('/state',tokens,runtime);
assert.deepEqual(chatConfig.agents.entries['tauric-chat'].tools.allow,['tauric_chat_context','tauric_request_research','tauric_research_status','tauric_portfolio_action','tauric_request_portfolio_research','tauric_project_read','tauric_project_action']);
assert.deepEqual(chatConfig.commands.ownerAllowFrom,['telegram:12345678','discord:123456789012345678']);
assert.deepEqual(chatConfig.agents.entries['governor-local'].tools.allow,['tauric_review_context']);
assert.equal(chatConfig.channels.telegram.dmPolicy,'allowlist');
assert.equal(chatConfig.channels.discord.groupPolicy,'disabled');
assert.equal(chatConfig.commands.text,false);
assert.equal(chatConfig.session.dmScope,'per-account-channel-peer');
assert.deepEqual(chatConfig.channels.telegram.allowFrom,['12345678']);
assert.deepEqual(chatConfig.bindings.map(b=>b.agentId),['tauric-chat','tauric-chat']);
assert.equal(config.agents.entries['governor-cloud'].model.primary,'tauric-cloud/fixture-cloud');
assert.deepEqual(config.agents.entries['governor-local'].model.fallbacks,[]);
for(const route of ['cloud','local']) {
 const broker=config.agents.entries[`broker-governor-${route}`];
 assert.deepEqual(broker.tools.allow,['tauric_broker_review_context']);
 assert.deepEqual(broker.skills,['tauric-broker-governance']);
 assert.deepEqual(broker.model.fallbacks,[]);
 assert.equal(broker.model.primary,`tauric-${route}/fixture-${route}`);
 assert.equal(broker.workspace,'/state/broker-workspace');
}
assert.equal(config.models.providers['tauric-local'].baseUrl,'http://127.0.0.1:9');
assert.equal(config.cron.enabled,false);assert.equal(config.plugins.entries['memory-core'].enabled,false);
assert.equal(buildConfig('/state',tokens,{routes:{}}).gateway.http.endpoints.chatCompletions.enabled,false);
const folder=fs.mkdtempSync(path.join(os.tmpdir(),'tauric-config-'));
try {
 const target=path.join(folder,'openclaw.json');fs.writeFileSync(target,JSON.stringify(process.env.TEST_EMPTY_CONFIG ? buildConfig('/state',tokens,{routes:{}}) : chatConfig),{mode:0o600});
 const validation=spawnSync(process.execPath,[new URL('./node_modules/openclaw/openclaw.mjs', import.meta.url).pathname,'config','validate','--json'],{env:{...process.env,OPENCLAW_CONFIG_PATH:target,OPENCLAW_STATE_DIR:folder},encoding:'utf8'});
 if(validation.status) {const sanitized=JSON.parse(validation.stdout || '{}');console.log(JSON.stringify({valid:sanitized.valid,issues:sanitized.issues}));throw Error('Pinned schema rejected project configuration');}
 console.log('Pinned Gateway schema accepts both routes, isolated agents, budgets and disabled scheduling. No model calls.');
} finally {fs.rmSync(folder,{recursive:true,force:true});}
