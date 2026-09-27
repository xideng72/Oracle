#!/usr/bin/env python3
"""
OCI 个人实例管理小工具（个人使用）

功能：
  1. 抢机   ：循环尝试创建免费 ARM 实例，直到成功
  2. 管理   ：列出 / 开机 / 关机 / 删除实例

依赖：
  pip install oci

配置：
  标准 OCI 配置文件 ~/.oci/config（可用 `oci setup config` 生成），
  然后把下面的 OCID 常量换成你自己的。

注意：
  - 抢机轮询间隔别设太小（建议 >= 60 秒），高频请求可能触发 Oracle 风控导致封号
  - 删除实例是不可逆操作，脚本会要求二次确认
"""

import argparse
import sys
import time

import oci
from oci.exceptions import ServiceError

# ============ 按需修改 ============
COMPARTMENT_ID = "ocid1.tenancy.oc1..xxxxxxxxxxxxxxxx"   # 租户 OCID
AVAILABILITY_DOMAIN = "xxxx:EU-AMSTERDAM-1-AD-1"          # 可用域
SUBNET_ID = "ocid1.subnet.oc1.eu-amsterdam-1.xxxxxxxx"    # 子网 OCID
IMAGE_ID = "ocid1.image.oc1.eu-amsterdam-1.xxxxxxxx"      # ARM 镜像 OCID（Ubuntu/Canonical）
SSH_PUBLIC_KEY = "ssh-rsa AAAA... your-ssh-public-key"    # 你的 SSH 公钥
# ==================================


def get_client():
    config = oci.config.from_file()  # 读取 ~/.oci/config 的 DEFAULT 配置
    oci.config.validate_config(config)
    return oci.core.ComputeClient(config)


def list_instances(client, compartment_id):
    """列出租户下所有实例"""
    resp = client.list_instances(compartment_id=compartment_id)
    if not resp.data:
        print("没有实例。")
        return
    print(f"{'实例名':<25} {'状态':<15} {'OCID'}")
    print("-" * 90)
    for inst in resp.data:
        print(f"{inst.display_name:<25} {inst.lifecycle_state:<15} {inst.id}")


def try_launch(client, display_name="arm-instance", ocpus=4, memory_gb=24):
    """尝试创建一台免费 ARM 实例，成功返回实例对象，失败返回 None"""
    details = oci.core.models.LaunchInstanceDetails(
        compartment_id=COMPARTMENT_ID,
        availability_domain=AVAILABILITY_DOMAIN,
        display_name=display_name,
        image_id=IMAGE_ID,
        shape="VM.Standard.A1.Flex",
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=ocpus, memory_in_gbs=memory_gb
        ),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=SUBNET_ID,
            assign_public_ip=True,
        ),
        metadata={"ssh_authorized_keys": SSH_PUBLIC_KEY},
    )
    try:
        resp = client.launch_instance(details)
        return resp.data
    except ServiceError as e:
        # 500/429 通常是无容量或限流；401/404 是配置问题，直接抛出来
        if e.status in (429, 500, 502, 503):
            msg = (e.message or "")[:120].replace("\n", " ")
            print(f"  本次失败（{e.status}）：{msg}")
            return None
        raise


def snatch(client, interval=120, max_tries=0, **launch_kwargs):
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
            inst = try_launch(client, **launch_kwargs)
        except ServiceError as e:
            print(f"配置或权限错误（{e.status}），停止：{e.message[:200]}")
            return 2
        if inst:
            print(f"成功！实例 {inst.display_name} 已创建：{inst.id}")
            print(f"状态：{inst.lifecycle_state}")
            return 0
        time.sleep(interval)


def instance_action(client, instance_id, action):
    """开机 / 关机 / 重启"""
    actions = {"start": "START", "stop": "STOP", "softstop": "SOFTSTOP",
               "reset": "RESET", "softreset": "SOFTRESET"}
    if action not in actions:
        print(f"未知操作：{action}")
        return 1
    client.instance_action(instance_id, actions[action])
    print(f"已发送 {action} 指令给 {instance_id}")


def terminate(client, instance_id):
    """删除实例（二次确认）"""
    confirm = input(f"确认删除实例 {instance_id}？输入 YES 继续：").strip()
    if confirm != "YES":
        print("已取消。")
        return 1
    client.terminate_instance(instance_id)
    print("删除指令已发送。")


def main():
    parser = argparse.ArgumentParser(description="OCI 个人实例管理小工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出所有实例")

    p = sub.add_parser("snatch", help="抢机：循环尝试创建 ARM 实例")
    p.add_argument("--name", default="arm-instance", help="实例显示名")
    p.add_argument("--ocpus", type=int, default=4, help="OCPU 数（免费上限 4）")
    p.add_argument("--memory", type=int, default=24, help="内存 GB（免费上限 24）")
    p.add_argument("--interval", type=int, default=120, help="轮询间隔秒数（建议>=60）")
    p.add_argument("--tries", type=int, default=0, help="最大尝试次数，0=无限")

    p = sub.add_parser("start", help="开机")
    p.add_argument("instance_id")
    p = sub.add_parser("stop", help="关机")
    p.add_argument("instance_id")
    p = sub.add_parser("softreset", help="软重启")
    p.add_argument("instance_id")
    p = sub.add_parser("terminate", help="删除实例")
    p.add_argument("instance_id")

    args = parser.parse_args()
    client = get_client()

    if args.cmd == "list":
        list_instances(client, COMPARTMENT_ID)
    elif args.cmd == "snatch":
        return snatch(client, interval=args.interval, max_tries=args.tries,
                      display_name=args.name, ocpus=args.ocpus, memory_gb=args.memory)
    elif args.cmd in ("start", "stop", "softreset"):
        return instance_action(client, args.instance_id, args.cmd)
    elif args.cmd == "terminate":
        return terminate(client, args.instance_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
