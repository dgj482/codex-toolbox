(() => {
  window.__codexToolboxSurfaceTranslation?.destroy?.();
  const config = __CODEX_TOOLBOX_CONFIG__;
  const appName=config.appName||'Codex';
  const accent=config.appName==='Claude'?'#b25a3c':'#5868df';
  const assistantSelector=config.assistantSelector||'[data-markdown-text-style="assistant-message"]';
  const messageSelector=config.messageSelector||'[data-markdown-text-style="assistant-message"],[data-markdown-text-tone="user-message"],.inline-markdown,[class*="MarkdownRoot"],[class*="InlineMarkdown"]';
  const dictionary = new Map(Object.entries({
    "New task":"新建任务", "New chat":"新聊天", "Settings":"设置", "Plugins":"插件", "Skills":"技能",
    "Automations":"自动化", "Archived tasks":"已归档任务", "Search":"搜索", "Pull requests":"拉取请求",
    "Installed":"已安装", "Projects":"项目", "Recent":"最近使用", "Sites":"站点", "Scheduled":"定时任务",
    "Open":"打开", "Cancel":"取消", "Confirm":"确认", "Delete":"删除", "Rename":"重命名",
    "Archive":"归档", "Unarchive":"取消归档", "Copy":"复制", "Retry":"重试", "Send":"发送",
    "Stop":"停止", "Review changes":"审查更改", "Changes":"更改", "Files":"文件", "Terminal":"终端",
    "Browser":"浏览器", "Local":"本地", "Cloud":"云端", "General":"常规", "Appearance":"外观",
    "Notifications":"通知", "Account":"账户", "Close":"关闭", "Save":"保存", "Done":"完成",
    "File":"文件", "Edit":"编辑", "View":"查看", "Help":"帮助", "Full access":"完全访问",
    "Medium":"中等", "Low":"低", "High":"高", "What should we build?":"我们要做什么？",
    "Choose project":"选择项目", "Do anything":"输入你想做的事情",
    "Permissions":"权限", "Default permissions":"默认权限",
    "By default, ChatGPT can read and edit files in its workspace. It can ask for additional access when needed":"默认情况下，ChatGPT 可以读取和编辑工作区内的文件，需要时会向你申请额外权限",
    "When ChatGPT runs with full access, it can edit any file on your computer and run commands with network, without your approval. This significantly increases the risk of data loss, leaks, or unexpected behavior.":"使用完全访问权限时，ChatGPT 无需逐次批准即可编辑电脑上的文件并运行联网命令，这会明显增加数据丢失、信息泄露或意外操作的风险。",
    "Learn more":"了解更多", "about elevated risks.":"关于高权限风险的信息。",
    "Projectless task folder":"非项目任务文件夹",
    "The location where tasks started outside of projects store their data by default.":"不属于任何项目的任务默认把数据保存在这里。",
    "Change":"更改", "Reset":"重置", "Default file open destination":"文件默认打开位置",
    "Where files and folders open by default":"选择文件和文件夹默认用什么程序打开",
    "Agent environment":"代理运行环境", "Choose where the agent runs on Windows":"选择代理在 Windows 上的运行方式",
    "Integrated terminal shell":"集成终端 Shell", "Choose which shell opens in the integrated terminal.":"选择集成终端默认使用的 Shell。",
    "Language":"语言", "Language for the app UI":"Codex 界面所使用的语言", "English":"英语",
    "Bottom panel":"底部面板", "Show the bottom panel control in the app header.":"在应用顶部显示底部面板控制按钮。",
    "Default terminal location":"终端默认位置",
    "Choose where the terminal shortcut and environment actions open terminal tabs":"选择终端快捷按钮和环境操作在哪里打开终端标签页",
    "Bottom":"底部", "Right":"右侧", "Windows native":"Windows 原生环境",
    "Speed":"速度", "Choose how quickly ChatGPT runs across chats, subagents, and compaction":"选择 ChatGPT 在聊天、子代理和上下文压缩中的运行速度",
    "Standard":"标准", "Suggested prompts":"提示词建议",
    "Suggest what to do next by searching project files and connected apps":"通过搜索项目文件和已连接的应用，建议下一步操作",
    "Open source licenses":"开源许可证", "Third-party notices for bundled dependencies":"内置依赖项的第三方声明",
    "Composer":"输入框", "Plain text composer":"纯文本输入框",
    "Keep code, Markdown, and links as literal text while writing messages":"编写消息时，将代码、Markdown 和链接保留为原始文本",
    "Show context window usage":"显示上下文窗口用量", "Send shortcut":"发送快捷键",
    "Choose when Enter sends a prompt or inserts a new line":"选择按回车键时发送提示词还是插入新行",
    "Allow ChatGPT to use installed plugins":"允许 ChatGPT 使用已安装的插件",
    "Back to app":"返回应用", "Search settings...":"搜索设置…", "Import":"导入", "Voice":"语音",
    "Configuration":"配置", "Personalization":"个性化", "Pets":"宠物", "Keyboard shortcuts":"键盘快捷键",
    "Integrations":"集成", "Computer use":"电脑操作", "Coding":"编程", "Hooks":"钩子",
    "Connections":"连接", "Git":"Git", "Environments":"环境", "Worktrees":"工作树",
    "Archived":"已归档", "Archived chats":"已归档聊天",
    "Follow-up behavior":"后续操作方式", "Queue":"排队", "Steer":"调整当前任务",
    "Queue follow-ups while ChatGPT runs or steer the current run. Press Ctrl+↵ to do the opposite for one message":"ChatGPT 工作时把后续消息排队，或用消息调整当前任务。按 Ctrl+回车可临时使用相反方式",
    "Popout Window":"弹出窗口", "Popout Window hotkey":"弹出窗口快捷键",
    "Set a global shortcut for Popout Window. Leave unset to keep it off.":"设置打开弹出窗口的全局快捷键；留空表示关闭",
    "Off":"关闭", "Default to standalone chat":"默认使用独立聊天",
    "Start new chats outside of any project":"新聊天默认不放入任何项目",
    "Turn completion notifications":"任务完成通知", "Only when unfocused":"仅窗口未激活时",
    "Set when ChatGPT alerts you that it's finished":"设置 ChatGPT 完成任务后何时提醒你",
    "Enable permission notifications":"开启权限请求通知",
    "Show alerts when notification permissions are required":"需要你批准权限时显示提醒",
    "Enable question notifications":"开启提问通知",
    "Show alerts when input is needed to continue":"需要你输入内容才能继续时显示提醒",
    "Toys":"趣味功能", "Confetti cannon":"彩纸庆祝效果",
    "Let Codex fire confetti in the app when you ask!":"当你提出要求时，让 Codex 在应用中播放彩纸效果",
    "Usage remaining":"剩余用量", "Show pet":"显示宠物", "Invite a friend":"邀请好友",
    "Workspace settings":"工作区设置", "Usage":"用量", "Show Mini":"显示 Mini",
    "Invite a coworker":"邀请同事", "Log out":"退出登录", "Sign out":"退出登录",
    "Documentation":"文档", "Keyboard Shortcuts":"键盘快捷键", "What's New":"新功能",
    "Troubleshooting":"故障排查", "System Status":"系统状态", "Send Feedback":"发送反馈",
    "Task Manager":"任务管理器", "Start Performance Trace":"开始性能跟踪",
    "Check for Updates":"检查更新", "About ChatGPT":"关于 ChatGPT"
  }));
  const protectedTerms = new Set(["Codex","ChatGPT","OpenAI","Git","PowerShell","PyCharm","API","URL","MCP","model","Model","models","Models"]);
  const modelNamePattern=/\b(?:gpt-\d[\w.-]*|o[134](?:-[\w.-]+)?|qwen[\w.-]*|claude[\w.-]*|gemini[\w.-]*|sonnet|opus|haiku|mythos|fable)\b/i;
  const BODY_TRANSLATION_CONCURRENCY=3;
  // 批次上限与后端 Qwen-MT 的打包参数保持一致（codex_surface_translation.QWEN_MT_PACK_ITEMS/CHARACTERS）：
  // 前端切得比后端小会让后端白拼一次，切得比后端大又会被后端拆成多次原生请求。
  const BATCH_MAX_ITEMS=8;
  const BATCH_MAX_CHARACTERS=800;
  // 界面与正文译文都只留在这份内存缓存里。上限纯粹是防泄漏：
  // Codex 长时间开着不断滚动会让缓存无限增长，被淘汰的正文最多是重新翻译一次。
  const CACHE_LIMIT=4000;
  const FAILURE_RECORD_LIMIT=600;
  const BODY_RETRY_AFTER_MS=30000;
  const BODY_STABILITY_MS=900;
  const REQUEST_TIMEOUT_MS=50000;
  const SEND_BUTTON_POLL_MS=1500;
  const SEND_SETTLE_MS=250;
  const state = {destroyed:false,showTranslations:false,replacements:new Map(),placeholderReplacements:new Map(),cache:new Map(),pending:new Map(),bodyQueue:new Set(),bodyPending:new Set(),bodyFailures:new Map(),bodyStability:new WeakMap(),bodyRequestsRunning:0,bodyStabilityTimer:0,interfaceQueue:new Set(),interfacePending:new Set(),interfaceFailures:new Map(),interfaceRunning:false,manualPageRunning:false,seq:0,renderQueued:false,renderTimer:0,renderFrame:0,positionFrame:0,generation:0,rectCache:new Map(),observedComposer:new Set(),editor:null,enterBypassText:''};
  const root=document.createElement('div');root.id='codex-toolbox-translation-layer';
  root.style.cssText='position:fixed;inset:0;z-index:2147483000;pointer-events:none;overflow:visible;font-family:inherit';
  (document.body||document.documentElement).appendChild(root);

  function cacheSet(key,value){
    state.cache.set(key,value);
    // Map 按插入顺序迭代，这里做的是 FIFO 淘汰；界面词条有本地词典兜底，正文淘汰后重译即可。
    while(state.cache.size>CACHE_LIMIT){const oldest=state.cache.keys().next();if(oldest.done)break;state.cache.delete(oldest.value);}
  }
  function request(texts,target,scope='body'){
    const id=Date.now()+'-'+(++state.seq);return new Promise((resolve,reject)=>{
      const settle=(fn,arg)=>{const item=state.pending.get(id);if(!item)return;state.pending.delete(id);clearTimeout(item.timer);fn(arg);};
      const timer=setTimeout(()=>settle(reject,new Error('翻译超时')),REQUEST_TIMEOUT_MS);
      state.pending.set(id,{resolve:value=>settle(resolve,value),reject:error=>settle(reject,error),timer});
      if(typeof window.codexToolboxTranslate!=='function'){settle(reject,new Error('工具箱翻译连接已断开'));return;}
      window.codexToolboxTranslate(JSON.stringify({id,texts,target,scope}));
    });
  }
  window.__codexToolboxTranslationResolve=payload=>{const item=state.pending.get(payload?.id);if(!item)return;payload.ok?item.resolve(payload.texts||[]):item.reject(new Error(payload.error||'翻译失败'));};

  function rangeRect(node){try{const range=document.createRange();range.selectNodeContents(node);const rect=range.getBoundingClientRect();return rect.width>1&&rect.height>1?rect:null;}catch(_){return null;}}
  function inViewport(rect){return rect.bottom>0&&rect.top<innerHeight&&rect.right>0&&rect.left<innerWidth;}
  // getBoundingClientRect 会强制同步布局。同一段文字在一轮渲染里可能被
  // interfaceEligible、bodyNodePriority、currentPageSnapshot 各测一次，
  // 所以按轮次记忆化，每轮开始时清空。
  function nodeRect(node){if(state.rectCache.has(node))return state.rectCache.get(node);const rect=rangeRect(node);state.rectCache.set(node,rect);return rect;}
  function baseExcluded(node){
    const parent=node.parentElement;return !parent||root.contains(parent)||!!parent.closest('code,pre,textarea,input,[contenteditable="true"],[aria-hidden="true"],[class*="activity-header"],[class*="loading-shimmer"]')||!!(parent.closest('a')&&insideAnyMessage(node));
  }
  function assistantBodyRoot(node){return node.parentElement?.closest(assistantSelector)||null;}
  function insideAnyMessage(node){return !!node.parentElement?.closest(messageSelector);}
  function interfaceTextProtected(text,node){
    if(protectedTerms.has(text)||/^(?:https?:\/\/|[A-Za-z]:\\|\/)[^\s]+$/.test(text)||/^\S+@\S+\.\S+$/.test(text))return true;
    if(/^(?:(?:Alt|Ctrl|Control|Shift|Meta|Win|Cmd|Command)[+\-])+/i.test(text))return true;
    const parent=node?.parentElement,control=parent?.closest('button,[role="button"],[role="option"],[role="menuitem"],[role="combobox"]');
    // Names and variants may be split across sibling spans. Protect the entire model control.
    if(modelNamePattern.test(text)||modelNamePattern.test(control?.textContent||''))return true;
    if(parent?.closest('[class*="ComposerLayout"]')&&['Medium','Low','High','Extra high','sol','astra','luna'].includes(text))return true;
    // Menu headers can contain account names; only actions and known labels are UI copy.
    return !!parent?.closest('[role="menu"]')&&!control&&!dictionary.has(text)&&!/^\d+(?:\.\d+)?% left$/.test(text);
  }
  function interfaceEligible(node){
    if(!config.translateInterface||baseExcluded(node)||insideAnyMessage(node))return false;const text=sourceText(node);
    if(interfaceTextProtected(text,node))return false;
    if(!text||text.length>1200||/^[\d\s\p{P}\p{S}]+$/u.test(text))return false;
    const rect=nodeRect(node);return !!rect&&inViewport(rect);
  }
  function visibleInterfaceTextNodes(){const nodes=[];const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);while(walker.nextNode())if(interfaceEligible(walker.currentNode))nodes.push(walker.currentNode);return nodes;}
  function bodyCandidate(text){
    if(!text||text.length>6000||protectedTerms.has(text)||/^(?:https?:\/\/|[A-Za-z]:\\|\/)[^\s]+$/.test(text))return false;
    const latin=(text.match(/[A-Za-z]/g)||[]).length,cjk=(text.match(/[\u4e00-\u9fff]/g)||[]).length;return latin>=3&&latin>cjk;
  }
  function bodyNodePriority(node){
    const rect=nodeRect(node);if(!rect)return Number.MAX_SAFE_INTEGER;
    if(inViewport(rect))return 0;
    return rect.bottom<=0?-rect.bottom:Math.max(0,rect.top-innerHeight);
  }
  function assistantBodyTextNodes(){
    if(!config.translateMessages)return [];const nodes=[];
    for(const message of document.querySelectorAll(assistantSelector)){
      const walker=document.createTreeWalker(message,NodeFilter.SHOW_TEXT);while(walker.nextNode()){
        const node=walker.currentNode,text=sourceText(node);if(!baseExcluded(node)&&assistantBodyRoot(node)===message&&bodyCandidate(text))nodes.push(node);
      }
    }
    // 先一次性测完再排序：把 getBoundingClientRect 放进比较器会让强制布局的次数
    // 从 O(n) 变成 O(n log n)，长对话滚动时能明显感觉到卡顿。
    return nodes.map(node=>({node,priority:bodyNodePriority(node)}))
      .sort((left,right)=>left.priority-right.priority)
      .map(item=>item.node);
  }
  function localTranslation(text,node){
    if(interfaceTextProtected(text,node))return null;
    const remaining=text.match(/^(\d+(?:\.\d+)?)% left$/);if(remaining)return '剩余 '+remaining[1]+'%';
    return dictionary.get(text)||null;
  }
  function sourceText(node){const entry=state.replacements.get(node);if(entry&&node.nodeValue===entry.translatedRaw)return entry.original;if(entry)state.replacements.delete(node);return node.nodeValue.trim();}
  function makeTranslatedRaw(raw,original,translated){const start=raw.indexOf(original);return start<0?translated:raw.slice(0,start)+translated+raw.slice(start+original.length);}
  function applyReplacement(node,original,translated,generation){
    let entry=state.replacements.get(node);
    if(!entry||entry.original!==original||entry.translated!==translated){const raw=node.nodeValue;entry={original,translated,originalRaw:raw,translatedRaw:makeTranslatedRaw(raw,original,translated),generation,kind:assistantBodyRoot(node)?'body':'interface'};state.replacements.set(node,entry);}
    entry.generation=generation;if(node.nodeValue!==entry.translatedRaw)node.nodeValue=entry.translatedRaw;
  }
  function removeReplacement(node,entry,restore=true){if(restore&&node.isConnected&&node.nodeValue===entry.translatedRaw)node.nodeValue=entry.originalRaw;state.replacements.delete(node);}
  function restoreAllTranslations(){
    for(const [node,entry] of [...state.replacements])removeReplacement(node,entry);
    for(const [element,entry] of [...state.placeholderReplacements])if(element.isConnected&&element.getAttribute('placeholder')===entry.translated)element.setAttribute('placeholder',entry.original);
    state.placeholderReplacements.clear();
  }
  function syncPlaceholderReplacements(generation){
    for(const element of document.querySelectorAll('input[placeholder],textarea[placeholder]')){
      let entry=state.placeholderReplacements.get(element);const current=element.getAttribute('placeholder')||'';
      const original=entry&&current===entry.translated?entry.original:current;const translated=config.translateInterface?localTranslation(original,null):null;
      if(!translated||translated===original){if(entry){if(current===entry.translated)element.setAttribute('placeholder',entry.original);state.placeholderReplacements.delete(element);}continue;}
      if(!entry||entry.original!==original||entry.translated!==translated){entry={original,translated,generation};state.placeholderReplacements.set(element,entry);}
      entry.generation=generation;if(current!==translated)element.setAttribute('placeholder',translated);
    }
    for(const [element,entry] of [...state.placeholderReplacements])if(!element.isConnected||entry.generation!==generation){if(element.isConnected&&element.getAttribute('placeholder')===entry.translated)element.setAttribute('placeholder',entry.original);state.placeholderReplacements.delete(element);}
  }
  function pruneBodyFailures(now){
    // 失败记录只用于退避重试，攒着不删会随着滚动无限增长。
    if(state.bodyFailures.size<=FAILURE_RECORD_LIMIT)return;
    for(const [text,failedAt] of state.bodyFailures)if(now-failedAt>BODY_RETRY_AFTER_MS)state.bodyFailures.delete(text);
  }
  function queueBodyTranslations(texts){
    const now=Date.now();pruneBodyFailures(now);state.bodyQueue.clear();
    for(const text of texts){const failedAt=state.bodyFailures.get(text)||0;if(!state.cache.has('body:zh:'+text)&&!state.bodyPending.has(text)&&now-failedAt>BODY_RETRY_AFTER_MS)state.bodyQueue.add(text);}
    queueMicrotask(pumpBodyTranslations);
  }
  function pumpBodyTranslations(){
    if(state.destroyed)return;
    while(state.bodyRequestsRunning<BODY_TRANSLATION_CONCURRENCY&&state.bodyQueue.size)translateBodyBatch();
  }
  async function translateBodyBatch(){
    if(state.destroyed||state.bodyRequestsRunning>=BODY_TRANSLATION_CONCURRENCY||!state.bodyQueue.size)return;
    const batch=[];let totalCharacters=0;
    for(const text of state.bodyQueue){
      if(batch.length>=BATCH_MAX_ITEMS)break;
      if(batch.length&&totalCharacters+text.length>BATCH_MAX_CHARACTERS)continue;
      batch.push(text);totalCharacters+=text.length;
    }
    if(!batch.length)return;state.bodyRequestsRunning++;
    for(const text of batch){state.bodyQueue.delete(text);state.bodyPending.add(text);}
    try{
      const values=await request(batch,'zh-CN','body');batch.forEach((text,index)=>{const translated=String(values[index]||'').trim();if(translated)cacheSet('body:zh:'+text,translated);else state.bodyFailures.set(text,Date.now());});
    }catch(error){const failedAt=Date.now();batch.forEach(text=>state.bodyFailures.set(text,failedAt));console.warn('Codex 工具箱正文翻译失败：',error);}
    finally{batch.forEach(text=>state.bodyPending.delete(text));state.bodyRequestsRunning--;scheduleRender();queueMicrotask(pumpBodyTranslations);}
  }
  function bodyTextIsStable(node,original){
    const now=Date.now(),seen=state.bodyStability.get(node);if(seen&&seen.text===original&&now-seen.at>=BODY_STABILITY_MS)return true;
    if(!seen||seen.text!==original)state.bodyStability.set(node,{text:original,at:now});
    if(!state.bodyStabilityTimer)state.bodyStabilityTimer=setTimeout(()=>{state.bodyStabilityTimer=0;scheduleRender();},BODY_STABILITY_MS+20);return false;
  }
  function replacementStillAllowed(node,entry){
    if(baseExcluded(node))return false;if(entry.kind==='body')return !!config.translateMessages&&!!assistantBodyRoot(node);return !!config.translateInterface&&!insideAnyMessage(node)&&!interfaceTextProtected(entry.original,node);
  }
  async function translateMenuQueue(){
    if(state.destroyed||!state.showTranslations||state.manualPageRunning||state.interfaceRunning||!state.interfaceQueue.size)return;
    const batch=manualTranslationBatches([...state.interfaceQueue])[0];state.interfaceRunning=true;
    for(const text of batch){state.interfaceQueue.delete(text);state.interfacePending.add(text);}
    try{
      const values=await request(batch,'zh-CN','interface');
      batch.forEach((text,index)=>{const translated=String(values[index]||'').trim();if(translated)cacheSet('zh:'+text,translated);else state.interfaceFailures.set(text,Date.now());});
    }catch(error){batch.forEach(text=>state.interfaceFailures.set(text,Date.now()));console.warn('Codex 工具箱菜单翻译失败：',error);}
    finally{
      for(const text of batch)state.interfacePending.delete(text);state.interfaceRunning=false;
      while(state.interfaceFailures.size>FAILURE_RECORD_LIMIT)state.interfaceFailures.delete(state.interfaceFailures.keys().next().value);
      scheduleRender();
    }
  }
  function render(){
    if(state.destroyed)return;const generation=++state.generation;
    // 一轮渲染内的几何测量共用一份缓存，开始时清空，避免拿着过期的 rect 做判断。
    state.rectCache.clear();
    const editor=findEditor();
    if(state.manualPageRunning){positionInputButton(editor);observeComposer(editor);return;}
    if(!state.showTranslations){restoreAllTranslations();positionInputButton(editor);observeComposer(editor);return;}
    state.interfaceQueue.clear();
    for(const node of visibleInterfaceTextNodes()){
      const original=sourceText(node);
      // 本地词典命中不写进 LRU：词典是常量，写进去只会挤掉需要重新请求的正文译文。
      const translated=localTranslation(original,node)||state.cache.get('zh:'+original);
      if(translated&&translated!==original)applyReplacement(node,original,translated,generation);else{const old=state.replacements.get(node);if(old)removeReplacement(node,old);}
      // Only newly visible menu actions need background requests; never send menu account headers.
      if(!translated&&bodyCandidate(original)&&node.parentElement.closest('[role="menuitem"],[role="option"],[role="menu"] button,[role="menu"] a')&&!state.interfacePending.has(original)&&Date.now()-(state.interfaceFailures.get(original)||0)>BODY_RETRY_AFTER_MS)state.interfaceQueue.add(original);
    }
    queueMicrotask(translateMenuQueue);
    const missing=[];for(const node of assistantBodyTextNodes()){
      const original=sourceText(node),translated=state.cache.get('body:zh:'+original);
      if(translated&&translated!==original)applyReplacement(node,original,translated,generation);else if(!translated&&bodyTextIsStable(node,original))missing.push(original);
    }
    queueBodyTranslations([...new Set(missing)]);
    for(const [node,entry] of [...state.replacements]){if(!node.isConnected)removeReplacement(node,entry,false);else if(!replacementStillAllowed(node,entry))removeReplacement(node,entry);}
    syncPlaceholderReplacements(generation);positionInputButton(editor);observeComposer(editor);
  }
  function scheduleRender(){if(state.renderQueued||state.destroyed)return;state.renderQueued=true;state.renderTimer=setTimeout(()=>{state.renderTimer=0;state.renderFrame=requestAnimationFrame(()=>{state.renderFrame=0;state.renderQueued=false;if(!state.destroyed)render();});},80);}
  function schedulePosition(){if(state.positionFrame||state.destroyed)return;state.positionFrame=requestAnimationFrame(()=>{state.positionFrame=0;positionInputButton();});}
  function viewportChanged(){schedulePosition();scheduleRender();}

  const inputControls=document.createElement('div');
  const CONTROL_POSITION_KEY=appName.toLowerCase()+'-toolbox-translation-controls-position-v1';
  let controlPosition=null,controlDrag=null;
  try{const saved=JSON.parse(localStorage.getItem(CONTROL_POSITION_KEY));if(saved&&Number.isFinite(saved.x)&&Number.isFinite(saved.y))controlPosition=saved;}catch(_){}
  const controlsDark=getComputedStyle(document.documentElement).colorScheme==='dark';
  inputControls.style.cssText='display:none;position:fixed;z-index:2147483001;pointer-events:auto;flex-direction:column;align-items:stretch;gap:3px;padding:3px;border:1px solid '+(controlsDark?'rgba(255,255,255,.14)':'rgba(105,115,145,.2)')+';border-radius:10px;background:'+(controlsDark?'#23252b':'#fff')+';box-shadow:0 3px 10px rgba(25,32,72,.12);font:12px/1.2 system-ui,sans-serif';
  const translationNotice=document.createElement('div');translationNotice.setAttribute('role','status');translationNotice.setAttribute('aria-live','polite');
  translationNotice.style.cssText='display:none;position:absolute;right:0;bottom:calc(100% + 6px);width:auto;min-width:180px;max-width:min(320px,70vw);box-sizing:border-box;padding:7px 9px;border:1px solid transparent;border-radius:8px;white-space:normal;line-height:1.35;box-shadow:0 4px 14px rgba(25,32,72,.14)';
  let translationNoticeTimer=0;
  function translationErrorMessage(error){const message=String(error?.message||error||'翻译失败');return /(?:HTTP Error\s*)?429|Too Many Requests/i.test(message)?'翻译服务请求过于频繁（HTTP 429），请稍后再试。':message;}
  function showTranslationNotice(message,kind='error'){if(translationNoticeTimer)clearTimeout(translationNoticeTimer);translationNotice.textContent=message;translationNotice.style.display='block';translationNotice.style.color=kind==='error'?(controlsDark?'#ffb4ad':'#9f2d24'):(controlsDark?'#d4d8e3':'#394154');translationNotice.style.background=controlsDark?'#2d3038':'#fff';translationNotice.style.borderColor=kind==='error'?'rgba(204,65,52,.35)':'rgba(105,115,145,.25)';translationNoticeTimer=setTimeout(()=>{translationNoticeTimer=0;translationNotice.style.display='none';},6000);}
  const inputTranslationRow=document.createElement('div');inputTranslationRow.style.cssText='display:'+(config.translateInput?'flex':'none')+';align-items:center;gap:3px';
  const pageTranslationRow=document.createElement('div');pageTranslationRow.style.cssText='display:'+(config.translateInterface||config.translateMessages?'flex':'none')+';align-items:center;gap:3px';
  const directionSelect=document.createElement('select');directionSelect.setAttribute('aria-label','选择翻译方向');
  directionSelect.style.cssText='width:66px;height:30px;border:1px solid '+(controlsDark?'rgba(255,255,255,.2)':'rgba(120,130,160,.32)')+';border-radius:7px;padding:0 5px;background:transparent;color:'+(controlsDark?'#f1f3f7':'#303744')+';font:inherit;cursor:pointer;outline:none';
  directionSelect.append(new Option('中→英','zh-en'),new Option('英→中','en-zh'));
  const translateButton=document.createElement('button');translateButton.type='button';translateButton.textContent='翻译';translateButton.title='内容语言与所选方向一致时，回车翻译；Shift+回车换行；取消后再按回车即直接发送';
  translateButton.style.cssText='width:48px;height:30px;border:0;border-radius:7px;padding:0;background:#5868df;color:#fff;font:inherit;cursor:pointer';
  const originalPageButton=document.createElement('button');originalPageButton.type='button';originalPageButton.textContent='原文';originalPageButton.title='恢复当前页面的英文原文';originalPageButton.setAttribute('aria-pressed','false');
  originalPageButton.style.cssText='width:48px;height:30px;border:1px solid '+(controlsDark?'rgba(255,255,255,.2)':'rgba(120,130,160,.28)')+';border-radius:7px;padding:0;background:transparent;color:'+(controlsDark?'#f1f3f7':'#394154')+';font:inherit;cursor:pointer;transition:background .16s ease,color .16s ease';
  const translatePageButton=document.createElement('button');translatePageButton.type='button';translatePageButton.textContent='翻译页面';translatePageButton.title='翻译当前屏幕内的英文，并在全部完成后一起显示';
  translatePageButton.style.cssText='width:66px;height:30px;border:0;border-radius:7px;padding:0;background:#5868df;color:#fff;font:inherit;cursor:pointer';
  const controlHeader=document.createElement('div');controlHeader.style.cssText='display:flex;align-items:center;height:16px;gap:4px';
  const appBadge=document.createElement('span');appBadge.textContent=appName;appBadge.style.cssText='font:600 10px/16px system-ui,sans-serif;letter-spacing:.1px;color:'+(appName==='Claude'?(controlsDark?'#e9b69d':'#a35435'):(controlsDark?'#a5b5ff':'#5369bd'))+';padding-left:2px;user-select:none;white-space:nowrap';
  const dragHandle=document.createElement('button');dragHandle.type='button';dragHandle.textContent='⠿';dragHandle.setAttribute('aria-label','拖动翻译浮窗');dragHandle.title='拖动到空白位置';dragHandle.style.cssText='flex:1;height:16px;border:0;border-radius:4px;padding:0;background:transparent;color:inherit;opacity:.55;cursor:grab;touch-action:none;user-select:none';
  const resetPosition=document.createElement('button');resetPosition.type='button';resetPosition.textContent='↺';resetPosition.setAttribute('aria-label','恢复浮窗默认位置');resetPosition.title='恢复默认位置';resetPosition.style.cssText='width:18px;height:16px;padding:0;border:0;border-radius:4px;background:transparent;color:inherit;opacity:.55;cursor:pointer';
  controlHeader.append(appBadge,dragHandle,resetPosition);
  translateButton.style.background=accent;translatePageButton.style.background=accent;
  inputTranslationRow.append(directionSelect,translateButton);pageTranslationRow.append(originalPageButton,translatePageButton);inputControls.append(translationNotice,controlHeader,inputTranslationRow,pageTranslationRow);root.appendChild(inputControls);
  function controlBounds(width,height,viewportWidth,viewportHeight){return {left:8,top:8,maxLeft:Math.max(8,viewportWidth-width-8),maxTop:Math.max(8,viewportHeight-height-8)};}
  function placeControls(left,top){
    inputControls.style.display='flex';const rect=inputControls.getBoundingClientRect(),bounds=controlBounds(rect.width,rect.height,innerWidth,innerHeight);
    inputControls.style.right='auto';inputControls.style.bottom='auto';inputControls.style.left=Math.min(bounds.maxLeft,Math.max(bounds.left,left))+'px';inputControls.style.top=Math.min(bounds.maxTop,Math.max(bounds.top,top))+'px';
  }
  function rememberControlPosition(){
    const rect=inputControls.getBoundingClientRect(),bounds=controlBounds(rect.width,rect.height,innerWidth,innerHeight);
    controlPosition={x:(rect.left-bounds.left)/Math.max(1,bounds.maxLeft-bounds.left),y:(rect.top-bounds.top)/Math.max(1,bounds.maxTop-bounds.top)};
    try{localStorage.setItem(CONTROL_POSITION_KEY,JSON.stringify(controlPosition));}catch(_){}
  }
  dragHandle.addEventListener('pointerdown',event=>{
    if(event.button!==0)return;event.preventDefault();const rect=inputControls.getBoundingClientRect();controlDrag={id:event.pointerId,x:event.clientX,y:event.clientY,left:rect.left,top:rect.top};dragHandle.setPointerCapture(event.pointerId);dragHandle.style.cursor='grabbing';
  });
  dragHandle.addEventListener('pointermove',event=>{if(!controlDrag||event.pointerId!==controlDrag.id)return;placeControls(controlDrag.left+event.clientX-controlDrag.x,controlDrag.top+event.clientY-controlDrag.y);});
  function finishControlDrag(event){if(!controlDrag||event.pointerId!==controlDrag.id)return;controlDrag=null;dragHandle.style.cursor='grab';rememberControlPosition();}
  dragHandle.addEventListener('pointerup',finishControlDrag);dragHandle.addEventListener('pointercancel',finishControlDrag);dragHandle.addEventListener('lostpointercapture',finishControlDrag);
  resetPosition.addEventListener('click',()=>{controlPosition=null;try{localStorage.removeItem(CONTROL_POSITION_KEY);}catch(_){}schedulePosition();});
  function editorLooksLikeComposer(editor){const rect=editor.getBoundingClientRect();return rect.width>160&&rect.height>1&&rect.bottom>innerHeight*.45&&editorPriority(editor)>0;}
  function editorPriority(editor){return editor.matches('.ProseMirror,[data-lexical-editor="true"],[contenteditable="true"]')?2:editor.closest('[class*="ComposerLayout"]')?1:0;}
  function findEditor(){
    // 输入框是长寿命节点，缓存住就不用每轮渲染都全文档扫一次。
    // 失效条件：节点被移除（换会话、切项目），或几何不再符合输入框特征（窗口尺寸变化）。
    if(state.editor&&state.editor.isConnected&&!root.contains(state.editor)&&editorLooksLikeComposer(state.editor))return state.editor;
    const all=[...document.querySelectorAll(config.composerSelector||'textarea,[contenteditable="true"]')].filter(editor=>!root.contains(editor)&&editorLooksLikeComposer(editor));
    // 先测一次再排序：把 getBoundingClientRect 放进比较器会让同一次排序重复强制布局。
    const editor=all.map(node=>({node,priority:editorPriority(node),bottom:node.getBoundingClientRect().bottom})).sort((a,b)=>b.priority-a.priority||b.bottom-a.bottom)[0]?.node||null;
    state.editor=editor;return editor;
  }
  function editorText(editor){return editor instanceof HTMLTextAreaElement||editor instanceof HTMLInputElement?editor.value:editor.innerText;}
  function setEditorText(editor,value){
    if(editor instanceof HTMLTextAreaElement||editor instanceof HTMLInputElement){
      const setter=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(editor),'value')?.set;setter?setter.call(editor,value):editor.value=value;
      editor.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:value}));editor.dispatchEvent(new Event('change',{bubbles:true}));
    }else{
      // ProseMirror 必须收到真实编辑操作。直接改 textContent 只改 DOM，发送时仍会读取旧草稿。
      editor.focus();const selection=getSelection(),range=document.createRange();range.selectNodeContents(editor);
      selection.removeAllRanges();selection.addRange(range);
      if(!document.execCommand('insertText',false,value))return false;
    }
    return editorText(editor).trim()===value.trim();
  }
  function editorIsUncovered(editor){const r=editor.getBoundingClientRect(),hit=document.elementFromPoint(Math.min(r.right-8,r.left+24),Math.min(r.bottom-8,r.top+18));return !!hit&&(hit===editor||editor.contains(hit)||hit.contains(editor));}
  function sendButtonKind(button){
    const labels=[button.getAttribute('aria-label'),button.title,button.textContent].filter(Boolean).map(value=>value.trim());
    if(labels.some(value=>/^(stop|stop generating|停止|停止生成)(?:\s*\([^)]*\))?$/i.test(value)))return 'stop';
    if(labels.some(value=>/^(send(?: message| prompt)?|submit(?: message| prompt)?|queue(?: message)?|steer|发送(?:消息)?|排队|调整当前任务)(?:\s*\([^)]*\))?$/i.test(value)))return 'send';
    return !labels.length&&button.type==='submit'?'send':null;
  }
  function composerButtons(editor){
    // Input 和 Footer 都含 ComposerLayout；最近的 Input 节点没有发送按钮。
    if(!editor)return [];const scope=(config.composerScopeSelector&&editor.closest(config.composerScopeSelector))||editor.closest('form')||editor.closest('[class*="ComposerLayoutRoot"],[data-composer-root]')||editor.closest('[class*="ComposerLayoutFooter"]')||editor.parentElement;
    return scope?[...scope.querySelectorAll('button')].filter(button=>!root.contains(button)&&(!button.getClientRects||button.getClientRects().length>0)):[];
  }
  function findSendButton(editor){return composerButtons(editor).find(button=>sendButtonKind(button))||null;}
  function parkControls(){inputControls.style.display='flex';const rect=inputControls.getBoundingClientRect();placeControls(innerWidth-rect.width-12,innerHeight-rect.height-14);}
  function positionInputButton(editor=findEditor()){
    if(!config.translateInput&&!config.translateInterface&&!config.translateMessages){inputControls.style.display='none';return;}
    if(controlDrag)return;
    if(controlPosition){inputControls.style.display='flex';const rect=inputControls.getBoundingClientRect(),bounds=controlBounds(rect.width,rect.height,innerWidth,innerHeight);placeControls(bounds.left+controlPosition.x*(bounds.maxLeft-bounds.left),bounds.top+controlPosition.y*(bounds.maxTop-bounds.top));return;}
    if(!editor||!editorIsUncovered(editor)){parkControls();return;}
    const editorRect=editor.getBoundingClientRect();inputControls.style.display='flex';const controlRect=inputControls.getBoundingClientRect();
    placeControls(editorRect.right-controlRect.width,editorRect.top-controlRect.height-10);
  }
  const composerResizeObserver=new ResizeObserver(schedulePosition);
  function observeComposer(editor=findEditor()){
    // 对同一元素重复 observe 只是空转，但断开连接的旧目标必须显式 unobserve，
    // 否则换几次会话之后观察集合会一直变大。
    for(const observed of [...state.observedComposer])if(!observed.isConnected){composerResizeObserver.unobserve(observed);state.observedComposer.delete(observed);}
    for(const target of [editor,findSendButton(editor)]){
      if(!target||state.observedComposer.has(target))continue;
      composerResizeObserver.observe(target);state.observedComposer.add(target);
    }
  }
  function fallbackSendButton(editor){
    return composerButtons(editor).find(button=>sendButtonKind(button)==='send'&&!button.disabled&&button.getAttribute('aria-disabled')!=='true')||null;
  }
  function enabledSendButton(editor){
    return fallbackSendButton(editor);
  }
  function preview(original,translated,editor,direction){
    const outputLanguage=direction==='en-zh'?'中文':'英文',shade=document.createElement('div');shade.style.cssText='position:fixed;inset:0;z-index:2147483002;display:grid;place-items:center;pointer-events:auto;background:rgba(0,0,0,.38)';
    const surfaceBorder=controlsDark?'rgba(255,255,255,.18)':'#ccd3df',surfaceText=controlsDark?'#e8eaf0':'#20242d';
    const card=document.createElement('div');card.style.cssText='width:min(680px,86vw);max-height:76vh;overflow:auto;padding:20px;border-radius:14px;background:'+(controlsDark?'#23252b':'#fff')+';color:'+surfaceText+';font:14px/1.55 sans-serif;box-shadow:0 22px 70px rgba(0,0,0,.32)';
    const title=document.createElement('h3');title.textContent='确认'+outputLanguage+'文字 · 回车发送';title.style.margin='0 0 12px';const source=document.createElement('pre');source.textContent=original;source.style.cssText='white-space:pre-wrap;background:'+(controlsDark?'#2d3038':'#f2f4f7')+';padding:10px;border-radius:8px;color:'+(controlsDark?'#c7ccd8':'#596273');
    const output=document.createElement('textarea');output.value=translated;output.style.cssText='width:100%;min-height:150px;box-sizing:border-box;padding:10px;border:1px solid '+surfaceBorder+';border-radius:8px;font:13px/1.5 monospace;background:'+(controlsDark?'#1c1e23':'#fff')+';color:'+surfaceText;
    const previewError=document.createElement('div');previewError.setAttribute('role','alert');previewError.style.cssText='display:none;margin-top:8px;color:'+(controlsDark?'#ffb4ad':'#9f2d24');
    const actions=document.createElement('div');actions.style.cssText='display:flex;justify-content:flex-end;gap:8px;margin-top:12px';
    function button(label,primary,handler){const b=document.createElement('button');b.textContent=label;b.style.cssText='padding:8px 13px;border-radius:8px;border:1px solid '+surfaceBorder+';cursor:pointer;background:'+(primary?accent:(controlsDark?'#2d3038':'#fff'))+';color:'+(primary?'#fff':(controlsDark?'#e8eaf0':'#303744'));b.onclick=handler;return b;}
    function close(){shade.remove();document.removeEventListener('keydown',onPreviewKeydown,true);if(state.inputPreview?.element===shade)state.inputPreview=null;}
    function onPreviewKeydown(event){
      if(event.isComposing||event.keyCode===229)return;
      if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();state.enterBypassText=original;close();return;}
      if(event.key!=='Enter'||event.shiftKey||event.ctrlKey||event.altKey||event.metaKey||event.target!==output)return;
      event.preventDefault();event.stopImmediatePropagation();if(!event.repeat)write(true);
    }
    document.addEventListener('keydown',onPreviewKeydown,true);
    function write(send){
      // 弹窗打开期间 Codex 可能重建了输入框（切会话、发完消息清空），
      // 拿闭包里的旧引用写入会静默失败，所以先确认它还连在 DOM 上。
      const target=editor.isConnected?editor:findEditor(),translated=output.value;
      if(!target||!target.isConnected||editorText(target).trim()!==original){previewError.textContent='输入框已变化，请关闭预览后重新翻译。';previewError.style.display='block';return;}
      if(!translated.trim()){previewError.textContent='译文为空，请补充文字或取消。';previewError.style.display='block';return;}
      if(!setEditorText(target,translated)){previewError.textContent='无法将译文写入输入框，已取消发送。请检查草稿后重试。';previewError.style.display='block';return;}
      // The preview already approved this draft; Enter should send it, even when auto-send is unavailable.
      state.enterBypassText=translated.trim();
      state.inputSendPending=send;
      close();target.focus();
      if(!send)return;
      const started=Date.now(),deadline=started+SEND_BUTTON_POLL_MS;
      let stableChecks=0;
      (function pollSendButton(){
        if(state.destroyed){state.inputSendPending=false;return;}
        if(!target.isConnected||editorText(target).trim()!==translated.trim()){
          state.inputSendPending=false;
          showTranslationNotice('译文未稳定写入输入框，已取消自动发送，请检查草稿。');return;
        }
        stableChecks++;
        const button=enabledSendButton(target);
        // 原文已经让发送按钮可用；必须先等编辑器处理译文，不能把按钮可用当作写入完成。
        if(button&&stableChecks>=2&&Date.now()>=started+SEND_SETTLE_MS){state.inputSendPending=false;button.click();return;}
        if(Date.now()>=deadline){
          state.inputSendPending=false;
          showTranslationNotice(outputLanguage+'已写入输入框，但发送按钮不可用，请检查译文后手动发送。','info');
          return;
        }
        setTimeout(pollSendButton,60);
      })();
    }
    // 点了取消说明用户其实想直接发送，记下这段文字放行下一次回车，
    // 否则同一段内容会一直被劫持，用户根本发不出消息。
    actions.append(button('取消',false,()=>{state.enterBypassText=original;close();}),button('仅写入',false,()=>write(false)),button('写入并发送',true,()=>write(true)));card.append(title,source,output,previewError,actions);shade.appendChild(card);root.appendChild(shade);state.inputPreview={element:shade,confirm:()=>write(true),close};output.focus();
  }
  translateButton.addEventListener('pointerdown',event=>event.preventDefault());
  async function translateComposerInput(){const editor=findEditor(),original=editor&&editorText(editor).trim();if(!editor||!original||translateButton.disabled||state.inputPreview||state.inputSendPending)return;const direction=directionSelect.value,target=direction==='en-zh'?'zh-CN':'en';translateButton.disabled=true;directionSelect.disabled=true;translateButton.textContent='翻译中…';try{const values=await request([original],target,'input');const translated=String(values[0]||'').trim();if(!translated)throw new Error('翻译服务返回空译文，原文已保留');if(!state.destroyed)preview(original,translated,editor,direction);}catch(error){showTranslationNotice('翻译失败：'+translationErrorMessage(error));console.warn('Codex 工具箱输入翻译失败：',error);}finally{translateButton.disabled=false;directionSelect.disabled=false;translateButton.textContent='翻译';}}
  // click also covers keyboard activation; pointerup alone silently ignores Enter/Space on this button.
  translateButton.addEventListener('click',()=>{void translateComposerInput();});
  function textMatchesDirection(text,direction){
    const cjk=(text.match(/[\u4e00-\u9fff]/g)||[]).length,latin=(text.match(/[A-Za-z]/g)||[]).length;
    // 劫持回车只在“确实有东西可翻”时生效：内容语言与所选方向不符时，
    // 回车必须照常交给 Codex 发送，否则用户会发不出消息。
    // Chinese prompts often contain long Latin paths, identifiers and model names.
    // Counting those letters against Han characters made the Enter shortcut appear broken.
    return direction==='zh-en'?cjk>=2:latin>=3&&latin>cjk;
  }
  function inputTranslationShortcut(event){
    if(!config.translateInput||config.interceptEnter===false||event.defaultPrevented||event.key!=='Enter'||event.shiftKey||event.ctrlKey||event.altKey||event.metaKey||event.isComposing||event.keyCode===229||root.contains(event.target))return;
    const editor=findEditor();
    if(!editor||!(event.target===editor||editor.contains(event.target)))return;
    if(translateButton.disabled||state.inputSendPending){event.preventDefault();event.stopImmediatePropagation();return;}
    if(state.inputPreview){event.preventDefault();event.stopImmediatePropagation();if(!event.repeat)state.inputPreview.confirm();return;}
    const text=editorText(editor).trim();
    if(!text)return;
    if(state.enterBypassText===text){
      const button=enabledSendButton(editor);
      if(button){event.preventDefault();event.stopImmediatePropagation();if(!event.repeat)button.click();}
      return;
    }
    if(!textMatchesDirection(text,directionSelect.value))return;
    event.preventDefault();event.stopImmediatePropagation();if(!event.repeat)void translateComposerInput();
  }
  document.addEventListener('keydown',inputTranslationShortcut,true);
  function currentPageSnapshot(){
    state.rectCache.clear();
    // 同一屏里重复出现的文字要合并成一个条目；用 Map 按 identity 直接取，
    // 之前的 items.find 是逐项线性比对，整页下来是 O(n²)。
    const byIdentity=new Map(),items=[];
    const candidates=[...visibleInterfaceTextNodes(),...assistantBodyTextNodes().filter(node=>bodyNodePriority(node)===0)];
    for(const node of candidates){
      const original=sourceText(node);if(!bodyCandidate(original))continue;
      const kind=assistantBodyRoot(node)?'body':'interface',key=(kind==='body'?'body:zh:':'zh:')+original,identity=kind+'\u0000'+original;
      const existing=byIdentity.get(identity);
      if(existing){existing.nodes.push(node);continue;}
      const item={node,original,kind,key,nodes:[node]};byIdentity.set(identity,item);items.push(item);
    }
    return items;
  }
  function manualTranslationBatches(texts){
    const batches=[];let batch=[],characters=0;
    for(const text of texts){if(batch.length&&(batch.length>=BATCH_MAX_ITEMS||characters+text.length>BATCH_MAX_CHARACTERS)){batches.push(batch);batch=[];characters=0;}batch.push(text);characters+=text.length;}
    if(batch.length)batches.push(batch);return batches;
  }
  async function translateCurrentPage(){
    if(state.manualPageRunning)return;const snapshot=currentPageSnapshot();if(!snapshot.length){state.showTranslations=true;updatePageButtons();scheduleRender();return;}
    state.manualPageRunning=true;state.showTranslations=true;translatePageButton.disabled=true;originalPageButton.disabled=true;translatePageButton.textContent='翻译中…';
    const ready=new Map(),missing={interface:[],body:[]};
    for(const item of snapshot){const local=item.kind==='interface'&&config.translateInterface?localTranslation(item.original,item.node):null,cached=state.cache.get(item.key);if(local||cached)ready.set(item.key,local||cached);else missing[item.kind].push(item.original);}
    try{
      const results=new Map(),failures=[];let attemptedBatches=0;
      async function translateKind(kind){
        const unique=[...new Set(missing[kind])],batches=manualTranslationBatches(unique);let next=0;
        async function worker(){
          while(next<batches.length){
            const batch=batches[next++];attemptedBatches++;
            try{
              const values=await request(batch,'zh-CN',kind);
              batch.forEach((text,index)=>{const value=String(values[index]||'').trim();if(value)results.set(kind+'\u0000'+text,value);});
            }catch(error){
              // 一批失败不该连带丢掉其他批次已经翻好的内容；先记下来，最后统一提示。
              failures.push(error);console.warn('Codex 工具箱页面翻译批次失败：',error);
            }
          }
        }
        await Promise.all(Array.from({length:Math.min(4,batches.length)},worker));
      }
      await Promise.all([translateKind('interface'),translateKind('body')]);
      for(const item of snapshot){const translated=ready.get(item.key)||results.get(item.kind+'\u0000'+item.original);if(translated)ready.set(item.key,translated);}
      await new Promise(resolve=>requestAnimationFrame(()=>{
        const generation=++state.generation;
        if(state.destroyed){resolve();return;}
        for(const item of snapshot){const translated=ready.get(item.key);if(!translated)continue;cacheSet(item.key,translated);if(translated===item.original)continue;for(const node of item.nodes)if(node.isConnected&&sourceText(node)===item.original&&replacementStillAllowed(node,item))applyReplacement(node,item.original,translated,generation);}
        resolve();
      }));
      // Local and cached successes count too; a network failure must not erase them.
      const partial=failures.length>0&&ready.size>0;
      if(failures.length&&!partial){state.showTranslations=false;restoreAllTranslations();}
      if(failures.length)showTranslationNotice((partial?'部分页面翻译失败（'+failures.length+'/'+attemptedBatches+' 批）：':'页面翻译失败：')+translationErrorMessage(failures[0]),partial?'info':'error');
    }catch(error){state.showTranslations=false;restoreAllTranslations();showTranslationNotice('页面翻译失败：'+translationErrorMessage(error));console.warn('Codex 工具箱页面翻译失败：',error);}
    finally{state.manualPageRunning=false;translatePageButton.disabled=false;originalPageButton.disabled=false;translatePageButton.textContent='翻译页面';updatePageButtons();scheduleRender();}
  }
  function updatePageButtons(){
    const original=!state.showTranslations;originalPageButton.setAttribute('aria-pressed',String(original));originalPageButton.style.background=original?accent:'transparent';originalPageButton.style.color=original?'#fff':(controlsDark?'#f1f3f7':'#394154');
  }
  originalPageButton.addEventListener('click',()=>{if(state.manualPageRunning)return;state.showTranslations=false;restoreAllTranslations();updatePageButtons();scheduleRender();});
  translatePageButton.addEventListener('click',translateCurrentPage);
  function isOwnTranslationWrite(node){const entry=state.replacements.get(node);return !!entry&&node.nodeValue===entry.translatedRaw;}
  const observer=new MutationObserver(records=>{
    if(state.destroyed)return;
    for(const record of records){
      if(root.contains(record.target))continue;
      // 自己写回的译文同样会触发 characterData。不过滤的话每轮渲染都会引发
      // 下一轮全文档扫描，翻译开着时 CPU 一直降不下来。
      if(record.type==='characterData'&&isOwnTranslationWrite(record.target))continue;
      scheduleRender();return;
    }
  });
  observer.observe(document.documentElement,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['aria-hidden','data-state','hidden']});
  const resizeObserver=new ResizeObserver(viewportChanged);resizeObserver.observe(document.documentElement);addEventListener('scroll',viewportChanged,true);addEventListener('resize',viewportChanged);observeComposer();
  // 全文档扫描只在译文显示期间才有意义，空闲时只维持悬浮控件的位置，
  // 否则每 900ms 白扫一遍 DOM。
  const renderInterval=setInterval(()=>{if(state.showTranslations)scheduleRender();},900);
  const positionInterval=setInterval(()=>schedulePosition(),1000);
  scheduleRender();
  window.__codexToolboxSurfaceTranslation={diagnostics(){return {appName,translateInterface:!!config.translateInterface,translateMessages:!!config.translateMessages,translateInput:!!config.translateInput,showTranslations:state.showTranslations,cachedTexts:state.cache.size,cacheLimit:CACHE_LIMIT,cachedBodyTexts:[...state.cache.keys()].filter(key=>key.startsWith('body:zh:')).length,queuedBodyTexts:state.bodyQueue.size,pendingBodyTexts:state.bodyPending.size,bodyRequestsRunning:state.bodyRequestsRunning,bodyRequestLimit:BODY_TRANSLATION_CONCURRENCY,batchMaxItems:BATCH_MAX_ITEMS,batchMaxCharacters:BATCH_MAX_CHARACTERS,observedComposerTargets:state.observedComposer.size,assistantBodies:document.querySelectorAll(assistantSelector).length};},destroy(){
    state.destroyed=true;state.inputPreview?.close();observer.disconnect();resizeObserver.disconnect();composerResizeObserver.disconnect();clearInterval(renderInterval);clearInterval(positionInterval);if(translationNoticeTimer)clearTimeout(translationNoticeTimer);if(state.renderTimer)clearTimeout(state.renderTimer);if(state.renderFrame)cancelAnimationFrame(state.renderFrame);if(state.positionFrame)cancelAnimationFrame(state.positionFrame);removeEventListener('scroll',viewportChanged,true);removeEventListener('resize',viewportChanged);document.removeEventListener('keydown',inputTranslationShortcut,true);
    state.pending.forEach(x=>x.reject(new Error('翻译层已关闭')));state.pending.clear();state.bodyQueue.clear();state.bodyPending.clear();state.interfaceQueue.clear();state.interfacePending.clear();state.rectCache.clear();state.observedComposer.clear();state.editor=null;if(state.bodyStabilityTimer)clearTimeout(state.bodyStabilityTimer);restoreAllTranslations();root.remove();delete window.__codexToolboxTranslationResolve;delete window.__codexToolboxSurfaceTranslation;
  }};
  return 'Codex 表层翻译已启动';
})()
