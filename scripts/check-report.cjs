/* Browser checks for the generated report; Node is needed only for development. */
const assert = require('node:assert/strict');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {chromium} = require('playwright');

(async () => {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
      ? {executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}
      : {}),
  });
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1100}});
    const errors = [];
    const externalRequests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      if (/^https?:/.test(request.url())) externalRequests.push(request.url());
    });
    const reportPath = path.resolve(process.argv[2] || 'reports/demo/index.html');
    await page.goto(pathToFileURL(reportPath).href);
    await page.waitForSelector('.card');
    assert.equal(await page.locator('.card').count(), 3);
    assert.equal(await page.locator('.result-row').count(), 30);
    await page.locator('#load-more').click();
    assert.equal(await page.locator('.result-row').count(), 60);

    await page.locator('#strategy').selectOption('candidate');
    await page.locator('#status').selectOption('fail');
    assert.equal(await page.locator('.result-row').count(), 0);
    assert.match(await page.locator('.empty').textContent(), /No trials match/);

    await page.locator('#strategy').selectOption('baseline');
    await page.locator('#search').fill('expired-day-thirty-one');
    assert.equal(await page.locator('.result-row').count(), 3);
    await page.locator('.result-row summary').first().click();
    await page.waitForSelector('.details-body');
    assert.match(await page.locator('.details-body').first().textContent(), /policy_denied/);
    assert.equal(await page.locator('.result-row').first().locator('.check.failed').count(), 2);

    await page.locator('[data-view="explorer"]').click();
    assert.equal(await page.locator('#overview-view').isVisible(), false);
    assert.equal(await page.locator('#explorer-view').isVisible(), true);
    await page.locator('[data-view="overview"]').click();
    assert.equal(await page.locator('#overview-view').isVisible(), true);

    const downloadPromise = page.waitForEvent('download');
    await page.locator('#download').click();
    assert.match((await downloadPromise).suggestedFilename(), /^agent-eval-.*\.json$/);

    await page.locator('#search').fill('');
    await page.locator('#strategy').selectOption('all');
    await page.locator('#status').selectOption('all');
    if (process.env.EVAL_SCREENSHOT_PATH) {
      await page.screenshot({path: process.env.EVAL_SCREENSHOT_PATH, fullPage: false});
    }
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await page.evaluate(() =>
      document.documentElement.scrollWidth <= window.innerWidth), true);
    await page.locator('[data-view="explorer"]').click();
    assert.equal(await page.locator('#explorer-view').isVisible(), true);
    assert.deepEqual(errors, []);
    assert.deepEqual(externalRequests, []);
    console.log('PASS: desktop/mobile report, filters, trace inspection, navigation, export, no external requests.');
  } finally {
    await browser.close();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
