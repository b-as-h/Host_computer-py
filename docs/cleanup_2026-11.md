# 仓库清理记录（2026-11-01）

本次清理只针对**文件与文档**，不改变任何运行逻辑。清理前的完整副本保存在仓库之外
（`../_backup_before_cleanup_2026-11-01/`），可随时比对。

## 一、判断标准

一个文件只有同时满足下面任意一条、且经不起"保留理由"检验时才会被删除：

| 编号 | 标准 | 具体判据 |
|---|---|---|
| S1 | **可再生成** | 由脚本或构建命令自动产出，删除后重新执行命令即可原样恢复 |
| S2 | **逐字重复** | 内容与仓库外已发布的权威副本（GitHub Release、README 表格）完全一致 |
| S3 | **无引用且过期** | 仓库内没有任何文件引用它，且内容对应已发布的旧版本 |
| S4 | **影响正确性** | 保留会让人误以为是最新版本（例如旧版可执行文件），风险大于收益 |

同时满足以下**任一**条件的文件一律保留，即使体积较大：

- 被代码、测试、README、安装脚本直接引用；
- 是某一类产物中唯一的一份（删了就再也拿不到，除非重新构建/重跑）；
- 承载无法从别处推导的信息（协议表、测试基线、构建参数）。

## 二、删除清单

| 文件 | 体积 | 命中标准 | 删除原因 |
|---|---|---|---|
| `dist/STM32Host.exe` | 11.3 MB | S1 S3 S4 | v1.9.0 构建的单文件 exe。源码已到 v1.10.0（新增 AT 快捷指令、可拖拽终端、两栏布局），这个 exe 打开仍是旧界面，留着会让人误判版本；`installer\build_installer.bat` 一条命令即可重建 |
| `dist/STM32Host-Setup-1.9.0.exe` | 13.0 MB | S1 S2 S4 | 同上，且该安装包作为资产挂在 GitHub Releases v1.9.0 上，仓库内这份是重复副本 |
| `docs/release_notes_v1.9.0.md` | 4.1 KB | S2 S3 | 与 GitHub Release `v1.9.0` 的正文**逐字相同**（含 SHA-256、验证结果表、已知问题）。仓库内没有任何文件链接到它，属于孤立文档 |

合计删除 **3 个文件，约 24.3 MB**，其中两个二进制文件占仓库总体积的 96%。

### 附带整理（非文件）

| 项目 | 处理 | 说明 |
|---|---|---|
| `host_monitor.py` 里的 `INFO` 常量 | 删除 1 行 | 定义后从未被引用（`grep -c` 计数为 1，即只有定义处） |
| `.gitignore` | 新增 `dist/` | 防止下次打包后 25 MB 二进制再次被误提交，从源头阻断 S4 类问题 |

## 三、删除不影响功能的验证

按"静态引用 → 语法 → 单元 → 系统级 → 关键路径"五层验证：

1. **引用检索**：`grep -rn "dist/" --include=*.py --include=*.bat .` 无结果；
   `dist/` 只出现在 README 的构建说明里（该说明讲的是"构建后生成到 dist"，不依赖仓库内已有文件）。
2. **语法编译**：`python -m py_compile host_monitor.py appconfig.py atcmd.py selftest.py sysinfo.py tools/*.py` 全部通过。
3. **离线自检**：`python selftest.py` → **95 项通过 / 0 失败**（协议收发、$D/$R/PC 帧、AT 行尾策略、布局断言）。
4. **前台系统测试**：`python tools/system_test.py` → **55 项通过 / 0 失败**
   （通信、控制、传感器、界面、异常恢复、长时可视化）。
5. **关键路径**：启动界面、连接虚拟串口、拖拽分隔条、AT 指令增删改、写配置后重启还原 —— 均正常。

补充说明：`docs/release_notes_v1.9.0.md` 的删除不影响 Release 页面，
因为页面正文本身就完整保存了这份内容（`https://github.com/b-as-h/Host_computer-py/releases/tag/v1.9.0`）。

## 四、保留清单与理由

### 源码（6 个）

| 文件 | 保留理由 |
|---|---|
| `host_monitor.py` | 主程序，唯一入口 |
| `sysinfo.py` | 被主程序导入；也可独立运行 |
| `appconfig.py` | v1.10.0 配置持久化，被主程序导入 |
| `atcmd.py` | v1.10.0 AT 指令模型，被主程序和自检导入 |
| `selftest.py` | 离线回归入口，README 明确要求"上板前先跑" |
| `tools/system_test.py` | 系统级测试台，产出测试报告，不可替代 |

### 辅助脚本（3 个）

| 文件 | 保留理由 |
|---|---|
| `tools/capture_screenshots.py` | README 四张展示图的**唯一生成方式**，删了就无法复现截图 |
| `tools/gyro_sim.py` | 无硬件演示 3D 姿态仪的入口，README 有专节介绍 |
| `run_host.bat` | 双击启动入口，README 首选运行方式 |

### 构建与安装（4 个）

| 文件 | 保留理由 |
|---|---|
| `installer/STM32Host.iss` | Inno Setup 脚本，安装包的唯一来源 |
| `installer/version_info.txt` | exe 版本资源，被 `.iss` 与打包命令引用 |
| `installer/build_installer.bat` | 一键构建入口 |
| `app.ico` | 图标，被 `--icon` 与安装脚本引用 |

### 文档与证据（5 个）

| 文件 | 保留理由 |
|---|---|
| `README.md` | 唯一总说明：安装、协议、界面、FAQ、更新记录 |
| `docs/system_test_report.md` | 自动生成的测试报告，README 与 Release 都链接到它，是该版本唯一的可读测试证据 |
| `docs/cleanup_2026-11.md` | 本文档，记录清理依据与保留清单 |
| `test-results/*.json` + 4 张 PNG | 最近一次测试的原始结果与阶段证据，被报告内联引用；重跑测试会覆盖，但作为"当前状态"的价值不可推导 |
| `screenshot.png`、`screenshots/*.png` | 仓库首页与功能展示图，由 `capture_screenshots.py` 生成，但已嵌入 README，删除会造成图片裂图 |

### 其他

| 文件 | 保留理由 |
|---|---|
| `requirements.txt` | 运行依赖（pyserial） |
| `requirements-dev.txt` | 打包依赖（PyInstaller） |
| `.gitignore` | 防止缓存与构建产物入库 |

> `test-results/` 与 `dist/` 的区别：前者是**测试证据**（几十 KB，被报告引用，保留有意义）；
> 后者是**可执行产物**（25 MB，可在 Releases 下载，仓库内副本只会过期）。

## 五、清理结果

| 指标 | 清理前 | 清理后 | 变化 |
|---|---|---|---|
| 文件数（不含 `.git`、`.workbuddy`） | 30 | 28 | −2（删 3、新增 1 份记录） |
| 仓库体积 | 26 MB | 620 KB | **−25.4 MB（−97.6%）** |
| 目录数 | 6 | 5 | −1（`dist/` 移除，打包时自动创建） |
| 顶层条目 | 19 | 18 | −1 |

结构变化：

```
删除  dist/                       ← 构建产物目录，由 build_installer.bat 生成
删除  docs/release_notes_v1.9.0.md ← 与 Release 正文重复
新增  docs/cleanup_2026-11.md      ← 本记录
修改  .gitignore                   ← 忽略 dist/
修改  README.md                    ← 文件说明表去掉 dist 行，补充清理说明
修改  host_monitor.py              ← 删除未使用的 INFO 常量
```

## 六、后续约定

1. `dist/` 只在本地构建，**不再提交**；发布走 GitHub Releases 上传安装包。
2. 版本说明写在 Release 正文里，仓库内不再保留 `release_notes_*.md`，避免两处维护。
3. 每次改动协议或界面后，按 `selftest.py` → `system_test.py` → `capture_screenshots.py` 的顺序回归，
   三者产出的报告与截图一起提交。
