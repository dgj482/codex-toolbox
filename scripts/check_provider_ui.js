// Isolated browser regression test. Requires Playwright and installed Edge.
const fs = require('fs');
const path = require('path');
const assert = require('assert/strict');
const {chromium} = require('playwright');

(async () => {
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  try {
    const page = await browser.newPage({viewport: {width:1100, height:850}});
    const errors=[];page.on('pageerror', error=>errors.push(error.message));
    const requests=[];page.on('request', request=>requests.push(request.url()));
    await page.setContent(fs.readFileSync(path.join(__dirname, '..', 'ui.html'), 'utf8'));
    await page.evaluate(() => {
      clearInterval(bootstrapRetryTimer);
      window.testCalls=[];
      window.testProviders=[
        {id:'openai',name:'Official',kind:'official',current:true,model:'test'},
        {id:'test-one',name:'Gateway One',kind:'relay',current:false,model:'test'},
        {id:'test-two',name:'Gateway Two',kind:'relay',current:false,model:'test'},
      ];
      refreshStatus=async()=>{};renderRouteProviderOptions=()=>{};
      api=async(method,...args)=>{
        testCalls.push([method,...args]);
        if(method==='get_providers')return testProviders;
        if(method==='switch_provider')return await new Promise(resolve=>window.finishSwitch=resolve);
        if(method==='save_provider'){
          const data=args[0],id=data.id||'new-provider';
          testProviders=testProviders.filter(p=>p.id!==id).concat({...data,id});
          return {ok:true,id};
        }
        return null;
      };
      providers=testProviders;selectedProviderId='test-one';renderProviders();
      document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
      document.getElementById('provider-list').closest('.panel').classList.add('active');
    });
    if(process.env.PROVIDER_HEADER_SCREENSHOT)await page.locator('#provider-css-import').locator('..').locator('..').screenshot({path:process.env.PROVIDER_HEADER_SCREENSHOT,animations:'disabled'});
    const valid='.provider-card { background: linear-gradient(135deg, #f8faff, #e6eaff); border-color:#b8c5fa; border-radius:16px; box-shadow:0 6px 18px rgba(80,90,160,.12); } .provider-name { color:#213a75; font-weight:700; }';
    const [picker]=await Promise.all([page.waitForEvent('filechooser'),page.locator('#provider-css-import').click()]);
    assert.equal(await page.locator('#pm-name').inputValue(),'Gateway One');
    await picker.setFiles({name:'blue.css',mimeType:'text/css',buffer:Buffer.from(valid)});
    await page.waitForFunction(()=>providerStyleDrafts.get('pm-style').css.includes('linear-gradient'));
    assert.equal(await page.locator('#pm-style .p-name').evaluate(el=>getComputedStyle(el).color), 'rgb(33, 58, 117)');
    // Unsupported/global/remote CSS is rejected without discarding the last valid draft.
    for(const bad of ['body { background: red; }','.provider-card { position: fixed; }',
      '.provider-card { background: url(https://example.invalid/pixel); }',
      '@import "https://example.invalid/theme.css";', '.provider-card { box-shadow:0 0 0 9999px red; }',
      '.provider-card { background:var(--bad); }', '.provider-card { color: red;',
      '.provider-card { box-shadow:0 0 0 9999lh red; }']) {
      await page.locator('#pm-style input[type=file]').setInputFiles({name:'bad.css',mimeType:'text/css',buffer:Buffer.from(bad)});
      await page.waitForFunction(()=>!providerStyleDrafts.get('pm-style').loading);
      assert.equal(await page.locator('#pm-style [role=status]').getAttribute('data-error'),'true',bad);
    }
    await page.evaluate(()=>saveProvider());
    assert.equal(await page.locator('[data-provider-id="test-one"] .p-name').evaluate(el=>getComputedStyle(el).color), 'rgb(33, 58, 117)');
    assert.notEqual(await page.locator('[data-provider-id="test-two"] .p-name').evaluate(el=>getComputedStyle(el).color), 'rgb(33, 58, 117)');
    await page.evaluate(()=>{openProviderModal(providers.find(p=>p.id==='test-one'));});
    await page.locator('#pm-style [data-reset]').click();
    await page.evaluate(()=>saveProvider());
    assert.equal(await page.evaluate(()=>testProviders.find(p=>p.id==='test-one').card_css),'');
    await page.evaluate(()=>{
      selectedProviderId='test-two';switchSelected();switchSelected();selectProvider('openai');
    });
    assert.equal(await page.evaluate(()=>testCalls.filter(call=>call[0]==='switch_provider').length),1);
    assert.equal(await page.evaluate(()=>selectedProviderId),'test-two');
    assert(await page.locator('#btn-switch').isDisabled());
    await page.evaluate(()=>finishSwitch({ok:false,error:'Test failure'}));
    await page.waitForFunction(()=>!providerSwitching);
    assert(!(await page.locator('#btn-switch').isDisabled()));
    assert.equal(await page.evaluate(()=>testCalls.filter(call=>call[0]==='get_sessions').length),0);
    // Same component works in the Claude editor; theme switches do not install global CSS.
    await page.evaluate(()=>{
      claudeProviderRows=[{id:'claude-test',name:'Claude Gateway',base_url:'https://example.invalid'}];
      claudeProviderSelected='claude-test';
      document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
      document.getElementById('claude-provider-list').closest('.panel').classList.add('active');
      document.body.dataset.toolboxApp='claude';
      syncClaudeProviderActions();
    });
    const [claudePicker]=await Promise.all([page.waitForEvent('filechooser'),page.locator('#claude-provider-css-import').click()]);
    assert.equal(await page.locator('#claude-provider-name').inputValue(),'Claude Gateway');
    await claudePicker.setFiles({name:'orange.css',mimeType:'text/css',buffer:Buffer.from('.provider-card {background:#fff0e3;} .provider-name {color:#804327;}')});
    await page.waitForFunction(()=>providerStyleDrafts.get('claude-provider-style').css.includes('#fff0e3'));
    assert.equal(await page.locator('#claude-provider-style .p-name').evaluate(el=>getComputedStyle(el).color),'rgb(128, 67, 39)');
    await page.locator('#claude-provider-name').fill('Unsaved name');
    const [draftPicker]=await Promise.all([page.waitForEvent('filechooser'),page.locator('#claude-provider-css-import').click()]);
    await draftPicker.setFiles({name:'same.css',mimeType:'text/css',buffer:Buffer.from('.provider-name {color:#804327;}')});
    await page.waitForFunction(()=>!providerStyleDrafts.get('claude-provider-style').loading);
    assert.equal(await page.locator('#claude-provider-name').inputValue(),'Unsaved name');
    assert.deepEqual(errors,[]);
    assert.deepEqual(requests,[],'CSS import must not request external assets');
    if(process.env.PROVIDER_UI_SCREENSHOT){
      await page.evaluate(()=>{openProviderModal(providers.find(p=>p.id==='test-one'));document.querySelector('#pm-style details').open=true;});
      await page.locator('#pm-style input[type=file]').setInputFiles({name:'blue.css',mimeType:'text/css',buffer:Buffer.from(valid)});
      await page.waitForFunction(()=>!providerStyleDrafts.get('pm-style').loading);
      await page.locator('#pm-style').scrollIntoViewIfNeeded();
      await page.evaluate(()=>document.querySelectorAll('.toast').forEach(node=>node.remove()));
      await page.screenshot({path:process.env.PROVIDER_UI_SCREENSHOT, animations:'disabled'});
    }
    console.log('Provider CSS import, isolation, reset, persistence and switch regressions OK');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
