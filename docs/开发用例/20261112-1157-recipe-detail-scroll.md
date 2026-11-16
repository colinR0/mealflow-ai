# 菜谱详情无法滚动及助手交接入口修复

- 日期：2026-11-12 11:57
- 范围：`apps/web/src/pages/shopping/recipes.vue`
- 状态：已实现并在 H5 验证；Android 仅完成构建，未上真机；改动未提交

## 问题与根因

- “今天吃什么”点击菜品后，详情面板 `.detail-sheet` 只有 `max-height: 85vh`，内部 `<scroll-view class="detail-scroll">` 没有高度约束。
- H5 实测“佛跳墙”详情内容高约 1170px，面板高度约 573px，但 `scroll-view` 被内容撑到 1170px，内部没有产生滚动。
- “向助手咨询搭配”按钮随内容位于面板底部之外（佛跳墙按钮顶部约 1243px），用户无法滚动到按钮，因此表现为“无法直接转给助手”。
- 助手交接逻辑本身已存在；按既有约定，跳转后只预填问题并聚焦输入框，由用户确认后发送，不自动发送。

## 修改内容

- `.detail-sheet` 增加 `overflow: hidden`，防止内容撑出圆角面板。
- `.detail-heading .recipe-name` 固定单行省略，保证标题区域高度稳定；关闭按钮禁止被压缩。
- `.detail-scroll` 增加 `max-height: calc(85vh - 94px)`，并补充带 `env(safe-area-inset-bottom)` 的版本；94px 为上下内边距、单行标题和标题与内容间距。
- 未修改 `askAssistant()` 和助手页发送逻辑，保持“跳转后预填、用户确认后发送”。

## 行为变化

- 长菜谱详情可在面板内上下滚动，能到达食材、烹饪步骤、来源说明和“向助手咨询搭配”按钮。
- 点击按钮仍会跳到助手页并预填当前菜品问题，不自动发送。
- 面板仍受 `85vh` 上限约束；内容不足上限时按内容自适应。

## 验证结果

- H5 实测（视口高 674px）：
  - 佛跳墙：面板 573px，滚动区 479px，内部内容 1170px；滚动到底后按钮位于 551.6–645.4px，完整落在面板内。
  - B52轰炸机、鸡蛋花、冬瓜茶：滚动区均为 479px，内部内容分别为 678px、778px、674px，均可滚动到按钮。
  - 点击佛跳墙按钮后，助手输入框预填佛跳墙问题；点击 B52轰炸机按钮后，输入框更新为 B52 问题，未自动发送。
- 自动化检查：
  - `npm run typecheck` 通过。
  - `node --test tests/assistant-interactions.test.mjs`：9/9 通过。
  - `npm run build:app-android` 构建成功（仅有既有 Sass 弃用警告）。
  - `git diff --check -- apps/web/src/pages/shopping/recipes.vue`：无空白错误，仅有 Git 的 LF/CRLF 提示。

## 已知限制

- 未在 Android 真机验证，当前为 H5 测量 + Android 构建通过；真机手势滚动仍需回归。
- 按 20261111-2108 的开发记录，助手交接保持“预填问题、用户确认后发送”，本次没有改为自动发送。