/* Optional browser regression: start tests/ui_server.py, then run with Playwright installed. */
const {chromium} = require(process.env.PLAYWRIGHT_PATH || 'playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH || '/usr/bin/google-chrome', headless:true, args:['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport:{width:1440, height:1000}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:8765');
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

    async function createProfile(name) {
      await page.locator('#add-profile').click();
      await page.locator('#profile-name').fill(name);
      await page.locator('#profile-create').click();
      await page.waitForFunction(name => document.querySelector('#profile option:checked').textContent === name && !document.getElementById('send').disabled, name);
    }
    async function ask(question) {
      await page.locator('#question').fill(question);
      await page.locator('#send').click();
      await page.waitForFunction(() => !document.getElementById('send').disabled && document.querySelector('.message.assistant .citation'));
      await page.waitForFunction(() => document.querySelectorAll('.history-item').length > 0);
    }
    const suffix = Date.now();
    const alice = `Alice ${suffix}`, bob = `Bob ${suffix}`;
    await createProfile(alice);
    const aliceId = await page.locator('#profile').inputValue();
    await page.locator('#eval').uncheck();
    await ask('Best pepperoni?');
    await ask('How is the service?');
    assert.equal(await page.locator('.message.assistant').count(), 2);
    assert.equal(await page.locator('.history-item').count(), 1);
    await page.reload();
    await page.waitForFunction(() => !document.getElementById('send').disabled && document.querySelectorAll('.message.assistant').length === 2);
    assert.equal(await page.locator('.review-card').count(), 1);
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

    await createProfile(bob);
    assert.equal(await page.locator('.message').count(), 0);
    assert.equal(await page.locator('.history-item').count(), 0);
    await page.locator('#profile').selectOption(aliceId);
    await page.waitForFunction(() => !document.getElementById('send').disabled && document.querySelectorAll('.message.assistant').length === 2);

    await page.locator('#eval').uncheck();
    await page.locator('#question').fill('Stop this request');
    await page.locator('#send').click();
    await page.waitForFunction(() => document.querySelector('.message.assistant:last-child .message-body').textContent.includes('Partial answer'));
    assert.equal(await page.locator('#profile').isDisabled(), true);
    await page.locator('#stop').click();
    await page.waitForFunction(() => !document.getElementById('send').disabled);
    await page.waitForFunction(async () => {
      const chats = await (await fetch('/api/chats')).json();
      const chat = await (await fetch(`/api/chats/${chats.chats[0].id}`)).json();
      return chat.turns.at(-1).status === 'interrupted' && chat.turns.at(-1).text.includes('Partial answer');
    });
    await page.reload();
    await page.waitForFunction(() => !document.getElementById('send').disabled && document.querySelectorAll('.message.assistant').length === 3);
    assert.match(await page.locator('.message.assistant:last-child .message-footer').textContent(), /did not finish/);

    // Each combination must remain usable without horizontal overflow.
    for (const width of [1440, 1000, 850, 390]) {
      await page.setViewportSize({width, height:1000});
      for (let mask = 0; mask < 8; mask++) {
        for (const [index, id] of ['sidebar', 'chat-pane', 'evidence-pane'].entries()) {
          const visible = !(mask & (1 << index));
          if ((await toggle(id).getAttribute('aria-expanded') === 'true') !== visible) await toggle(id).click();
        }
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
        assert.equal(overflow, false, `Horizontal overflow at ${width}px, hidden mask ${mask}`);
      }
      for (const id of ['sidebar', 'chat-pane', 'evidence-pane']) await toggle(id).click();
    }
    await page.setViewportSize({width:1440, height:1000});
    await page.screenshot({path:'/tmp/pizzalover-history-desktop.png', fullPage:true});
    await page.setViewportSize({width:390, height:900});
    await page.screenshot({path:'/tmp/pizzalover-history-mobile.png', fullPage:true});
    await page.locator('.history-delete').click();
    await page.waitForFunction(() => !document.getElementById('send').disabled && document.querySelectorAll('.history-item').length === 0);
    assert.equal(await page.locator('.message').count(), 0);
    assert.deepEqual(errors, []);
    console.log('PASS: pane combinations and saved preferences, profile switching, chat continuation/restoration/deletion, stopped turn persistence, sources, and responsive layouts.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
