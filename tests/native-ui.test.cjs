const { chromium } = require('playwright');
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const HOST = 'http://127.0.0.1:8198';
const shots = path.join(__dirname, 'screenshots'); fs.mkdirSync(shots, { recursive: true });
async function history(id) {
  for (let i = 0; i < 100; i++) {
    const data = await (await fetch(HOST + '/history/' + id)).json();
    if (data[id]) return data[id];
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw Error('Native prompt did not finish');
}
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.VC_BROWSER_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe' });
  const page = await browser.newPage({ viewport: { width: 1450, height: 1250 } }), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.addInitScript(() => {
      const OriginalError = Error; globalThis.canvasErrors = [];
      globalThis.Error = new Proxy(OriginalError, { apply(target, that, args) { const value = Reflect.apply(target, that, args); if (String(args[0]).includes('getCanvas: canvas is null')) canvasErrors.push(value.stack); return value; }, construct(target, args, newTarget) { const value = Reflect.construct(target, args, newTarget); if (String(args[0]).includes('getCanvas: canvas is null')) canvasErrors.push(value.stack); return value; } });
      navigator.mediaDevices.getDisplayMedia = async () => {
        const source = document.createElement('canvas'); source.width = 320; source.height = 180;
        const context = source.getContext('2d'); let count = 0;
        const draw = () => { context.fillStyle = `rgb(${30 + ++count % 160},40,210)`; context.fillRect(0, 0, 320, 180); context.fillStyle = '#f6f0c0'; context.fillRect(120, 50, 60, 60); };
        draw(); const timer = setInterval(draw, 50), stream = source.captureStream(20);
        globalThis.syntheticStream = stream; globalThis.syntheticTimer = timer;
        return stream;
      };
    });
    await page.goto(HOST);
    await page.waitForFunction(() => { const app = globalThis.comfyAPI?.app?.app; return app?.isGraphReady && app?.vueAppReady && app?.canvas?.canvas && globalThis.LiteGraph?.registered_node_types?.DesktopScreenCapture && globalThis.DesktopBridgeUI; }, {}, { timeout: 60000 });
    await page.waitForTimeout(1500);
    const mounted = await page.evaluate(async () => {
      const { app } = await import('/scripts/app.js');
      app.graph.clear();
      const capture = LiteGraph.createNode('DesktopScreenCapture'), save = LiteGraph.createNode('SaveImage');
      if (!capture || !save) throw Error('Native node registration failed');
      capture.pos = [100, 80]; save.pos = [850, 100]; app.graph.add(capture); app.graph.add(save); capture.connect(0, save, 0);
      globalThis.screenTestNode = capture;
      capture.widgets.find(w => w.name === 'enabled').value = false;
      capture.widgets.find(w => w.name === 'resize_width').value = 128;
      capture.widgets.find(w => w.name === 'resize_height').value = 96;
      app.canvas.ds.scale = .8; app.canvas.centerOnNode(capture); app.canvas.setDirty(true, true);
      return { panel: !!capture.screenCapturePanel, names: capture.widgets.map(w => w.name) };
    });
    assert.ok(mounted.panel); assert.ok(mounted.names.includes('bridge_auth'));
    const queue = () => page.evaluate(async () => { const { app } = await import('/scripts/app.js'), { api } = await import('/scripts/api.js'); const prompt = await app.graphToPrompt(); if (Object.values(prompt.output).some(n => !['DesktopScreenCapture', 'SaveImage'].includes(n.class_type))) throw Error('Unexpected native graph; refusing model execution'); return api.queuePrompt(0, prompt); });
    let queued = await queue(), completed = await history(queued.prompt_id);
    assert.equal(completed.status.status_str, 'success', JSON.stringify(completed.status));
    assert.ok(Object.values(completed.outputs).some(output => output.images?.length === 1));
    // Native DOM widget, actual bridge browser transport, execution-time request, native SaveImage.
    await page.locator('.sc-panel').getByRole('button', { name: '浏览器共享 / 停止', exact: true }).click();
    await page.waitForFunction(() => screenTestNode.widgets.find(w => w.name === 'device').value && screenTestNode.widgets.find(w => w.name === 'source').value === 'browser');
    const browserDeviceId = await page.evaluate(() => screenTestNode.widgets.find(w => w.name === 'device').value);
    await page.evaluate(() => {
      for (const [name, value] of Object.entries({ frames: 3, interval: .2, fps: 10, resize_width: 160, resize_height: 90, on_error: 'error' })) screenTestNode.widgets.find(w => w.name === name).value = value;
    });
    await page.screenshot({ path: path.join(shots, 'native-screen-capture.png') });
    queued = await queue(); completed = await history(queued.prompt_id);
    assert.equal(completed.status.status_str, 'success', JSON.stringify(completed.status));
    const output = Object.values(completed.outputs).find(value => value.images?.length === 3); assert.ok(output, 'VHS-compatible IMAGE batch creates three saved images');
    const colors = await page.evaluate(async descriptors => {
      const canvas = document.createElement('canvas'); canvas.width = 1; canvas.height = 1; const context = canvas.getContext('2d');
      const result = [];
      for (const descriptor of descriptors) { const image = new Image(); image.src = '/view?' + new URLSearchParams(descriptor); await image.decode(); context.drawImage(image, 0, 0); result.push([...context.getImageData(0, 0, 1, 1).data]); }
      return result;
    }, output.images);
    assert.equal(new Set(colors.map(color => color[0])).size, 3, 'each requested frame is fresh');
    assert.equal(colors[0][1], 40); assert.equal(colors[0][2], 210);
    // Vertical resizing must allocate the added height to the actual preview.
    const metrics = () => page.evaluate(async () => { const { app } = await import('/scripts/app.js'); const root = screenTestNode.screenCapturePanel.root; return { size: [...screenTestNode.size], canvas: root.querySelector('canvas').clientHeight, root: root.clientHeight, controls: root.querySelector('.sc-toolbar').clientHeight }; });
    await page.waitForTimeout(300); const before = await metrics();
    await page.evaluate(() => screenTestNode.setSize([screenTestNode.size[0], screenTestNode.size[1] + 360]));
    await page.waitForTimeout(300); const expanded = await metrics();
    assert.ok(Math.abs(expanded.canvas - before.canvas - 360) < 5, JSON.stringify({ before, expanded }));
    assert.equal(expanded.controls, before.controls);
    await page.waitForTimeout(300); assert.deepEqual(await metrics(), expanded, 'resizing settles without a layout feedback loop');
    await page.evaluate(async () => { const { app } = await import('/scripts/app.js'); app.canvas.ds.scale = .6; app.canvas.centerOnNode(screenTestNode); app.canvas.setDirty(true, true); });
    await page.waitForTimeout(200);
    const cropValues = () => page.evaluate(() => Object.fromEntries(screenTestNode.widgets.filter(w => ['x', 'y', 'width', 'height'].includes(w.name)).map(w => [w.name, w.value])));
    const cropDrag = async (selector, start, end) => {
      const b = await page.locator(selector).boundingBox(), scale = Math.min(b.width / 320, b.height / 180), left = b.x + (b.width - 320 * scale) / 2, top = b.y + (b.height - 180 * scale) / 2;
      await page.mouse.move(left + start[0] * scale, top + start[1] * scale); await page.mouse.down(); await page.mouse.move(left + end[0] * scale, top + end[1] * scale, { steps: 8 }); await page.mouse.up();
    };
    // Double-click itself preserves the crop; both dismissal paths retain edits.
    const originalCrop = await cropValues();
    await page.locator('.sc-canvas').dblclick(); await page.locator('.sc-crop-dialog').waitFor();
    assert.deepEqual(await cropValues(), originalCrop);
    const modalSize = await page.locator('.sc-crop-dialog').boundingBox(); assert.ok(modalSize.width > 1300 && modalSize.height > 1100);
    await cropDrag('.sc-crop-canvas', [40, 30], [200, 120]); const modalCrop = await cropValues();
    assert.ok(Math.abs(modalCrop.x - 40) <= 1 && Math.abs(modalCrop.y - 30) <= 1 && Math.abs(modalCrop.width - 161) <= 1 && Math.abs(modalCrop.height - 91) <= 1, JSON.stringify(modalCrop));
    await page.screenshot({ path: path.join(shots, 'native-fullscreen-crop.png') });
    await page.getByRole('button', { name: '关闭裁剪预览', exact: true }).click(); await page.locator('.sc-crop-dialog').waitFor({ state: 'detached' });
    assert.deepEqual(await cropValues(), modalCrop);
    await page.locator('.sc-canvas').dblclick(); await page.locator('.sc-crop-dialog').waitFor();
    assert.deepEqual(await cropValues(), modalCrop);
    await cropDrag('.sc-crop-canvas', [20, 15], [170, 100]); const outsideCrop = await cropValues();
    await page.mouse.click(5, 5); await page.locator('.sc-crop-dialog').waitFor({ state: 'detached' });
    assert.deepEqual(await cropValues(), outsideCrop);
    await page.locator('.sc-canvas').dblclick(); await page.locator('.sc-crop-dialog').waitFor(); await page.keyboard.press('Escape'); await page.locator('.sc-crop-dialog').waitFor({ state: 'detached' });
    assert.deepEqual(await cropValues(), outsideCrop);
    const serializedCrop = await page.evaluate(async () => { const { app } = await import('/scripts/app.js'); return (await app.graphToPrompt()).output[String(screenTestNode.id)].inputs; });
    for (const name of ['x', 'y', 'width', 'height']) assert.equal(serializedCrop[name], outsideCrop[name]);
    // Crop mouse interaction maps through contain letterboxing to original source pixels.
    await cropDrag('.sc-canvas', [30, 20], [220, 135]);
    const crop = await page.evaluate(() => Object.fromEntries(screenTestNode.widgets.filter(w => ['x', 'y', 'width', 'height'].includes(w.name)).map(w => [w.name, w.value])));
    assert.ok(crop.width > 30 && crop.width < 320); assert.ok(crop.height > 10 && crop.height < 180);
    await page.evaluate(() => { screenTestNode.widgets.find(w => w.name === 'frames').value = 20; screenTestNode.widgets.find(w => w.name === 'interval').value = 1; screenTestNode.widgets.find(w => w.name === 'on_error').value = 'blank'; });
    queued = await queue(); await new Promise(resolve => setTimeout(resolve, 500));
    const interruptStart = performance.now();
    await fetch(HOST + '/interrupt', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const interrupted = await history(queued.prompt_id);
    assert.ok(interrupted.status.messages.some(message => message[0] === 'execution_interrupted'), 'blank error policy must preserve native cancellation');
    assert.ok(performance.now() - interruptStart < 5000, 'long capture batch cancels promptly');
    await page.locator('.sc-panel').getByRole('button', { name: '浏览器共享 / 停止', exact: true }).click();
    await page.waitForFunction(() => !screenTestNode.widgets.find(w => w.name === 'device').value);
    await page.waitForFunction(async () => !(await (await fetch('/desktop_bridge/devices')).json()).devices.some(device => device.browser));
    const devices = await (await fetch(HOST + '/desktop_bridge/devices')).json(); assert.equal(devices.devices.filter(d => d.id === browserDeviceId).length, 0);
    // A Python-mode output is already cropped; reopening must load the full source.
    const previewPng = await page.evaluate(() => { const canvas = document.createElement('canvas'); canvas.width = 320; canvas.height = 180; const context = canvas.getContext('2d'); context.fillStyle = '#345678'; context.fillRect(0, 0, 320, 180); return canvas.toDataURL('image/png').split(',')[1]; });
    let sourceReads = 0;
    await page.route('**/screen_capture/preview', route => { sourceReads++; return route.fulfill({ json: { ok: true, image: previewPng, width: 320, height: 180 } }); });
    await page.evaluate(descriptor => { screenTestNode.widgets.find(w => w.name === 'source').value = 'server'; screenTestNode.screenCapturePanel.show({ screen_preview: [descriptor] }); }, output.images[0]);
    await page.waitForFunction(() => document.querySelector('.sc-hint').textContent.includes('当前为本次输出画面'));
    const preserved = await cropValues();
    await page.locator('.sc-canvas').dblclick(); await page.waitForFunction(() => document.querySelector('.sc-crop-info')?.textContent.startsWith('320 × 180'));
    assert.equal(sourceReads, 1); assert.deepEqual(await cropValues(), preserved);
    await page.getByRole('button', { name: '关闭裁剪预览', exact: true }).click(); await page.locator('.sc-crop-dialog').waitFor({ state: 'detached' });
    const launcher = page.getByRole('button', { name: '电脑桥接', exact: true }), launcherBefore = await launcher.boundingBox();
    await page.mouse.move(launcherBefore.x + 25, launcherBefore.y + 10); await page.mouse.down(); await page.mouse.move(launcherBefore.x + 245, launcherBefore.y - 130, { steps: 10 }); await page.mouse.up();
    assert.equal(await page.locator('.desktop-bridge-dialog').count(), 0, 'dragging must not open the connection dialog');
    const launcherAfter = await launcher.boundingBox(); assert.ok(launcherAfter.x > launcherBefore.x + 200 && launcherAfter.y < launcherBefore.y - 100);
    const savedPosition = await page.evaluate(() => JSON.parse(localStorage.getItem('desktop-bridge-launcher-position')));
    assert.ok(Math.abs(savedPosition.x - launcherAfter.x) < 2 && Math.abs(savedPosition.y - launcherAfter.y) < 2);
    const persisted = await page.evaluate(async () => { const { movableLauncher } = await import('/extensions/ComfyUI-DesktopBridge/launcher.js'); const copy = document.createElement('button'); copy.textContent = 'position test'; copy.style.cssText = 'position:fixed;left:0;top:0'; document.body.append(copy); const dispose = movableLauncher(copy); const rect = copy.getBoundingClientRect(); dispose(); copy.remove(); return { x: rect.x, y: rect.y }; });
    assert.ok(Math.abs(persisted.x - savedPosition.x) < 2 && Math.abs(persisted.y - savedPosition.y) < 2);
    await page.getByRole('button', { name: '电脑桥接', exact: true }).click();
    await page.getByRole('button', { name: '生成配对码', exact: true }).click();
    await page.waitForFunction(() => /配对码：[A-F0-9]{12}/.test(document.querySelector('.desktop-bridge-dialog')?.textContent || ''));
    assert.match(await page.locator('.desktop-bridge-dialog').innerText(), /配对码：[A-F0-9]{12}/);
    await page.screenshot({ path: path.join(shots, 'native-pairing.png') });
    await page.locator('.desktop-bridge-dialog').getByRole('button', { name: '关闭', exact: true }).click();
    await page.setViewportSize({ width: 600, height: 500 }); const clamped = await launcher.boundingBox();
    assert.ok(clamped.x >= 0 && clamped.y >= 0 && clamped.x + clamped.width <= 601 && clamped.y + clamped.height <= 501);
    assert.deepEqual(errors, []);
    const canvasErrors = await page.evaluate(() => globalThis.canvasErrors); assert.deepEqual(canvasErrors, []);
    fs.writeFileSync(path.join(__dirname, 'native-report.json'), JSON.stringify({ native: 'ComfyUI 0.39.0 / frontend 1.53.10 / CPU', tests: ['registration', 'disabled blank native queue', 'authorized browser relay', 'fresh batch native queue', 'RGB pixels', 'vertical preview growth and stable layout', 'fullscreen crop and close/outside/Escape retention', 'full source reload from cropped output', 'crop prompt serialization', 'movable persistent launcher with viewport clamping', 'native cancellation with blank error policy', 'stop/revoke sharing', 'pairing dialog'], colors, crop, resize: { before, expanded }, modalCrop, outsideCrop }, null, 2));
    console.log('NATIVE PASS: preview resizing, fullscreen crop, close/outside retention, prompt serialization, movable persistent launcher; original queue/RGB/cancellation/pairing checks pass.');
  } catch (error) { await page.screenshot({ path: path.join(shots, 'native-failure.png') }); console.log(await page.locator('body').innerText()); throw error; }
  finally { await page.evaluate(() => { clearInterval(globalThis.syntheticTimer); globalThis.syntheticStream?.getTracks().forEach(track => track.stop()); }).catch(() => {}); await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
