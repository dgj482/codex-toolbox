// Isolated UI checks for file-read races and provider import feedback.
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const {chromium}=require('playwright');
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1100,height:850}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.setContent(fs.readFileSync(path.join(__dirname,'..','ui.html'),'utf8'));
    await page.evaluate(()=>{
      clearInterval(bootstrapRetryTimer);
      window.calls=[];
      api=async(method,...args)=>{
        calls.push([method,...args]);
        if(method==='import_claude_providers')return await new Promise(resolve=>window.finishImport=resolve);
        if(method==='get_claude_providers')return {current_id:'claude-official',providers:[
          {id:'claude-official',name:'Official',official:true,current:true},
          {id:'new-test',name:'New provider',base_url:'https://example.invalid'}]};
        return null;
      };
      providers=[{id:'one',name:'First',kind:'relay'}];
      openProviderModal(providers[0]);
      const original=File.prototype.arrayBuffer;
      window.reads={};
      File.prototype.arrayBuffer=function(){
        if(this.name.startsWith('defer-'))return new Promise(resolve=>reads[this.name]=value=>resolve(new TextEncoder().encode(value).buffer));
        return original.call(this);
      };
    });
    const input=page.locator('#pm-style input[type=file]');
    const file=name=>({name,mimeType:'text/css',buffer:Buffer.from('.provider-name {color:blue;}')});
    await input.setInputFiles(file('defer-old.css'));
    await input.setInputFiles(file('defer-latest.css'));
    await page.evaluate(async()=>{reads['defer-old.css']('.provider-name {color:red;}');await new Promise(resolve=>setTimeout(resolve,0));});
    assert.equal(await page.evaluate(()=>providerStyleDrafts.get('pm-style').loading),true,'older completion must not unblock Save');
    await page.evaluate(()=>saveProvider());
    assert.equal(await page.evaluate(()=>calls.filter(c=>c[0]==='save_provider').length),0);
    await page.evaluate(()=>reads['defer-latest.css']('.provider-name {color:green;}'));
    await page.waitForFunction(()=>!providerStyleDrafts.get('pm-style').loading);
    assert((await page.evaluate(()=>providerStyleDrafts.get('pm-style').css)).includes('green'));
    // Older read finishing last must not overwrite the newer selection.
    await input.setInputFiles(file('defer-last.css'));
    await input.setInputFiles(file('fast.css'));
    await page.waitForFunction(()=>!providerStyleDrafts.get('pm-style').loading);
    await page.evaluate(async()=>{reads['defer-last.css']('.provider-name {color:red;}');await new Promise(resolve=>setTimeout(resolve,0));});
    assert((await page.evaluate(()=>providerStyleDrafts.get('pm-style').css)).includes('blue'));
    await input.setInputFiles({name:'encoding.css',mimeType:'text/css',buffer:Buffer.from([0xff,0xfe,0x41])});
    await page.waitForFunction(()=>!providerStyleDrafts.get('pm-style').loading);
    assert((await page.locator('#pm-style [role=status]').textContent()).includes('UTF-8'));
    assert((await page.evaluate(()=>providerStyleDrafts.get('pm-style').css)).includes('blue'));
    await input.setInputFiles(file('defer-reset.css'));
    await page.evaluate(()=>{beginProviderStyle('pm-style','');renderProviderStylePreview('pm-style','pm-name');});
    await page.evaluate(async()=>{reads['defer-reset.css']('.provider-name {color:red;}');await new Promise(resolve=>setTimeout(resolve,0));});
    assert.equal(await page.evaluate(()=>providerStyleDrafts.get('pm-style').css),'');
    await input.setInputFiles(file('defer-close.css'));
    await page.evaluate(()=>{closeProviderModal();openProviderModal({id:'other',name:'Other'});});
    await page.evaluate(async()=>{reads['defer-close.css']('.provider-name {color:red;}');await new Promise(resolve=>setTimeout(resolve,0));});
    assert.equal(await page.evaluate(()=>providerStyleDrafts.get('pm-style').css),'');
    await page.evaluate(async()=>{
      closeProviderModal();await loadClaudeProviders();
      importClaudeProviders(document.getElementById('claude-provider-import'));
      importClaudeProviders(document.getElementById('claude-provider-import'));
      switchClaudeProvider('new-test');
    });
    assert.equal(await page.evaluate(()=>calls.filter(c=>c[0]==='import_claude_providers').length),1);
    assert.equal(await page.evaluate(()=>calls.filter(c=>c[0]==='switch_claude_provider').length),0);
    assert(await page.locator('#claude-provider-switch').isDisabled());
    await page.evaluate(()=>finishImport({ok:true,message:'Imported 1, invalid 1; active channel unchanged',imported_ids:['new-test'],items:[
      {name:'New provider',status:'imported',message:'Saved'},
      {name:'<img src=x onerror=alert(1)>',status:'failed',message:'Invalid source'}]}));
    await page.waitForFunction(()=>!claudeProviderImporting);
    assert.equal(await page.locator('#claude-import-report li').count(),2);
    assert.equal(await page.locator('#claude-import-report img').count(),0);
    assert.equal(await page.locator('#claude-import-report').getAttribute('open'),'');
    assert.equal(await page.evaluate(()=>claudeProviderSelected),'new-test');
    assert.equal(await page.evaluate(()=>claudeChannelCurrent),'claude-official');
    assert(!(await page.locator('#claude-provider-import').isDisabled()));
    await page.evaluate(()=>{importClaudeProviders(document.getElementById('claude-provider-import'));});
    await page.evaluate(()=>finishImport({ok:false,error:'Storage unavailable'}));
    await page.waitForFunction(()=>!claudeProviderImporting);
    assert.equal(await page.locator('#claude-channel-status').textContent(),'Storage unavailable');
    assert(await page.locator('#claude-import-report').evaluate(el=>el.hidden));
    assert(!(await page.locator('#claude-provider-import').isDisabled()));
    assert.deepEqual(errors,[]);
    console.log('Provider import report, concurrent-operation guard, UTF-8 and stale CSS read regressions OK');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
