from decimal import Decimal
from app.report import report, quarantine
from app.timeops import display
from app.modules.m5_excess.rules import capacity_at

def seat_hours(rows):
    return sum((r["b"]-r["a"])*r["excess"]/Decimal(3600) for r in rows)

def occupied_at(leases, product, instant):
    return sum(r["quantity"] for r in leases if r["canonical"]==product and r["a"]<=instant<r["b"])

def build_tickets(ctx):
    """把 request/grant/abandon 事件归并为等待申请单。

    同一 (产品, 租约号) 的多条 request 是同一申请的重试：合并为一张票，
    起点取最早一次拒绝；不同租约号即并行作业，各自成票。
    grant/abandon 只关闭本票，迟到的同版/旧版终端进隔离，不重复缩短等待。
    """
    tickets={}
    terminals={}
    for row in sorted((r for r in ctx["normalized"] if r["kind"] in {"request","grant","abandon"}), key=lambda r:(r["a"], r["line"])):
        key=(row["canonical"], row["lease_id"])
        if row["kind"]=="request":
            ticket=tickets.get(key)
            if ticket is None:
                ticket={"product":row["canonical"],"lease_id":row["lease_id"],"user":row["user"],"seats":row["quantity"],
                        "first_line":row["line"],"request_lines":[row["line"]],"request_revs":[row["rev"]],
                        "first_at":row["a"],"last_retry_at":row["a"],"retry_count":1,
                        "close_at":None,"close_kind":None,"close_line":None,"close_rev":None}
                tickets[key]=ticket
            else:
                if row["user"] and ticket["user"] and row["user"]!=ticket["user"]:
                    quarantine(ctx,row,"REQUEST_USER_CONFLICT")
                if row["quantity"]!=ticket["seats"]:
                    quarantine(ctx,row,"REQUEST_SEATS_CONFLICT")
                ticket["request_lines"].append(row["line"]);ticket["request_revs"].append(row["rev"])
                ticket["last_retry_at"]=row["a"];ticket["retry_count"]+=1
            continue
        if key not in tickets:
            quarantine(ctx,row,"ORPHAN_TERMINAL");continue
        ticket=tickets[key]
        if row["rev"]<max(ticket["request_revs"]):
            quarantine(ctx,row,"STALE_TERMINAL");continue
        bucket=terminals.setdefault(key,[])
        if any(x["kind"]==row["kind"] for x in bucket):
            quarantine(ctx,row,"GRANT_DUPLICATE" if row["kind"]=="grant" else "ABANDON_DUPLICATE");continue
        if ticket["close_at"] is not None and row["a"]>ticket["close_at"]:
            quarantine(ctx,row,"TERMINAL_CONFLICT");continue
        bucket.append(row)
        if ticket["close_at"] is None or row["a"]<ticket["close_at"]:
            ticket["close_at"]=row["a"];ticket["close_kind"]=row["kind"];ticket["close_line"]=row["line"];ticket["close_rev"]=row["rev"]
    return list(tickets.values())

def _horizon(ctx, capacities):
    """观察窗末：取采购覆盖终点；无采购记录时取最晚业务事件（心跳不建立业务时刻）。"""
    cover_end=[r["b"] for r in capacities]
    if cover_end:return max(cover_end)
    business=[r["a"] for r in ctx["normalized"] if r["kind"]!="heartbeat"]
    return max(business, default=Decimal(0))

def adjudicate_tickets(ctx, tickets):
    """以首次拒绝时刻的采购上限与在席占用裁决：仅满席拒绝才形成缺口。"""
    capacities=[r for r in ctx["normalized"] if r["kind"]=="capacity"]
    horizon=_horizon(ctx, capacities)
    for ticket in tickets:
        caps=[r for r in capacities if r["canonical"]==ticket["product"]]
        capacity, conflict = capacity_at(caps, ticket["first_at"])
        occupied = occupied_at(ctx["leases"], ticket["product"], ticket["first_at"])
        ticket["capacity_at_request"]=capacity
        ticket["occupied_at_request"]=occupied
        ticket["capacity_conflict"]=conflict
        ticket["full"]=(not conflict) and occupied>=capacity
        if conflict:
            ticket["status"]="CAPACITY_CONFLICT";ticket["reason"]="采购上限版本冲突，需人工复核"
        elif not ticket["full"]:
            ticket["status"]="NOT_FULL";ticket["reason"]="拒绝时仍有空席，缺口不成立"
        else:
            ticket["status"]="FULL";ticket["reason"]="SEATS_FULL"
        ticket["end_at"]=ticket["close_at"] if ticket["close_at"] is not None else horizon
        ticket["outcome"]=ticket["close_kind"].upper() if ticket["close_kind"] else "WAITING"
        ticket["waiting_seconds"]=ticket["end_at"]-ticket["first_at"]
    return tickets

def shortage_timeline(tickets):
    """满席票在 [首次拒绝, 获准/放弃/观察窗末) 上的并发票据扫线，半开区间，按产品切分。"""
    out=[]
    for product in sorted({t["product"] for t in tickets if t["full"]}):
        full=[t for t in tickets if t["full"] and t["product"]==product]
        points=sorted({t[k] for t in full for k in ("first_at","end_at")})
        for a,b in zip(points, points[1:]):
            active=[t for t in full if t["first_at"]<=a<t["end_at"]]
            if active:
                out.append({"product":product,"a":a,"b":b,"waiting":len(active),
                            "waiting_seats":sum(t["seats"] for t in active),
                            "ticket_ids":sorted(t["lease_id"] for t in active)})
    return out

def shortage_seat_hours(segments):
    return sum((r["b"]-r["a"])*r["waiting_seats"]/Decimal(3600) for r in segments)

def present(ticket):
    return {"product":ticket["product"],"lease_id":ticket["lease_id"],"user":ticket["user"],"seats":ticket["seats"],
            "request_at":display(ticket["first_at"]),"retry_count":ticket["retry_count"],
            "request_lines":";".join(str(x) for x in ticket["request_lines"]),
            "capacity_at_request":ticket["capacity_at_request"],"occupied_at_request":ticket["occupied_at_request"],
            "outcome":ticket["outcome"],
            "close_at":display(ticket["close_at"]) if ticket["close_at"] is not None else "—",
            "waiting_seconds":ticket["waiting_seconds"],"status":ticket["status"],"reason":ticket["reason"]}

def apply(ctx):
    out=[]
    for p in sorted({r["product"] for r in ctx["usage"]} | {r["canonical"] for r in ctx["normalized"] if r["kind"]=="request"}):
        rows=[r for r in ctx["usage"] if r["product"]==p]
        out.append({"product":p,"peak":max((r["concurrent"] for r in rows),default=0),
                    "excess_seat_hours":seat_hours(rows),"overuse_segments":sum(r["excess"]>0 for r in rows)})
    tickets=adjudicate_tickets(ctx, build_tickets(ctx))
    segments=shortage_timeline(tickets)
    for item in out:
        p=item["product"];own=[s for s in segments if s["product"]==p]
        item["waiting_peak"]=max((s["waiting_seats"] for s in own),default=0)
        item["shortage_seat_hours"]=shortage_seat_hours(own)
        item["recommended_add"]=item["waiting_peak"]
    ctx["purchase"]=out
    ctx["wait_tickets"]=[present(t) for t in tickets]
    ctx["shortage"]=[dict(s, a=display(s["a"]), b=display(s["b"])) for s in segments]
    ctx["balance"]={"input":ctx["input_count"],"accepted":ctx["input_count"]-len(ctx["quarantine"])-ctx["blank_rows"],"quarantined":len(ctx["quarantine"]),"blank":ctx["blank_rows"]}
    return report(out, 产品=len(out), 超用席位小时=seat_hours(ctx["usage"]),
                  等待申请=len(tickets), 满席拒绝=sum(t["full"] for t in tickets),
                  重试合并=sum(t["retry_count"]-1 for t in tickets),
                  缺口峰值=max((s["waiting_seats"] for s in segments),default=0),
                  缺口席位小时=shortage_seat_hours(segments))
