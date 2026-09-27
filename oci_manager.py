#!/usr/bin/env python3
"""
OCI 个人实例管理小工具（个人使用）

功能：
  1. 抢机   ：循环尝试创建免费 ARM 实例，直到成功
  2. 管理   ：列出 / 开机 / 关机 / 删除实例

依赖：
  pip install oci

配置：
  标准 OCI 配置文件 ~/.oci/config（可用 `oci setup config` 生成）。
  租户 / 可用域 / 子网 / 镜像 / SSH 公钥 全部自动识别，
  也可以用命令行参数手动覆盖。

注意：
  - 抢机轮询间隔别设太小（建议 >= 60 秒），高频请求可能触发 Oracle 风控导致封号
  - 删除实例是不可逆操作，脚本会要求二次确认
"""

import argparse
import os
import sys
import time

import oci
from oci.exceptions import ServiceError


def get_clients(profile="DEFAULT"):
    config = oci.config.from_file(profile_name=profile)
    oci.config.validate_config(config)
    return (
        config,
        oci.core.ComputeClient(config),
        oci.core.VirtualNetworkClient(config),
        oci.identity.IdentityClient(config),
    )


def discover(config, compute, network, identity, args):
    """自动识别 5 个参数；命令行参数可手动覆盖"""
    tenancy_id = config["tenancy"]
    compartment_id = args.compartment or tenancy_id

    # 1. 可用域：取第一个
    if args.ad:
        ad = args.ad
    else:
        ads = identity.list_availability_domains(compartment_id).data
        if not ads:
            raise RuntimeError("找不到可用域")
        ad = ads[0].name
        print(f"自动选择可用域：{ad}")

    # 2. 子网：在该可用域下找第一个子网（区域子网 availability_domain 为空，通用）
    if args.subnet:
        subnet_id = args.subnet
    else:
        subnet_id = None
        for vcn in network.list_vcns(compartment_id).data:
            for s in network.list_subnets(compartment_id, vcn_id=vcn.id).data:
                if not s.availability_domain or s.availability_domain == ad:
                    if s.lifecycle_state == "AVAILABLE":
                        subnet_id = s.id
                        break
            if subnet_id:
                break
        if not subnet_id:
            raise RuntimeError(
                "没找到可用子网：请先在控制台创建一个 VCN 和子网，或用 --subnet 手动指定"
            )
        print(f"自动选择子网：{subnet_id}")

    # 3. 镜像：找最新的 Canonical Ubuntu ARM 镜像
    if args.image:
        image_id = args.image
    else:
        images = compute.list_images(
            compartment_id,
            operating_system="Canonical Ubuntu",
            shape="VM.Standard.A1.Flex",
            sort_by="TIMECREATED",
            sort_order="DESC",
        ).data
        if not images:
            raise RuntimeError("没找到 Ubuntu ARM 镜像，请用 --image 手动指定")
        image_id = images[0].id
        print(f"自动选择镜像：{images[0].display_name}")

    # 4. SSH 公钥：从本机常见路径自动读取
    if args.ssh_key:
        ssh_key = args.ssh_key
    elif args.ssh_key_file:
        with open(os.path.expanduser(args.ssh_key_file)) as f:
            ssh_key = f.read().strip()
    else:
        ssh_key = None
        for name in ("id_ed25519.pub", "id_rsa.pub", "id_ecdsa.pub"):
            p = os.path.expanduser(f"~/.ssh/{name}")
            if os.path.exists(p):
                with open(p) as f:
                    ssh_key = f.read().strip()
                print(f"自动读取 SSH 公钥：{p}")
                break
        if not ssh_key:
            raise RuntimeError(
                "本机 ~/.ssh 下没找到公钥：请先运行 ssh-keygen 生成，"
                "或用 --ssh-key / --ssh-key-file 手动指定"
            )

    return {
        "compartment_id": compartment_id,
        "ad": ad,
        "subnet_id": subnet_id,
        "image_id": image_id,
        "ssh_key": ssh_key,
    }


def list_instances(compute, compartment_id):
    """列出实例"""
    resp = compute.list_instances(compartment_id=compartment_id)
    if not resp.data:
        print("没有实例。")
        return
    print(f"{'实例名':<25} {'状态':<15} {'OCID'}")
    print("-" * 90)
    for inst in resp.data:
        print(f"{inst.display_name:<25} {inst.lifecycle_state:<15} {inst.id}")


def try_launch(compute, env, display_name="arm-instance", ocpus=4, memory_gb=24):
    """尝试创建一台免费 ARM 实例，成功返回实例对象，失败返回 None"""
    details = oci.core.models.LaunchInstanceDetails(
        compartment_id=env["compartment_id"],
        availability_domain=env["ad"],
        display_name=display_name,
        image_id=env["image_id"],
        shape="VM.Standard.A1.Flex",
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=ocpus, memory_in_gbs=memory_gb
        ),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=env["subnet_id"],
            assign_public_ip=True,
        ),
        metadata={"ssh_authorized_keys": env["ssh_key"]},
    )
    try:
        return compute.launch_instance(details).data
    except ServiceError as e:
        if e.status in (429, 500, 502, 503):
            msg = (e.message or "")[:120].replace("\n", " ")
            print(f"  本次失败（{e.status}）：{msg}")
            return None
        raise


def snatch(compute, env, interval=120, max_tries=0, **launch_kwargs):
    """抢机：循环尝试创建，直到成功或达到最大次数（0 = 无限）"""
    attempt = 0
    print(f"开始抢机：每 {interval} 秒尝试一次（Ctrl+C 停止）")
    while True:
        attempt += 1
        if max_tries and attempt > max_tries:
            print(f"已尝试 {max_tries} 次，未成功，退出。")
            return 1
        print(f"[第 {attempt} 次] 尝试创建实例…")
        try:
            inst = try_launch(compute, env, **launch_kwargs)
        except ServiceError as e:
            print(f"配置或权限错误（{e.status}），停止：{(e.message or '')[:200]}")
            return 2
        if inst:
            print(f"成功！实例 {inst.display_name} 已创建：{inst.id}")
            print(f"状态：{inst.lifecycle_state}")
            return 0
        time.sleep(interval)


def instance_action(compute, instance_id, action):
    actions = {"start": "START", "stop": "STOP", "softstop": "SOFTSTOP",
               "reset": "RESET", "softreset": "SOFTRESET"}
    compute.instance_action(instance_id, actions[action])
    print(f"已发送 {action} 指令给 {instance_id}")


def terminate(compute, instance_id):
    confirm = input(f"确认删除实例 {instance_id}？输入 YES 继续：").strip()
    if confirm != "YES":
        print("已取消。")
        return 1
    compute.terminate_instance(instance_id)
    print("删除指令已发送。")


# 受限 API 用户相关（对标 oracle_role_apiuser_policy.sh 的作用）
API_GROUP_NAME = "Group_for_Api_used"
API_POLICY_NAME = "Policy_for_Api_used"
API_USER_NAME = "User_for_Api_used"


def api_policy_statements(group_name, idcs_type="new"):
    """生成受限策略语句：只能管实例/硬盘/网络，不能管用户和账单"""
    prefix = f"'Default'/'{group_name}'" if idcs_type == "new" else group_name
    return [
        f"Allow group {prefix} to manage instance-family in tenancy",
        f"Allow group {prefix} to manage volume-family in tenancy",
        f"Allow group {prefix} to manage virtual-network-family in tenancy",
        f"Allow group {prefix} to read all-resources in tenancy",
    ]


def setup_api_user(identity, config, args):
    """创建受限 API 用户：默认账户永不扫描/删除；目标用户存在时 y=删除重建 / n=递增新建"""
    tenancy_id = config["tenancy"]
    group_name = args.group_name
    policy_name = args.policy_name
    base_user_name = args.user_name

    # 组 / 策略：存在则复用，不存在则创建，永不删除
    groups = identity.list_groups(tenancy_id, name=group_name).data
    if groups:
        group = groups[0]
        print(f"组已存在，复用：{group_name}")
    else:
        group = identity.create_group(
            oci.identity.models.CreateGroupDetails(
                compartment_id=tenancy_id,
                name=group_name,
                description="给 API 使用的受限权限组，防止 API 操作用户类权限",
            )
        ).data
        print(f"组已创建：{group_name}")

    policies = identity.list_policies(tenancy_id, name=policy_name).data
    if policies:
        print(f"策略已存在，复用：{policy_name}")
    else:
        identity.create_policy(
            oci.identity.models.CreatePolicyDetails(
                compartment_id=tenancy_id,
                name=policy_name,
                description="给 API 使用的受限权限策略，防止 API 操作用户类权限",
                statements=api_policy_statements(group_name, args.type),
            )
        )
        print(f"策略已创建：{policy_name}")

    # 只扫描目标 API 用户；默认账户（当前配置在用的那个）碰都不碰
    users = identity.list_users(tenancy_id, name=base_user_name).data
    user = users[0] if users else None
    if user and user.id == config["user"]:
        print(f"用户 {base_user_name} 是注册时的默认账户，不能删除也不处理，本次退出。")
        return 1

    target_name = base_user_name
    if user:
        if args.yes:
            choice = "y"
        else:
            new_name = next_available_username(identity, tenancy_id, base_user_name)
            print(f"扫描到用户 {base_user_name} 已存在。")
            choice = input(
                f"  [y] 删除它，并用同名重建\n"
                f"  [n] 保留它，新建 {new_name}\n"
                f"请选择 y/n："
            ).strip().lower()
        if choice == "y":
            for m in identity.list_user_group_memberships(
                tenancy_id, group_id=group.id, user_id=user.id
            ).data:
                identity.remove_user_from_group(m.id)
            identity.delete_user(user.id)
            print(f"用户已删除：{base_user_name}（其 API 密钥已同步失效）")
        elif choice == "n":
            target_name = new_name
            print(f"保留现有用户，新建：{target_name}")
        else:
            print("输入无效，已取消。")
            return 1

    details = oci.identity.models.CreateUserDetails(
        compartment_id=tenancy_id,
        name=target_name,
        description="给 API 使用的受限权限用户，防止 API 操作用户类权限",
    )
    if args.user_email:
        details.email = args.user_email
    try:
        usr = identity.create_user(details).data
    except ServiceError as e:
        # 只有 Oracle 明确报错缺邮箱时，才现场询问
        if not args.user_email and "email" in (e.message or "").lower():
            email = input(f"Oracle 要求提供邮箱，请为 {target_name} 输入（回车放弃）：").strip()
            if not email:
                raise
            details.email = email
            usr = identity.create_user(details).data
        else:
            raise
    identity.add_user_to_group(
        oci.identity.models.AddUserToGroupDetails(user_id=usr.id, group_id=group.id)
    )
    print(f"用户已创建：{target_name}")
    print(f"  用户 OCID : {usr.id}")
    print(f"  所属组    : {group_name}（受限权限：仅管理实例/硬盘/网络）")
    return 0


def next_available_username(identity, tenancy_id, base):
    """找到下一个可用的递增用户名，如 User_for_Api_used01"""
    for i in range(1, 100):
        name = f"{base}{i:02d}"
        if not identity.list_users(tenancy_id, name=name).data:
            return name
    raise RuntimeError("01~99 都被占用了，请手动清理后再试")


def add_common(p):
    """自动识别参数的手动覆盖选项"""
    p.add_argument("--compartment", default="", help="手动指定租户/ compartment OCID")
    p.add_argument("--ad", default="", help="手动指定可用域")
    p.add_argument("--subnet", default="", help="手动指定子网 OCID")
    p.add_argument("--image", default="", help="手动指定镜像 OCID")
    p.add_argument("--ssh-key", default="", help="手动指定 SSH 公钥内容")
    p.add_argument("--ssh-key-file", default="", help="手动指定 SSH 公钥文件路径")
    p.add_argument("--profile", default="DEFAULT", help="~/.oci/config 中的配置名")


def main():
    parser = argparse.ArgumentParser(description="OCI 个人实例管理小工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="列出所有实例")
    add_common(p)

    p = sub.add_parser("snatch", help="抢机：循环尝试创建 ARM 实例")
    add_common(p)
    p.add_argument("--name", default="arm-instance", help="实例显示名")
    p.add_argument("--ocpus", type=int, default=4, help="OCPU 数（免费上限 4）")
    p.add_argument("--memory", type=int, default=24, help="内存 GB（免费上限 24）")
    p.add_argument("--interval", type=int, default=120, help="轮询间隔秒数（建议>=60）")
    p.add_argument("--tries", type=int, default=0, help="最大尝试次数，0=无限")

    for cmd in ("start", "stop", "softreset"):
        p = sub.add_parser(cmd, help={"start": "开机", "stop": "关机",
                                      "softreset": "软重启"}[cmd])
        p.add_argument("instance_id")
        add_common(p)

    p = sub.add_parser("terminate", help="删除实例")
    p.add_argument("instance_id")
    add_common(p)

    p = sub.add_parser("setup-api-user", help="创建受限 API 用户；已存在时 y=删除重建 / n=递增新建")
    p.add_argument("--profile", default="DEFAULT", help="~/.oci/config 中的配置名（需有 IAM 管理权限）")
    p.add_argument("--group-name", default=API_GROUP_NAME, help="组名")
    p.add_argument("--policy-name", default=API_POLICY_NAME, help="策略名")
    p.add_argument("--user-name", default=API_USER_NAME, help="用户名")
    p.add_argument("--user-email", default="", help="用户邮箱（一般不需要；只有 Oracle 报错缺邮箱时才会用）")
    p.add_argument("--type", default="new", choices=("new", "old"),
                   help="控制台类型：new=身份域版，old=旧版")
    p.add_argument("--yes", action="store_true", help="跳过询问，相当于每次都选 y")

    args = parser.parse_args()
    config, compute, network, identity = get_clients(args.profile)

    if args.cmd == "list":
        list_instances(compute, args.compartment or config["tenancy"])
        return 0

    env = discover(config, compute, network, identity, args)

    if args.cmd == "snatch":
        return snatch(compute, env, interval=args.interval, max_tries=args.tries,
                      display_name=args.name, ocpus=args.ocpus, memory_gb=args.memory)
    if args.cmd in ("start", "stop", "softreset"):
        return instance_action(compute, args.instance_id, args.cmd)
    if args.cmd == "terminate":
        return terminate(compute, args.instance_id)
    if args.cmd == "setup-api-user":
        return setup_api_user(identity, config, args)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        print(f"错误：{e}")
        sys.exit(1)
    except ServiceError as e:
        print(f"API 错误（{e.status}）：{(e.message or '')[:300]}")
        sys.exit(1)
