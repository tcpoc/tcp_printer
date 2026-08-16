# TCP Printer

局域网自助打印服务。用户通过浏览器上传文件、预览 PDF、选择黑白/彩色、份数和页码范围后加入 CUPS 打印队列。

## 当前功能

- 手机和电脑响应式页面。
- PDF、JPG、PNG 直接转换或预览；Office 文件通过 LibreOffice headless 转 PDF。
- 黑白/彩色、份数、页码范围、单面打印摘要。
- SQLite 任务队列、当前浏览器会话任务、取消任务和停止后续打印。
- `dry-run` 模式用于当前电脑开发；`cups` 模式用于 Ubuntu Mini PC。

## 当前电脑运行

先进入本项目目录，再执行以下命令。命令直接调用虚拟环境里的 Python，不需要激活脚本，因此不会受 PowerShell 执行策略影响：

```powershell
cd C:\Users\lenovo\Desktop\培训\tcp_printer
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

浏览器访问 `http://127.0.0.1:8080`。默认模式是 `dry-run`，会模拟完成打印任务，不会调用系统打印机。

如需修改端口、测试模式或上传文件大小，请把 `.env.example` 复制为 `.env` 后修改。程序会在启动时自动读取 `.env`；该文件包含本机配置，不应复制到公共仓库。

服务默认只保留 24 小时的上传文件和非活动任务。清理线程每小时运行一次，正在排队或打印中的任务不会被删除；可以在 `.env` 中通过 `TCP_PRINTER_RETENTION_HOURS` 调整保留时间。

### 管理员运维页

在 `.env` 设置一个足够随机的令牌：

```ini
TCP_PRINTER_ADMIN_TOKEN=替换为随机长字符串
```

重启服务后访问 `http://MiniPC地址:8080/admin`，输入令牌即可查看打印机状态、磁盘空间和全局任务，并执行取消、停止和立即清理。令牌只保存在当前浏览器会话中；未设置令牌时管理页不可访问。

## Ubuntu Mini PC 部署

### 1. 安装依赖

先进入你已经解压好的 `tcp_printer` 目录。以下以 `/opt/tcp_printer` 为例，实际目录不同则替换路径。

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip libreoffice-core libreoffice-writer \
  libreoffice-calc libreoffice-impress cups cups-client cups-filters \
  printer-driver-foo2zjs

cd /opt/tcp_printer
rm -rf .venv  # 若压缩包来自 Windows，必须删除其中的 Windows 虚拟环境
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

### 2. 先验证 CUPS 与 CP1025

确认队列名和驱动选项：

```bash
lpinfo -v
lpinfo -m | grep -i CP1025
lpstat -p
lpoptions -p CP1025 -l
```

如果 `lpstat -p` 没有显示 CP1025，请先在 CUPS 中添加 USB 打印机和正确驱动，再继续。CP1025 没有自动双面器，网页固定为单面打印。颜色选项应以 `lpoptions` 输出为准；若 PPD 使用 `ColorModel` 而非 `print-color-mode`，在下一步的 `.env` 中填写：

```ini
TCP_PRINTER_COLOR_OPTION=ColorModel
TCP_PRINTER_COLOR_MONO=Gray
TCP_PRINTER_COLOR_COLOR=RGB
```

### 3. 启用真实 CUPS 打印

```bash
cd /opt/tcp_printer
sudo useradd --system --create-home --shell /usr/sbin/nologin printsvc 2>/dev/null || true
sudo chown -R printsvc:printsvc /opt/tcp_printer
sudo -u printsvc cp .env.example .env
sudo -u printsvc nano .env
```

`.env` 至少应包含：

```ini
TCP_PRINTER_MODE=cups
TCP_PRINTER_QUEUE=CP1025
TCP_PRINTER_HOST=0.0.0.0
TCP_PRINTER_PORT=8080
TCP_PRINTER_ADMIN_TOKEN=替换为随机长字符串
```

可用 `openssl rand -hex 32` 生成管理员令牌。保存后进行前台测试：

```bash
sudo -u printsvc /opt/tcp_printer/.venv/bin/python /opt/tcp_printer/run.py
```

手机或电脑访问 `http://MiniPC的IP地址:8080`；用 `hostname -I` 查看 IP。先测试 PDF 黑白、彩色、份数、页码范围、Word/Excel/PPT 转换及取消任务。确认稳定后按 `Ctrl+C` 停止前台测试，再配置 systemd。

### 4. systemd 服务

项目已提供 `deploy/tcp-printer.service`。该服务强制读取 `/opt/tcp_printer/.env`，因此 `.env` 不存在时不会启动；以下内容供核对：

```ini
[Unit]
Description=TCP Printer self-service web app
After=network-online.target cups.service
Wants=network-online.target

[Service]
User=printsvc
Group=printsvc
WorkingDirectory=/opt/tcp_printer
EnvironmentFile=/opt/tcp_printer/.env
ExecStart=/opt/tcp_printer/.venv/bin/python run.py
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
ReadWritePaths=/opt/tcp_printer/data /opt/tcp_printer/storage
UMask=0077

[Install]
WantedBy=multi-user.target
```

启用服务：

```bash
sudo cp deploy/tcp-printer.service /etc/systemd/system/tcp-printer.service
sudo systemctl daemon-reload
sudo systemctl enable --now tcp-printer
sudo systemctl status tcp-printer
```

查看运行日志：

```bash
sudo journalctl -u tcp-printer -f
```

仅开放 Web 端口给局域网，CUPS 管理端口不要对普通用户开放。
systemd 服务使用无 sudo 权限的 `printsvc` 用户，并限制了可写目录；不要把该用户加入管理员组。

## 修改文字、颜色和图标

### 文字

- 固定页面文字：`app/templates/index.html`。
- 前端交互文字：`app/static/app.js` 顶部的 `TEXT` 常量，以及文件内的状态、按钮和提示文案。
- 后端返回的错误文字：`app/jobs.py` 与 `app/main.py`。

改完后刷新浏览器即可；若运行的是 systemd 服务，后端 Python 文件改动后执行：

```bash
sudo systemctl restart tcp-printer
```

### 颜色和圆角

编辑 `app/static/styles.css` 顶部的 CSS 变量：

```css
:root {
  --primary: #0d9488;
  --danger: #ba1a1a;
  --warning: #b7791f;
}
```

### 图标

当前页面使用文字和熟悉的符号按钮，避免依赖外网图标库。要替换图标时，在 `app/templates/index.html` 中定位按钮或标题文字；建议使用本地 SVG 文件放入 `app/static/icons/`，再用：

```html
<img src="/static/icons/print.svg" alt="打印">
```

不要使用远程 CDN 图标或字体，否则局域网无外网时页面可能缺少资源。

### 页头队标

页头使用 `app/static/brand/tcp-logo.jpg`。替换队标时，使用同名图片覆盖该文件即可；推荐使用已裁去大面积空白边缘的横向 JPG 或 PNG。图片的显示高度在 `app/static/styles.css` 的 `.brand-logo` 中控制。

## 目录说明

```text
tcp_printer/
  app/
    main.py       Web API 与路由
    jobs.py       文件转换、SQLite 队列、CUPS 调用
    config.py     环境配置
    templates/    网页结构
    static/       CSS 与浏览器脚本
  data/           SQLite 数据库和日志运行时目录（不随代码迁移）
  storage/        上传文件与转换后的 PDF 运行时目录（不随代码迁移）
  deploy/         Ubuntu systemd 服务文件
  tests/          不依赖打印机的基础测试
  requirements.txt
  run.py
```

## 已知边界

- `dry-run` 只用于开发演示，不代表真实出纸完成。
- CUPS 作业提交成功不等于设备已完成所有页面；CP1025 的精确页进度不可可靠获取。
- Office 转换依赖 Mini PC 中安装的 LibreOffice 与中文字体。
