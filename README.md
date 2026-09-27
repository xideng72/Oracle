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
| `python oci_manager.py setup-api-user` | 创建受限 API 用户/组/策略；已存在则询问是否删除重建 |

抢机可选参数：`--name 实例名` `--ocpus 4` `--memory 24` `--interval 120` `--tries 0`
（`--interval` 是轮询间隔秒数，建议不小于 60；`--tries 0` 表示无限尝试）

## 使用步骤

1. 安装依赖：
   ```
   pip install oci
   ```
2. 认证（二选一，脚本会自动适配）：
   - 本机：`oci setup config` 生成 `~/.oci/config`
     （跑 `setup-api-user` 的配置需有 IAM 管理权限）
   - Cloud Shell：无需配置文件，脚本自动用 delegation token 认证，
     粘贴一键命令即可全自动执行
3. 参数全自动识别：租户 / 可用域 / 子网 / 镜像 / SSH 公钥都不用手填，
   脚本会自动查（可用 `--compartment` `--ad` `--subnet` `--image` `--ssh-key` 手动覆盖）。
   唯一前提：本机 `~/.ssh` 下有公钥（没有就先跑 `ssh-keygen` 生成一个），
   且租户里已经建好 VCN 和子网。
4. 一键命令（Cloud Shell 里粘贴执行，注意私库需先解决下载鉴权）：
   ```
   wget -N "https://raw.githubusercontent.com/xideng72/Oracle/main/oci_manager.py" && pip3 install -q --user oci && python3 oci_manager.py setup-api-user
   ```
   或分步跑：
   ```
   python oci_manager.py list
   python oci_manager.py snatch --interval 120
   ```

## setup-api-user：创建受限 API 用户

```
python oci_manager.py setup-api-user
```

流程：
1. 组 `Group_for_Api_used` / 策略 `Policy_for_Api_used`：存在则复用，不存在则创建（永不删除）
2. 只扫描目标 API 用户（如 `User_for_Api_used`）：
   - 不存在 → 直接新建并加入组
   - 已存在 → 询问：`y` 删除它并用同名重建 / `n` 保留它，自动递增新建 `User_for_Api_used01`（01 被占就 02，以此类推）
3. 注册时的默认账户（xxx@gmail.com 那个）永远不会被扫描和删除，碰到直接退出

创建完成后只打印用户 OCID；API 密钥本脚本不处理，请自行在控制台生成。

- 这个命令需要**有 IAM 管理权限**的配置来运行（比如租户管理员）
- 邮箱一般不需要：脚本先不带邮箱创建，只有 Oracle 明确报错缺邮箱时才现场问你；
  也可以用 `--user-email` 预先指定
- 可选参数：`--group-name` `--policy-name` `--user-name` `--type new/old` `--yes`（跳过询问，相当于每次都选 y）

## 安全提醒

- `~/.oci/config` 里的是你的 API 私钥，别传到 GitHub，别发给别人
- 建议配合一个**受限权限的 API 用户**使用（只能管实例/硬盘/网络，不能管用户和账单），不要用租户管理员的密钥跑这个脚本
- 抢机轮询别太激进，高频请求可能触发 Oracle 风控导致封号
