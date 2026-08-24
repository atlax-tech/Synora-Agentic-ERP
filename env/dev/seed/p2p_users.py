"""Synora Phase 1 P2P 测试用户初始化（P1.3 / Inc-3）。

Administrator 仅用于本初始化（创建命名测试用户并分配上游真实 Role）；
后续所有 P2P 操作由测试用户经 HTTP 完成。幂等：存在即跳过。
角色依据（候选 SHA erpnext 11e0ba0a DocPerm 取证）：
- Purchase User：MR/PO/PR 全权（read/write/create/submit/cancel/amend），PI 只读
- 独立 Purchase User：具备 PO Submit 及关联对象读取权限，与 Buyer 身份分离
- Stock User：MR/PR 全权，PO 只读
- Accounts User：PI create/submit/cancel/amend（无 delete），PR 只读，MR/PO 无权限行
密码从环境变量 SYNORA_P2P_USER_PWD 注入（env/dev/.env，不入库）。

容器内用法（env.sh p2p-users 已封装）：
  exec(open("/tmp/synora_seed/p2p_users.py").read(), globals()); run()
"""

import os

import frappe

# 用户名即命名空间标记（邮箱前缀 SYNORA-P1）。
# receiver 需组合 Stock User + Purchase User：实测 Stock User 单独创建 Purchase Receipt 时
# 因无 Account 读权限被拒（Account DocPerm：Stock User 无行，Purchase User read），
# 属候选 SHA 源码权限矩阵事实，详见 Inc-3 开发日志。
USERS = [
    ("synora-p1-buyer@dev.localhost", "Synora P1 Buyer", ["Purchase User"]),
    ("synora-p1-approver@dev.localhost", "Synora P1 Approver", ["Purchase User"]),
    ("synora-p1-receiver@dev.localhost", "Synora P1 Receiver", ["Stock User", "Purchase User"]),
    ("synora-p1-accountant@dev.localhost", "Synora P1 Accountant", ["Accounts User"]),
    ("synora-p1-viewer@dev.localhost", "Synora P1 Viewer", []),  # 无业务角色：权限拒绝用例
]


def run():
    pwd = os.environ.get("SYNORA_P2P_USER_PWD")
    if not pwd:
        raise Exception("[p2p-users] 缺少环境变量 SYNORA_P2P_USER_PWD")
    try:
        for email, first_name, roles in USERS:
            if frappe.db.exists("User", email):
                user = frappe.get_doc("User", email)
                actual = {row.role for row in user.roles}
                if actual - set(roles):
                    raise Exception(
                        f"[p2p-users] {email} 存在未授权显式角色: {sorted(actual - set(roles))}"
                    )
                for role in set(roles) - actual:
                    user.append("roles", {"role": role})
                user.enabled = 1
                user.new_password = pwd
                user.save()
                print(f"[p2p-users] verified {email} roles={sorted(roles)}")
                continue
            frappe.get_doc({
                "doctype": "User",
                "email": email,
                "first_name": first_name,
                "send_welcome_email": 0,
                "enabled": 1,
                "new_password": pwd,  # 标准 User 字段，走 frappe 哈希存储
                "roles": [{"role": role} for role in roles],
            }).insert()
            print(f"[p2p-users] created {email} roles={roles}")
        frappe.db.commit()
        print("P2P-USERS-OK")
    except Exception:
        frappe.db.rollback()
        raise
