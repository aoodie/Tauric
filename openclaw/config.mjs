// Build only project-owned configuration; the pinned OpenClaw runtime is untouched.
export function gatewayRunning(child) {
  return !!child && child.exitCode === null && child.signalCode === null;
}

export function buildConfig(root, tokens, runtime, {localHeartbeat = false} = {}) {
  const providers = {}, entries = {}, allowed = {};
  const settings = runtime.settings ?? {timeout_seconds:180,max_output_tokens:2000};
  const defaults = {
    openai:["https://api.openai.com/v1","openai-completions"],
    anthropic:["https://api.anthropic.com","anthropic-messages"],
    google:["https://generativelanguage.googleapis.com/v1beta","google-generative-ai"],
    openrouter:["https://openrouter.ai/api/v1","openai-completions"],
    openai_compatible:["","openai-completions"], ollama:["","ollama"],
  };
  for(const [key, route] of Object.entries(runtime.routes ?? {})) {
    if(!["cloud","local"].includes(key) || !defaults[route.provider] || !route.model) throw new Error("Invalid private model route");
    const [base, api] = defaults[route.provider];
    let address = route.base_url || base;
    if(route.provider === "ollama") address = address.replace(/\/v1\/?$/, "");
    const id = `tauric-${key}`, model = `${id}/${route.model}`;
    providers[id] = {
      baseUrl:address,api,apiKey:route.api_key || "tauric-local-no-key",
      authHeader:!!route.api_key,request:{allowPrivateNetwork:true},
      timeoutSeconds:settings.timeout_seconds,maxTokens:settings.max_output_tokens,
      models:[{id:route.model,name:route.model,input:["text"],maxTokens:settings.max_output_tokens}],
    };
    allowed[model] = {params:{maxTokens:settings.max_output_tokens}};
    entries[`governor-${key}`] = {
      name:`Tauric ${key} paper governor`,workspace:`${root}/workspace`,
      model:{primary:model,fallbacks:[]},modelPolicy:{allow:[model]},
      thinkingDefault:"off",reasoningDefault:"off",skills:["tauric-governance"],
      tools:{allow:["tauric_review_context"]},
    };
    entries[`broker-governor-${key}`] = {
      name:`Tauric ${key} demo broker governor`,workspace:`${root}/broker-workspace`,
      model:{primary:model,fallbacks:[]},modelPolicy:{allow:[model]},
      thinkingDefault:"off",reasoningDefault:"off",skills:["tauric-broker-governance"],
      tools:{allow:["tauric_broker_review_context"]},
    };
  }
  const chat = runtime.chat ?? {profile:"local"};
  const channels = {}, bindings = [], channelPlugins = [];
  if(entries[`governor-${chat.profile}`]) {
    const selected = entries[`governor-${chat.profile}`];
    entries['tauric-chat'] = {
      name:"OpenClaw · Tauric assistant",workspace:`${root}/chat-workspace`,
      model:selected.model,modelPolicy:selected.modelPolicy,
      thinkingDefault:"off",reasoningDefault:"off",skills:[],
      tools:{allow:["tauric_chat_context","tauric_request_research","tauric_research_status","tauric_portfolio_action","tauric_request_portfolio_research","tauric_project_read","tauric_project_action","tauric_market_data","tauric_open_research","tauric_task","tauric_engineering","tauric_release"]},
    };
    for(const key of ['telegram','discord']) {
      const channel = chat[key];
      if(!channel?.enabled) continue;
      if(!/^[1-9][0-9]{0,24}$/.test(channel.owner_id) || !channel.token) throw new Error('Invalid owner chat configuration');
      channels[key] = {
        enabled:true,dmPolicy:'allowlist',allowFrom:[channel.owner_id],
        groupPolicy:'disabled',joinIntro:false,configWrites:false,
        commands:{native:false,nativeSkills:false},
        ...(key === 'telegram' ? {botToken:channel.token,streaming:{mode:'off'}} : {token:channel.token,allowBots:false,dm:{enabled:true}}),
      };
      bindings.push({agentId:'tauric-chat',match:{channel:key,accountId:'default'}});
      channelPlugins.push(key);
    }
  }
  if(localHeartbeat) {
    const model = 'tauric-heartbeat/tauric-heartbeat:4b';
    providers['tauric-heartbeat'] = {
      baseUrl:'http://memory-model:11434',api:'ollama',apiKey:'tauric-local-no-key',
      authHeader:false,request:{allowPrivateNetwork:true},
      models:[{id:'tauric-heartbeat:4b',name:'Local Qwen3 4B heartbeat',input:['text'],reasoning:false,contextWindow:8192,contextTokens:8192,maxTokens:1024,
        params:{think:false,num_ctx:8192,num_predict:1024,num_thread:3}}],
    };
    allowed[model] = {params:{maxTokens:1024,num_ctx:8192,num_predict:1024,think:false}};
    entries['tauric-heartbeat'] = {
      name:'OpenClaw local service heartbeat',workspace:`${root}/heartbeat-workspace`,
      model:{primary:model,fallbacks:[]},modelPolicy:{allow:[model]},
      thinkingDefault:'off',reasoningDefault:'off',skills:[],
      tools:{allow:['tauric_heartbeat_status']},
      heartbeat:{every:'30m',model,lightContext:true,isolatedSession:true,target:'none',timeoutSeconds:600,
        prompt:'Call tauric_heartbeat_status once. If ready=true, reply NO_REPLY. Otherwise briefly report the check failure. /no_think'},
    };
  }
  return {
    gateway:{mode:"local",port:18789,bind:"lan",auth:{mode:"token",token:tokens.gateway_token},controlUi:{enabled:false},
      http:{endpoints:{chatCompletions:{enabled:Object.keys(entries).length > 0}}}},
    agents:{...(Object.keys(entries).length ? {ownership:"explicit",entries} : {}),defaults:{workspace:`${root}/workspace`,skipBootstrap:true,
      heartbeat:{every:"0m"},timeoutSeconds:settings.timeout_seconds,maxConcurrent:1,
      models:allowed,thinkingDefault:"off",reasoningDefault:"off",
      embeddedAgent:{cyberFailover:{mode:"off"}},compaction:{memoryFlush:{enabled:false}}}},
    models:{mode:"replace",providers},cron:{enabled:localHeartbeat,...(localHeartbeat ? {skipMissedJobs:true,triggers:{enabled:false}} : {})},
    ...(localHeartbeat ? {skills:{workshop:{autonomous:{mode:'off'}}}} : {}),
    channels,bindings,session:{dmScope:'per-account-channel-peer'},
    commands:{native:false,nativeSkills:false,text:false,config:false,debug:false,restart:false,bash:false,
      ownerAllowFrom:channelPlugins.map(key=>`${key}:${chat[key].owner_id}`)},
    tools:{toolSearch:false,codeMode:false,allow:["tauric_paper_context","tauric_review_context","tauric_chat_context","tauric_request_research","tauric_research_status","tauric_portfolio_action","tauric_request_portfolio_research","tauric_broker_review_context","tauric_project_read","tauric_project_action","tauric_market_data","tauric_open_research","tauric_task","tauric_engineering","tauric_release",...(localHeartbeat ? ['tauric_heartbeat_status'] : [])],loopDetection:{enabled:true}},
    plugins:{slots:{memory:"none"},allow:["tauric-governance-tools",...channelPlugins,...(localHeartbeat ? ['ollama'] : [])],load:{paths:["/app/plugin"]},
      entries:{"memory-core":{enabled:false,...(localHeartbeat ? {config:{dreaming:{enabled:false}}} : {})},"tauric-governance-tools":{enabled:true,
        config:{platformUrl:"http://api:8000/",platformToken:tokens.platform_token}},
        ...(localHeartbeat ? {ollama:{enabled:true}} : {}),
        ...Object.fromEntries(channelPlugins.map(key=>[key,{enabled:true}]))}},
  };
}
