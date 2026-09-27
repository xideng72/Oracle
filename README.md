# OCI 个人实例管理小工具

用 Oracle 官方 Python SDK 写的个人小工具：抢免费 ARM 实例 + 日常管理实例。

## 功能

| 命令 | 说明 |
|---|---|
| `python oci_manager.py list` | 列出名下所有实例 |
| `python oci_manager.py snatch` | 抢机：循环尝试创建 ARM 实例，直到成功（Ctrl+C 停止） |
| `python oci_manager.py start <实例OCID>` | 开机 |
| `python oci_manager.py stop <实例OCID>` | 关机 |
| `python oci_manager.py softreset <实例OCID>` | 软重启 |
| `python oci_manager.py terminate <实例OCID>` | 删除实例（需输入 YES 二次确认） |

抢机可选参数：`--name 实例名` `--ocpus 4` `--memory 24` `--interval 120` `--tries 0`
（`--interval` 是轮询间隔秒数，建议不小于 60；`--tries 0` 表示无限尝试）

## 使用步骤

1. 安装依赖：
   ```
   pip install oci
   ```
2. 生成 API 配置（二选一）：
   - 命令行：`oci setup config` 按提示走完
   - 控制台：身份 → 用户 → 你的用户 → API 密钥 → 添加，把生成的配置文件放到 `~/.oci/config`
3. 打开 `oci_manager.py`，把文件顶部的 5 个常量换成你自己的：
   - `COMPARTMENT_ID`（租户 OCID）
   - `AVAILABILITY_DOMAIN`（可用域，如 `xxxx:EU-AMSTERDAM-1-AD-1`）
   - `SUBNET_ID`（子网 OCID）
   - `IMAGE_ID`（ARM 镜像 OCID，控制台创建实例页面能查到）
   - `SSH_PUBLIC_KEY`（你的 SSH 公钥）
4. 跑起来：
   ```
   python oci_manager.py list
   python oci_manager.py snatch --interval 120
   ```

## 安全提醒

- `~/.oci/config` 里的是你的 API 私钥，别传到 GitHub，别发给别人
- 建议配合一个**受限权限的 API 用户**使用（只能管实例/硬盘/网络，不能管用户和账单），不要用租户管理员的密钥跑这个脚本
- 抢机轮询别太激进，高频请求可能触发 Oracle 风控导致封号
