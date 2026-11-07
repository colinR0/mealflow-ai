# 九道多格式菜谱加载测试资料

日期：2026-11-07 12:02（Asia/Shanghai）

## 范围与文件

- 新增 `scripts/create_recipe_loading_corpus.py`，以稳定菜谱 ID 生成九道原创菜谱。
- 新增 `output/pdf/recipe-loading-v1/` 中三份 PDF，以及 `output/recipe-loading-v1/md/`、`output/recipe-loading-v1/txt/` 中各三份文本菜谱。
- 新增 `output/recipe-loading-v1/manifest.json`，记录格式、相对路径、来源、版本和 SHA-256。
- 不修改既有菜谱、数据库或向量索引；保留工作区其他进行中的改动。

## 交付行为

每份文件仅包含一道菜，明确基准份数、时间、设备、食材用量、过敏原、步骤、完成判断、替换限制、原创来源和使用说明。三种格式用于后续加载器解析验证；生成文件本身不代表知识库加载完成。

## 验证结果

- 九个 ID 和菜名唯一，且菜名不与当前 JSON 菜谱完全重复。
- 清单记录的九个文件摘要与实际字节一致；三份 PDF 均为单页且可提取中文文本。
- 用 Poppler 渲染三份 PDF，逐页检查中文、段落、步骤和页边距，没有截断或异常分页。
- Markdown 与 TXT 按 UTF-8 回读；各文件包含预期菜名、来源和连续步骤。

## 已知缺口

- 尚未实现 PDF、Markdown、TXT 到统一父菜谱文档的加载器，未进行切分、向量化或入库。
- 菜谱为自制测试内容，未进行独立厨师试做、营养计算或医疗适用性验证。
