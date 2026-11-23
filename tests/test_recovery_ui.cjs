/* Run against tests/ui_server.py; SERVER_LOG points to its console log. */
const {chromium} = require(process.env.PLAYWRIGHT_PATH || 'playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
(async () => {
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH || '/usr/bin/google-chrome', headless:true, args:['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport:{width:390, height:900}});
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:8765');
    await page.locator('#auth-dialog').waitFor({state:'visible'});
    const logCode = () => [...fs.readFileSync(process.env.SERVER_LOG, 'utf8').matchAll(/PASSWORD RESET.*code (\d{6})/g)].at(-1)[1];
    for (const [channel, identifier, password] of [
      ['email', 'BROWSER@EXAMPLE.COM', 'email-recovered-password-123'],
      ['phone', '021 123 4567', 'phone-recovered-password-123']
    ]) {
      await page.locator('#forgot-password').click();
      await page.locator('#recovery-channel').selectOption(channel);
      assert.equal(await page.locator('#recovery-dev').isVisible(), true);
      if (channel === 'phone') {
        await page.locator('#recovery-country-search').fill('+64');
        await page.locator('#recovery-country').selectOption('NZ');
        assert.match(await page.locator('#recovery-country option:checked').textContent(), /New Zealand \(NZ\) \+64/);
      }
      await page.locator('#recovery-identifier').fill(identifier);
      await page.locator('#recovery-request-form button').click();
      await page.locator('#recovery-verify-form').waitFor({state:'visible'});
      assert.match(await page.locator('#recovery-confirmation').textContent(), /If an account matches/);
      const code = logCode();
      const wrong = code === '000000' ? '111111' : '000000';
      await page.locator('#recovery-code').fill(wrong);
      await page.locator('#recovery-verify-form button[type=submit]').click();
      await page.waitForFunction(() => document.getElementById('recovery-error').textContent.includes('Invalid or expired'));
      await page.locator('#recovery-code').fill(code);
      await page.locator('#recovery-verify-form button[type=submit]').click();
      await page.locator('#recovery-password-form').waitFor({state:'visible'});
      await page.locator('#recovery-password').fill(password);
      await page.locator('#recovery-confirm-password').fill(password + '-mismatch');
      await page.locator('#recovery-password-form button').click();
      assert.match(await page.locator('#recovery-error').textContent(), /Passwords do not match/);
      await page.locator('#recovery-confirm-password').fill(password);
      await page.locator('#recovery-password-form button').click();
      await page.locator('#recovery-dialog').waitFor({state:'hidden'});
      assert.match(await page.locator('#auth-notice').textContent(), /Password updated/);
      if (channel === 'phone') {
        await page.locator('#auth-method').selectOption('phone');
        await page.locator('#auth-country-search').fill('New Zealand');
        await page.locator('#auth-country').selectOption('NZ');
      }
      await page.locator('#auth-name').fill(identifier);
      await page.locator('#auth-password').fill(password);
      await page.locator('#auth-form button[type=submit]').click();
      await page.locator('#auth-dialog').waitFor({state:'hidden'});
      await page.waitForFunction(() => !document.getElementById('sign-out').disabled);
      await page.locator('#settings').click();
      await page.locator('#sign-out').click();
      await page.locator('#auth-dialog').waitFor({state:'visible'});
    }
    await page.locator('#forgot-password').click();
    await page.locator('#recovery-identifier').fill('unknown@example.com');
    await page.locator('#recovery-request-form button').click();
    await page.locator('#recovery-verify-form').waitFor({state:'visible'});
    assert.match(await page.locator('#recovery-confirmation').textContent(), /If an account matches/);
    await page.locator('#recovery-cancel').click();
    assert.equal(await page.locator('#auth-dialog').isVisible(), true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.screenshot({path:'/tmp/pizzalover-recovery-mobile.png', fullPage:true});
    assert.deepEqual(errors, []);
    console.log('PASS: email and phone recovery, searchable countries, console code delivery, invalid codes, password confirmation, normalized sign-in and generic unknown-account response.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
