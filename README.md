# TCP Printer

一个面向局域网的自助打印 Web 服务。用户通过手机或电脑浏览器上传文件、预览转换后的 PDF、选择打印选项并提交任务；服务端负责文件转换、排队和调用本机打印机。

> 本项目定位为局域网内的自助打印工具。请仅在受信任的网络中部署，并由管理员负责设备、文件和打印队列的管理。

## 功能

- 响应式网页，支持手机和桌面浏览器。
- 支持 PDF、JPG、PNG、DOC、DOCX、XLS、XLSX、PPT、PPTX 上传。
- 本地 PDF.js 预览，不依赖外部 CDN。
- 黑白/彩色、份数、页码范围和单面打印。
- SQLite 任务队列，支持取消等待任务、停止后续打印和管理员查看任务。
- 自动清理过期上传文件和已结束任务，避免 `storage/` 持续增长。
- 三种后端模式：`dry-run`、`cups` 和 `windows`。
- Windows 下可使用 Microsoft Word 转换 DOC/DOCX，以提高包含复杂公式的 Word 文档的保真度。

## 架构

```text
浏览器
  -> 上传文件
  -> 转换为 PDF
  -> PDF.js 预览
  -> SQLite 打印队列
  -> CUPS 或 Windows 打印队列
  -> 本地打印机
```

## 快速开始

需要 Python 3.10 或更高版本。

```powershell
git clone https://github.com/<your-account>/tcp_printer.git
cd tcp_printer
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

浏览器访问 `http://127.0.0.1:8080`。默认是 `dry-run` 模式：任务会模拟完成，不会调用真实打印机。

PowerShell 禁止执行虚拟环境激活脚本时，无需修改执行策略，直接使用 `.venv\Scripts\python.exe` 即可。

运行基础测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 配置

在项目根目录创建 `.env`。此文件已被 Git 忽略，不能提交密码、令牌或实际网络地址。

最小配置示例：

```ini
TCP_PRINTER_MODE=dry-run
TCP_PRINTER_HOST=0.0.0.0
TCP_PRINTER_PORT=8080
```

常用配置：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `TCP_PRINTER_MODE` | `dry-run` | `dry-run`、`cups` 或 `windows` |
| `TCP_PRINTER_QUEUE` | `CP1025` | 系统中显示的打印机队列名称 |
| `TCP_PRINTER_HOST` | `0.0.0.0` | Web 服务监听地址 |
| `TCP_PRINTER_PORT` | `8080` | Web 服务端口 |
| `TCP_PRINTER_OFFICE_CONVERTER` | `auto` | `auto`、`word` 或 `libreoffice` |
| `TCP_PRINTER_MAX_UPLOAD_MB` | `200` | 单个上传文件的最大大小（MB） |
| `TCP_PRINTER_RETENTION_HOURS` | `24` | 已结束任务及其文件的保留时间（小时） |
| `TCP_PRINTER_ADMIN_TOKEN` | 空 | 管理页面访问令牌；为空时禁用管理页面 |

## Windows 部署

Windows 模式适合需要准确转换复杂 DOC/DOCX 公式的场景。

### 前提条件

1. 安装并确认 Windows 能正常打印测试页的打印机驱动。
2. 安装 Microsoft Word 桌面版。首次部署前，以运行服务的 Windows 用户手动启动一次 Word，完成许可证或首次启动提示。
3. 安装 Python 依赖：`pywin32` 和 `PyMuPDF` 已在 `requirements.txt` 中列出。

示例 `.env`：

```ini
TCP_PRINTER_MODE=windows
TCP_PRINTER_QUEUE=你的 Windows 打印机名称
TCP_PRINTER_OFFICE_CONVERTER=auto
TCP_PRINTER_WINDOWS_PRINT_DPI=300
TCP_PRINTER_WINDOWS_WAIT_SECONDS=900
TCP_PRINTER_WINDOWS_STALL_SECONDS=60
TCP_PRINTER_HOST=0.0.0.0
TCP_PRINTER_PORT=8080
```

在 Windows 中，`TCP_PRINTER_OFFICE_CONVERTER=auto` 会让 DOC/DOCX 优先交给 Microsoft Word 导出 PDF；Excel 和 PowerPoint 等其他 Office 文件仍需要 LibreOffice。若只允许 Word 转换 DOC/DOCX，可设置为 `word`。

Word 自动化需要可交互的用户会话。不要使用 `LocalSystem` 或没有桌面会话的 Windows 服务账户运行它。项目提供了一个“用户登录时运行”的计划任务脚本：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\deploy\register-windows-task.ps1
Start-ScheduledTask -TaskName "TCP Printer"
```

删除该计划任务：

```powershell
.\deploy\register-windows-task.ps1 -Remove
```

Windows 打印队列可尝试报告缺纸、离线、卡纸、需要人工处理等状态，但具体信息取决于打印机驱动。任务从 Windows 队列中消失不严格等同于最后一页已经出纸。

## Ubuntu / CUPS 部署

Ubuntu 模式适合使用 CUPS 管理本地 USB 或网络打印机的环境。

安装运行依赖：

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip libreoffice-core libreoffice-writer \
  libreoffice-calc libreoffice-impress cups cups-client cups-filters

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

确认 CUPS 已添加打印机并记录实际队列名称：

```bash
lpstat -p
lpoptions -p <queue-name> -l
```

示例 `.env`：

```ini
TCP_PRINTER_MODE=cups
TCP_PRINTER_QUEUE=your-cups-queue
TCP_PRINTER_OFFICE_CONVERTER=libreoffice
TCP_PRINTER_HOST=0.0.0.0
TCP_PRINTER_PORT=8080
```

若打印机 PPD 使用 `ColorModel` 而非默认的 `print-color-mode`，还需要设置：

```ini
TCP_PRINTER_COLOR_OPTION=ColorModel
TCP_PRINTER_COLOR_MONO=Gray
TCP_PRINTER_COLOR_COLOR=RGB
```

LibreOffice 无法可靠保持某些旧版 OLE 公式对象（例如 `Microsoft Equation 3.0`）的版式。涉及复杂公式的 Word 文档建议由 Microsoft Word 导出 PDF 后上传，或使用 Windows + Word 模式。

项目附带 systemd 服务示例 `deploy/tcp-printer.service`。部署为系统服务前，请确认服务账户拥有项目的 `data/` 和 `storage/` 写入权限，以及访问 CUPS 队列的权限。

## 局域网访问

服务监听 `0.0.0.0` 后，其他设备通过服务器当前局域网 IP 访问：

```text
http://<server-ip>:8080
```

`127.0.0.1` 只代表访问者自己的设备，不能供手机访问。手机和服务器通常需要处在可互相访问的网络中；还应检查防火墙、访客网络和 Wi-Fi 客户端隔离设置。长期部署建议在路由器中按服务器网卡 MAC 地址配置 DHCP 地址保留。

## 管理页面

设置 `TCP_PRINTER_ADMIN_TOKEN` 并重启服务后，可访问 `/admin`。管理页面用于查看打印机状态、磁盘空间和最近任务，并执行取消、停止或清理操作。

请使用足够长的随机令牌，例如：

```bash
openssl rand -hex 32
```

## 数据与安全

- 上传源文件、转换后的 PDF 和 SQLite 数据库保存在 `storage/` 与 `data/`，默认不应提交到 Git。
- 自动清理只删除已结束任务；正在转换、排队或打印的任务不会被自动删除。
- 该项目没有用户账户、文件加密或面向公网的访问控制。不要直接暴露到互联网。
- 若必须跨网络访问，请在受控的 VPN、反向代理和 HTTPS 环境中部署，并自行补充认证、限流和审计。
- 不要将 `.env`、打印队列凭据、私有文档、许可证文件或商业字体提交到公开仓库。

## 已知限制

- CP1025 等不带自动双面器的设备不支持真正的自动双面打印。
- 打印驱动不一定能提供精确的物理出纸进度或故障原因；网页状态应作为辅助信息，而不是设备面板的替代。
- 在微信内打开网页时，选择文件和 PDF 预览的行为受微信 WebView 限制。复杂文件通常应先保存到系统“文件”应用，或在系统浏览器中打开页面。
- 本项目不是 AirPrint 服务。iPhone/iPad 可通过 Safari 使用网页，但不会出现在系统原生“打印”菜单中。

## 项目结构

```text
app/                 FastAPI 应用、任务队列、转换与打印后端
app/static/          前端资源和本地 PDF.js
app/templates/       页面模板
data/                SQLite 运行数据（Git 忽略）
storage/             上传文件与转换结果（Git 忽略）
deploy/              Windows 和 systemd 部署脚本
tests/               基础单元测试
```

