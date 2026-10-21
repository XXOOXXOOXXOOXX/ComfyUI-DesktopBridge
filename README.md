# ComfyUI 电脑桥接 1.0.1

让局域网或外网同事的本机 Eagle、屏幕、应用窗口连接到共享的 ComfyUI。客户端主动向 ComfyUI 轮询，不监听同事电脑端口，也不执行远程命令。

左下角“电脑桥接”按钮可以按住拖动到页面任意位置，松开后记住位置；再次打开页面恢复。普通点击仍打开连接管理窗口，拖动不会误触打开。浏览器缩小时自动将按钮限制在可见范围内。

## 安装位置

服务器：把 `ComfyUI-DesktopBridge`、更新的 `ComfyUI-EagleAssets`、`ComfyUI-ScreenCapture` 放入 ComfyUI 的 `custom_nodes`，重启，再 Ctrl+F5 刷新浏览器。只需要 Eagle 时可不安装 ScreenCapture。

同事电脑：解压 `DesktopBridge-Client.zip` 到任意目录，需要 Python 3.10+（Windows 标准安装包含 tkinter）。先双击 `安装依赖.cmd`，再双击 `启动桥接.cmd`。程序保持打开才可连接；关闭后停止服务。它不是旧版的 EagleAssets-RenameBridge，两者用途不同。

## 配对

1. 同事先在 ComfyUI 多用户页面选择自己的用户。
2. 点击页面左下角“电脑桥接”，或 Eagle 节点里的“连接我的电脑”，生成配对码。
3. 在同事电脑的桥接程序填入 ComfyUI 地址、配对码、电脑名称，按需勾选 Eagle 或屏幕捕获权限，点击连接。
4. 返回 ComfyUI，刷新电脑列表并选择。Eagle 节点地址显示为 `bridge://设备ID`，点击读取目录，再加载素材或选择导出目录。

设备按 ComfyUI 用户归属保存，另一个用户无法通过本插件选择、调用或撤销它；旧工作流中其他人的设备 ID 不会自动取得授权。配对码一次使用，5 分钟有效。客户端断线自动重连；再次启动可留空配对码恢复已有配对。改变授权范围请撤销原设备后重新配对。设备密码保存在客户端当前用户目录 `.comfyui-desktop-bridge.json`，服务器存放在其 user/desktop_bridge/devices.json，请勿分享这些文件。

## 局域网和外网

局域网地址示例 `http://192.168.1.10:8188`。不同局域网时，使用你已配置、同事可以访问的 **HTTPS ComfyUI 地址**（反向代理或受保护的隧道）；本插件不提供公网域名、穿透或 VPN。同事电脑不用开放 Eagle 或截图端口。

代理应将 `/desktop_bridge/agent/*` 转发到本插件，由配对码/设备密码验证这些接口；不要把客户端接口重定向到网页登录页。网页端的配对和管理接口仍须经过你的 ComfyUI 登录入口。若 ComfyUI 部署在子路径，客户端地址需包含该子路径。

ComfyUI `--multi-user` 是用户配置隔离，不是登录鉴权。公网服务需要在入口配置登录和 HTTPS，并正确隔离用户身份；本插件沿用 ComfyUI 的用户身份，不会替代服务器认证。若入口本身允许冒用 `comfy-user`，也不能把用户配置当成安全边界。配对码、设备密码不应经不可信的公网 HTTP 传输。

## Eagle 工作方式和边界

素材在同事电脑由本机 Eagle API 获取，再按需传到服务器私有缓存，模型所需的同目录资源一起传输；输出文件先传回同事电脑，再调用本机 Eagle 导入，所以不要求共享盘。原有主机 Eagle / HTTP 直连方式保留。

单素材/单批导出最多 128 MB，单素材包最多 512 文件；大视频请拆分。远程导出是 Eagle 异步导入，预览关联可能需要等几秒后重新打开。客户端中转导出保留至少 24 小时，下次启动清理旧中转文件；服务器缓存保留在 user/desktop_bridge/cache，可停机后手动清理。远程改名使用 Eagle 的 item/update；旧 Eagle 不接受改名时请在 Eagle 内改名。此版不自动调用旧改名桥。

## 验证

`tests/test_integration.py` 使用独立模拟 Eagle 和真实 HTTP 往返，验证素材/透明通道传输、输出回存、用户隔离、队列身份覆盖、截图批次/色彩/MASK/尺寸和错误策略。`tests/start_native.py` 用本地安装的 ComfyUI 启动隔离 CPU 测试实例，测试目录在工作区，不改已安装的 ComfyUI。
