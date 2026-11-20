# 接口

`POST /api/run`：{"text":"CSV内容"}。同规则版本与输入摘要只产生一条批次。

`GET/POST /api/1` 至 `/api/7`：独立样例或自定义文件计算。各模块返回 report、quarantine、balance。M6 采购对照额外返回 wait_tickets（等待申请票，含当时上限/占用、来源行、重试次数、结局）、shortage（缺口扫线段）、purchase 汇总中的 waiting_peak、shortage_seat_hours、recommended_add。

`POST /api/review`：id、整数revision、decision取通过或退回。旧版本409；阻断项不能通过。

`GET /api/export/{id}`：唯一证据CSV。`GET /health`：存活检查。

错误：结构/数值422，版本冲突409，不存在404，文件过大413。逐行异常进入quarantine。

## 席位申请事件（request/grant/abandon）

点事件只填 start，end 留空。同(产品,租约号)重复 request 按重试合并，不同租约号按并行作业分开；grant/abandon 决定等待票结局，重复或旧版终端隔离。批次按规则版本+输入摘要内容寻址，补传产生新批次，旧批次与完整测算保留；同一内容重复执行复用同一批次。
