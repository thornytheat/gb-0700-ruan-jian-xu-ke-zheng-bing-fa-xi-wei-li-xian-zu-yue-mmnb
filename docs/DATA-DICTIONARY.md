# 席位定义

lease_id、user保持字符串。CAD-Pro、cad归一为CAD。kind取online、offline、heartbeat、release、capacity、request、grant、abandon。start/end必须带时区并转换UTC。seats为正整数；capacity记录表示采购生效区间的席位上限。

request（席位申请，点事件）、grant（获准，点事件）、abandon（放弃，点事件）不填写end；带end的点事件隔离（POINT_EVENT）。同一(产品,租约号)的多条request视为同一申请的重试，合并为一张等待票，首次拒绝时刻为等待起点；不同租约号即并行作业，各自成票。grant/abandon关闭等待票；重复终端（GRANT_DUPLICATE/ABANDON_DUPLICATE）、旧版终端（STALE_TERMINAL）、无申请号的孤儿终端（ORPHAN_TERMINAL）进隔离，不重复缩短等待。重试改使用人（REQUEST_USER_CONFLICT）或席位数（REQUEST_SEATS_CONFLICT）隔离，原票保留。

等待票以首次拒绝时刻的采购上限与在席占用裁决：占用达到或超过上限记FULL（SEATS_FULL），仍有空席记NOT_FULL不形成缺口；采购上限版本冲突记CAPACITY_CONFLICT交人工复核。等待区间为半开[首次拒绝, 获准/放弃/观察窗末)；观察窗末取采购覆盖终点，无采购记录时取最晚业务事件时刻（心跳不建立业务时刻）。缺口扫线按产品切分，同时等待的票按峰值计等待席位，建议增购=缺口峰值，缺口席位小时用Decimal按秒折算。每张票保留当时上限、当时占用、来源行号与全部重试行号，证据CSV含wait_ticket与shortage两类记录。

在线租约可由同租约同版本release提前结束；离线授权一直占用至原到期时刻，即使收到release仍保留。心跳不建立租约。租约采用最高版本；同版多条实体租约冲突隔离。采购区间可重叠，最高version优先，同最高版不同数量返回CAPACITY_CONFLICT。

并发使用半开区间[a,b)，结束同刻开始不重叠。采购边界必须切分并发段。超用责任按租约开始时间倒序、再按编号倒序分配，分配席位严格等于超用数量。超用席位小时为超用数量×秒数/3600，Decimal计算。此工具只核验约定席位数，不含厂商复杂许可证条款。

租约ID在产品内唯一；不同产品允许相同租约号。线上租约在start同刻release得到零时占用，离线仍保留原到期。
