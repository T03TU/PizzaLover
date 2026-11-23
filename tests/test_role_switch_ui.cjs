/* Session role switch and visible alias regression against tests/ui_server.py. */
const {chromium}=require(process.env.PLAYWRIGHT_PATH || 'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH || '/usr/bin/google-chrome',headless:true,args:['--no-sandbox']});
 try {
  const admin=await browser.newContext();
  await admin.request.post('http://127.0.0.1:8765/api/auth/login',{data:{name:'Admin',password:'admin-password-123'}});
  const suffix=Date.now(),username=`Alias${suffix}`,role=`CHAT${suffix}`;
  await admin.request.post('http://127.0.0.1:8765/api/roles',{data:{name:role,permissions:['chat:use']}});
  await admin.request.post('http://127.0.0.1:8765/api/users',{data:{name:`Display Name ${suffix}`,username,password:'switch-role-password-123',roles:['USER','ADMIN',role]}});
  const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto('http://127.0.0.1:8765');
  await page.locator('#auth-dialog').waitFor({state:'visible'});
  await page.locator('#auth-name').fill(username);await page.locator('#auth-password').fill('switch-role-password-123');
  await page.locator('#auth-form button[type=submit]').click();
  await page.waitForFunction(()=>!document.getElementById('send').disabled);
  assert.equal(await page.locator('#signed-in-alias').textContent(),username);
  assert.equal(await page.locator('#active-role').inputValue(),'ADMIN');
  await page.locator('#active-role').selectOption('USER');
  await page.waitForFunction(()=>!document.getElementById('active-role').disabled && document.getElementById('active-role').value==='USER');
  assert.equal(await page.evaluate(async()=>(await fetch('/api/users')).status),403);
  await page.locator('#settings').click();
  assert.equal(await page.locator('#open-users').isVisible(),false);
  assert.equal(await page.locator('#open-pipeline').isVisible(),false);
  await page.locator('[data-close="settings-dialog"]').click();
  await page.reload();await page.waitForFunction(()=>!document.getElementById('active-role').disabled);
  assert.equal(await page.locator('#active-role').inputValue(),'USER');
  await page.locator('#active-role').selectOption(role);
  await page.waitForFunction(role=>!document.getElementById('active-role').disabled && document.getElementById('active-role').value===role,role);
  assert.equal(await page.locator('.quality').isVisible(),false);
  assert.equal(await page.locator('.scorecard').isVisible(),false);
  assert.equal(await page.evaluate(async()=>(await fetch('/api/evaluation/summary')).status),403);
  await page.locator('#active-role').selectOption('ADMIN');
  await page.waitForFunction(()=>!document.getElementById('active-role').disabled && document.getElementById('active-role').value==='ADMIN');
  await page.locator('#settings').click();assert.equal(await page.locator('#open-users').isVisible(),true);
  await page.locator('[data-close="settings-dialog"]').click();
  assert.equal(await page.evaluate(async()=>(await fetch('/api/users')).status),200);
  await page.setViewportSize({width:390,height:900});
  assert.equal(await page.locator('#active-role').isVisible(),true);
  assert.equal(await page.locator('#signed-in-alias').isVisible(),true);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.screenshot({path:'/tmp/pizzalover-active-role-mobile.png',fullPage:true});
  assert.deepEqual(errors,[]);
  console.log('PASS: visible username, active-role switching, scoped controls/API permissions, role persistence on reload, switching back to ADMIN, and mobile layout.');
  await admin.close();
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
