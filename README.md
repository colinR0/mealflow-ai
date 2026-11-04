# 智慧冰箱 · 旗舰产品配套 App

本项目是某国产冰箱品牌高端旗舰冰箱的配套 App 及服务端。目标设备内置摄像头，通过 YOLO 对冰箱食材画框识别，用户核对后形成确认记录，并延伸到晚餐建议与家庭采购。品牌未定前，用户界面暂用“智慧冰箱”。当前摄像头与识别服务尚未接入。

项目采用 Android 优先交付，客户端使用 uni-app、Vue 3 和 TypeScript，服务端使用 FastAPI、PostgreSQL、持久化 Agent 与独立 worker。登录仅支持手机号和密码，不支持邮箱。

产品主线是：**查看冰箱 → 核对食材 → 购物查漏 → 共享采购**。助手是独立入口，用于查询家庭记录、生成晚餐建议和提出待确认的共享操作，不替代所有结构化按钮操作。

## 项目依据

最新产品、技术和实施基线位于 `docs/`：

- [产品需求文档](docs/01_产品需求文档.md)：产品定位、用户权限、业务规则和验收场景。
- [技术设计文档](docs/02_技术设计文档.md)：客户端、API、数据模型和 Agent 架构。
- [实施与学习手册](docs/03_实施与学习手册.md)：实施阶段、验证方法和学习边界。
- [当前实现与决策](docs/README.md)：已确认的重构决策、当前实现范围和已知缺口。

三份基线文档描述目标和验收要求；是否已经实现，以当前代码和 `docs/README.md` 的实现记录为准。

基线中的移动网页交付方案已由后续确认的独立 Android App 决策取代。文档中的旧工作区快照和测试数字仅供历史查证。

## 当前架构

```text
apps/web/    uni-app + Vue 3 + TypeScript Android 客户端
apps/api/    FastAPI API、PostgreSQL 模型、Alembic 迁移、Agent 和 worker
data/seed/   开发和联调种子数据
docs/        产品、技术、实施和运行文档
```

界面采用浅蓝渐变、圆角白卡和场景入口，提供加载骨架、按压反馈与助手运行提示。家庭端统一为一个 `user` 角色；家庭创建者和受邀用户都可邀请家人，家庭归属与个人会话权限仍分别校验。

客户端家庭端包含四个原生 Tab：

1. **冰箱**：展示已确认食材，并保留设备画面和视觉识别的真实空态。
2. **助手**：提交持久化 Agent 任务，显示执行步骤、依据、结果和待确认操作。
3. **采购**：读取共享采购清单，区分购买状态和实际入库。
4. **家庭**：登录、家庭选择和家庭信息。

服务端只使用 PostgreSQL。旧 SQLite 数据库、导入器和旧桌面管理端已移除，不迁移历史本地数据。原生 App 使用带 native 标记的 Bearer 会话；浏览器会话继续使用 Cookie 和 CSRF 保护。

## 当前边界

- 视觉模型和真实设备画面尚未接入，客户端不得把空态伪造成识别结果。
- 视觉观察与用户确认的库存记录分开；数量未知不会被默认成 `1` 或“库存充足”。
- Agent 的采购和其他共享写入先生成待确认操作，确认后才产生副作用。
- 品牌管理端、真实设备绑定、图片上传、真机 SSE 断线验收和签名 APK 尚未完成。
- 移动端手动登记、采购状态修改、确认入库及完整家庭管理仍待配套页面和接口；食材查找目前仅过滤已载入的最多 100 条记录。
- 默认 Agent Provider 可用于联调，不代表真实模型效果。

## 环境要求

- Python 3.12 或 3.13
- Node.js 22.12 或更高版本
- PostgreSQL
- HBuilderX 5.34 或更高版本（用于导入、运行和打包 uni-app）

## 安装与启动

在仓库根目录创建虚拟环境并安装后端依赖：

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e ".\apps\api[dev]"
```

首次配置时，将根目录 `.env.example` 复制为 `.env`，已有 `.env` 时保留原配置：

```powershell
Copy-Item .env.example .env
```

修改 `.env` 的 `DATABASE_URL` 为实际 PostgreSQL 连接，并提前创建对应数据库和账号。真实模型配置也由 `.env` 或环境变量提供，密钥不提交到仓库。

进入 `apps/api` 后先检查目标连接和 Alembic 状态，再执行迁移、启动 API。API 启动时会检查数据库是否已到迁移 head：

```powershell
Set-Location apps/api
& ..\..\.venv\Scripts\python.exe -m alembic current
& ..\..\.venv\Scripts\python.exe -m alembic heads
& ..\..\.venv\Scripts\python.exe -m alembic upgrade head
& ..\..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

另开终端启动 worker：

```powershell
Set-Location apps/api
& ..\..\.venv\Scripts\python.exe -m app.worker
```

首次运行客户端时，在 `apps/web` 安装锁定依赖：

```powershell
Set-Location apps/web
npm ci
npm run typecheck
```

然后用 HBuilderX 导入 `apps/web` 目录。HBuilderX 报告“`node_modules` 缺少编译器模块”时，先在该目录执行 `npm ci`，确认 `node_modules/@dcloudio/vite-plugin-uni` 存在，再重新打开或编译项目。

## Android 构建与地址

命令行构建 Android App-Plus：

```powershell
Set-Location apps/web
npm run build:app-android
```

构建产物位于 `apps/web/dist/build/app`。这不是签名 APK；可安装 APK 需要 HBuilderX 云打包，或本机 Android SDK 与签名配置。

Android 模拟器默认通过 `http://10.0.2.2:8000` 访问宿主机 API，配置位置为 `apps/web/src/shared/config.ts`。真机调试时，将 API 地址改为开发电脑可访问的局域网地址；正式环境使用 HTTPS。

API 文档默认地址：<http://localhost:8000/docs>

真机访问时，API 需监听局域网可达地址（如给 uvicorn 添加 `--host 0.0.0.0`），并允许开发端口通过本机防火墙。

从 `apps/api` 使用项目虚拟环境创建本地测试账号，按提示设置密码：

```powershell
& ..\..\.venv\Scripts\python.exe -m app.create_local_account 13900000000
```

家庭记录由账号授权决定，不会自动关联旧演示工作区。

## 测试与验证

后端测试会重建 ORM 测试表，必须使用库名以 `_test` 结尾的独立 PostgreSQL 数据库：

```powershell
# 从仓库根目录执行；连接示例按本机测试数据库调整
$env:MEALFLOW_TEST_DATABASE_URL = 'postgresql+psycopg://mealflow@127.0.0.1:5434/mealflow_s7_test?gssencmode=disable'
& .\.venv\Scripts\python.exe -m pytest apps/api -q
```

禁止将 `MEALFLOW_TEST_DATABASE_URL` 指向开发库、演示库或生产库。后端 lint 和前端验证：

```powershell
& .\.venv\Scripts\python.exe -m ruff check apps/api
Set-Location apps/web
npm run typecheck
npm run build:app-android
```

更完整的部署、演示和阶段验证说明见 [部署与运行](docs/部署与运行.md) 及 `docs/S4-*`、`docs/S5-*`、`docs/S6-*`、`docs/S7-*` 文档。
