const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const required = name => {
  const value = String(process.env[name] || '').trim();
  if (!value) throw new Error(`Missing required environment variable: ${name}`);
  return value;
};

const safeUrl = (base, pathname) => new URL(pathname, base).toString();
const timestamp = () => new Date().toISOString().replace(/[:.]/g, '-');

async function main() {
  const baseUrl = new URL(required('INTERNAL_TEST_BASE_URL'));
  if (!['http:', 'https:'].includes(baseUrl.protocol)) {
    throw new Error('INTERNAL_TEST_BASE_URL must use http or https.');
  }
  const username = required('INTERNAL_TEST_USERNAME');
  const password = required('INTERNAL_TEST_PASSWORD');
  const marker = String(process.env.INTERNAL_TEST_EXPECTED_MARKER || 'ТЕСТОВЫЙ ПОРТАЛ').trim();
  const loginPath = String(process.env.INTERNAL_TEST_LOGIN_PATH || '/accounts/login/').trim();
  const targetPath = String(process.env.INTERNAL_TEST_TARGET_PATH || '/dashboard/').trim();
  const resultsDir = path.resolve(required('INTERNAL_TEST_RESULTS_DIR'));
  fs.mkdirSync(resultsDir, { recursive: true });

  const startedAt = new Date();
  const diagnostics = [];
  const report = {
    status: 'failed',
    started_at: startedAt.toISOString(),
    finished_at: null,
    portal_origin: baseUrl.origin,
    target_path: targetPath,
    current_url: null,
    screenshots: [],
    diagnostics,
    error: null,
  };
  let browser;
  let page;

  const screenshot = async name => {
    if (!page) return;
    const filename = `${name}-${timestamp()}.png`;
    await page.screenshot({ path: path.join(resultsDir, filename), fullPage: true, animations: 'disabled' });
    report.screenshots.push(filename);
  };

  try {
    const executablePath = String(process.env.INTERNAL_TEST_CHROMIUM_EXECUTABLE || '').trim();
    browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
      ignoreHTTPSErrors: true,
    });
    page = await context.newPage();
    page.on('pageerror', error => diagnostics.push(`Page error: ${error.message}`));
    page.on('console', message => {
      if (message.type() === 'error') diagnostics.push(`Console error: ${message.text()}`);
    });
    page.on('requestfailed', request => {
      try {
        if (new URL(request.url()).origin === baseUrl.origin) {
          diagnostics.push(`Request failed: ${request.method()} ${new URL(request.url()).pathname} (${request.failure()?.errorText || 'unknown'})`);
        }
      } catch (_) {}
    });

    const loginResponse = await page.goto(safeUrl(baseUrl, loginPath), { waitUntil: 'domcontentloaded', timeout: 45000 });
    if (!loginResponse || loginResponse.status() >= 400) {
      throw new Error(`TEST login page returned HTTP ${loginResponse ? loginResponse.status() : 'no response'}.`);
    }
    const loginText = await page.locator('body').innerText();
    if (!marker || !loginText.includes(marker)) {
      throw new Error(`Safety stop: the login page does not contain the required TEST marker “${marker}”. Credentials were not entered.`);
    }
    await screenshot('01-test-login');

    await page.locator('[name="username"]').fill(username);
    await page.locator('[name="password"]').fill(password);
    await page.getByRole('button', { name: 'Войти', exact: true }).click();
    await page.waitForURL(url => !url.pathname.includes(loginPath), { timeout: 30000 });

    const targetResponse = await page.goto(safeUrl(baseUrl, targetPath), { waitUntil: 'domcontentloaded', timeout: 45000 });
    if (!targetResponse || targetResponse.status() >= 400) {
      throw new Error(`Target page returned HTTP ${targetResponse ? targetResponse.status() : 'no response'}.`);
    }
    if (new URL(page.url()).pathname.includes(loginPath)) {
      throw new Error('The portal returned to the login page. Check the dedicated test account credentials and access rights.');
    }
    report.current_url = page.url();
    await screenshot('02-dashboard-desktop');

    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload({ waitUntil: 'domcontentloaded', timeout: 45000 });
    await screenshot('03-dashboard-mobile');

    if (diagnostics.length) {
      throw new Error(`The browser recorded ${diagnostics.length} internal page error(s). See diagnostics.json.`);
    }
    report.status = 'passed';
  } catch (error) {
    report.error = error instanceof Error ? error.message : String(error);
    try { await screenshot('99-failure'); } catch (_) {}
    throw error;
  } finally {
    report.finished_at = new Date().toISOString();
    fs.writeFileSync(path.join(resultsDir, 'diagnostics.json'), JSON.stringify(report, null, 2));
    const passed = report.status === 'passed';
    const lines = [
      `## Internal TEST portal check: ${passed ? 'пройдена' : 'ошибка'}`,
      '',
      `- Начало: ${report.started_at}`,
      `- Адрес: ${report.portal_origin}${targetPath}`,
      `- Скриншотов: ${report.screenshots.length}`,
      `- Ошибок браузера: ${report.diagnostics.length}`,
    ];
    if (report.error) lines.push(`- Что исправить: ${report.error}`);
    lines.push('', passed ? 'Вход и открытие тестового портала работают.' : 'Подробности и скриншот ошибки находятся в артефакте запуска.');
    fs.writeFileSync(path.join(resultsDir, 'summary.md'), `${lines.join('\n')}\n`);
    if (browser) await browser.close();
  }
}

main().catch(error => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
