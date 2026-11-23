/* Start tests/ui_server.py, then run with Playwright and Chrome installed. */
const {chromium} = require(process.env.PLAYWRIGHT_PATH || 'playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH || '/usr/bin/google-chrome', headless:true, args:['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport:{width:1440, height:1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:8765');
    async function login(name, password) {
      await page.locator('#auth-dialog').waitFor({state:'visible'});
      await page.locator('#auth-name').fill(name);
      await page.locator('#auth-password').fill(password);
      await page.locator('#auth-form button[type=submit]').click();
      await page.locator('#auth-dialog').waitFor({state:'hidden'});
      await page.waitForFunction(() => !document.getElementById('settings').disabled);
    }
    await login('Admin', 'admin-password-123');
    await page.waitForFunction(() => !document.getElementById('send').disabled);
    const toggle = id => page.locator(`[data-pane="${id}"]`);
    for (const id of ['sidebar', 'chat-pane', 'evidence-pane']) {
      await toggle(id).click();
      assert.equal(await page.locator(`#${id}`).isVisible(), false);
      assert.equal(await toggle(id).getAttribute('aria-expanded'), 'false');
    }
    assert.equal(await page.locator('#panes-empty').isVisible(), true);
    await page.reload();
    await page.waitForFunction(() => !document.getElementById('send').disabled);
    assert.equal(await page.locator('#panes-empty').isVisible(), true);
    for (const id of ['sidebar', 'chat-pane', 'evidence-pane']) await toggle(id).click();
    const suffix = Date.now();
    const reader = `Reader ${suffix}`;
    assert.equal(await page.locator('.topbar #add-profile').count(), 0);
    await page.locator('#settings').click();
    await page.locator('#open-users').click();
    await page.locator('#add-profile').click();
    await page.locator('#profile-name').fill(reader);
    await page.locator('#profile-password').fill('reader-password-123');
    await page.locator('#profile-create').click();
    await page.locator('#profile-dialog').waitFor({state:'hidden'});
    assert.equal(await page.locator('#profile option:checked').textContent(), 'Admin');
    await page.locator('[data-close="users-dialog"]').click();
    await page.locator('[data-close="settings-dialog"]').click();

    // Stage, clean and embed through the actual settings dialog.
    await page.locator('#settings').click();
    await page.locator('#open-pipeline').click();
    await page.locator('#stage-file').setInputFiles({name:'staged.csv', mimeType:'text/csv', buffer:Buffer.from(`Feedback_ID,Franchise,Location,Rating,Comment\nFB-UI-${suffix},Old name,Auckland,5,Great pizza\n`)});
    await page.locator('#stage-upload').click();
    await page.waitForFunction(() => document.getElementById('pipeline-status').textContent.includes('1 reviews staged'));
    await page.locator('#pipeline-sql').fill("UPDATE staging_reviews SET Franchise = 'Clean name' RETURNING *");
    await page.locator('#run-sql').click();
    await page.waitForFunction(() => document.getElementById('pipeline-status').textContent.includes('1 rows changed'));
    assert.match(await page.locator('#pipeline-results').textContent(), /Clean name/);
    await page.locator('#pipeline-sql').fill('DROP TABLE staging_reviews');
    await page.locator('#run-sql').click();
    await page.waitForFunction(() => document.getElementById('pipeline-status').textContent.includes('SQL rejected'));
    await page.locator('#embed-staged').click();
    await page.waitForFunction(() => document.getElementById('pipeline-status').textContent.includes('1 updated reviews embedded'));
    await page.locator('[data-close="pipeline-dialog"]').click();

    // Create a role with only chat permission.
    await page.locator('#open-roles').click();
    await page.locator('#role-select option[value="ADMIN"]').waitFor({state:'attached'});
    await page.locator('#role-name').fill(`Limited ${suffix}`);
    await page.locator('#permission-fields input[value="chat:use"]').check();
    await page.locator('#role-save').click();
    await page.waitForFunction(() => document.getElementById('role-status').textContent === 'Role saved.');
    await page.locator('[data-close="roles-dialog"]').click();
    await page.locator('[data-close="settings-dialog"]').click();

    async function ask(question) {
      await page.locator('#question').fill(question);
      await page.locator('#send').click();
      await page.waitForFunction(() => !document.getElementById('send').disabled && document.querySelector('.message.assistant .citation'));
      await page.waitForFunction(() => document.querySelectorAll('.history-item').length > 0);
    }
    await page.locator('#eval').uncheck();
    await ask('Best pepperoni?');
    await ask('How is the service?');
    assert.equal(await page.locator('.message.assistant').count(), 2);
    assert.equal(await page.locator('.history-item').count(), 1);
    assert.equal(await page.locator('.feedback-controls').count(), 2);
    let feedbackRequests = 0;
    page.on('request', request => { if (request.url().endsWith('/api/feedback') && request.method() === 'POST') feedbackRequests++; });
    const feedback = page.locator('.feedback-controls').first();
    await feedback.getByRole('button', {name:'👎 Thumbs Down'}).click();
    await feedback.locator('textarea').fill('Discard this draft');
    await feedback.getByRole('button', {name:'Cancel', exact:true}).click();
    assert.equal(await feedback.locator('form').isVisible(), false);
    assert.equal(await feedback.getByRole('button', {name:'👎 Thumbs Down'}).getAttribute('aria-pressed'), 'false');
    assert.equal(await feedback.locator('textarea').inputValue(), '');
    assert.equal(feedbackRequests, 0);
    await page.locator('.feedback-controls').first().getByRole('button', {name:'👍 Thumbs Up'}).click();
    await page.locator('.feedback-controls textarea').first().fill('Useful sources');
    await page.locator('.feedback-controls').first().getByRole('button', {name:'Submit', exact:true}).click();
    await page.waitForFunction(() => document.querySelector('.feedback-controls p').textContent.includes('Feedback saved'));
    await feedback.getByRole('button', {name:'👎 Thumbs Down'}).click();
    await feedback.locator('textarea').fill('Discard changed feedback');
    await feedback.getByRole('button', {name:'Cancel', exact:true}).click();
    assert.equal(await feedback.getByRole('button', {name:'👍 Thumbs Up'}).getAttribute('aria-pressed'), 'true');
    assert.equal(await feedback.locator('textarea').inputValue(), 'Useful sources');
    assert.equal(feedbackRequests, 1);
    await page.reload();
    await page.waitForFunction(() => !document.getElementById('send').disabled && document.querySelectorAll('.message.assistant').length === 2);
    assert.equal(await page.locator('.feedback-controls').first().getByRole('button', {name:'👍 Thumbs Up'}).getAttribute('aria-pressed'), 'true');
    assert.equal(await page.locator('.review-card').count(), 2);
    await toggle('chat-pane').click();
    await page.reload();
    await page.waitForFunction(() => !document.getElementById('send').disabled);
    assert.equal(await page.locator('#chat-pane').isVisible(), false);
    await toggle('chat-pane').click();
    await toggle('evidence-pane').click();
    await page.locator('.message.assistant .citation').first().click();
    assert.equal(await page.locator('#evidence-pane').isVisible(), true);
    await page.locator('#new-chat').click();
    assert.equal(await page.locator('.message').count(), 0);
    await page.locator('.history-open').click();
    await page.waitForFunction(() => !document.getElementById('send').disabled && document.querySelectorAll('.message.assistant').length === 2);
    await page.locator('#eval').uncheck();
    await page.locator('#question').fill('Stop this request');
    await page.locator('#send').click();
    await page.waitForFunction(() => document.querySelector('.message.assistant:last-child .message-body').textContent.includes('Partial answer'));
    assert.equal(await page.locator('.message.assistant:last-child .feedback-controls').count(), 0);
    await page.locator('#stop').click();
    await page.waitForFunction(() => !document.getElementById('send').disabled);
    await page.waitForFunction(async () => {
      const chats = await (await fetch('/api/chats')).json();
      const chat = await (await fetch(`/api/chats/${chats.chats[0].id}`)).json();
      return chat.turns.at(-1).status === 'interrupted';
    });
    await page.reload();
    await page.waitForFunction(() => !document.getElementById('send').disabled && document.querySelectorAll('.message.assistant').length === 3);
    assert.match(await page.locator('.message.assistant:last-child .message-footer').textContent(), /did not finish/);
    assert.equal(await page.locator('.message.assistant:last-child .feedback-controls').count(), 0);

    for (const width of [1440, 1000, 850, 390]) {
      await page.setViewportSize({width, height:1000});
      for (let mask = 0; mask < 8; mask++) {
        for (const [index, id] of ['sidebar', 'chat-pane', 'evidence-pane'].entries()) {
          const visible = !(mask & (1 << index));
          if ((await toggle(id).getAttribute('aria-expanded') === 'true') !== visible) await toggle(id).click();
        }
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `Overflow at ${width}px, mask ${mask}`);
      }
      for (const id of ['sidebar', 'chat-pane', 'evidence-pane']) await toggle(id).click();
    }
    await page.setViewportSize({width:1440, height:1000});
    await page.screenshot({path:'/tmp/pizzalover-rbac-desktop.png', fullPage:true});
    await page.setViewportSize({width:390, height:900});
    await page.screenshot({path:'/tmp/pizzalover-rbac-mobile.png', fullPage:true});
    await page.locator('#sign-out').click();
    await login(reader, 'reader-password-123');
    await page.waitForFunction(() => !document.getElementById('send').disabled);
    assert.equal(await page.locator('.history-item').count(), 0);
    assert.equal(await page.locator('#add-profile').isVisible(), false);
    await page.locator('#settings').click();
    assert.equal(await page.locator('#settings-dialog').isVisible(), true);
    for (const id of ['open-pipeline', 'open-roles', 'open-users']) assert.equal(await page.locator(`#${id}`).isVisible(), false);
    await page.locator('[data-close="settings-dialog"]').click();
    assert.equal(await page.evaluate(async () => (await fetch('/api/pipeline')).status), 403);
    await ask('Reader question');
    assert.equal(await page.locator('.feedback-controls').count(), 1);
    // Assign the custom role through an independently signed-in admin API context.
    const admin = await browser.newContext();
    await admin.request.post('http://127.0.0.1:8765/api/auth/login', {data:{name:'Admin', password:'admin-password-123'}});
    const users = (await (await admin.request.get('http://127.0.0.1:8765/api/users')).json()).users;
    const user = users.find(user => user.name === reader);
    await admin.request.patch(`http://127.0.0.1:8765/api/users/${user.id}`, {data:{role:`Limited ${suffix}`}});
    await page.reload();
    await page.waitForFunction(() => !document.getElementById('send').disabled);
    assert.equal(await page.locator('.feedback-controls').count(), 0);
    assert.equal(await page.locator('.scorecard').isVisible(), false);
    assert.equal(await page.locator('.quality').isVisible(), false);
    await page.locator('#settings').click();
    assert.equal(await page.locator('#settings-dialog').isVisible(), true);
    const adminPage = await admin.newPage();
    adminPage.on('pageerror', error => errors.push(error.message));
    await adminPage.goto('http://127.0.0.1:8765');
    await adminPage.waitForFunction(() => !document.getElementById('send').disabled);
    await adminPage.locator('#settings').click();
    assert.equal(await adminPage.locator('#settings-dialog #reset-knowledge').count(), 0);
    await adminPage.locator('#open-pipeline').click();
    let resetRequests = 0;
    adminPage.on('request', request => { if (request.url().endsWith('/api/pipeline/reset') && request.method() === 'POST') resetRequests++; });
    await adminPage.locator('#reset-knowledge').click();
    await adminPage.locator('#reset-cancel').click();
    await adminPage.locator('#reset-knowledge').click();
    await adminPage.locator('#reset-confirmation').fill('wrong');
    assert.equal(await adminPage.locator('#reset-confirm').isDisabled(), true);
    assert.equal(resetRequests, 0);
    assert.equal(await adminPage.evaluate(async () => (await (await fetch('/api/metadata/options')).json()).total_documents), 2);
    await adminPage.locator('#reset-confirmation').fill('CONFIRM');
    await adminPage.locator('#reset-confirm').click();
    await adminPage.locator('#reset-knowledge-dialog').waitFor({state:'hidden'});
    await adminPage.waitForFunction(() => document.getElementById('reset-status').textContent.includes('2 embedded reviews removed'));
    assert.equal(resetRequests, 1);
    assert.equal(await adminPage.evaluate(async () => (await (await fetch('/api/metadata/options')).json()).total_documents), 0);
    await adminPage.locator('[data-close="pipeline-dialog"]').click();
    await adminPage.locator('[data-close="settings-dialog"]').click();
    await adminPage.locator('#question').fill('List the previously indexed pizza places');
    await adminPage.locator('#send').click();
    await adminPage.waitForFunction(() => !document.getElementById('send').disabled && document.querySelector('.message.assistant .message-body')?.textContent.includes('knowledge bank is empty'));
    assert.equal(await adminPage.locator('.review-card').count(), 0);
    await adminPage.locator('#settings').click();
    await adminPage.locator('#open-users').click();
    const readerForm = adminPage.locator('.user-editor').filter({hasText:reader});
    await readerForm.locator('input[type="checkbox"]').uncheck();
    await readerForm.getByRole('button', {name:'Save user', exact:true}).click();
    await adminPage.waitForFunction(() => document.getElementById('users-status').textContent === 'User saved.');
    await page.reload();
    await page.locator('#auth-dialog').waitFor({state:'visible'});
    adminPage.once('dialog', dialog => dialog.accept());
    await readerForm.getByRole('button', {name:`Remove user ${reader}`, exact:true}).click();
    await adminPage.waitForFunction(() => document.getElementById('users-status').textContent === 'User removed.');
    assert.equal(await adminPage.locator('.user-editor').filter({hasText:reader}).count(), 0);
    await admin.close();
    assert.deepEqual(errors, []);
    console.log('PASS: authenticated accounts, permissions, staging SQL and embedding, custom roles, feedback persistence, incomplete responses, chat restoration, responsive pane layouts, account restriction/removal, and knowledge reset.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
