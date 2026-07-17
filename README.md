# Self-Improvement Research Radar

每天检索 arXiv 上与 AI self-improvement 相关的论文，提取标题、作者、摘要、分类、论文页和 PDF 链接，并生成一个可搜索、可筛选的静态网页。

项目只使用 Python 标准库，不需要安装第三方依赖。

在线网站：<https://simon32123.github.io/arxiv-self-improvement/>

## 立即运行

```bash
cd arxiv-self-improvement
./run.sh
```

`run.sh` 会优先使用可用的 Miniconda 或 macOS 系统 Python，避免 PATH 中损坏或过时的 `python3`。也可以显式运行：

```bash
/opt/miniconda3/bin/python3 fetch_arxiv.py
# 或
/usr/bin/python3 fetch_arxiv.py
```

生成完成后，直接用浏览器打开 `public/index.html` 即可。也可以启动本地网页服务：

```bash
python3 -m http.server 8000 -d public
```

然后访问 <http://localhost:8000>。

脚本每次只向 arXiv 发起一次检索请求。新结果会与 `data/papers.json` 中的历史记录合并；同一论文的新版本会覆盖旧版本，但不会产生重复卡片。

## 默认检索范围

默认关键词包括：

- `self improvement` / `self-improvement`
- `self improving` / `self-improving`
- `recursive self-improvement`
- `self-refinement`
- `self-reflection`
- `self-correction`

结果限定在 `cs.AI`、`cs.CL`、`cs.LG` 和 `cs.MA` 分类，以及 2026-01-01 以后提交的论文。每日任务获取最近 100 条匹配结果，并按首次提交时间倒序处理。

首次回溯 2026 年以来的全部匹配结果可运行：

```bash
./run.sh --all-results --max-results 200
```

可以临时替换检索式：

```bash
python3 fetch_arxiv.py \
  --query '(all:"self-improvement" OR all:"self-refinement") AND cat:cs.AI' \
  --max-results 50
```

如果只想用已有缓存重新生成网页，不访问网络：

```bash
python3 fetch_arxiv.py --offline
```

## 每日自动运行

仓库已经包含 `.github/workflows/daily-arxiv.yml`，默认每天北京时间 09:15 运行，更新论文缓存并发布到 GitHub Pages。

使用方法：

1. 把整个 `arxiv-self-improvement` 目录推送到一个 GitHub 仓库。
2. 在仓库的 **Settings → Pages → Build and deployment** 中，将 Source 设为 **GitHub Actions**。
3. 在 **Actions → Daily arXiv Radar** 中手动运行一次，之后会按日自动执行。

如果只在本机运行，也可以用 `cron`。执行 `crontab -e` 后加入一行，并替换为项目的绝对路径：

```cron
15 9 * * * cd /absolute/path/arxiv-self-improvement && /usr/bin/python3 fetch_arxiv.py >> daily-arxiv.log 2>&1
```

## 常用参数

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `--query` | 内置 self-improvement 检索式 | 自定义 arXiv `search_query` |
| `--max-results` | `100` | 每次获取的结果数，范围 1–2000 |
| `--all-results` | 关闭 | 分页获取当前检索式的全部结果，适合历史回溯 |
| `--data-file` | `data/papers.json` | 历史缓存位置 |
| `--output-dir` | `public` | 网页输出目录 |
| `--user-agent` | 项目默认标识 | 自定义请求标识，公开部署时建议换成自己的项目地址 |
| `--offline` | 关闭 | 只根据缓存重建网页 |

## 测试

```bash
python3 -m unittest discover -s tests -v
```

## 目录结构

```text
fetch_arxiv.py                 检索、解析、去重、生成网页
build_sites_worker.py          生成 Sites 托管构建
src/                           网页模板、样式与交互
data/papers.json               持久化论文缓存
public/                        可直接发布的最终网页
tests/                         标准库单元测试
.github/workflows/             每日更新与 GitHub Pages 发布
```

## 使用说明

arXiv 的搜索结果通常每天批量更新一次，因此没有必要对相同查询高频轮询。脚本遵循每日一次、单页小批量获取的方式，并针对 429/5xx 临时错误进行退避重试。网页仅展示 arXiv 返回的论文元数据和摘要，并始终链接回原始论文页面。
