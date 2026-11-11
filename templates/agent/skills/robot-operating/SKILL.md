---
name: robot-operating
description: 操作移动操作机器人（差速底盘 + 6-DOF 机械臂 + 夹爪）的作业手册：安全检查、忙时重试、失败码处理、典型动作序列。
---

# 机器人作业手册

工具经 MCP server `robot` 提供，名字前缀 `mcp_robot_`。返回值统一是
`{ok, code, message, data}`：**`ok` 只表示通信层走完**，动作成没成看 `data.success`。

## 动手前必看

1. **先读状态**：`mcp_robot_get_status`。`data.state` 为 `2=NAVIGATING` / `3=MOVING`
   说明还在忙（`1=IDLE` 才空闲）。
2. **同一时刻只有一个运动目标**：导航与机械臂是**两条独立通道**（可以一边开一边伸臂），
   但"两段导航"或"两个手臂动作"不能同时 —— 后发的会被后端**直接拒收**。
3. 运动类命令默认**需要人工审批**；被拒不是故障。

## 忙时怎么办

`code=UNAVAILABLE` 且消息里有 `rejected` ⇒ **忙**，不是链路坏了：

- 先 `mcp_robot_get_status` 确认 `state`；
- 等当前动作结束再重发。判断"结束了没"用 `mcp_robot_get_pose` 连读两次看位置还变不变；
- **不要**原样连发：后端是"忙时拒收"，不是排队。

## 失败码

| code | 意思 | 怎么做 |
|---|---|---|
| `UNAVAILABLE` | 忙 / 服务端不在 | 先看 `state` 分清是哪一种 |
| `INVALID_ARGUMENT` | 参数不合法（如没配置的具名目标） | 改参数，**别原样重试** |
| `OUT_OF_RANGE` | 超行程 / 限位（由执行侧判定） | 换目标 |
| `TIMEOUT` | 动作没在时限内结束 | 先 `get_pose` 看它走到哪了，再决定 |

## 典型序列

- **去某处**：`get_status` → `scan_summary`（看路）→ `navigate_to` → `get_pose` 核对。
  核对是必须的：`navigate_to` 的 `success=true` 只是后端报到达，`get_pose` 才是真相。
- **动手臂**：`joint_states`（拿到关节名）→ `arm_move_joints`。`joint_names` 与
  `positions` 一一对应；后端按名字对齐，顺序不要求一致。
- **抓取**：`arm_move_joints` 就位 → `gripper(action="open")` → 靠近 → `gripper(action="close")`。
- **急停**：`cancel`。**随时可用、无需审批、立即生效** —— 觉得不对就按。

## 边界（别误判成自己写错）

- `move_eef_to_pose` 走 SDK 的笛卡尔 action，**裸 mock 不提供**（要接了 MoveIt 适配器才有）；
  对着 mock 调它会拿到 `UNAVAILABLE`。
- `home` / `named_target` 依赖服务器配置里的具名目标（`ROBOT_SDK_HOME_POSITIONS`）；
  没配就是 `INVALID_ARGUMENT: unknown named target`。
- `scan_summary` 的 `nearest_m` 为 `null` 表示"窗口内没有有效回波"，不是 0。
- 目标点是**世界坐标**（不是相对底盘）；坐标系默认 `map`。
