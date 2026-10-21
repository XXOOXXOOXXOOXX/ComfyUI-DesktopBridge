import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { movableLauncher } from "./launcher.js";

async function request(path, data) {
    const response = await api.fetchApi("/desktop_bridge/" + path, data === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
    if (!response.ok) throw Error("电脑桥接请求失败，请检查用户及插件是否已启用。");
    return response.json();
}
const button = (text, action) => { const node = document.createElement("button"); node.textContent = text; node.type = "button"; node.onclick = action; return node; };
async function open({ capability, select } = {}) {
    const modal = document.createElement("dialog"); modal.className = "desktop-bridge-dialog";
    Object.assign(modal.style, { color: "#e6ebf0", background: "#232b34", border: "1px solid #617387", borderRadius: "12px", width: "min(680px,90vw)", padding: "20px", font: "14px/1.6 system-ui" });
    const title = document.createElement("strong"); title.textContent = "连接我的电脑";
    const status = document.createElement("p"), list = document.createElement("div");
    const help = document.createElement("p"); help.textContent = "在素材或屏幕所在电脑运行 DesktopBridge-Client，将这里的 ComfyUI 地址和配对码填入。配对按当前 ComfyUI 用户保存。外网使用受保护的 HTTPS 地址。";
    const address = document.createElement("code"); address.textContent = location.origin + location.pathname.replace(/\/$/, "");
    const row = document.createElement("div"); row.style.cssText = "display:flex;gap:10px;flex-wrap:wrap;margin:12px 0";
    const report = async action => { try { await action(); } catch (error) { status.textContent = error.message; } };
    const refresh = async () => {
        const data = await request("devices"); list.replaceChildren();
        for (const device of data.devices.filter(d => !capability || d.capabilities.includes(capability))) {
            const line = document.createElement("div"); line.style.cssText = "display:flex;align-items:center;gap:10px;padding:8px;border-bottom:1px solid #465464";
            const name = document.createElement("span"); name.style.flex = "1"; name.textContent = `${device.name} · ${device.online ? "在线" : "离线"}`;
            line.append(name);
            if (select) { const choose = button("选择", () => { select(device); modal.close(); }); choose.disabled = !device.online; line.append(choose); }
            line.append(button("撤销授权", () => report(async () => { await request("forget", { id: device.id }); await refresh(); })));
            list.append(line);
        }
        if (!list.children.length) list.textContent = "暂无匹配的电脑，请生成配对码。";
    };
    row.append(button("生成配对码", () => report(async () => { const data = await request("pair", {}); status.textContent = `配对码：${data.code}（5 分钟有效）`; })), button("刷新电脑列表", () => report(refresh)), button("关闭", () => modal.close()));
    modal.append(title, help, address, row, status, list); document.body.append(modal);
    modal.addEventListener("click", event => { if (event.target === modal) { const r = modal.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) modal.close(); } });
    modal.addEventListener("close", () => modal.remove(), { once: true }); modal.showModal(); await report(refresh);
}
globalThis.DesktopBridgeUI = { open, request };
app.registerExtension({ name: "DesktopBridge.Connection", setup() {
    const launcher = button("电脑桥接", () => open()); launcher.title = "拖动可移动；点击管理本机 Eagle 与跨电脑屏幕捕获连接";
    launcher.style.cssText = "position:fixed;bottom:10px;left:12px;z-index:1100;background:#263e4b;color:#c4eee3;border:1px solid #547c85;border-radius:7px;padding:5px 10px;font:12px system-ui;cursor:pointer";
    document.body.append(launcher);
    movableLauncher(launcher);
} });
