from app.modules.m6_purchase.rules import *
from app.pipeline import run
from app import store
from app.errors import BusinessError
from decimal import Decimal
import pytest

HEADER="lease_id,product,user,start,end,kind,version,seats"

def csv_line(lease,user,start,kind,seats=1,product="CAD",end="",version=1):
    return f"{lease},{product},{user},{start},{end},{kind},{version},{seats}"

def capacity(start="2026-11-18T00:00:00+00:00",end="2026-11-19T00:00:00+00:00",seats=1):
    return csv_line("CAP01","",start,"capacity",seats,end=end)

def lease(lease,user,start,end,seats=1):
    return csv_line(lease,user,start,"online",seats,end=end)

def request(lease,user,start,seats=1,version=1):
    return csv_line(lease,user,start,"request",seats,version=version)

def terminal(lease,user,start,kind,version=1,seats=1):
    return csv_line(lease,user,start,kind,seats=seats,version=version)

def build(*lines):
    return run("\n".join((HEADER,)+lines)+"\n")

def ticket(result, lease_id):
    return next(t for t in result["wait_tickets"] if t["lease_id"]==lease_id)

def reasons(result):
    return {q["reason"] for q in result["quarantine"]}

def test_purchase_decimal():
    assert seat_hours([{"a":Decimal(0),"b":Decimal(3600),"excess":2}])==2

def test_purchase_partial():
    assert seat_hours([{"a":Decimal(0),"b":Decimal(1800),"excess":1}])==Decimal("0.5")

def test_purchase_zero():
    assert seat_hours([])==0

def test_purchase_conserve():
    assert seat_hours([{"a":Decimal(0),"b":Decimal(1800),"excess":2},{"a":Decimal(1800),"b":Decimal(3600),"excess":2}])==2

def test_purchase_sample(result):
    assert result["purchase"][0]["peak"]==20

def test_purchase_balance(result):
    assert result["balance"]=={"input":48,"accepted":43,"quarantined":4,"blank":1}

def test_purchase_single(result):
    assert len(result["purchase"])==1

# TRAP-30 重试归并：同一租约号连点三次只算一张缺口票
def test_retry_same_ticket_merged_once():
    r=build(capacity(seats=1),
            lease("L1","u1","2026-11-18T00:00:00+00:00","2026-11-18T12:00:00+00:00"),
            request("W1","u2","2026-11-18T01:00:00+00:00"),
            request("W1","u2","2026-11-18T01:05:00+00:00"),
            request("W1","u2","2026-11-18T01:10:00+00:00"))
    assert len(r["wait_tickets"])==1
    t=ticket(r,"W1")
    assert t["retry_count"]==3 and t["status"]=="FULL"
    assert t["request_at"]=="11-18 01:00 UTC"
    assert r["reports"][5]["metrics"]["重试合并"]==2
    assert r["shortage"][0]["waiting"]==1 and r["shortage"][0]["waiting_seats"]==1

# TRAP-31 并行作业：同一人不同租约号各自成票，缺口峰值为两张
def test_parallel_jobs_count_separately():
    r=build(capacity(seats=1),
            lease("L1","u1","2026-11-18T00:00:00+00:00","2026-11-18T12:00:00+00:00"),
            request("W1","u2","2026-11-18T01:00:00+00:00"),
            request("W2","u2","2026-11-18T01:05:00+00:00"))
    assert {t["lease_id"] for t in r["wait_tickets"]}=={"W1","W2"}
    assert all(t["status"]=="FULL" for t in r["wait_tickets"])
    assert r["purchase"][0]["waiting_peak"]==2 and r["purchase"][0]["recommended_add"]==2

# TRAP-32 晚到获准：grant 时刻缩短等待区间，缺口只算到获准为止
def test_late_grant_shortens_waiting():
    r=build(capacity(seats=1),
            lease("L1","u1","2026-11-18T00:00:00+00:00","2026-11-18T12:00:00+00:00"),
            request("W1","u2","2026-11-18T01:00:00+00:00"),
            terminal("W1","u2","2026-11-18T03:00:00+00:00","grant"))
    t=ticket(r,"W1")
    assert t["outcome"]=="GRANT" and t["close_at"]=="11-18 03:00 UTC"
    assert Decimal(t["waiting_seconds"])==Decimal(7200)
    seg=r["shortage"][0]
    assert seg["a"]=="11-18 01:00 UTC" and seg["b"]=="11-18 03:00 UTC"
    assert Decimal(r["purchase"][0]["shortage_seat_hours"])==Decimal("2")

# 放弃同样关闭等待区间
def test_abandon_closes_waiting():
    r=build(capacity(seats=1),
            lease("L1","u1","2026-11-18T00:00:00+00:00","2026-11-18T12:00:00+00:00"),
            request("W1","u2","2026-11-18T01:00:00+00:00"),
            terminal("W1","u2","2026-11-18T02:30:00+00:00","abandon"))
    assert ticket(r,"W1")["outcome"]=="ABANDON"
    assert Decimal(ticket(r,"W1")["waiting_seconds"])==Decimal(5400)

# 未关闭的申请按观察窗（采购覆盖终点）截止，心跳不改变观察窗
def test_open_ticket_ends_at_horizon():
    r=build(capacity(start="2026-11-18T00:00:00+00:00",end="2026-11-18T05:00:00+00:00",seats=1),
            lease("L1","u1","2026-11-18T00:00:00+00:00","2026-11-18T12:00:00+00:00"),
            request("W1","u2","2026-11-18T01:00:00+00:00"),
            terminal("X9","u9","2026-11-18T04:00:00+00:00","heartbeat"))
    assert ticket(r,"W1")["outcome"]=="WAITING"
    assert Decimal(ticket(r,"W1")["waiting_seconds"])==Decimal(14400)

# 拒绝时仍有空席：不形成采购缺口
def test_rejection_with_free_seat_is_not_shortage():
    r=build(capacity(seats=3),
            lease("L1","u1","2026-11-18T00:00:00+00:00","2026-11-18T12:00:00+00:00"),
            request("W1","u2","2026-11-18T01:00:00+00:00"))
    assert ticket(r,"W1")["status"]=="NOT_FULL"
    assert r["shortage"]==[] and r["purchase"][0]["waiting_peak"]==0

# 证据可追溯：记录当时占用、上限与来源行
def test_ticket_keeps_occupation_evidence():
    r=build(capacity(seats=2),
            lease("L1","u1","2026-11-18T00:00:00+00:00","2026-11-18T12:00:00+00:00"),
            lease("L2","u3","2026-11-18T00:30:00+00:00","2026-11-18T12:00:00+00:00"),
            request("W1","u2","2026-11-18T01:00:00+00:00"))
    t=ticket(r,"W1")
    assert t["capacity_at_request"]==2 and t["occupied_at_request"]==2
    assert t["request_lines"]=="5"
    assert r["shortage"][0]["ticket_ids"]==["W1"]

# 同批补传的重复 grant 不重复计入，进隔离
def test_duplicate_grant_quarantined_not_double_counted():
    r=build(capacity(seats=1),
            lease("L1","u1","2026-11-18T00:00:00+00:00","2026-11-18T12:00:00+00:00"),
            request("W1","u2","2026-11-18T01:00:00+00:00"),
            terminal("W1","u2","2026-11-18T03:00:00+00:00","grant"),
            terminal("W1","u2","2026-11-18T04:00:00+00:00","grant"))
    assert "GRANT_DUPLICATE" in reasons(r)
    assert ticket(r,"W1")["close_at"]=="11-18 03:00 UTC"
    assert len(r["shortage"])==1

# 旧版终端（陈旧补传）隔离，不改变等待
def test_stale_terminal_rejected():
    r=build(capacity(seats=1),
            lease("L1","u1","2026-11-18T00:00:00+00:00","2026-11-18T12:00:00+00:00"),
            request("W1","u2","2026-11-18T01:00:00+00:00",version=2),
            terminal("W1","u2","2026-11-18T03:00:00+00:00","grant",version=1))
    assert "STALE_TERMINAL" in reasons(r)
    assert ticket(r,"W1")["outcome"]=="WAITING"

# 无申请号的孤儿终端隔离
def test_orphan_terminal_quarantined():
    r=build(capacity(seats=1),
            terminal("W9","u2","2026-11-18T03:00:00+00:00","grant"))
    assert "ORPHAN_TERMINAL" in reasons(r)
    assert r["wait_tickets"]==[]

# 重试改席位数进隔离，原票保留
def test_retry_seat_conflict_quarantined():
    r=build(capacity(seats=1),
            lease("L1","u1","2026-11-18T00:00:00+00:00","2026-11-18T12:00:00+00:00"),
            request("W1","u2","2026-11-18T01:00:00+00:00",seats=1),
            request("W1","u2","2026-11-18T01:05:00+00:00",seats=2))
    assert "REQUEST_SEATS_CONFLICT" in reasons(r)
    assert ticket(r,"W1")["seats"]==1

# 采购上限版本冲突：缺口票挂 CAPACITY_CONFLICT，不计采购建议
def test_capacity_conflict_ticket_flagged():
    r=build(csv_line("CAP01","","2026-11-18T00:00:00+00:00","capacity",1,end="2026-11-19T00:00:00+00:00",version=1),
            csv_line("CAP02","","2026-11-18T00:00:00+00:00","capacity",2,end="2026-11-19T00:00:00+00:00",version=1),
            lease("L1","u1","2026-11-18T00:00:00+00:00","2026-11-18T12:00:00+00:00"),
            request("W1","u2","2026-11-18T01:00:00+00:00"))
    assert ticket(r,"W1")["status"]=="CAPACITY_CONFLICT"
    assert r["purchase"][0]["waiting_peak"]==0

# 缺口扫线：重叠等待按峰值取，不逐笔累加
def test_overlapping_waiting_peak_not_sum_of_tickets():
    r=build(capacity(seats=1),
            lease("L1","u1","2026-11-18T00:00:00+00:00","2026-11-18T12:00:00+00:00"),
            request("W1","u2","2026-11-18T01:00:00+00:00"),
            request("W2","u3","2026-11-18T02:00:00+00:00"),
            terminal("W1","u2","2026-11-18T03:00:00+00:00","grant"))
    peaks=[s["waiting_seats"] for s in r["shortage"]]
    assert peaks==[1,2,1]
    assert r["purchase"][0]["recommended_add"]==2
    # 1h*1 + 1h*2 + 观察窗内 W2 继续等待
    assert Decimal(r["purchase"][0]["shortage_seat_hours"])>Decimal("3")

# 点事件带结束时间被隔离
def test_point_event_with_end_quarantined():
    r=build(capacity(seats=1),
            csv_line("W1","u2","2026-11-18T01:00:00+00:00","request",1,end="2026-11-18T02:00:00+00:00"))
    assert "POINT_EVENT" in reasons(r)

# 更新中断：失败的补传不落库，旧批次完整测算仍可取回
def test_interrupted_update_keeps_previous_run_intact():
    text="\n".join((HEADER,capacity(seats=1),
                    lease("L1","u1","2026-11-18T00:00:00+00:00","2026-11-18T12:00:00+00:00"),
                    request("W1","u2","2026-11-18T01:00:00+00:00")))+"\n"
    saved=store.save(text,run(text))
    before=store.get(saved["id"])
    with pytest.raises(BusinessError):
        store.save("坏数据,无法解析\nx",run("坏数据,无法解析\nx"))
    after=store.get(saved["id"])
    assert after["digest"]==before["digest"]
    assert after["result"]["wait_tickets"]==before["result"]["wait_tickets"]
    assert after["result"]["reports"][5]["metrics"]["满席拒绝"]==1

# 同内容重复执行复用同一批次，重试不产生第二张票
def test_same_content_idempotent_batch():
    text="\n".join((HEADER,capacity(seats=1),
                    lease("L1","u1","2026-11-18T00:00:00+00:00","2026-11-18T12:00:00+00:00"),
                    request("W1","u2","2026-11-18T01:00:00+00:00"),
                    request("W1","u2","2026-11-18T01:05:00+00:00")))+"\n"
    a=store.save(text,run(text));b=store.save(text,run(text))
    assert a["id"]==b["id"]
    assert len(a["result"]["wait_tickets"])==1
