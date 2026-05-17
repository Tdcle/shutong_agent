---
name: bash
description: Windows Git Bash 环境下的 bash 命令使用指南，含常见命令、路径写法和限制说明
---

# Bash 命令使用指南

## 运行环境

当前 `execute_bash` 工具使用 **Git Bash**（Windows 上 Git 自带的 bash）。支持标准 GNU bash 语法，包括管道、重定向、变量、条件判断、循环等。

## 路径写法

| 场景 | 写法 | 说明 |
|------|------|------|
| Windows 绝对路径 | `/c/Users/xxx/Desktop/file.txt` | 推荐，和 bash 习惯一致 |
| Windows 绝对路径 | `C:/Users/xxx/Desktop/file.txt` | 也可，正斜杠在 Windows Python/bash 中都能用 |
| 工作区相对路径 | `./file.txt` 或 `file.txt` | 当前目录就是会话工作区 |
| 含空格路径 | `"/c/Program Files/app/run.exe"` | 必须引号包裹 |

**不要使用反斜杠路径**（如 `C:\Users\...`），在 bash 中反斜杠是转义符，会导致路径解析错误。

## 常用命令对照

| 操作 | bash 命令 | 旧 cmd 等价 |
|------|-----------|------------|
| 列出文件 | `ls -la` | `dir` |
| 搜索文本 | `grep -r "pattern" .` | `findstr` |
| 查找文件 | `find . -name "*.py"` | `dir /s *.py` |
| 创建目录 | `mkdir -p path/to/dir` | `mkdir path\to\dir` |
| 复制文件 | `cp source dest` | `copy` |
| 移动文件 | `mv source dest` | `move` |
| 删除文件 | `rm file` | `del` |
| 递归删除 | `rm -rf dir/` | `rmdir /s` |
| 查看文件 | `cat file` | `type` |
| 逐页查看 | `less file` | `more` |
| 管道组合 | `grep "error" log.txt \| wc -l` | 需 `findstr` 配合临时文件 |
| 输出重定向 | `ls > files.txt` | 相同 |
| 追加输出 | `echo "log" >> app.log` | 相同 |
| 环境变量 | `echo $HOME` | `echo %USERPROFILE%` |
| 命令替换 | `result=$(ls \| wc -l)` | `for /f ...` |
| 条件判断 | `if [ -f "file" ]; then ... fi` | `if exist ...` |
| 文件权限 | `chmod +x script.sh` | 不适用 |
| 进程列表 | `ps aux` | `tasklist` |

## 限制与注意事项

### 不能用的命令
- **网络请求**：`curl`、`wget`、`ssh`、`scp`、`sftp`、`ftp`、`telnet` — 出于安全考虑被禁用
- **嵌套 Shell**：不能在 bash 里再起 `powershell`、`cmd`、`sh`、`zsh`
- **pip 安装**：`pip install`、`pip3 install`、`conda install` — 包管理被禁用，依赖由管理员预装
- **危险系统命令**：`shutdown`、`format`、`regsvr32`、`rundll32` 等

### 可以但要注意
- **`rm` 操作**：在外部路径上使用 `rm -rf` 带通配符会被拦截。批量删除请用 `delete_paths` 工具
- **`python` 命令**：不要在 bash 里包 Python 代码（如 `python -c "..."`），请用 `execute_python` 工具
- **Git Bash 特有行为**：`/dev/null` 可用；`/proc` 不存在；`find` 不支持 `-executable` 等 Linux 独有选项

### Python 相关
- 执行 Python 脚本：`python script.py`（Python 在工作区中运行）
- Python 运行时路径：沙箱会使用专用 Python 环境（已预装 pandas、numpy 等）
- 不要在 bash 里写多行 `python -c` 内联代码，用 `execute_python` 工具更安全、更清晰

## 项目常用命令速查

```bash
# 后端
cd backend && python main.py              # 启动 API 服务
cd backend && python tests/test_sandbox.py # 运行测试

# 前端
cd frontend && npm run dev                 # Vite 开发服务器
cd frontend && npm run build               # 生产构建

# Git 操作
git status
git diff
git log --oneline -10
git add -A && git commit -m "commit message"
```
